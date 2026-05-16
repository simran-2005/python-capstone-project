"""
app.py — GitHub Portfolio & Issue Tracker
"""

import os
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, g
)
from dotenv import load_dotenv

import database as db
import github_api
import mongo_cache

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-this")

# ---------------------------------------------------------------------------
# DB lifecycle hooks
# ---------------------------------------------------------------------------

@app.teardown_appcontext
def close_db_on_teardown(e=None):
    db.close_db(e)


# ---------------------------------------------------------------------------
# Auth helper — every protected route calls this
# ---------------------------------------------------------------------------

def get_current_user():
    """Return user row if logged in, else None."""
    username = session.get("username")
    if not username:
        return None
    return db.get_or_create_user(db.get_db(), username)


def require_login():
    """Redirect to / if not logged in. Use at the top of protected routes."""
    user = get_current_user()
    if user is None:
        flash("Please enter a username to continue.", "info")
        return redirect(url_for("index")), None
    return None, user


# ---------------------------------------------------------------------------
# Route: / — username login
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if not username:
            flash("Username cannot be empty.", "error")
            return redirect(url_for("index"))

        # Create user if new, then store in session
        db.get_or_create_user(db.get_db(), username)
        session["username"] = username
        return redirect(url_for("dashboard"))

    # If already logged in, skip the login page
    if session.get("username"):
        return redirect(url_for("dashboard"))

    return render_template("index.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Route: /dashboard — tracked repos overview
# ---------------------------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    repos = db.get_tracked_repos(db.get_db(), user["id"])
    return render_template("dashboard.html", user=user, repos=repos)


# ---------------------------------------------------------------------------
# Route: POST /repos/add — add a repo to track
# ---------------------------------------------------------------------------

@app.route("/repos/add", methods=["POST"])
def add_repo():
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    owner     = request.form.get("owner", "").strip()
    repo_name = request.form.get("repo_name", "").strip()

    if not owner or not repo_name:
        flash("Both owner and repo name are required.", "error")
        return redirect(url_for("dashboard"))

    # 1. Check GitHub first — don't save a repo that doesn't exist
    github_data, err = github_api.get_repo(owner, repo_name)
    if err:
        flash(f"Could not add repo: {err}", "error")
        return redirect(url_for("dashboard"))

    # 2. Save to SQLite
    repo_id, already_existed = db.add_tracked_repo(
        db.get_db(), user["id"], owner, repo_name
    )

    if already_existed:
        flash(f"{owner}/{repo_name} is already in your tracker.", "info")
        return redirect(url_for("dashboard"))

    # 3. Populate repo_cache immediately so dashboard shows metadata right away
    db.upsert_repo_cache(db.get_db(), repo_id, github_data)

    # 4. Store raw payload in MongoDB (best-effort)
    mongo_cache.cache_repo(owner, repo_name, github_data)

    flash(f"Added {owner}/{repo_name}!", "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Route: GET /repos/<id> — repo detail + live issues
# ---------------------------------------------------------------------------

@app.route("/repos/<int:repo_id>")
def repo_detail(repo_id):
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    repo = db.get_repo_by_id(db.get_db(), repo_id, user["id"])
    if repo is None:
        flash("Repo not found or doesn't belong to you.", "error")
        return redirect(url_for("dashboard"))

    owner     = repo["owner"]
    repo_name = repo["repo_name"]

    # Try Mongo cache first, then hit GitHub.
    # Cache stores {"issues": [...], "prs": [...]} so we can separate them.
    cached = mongo_cache.get_cached_issues(owner, repo_name)
    cache_source = "mongo"

    if cached is not None:
        # Handle both old format (plain list) and new format (dict with split)
        if isinstance(cached, dict):
            issues = cached.get("issues", [])
            prs    = cached.get("prs", [])
        else:
            issues = [i for i in cached if "pull_request" not in i]
            prs    = [i for i in cached if "pull_request" in i]
    else:
        issues, prs, err = github_api.get_open_issues(owner, repo_name)
        cache_source = "github"
        if err:
            flash(f"Could not load issues: {err}", "error")
            issues, prs = [], []
        else:
            # Store split payload in Mongo and update PR count in SQLite
            mongo_cache.cache_issues(owner, repo_name, {"issues": issues, "prs": prs})
            db.upsert_repo_cache(db.get_db(), repo_id, dict(repo), open_prs_count=len(prs))

    return render_template(
        "repo_detail.html",
        user=user,
        repo=repo,
        issues=issues,
        prs=prs,
        cache_source=cache_source,
    )


# ---------------------------------------------------------------------------
# Route: POST /repos/<id>/refresh — force-refresh repo metadata
# ---------------------------------------------------------------------------

@app.route("/repos/<int:repo_id>/refresh", methods=["POST"])
def refresh_repo(repo_id):
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    repo = db.get_repo_by_id(db.get_db(), repo_id, user["id"])
    if repo is None:
        flash("Repo not found.", "error")
        return redirect(url_for("dashboard"))

    github_data, err = github_api.get_repo(repo["owner"], repo["repo_name"])
    if err:
        flash(f"Refresh failed: {err}", "error")
    else:
        db.upsert_repo_cache(db.get_db(), repo_id, github_data)
        mongo_cache.cache_repo(repo["owner"], repo["repo_name"], github_data)
        flash("Repo metadata refreshed.", "success")

    return redirect(url_for("repo_detail", repo_id=repo_id))


# ---------------------------------------------------------------------------
# Route: POST /issues/save — save an issue with a note
# ---------------------------------------------------------------------------

@app.route("/issues/save", methods=["POST"])
def save_issue():
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    repo_id      = request.form.get("repo_id", type=int)
    issue_number = request.form.get("issue_number", type=int)
    title        = request.form.get("title", "").strip()
    url          = request.form.get("url", "").strip()
    note         = request.form.get("note", "").strip()

    if not all([repo_id, issue_number, title, url]):
        flash("Missing required fields.", "error")
        return redirect(url_for("dashboard"))

    issue_id, already_saved = db.save_issue(
        db.get_db(), user["id"], repo_id, issue_number, title, url, note
    )

    if already_saved:
        flash("You've already saved this issue.", "info")
    else:
        flash("Issue saved!", "success")

    return redirect(url_for("repo_detail", repo_id=repo_id))


# ---------------------------------------------------------------------------
# Route: GET /issues — all saved issues
# ---------------------------------------------------------------------------

@app.route("/issues")
def saved_issues():
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    issues = db.get_saved_issues(db.get_db(), user["id"])
    return render_template("saved_issues.html", user=user, issues=issues)


# ---------------------------------------------------------------------------
# Route: POST /issues/<id>/delete — remove a saved issue
# ---------------------------------------------------------------------------

@app.route("/issues/<int:issue_id>/delete", methods=["POST"])
def delete_issue(issue_id):
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    db.delete_saved_issue(db.get_db(), issue_id, user["id"])
    flash("Issue removed.", "success")
    return redirect(url_for("saved_issues"))


# ---------------------------------------------------------------------------
# Route: GET /stats — aggregate view
# ---------------------------------------------------------------------------

@app.route("/stats")
def stats():
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    stats_data = db.get_stats(db.get_db(), user["id"])
    return render_template("stats.html", user=user, stats=stats_data)


# ---------------------------------------------------------------------------
# Route: GET /plans — contribution planner
# ---------------------------------------------------------------------------

@app.route("/plans")
def plans():
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    plans_list = db.get_contribution_plans(db.get_db(), user["id"])
    saved = db.get_saved_issues(db.get_db(), user["id"])
    return render_template("plans.html", user=user, plans=plans_list, saved_issues=saved)


@app.route("/plans/add", methods=["POST"])
def add_plan():
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    issue_id    = request.form.get("issue_id", type=int)
    target_date = request.form.get("target_date", "").strip()
    note        = request.form.get("note", "").strip()

    if not issue_id:
        flash("Select an issue.", "error")
        return redirect(url_for("plans"))

    db.add_contribution_plan(db.get_db(), user["id"], issue_id, target_date, note)
    flash("Plan added.", "success")
    return redirect(url_for("plans"))


@app.route("/plans/<int:plan_id>/done", methods=["POST"])
def mark_plan_done(plan_id):
    redirect_resp, user = require_login()
    if redirect_resp:
        return redirect_resp

    db.update_plan_status(db.get_db(), plan_id, user["id"], "done")
    flash("Marked as done!", "success")
    return redirect(url_for("plans"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db.init_db()
    app.run(debug=True)

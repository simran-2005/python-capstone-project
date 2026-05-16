"""
database.py — SQLite schema and query helpers.

Call init_db() once at startup (app.py does this).
All functions take a db connection; get one via get_db().
"""

import sqlite3
from flask import g

DATABASE = "github_tracker.db"


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def get_db():
    """Return the db connection for this request, creating it if needed."""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row  # rows behave like dicts: row["col"]
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    """Close db at end of request. Registered in app.py via teardown_appcontext."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------------------
# Schema creation + migration
# ---------------------------------------------------------------------------

def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
    db = sqlite3.connect(DATABASE)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT    NOT NULL UNIQUE,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tracked_repos (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            owner      TEXT    NOT NULL,
            repo_name  TEXT    NOT NULL,
            added_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, owner, repo_name)
        );

        -- Cached metadata from the GitHub API (one row per repo).
        -- open_issues_count = GitHub's raw count (includes PRs).
        -- open_prs_count    = actual PR count (known after fetching issues).
        CREATE TABLE IF NOT EXISTS repo_cache (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id           INTEGER NOT NULL UNIQUE REFERENCES tracked_repos(id),
            stars             INTEGER,
            language          TEXT,
            description       TEXT,
            open_issues_count INTEGER,  -- GitHub raw: issues + PRs combined
            open_prs_count    INTEGER,  -- actual PR count, NULL until issues fetched
            last_updated      TEXT,
            fetched_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS saved_issues (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id),
            repo_id      INTEGER NOT NULL REFERENCES tracked_repos(id),
            issue_number INTEGER NOT NULL,
            title        TEXT    NOT NULL,
            url          TEXT    NOT NULL,
            note         TEXT,
            saved_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, repo_id, issue_number)
        );

        CREATE TABLE IF NOT EXISTS contribution_plans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            issue_id    INTEGER NOT NULL REFERENCES saved_issues(id),
            target_date TEXT,
            status      TEXT NOT NULL DEFAULT 'open',
            note        TEXT,
            UNIQUE(user_id, issue_id)
        );
    """)
    db.commit()

    # Migration: add open_prs_count to existing databases that don't have it.
    # ALTER TABLE ... ADD COLUMN is safe to run; we catch the error if it already exists (SQLite doesn't support IF NOT EXISTS on ADD COLUMN).
    try:
        db.execute("ALTER TABLE repo_cache ADD COLUMN open_prs_count INTEGER")
        db.commit()
    except sqlite3.OperationalError:
        pass  # column already exists — nothing to do

    db.close()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def get_or_create_user(db, username):
    """Return the user row, inserting a new row if username is new."""
    user = db.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()

    if user is None:
        db.execute("INSERT INTO users (username) VALUES (?)", (username,))
        db.commit()
        user = db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

    return user


# ---------------------------------------------------------------------------
# Repo helpers
# ---------------------------------------------------------------------------

def add_tracked_repo(db, user_id, owner, repo_name):
    """
    Insert a new tracked repo. Returns (repo_id, already_existed).
    already_existed=True means the user already tracks this repo.
    """
    existing = db.execute(
        "SELECT id FROM tracked_repos WHERE user_id=? AND owner=? AND repo_name=?",
        (user_id, owner, repo_name)
    ).fetchone()

    if existing:
        return existing["id"], True

    db.execute(
        "INSERT INTO tracked_repos (user_id, owner, repo_name) VALUES (?, ?, ?)",
        (user_id, owner, repo_name)
    )
    db.commit()
    repo_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return repo_id, False


def get_tracked_repos(db, user_id):
    """Return all repos tracked by a user, joined with their cached metadata."""
    return db.execute("""
        SELECT
            tr.id, tr.owner, tr.repo_name, tr.added_at,
            rc.stars, rc.language, rc.description,
            rc.open_issues_count, rc.open_prs_count,
            rc.last_updated, rc.fetched_at
        FROM tracked_repos tr
        LEFT JOIN repo_cache rc ON rc.repo_id = tr.id
        WHERE tr.user_id = ?
        ORDER BY tr.added_at DESC
    """, (user_id,)).fetchall()


def get_repo_by_id(db, repo_id, user_id):
    """Return a single tracked repo (with cache) belonging to this user."""
    return db.execute("""
        SELECT
            tr.id, tr.owner, tr.repo_name, tr.added_at,
            rc.stars, rc.language, rc.description,
            rc.open_issues_count, rc.open_prs_count,
            rc.last_updated, rc.fetched_at
        FROM tracked_repos tr
        LEFT JOIN repo_cache rc ON rc.repo_id = tr.id
        WHERE tr.id = ? AND tr.user_id = ?
    """, (repo_id, user_id)).fetchone()


def upsert_repo_cache(db, repo_id, github_data, open_prs_count=None):
    """
    Insert or replace cached metadata for a repo.
    github_data is the dict returned by github_api.get_repo().
    open_prs_count is passed separately when known (after fetching issues).
    When None, any existing open_prs_count value is preserved.
    """
    if open_prs_count is not None:
        db.execute("""
            INSERT INTO repo_cache
                (repo_id, stars, language, description,
                 open_issues_count, open_prs_count, last_updated, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(repo_id) DO UPDATE SET
                stars             = excluded.stars,
                language          = excluded.language,
                description       = excluded.description,
                open_issues_count = excluded.open_issues_count,
                open_prs_count    = excluded.open_prs_count,
                last_updated      = excluded.last_updated,
                fetched_at        = excluded.fetched_at
        """, (
            repo_id,
            github_data.get("stargazers_count"),
            github_data.get("language"),
            github_data.get("description"),
            github_data.get("open_issues_count"),
            open_prs_count,
            github_data.get("updated_at"),
        ))
    else:
        # Don't overwrite open_prs_count if we don't have a fresh value.
        db.execute("""
            INSERT INTO repo_cache
                (repo_id, stars, language, description,
                 open_issues_count, last_updated, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(repo_id) DO UPDATE SET
                stars             = excluded.stars,
                language          = excluded.language,
                description       = excluded.description,
                open_issues_count = excluded.open_issues_count,
                last_updated      = excluded.last_updated,
                fetched_at        = excluded.fetched_at
        """, (
            repo_id,
            github_data.get("stargazers_count"),
            github_data.get("language"),
            github_data.get("description"),
            github_data.get("open_issues_count"),
            github_data.get("updated_at"),
        ))
    db.commit()


# ---------------------------------------------------------------------------
# Issue helpers
# ---------------------------------------------------------------------------

def save_issue(db, user_id, repo_id, issue_number, title, url, note=""):
    """Save an issue to the user's saved list. Returns (issue_id, already_saved)."""
    existing = db.execute(
        "SELECT id FROM saved_issues WHERE user_id=? AND repo_id=? AND issue_number=?",
        (user_id, repo_id, issue_number)
    ).fetchone()

    if existing:
        return existing["id"], True

    db.execute("""
        INSERT INTO saved_issues (user_id, repo_id, issue_number, title, url, note)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, repo_id, issue_number, title, url, note))
    db.commit()
    issue_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return issue_id, False


def get_saved_issues(db, user_id):
    """Return all saved issues for a user, joined with repo info."""
    return db.execute("""
        SELECT
            si.id, si.issue_number, si.title, si.url, si.note, si.saved_at,
            tr.owner, tr.repo_name, tr.id AS repo_id
        FROM saved_issues si
        JOIN tracked_repos tr ON tr.id = si.repo_id
        WHERE si.user_id = ?
        ORDER BY si.saved_at DESC
    """, (user_id,)).fetchall()


def delete_saved_issue(db, issue_id, user_id):
    """Delete a saved issue (only if it belongs to user_id)."""
    db.execute(
        "DELETE FROM saved_issues WHERE id = ? AND user_id = ?",
        (issue_id, user_id)
    )
    db.commit()


# ---------------------------------------------------------------------------
# Contribution plan helpers
# ---------------------------------------------------------------------------

def add_contribution_plan(db, user_id, issue_id, target_date, note=""):
    db.execute("""
        INSERT INTO contribution_plans (user_id, issue_id, target_date, note)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, issue_id) DO UPDATE SET
            target_date = excluded.target_date,
            note        = excluded.note
    """, (user_id, issue_id, target_date, note))
    db.commit()


def get_contribution_plans(db, user_id):
    """Return plans with issue + repo context, including overdue detection."""
    return db.execute("""
        SELECT
            cp.id, cp.target_date, cp.status, cp.note,
            si.title AS issue_title, si.url AS issue_url, si.issue_number,
            tr.owner, tr.repo_name,
            CASE
                WHEN cp.status = 'open' AND cp.target_date < date('now')
                THEN 1 ELSE 0
            END AS is_overdue
        FROM contribution_plans cp
        JOIN saved_issues si ON si.id = cp.issue_id
        JOIN tracked_repos tr ON tr.id = si.repo_id
        WHERE cp.user_id = ?
        ORDER BY cp.target_date ASC
    """, (user_id,)).fetchall()


def update_plan_status(db, plan_id, user_id, status):
    db.execute(
        "UPDATE contribution_plans SET status=? WHERE id=? AND user_id=?",
        (status, plan_id, user_id)
    )
    db.commit()


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------

def get_stats(db, user_id):
    """
    Returns aggregate data across all tracked repos for a user.
    Used by the /stats route.
    """
    rows = db.execute("""
        SELECT rc.language, rc.stars, rc.open_issues_count, rc.open_prs_count
        FROM tracked_repos tr
        JOIN repo_cache rc ON rc.repo_id = tr.id
        WHERE tr.user_id = ?
    """, (user_id,)).fetchall()

    total_stars = 0
    total_open_issues = 0
    total_open_prs = 0
    language_counts = {}

    for row in rows:
        total_stars += (row["stars"] or 0)
        prs = (row["open_prs_count"] or 0)
        raw = (row["open_issues_count"] or 0)
        # If we have a known PR count, derive true issue count. Otherwise use raw.
        if row["open_prs_count"] is not None:
            total_open_issues += max(raw - prs, 0)
        else:
            total_open_issues += raw
        total_open_prs += prs
        lang = row["language"] or "Unknown"
        language_counts[lang] = language_counts.get(lang, 0) + 1

    return {
        "total_stars": total_stars,
        "total_open_issues": total_open_issues,
        "total_open_prs": total_open_prs,
        "language_counts": language_counts,
        "repo_count": len(rows),
    }

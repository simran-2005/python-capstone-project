# ⚡ GitHub Portfolio & Issue Tracker

A Flask web application for tracking GitHub repositories and managing open-source contribution plans. Built as the ENPM818Q capstone project at the University of Maryland.

---

## What This Is

Developers who follow multiple open-source repositories have no lightweight way to track interesting issues, plan contributions, or see a portfolio-level summary across everything they watch. GitHub's own interface is repo-centric — there is no personal cross-repo dashboard.

This app solves that. You add any public GitHub repository by owner and name, and it becomes part of your personal tracker. You can browse its open issues, save the ones you want to contribute to with your own notes, set target dates, and see when you are overdue. A stats page aggregates language distributions and star counts across everything you track.

The app also demonstrates a two-database architecture: SQLite for structured relational data and MongoDB Atlas for raw API response caching — reducing GitHub API calls and showing cloud database integration in a real context.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Web Framework | Flask 3.0 |
| Relational Database | SQLite (via Python's built-in `sqlite3`) |
| Cloud Database | MongoDB Atlas (M0 free tier) via `pymongo` |
| External API | GitHub REST API v3 |
| Templating | Jinja2 (bundled with Flask) |
| HTTP Client | `requests` |
| Config Management | `python-dotenv` |
| Frontend | HTML + CSS |

---

## Project Structure

```
github_tracker/
├── app.py              # All Flask routes and request handling
├── database.py         # SQLite schema, connection management, all query helpers
├── github_api.py       # GitHub REST API wrapper (get_repo, get_open_issues)
├── mongo_cache.py      # MongoDB Atlas caching layer
├── init_db.py          # One-time database initializer
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template (copy to .env)
└── templates/
    ├── base.html           # Shared layout, nav, flash messages
    ├── index.html          # Username login page
    ├── dashboard.html      # Tracked repos overview
    ├── repo_detail.html    # Issues and PRs for a single repo
    ├── saved_issues.html   # Personal saved issues list
    ├── stats.html          # Aggregate stats across all tracked repos
    └── plans.html          # Contribution planner
```

---

## How It Works

```
Browser
  │
  ▼
Flask Routes (app.py)
  │
  ├──▶ database.py  ──▶  SQLite (github_tracker.db)
  │                       structured user data
  │
  ├──▶ github_api.py ──▶  GitHub REST API
  │                        live repo + issue data
  │
  └──▶ mongo_cache.py ──▶  MongoDB Atlas
                            raw API JSON cache
```

Every page is server-rendered. No JavaScript API calls. Flask fetches data, queries the database, and returns a complete HTML page via Jinja2 templates.

**Caching strategy:** Before calling GitHub, the app checks MongoDB for a fresh cached response. If found and within TTL (1 hour for repo metadata, 15 minutes for issues), MongoDB is used. On cache miss, GitHub is called and the response is written to MongoDB for next time. This keeps API usage well under GitHub's 5,000 requests/hour authenticated limit.

**Issues vs Pull Requests:** GitHub's `/issues` API endpoint returns both issues and pull requests mixed together, and the `open_issues_count` field on a repo counts both. This app separates them — issues and PRs are fetched together then split by the presence of the `pull_request` key, and displayed in separate sections. The dashboard shows the breakdown as `🐛 N issues  ⤴ N PRs` once a repo has been browsed.

---

## What Gets Stored

### SQLite — `github_tracker.db`

Structured, relational, user-specific data.

| Table | What It Stores |
|---|---|
| `users` | Usernames and creation timestamps. Created automatically on first login. |
| `tracked_repos` | Which repos each user is tracking (owner, repo_name). One row per user-repo pair. |
| `repo_cache` | Cached GitHub metadata per repo: stars, language, description, open issue count, PR count, last updated. Updated on add and on manual refresh. |
| `saved_issues` | Issues a user has bookmarked. Stores issue number, title, URL, and a personal note. |
| `contribution_plans` | Plans linked to saved issues. Stores target date and status (open/done). Overdue detection is computed in SQL using `date('now')`. |

All tables use parameterized queries. Foreign keys are enforced with `PRAGMA foreign_keys = ON`. All user-scoped queries include `WHERE user_id = ?` — users cannot access each other's data.

### MongoDB Atlas — `github_tracker` database

Raw GitHub API JSON payloads, stored as documents.

**`repo_cache` collection**
```json
{
  "owner": "pallets",
  "repo_name": "flask",
  "payload": { /* full GitHub /repos/{owner}/{repo} JSON response */ },
  "cached_at": "2026-05-16T05:38:24.130+00:00"
}
```
TTL: 1 hour. One document per repository, upserted on every fetch.

**`issue_cache` collection**
```json
{
  "owner": "pallets",
  "repo_name": "flask",
  "payload": {
    "issues": [ /* real issues array */ ],
    "prs":    [ /* pull requests array */ ]
  },
  "cached_at": "2026-05-16T05:38:24.130+00:00"
}
```
TTL: 15 minutes. One document per repository. Payload stores issues and PRs pre-split so the app never has to re-filter on cache hit.

MongoDB is best-effort — every function wraps in `try/except`. If Atlas is unreachable or `MONGO_URI` is not set, the app falls through to GitHub API silently. No crashes.

---

## Setup & Deployment

### Prerequisites

- Python 3.11 or higher
- A GitHub account (for generating a Personal Access Token)
- A MongoDB Atlas account (free tier is sufficient)
- WSL or any Unix-like terminal (Windows users)

### Step 1 — Clone / download the project

Place all files in a folder. The `templates/` directory must be inside the project root.

### Step 2 — Install dependencies

```bash
cd github_tracker
pip install -r requirements.txt
```

### Step 3 — Get a GitHub Personal Access Token

1. Go to [github.com](https://github.com) → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Click **Generate new token**
3. Set expiration: 90 days
4. Repository access: **Public repositories (read-only)**
5. No additional permissions needed
6. Copy the token immediately — it is shown only once

### Step 4 — Set up MongoDB Atlas

1. Create a free account at [atlas.mongodb.com](https://atlas.mongodb.com)
2. Create a free **M0** cluster
3. Under **Database Access**, create a database user with read/write permissions
4. Under **Network Access**, add `0.0.0.0/0` (allow all IPs) for local development
5. Click **Connect** → **Drivers** → copy the connection string
6. Replace `<password>` in the connection string with your database user's password

### Step 5 — Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```
GITHUB_TOKEN=ghp_your_token_here
FLASK_SECRET_KEY=any-long-random-string-you-make-up
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/github_tracker?retryWrites=true&w=majority
```

**Never commit `.env` to version control.** It is in `.gitignore` by convention.

### Step 6 — Initialize the database

```bash
python init_db.py
```

This creates `github_tracker.db` with all five tables. Safe to run on a fresh database only — subsequent runs apply migrations automatically (e.g. adding new columns).

### Step 7 — Run the application

```bash
flask --app app run --debug
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

### Resetting the database

```bash
rm github_tracker.db
python init_db.py
```

To also clear the MongoDB cache (e.g. after schema changes):

```python
# python clear_mongo_cache.py
from dotenv import load_dotenv
import os
from pymongo import MongoClient

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI"))
db = client["github_tracker"]
db.issue_cache.delete_many({})
db.repo_cache.delete_many({})
print("Mongo cache cleared.")
```

---

## Routes Reference

| Route | Method | Description |
|---|---|---|
| `/` | GET, POST | Username login. Creates user on first visit. |
| `/logout` | GET | Clears session. |
| `/dashboard` | GET | All tracked repos with cached metadata. |
| `/repos/add` | POST | Add a repo by owner/name. Validates against GitHub before saving. |
| `/repos/<id>` | GET | Repo detail: issues and PRs, separated. |
| `/repos/<id>/refresh` | POST | Force-refresh cached metadata from GitHub. |
| `/issues/save` | POST | Save an issue with a personal note. |
| `/issues` | GET | All saved issues across all tracked repos. |
| `/issues/<id>/delete` | POST | Remove a saved issue. |
| `/stats` | GET | Aggregate: languages, stars, issue/PR counts. |
| `/plans` | GET | Contribution planner. |
| `/plans/add` | POST | Add a plan for a saved issue with target date. |
| `/plans/<id>/done` | POST | Mark a plan as done. |

---

## Expected Output

**Dashboard** — Cards for each tracked repo showing language tag, star count, issue count, and PR count (shown separately once the repo has been browsed). Add-repo form at the top.

**Repo Detail** — Two sections: `🐛 Open Issues` and `⤴ Open Pull Requests`. Each shows number, title, labels, author, and date. Issues have a Save button with an optional note field. A callout box explains that GitHub's reported count includes PRs. The page also shows whether data was served from MongoDB cache or fetched live from GitHub.

**Saved Issues** — A list of bookmarked issues across all repos, with the user's note, save date, and a delete button.

**Stats** — Summary tiles (repos tracked, total stars, total open issues, total open PRs) and a language bar chart built from CSS width percentages.

**Contribution Plans** — A form to attach a plan to any saved issue with a target date. Plans past their target date are highlighted in red as overdue. Done plans grey out with strikethrough.

---

## Known Limitations

- **Pagination:** Issues and PRs are capped at 30 per repository (GitHub's default `per_page`). Repos with hundreds of issues will not show all of them.
- **Authentication:** Session-based username only — no passwords, no GitHub OAuth. Anyone who knows a username can log in as that user. Acceptable for a local demo tool.
- **Private repositories:** Not supported. The app only calls public GitHub API endpoints and does not implement OAuth.
- **Real-time updates:** No WebSockets or background polling. Issue data is updated only on manual Refresh or on cache expiry.
- **Saved issue titles:** Stored at the time of saving. If the issue title changes on GitHub later, the saved row retains the old title.
- **Concurrent users:** SQLite is not suited for high-concurrency production use. Acceptable for a single-user local tool.
- **PR saving:** PRs are displayed but cannot be saved to the contribution planner — only real issues can. This is intentional scope control.

---

## Future Improvements

**Deployment**
- Deploy to AWS EC2 via GitHub Actions CI/CD (push to `main` → build → SSH deploy). This would also close the GitHub Actions practical gap and make the app publicly accessible.
- Containerize with Docker for consistent environment across machines.
- Switch SQLite to PostgreSQL for production-grade concurrency.

**Features**
- **Pagination:** Follow GitHub's `Link` response header to fetch all pages of issues, not just the first 30.
- **Label filtering:** Filter the issue list by GitHub label (e.g. `good first issue`, `help wanted`, `bug`).
- **Issue search:** Full-text search across saved issues by title or note.
- **OAuth login:** Replace username-only sessions with GitHub OAuth so users log in with their actual GitHub account.
- **PR saving:** Allow saving pull requests to the contribution planner (for code review tracking, not just issue contributions).
- **Email/Slack reminders:** Notify users when a contribution plan is overdue.
- **Repo removal:** Currently repos can be added but not removed. Add a delete flow.

**Code Quality**
- `pytest` test suite covering database helpers, `github_api` error paths, and `mongo_cache` TTL logic.
- Input length limits on form fields to prevent oversized payloads.
- Rate limit tracking: display remaining GitHub API calls in the UI.

---

## API Reference

### GitHub REST API v3

Base URL: `https://api.github.com`

| Endpoint | Used For |
|---|---|
| `GET /repos/{owner}/{repo}` | Fetch repo metadata on add and refresh |
| `GET /repos/{owner}/{repo}/issues?state=open&per_page=30` | Fetch open issues and PRs |

Authentication header sent with every request:
```
Authorization: Bearer {GITHUB_TOKEN}
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
```

Unauthenticated rate limit: 60 requests/hour. Authenticated: 5,000 requests/hour.

---

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | Recommended | GitHub Personal Access Token. Without it, rate limit is 60 req/hr. |
| `FLASK_SECRET_KEY` | Yes | Signs the Flask session cookie. Must be random and kept secret. |
| `MONGO_URI` | Optional | MongoDB Atlas connection string. If absent, caching is skipped. |

---

*ENPM818Q – Python Programming for Cloud Engineering · University of Maryland · May 2026*

"""
github_api.py — thin wrapper around GitHub REST API v3.

All external HTTP calls live here. The rest of the app imports from this module,
so if GitHub changes anything we only have one file to fix.

Docs: https://docs.github.com/en/rest
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_API_BASE = "https://api.github.com"

# Read token once at import time. The token is optional (unauthenticated requests get 60 req/hour; authenticated gets 5000 req/hour — use the token).
_TOKEN = os.getenv("GITHUB_TOKEN")
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if _TOKEN:
    _HEADERS["Authorization"] = f"Bearer {_TOKEN}"


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get(path, params=None):
    """
    Make a GET request to GitHub API.
    Returns (data, error_message).
    data is the parsed JSON on success; None on failure.
    error_message is a human-readable string on failure; None on success.
    """
    url = f"{GITHUB_API_BASE}{path}"
    try:
        response = requests.get(url, headers=_HEADERS, params=params, timeout=10)
    except requests.exceptions.RequestException as e:
        return None, f"Network error: {e}"

    if response.status_code == 404:
        return None, "Repository not found. Check owner and repo name."
    if response.status_code == 403:
        return None, "GitHub API rate limit exceeded. Try again later."
    if response.status_code == 401:
        return None, "Invalid GitHub token. Check your .env file."
    if not response.ok:
        return None, f"GitHub API error {response.status_code}: {response.text[:200]}"

    return response.json(), None


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def get_repo(owner, repo_name):
    """
    Fetch metadata for a single repository.

    Returns (repo_dict, error_string).
    repo_dict keys of interest:
        full_name, description, stargazers_count, language,
        open_issues_count, updated_at, html_url

    NOTE: GitHub's open_issues_count includes pull requests.
    The true breakdown is only known after calling get_open_issues().
    """
    data, err = _get(f"/repos/{owner}/{repo_name}")
    return data, err


def get_open_issues(owner, repo_name, per_page=30):
    """
    Fetch open issues AND pull requests for a repo.
    Returns (issues_list, prs_list, error_string).

    GitHub's /issues endpoint mixes real issues and PRs together.
    PRs have a "pull_request" key; real issues do not.
    We split here so callers can display and store them separately.

    On error: returns (None, None, error_string).
    """
    data, err = _get(
        f"/repos/{owner}/{repo_name}/issues",
        params={"state": "open", "per_page": per_page}
    )
    if err:
        return None, None, err

    issues = [item for item in data if "pull_request" not in item]
    prs    = [item for item in data if "pull_request" in item]
    return issues, prs, None

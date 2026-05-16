"""
mongo_cache.py — MongoDB Atlas caching for raw GitHub API responses.

Design: SQLite holds structured working data. Mongo holds raw API payloads.
Before calling GitHub, check Mongo. On cache miss, call GitHub then write to Mongo.

Collections:
    repo_cache   — one doc per (owner, repo_name), raw GitHub repo JSON
    issue_cache  — one doc per (owner, repo_name), list of raw issue JSONs
"""

import os
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_client = None
_db = None

def _get_db():
    """Return the MongoDB database object, connecting on first call."""
    global _client, _db
    if _db is None:
        mongo_uri = os.getenv("MONGO_URI")
        if not mongo_uri:
            raise RuntimeError("MONGO_URI not set in .env — MongoDB caching unavailable.")
        _client = MongoClient(mongo_uri)
        _db = _client["github_tracker"]  #database name
    return _db


# ---------------------------------------------------------------------------
# Repo caching 
# ---------------------------------------------------------------------------

REPO_CACHE_TTL_HOURS = 1  # re-fetch from GitHub if cache is older than this


def get_cached_repo(owner, repo_name):
    """
    Return cached raw repo JSON if it exists and isn't stale.
    Returns None on cache miss or stale entry.
    """
    try:
        db = _get_db()
        doc = db.repo_cache.find_one(
            {"owner": owner, "repo_name": repo_name},
            {"_id": 0}
        )
        if doc is None:
            return None

        # Check freshness
        cached_at = doc.get("cached_at")
        if cached_at and (datetime.now(timezone.utc) - cached_at) < timedelta(hours=REPO_CACHE_TTL_HOURS):
            return doc.get("payload")

        return None  # stale
    except Exception:
        return None  # if Mongo is down, fall through to GitHub API


def cache_repo(owner, repo_name, github_payload):
    """Upsert raw GitHub repo JSON into Mongo."""
    try:
        db = _get_db()
        db.repo_cache.update_one(
            {"owner": owner, "repo_name": repo_name},
            {"$set": {
                "owner": owner,
                "repo_name": repo_name,
                "payload": github_payload,
                "cached_at": datetime.now(timezone.utc),
            }},
            upsert=True
        )
    except Exception:
        pass  #caching is best-effort; never crash the app over it


# ---------------------------------------------------------------------------
# Issue caching
# ---------------------------------------------------------------------------

ISSUE_CACHE_TTL_MINUTES = 15


def get_cached_issues(owner, repo_name):
    """Return cached issues list, or None on miss/stale."""
    try:
        db = _get_db()
        doc = db.issue_cache.find_one(
            {"owner": owner, "repo_name": repo_name},
            {"_id": 0}
        )
        if doc is None:
            return None

        cached_at = doc.get("cached_at")
        if cached_at and (datetime.now(timezone.utc) - cached_at) < timedelta(minutes=ISSUE_CACHE_TTL_MINUTES):
            return doc.get("payload")

        return None
    except Exception:
        return None


def cache_issues(owner, repo_name, issues_payload):
    """Upsert raw GitHub issues list into Mongo."""
    try:
        db = _get_db()
        db.issue_cache.update_one(
            {"owner": owner, "repo_name": repo_name},
            {"$set": {
                "owner": owner,
                "repo_name": repo_name,
                "payload": issues_payload,
                "cached_at": datetime.now(timezone.utc),
            }},
            upsert=True
        )
    except Exception:
        pass

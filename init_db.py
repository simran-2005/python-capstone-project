"""
Run this once before starting the app:
    python init_db.py
"""
import database
database.init_db()
print("Database initialized: github_tracker.db")

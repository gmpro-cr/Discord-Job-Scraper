"""Tests for database.py migrations and schema."""
import os
import sqlite3
import tempfile
import pytest

# Point to a temp DB so tests don't pollute jobs.db
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from database import init_db, get_connection


def test_cv_score_column_exists():
    """cv_score column must exist after init_db()."""
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(job_listings)")
    cols = [row["name"] for row in cursor.fetchall()]
    conn.close()
    assert "cv_score" in cols, f"cv_score column missing; found: {cols}"

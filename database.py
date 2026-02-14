"""
database.py - SQLite database operations for job listings tracking.
Handles creating tables, inserting/querying jobs, deduplication, and statistics.
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_IS_VERCEL = bool(os.environ.get("VERCEL"))
if _IS_VERCEL:
    DB_PATH = "/tmp/jobs.db"
else:
    DB_PATH = os.path.join(os.environ.get("DATA_DIR", _BASE_DIR), "jobs.db")


def get_connection():
    """Get a SQLite connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the job_listings table if it doesn't exist."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_listings (
            job_id TEXT PRIMARY KEY,
            portal TEXT NOT NULL,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            salary TEXT,
            salary_currency TEXT DEFAULT 'INR',
            location TEXT,
            job_description TEXT,
            apply_url TEXT,
            relevance_score INTEGER DEFAULT 0,
            remote_status TEXT DEFAULT 'on-site',
            company_type TEXT DEFAULT 'corporate',
            date_found TEXT NOT NULL,
            date_sent_in_digest TEXT,
            applied_status INTEGER DEFAULT 0,
            applied_date TEXT,
            user_notes TEXT
        )
    """)
    conn.commit()

    # Add date_posted column for actual job posting date (idempotent)
    for col in ["date_posted TEXT"]:
        try:
            cursor.execute(f"ALTER TABLE job_listings ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

    # Add contact enrichment columns (idempotent)
    for col in ["poster_name TEXT", "poster_email TEXT", "poster_phone TEXT", "poster_linkedin TEXT"]:
        try:
            cursor.execute(f"ALTER TABLE job_listings ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DB_PATH)


def generate_job_id(portal, company, role, location):
    """Generate a unique job ID from portal + company + role + location."""
    import hashlib
    raw = f"{portal}:{company}:{role}:{location}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def job_exists(job_id):
    """Check if a job already exists in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM job_listings WHERE job_id = ?", (job_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def was_sent_recently(job_id, days=7):
    """Check if a job was already sent in a digest within the last N days."""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cursor.execute(
        "SELECT 1 FROM job_listings WHERE job_id = ? AND date_sent_in_digest > ?",
        (job_id, cutoff),
    )
    sent = cursor.fetchone() is not None
    conn.close()
    return sent


def insert_job(job):
    """
    Insert a new job into the database. Returns True if inserted, False if duplicate.
    job: dict with keys matching the table columns.
    """
    job_id = job.get("job_id") or generate_job_id(
        job["portal"], job["company"], job["role"], job.get("location", "")
    )
    if job_exists(job_id):
        logger.debug("Job %s already exists, skipping insert", job_id)
        return False

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO job_listings
                (job_id, portal, company, role, salary, salary_currency, location,
                 job_description, apply_url, relevance_score, remote_status,
                 company_type, date_found, date_posted, applied_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                job_id,
                job.get("portal", "unknown"),
                job["company"],
                job["role"],
                job.get("salary"),
                job.get("salary_currency", "INR"),
                job.get("location"),
                job.get("job_description"),
                job.get("apply_url"),
                job.get("relevance_score", 0),
                job.get("remote_status", "on-site"),
                job.get("company_type", "corporate"),
                datetime.now().isoformat(),
                job.get("date_posted"),
            ),
        )
        conn.commit()
        logger.debug("Inserted job %s: %s at %s", job_id, job["role"], job["company"])
        return True
    except sqlite3.IntegrityError:
        logger.debug("Duplicate job_id %s on insert", job_id)
        return False
    finally:
        conn.close()


def insert_jobs_bulk(jobs):
    """Insert multiple jobs, returning counts of inserted and skipped."""
    inserted = 0
    skipped = 0
    for job in jobs:
        if insert_job(job):
            inserted += 1
        else:
            skipped += 1
    return inserted, skipped


def mark_sent_in_digest(job_ids):
    """Mark jobs as sent in today's digest."""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    for jid in job_ids:
        cursor.execute(
            "UPDATE job_listings SET date_sent_in_digest = ? WHERE job_id = ?",
            (now, jid),
        )
    conn.commit()
    conn.close()
    logger.info("Marked %d jobs as sent in digest", len(job_ids))


def update_applied_status(job_id, status, notes=None):
    """Update applied status: 0=not applied, 1=applied, 2=saved for later."""
    conn = get_connection()
    cursor = conn.cursor()
    if status == 1:
        cursor.execute(
            "UPDATE job_listings SET applied_status = ?, applied_date = ?, user_notes = ? WHERE job_id = ?",
            (status, datetime.now().isoformat(), notes, job_id),
        )
    else:
        cursor.execute(
            "UPDATE job_listings SET applied_status = ?, user_notes = ? WHERE job_id = ?",
            (status, notes, job_id),
        )
    conn.commit()
    conn.close()


def get_unsent_jobs(min_score=65, limit=None):
    """Get jobs that haven't been sent in a digest yet, above the minimum score."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT * FROM job_listings
        WHERE (date_sent_in_digest IS NULL)
        AND relevance_score >= ?
        ORDER BY relevance_score DESC
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    cursor.execute(query, (min_score,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_jobs_found_today():
    """Get count of jobs found today."""
    conn = get_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE date_found LIKE ?",
        (f"{today}%",),
    )
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_jobs_found_this_week():
    """Get count of jobs found in the last 7 days."""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE date_found > ?", (cutoff,)
    )
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_portal_stats():
    """Get job count per portal."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT portal, COUNT(*) as cnt FROM job_listings GROUP BY portal ORDER BY cnt DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return {r["portal"]: r["cnt"] for r in rows}


def get_top_companies(limit=5):
    """Get top companies by number of job postings."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT company, COUNT(*) as cnt FROM job_listings GROUP BY company ORDER BY cnt DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [(r["company"], r["cnt"]) for r in rows]


def get_top_roles(limit=5):
    """Get top job titles by frequency."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, COUNT(*) as cnt FROM job_listings GROUP BY role ORDER BY cnt DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [(r["role"], r["cnt"]) for r in rows]


def get_comprehensive_stats():
    """Return a full stats dictionary for display."""
    return {
        "total_jobs": get_total_jobs(),
        "jobs_today": get_jobs_found_today(),
        "jobs_this_week": get_jobs_found_this_week(),
        "portal_stats": get_portal_stats(),
        "top_companies": get_top_companies(5),
        "top_roles": get_top_roles(5),
        "applied_count": get_applied_count(),
        "saved_count": get_saved_count(),
    }


def get_total_jobs():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM job_listings")
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_applied_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE applied_status = 1"
    )
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_saved_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE applied_status = 2"
    )
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_portal_quality_stats():
    """Get average relevance score per portal - shows which portal returns best jobs."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT portal,
               COUNT(*) as total_jobs,
               ROUND(AVG(relevance_score), 1) as avg_score,
               MAX(relevance_score) as max_score,
               SUM(CASE WHEN relevance_score >= 65 THEN 1 ELSE 0 END) as quality_jobs
        FROM job_listings
        GROUP BY portal
        ORDER BY avg_score DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_job_contacts(job_id, poster_name, poster_email, poster_phone, poster_linkedin):
    """Update contact enrichment fields for a job listing."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE job_listings
           SET poster_name = ?, poster_email = ?, poster_phone = ?, poster_linkedin = ?
           WHERE job_id = ?""",
        (poster_name, poster_email, poster_phone, poster_linkedin, job_id),
    )
    conn.commit()
    conn.close()


def get_distinct_locations():
    """Get sorted list of distinct non-null locations from job listings."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT location FROM job_listings WHERE location IS NOT NULL AND location != '' ORDER BY location"
    )
    rows = cursor.fetchall()
    conn.close()
    return [r["location"] for r in rows]


# ---------------------------------------------------------------------------
# Location normalization
# ---------------------------------------------------------------------------

# Map of canonical city name -> patterns that identify it
_CITY_PATTERNS = {
    "Pune": ["pune"],
    "Mumbai": ["mumbai", "navi mumbai", "thane"],
    "Bengaluru": ["bengaluru", "bangalore", "bengaluru"],
    "Delhi / NCR": ["delhi", "noida", "gurgaon", "gurugram", "ghaziabad", "greater noida"],
    "Hyderabad": ["hyderabad", "secunderabad"],
    "Chennai": ["chennai"],
    "Kolkata": ["kolkata"],
    "Ahmedabad": ["ahmedabad"],
    "Remote": ["remote"],
    "India": ["india"],
}


def normalize_location(raw_location):
    """
    Normalize a raw location string to a canonical city name.
    Returns the canonical name or the original string if no match.
    """
    if not raw_location:
        return ""
    raw_lower = raw_location.lower()
    for canonical, patterns in _CITY_PATTERNS.items():
        for pattern in patterns:
            if pattern in raw_lower:
                return canonical
    return raw_location


def get_normalized_locations():
    """
    Get sorted list of canonical (normalized) location names
    with counts, for the filter dropdown.
    Returns list of (canonical_name, count) tuples.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT location FROM job_listings WHERE location IS NOT NULL AND location != ''"
    )
    rows = cursor.fetchall()
    conn.close()

    from collections import Counter
    counts = Counter()
    for r in rows:
        canonical = normalize_location(r["location"])
        counts[canonical] += 1

    # Sort by count descending so most popular cities appear first
    return sorted(counts.items(), key=lambda x: -x[1])

"""
app.py - Flask web UI for Job Search Agent.
Provides a browser-based interface for managing preferences, running the scraper,
viewing jobs, and browsing digests.
"""

import os
import sys
import logging
import threading
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_from_directory,
)

# Ensure project root is on the path so we can import sibling modules
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from main import load_config, load_preferences, save_preferences, DEFAULT_PREFS, apply_env_overrides
from database import (
    init_db, get_connection, get_comprehensive_stats, get_portal_quality_stats,
    update_applied_status, insert_jobs_bulk, generate_job_id, mark_sent_in_digest,
    get_unsent_jobs, update_job_contacts, get_distinct_locations,
    get_normalized_locations, normalize_location, _CITY_PATTERNS,
)
from scrapers import scrape_all_portals
from analyzer import analyze_jobs
from digest_generator import generate_digest, get_latest_digest, DIGEST_DIR
from email_notifier import send_job_email
from apollo_enricher import enrich_jobs_with_contacts
from discord_notifier import send_discord_alert, send_discord_batch_summary

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "job-search-agent-dev-key")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize the database on startup
init_db()

# ---------------------------------------------------------------------------
# Background scraper state
# ---------------------------------------------------------------------------

scraper_status = {
    "running": False,
    "phase": "idle",
    "portal_progress": {},
    "done_portals": 0,
    "total_portals": 0,
    "total_jobs": 0,
    "qualified_jobs": 0,
    "inserted": 0,
    "skipped": 0,
    "digest_path": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
scraper_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Live search state
# ---------------------------------------------------------------------------

live_search_status = {
    "running": False,
    "phase": "idle",
    "portal_progress": {},
    "done_portals": 0,
    "total_portals": 0,
    "total_jobs": 0,
    "qualified_jobs": 0,
    "inserted": 0,
    "skipped": 0,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "result_job_ids": [],
}
live_search_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Daily scheduler (11:00 AM)
# ---------------------------------------------------------------------------

_scheduler = None


def _scheduled_pipeline_run():
    """Callback for the daily scheduled scraper run."""
    global scraper_status
    with scraper_lock:
        if scraper_status["running"]:
            logger.info("Scheduled run skipped - scraper is already running")
            return
        scraper_status = {
            "running": True,
            "phase": "starting",
            "portal_progress": {},
            "done_portals": 0,
            "total_portals": 0,
            "total_jobs": 0,
            "qualified_jobs": 0,
            "inserted": 0,
            "skipped": 0,
            "digest_path": None,
            "error": None,
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
        }
    logger.info("Scheduled daily pipeline run starting")
    _run_scraper_pipeline()


def setup_background_scheduler():
    """Create and start the APScheduler BackgroundScheduler for daily 11 AM runs."""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(
            _scheduled_pipeline_run,
            trigger=CronTrigger(hour=11, minute=0),
            id="daily_pipeline",
            name="Daily job scraper pipeline at 11:00 AM",
            replace_existing=True,
        )
        _scheduler.start()
        logger.info("Background scheduler started - daily pipeline at 11:00 AM")
    except ImportError:
        logger.warning("APScheduler not installed - daily scheduling disabled")
    except Exception as e:
        logger.error("Failed to start background scheduler: %s", e)


def _run_apollo_enrichment(job_ids, api_key):
    """Run Apollo contact enrichment for a list of job IDs."""
    if not api_key:
        return
    conn = get_connection()
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in job_ids)
    cursor.execute(
        f"SELECT job_id, company FROM job_listings WHERE job_id IN ({placeholders}) "
        f"AND (poster_email IS NULL OR poster_email = '')",
        job_ids,
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not rows:
        return

    contacts = enrich_jobs_with_contacts(rows, api_key)
    for jid, info in contacts.items():
        update_job_contacts(
            jid,
            info.get("poster_name", ""),
            info.get("poster_email", ""),
            info.get("poster_phone", ""),
            info.get("poster_linkedin", ""),
        )


def _run_scraper_pipeline():
    """Run the full pipeline in a background thread."""
    global scraper_status
    try:
        config = load_config()
        preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
        job_titles = preferences.get("job_titles", DEFAULT_PREFS["job_titles"])
        locations = preferences.get("locations", DEFAULT_PREFS["locations"])
        top_n = preferences.get("top_jobs_per_digest", 5)

        # Phase 1: Scrape
        with scraper_lock:
            scraper_status["phase"] = "scraping"
            scraper_status["portal_progress"] = {}

        def scrape_cb(portal, status, count, done, total):
            with scraper_lock:
                scraper_status["portal_progress"][portal] = {
                    "status": status, "count": count,
                }
                scraper_status["done_portals"] = done
                scraper_status["total_portals"] = total

        all_jobs, portal_results = scrape_all_portals(
            job_titles, locations, config, progress_callback=scrape_cb,
        )

        with scraper_lock:
            scraper_status["total_jobs"] = len(all_jobs)

        if not all_jobs:
            with scraper_lock:
                scraper_status["phase"] = "done"
                scraper_status["finished_at"] = datetime.now().isoformat()
                scraper_status["running"] = False
            return

        # Phase 2: Analyze
        with scraper_lock:
            scraper_status["phase"] = "analyzing"

        qualified_jobs, all_analyzed = analyze_jobs(all_jobs, preferences, config)

        with scraper_lock:
            scraper_status["qualified_jobs"] = len(qualified_jobs)

        # Phase 3: Store
        with scraper_lock:
            scraper_status["phase"] = "storing"

        for job in all_analyzed:
            job["job_id"] = generate_job_id(
                job["portal"], job["company"], job["role"], job.get("location", ""),
            )
        inserted, skipped = insert_jobs_bulk(all_analyzed)

        with scraper_lock:
            scraper_status["inserted"] = inserted
            scraper_status["skipped"] = skipped

        # Phase 3.5: Discord alerts
        discord_url = preferences.get("discord_webhook_url", "").strip()
        discord_min = int(preferences.get("discord_min_score", 65))
        if discord_url:
            with scraper_lock:
                scraper_status["phase"] = "discord_alerts"
            alert_count = 0
            for job in qualified_jobs:
                if job.get("relevance_score", 0) >= discord_min:
                    send_discord_alert(job, discord_url)
                    alert_count += 1
            if alert_count > 0 or inserted > 0:
                send_discord_batch_summary(len(all_jobs), len(qualified_jobs), inserted, discord_url)
            logger.info("Sent %d Discord alerts", alert_count)

        # Phase 3.6: Apollo contact enrichment
        apollo_key = preferences.get("apollo_api_key", "").strip()
        if apollo_key:
            with scraper_lock:
                scraper_status["phase"] = "enriching_contacts"
            all_job_ids = [j["job_id"] for j in all_analyzed if j.get("job_id")]
            _run_apollo_enrichment(all_job_ids, apollo_key)

        # Phase 4: Digest
        with scraper_lock:
            scraper_status["phase"] = "generating_digest"

        digest_jobs = qualified_jobs[:top_n]
        stats = get_comprehensive_stats()
        html_path, _ = generate_digest(
            digest_jobs, portal_results, preferences, stats, open_browser=False,
        )

        sent_ids = [j.get("job_id") for j in digest_jobs if j.get("job_id")]
        if sent_ids:
            mark_sent_in_digest(sent_ids)

        with scraper_lock:
            scraper_status["digest_path"] = os.path.basename(html_path)

        # Phase 5: Email notification
        recipient = preferences.get("email", "").strip()
        gmail_addr = preferences.get("gmail_address", "").strip()
        gmail_pass = preferences.get("gmail_app_password", "").strip()
        if recipient and gmail_addr and gmail_pass:
            with scraper_lock:
                scraper_status["phase"] = "sending_email"
            try:
                email_jobs = digest_jobs if digest_jobs else []
                send_job_email(recipient, email_jobs, preferences)
                logger.info("Email digest sent to %s", recipient)
            except Exception as e:
                logger.error("Failed to send email: %s", e)

        with scraper_lock:
            scraper_status["phase"] = "done"
            scraper_status["finished_at"] = datetime.now().isoformat()
            scraper_status["running"] = False

    except Exception as e:
        logger.exception("Scraper pipeline error")
        with scraper_lock:
            scraper_status["error"] = str(e)
            scraper_status["phase"] = "error"
            scraper_status["running"] = False


def _run_live_search(query, location):
    """Run a slim scrape+analyze+store pipeline for live search from the jobs page."""
    global live_search_status
    try:
        config = load_config()
        preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())

        job_titles = [query] if query else preferences.get("job_titles", DEFAULT_PREFS["job_titles"])
        locations_list = [location] if location else preferences.get("locations", DEFAULT_PREFS["locations"])

        # Phase 1: Scrape
        with live_search_lock:
            live_search_status["phase"] = "scraping"
            live_search_status["portal_progress"] = {}

        def scrape_cb(portal, status, count, done, total):
            with live_search_lock:
                live_search_status["portal_progress"][portal] = {
                    "status": status, "count": count,
                }
                live_search_status["done_portals"] = done
                live_search_status["total_portals"] = total

        all_jobs, portal_results = scrape_all_portals(
            job_titles, locations_list, config, progress_callback=scrape_cb,
        )

        with live_search_lock:
            live_search_status["total_jobs"] = len(all_jobs)

        if not all_jobs:
            with live_search_lock:
                live_search_status["phase"] = "done"
                live_search_status["finished_at"] = datetime.now().isoformat()
                live_search_status["running"] = False
            return

        # Phase 2: Analyze
        with live_search_lock:
            live_search_status["phase"] = "analyzing"

        qualified_jobs, all_analyzed = analyze_jobs(all_jobs, preferences, config)

        with live_search_lock:
            live_search_status["qualified_jobs"] = len(qualified_jobs)

        # Phase 3: Store
        with live_search_lock:
            live_search_status["phase"] = "storing"

        for job in all_analyzed:
            job["job_id"] = generate_job_id(
                job["portal"], job["company"], job["role"], job.get("location", ""),
            )
        inserted, skipped = insert_jobs_bulk(all_analyzed)
        result_ids = [j["job_id"] for j in all_analyzed if j.get("job_id")]

        with live_search_lock:
            live_search_status["inserted"] = inserted
            live_search_status["skipped"] = skipped
            live_search_status["result_job_ids"] = result_ids

        # Phase 3.5: Discord alerts
        discord_url = preferences.get("discord_webhook_url", "").strip()
        discord_min = int(preferences.get("discord_min_score", 65))
        if discord_url:
            with live_search_lock:
                live_search_status["phase"] = "discord_alerts"
            for job in qualified_jobs:
                if job.get("relevance_score", 0) >= discord_min:
                    send_discord_alert(job, discord_url)

        # Phase 4: Apollo enrichment
        apollo_key = preferences.get("apollo_api_key", "").strip()
        if apollo_key and result_ids:
            with live_search_lock:
                live_search_status["phase"] = "enriching_contacts"
            _run_apollo_enrichment(result_ids, apollo_key)

        with live_search_lock:
            live_search_status["phase"] = "done"
            live_search_status["finished_at"] = datetime.now().isoformat()
            live_search_status["running"] = False

    except Exception as e:
        logger.exception("Live search pipeline error")
        with live_search_lock:
            live_search_status["error"] = str(e)
            live_search_status["phase"] = "error"
            live_search_status["running"] = False


# Guard against double-fire with Flask debug reloader
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    setup_background_scheduler()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    stats = get_comprehensive_stats()
    portal_quality = get_portal_quality_stats()
    return render_template("dashboard.html", stats=stats, portal_quality=portal_quality)


@app.route("/jobs")
def jobs():
    # Read filter params
    search = request.args.get("search", "").strip()
    portal = request.args.get("portal", "")
    remote = request.args.get("remote", "")
    company_type = request.args.get("company_type", "")
    sort = request.args.get("sort", "score_desc")
    applied = request.args.get("applied", "")
    location = request.args.get("location", "")
    recency = request.args.get("recency", "")
    min_score = request.args.get("min_score", "40")  # default 40 to cut noise
    page = max(1, int(request.args.get("page", "1")))
    per_page = 25

    conn = get_connection()
    cursor = conn.cursor()

    # Build query
    conditions = []
    params = []

    # Default minimum score filter (0 = show all)
    try:
        min_score_val = int(min_score)
    except (ValueError, TypeError):
        min_score_val = 40
    if min_score_val > 0:
        conditions.append("relevance_score >= ?")
        params.append(min_score_val)

    if search:
        conditions.append("(role LIKE ? OR company LIKE ? OR job_description LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if portal:
        conditions.append("portal = ?")
        params.append(portal)
    if remote:
        conditions.append("remote_status = ?")
        params.append(remote)
    if company_type:
        conditions.append("company_type = ?")
        params.append(company_type)
    if location:
        # Use normalized location matching: find the patterns for this canonical city
        city_patterns = _CITY_PATTERNS.get(location)
        if city_patterns:
            like_clauses = ["location LIKE ?" for _ in city_patterns]
            conditions.append("(" + " OR ".join(like_clauses) + ")")
            params.extend([f"%{p}%" for p in city_patterns])
        else:
            conditions.append("location LIKE ?")
            params.append(f"%{location}%")
    if recency:
        recency_map = {
            "24h": timedelta(hours=24),
            "3d": timedelta(days=3),
            "1w": timedelta(weeks=1),
            "1m": timedelta(days=30),
        }
        td = recency_map.get(recency)
        if td:
            cutoff_date = (datetime.now() - td).strftime("%Y-%m-%d")
            # Only show jobs that have a known posting date within the range.
            # Jobs without date_posted are excluded from strict recency filters.
            conditions.append(
                "(date_posted IS NOT NULL AND date_posted != '' AND date_posted >= ?)"
            )
            params.append(cutoff_date)
    if applied == "applied":
        conditions.append("applied_status = 1")
    elif applied == "saved":
        conditions.append("applied_status = 2")
    elif applied == "none":
        conditions.append("applied_status = 0")

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    # Sort
    sort_map = {
        "score_desc": "relevance_score DESC",
        "score_asc": "relevance_score ASC",
        "date_desc": "date_found DESC",
        "date_asc": "date_found ASC",
        "company_asc": "company ASC",
    }
    order = sort_map.get(sort, "relevance_score DESC")

    # Count
    cursor.execute(f"SELECT COUNT(*) as cnt FROM job_listings{where}", params)
    total = cursor.fetchone()["cnt"]

    # Fetch page
    offset = (page - 1) * per_page
    cursor.execute(
        f"SELECT * FROM job_listings{where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    total_pages = max(1, (total + per_page - 1) // per_page)

    # Get distinct portals for filter dropdown
    conn2 = get_connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT DISTINCT portal FROM job_listings ORDER BY portal")
    portals = [r["portal"] for r in cur2.fetchall()]
    conn2.close()

    # Get normalized locations for filter dropdown (canonical name + count)
    normalized_locs = get_normalized_locations()

    return render_template(
        "jobs.html",
        jobs=rows, total=total, page=page, total_pages=total_pages,
        portals=portals, locations=normalized_locs,
        filters={
            "search": search, "portal": portal, "remote": remote,
            "company_type": company_type, "sort": sort, "applied": applied,
            "location": location, "recency": recency, "min_score": min_score,
        },
    )


@app.route("/api/jobs/<job_id>/status", methods=["POST"])
def update_job_status(job_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status", 0)
    notes = data.get("notes")
    try:
        status = int(status)
    except (ValueError, TypeError):
        status = 0
    update_applied_status(job_id, status, notes)
    return jsonify({"ok": True, "job_id": job_id, "status": status})


@app.route("/preferences", methods=["GET", "POST"])
def preferences():
    config = load_config()
    if request.method == "POST":
        prefs = {
            "job_titles": [
                t.strip() for t in request.form.get("job_titles", "").split(",") if t.strip()
            ],
            "locations": [
                l.strip() for l in request.form.get("locations", "").split(",") if l.strip()
            ],
            "industries": [
                i.strip() for i in request.form.get("industries", "").split(",") if i.strip()
            ],
            "top_jobs_per_digest": max(3, min(10, int(request.form.get("top_jobs", "5")))),
            "digest_time": request.form.get("digest_time", "6:00 AM").strip(),
            "email": request.form.get("email", "").strip(),
            "gmail_address": request.form.get("gmail_address", "").strip(),
            "gmail_app_password": request.form.get("gmail_app_password", "").strip(),
            "apollo_api_key": request.form.get("apollo_api_key", "").strip(),
            "discord_webhook_url": request.form.get("discord_webhook_url", "").strip(),
            "discord_min_score": max(0, min(100, int(request.form.get("discord_min_score", "65")))),
        }
        save_preferences(prefs)
        flash("Preferences saved successfully!", "success")
        return redirect(url_for("preferences"))

    prefs = load_preferences() or DEFAULT_PREFS.copy()
    return render_template("preferences.html", prefs=prefs, config=config)


@app.route("/scraper")
def scraper():
    return render_template("scraper.html")


@app.route("/api/scraper/start", methods=["POST"])
def start_scraper():
    global scraper_status
    with scraper_lock:
        if scraper_status["running"]:
            return jsonify({"ok": False, "error": "Scraper is already running"}), 409
        scraper_status = {
            "running": True,
            "phase": "starting",
            "portal_progress": {},
            "done_portals": 0,
            "total_portals": 0,
            "total_jobs": 0,
            "qualified_jobs": 0,
            "inserted": 0,
            "skipped": 0,
            "digest_path": None,
            "error": None,
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
        }
    t = threading.Thread(target=_run_scraper_pipeline, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/scraper/status")
def scraper_status_api():
    with scraper_lock:
        return jsonify(dict(scraper_status))


# ---------------------------------------------------------------------------
# Live Search API
# ---------------------------------------------------------------------------

@app.route("/api/search/start", methods=["POST"])
def start_live_search():
    global live_search_status
    data = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()
    location = data.get("location", "").strip()

    with live_search_lock:
        if live_search_status["running"]:
            return jsonify({"ok": False, "error": "A search is already running"}), 409
        live_search_status = {
            "running": True,
            "phase": "starting",
            "portal_progress": {},
            "done_portals": 0,
            "total_portals": 0,
            "total_jobs": 0,
            "qualified_jobs": 0,
            "inserted": 0,
            "skipped": 0,
            "error": None,
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "result_job_ids": [],
        }
    t = threading.Thread(target=_run_live_search, args=(query, location), daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/search/status")
def live_search_status_api():
    with live_search_lock:
        return jsonify(dict(live_search_status))


# ---------------------------------------------------------------------------
# Scheduler & Digests
# ---------------------------------------------------------------------------

@app.route("/api/scheduler/status")
def scheduler_status():
    """Return the current scheduler state and next run time."""
    if _scheduler and _scheduler.running:
        job = _scheduler.get_job("daily_pipeline")
        if job:
            next_run = job.next_run_time
            return jsonify({
                "enabled": True,
                "next_run": next_run.isoformat() if next_run else None,
                "next_run_human": next_run.strftime("%B %d, %Y at %I:%M %p") if next_run else None,
            })
    return jsonify({"enabled": False, "next_run": None, "next_run_human": None})


@app.route("/digests")
def digests():
    files = []
    if os.path.isdir(DIGEST_DIR):
        for f in sorted(os.listdir(DIGEST_DIR), reverse=True):
            if f.endswith(".html"):
                path = os.path.join(DIGEST_DIR, f)
                mtime = os.path.getmtime(path)
                files.append({
                    "filename": f,
                    "date": datetime.fromtimestamp(mtime).strftime("%B %d, %Y %I:%M %p"),
                    "size_kb": round(os.path.getsize(path) / 1024, 1),
                })
    return render_template("digests.html", files=files)


@app.route("/digests/<filename>")
def serve_digest(filename):
    return send_from_directory(DIGEST_DIR, filename)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5001)

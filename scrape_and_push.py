#!/usr/bin/env python3
"""
scrape_and_push.py - Standalone scraper that runs on GitHub Actions.

Scrapes all job portals, analyzes/scores jobs, then POSTs results
to the Render-hosted app via the /api/jobs/import endpoint.

Required env vars:
    RENDER_APP_URL   - e.g. https://job-search-agent.onrender.com
    IMPORT_SECRET    - shared secret for the import API
    OPENROUTER_API_KEY - (optional) for AI-based scoring
"""

import json
import logging
import os
import sys

# Ensure project root is importable
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Load .env if present (for local testing)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

import requests
from main import load_config, load_preferences, DEFAULT_PREFS, apply_env_overrides
from scrapers import scrape_all_portals
from analyzer import analyze_jobs
from database import generate_job_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    render_url = os.environ.get("RENDER_APP_URL", "").rstrip("/")
    import_secret = os.environ.get("IMPORT_SECRET", "")

    if not render_url:
        logger.error("RENDER_APP_URL env var is required")
        sys.exit(1)
    if not import_secret:
        logger.error("IMPORT_SECRET env var is required")
        sys.exit(1)

    config = load_config()
    preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
    job_titles = preferences.get("job_titles", DEFAULT_PREFS["job_titles"])
    locations = preferences.get("locations", DEFAULT_PREFS["locations"])

    # --- Phase 1: Scrape ---
    logger.info("Scraping %d titles across %d locations...", len(job_titles), len(locations))
    all_jobs, portal_results = scrape_all_portals(job_titles, locations, config)

    for portal, result in portal_results.items():
        logger.info("  %s: %s (%d jobs)", portal, result.get("status"), result.get("count", 0))

    if not all_jobs:
        logger.warning("No jobs scraped. Exiting.")
        return

    logger.info("Total raw jobs scraped: %d", len(all_jobs))

    # --- Phase 2: Analyze ---
    logger.info("Analyzing and scoring jobs...")
    qualified_jobs, all_analyzed = analyze_jobs(all_jobs, preferences, config)
    logger.info("Analyzed %d jobs, %d qualified (score >= threshold)", len(all_analyzed), len(qualified_jobs))

    # --- Phase 3: Generate IDs ---
    for job in all_analyzed:
        job["job_id"] = generate_job_id(
            job.get("portal", "unknown"),
            job.get("company", ""),
            job.get("role", ""),
            job.get("location", ""),
        )

    # --- Phase 4: Push to Render ---
    # Serialize jobs for JSON transport (strip non-serializable fields)
    serializable_fields = [
        "job_id", "portal", "company", "role", "salary", "salary_currency",
        "location", "job_description", "apply_url", "relevance_score",
        "remote_status", "company_type", "date_posted",
        "experience_min", "experience_max", "salary_min", "salary_max",
        "company_size", "company_funding_stage", "company_glassdoor_rating",
    ]
    payload_jobs = []
    for job in all_analyzed:
        clean = {}
        for key in serializable_fields:
            if key in job and job[key] is not None:
                clean[key] = job[key]
        payload_jobs.append(clean)

    endpoint = f"{render_url}/api/jobs/import"
    payload = {"secret": import_secret, "jobs": payload_jobs}
    payload_size = len(json.dumps(payload))
    logger.info("Pushing %d jobs to %s (payload: %.1f KB)...", len(payload_jobs), endpoint, payload_size / 1024)

    try:
        resp = requests.post(endpoint, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        logger.info(
            "Import successful: inserted=%s, skipped=%s, alerts=%s",
            result.get("inserted"), result.get("skipped"), result.get("alerts"),
        )
    except requests.RequestException as e:
        logger.error("Failed to push jobs: %s", e)
        if hasattr(e, "response") and e.response is not None:
            logger.error("Response body: %s", e.response.text[:500])
        sys.exit(1)


if __name__ == "__main__":
    main()

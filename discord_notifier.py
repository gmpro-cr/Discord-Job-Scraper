"""
discord_notifier.py - Send job alerts to Discord via webhook.
Uses Discord's webhook API (no bot library needed).
"""

import logging
import requests

logger = logging.getLogger(__name__)


def _score_color(score):
    """Return Discord embed color based on relevance score."""
    if score >= 75:
        return 0x2ECC71  # green
    if score >= 50:
        return 0xF1C40F  # yellow
    return 0xE67E22      # orange


def send_discord_alert(job, webhook_url):
    """
    Post a single job as a rich embed to a Discord webhook.
    job: dict with keys like role, company, location, relevance_score, etc.
    """
    if not webhook_url:
        return

    score = job.get("relevance_score", 0)
    fields = [
        {"name": "Company", "value": job.get("company", "Unknown"), "inline": True},
        {"name": "Score", "value": f"{score}/100", "inline": True},
    ]

    location = job.get("location")
    if location:
        fields.append({"name": "Location", "value": location, "inline": True})

    remote = job.get("remote_status")
    if remote and remote != "on-site":
        fields.append({"name": "Remote", "value": remote.title(), "inline": True})

    salary = job.get("salary")
    if salary:
        fields.append({"name": "Salary", "value": salary, "inline": True})

    portal = job.get("portal")
    if portal:
        fields.append({"name": "Portal", "value": portal.title(), "inline": True})

    embed = {
        "title": job.get("role", "New Job Found"),
        "color": _score_color(score),
        "fields": fields,
        "footer": {"text": "Job Search Agent"},
    }

    apply_url = job.get("apply_url")
    if apply_url:
        embed["url"] = apply_url

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 204:
            logger.info("Discord alert sent: %s at %s", job.get("role"), job.get("company"))
        else:
            logger.warning("Discord webhook returned %d: %s", resp.status_code, resp.text[:200])
    except requests.RequestException as e:
        logger.error("Discord webhook failed: %s", e)


def send_discord_batch_summary(total_found, qualified_count, inserted_count, webhook_url):
    """Post a summary embed after a scraping run completes."""
    if not webhook_url:
        return

    embed = {
        "title": "Scraping Run Complete",
        "color": 0x3498DB,  # blue
        "fields": [
            {"name": "Total Found", "value": str(total_found), "inline": True},
            {"name": "Qualified", "value": str(qualified_count), "inline": True},
            {"name": "New (Inserted)", "value": str(inserted_count), "inline": True},
        ],
        "footer": {"text": "Job Search Agent"},
    }

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code != 204:
            logger.warning("Discord summary webhook returned %d", resp.status_code)
    except requests.RequestException as e:
        logger.error("Discord summary webhook failed: %s", e)

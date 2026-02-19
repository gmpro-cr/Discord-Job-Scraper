"""
telegram_notifier.py - Send job alerts to Telegram via Bot API.
Uses requests.post to the Telegram sendMessage endpoint.
"""

import logging
import requests

logger = logging.getLogger(__name__)


def _score_emoji(score):
    """Return an emoji circle based on relevance score."""
    if score >= 75:
        return "\U0001f7e2"  # green circle
    if score >= 50:
        return "\U0001f7e1"  # yellow circle
    return "\U0001f7e0"      # orange circle


def send_telegram_alert(job, bot_token, chat_id):
    """
    Send a single job as a formatted HTML message to a Telegram chat.
    job: dict with keys like role, company, location, relevance_score, etc.
    """
    if not bot_token or not chat_id:
        return

    score = job.get("relevance_score", 0)
    emoji = _score_emoji(score)

    lines = [
        f"<b>{job.get('role', 'New Job Found')}</b>",
        f"Company: {job.get('company', 'Unknown')}",
        f"Score: {emoji} {score}/100",
    ]

    location = job.get("location")
    if location:
        remote = job.get("remote_status", "")
        if remote and remote != "on-site":
            location = f"{location} ({remote.title()})"
        lines.append(f"Location: {location}")

    salary = job.get("salary")
    if salary:
        lines.append(f"Salary: {salary}")

    portal = job.get("portal")
    if portal:
        lines.append(f"Portal: {portal.title()}")

    apply_url = job.get("apply_url")
    if apply_url:
        lines.append(f'\n<a href="{apply_url}">Apply</a>')

    text = "\n".join(lines)

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            logger.info("Telegram alert sent: %s at %s", job.get("role"), job.get("company"))
        else:
            logger.warning("Telegram API error: %s", data.get("description", resp.text[:200]))
    except requests.RequestException as e:
        logger.error("Telegram alert failed: %s", e)


def send_telegram_batch_summary(total_found, qualified_count, inserted_count, bot_token, chat_id):
    """Send a summary message after a scraping run completes."""
    if not bot_token or not chat_id:
        return

    text = (
        "<b>Scraping Run Complete</b>\n"
        f"Total Found: {total_found}\n"
        f"Qualified: {qualified_count}\n"
        f"New (Inserted): {inserted_count}"
    )

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.warning("Telegram summary error: %s", data.get("description"))
    except requests.RequestException as e:
        logger.error("Telegram summary failed: %s", e)

"""
telegram_bot.py - Interactive Telegram bot for the Job Search Agent.
Lets users query jobs using natural language from Telegram.
Every message is treated as a job search query and parsed via NLP.

Runs in a background thread alongside the Flask app.
"""

import asyncio
import logging
import threading

import requests as http_requests

from database import get_comprehensive_stats
from telegram_notifier import _score_emoji

logger = logging.getLogger(__name__)

# The port the Flask app listens on (used for API calls)
_FLASK_PORT = int(__import__("os").environ.get("PORT", 5001))


# ---------------------------------------------------------------------------
# NLP search (the primary handler for all job queries)
# ---------------------------------------------------------------------------

def _nlp_search(query):
    """Call the NLP search API endpoint and return (filters_dict, filter_labels, jobs, total)."""
    try:
        resp = http_requests.post(
            f"http://localhost:{_FLASK_PORT}/api/nlp-search",
            json={"query": query},
            timeout=30,
        )
        data = resp.json()
        if data.get("ok"):
            return data.get("filters", {}), data.get("filter_labels", []), data.get("jobs", []), data.get("total", 0)
        return {}, [], [], 0
    except Exception as e:
        logger.warning("NLP search API call failed: %s", e)
        return None, None, None, None


def _format_jobs_html(query, filter_labels, jobs, total):
    """Build a Telegram HTML message for NLP search results."""
    lines = []

    if filter_labels:
        lines.append("<b>I understood:</b>")
        lines.append(" | ".join(f"<b>{label}</b>" for label in filter_labels))
    else:
        lines.append(f"<b>Results for:</b> {query}")

    if not jobs:
        lines.append("\nNo jobs found matching your query. Try different words or broader terms.")
        return "\n".join(lines)

    lines.append("")
    for j in jobs[:7]:
        score = j.get("relevance_score", 0)
        emoji = _score_emoji(score)
        loc = j.get("location") or "N/A"
        remote = j.get("remote_status", "")
        if remote and remote != "on-site":
            loc = f"{loc} ({remote.title()})"
        portal = (j.get("portal") or "").title()

        salary_text = ""
        if j.get("salary"):
            salary_text = f" | {j.get('salary_currency', '')} {j['salary']}"

        role = j.get("role", "Unknown")
        company = j.get("company", "Unknown")
        apply_url = j.get("apply_url")

        lines.append(f"{emoji} <b>{role}</b> @ {company}")
        lines.append(f"   Score: {score}/100 | {loc} | {portal}{salary_text}")
        if apply_url:
            lines.append(f'   <a href="{apply_url}">Apply</a>')
        lines.append("")

    if total > 7:
        lines.append(f"<i>Showing 7 of {total} matches</i>")
    else:
        lines.append(f"<i>{total} match{'es' if total != 1 else ''}</i>")

    return "\n".join(lines)


def _format_stats_html():
    """Build a Telegram HTML message with job search statistics."""
    stats = get_comprehensive_stats()
    lines = [
        "<b>Job Search Statistics</b>",
        "",
        f"Total Jobs: {stats['total_jobs']}",
        f"Found Today: {stats['jobs_today']}",
        f"This Week: {stats['jobs_this_week']}",
        f"Applied: {stats['applied_count']}",
        f"Saved: {stats['saved_count']}",
    ]

    if stats["portal_stats"]:
        lines.append("\n<b>Jobs per Portal:</b>")
        for p, c in stats["portal_stats"].items():
            lines.append(f"  {p}: {c}")

    if stats["top_companies"]:
        lines.append("\n<b>Top Companies:</b>")
        for name, count in stats["top_companies"]:
            lines.append(f"  {name}: {count}")

    return "\n".join(lines)


def _trigger_scrape():
    """Call the Flask API to start a scraper run. Returns (ok, message)."""
    try:
        resp = http_requests.post(
            f"http://localhost:{_FLASK_PORT}/api/scraper/start",
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            return True, "Scraper started! I'll send alerts as jobs are found."
        return False, data.get("error", "Failed to start scraper.")
    except Exception as e:
        return False, f"Could not reach the server: {e}"


HELP_TEXT = """<b>Hey! I'm your Job Search Agent bot.</b>

Just type what you're looking for - I understand natural language:

"remote PM jobs in Bangalore above 20 lakhs"
"startup roles in Mumbai"
"senior engineer positions 3-7 years experience"
"hybrid jobs in Delhi under 30 lpa"
"fintech companies hiring"
"jobs at Google"
"product manager"

I'll figure out the filters from your message and show matching jobs.

<b>Commands:</b>
/stats - job search statistics
/scrape - trigger a new scraper run
/help - this message
"""


# ---------------------------------------------------------------------------
# Bot setup using python-telegram-bot
# ---------------------------------------------------------------------------

def start_telegram_bot(token):
    """
    Start the Telegram bot in a background daemon thread.
    Safe to call from the Flask startup path.
    """
    if not token:
        logger.info("No Telegram bot token provided - bot disabled")
        return

    def _run():
        from telegram import Update
        from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

        async def cmd_start(update: Update, context):
            await update.message.reply_text(HELP_TEXT, parse_mode="HTML")

        async def cmd_help(update: Update, context):
            await update.message.reply_text(HELP_TEXT, parse_mode="HTML")

        async def cmd_stats(update: Update, context):
            text = _format_stats_html()
            await update.message.reply_text(text, parse_mode="HTML")

        async def cmd_scrape(update: Update, context):
            await update.message.reply_text("On it - starting a scraper run...")
            ok, msg = _trigger_scrape()
            await update.message.reply_text(msg if ok else f"Hmm, something went wrong: {msg}")

        async def handle_message(update: Update, context):
            text = (update.message.text or "").strip()
            if not text:
                await update.message.reply_text(
                    "Hey! Just tell me what kind of jobs you're looking for.\n"
                    'e.g. "remote PM jobs in Bangalore above 20 lakhs"'
                )
                return

            filters_dict, filter_labels, jobs, total = _nlp_search(text)

            if filters_dict is None:
                await update.message.reply_text(
                    "Couldn't reach the search service right now. "
                    "Make sure the server is running and try again."
                )
                return

            reply = _format_jobs_html(text, filter_labels, jobs, total)
            await update.message.reply_text(reply, parse_mode="HTML", disable_web_page_preview=True)

        app = ApplicationBuilder().token(token).build()
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("help", cmd_help))
        app.add_handler(CommandHandler("stats", cmd_stats))
        app.add_handler(CommandHandler("scrape", cmd_scrape))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("Telegram bot starting polling...")
        app.run_polling(drop_pending_updates=True)

    t = threading.Thread(target=_run, daemon=True, name="telegram-bot")
    t.start()
    logger.info("Telegram bot thread started")

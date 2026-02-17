"""
discord_bot.py - Interactive Discord bot for the Job Search Agent.
Lets users query jobs using natural language from Discord.
Every message is treated as a job search query and parsed via NLP —
no rigid command patterns or specific phrases needed.

Runs in a background thread alongside the Flask app.
"""

import asyncio
import logging
import re
import time
import threading

import discord
import requests as http_requests

from database import get_connection, get_comprehensive_stats
from discord_notifier import _score_color

logger = logging.getLogger(__name__)

# The port the Flask app listens on (used for API calls)
_FLASK_PORT = int(__import__("os").environ.get("PORT", 5001))


# ---------------------------------------------------------------------------
# Only these three intents are handled specially (they are actions, not searches)
# ---------------------------------------------------------------------------

_STATS_PATTERNS = [
    r"^\s*stats?\s*$",
    r"^\s*statistics\s*$",
    r"^\s*summary\s*$",
    r"^\s*dashboard\s*$",
    r"^\s*overview\s*$",
    r"\bhow\s+many\b.*\bjobs?\b",
]

_SCRAPE_PATTERNS = [
    r"\bstart\b.*\bscrap",
    r"\brun\b.*\bscrap",
    r"\btrigger\b.*\bscrap",
    r"\bgo\b.*\bscrap",
    r"^\s*scrape\s*$",
]

_HELP_PATTERNS = [
    r"^\s*help\s*$",
    r"\bwhat\s+can\s+you\s+do\b",
    r"^\s*commands?\s*$",
]


def _match_any(text, patterns):
    """Return the first regex match against any pattern, or None."""
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m
    return None


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
        return None, None, None, None  # distinguish API failure from 0 results


def _nlp_jobs_embed(query, filter_labels, jobs, total):
    """Build a Discord embed for NLP search results."""
    embed = discord.Embed(color=0x3498DB)

    # Show parsed understanding
    if filter_labels:
        embed.title = "I understood:"
        embed.description = " | ".join(f"**{label}**" for label in filter_labels)
    else:
        embed.title = f"Results for: {query}"

    if not jobs:
        embed.add_field(
            name="No results",
            value="No jobs found matching your query. Try different words or broader terms.",
            inline=False,
        )
        embed.color = 0x95A5A6
        return embed

    # Show up to 7 jobs in embed (Discord embed field limit)
    for j in jobs[:7]:
        score = j.get("relevance_score", 0)
        loc = j.get("location") or "N/A"
        remote = j.get("remote_status", "")
        if remote and remote != "on-site":
            loc = f"{loc} ({remote.title()})"
        portal = (j.get("portal") or "").title()

        salary_text = ""
        if j.get("salary"):
            salary_text = f" | {j.get('salary_currency', '')} {j['salary']}"

        name = f"{j.get('role', 'Unknown')} @ {j.get('company', 'Unknown')}"
        value = f"Score: **{score}**/100 | {loc} | {portal}{salary_text}"
        url = j.get("apply_url")
        if url:
            value += f"\n[Apply]({url})"

        embed.add_field(name=name, value=value, inline=False)

    if total > 7:
        embed.set_footer(text=f"Showing 7 of {total} matches | Job Search Agent")
    else:
        embed.set_footer(text=f"{total} match{'es' if total != 1 else ''} | Job Search Agent")

    return embed


# ---------------------------------------------------------------------------
# Stats & scrape helpers
# ---------------------------------------------------------------------------

def _stats_embed():
    """Build a Discord embed with job search statistics."""
    stats = get_comprehensive_stats()
    embed = discord.Embed(title="Job Search Statistics", color=0x9B59B6)

    embed.add_field(name="Total Jobs", value=str(stats["total_jobs"]), inline=True)
    embed.add_field(name="Found Today", value=str(stats["jobs_today"]), inline=True)
    embed.add_field(name="This Week", value=str(stats["jobs_this_week"]), inline=True)
    embed.add_field(name="Applied", value=str(stats["applied_count"]), inline=True)
    embed.add_field(name="Saved", value=str(stats["saved_count"]), inline=True)

    if stats["portal_stats"]:
        portal_lines = [f"**{p}**: {c}" for p, c in stats["portal_stats"].items()]
        embed.add_field(
            name="Jobs per Portal",
            value="\n".join(portal_lines) or "None",
            inline=False,
        )

    if stats["top_companies"]:
        company_lines = [f"**{name}**: {count}" for name, count in stats["top_companies"]]
        embed.add_field(
            name="Top Companies",
            value="\n".join(company_lines),
            inline=False,
        )

    embed.set_footer(text="Job Search Agent")
    return embed


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


HELP_TEXT = """**Hey! I'm your Job Search Agent bot.**

Just type what you're looking for — I understand natural language:

"remote PM jobs in Bangalore above 20 lakhs"
"startup roles in Mumbai"
"senior engineer positions 3-7 years experience"
"hybrid jobs in Delhi under 30 lpa"
"fintech companies hiring"
"jobs at Google"
"product manager"

I'll figure out the filters (location, salary, experience, remote, etc.) from your message and show matching jobs.

**Other commands:**
**stats** — job search statistics
**scrape** — trigger a new scraper run
**help** — this message
"""


# ---------------------------------------------------------------------------
# Bot client
# ---------------------------------------------------------------------------

def _create_bot():
    """Create and configure the Discord bot client."""
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info("Discord bot connected as %s (id=%s)", client.user, client.user.id)

    @client.event
    async def on_message(message):
        # Ignore own messages
        if message.author == client.user:
            return

        # Only respond when bot is mentioned or in DMs
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = client.user in message.mentions
        text = message.content.strip()

        # Remove the bot mention from text if present
        if is_mentioned:
            text = re.sub(r"<@!?\d+>", "", text).strip()

        # In servers, only respond when mentioned or in DMs
        if not is_dm and not is_mentioned:
            return

        if not text:
            await message.channel.send(
                "Hey! Just tell me what kind of jobs you're looking for.\n"
                "e.g. \"remote PM jobs in Bangalore above 20 lakhs\""
            )
            return

        # --- Only 3 special intents: help, stats, scrape ---

        if _match_any(text, _HELP_PATTERNS):
            await message.channel.send(HELP_TEXT)
            return

        if _match_any(text, _SCRAPE_PATTERNS):
            await message.channel.send("On it — starting a scraper run...")
            ok, msg = _trigger_scrape()
            await message.channel.send(msg if ok else f"Hmm, something went wrong: {msg}")
            return

        if _match_any(text, _STATS_PATTERNS):
            embed = _stats_embed()
            await message.channel.send(embed=embed)
            return

        # --- Everything else → NLP search ---
        # No pattern matching, no rigid phrases. Whatever the user types
        # gets sent to the NLP parser which extracts filters from it.

        query = re.sub(r"[?.!]+$", "", text).strip()
        if not query:
            await message.channel.send(
                "Just tell me what you're looking for! "
                "e.g. \"remote PM jobs in Bangalore above 20 lakhs\""
            )
            return

        async with message.channel.typing():
            filters, filter_labels, jobs, total = _nlp_search(query)

        # API failure (not just 0 results)
        if filters is None:
            await message.channel.send(
                "Couldn't reach the search service right now. "
                "Make sure the server is running and try again."
            )
            return

        embed = _nlp_jobs_embed(query, filter_labels, jobs, total)
        await message.channel.send(embed=embed)

    return client


# ---------------------------------------------------------------------------
# Entry point (called from app.py)
# ---------------------------------------------------------------------------

def start_discord_bot(token):
    """
    Start the Discord bot in a background daemon thread.
    Safe to call from the Flask startup path.
    """
    if not token:
        logger.info("No Discord bot token provided — bot disabled")
        return

    def _run():
        attempt = 0
        while True:
            attempt += 1
            client = _create_bot()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(client.start(token))
                break  # Clean shutdown
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    wait = min(60 * attempt, 600)  # 60s, 120s, ... up to 10min
                    logger.warning("Discord rate limited (attempt %d), retrying in %ds", attempt, wait)
                    loop.close()
                    time.sleep(wait)
                    continue
                logger.exception("Discord bot crashed with HTTP error")
                break
            except Exception:
                logger.exception("Discord bot crashed, restarting in 30s")
                loop.close()
                time.sleep(30)
                continue
            finally:
                if not loop.is_closed():
                    loop.close()

    t = threading.Thread(target=_run, daemon=True, name="discord-bot")
    t.start()
    logger.info("Discord bot thread started")

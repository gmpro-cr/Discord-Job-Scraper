"""
discord_bot.py - Interactive Discord bot for the Job Search Agent.
Lets users query jobs, search, view stats, and trigger scrapes from Discord
using natural language (no command prefixes needed).

Runs in a background thread alongside the Flask app.
"""

import asyncio
import logging
import re
import threading

import discord
import requests as http_requests

from database import get_connection, get_comprehensive_stats
from discord_notifier import _score_color

logger = logging.getLogger(__name__)

# The port the Flask app listens on (used to trigger scraper via API)
_FLASK_PORT = int(__import__("os").environ.get("PORT", 5001))


# ---------------------------------------------------------------------------
# Intent patterns (keyword matching for natural language)
# ---------------------------------------------------------------------------

_JOBS_PATTERNS = [
    r"\bshow\b.*\bjobs?\b",
    r"\blatest\b.*\bjobs?\b",
    r"\btop\b.*\bjobs?\b",
    r"\bbest\b.*\bjobs?\b",
    r"\brecent\b.*\bjobs?\b",
    r"\bnew\b.*\bjobs?\b",
    r"\bget\b.*\bjobs?\b",
    r"\blist\b.*\bjobs?\b",
    r"\bjobs?\b.*\bplease\b",
    r"\bfetch\b.*\bjobs?\b",
    r"\bwhat\b.*\bjobs?\b",
    r"\bjobs?\s*$",
]

_SEARCH_PATTERNS = [
    r"\bsearch\b\s+(?:for\s+)?(.+)",
    r"\bfind\b\s+(?:me\s+)?(.+?)(?:\s+jobs?)?\s*$",
    r"\blook\s*(?:ing)?\s+for\b\s+(.+?)(?:\s+jobs?)?\s*$",
    r"\bany\b\s+(.+?)\s+(?:jobs?|roles?|openings?|positions?)",
    r"\b(?:jobs?|roles?|openings?|positions?)\s+(?:for|in|at|related\s+to)\s+(.+)",
]

_STATS_PATTERNS = [
    r"\bstats?\b",
    r"\bstatistics\b",
    r"\bsummary\b",
    r"\bhow\s+many\b.*\bjobs?\b",
    r"\boverview\b",
    r"\bnumbers?\b",
    r"\bdashboard\b",
]

_SCRAPE_PATTERNS = [
    r"\bscrape\b",
    r"\bstart\b.*\bscraper\b",
    r"\brun\b.*\bscraper\b",
    r"\bfetch\b.*\bnew\b",
    r"\bscan\b.*\bjobs?\b",
    r"\bscraping\b",
    r"\btrigger\b",
    r"\bgo\b.*\bscrape\b",
    r"\bfind\s+new\s+jobs?\b",
]

_HELP_PATTERNS = [
    r"\bhelp\b",
    r"\bwhat\s+can\s+you\s+do\b",
    r"\bcommands?\b",
    r"\bhow\s+(?:do\s+(?:i|you)|to)\s+use\b",
    r"\bwhat\s+do\s+you\s+do\b",
]

_GREETING_PATTERNS = [
    r"^(?:hi|hello|hey|yo|sup|hola|namaste|good\s+(?:morning|afternoon|evening))\b",
]


def _match_any(text, patterns):
    """Return the first regex match against any pattern, or None."""
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m
    return None


def _extract_number(text):
    """Try to extract a number from text like 'show me 7 jobs'."""
    m = re.search(r"\b(\d{1,2})\b", text)
    if m:
        return max(1, min(int(m.group(1)), 10))
    return 5


def _looks_like_nlp_query(text):
    """Check if text contains NLP filter hints beyond just 'show jobs'."""
    nlp_hints = [
        r"\b(remote|hybrid|wfh|on[\s-]?site)\b",
        r"\b(bangalore|bengaluru|mumbai|delhi|hyderabad|chennai|pune|kolkata|noida|gurgaon|gurugram)\b",
        r"\b\d+\s*(?:lakhs?|lpa|l)\b",
        r"\b\d+\s*[-to]+\s*\d+\s*(?:years?|yrs?)\b",
        r"\b(startup|corporate|mnc)\b",
        r"\b(senior|junior|entry|lead|staff)\b",
        r"\b(product\s*manager|pm|sde|engineer|developer|designer|analyst)\b",
        r"\b(above|below|under|more\s*than|less\s*than)\b.*\d",
    ]
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in nlp_hints)


# ---------------------------------------------------------------------------
# Data handlers
# ---------------------------------------------------------------------------

def _fetch_latest_jobs(limit=5):
    """Fetch the top N jobs by score from the database."""
    limit = max(1, min(limit, 10))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT role, company, location, relevance_score, portal, apply_url, remote_status "
        "FROM job_listings ORDER BY relevance_score DESC LIMIT ?",
        (limit,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _search_jobs(query, limit=5):
    """Search jobs by keyword in role or company."""
    conn = get_connection()
    cur = conn.cursor()
    like = f"%{query}%"
    cur.execute(
        "SELECT role, company, location, relevance_score, portal, apply_url, remote_status "
        "FROM job_listings "
        "WHERE role LIKE ? OR company LIKE ? "
        "ORDER BY relevance_score DESC LIMIT ?",
        (like, like, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


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
        return {}, [], [], 0


def _nlp_jobs_embed(query, filter_labels, jobs, total):
    """Build a Discord embed for NLP search results."""
    embed = discord.Embed(color=0x3498DB)

    # Show parsed understanding
    if filter_labels:
        embed.title = "I understood:"
        embed.description = " | ".join(f"**{label}**" for label in filter_labels)
    else:
        embed.title = f"Search: {query}"

    if not jobs:
        embed.add_field(
            name="No results",
            value="No jobs found matching your query. Try broadening your search.",
            inline=False,
        )
        embed.color = 0x95A5A6
        return embed

    # Show up to 7 jobs in embed (Discord has field limits)
    for j in jobs[:7]:
        score = j.get("relevance_score", 0)
        loc = j.get("location") or "N/A"
        remote = j.get("remote_status", "")
        if remote and remote != "on-site":
            loc = f"{loc} ({remote.title()})"
        portal = (j.get("portal") or "").title()

        # Salary info
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


def _jobs_embed(jobs, title):
    """Build a Discord embed from a list of job rows."""
    if not jobs:
        embed = discord.Embed(
            title=title,
            description="No jobs found.",
            color=0x95A5A6,
        )
        return embed

    embed = discord.Embed(title=title, color=0x3498DB)
    for j in jobs:
        score = j.get("relevance_score", 0)
        loc = j.get("location") or "N/A"
        remote = j.get("remote_status", "")
        if remote and remote != "on-site":
            loc = f"{loc} ({remote})"
        portal = (j.get("portal") or "").title()

        name = f"{j['role']} @ {j['company']}"
        value = f"Score: **{score}**/100 | {loc} | {portal}"
        url = j.get("apply_url")
        if url:
            value += f"\n[Apply]({url})"

        embed.add_field(name=name, value=value, inline=False)

    embed.set_footer(text="Job Search Agent")
    return embed


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


HELP_TEXT = """**Hey! I'm your Job Search Agent bot.** Just talk to me naturally:

**Smart Search** — Ask in plain English and I'll parse your filters:
  "remote PM jobs in Bangalore above 20 lakhs"
  "startup roles in Mumbai for 3-7 years experience"
  "senior positions in Delhi under 30 lpa"
  "hybrid software engineer jobs in Pune"

**See jobs** — "show me latest jobs", "top 5 jobs", "best jobs"
**Stats** — "show stats", "how many jobs", "give me a summary"
**Scrape** — "start scraping", "find new jobs", "run the scraper"
**Help** — "help", "what can you do"
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
        # Also respond if the message starts with common greetings/keywords
        # in channels where the bot can see messages
        text = message.content.strip()

        # Remove the bot mention from text if present
        if is_mentioned:
            text = re.sub(r"<@!?\d+>", "", text).strip()

        # In servers, only respond when mentioned or in DMs
        if not is_dm and not is_mentioned:
            return

        if not text:
            await message.channel.send("Hey! Ask me anything — try \"show me latest jobs\" or \"help\"")
            return

        # --- Match intent ---

        # Help
        if _match_any(text, _HELP_PATTERNS):
            await message.channel.send(HELP_TEXT)
            return

        # Greeting
        if _match_any(text, _GREETING_PATTERNS):
            await message.channel.send(
                "Hey! I'm your Job Search Agent. Ask me things like "
                "\"show latest jobs\", \"search for PM roles\", or \"stats\"."
            )
            return

        # Scrape (check before search to avoid "find new jobs" matching search)
        if _match_any(text, _SCRAPE_PATTERNS):
            await message.channel.send("On it — starting a scraper run...")
            ok, msg = _trigger_scrape()
            await message.channel.send(msg if ok else f"Hmm, something went wrong: {msg}")
            return

        # Stats (check before NLP search to avoid it swallowing "stats")
        if _match_any(text, _STATS_PATTERNS):
            embed = _stats_embed()
            await message.channel.send(embed=embed)
            return

        # Jobs listing (simple "show jobs" without filters)
        if _match_any(text, _JOBS_PATTERNS) and not _looks_like_nlp_query(text):
            limit = _extract_number(text)
            jobs = _fetch_latest_jobs(limit)
            embed = _jobs_embed(jobs, f"Top {limit} Jobs")
            await message.channel.send(embed=embed)
            return

        # NLP Search — handles everything else: natural language queries,
        # "search for X", "find me Y", and any freeform job queries
        await message.channel.send("Searching...")
        query = text
        # If it matched a search pattern, extract the query part
        search_match = _match_any(text, _SEARCH_PATTERNS)
        if search_match and search_match.lastindex:
            query = search_match.group(1).strip()
        query = re.sub(r"[?.!]+$", "", query).strip()

        if not query:
            await message.channel.send("What should I search for? Try something like \"remote PM jobs in Bangalore above 20 lakhs\"")
            return

        filters, filter_labels, jobs, total = _nlp_search(query)
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
        client = _create_bot()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(client.start(token))
        except Exception:
            logger.exception("Discord bot crashed")
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True, name="discord-bot")
    t.start()
    logger.info("Discord bot thread started")

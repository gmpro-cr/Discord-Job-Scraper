# Job Search Agent

Automated multi-portal job scraper that aggregates listings from LinkedIn, Indeed, Naukri, HiringCafe, Wellfound (AngelList), and IIMJobs. Scores jobs using AI (Ollama/Mistral) or keyword matching, and generates beautiful HTML/TXT daily digests.

## Quick Start

```bash
cd job-search-agent

# Install dependencies
pip install -r requirements.txt

# First run - interactive setup
python main.py

# Subsequent runs
python main.py                    # Run once, generate digest
python main.py --schedule         # Run daily at scheduled time
python main.py --edit-preferences # Change search preferences
python main.py --view-stats       # View statistics
python main.py --portal-stats     # Compare portal quality
python main.py --last-digest      # Open most recent digest
```

## Setup

### 1. Python Dependencies

```bash
pip install -r requirements.txt
```

### 2. Selenium / ChromeDriver (for LinkedIn & Wellfound)

The agent uses Selenium with headless Chrome for JavaScript-heavy portals. Install Chrome/Chromium:

- **macOS**: `brew install --cask google-chrome`
- **Ubuntu**: `sudo apt install chromium-browser`
- **Windows**: Download from google.com/chrome

ChromeDriver is managed automatically via `webdriver-manager`.

### 3. Ollama (Optional - for AI-powered scoring)

For intelligent job scoring using the Mistral model:

```bash
# Install Ollama: https://ollama.ai
curl -fsSL https://ollama.ai/install.sh | sh

# Pull the mistral model
ollama pull mistral

# Start Ollama server (runs in background)
ollama serve
```

If Ollama isn't available, the agent falls back to keyword-based scoring automatically.

## Configuration

### User Preferences (`user_preferences.json`)

Created on first run via interactive setup. Edit anytime:

```bash
python main.py --edit-preferences
```

Fields:
- `job_titles` - List of target roles (e.g., "Product Manager", "PM")
- `locations` - Preferred cities or "Remote"
- `industries` - Target industries for filtering
- `top_jobs_per_digest` - Number of jobs in each digest (3-10)
- `digest_time` - When to send daily digest (e.g., "6:00 AM")
- `email` - Email for digests (optional)

### Agent Config (`config.json`)

Controls scraping behavior, portal settings, and scoring:

- **Enable/disable portals**: Set `"enabled": false` for any portal
- **Thread count**: Parallel scraping threads (default: 4)
- **Request delays**: Anti-blocking delays between requests (2-5s)
- **Selenium**: Toggle per-portal for JS-heavy sites
- **Scoring thresholds**: Minimum relevance score (default: 65)

## How Scoring Works

Each job is scored 0-100 based on:

| Factor | Points | Description |
|--------|--------|-------------|
| Title match | 0-25 | How closely the role matches your target titles |
| Location match | 0-15 | Whether the location matches your preferences |
| Remote flexibility | 0-15 | Remote/hybrid/on-site detection |
| Domain relevance | 0-15 | Banking/fintech background advantage |
| PM keywords | 0-20 | Product management terminology |
| Growth potential | 0-10 | Career growth indicators |

Jobs scoring below 65 are excluded from the digest (configurable).

## File Structure

```
job-search-agent/
├── main.py              # CLI entry point and pipeline orchestrator
├── scrapers.py          # Portal-specific scrapers with threading
├── analyzer.py          # Ollama/keyword scoring and analysis
├── database.py          # SQLite operations and statistics
├── digest_generator.py  # HTML and TXT digest generation
├── scheduler.py         # APScheduler daily scheduling
├── config.json          # Agent configuration
├── requirements.txt     # Python dependencies
├── README.md            # This file
├── user_preferences.json # Created on first run
├── jobs.db              # SQLite database (auto-created)
├── job_agent.log        # Log file (daily rotation)
├── digests/             # Generated digest files
│   ├── digest_2025-01-15_06-00.html
│   └── digest_2025-01-15_06-00.txt
└── .cache/              # HTML cache for scraped pages
```

## Portal Notes

| Portal | Method | Notes |
|--------|--------|-------|
| LinkedIn | Selenium | Public job search (no login needed). Heavy anti-scraping. |
| Indeed | Requests | Standard HTTP scraping. May require Selenium fallback. |
| Naukri | Requests + Session | Uses session cookies. India's largest job site. |
| HiringCafe | Requests | Startup-focused hiring platform. |
| Wellfound | Selenium | Formerly AngelList. JS-heavy, needs Selenium. |
| IIMJobs | Requests | MBA/experienced professional jobs. |

## Troubleshooting

**No jobs found**: Portals may block scraping. Check `job_agent.log` for details. Try:
- Increasing delays in `config.json` (`request_delay_min`/`max`)
- Disabling problematic portals
- Running at different times of day

**Selenium errors**: Ensure Chrome is installed and up to date. The agent auto-downloads ChromeDriver.

**Ollama not connecting**: Start with `ollama serve`. The agent falls back to keyword scoring automatically.

**Rate limiting**: The agent uses random delays, rotating user agents, and retries. If blocked, increase delays or reduce `max_pages`.

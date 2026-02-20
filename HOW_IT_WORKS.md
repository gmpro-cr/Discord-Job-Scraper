# How This Job Search Agent Works — Plain English Guide

Think of this tool as your **personal job hunting assistant** that runs on your laptop. You tell it what kind of jobs you're looking for, and it goes out every day, searches multiple job websites, filters out the noise, and brings back only the best matches for you.

---

## The Big Picture

```
Your Preferences  →  Scraper  →  Scorer  →  Database  →  Web Dashboard + Email/Telegram
(what you want)      (fetches)   (ranks)    (stores)      (shows you results)
```

Every day at a time you set (currently 11:00 AM), the system automatically does all of this on its own.

---

## The Files and What They Do

### `app.py` — The Web Interface
This is the main file that runs the website you open in your browser at `http://localhost:5001`.

It powers every page you see:
- **Dashboard** — overview of your job search progress
- **Jobs** — the list of all jobs found, with filters and scores
- **My CV** — upload your resume so jobs can be matched against it
- **Digests** — past daily summaries
- **Run Scraper** — trigger a manual search right now
- **Settings** — change your job titles, locations, email, etc.

Think of it like the reception desk — it takes your clicks and requests, fetches data from the database, and shows it back to you as a webpage.

---

### `scrapers.py` — The Job Hunter
This is the file that actually goes out and **visits job websites** on your behalf.

It currently searches these 6 portals:
| Portal | What it does |
|--------|-------------|
| **LinkedIn** | Searches public job listings (past 3 days only) |
| **Indeed** | Searches India jobs (past 3 days only) |
| **Naukri** | India's largest job board (past 3 days only) |
| **HiringCafe** | API-based search, returns up to 50 results |
| **Wellfound** | Startup jobs (formerly AngelList) |
| **IIMJobs** | Senior/MBA-level jobs |

For each portal, it:
1. Searches using your job titles × your locations (e.g. "Product Manager" + "Bangalore")
2. Fetches the webpage or API response
3. Extracts the job title, company, location, salary, and apply link
4. Saves the fetched page in a local cache folder (`.cache/`) for 12 hours so it doesn't hammer the same site twice

**Anti-duplicate trick:** It saves the result of each website visit in a temporary cache for 12 hours. So if you run the scraper twice in a row, the second time it just reads from memory instead of going back to the website.

---

### `analyzer.py` — The Scorer
After collecting raw jobs, every job gets a **relevance score from 0 to 100**.

It does this in two ways:

1. **AI scoring (preferred):** Uses a locally-running AI model called Ollama (Mistral) to read the job description and give it a score based on how well it matches your profile.

2. **Keyword scoring (fallback):** If the AI isn't running, it uses a simpler point system:
   - Matching your job title keywords → up to +20 points
   - Fintech/SaaS/Banking industry → up to +15 points
   - Remote/Hybrid work → up to +10 points
   - Your transferable skills mentioned → bonus points
   - Negative keywords (like "fresher only", "5+ years coding") → minus points

Only jobs scoring **65 or above** make it into your daily digest. Jobs below that are still saved to the database but marked as lower priority.

---

### `database.py` — The Filing Cabinet
All job data is stored in a single file: **`jobs.db`** (currently 3.1 MB with 7,413 jobs).

This is a SQLite database — think of it like a spreadsheet saved as a file on your computer. No internet connection needed, no server, no monthly fees.

Each job record stores:
- Basic info: title, company, location, salary, portal, apply link
- Dates: when it was found, when it was posted
- Score: the relevance score (0–100)
- Status: New / Saved / Applied / Phone Screen / Interview / Offer / Rejected
- Your notes on the job
- CV match % (if you've uploaded your resume)
- Contact info (if Apollo.io enrichment is configured)

**No duplicate jobs are saved.** Every job gets a unique ID based on a fingerprint of `portal + company + role + location`. Before saving, it checks if that ID already exists. If it does, the job is silently skipped.

---

### `scheduler.py` — The Alarm Clock
This runs quietly in the background and triggers the full pipeline every day at the time you set in Settings.

When `app.py` starts, it automatically starts this scheduler. So as long as your laptop is on and `app.py` is running, the scraper will fire automatically every day.

---

### `digest_generator.py` — The Daily Summary
After each scraping run, this generates a **daily digest** — a nicely formatted HTML file (and plain text version) showing your top jobs.

This digest is:
- Saved as an HTML file in the `digests/` folder
- Viewable from the **Digests** page on the website
- Optionally emailed to you (if you've set up Gmail SMTP in Settings)
- Optionally sent to Telegram (if you've set up a Telegram bot)

---

### `email_notifier.py` & `telegram_notifier.py` — The Messengers
These handle sending the daily digest out to you.

- **Email:** Uses Gmail's SMTP (you need to generate an "App Password" from your Google account — not your regular password)
- **Telegram:** Sends a formatted message with the top jobs to your Telegram chat

Both are optional. The tool works fine without them — you can just open the website to check your jobs.

---

### `user_preferences.json` — Your Settings
This file stores everything you've configured in the Settings page:
- Job titles to search for
- Locations
- Industries
- Digest time
- Email address
- Number of jobs per digest

Sensitive credentials (Gmail password, Telegram token) are stored separately in a `.env` file so they don't accidentally get shared.

---

### `config.json` — Technical Settings
Controls the scraper's behaviour — things like:
- Which portals are enabled/disabled
- How many pages to scrape per portal
- Request delays between calls (to avoid getting blocked)
- Cache expiry time

You generally don't need to touch this file.

---

## The Daily Routine (What Happens at 11 AM)

```
1. SCRAPE    → Visit LinkedIn, Indeed, Naukri, HiringCafe, Wellfound, IIMJobs
               Search for your job titles in your locations
               ~200–500 raw job listings collected

2. SCORE     → Each job gets a 0–100 relevance score
               Jobs below 65 are stored but excluded from digest

3. STORE     → New jobs saved to jobs.db
               Already-seen jobs silently skipped

4. DIGEST    → Top N jobs packaged into a digest
               Saved as HTML file
               Sent via Email / Telegram (if configured)
```

Total time: typically **3–8 minutes** depending on how responsive the job sites are.

---

## Where Data Lives on Your Computer

```
job-search-agent/
├── jobs.db                  ← All 7,413 jobs (the database)
├── user_preferences.json    ← Your search preferences
├── config.json              ← Scraper technical settings
├── .env                     ← Passwords/tokens (never shared)
├── .cache/                  ← Cached webpage HTML (auto-cleaned after 12 hours)
├── digests/                 ← Daily digest HTML files
└── job_agent.log            ← Log of everything the scraper did
```

Everything stays **100% on your laptop**. No data is uploaded anywhere unless you configure email or Telegram.

---

## CV Matching

If you upload your CV on the **My CV** page:
1. The system extracts all skills from your resume
2. For each job, it compares the job description against your skills
3. A **CV Match %** is shown on each job card
4. You can click "Re-score All Jobs Against CV" to update the match % for all existing jobs

This is separate from the relevance score — the relevance score is about the job itself, the CV match is about how well *your* background fits that specific job.

---

## In Short

| What you see | What's actually happening |
|---|---|
| Jobs page loads | `app.py` queries `jobs.db` and formats results |
| You run the scraper | `scrapers.py` visits 6 job sites, `analyzer.py` scores results, `database.py` saves new ones |
| Daily digest arrives | Scheduler triggers the above at 11 AM, `digest_generator.py` creates the HTML |
| CV match % appears | Your uploaded CV's skills are compared against each job description |
| You mark a job Applied | `database.py` updates that row's status in `jobs.db` |

# Job Search Agent — Major Upgrade Design
**Date:** 2026-02-18
**Status:** Approved

## Problem Statement

The current portal has five issues:
1. Generic indigo UI — not distinctive, hard to scan
2. No CV upload — scoring is hardcoded for a "banking → PM" profile regardless of actual CV
3. Score is just a number — no explanation of what matched or what's missing
4. Contact enrichment (Apollo) is broken — uses a deprecated API endpoint
5. Job status exists but is visually buried — hard to see at a glance what you've acted on

## Design Decisions

### 1. UI Redesign — Clean Light (Notion/Linear-style)

**Direction:** White/light-gray background, slate color palette. Left sidebar nav on desktop, bottom tab bar on mobile.

**Job card changes:**
- Left colored border = status (gray=new, blue=saved, green=applied, orange=interview, emerald=offer, red=rejected)
- Score circle → horizontal CV match % progress bar
- Status pill moved to top-right of card (prominent)
- Clean info row: salary · experience · remote type
- Action row: `[Apply →]` `[Save]` `[Status ▾]` `[Gap Analysis ▾]`

**Dashboard:** Cleaner stat grid, pipeline as column counts.

### 2. CV Upload & CV-Based Scoring

**Upload page** (`/cv`):
- Drag-and-drop accepting `.pdf`, `.txt`, `.docx`
- Shows parsed CV summary: detected skills, experience, keywords
- "Rescore all jobs" button

**Technical approach (no LLM required):**
- Parse CV using `pdfplumber` (PDF), `python-docx` (DOCX), or plain text
- Store parsed result in `cv_data.json` (project root)
- New `cv_score(job, cv_data)` in `analyzer.py`:
  - Extract skills from job JD
  - `cv_match = len(matched_skills) / len(jd_skills) * 100`
  - Also scores title match + experience overlap + industry keywords
- New `cv_score` column added to `job_listings` table
- Original `relevance_score` (keyword/Ollama) is preserved — both scores shown

### 3. Gap Analysis Panel

Expandable panel per job card triggered by `[View Gap Analysis ▾]`:

```
CV Match: 73%  ████████░░░
✓ Matched (5): SQL, Agile, Roadmap, Stakeholder Mgmt, Data Analysis
✗ Missing (3): Python, Figma, Kafka
→ Action Steps:
  • Python: Complete a pandas course (2 weeks, free on Kaggle)
  • Figma: Add "basic Figma" — 1 day to learn basics
  • Kafka: Mention messaging systems from banking context
```

**Implementation:**
- API endpoint: `GET /api/jobs/<id>/gap-analysis`
- Returns: `{cv_score, matched_skills, missing_skills, action_steps}`
- Action steps from a curated `SKILL_TIPS` dict in `analyzer.py`
- Requires CV to be uploaded first; shows a prompt if not

### 4. Contact Enrichment — Google + Website Scraper

Replaces broken Apollo API with `contact_scraper.py`:

**Strategy (tried in order):**
1. **JD text scan** — regex for email addresses and LinkedIn URLs in the job description (~20% hit rate)
2. **Company website** — fetch careers/about/team page and scan for recruiter emails (~20%)
3. **Google search** — query `"{company}" recruiter OR "talent acquisition" email` and parse results (~30%)

Uses `requests` + `BeautifulSoup`. 2-second delay between requests. Per-company cache to avoid re-fetching.

Preferences page gets a "Test Contact Enrichment" button.

### 5. Job Status Visibility

No schema changes needed — `applied_status` column already exists. UI changes only:
- Left border color on card reflects status
- Status pill moved from bottom to top-right (always visible)
- Status dropdown styled as buttons in the action row

## Architecture Changes

| File | Type | Change |
|---|---|---|
| `templates/base.html` | Modified | New sidebar nav, global styles |
| `templates/dashboard.html` | Modified | Cleaner stat grid |
| `templates/jobs.html` | Modified | New cards, gap panel, CV match bar |
| `templates/cv.html` | New | CV upload page |
| `app.py` | Modified | Routes: `/cv`, `/api/cv/upload`, `/api/cv/rescore`, `/api/jobs/<id>/gap-analysis` |
| `analyzer.py` | Modified | Add: `parse_cv_text()`, `cv_score()`, `compute_gap_analysis()` |
| `database.py` | Modified | Add `cv_score` column migration |
| `contact_scraper.py` | New | Replaces `apollo_enricher.py` |
| `requirements.txt` | Modified | Add `pdfplumber`, `python-docx` |

## Out of Scope

- Duplicate merging (user wants duplicates kept as-is)
- LLM-powered gap analysis (regex + curated tips is sufficient)
- Apollo.io login-based scraping (Google + website scraper chosen instead)

# Job Search Agent — Major Upgrade Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade the job search portal with a clean Notion-style UI, CV upload + CV-based job scoring, per-job gap analysis panels, and a working contact enrichment scraper (replaces broken Apollo API).

**Architecture:** CV text is stored as `cv_data.json` in the project root; Python functions in `analyzer.py` score jobs against it and compute gap analysis. A new `contact_scraper.py` replaces `apollo_enricher.py` using JD text scanning, company website scraping, and Google search. All UI is Jinja2 templates with Tailwind CDN — no build step.

**Tech Stack:** Flask, SQLite, Tailwind CSS (CDN), pdfplumber (PDF parsing), python-docx (DOCX parsing), requests + BeautifulSoup (already in requirements), pytest

---

## Task 1: Add test infrastructure + update requirements.txt

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/test_analyzer.py`

**Step 1: Add new dependencies to requirements.txt**

```
pdfplumber>=0.10.0
python-docx>=1.1.0
pytest>=8.0.0
```

Final `requirements.txt`:
```
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
selenium>=4.15.0
webdriver-manager>=4.0.0
ollama>=0.1.0
APScheduler>=3.10.0
schedule>=1.2.0
flask>=3.0.0
gunicorn>=21.0.0
python-telegram-bot>=21.0
python-dotenv>=1.0.0
openai>=1.0.0
pdfplumber>=0.10.0
python-docx>=1.1.0
pytest>=8.0.0
```

**Step 2: Install new dependencies**

```bash
cd /Users/gaurav/job-search-agent
pip install pdfplumber python-docx pytest
```

Expected: packages install without errors.

**Step 3: Create tests directory**

```bash
mkdir -p /Users/gaurav/job-search-agent/tests
touch /Users/gaurav/job-search-agent/tests/__init__.py
```

**Step 4: Commit**

```bash
git add requirements.txt tests/__init__.py
git commit -m "chore: add pdfplumber, python-docx, pytest to requirements"
```

---

## Task 2: Database — add cv_score column

**Files:**
- Modify: `database.py` (lines 81-103, after the Phase 3a block)

**Step 1: Write the failing test**

Create `tests/test_database.py`:

```python
"""Tests for database.py migrations and schema."""
import os
import sqlite3
import tempfile
import pytest

# Point to a temp DB so tests don't pollute jobs.db
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from database import init_db, get_connection


def test_cv_score_column_exists():
    """cv_score column must exist after init_db()."""
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(job_listings)")
    cols = [row["name"] for row in cursor.fetchall()]
    conn.close()
    assert "cv_score" in cols, f"cv_score column missing; found: {cols}"
```

**Step 2: Run the test to verify it fails**

```bash
cd /Users/gaurav/job-search-agent
python -m pytest tests/test_database.py::test_cv_score_column_exists -v
```

Expected output: `FAILED — AssertionError: cv_score column missing`

**Step 3: Add cv_score migration in database.py**

Find the Phase 3a block (around line 91-103) and add after it:

```python
    # CV scoring column
    for col in ["cv_score INTEGER DEFAULT 0"]:
        try:
            cursor.execute(f"ALTER TABLE job_listings ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()
```

Also find `insert_jobs_bulk` and `insert_job` functions — add `cv_score` to the INSERT statement. In `insert_job` (around line 138), add `cv_score` to the column list and bind `job.get("cv_score", 0)`.

**Step 4: Run the test to verify it passes**

```bash
python -m pytest tests/test_database.py::test_cv_score_column_exists -v
```

Expected: `PASSED`

**Step 5: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: add cv_score column to job_listings table"
```

---

## Task 3: Analyzer — CV parsing functions

**Files:**
- Modify: `analyzer.py` (append to end of file)
- Modify: `tests/test_analyzer.py` (create)

### 3a: parse_cv_text()

**Step 1: Write failing tests**

Create `tests/test_analyzer.py`:

```python
"""Tests for CV parsing and scoring functions in analyzer.py."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from analyzer import parse_cv_text, cv_score, compute_gap_analysis


SAMPLE_CV = """
John Doe | Product Manager
Skills: SQL, Python, Agile, Stakeholder Management, Data Analysis, Roadmap Planning
Experience: 8 years in banking and financial services
Led product teams at HDFC Bank, worked with Jira, Tableau
"""

SAMPLE_JD_JOB = {
    "role": "Senior Product Manager",
    "job_description": "We need Python, SQL, Agile, Figma, Kafka experience. "
                       "Roadmap planning and stakeholder management required.",
    "location": "Bangalore",
}


def test_parse_cv_text_extracts_skills():
    result = parse_cv_text(SAMPLE_CV)
    assert "skills" in result
    assert len(result["skills"]) > 0
    # SQL and Python should be detected
    skill_names_lower = [s.lower() for s in result["skills"]]
    assert "sql" in skill_names_lower
    assert "python" in skill_names_lower


def test_parse_cv_text_returns_raw_text():
    result = parse_cv_text(SAMPLE_CV)
    assert result["raw_text"] == SAMPLE_CV


def test_parse_cv_text_empty_string():
    result = parse_cv_text("")
    assert result["skills"] == []
```

**Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_analyzer.py::test_parse_cv_text_extracts_skills -v
```

Expected: `FAILED — ImportError: cannot import name 'parse_cv_text'`

**Step 3: Implement parse_cv_text() in analyzer.py**

Append to the end of `analyzer.py`:

```python
# =============================================================================
# CV Upload and Matching
# =============================================================================

import json as _json
from datetime import datetime as _datetime

CV_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cv_data.json")

# Extended skill list specifically for CV scanning (broader than JD extraction)
_CV_SKILL_PATTERNS = [
    r"SQL", r"Python", r"Excel", r"Tableau", r"Power BI", r"Jira", r"Confluence",
    r"Figma", r"Analytics", r"A/B [Tt]esting", r"Data [Aa]nalysis", r"Data Science",
    r"Product [Ss]trategy", r"Roadmap", r"Agile", r"Scrum", r"Kanban",
    r"Stakeholder [Mm]anagement", r"User [Rr]esearch", r"UX", r"UI",
    r"API", r"REST", r"Microservices", r"AWS", r"GCP", r"Azure", r"Cloud",
    r"Machine Learning", r"AI", r"NLP", r"Deep Learning",
    r"React", r"JavaScript", r"TypeScript", r"Node\.?[Jj][Ss]", r"Java",
    r"Go", r"Kubernetes", r"Docker", r"CI/CD", r"Git", r"GitHub",
    r"MongoDB", r"PostgreSQL", r"Redis", r"Kafka", r"Spark", r"Hadoop",
    r"Fintech", r"Payments", r"UPI", r"Lending", r"Credit", r"Banking",
    r"Risk [Mm]anagement", r"Compliance", r"P&L", r"Revenue",
    r"Cross[\s-]functional", r"Leadership", r"Mentoring", r"Strategy",
    r"OKR", r"KPI", r"Metrics", r"Growth", r"Retention", r"Conversion",
    r"B2B", r"B2C", r"SaaS", r"Mobile", r"iOS", r"Android",
]


def parse_cv_text(text):
    """
    Parse raw CV text and extract structured data.

    Args:
        text: Raw text content of the CV

    Returns:
        dict with keys: skills (list), raw_text (str), uploaded_at (str)
    """
    if not text or not text.strip():
        return {"skills": [], "raw_text": text or "", "uploaded_at": _datetime.now().isoformat()}

    found_skills = []
    for pattern in _CV_SKILL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            clean = re.sub(r'[\[\]\\]', '', pattern).replace(r"\.", ".").replace(r"\.?", "").strip()
            # Use a cleaner display name
            display = re.sub(r'\([^)]+\)', '', clean).strip()
            if display and display not in found_skills:
                found_skills.append(display)

    return {
        "skills": found_skills,
        "raw_text": text,
        "uploaded_at": _datetime.now().isoformat(),
    }


def load_cv_data():
    """Load stored CV data from cv_data.json. Returns None if not uploaded yet."""
    if not os.path.exists(CV_DATA_PATH):
        return None
    try:
        with open(CV_DATA_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return None


def save_cv_data(cv_data):
    """Save CV data dict to cv_data.json."""
    with open(CV_DATA_PATH, "w", encoding="utf-8") as f:
        _json.dump(cv_data, f, indent=2)
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_analyzer.py::test_parse_cv_text_extracts_skills tests/test_analyzer.py::test_parse_cv_text_returns_raw_text tests/test_analyzer.py::test_parse_cv_text_empty_string -v
```

Expected: all 3 `PASSED`

---

### 3b: cv_score() function

**Step 1: Write failing tests** (add to `tests/test_analyzer.py`):

```python
def test_cv_score_returns_0_without_cv():
    score = cv_score(SAMPLE_JD_JOB, None)
    assert score == 0


def test_cv_score_returns_0_to_100():
    cv_data = parse_cv_text(SAMPLE_CV)
    score = cv_score(SAMPLE_JD_JOB, cv_data)
    assert 0 <= score <= 100


def test_cv_score_higher_when_more_skills_match():
    # CV with many matching skills
    rich_cv = parse_cv_text("Skills: Python, SQL, Agile, Figma, Kafka, Roadmap, Stakeholder Management")
    poor_cv = parse_cv_text("Skills: Cooking, Gardening, Photography")

    rich_score = cv_score(SAMPLE_JD_JOB, rich_cv)
    poor_score = cv_score(SAMPLE_JD_JOB, poor_cv)
    assert rich_score > poor_score
```

**Step 2: Run to confirm they fail**

```bash
python -m pytest tests/test_analyzer.py::test_cv_score_returns_0_without_cv -v
```

Expected: `FAILED — ImportError: cannot import name 'cv_score'`

**Step 3: Implement cv_score() in analyzer.py** (append after `save_cv_data`):

```python
def cv_score(job, cv_data):
    """
    Score a job 0-100 based on how well the applicant's CV matches the JD.

    Args:
        job: dict with role, job_description, location fields
        cv_data: dict from parse_cv_text(), or None if no CV uploaded

    Returns:
        int 0-100
    """
    if not cv_data:
        return 0

    cv_skills_lower = {s.lower() for s in cv_data.get("skills", [])}
    if not cv_skills_lower:
        return 0

    jd_text = " ".join([job.get("role", ""), job.get("job_description", "")])
    jd_skills = extract_skills(jd_text, max_skills=20)

    if not jd_skills:
        # If no specific skills extracted from JD, fall back to keyword overlap
        jd_words = set(re.findall(r'\b\w{4,}\b', jd_text.lower()))
        cv_words = set(re.findall(r'\b\w{4,}\b', cv_data.get("raw_text", "").lower()))
        common = jd_words & cv_words
        if not jd_words:
            return 0
        return min(int(len(common) / len(jd_words) * 100), 100)

    jd_skills_lower = [s.lower() for s in jd_skills]
    matched = [s for s in jd_skills_lower if s in cv_skills_lower]
    score = int(len(matched) / len(jd_skills_lower) * 100)
    return min(score, 100)
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_analyzer.py -k "cv_score" -v
```

Expected: all 3 `PASSED`

---

### 3c: compute_gap_analysis() function

**Step 1: Write failing test** (add to `tests/test_analyzer.py`):

```python
def test_compute_gap_analysis_structure():
    cv_data = parse_cv_text(SAMPLE_CV)
    result = compute_gap_analysis(SAMPLE_JD_JOB, cv_data)

    assert "cv_score" in result
    assert "matched_skills" in result
    assert "missing_skills" in result
    assert "action_steps" in result
    assert isinstance(result["matched_skills"], list)
    assert isinstance(result["missing_skills"], list)
    assert isinstance(result["action_steps"], list)


def test_compute_gap_analysis_no_cv():
    result = compute_gap_analysis(SAMPLE_JD_JOB, None)
    assert result["cv_score"] == 0
    assert "Upload your CV" in result["action_steps"][0]
```

**Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_analyzer.py::test_compute_gap_analysis_structure -v
```

Expected: `FAILED — ImportError`

**Step 3: Implement compute_gap_analysis() in analyzer.py** (append after `cv_score`):

```python
# Curated tips for common missing skills
SKILL_TIPS = {
    "python": "Take a free Python for Data Analysis course on Kaggle (2-3 days). Focus on pandas.",
    "sql": "You likely have SQL from banking work — emphasize this explicitly in your CV.",
    "figma": "Complete Figma basics on YouTube (1 day). Add 'basic Figma' to your skills section.",
    "kafka": "Frame your banking messaging/event systems experience as equivalent. Add a note in your cover letter.",
    "kubernetes": "Note your exposure to cloud infrastructure from banking IT projects.",
    "docker": "Mention any containerization or DevOps exposure. A 2-hour intro tutorial covers basics.",
    "machine learning": "Highlight any analytics or predictive modelling work from banking.",
    "react": "Note your familiarity with web product decisions if you've worked with frontend teams.",
    "javascript": "As a PM, familiarity (not proficiency) is sufficient. Mention product decisions around JS-heavy features.",
    "aws": "Highlight any cloud migration or AWS-based projects from your banking background.",
    "a/b testing": "Emphasize any data-driven experiments or hypothesis testing from your banking role.",
    "user research": "Frame any customer interviews, NPS analysis, or journey mapping work you've done.",
    "agile": "If you have this, make it explicit with specific examples of sprints, stand-ups, retrospectives.",
    "data analysis": "Quantify your analytics work — rows analyzed, reports built, decisions influenced.",
    "tableau": "Free Tableau Public is available. Even basic dashboards count — add to skills.",
    "jira": "Mention any project tracking tools used in banking (Jira, ServiceNow, etc.).",
}


def compute_gap_analysis(job, cv_data):
    """
    Compute the gap between a job's requirements and the applicant's CV.

    Args:
        job: dict with role, job_description fields
        cv_data: dict from parse_cv_text(), or None

    Returns:
        dict: {cv_score, matched_skills, missing_skills, action_steps}
    """
    if not cv_data:
        return {
            "cv_score": 0,
            "matched_skills": [],
            "missing_skills": [],
            "action_steps": ["Upload your CV on the CV page to see personalized gap analysis."],
        }

    cv_skills_lower = {s.lower(): s for s in cv_data.get("skills", [])}
    jd_text = " ".join([job.get("role", ""), job.get("job_description", "")])
    jd_skills = extract_skills(jd_text, max_skills=20)

    if not jd_skills:
        return {
            "cv_score": cv_score(job, cv_data),
            "matched_skills": [],
            "missing_skills": [],
            "action_steps": ["No specific skills detected in job description."],
        }

    matched = []
    missing = []
    for skill in jd_skills:
        if skill.lower() in cv_skills_lower:
            matched.append(skill)
        else:
            missing.append(skill)

    score = int(len(matched) / len(jd_skills) * 100) if jd_skills else 0

    # Generate action steps for top 3 missing skills
    action_steps = []
    for skill in missing[:3]:
        tip = SKILL_TIPS.get(skill.lower())
        if tip:
            action_steps.append(f"**{skill}**: {tip}")
        else:
            action_steps.append(f"**{skill}**: Research this skill and add relevant experience from your background.")

    if not missing:
        action_steps = ["Great match! Highlight your strongest matching skills in the cover letter."]

    return {
        "cv_score": min(score, 100),
        "matched_skills": matched,
        "missing_skills": missing,
        "action_steps": action_steps,
    }
```

**Step 4: Run all analyzer tests**

```bash
python -m pytest tests/test_analyzer.py -v
```

Expected: all tests `PASSED`

**Step 5: Commit**

```bash
git add analyzer.py tests/test_analyzer.py
git commit -m "feat: add CV parsing, cv_score, and gap analysis functions"
```

---

## Task 4: Contact scraper (replaces apollo_enricher.py)

**Files:**
- Create: `contact_scraper.py`
- Create: `tests/test_contact_scraper.py`

**Step 1: Write failing tests**

Create `tests/test_contact_scraper.py`:

```python
"""Tests for contact_scraper.py."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from contact_scraper import extract_contacts_from_text, enrich_jobs_with_contacts


def test_extract_email_from_jd():
    text = "Contact us at hr@razorpay.com or careers@company.io for questions."
    emails, linkedin_urls = extract_contacts_from_text(text)
    assert "hr@razorpay.com" in emails
    assert "careers@company.io" in emails


def test_extract_linkedin_from_jd():
    text = "Connect with our recruiter at linkedin.com/in/jane-recruiter for more info."
    emails, linkedin_urls = extract_contacts_from_text(text)
    assert any("jane-recruiter" in url for url in linkedin_urls)


def test_extract_no_contacts():
    text = "We are hiring a senior product manager. Apply on our website."
    emails, linkedin_urls = extract_contacts_from_text(text)
    assert emails == []
    assert linkedin_urls == []


def test_enrich_returns_dict():
    jobs = [{"job_id": "abc123", "company": "TestCo", "job_description": "Contact hr@testco.com"}]
    result = enrich_jobs_with_contacts(jobs)
    assert isinstance(result, dict)
    assert "abc123" in result
    assert result["abc123"]["poster_email"] == "hr@testco.com"
```

**Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_contact_scraper.py -v
```

Expected: `FAILED — ModuleNotFoundError: No module named 'contact_scraper'`

**Step 3: Create contact_scraper.py**

```python
"""
contact_scraper.py - Contact enrichment via web scraping (no API key required).
Replaces apollo_enricher.py. Uses three strategies in order:
  1. Extract contacts from the job description text itself
  2. Scrape the company's website (careers/about/team page)
  3. Google search for HR contacts at the company
"""
import re
import time
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urljoin

logger = logging.getLogger(__name__)

# Request headers to appear as a browser
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
_LINKEDIN_RE = re.compile(r'linkedin\.com/in/([\w-]+)', re.IGNORECASE)

# Domains to skip when found in JD text (noisy/irrelevant emails)
_BLOCKED_EMAIL_DOMAINS = {"example.com", "test.com", "domain.com", "email.com", "yourcompany.com"}


def extract_contacts_from_text(text):
    """
    Extract email addresses and LinkedIn profile URLs from raw text.

    Returns:
        tuple: (emails: list[str], linkedin_urls: list[str])
    """
    if not text:
        return [], []

    raw_emails = _EMAIL_RE.findall(text)
    emails = [e for e in raw_emails if e.split("@")[-1].lower() not in _BLOCKED_EMAIL_DOMAINS]

    linkedin_matches = _LINKEDIN_RE.findall(text)
    linkedin_urls = [f"https://linkedin.com/in/{m}" for m in linkedin_matches]

    return list(dict.fromkeys(emails)), list(dict.fromkeys(linkedin_urls))  # dedupe preserving order


def _scrape_page_for_contacts(url, timeout=8):
    """Fetch a URL and extract emails/LinkedIn URLs from its text content."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # Remove script/style noise
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
        return extract_contacts_from_text(text)
    except Exception as e:
        logger.debug("Failed to scrape %s: %s", url, e)
        return [], []


def _try_company_website(company_name, apply_url=None):
    """
    Try to scrape the company's careers or about page for contacts.
    Uses the apply_url domain as a starting point if available.
    """
    emails, linkedin_urls = [], []

    # Derive company domain from apply URL if possible
    domain = None
    if apply_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(apply_url)
            host = parsed.netloc.lstrip("www.")
            # Skip known job boards
            known_boards = {"linkedin.com", "naukri.com", "indeed.com", "wellfound.com",
                            "hiringcafe.com", "iimjobs.com", "instahyre.com", "angel.co"}
            if host and not any(board in host for board in known_boards):
                domain = host
        except Exception:
            pass

    if not domain:
        return emails, linkedin_urls

    # Try company contact/about/team pages
    candidate_paths = ["/about", "/team", "/contact", "/careers/contact", "/about-us"]
    for path in candidate_paths:
        url = f"https://{domain}{path}"
        e, l = _scrape_page_for_contacts(url)
        emails.extend(e)
        linkedin_urls.extend(l)
        if emails or linkedin_urls:
            break
        time.sleep(0.5)

    return list(dict.fromkeys(emails)), list(dict.fromkeys(linkedin_urls))


def _google_search_contacts(company_name):
    """
    Search Google for HR/recruiter contacts at a company.
    Parses only the result snippets (no JS rendering needed).
    """
    query = f'"{company_name}" recruiter OR "talent acquisition" OR "HR manager" email'
    url = f"https://www.google.com/search?q={quote_plus(query)}&num=5"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # Extract text from result snippets only
        snippets = []
        for div in soup.find_all("div", class_=re.compile(r"BNeawe|s3v9rd|VwiC3b")):
            snippets.append(div.get_text())
        text = " ".join(snippets)
        return extract_contacts_from_text(text)
    except Exception as e:
        logger.debug("Google search failed for %s: %s", company_name, e)
        return [], []


def enrich_jobs_with_contacts(jobs_needing_contacts):
    """
    Enrich jobs with recruiter contact data using a free multi-strategy scraper.

    Args:
        jobs_needing_contacts: list of dicts with at least {job_id, company, job_description, apply_url}

    Returns:
        dict mapping job_id -> {poster_name, poster_email, poster_phone, poster_linkedin}
    """
    results = {}
    company_cache = {}

    for job in jobs_needing_contacts:
        company = job.get("company", "").strip()
        job_id = job.get("job_id", "")
        if not company or not job_id:
            continue

        # Strategy 1: Extract directly from the JD text (fastest, zero network calls)
        jd_text = job.get("job_description", "")
        emails, linkedin_urls = extract_contacts_from_text(jd_text)

        if not emails and not linkedin_urls:
            # Strategy 2 + 3 — cache by company to avoid duplicate scrapes
            cache_key = company.lower()
            if cache_key not in company_cache:
                apply_url = job.get("apply_url", "")
                w_emails, w_linkedin = _try_company_website(company, apply_url)
                time.sleep(1)  # polite delay between companies

                g_emails, g_linkedin = [], []
                if not w_emails and not w_linkedin:
                    g_emails, g_linkedin = _google_search_contacts(company)
                    time.sleep(2)  # slightly longer delay for Google

                company_cache[cache_key] = (w_emails + g_emails, w_linkedin + g_linkedin)

            emails, linkedin_urls = company_cache[cache_key]

        if emails or linkedin_urls:
            results[job_id] = {
                "poster_name": "",  # Not available without API
                "poster_email": emails[0] if emails else "",
                "poster_phone": "",
                "poster_linkedin": linkedin_urls[0] if linkedin_urls else "",
            }
            logger.info("Found contact for %s at %s: %s", company, job_id, emails[0] if emails else linkedin_urls[0])

    logger.info(
        "Contact enrichment: %d/%d jobs got contacts (%d unique companies scraped)",
        len(results),
        len(jobs_needing_contacts),
        len(company_cache),
    )
    return results
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_contact_scraper.py -v
```

Expected: all 4 `PASSED`

**Step 5: Commit**

```bash
git add contact_scraper.py tests/test_contact_scraper.py
git commit -m "feat: add contact_scraper.py replacing broken apollo_enricher"
```

---

## Task 5: App routes — CV upload, re-score, gap analysis

**Files:**
- Modify: `app.py`

**Step 1: Add imports to app.py**

At the top of `app.py`, find the import block and add:

```python
from analyzer import (
    analyze_jobs, generate_tailored_points, parse_nlp_query,
    parse_cv_text, cv_score, compute_gap_analysis, load_cv_data, save_cv_data, CV_DATA_PATH,
)
from contact_scraper import enrich_jobs_with_contacts
```

Remove the old `from apollo_enricher import enrich_jobs_with_contacts` line.

**Step 2: Add CV routes to app.py**

Find the `# ---------------------------------------------------------------------------` separator before `# Run` (near line 965) and insert these routes before it:

```python
# ---------------------------------------------------------------------------
# CV Management Routes
# ---------------------------------------------------------------------------

@app.route("/cv")
def cv_page():
    cv_data = load_cv_data()
    return render_template("cv.html", cv_data=cv_data)


@app.route("/api/cv/upload", methods=["POST"])
def upload_cv():
    """Accept a CV file upload, parse it, and store cv_data.json."""
    if "cv_file" not in request.files:
        return jsonify({"ok": False, "error": "No file provided"}), 400

    f = request.files["cv_file"]
    filename = f.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    text = ""
    if ext == "pdf":
        try:
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(f.read())) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except Exception as e:
            return jsonify({"ok": False, "error": f"PDF parsing failed: {e}"}), 400
    elif ext == "docx":
        try:
            import docx, io
            doc = docx.Document(io.BytesIO(f.read()))
            text = "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            return jsonify({"ok": False, "error": f"DOCX parsing failed: {e}"}), 400
    elif ext in ("txt", ""):
        text = f.read().decode("utf-8", errors="ignore")
    else:
        return jsonify({"ok": False, "error": f"Unsupported file type: {ext}. Use PDF, DOCX, or TXT."}), 400

    if not text.strip():
        return jsonify({"ok": False, "error": "Could not extract text from the file"}), 400

    cv_data = parse_cv_text(text)
    save_cv_data(cv_data)
    logger.info("CV uploaded: %d skills detected", len(cv_data["skills"]))

    return jsonify({
        "ok": True,
        "skills_count": len(cv_data["skills"]),
        "skills": cv_data["skills"],
    })


@app.route("/api/cv/rescore", methods=["POST"])
def rescore_jobs():
    """Re-score all jobs in the DB against the uploaded CV."""
    cv_data = load_cv_data()
    if not cv_data:
        return jsonify({"ok": False, "error": "No CV uploaded yet"}), 400

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT job_id, role, job_description FROM job_listings")
    jobs = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not jobs:
        return jsonify({"ok": True, "updated": 0, "message": "No jobs in database"})

    conn = get_connection()
    cursor = conn.cursor()
    updated = 0
    for job in jobs:
        score = cv_score(job, cv_data)
        cursor.execute(
            "UPDATE job_listings SET cv_score = ? WHERE job_id = ?",
            (score, job["job_id"]),
        )
        updated += 1
    conn.commit()
    conn.close()

    logger.info("Re-scored %d jobs against CV", updated)
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/jobs/<job_id>/gap-analysis")
def gap_analysis(job_id):
    """Return gap analysis for a specific job against the uploaded CV."""
    cv_data = load_cv_data()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM job_listings WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({"ok": False, "error": "Job not found"}), 404

    job = dict(row)
    result = compute_gap_analysis(job, cv_data)
    return jsonify({"ok": True, **result})
```

**Step 3: Update nav in app.py to include CV page**

The nav links are in `templates/base.html` — this is handled in Task 6 (UI redesign).

**Step 4: Quick smoke test — start the server and verify routes exist**

```bash
cd /Users/gaurav/job-search-agent
python app.py &
sleep 2
curl -s http://localhost:5001/cv | grep -c "cv" && echo "CV page OK"
curl -s -X POST http://localhost:5001/api/cv/rescore | python3 -c "import sys,json; d=json.load(sys.stdin); print('Rescore API OK:', d)"
kill %1
```

Expected: `CV page OK` and `Rescore API OK: {'ok': False, 'error': 'No CV uploaded yet'}` (correct error since no CV yet)

**Step 5: Commit**

```bash
git add app.py
git commit -m "feat: add CV upload, rescore, and gap analysis routes to app.py"
```

---

## Task 6: CV upload template

**Files:**
- Create: `templates/cv.html`

**Step 1: Create templates/cv.html**

```html
{% extends "base.html" %}
{% block title %}CV — Job Search Agent{% endblock %}

{% block content %}
<div class="mb-6">
  <h1 class="text-2xl font-semibold text-gray-900">Your CV</h1>
  <p class="text-gray-500 mt-1 text-sm">Upload your CV to enable CV-based job scoring and gap analysis.</p>
</div>

<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">

  <!-- Upload Section -->
  <div class="bg-white border border-gray-200 rounded-xl p-6">
    <h2 class="text-base font-semibold text-gray-900 mb-4">Upload CV</h2>

    <div id="drop-zone"
         class="border-2 border-dashed border-gray-300 rounded-lg p-10 text-center cursor-pointer hover:border-indigo-400 hover:bg-indigo-50 transition-colors"
         onclick="document.getElementById('cv-file-input').click()"
         ondragover="event.preventDefault(); this.classList.add('border-indigo-500','bg-indigo-50')"
         ondragleave="this.classList.remove('border-indigo-500','bg-indigo-50')"
         ondrop="handleDrop(event)">
      <svg class="mx-auto h-10 w-10 text-gray-400 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
              d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
      </svg>
      <p class="text-sm font-medium text-gray-700">Drop your CV here or <span class="text-indigo-600">click to browse</span></p>
      <p class="text-xs text-gray-400 mt-1">Supports PDF, DOCX, TXT</p>
    </div>

    <input type="file" id="cv-file-input" accept=".pdf,.docx,.txt" class="hidden" onchange="uploadCV(this.files[0])">

    <div id="upload-status" class="hidden mt-4 p-3 rounded-lg text-sm"></div>

    {% if cv_data %}
    <div class="mt-4 p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-800">
      <p class="font-medium">✓ CV uploaded — {{ cv_data.skills | length }} skills detected</p>
      <p class="text-xs text-green-600 mt-0.5">Last updated: {{ cv_data.uploaded_at[:10] }}</p>
    </div>
    {% endif %}

    <button onclick="rescoreJobs()"
            class="mt-4 w-full px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            id="rescore-btn" {% if not cv_data %}disabled title="Upload a CV first"{% endif %}>
      Re-score All Jobs Against CV
    </button>
    <div id="rescore-status" class="hidden mt-2 text-sm text-gray-600"></div>
  </div>

  <!-- Detected Skills -->
  <div class="bg-white border border-gray-200 rounded-xl p-6">
    <h2 class="text-base font-semibold text-gray-900 mb-4">Detected Skills</h2>
    {% if cv_data and cv_data.skills %}
    <div class="flex flex-wrap gap-2" id="skills-list">
      {% for skill in cv_data.skills %}
      <span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-100">
        {{ skill }}
      </span>
      {% endfor %}
    </div>
    <p class="text-xs text-gray-400 mt-4">
      These skills are extracted from your CV and used to compute the CV match % on each job card.
      If a skill is missing, check that it appears clearly in your CV text.
    </p>
    {% else %}
    <p class="text-gray-400 text-sm">No CV uploaded yet. Skills will appear here after upload.</p>
    {% endif %}
  </div>

</div>
{% endblock %}

{% block scripts %}
<script>
function handleDrop(event) {
  event.preventDefault();
  document.getElementById('drop-zone').classList.remove('border-indigo-500', 'bg-indigo-50');
  const file = event.dataTransfer.files[0];
  if (file) uploadCV(file);
}

function uploadCV(file) {
  if (!file) return;
  const allowed = ['pdf', 'docx', 'txt'];
  const ext = file.name.split('.').pop().toLowerCase();
  if (!allowed.includes(ext)) {
    showStatus('upload-status', `Unsupported file type: .${ext}. Use PDF, DOCX, or TXT.`, 'error');
    return;
  }

  const formData = new FormData();
  formData.append('cv_file', file);

  showStatus('upload-status', 'Uploading and parsing CV...', 'info');

  fetch('/api/cv/upload', { method: 'POST', body: formData })
    .then(r => r.json())
    .then(data => {
      if (data.ok) {
        showStatus('upload-status', `✓ CV parsed — ${data.skills_count} skills detected. Reloading...`, 'success');
        setTimeout(() => window.location.reload(), 1200);
      } else {
        showStatus('upload-status', `Error: ${data.error}`, 'error');
      }
    })
    .catch(err => showStatus('upload-status', `Network error: ${err.message}`, 'error'));
}

function rescoreJobs() {
  const btn = document.getElementById('rescore-btn');
  btn.disabled = true;
  btn.textContent = 'Rescoring...';
  const status = document.getElementById('rescore-status');
  status.classList.remove('hidden');
  status.textContent = 'Computing CV match scores for all jobs...';

  fetch('/api/cv/rescore', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.textContent = 'Re-score All Jobs Against CV';
      if (data.ok) {
        status.textContent = `✓ ${data.updated} jobs re-scored. Go to Jobs page to see CV match %.`;
        status.className = 'mt-2 text-sm text-green-700';
      } else {
        status.textContent = `Error: ${data.error}`;
        status.className = 'mt-2 text-sm text-red-600';
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.textContent = 'Re-score All Jobs Against CV';
      status.textContent = `Network error: ${err.message}`;
    });
}

function showStatus(id, msg, type) {
  const el = document.getElementById(id);
  el.classList.remove('hidden');
  el.className = `mt-4 p-3 rounded-lg text-sm ${
    type === 'success' ? 'bg-green-50 text-green-800 border border-green-200' :
    type === 'error'   ? 'bg-red-50 text-red-700 border border-red-200' :
                         'bg-blue-50 text-blue-700 border border-blue-200'
  }`;
  el.textContent = msg;
}
</script>
{% endblock %}
```

**Step 2: Verify the page loads**

```bash
cd /Users/gaurav/job-search-agent
python app.py &
sleep 2
curl -s http://localhost:5001/cv | grep -c "Upload CV" && echo "CV template OK"
kill %1
```

Expected: `1` then `CV template OK`

**Step 3: Commit**

```bash
git add templates/cv.html
git commit -m "feat: add CV upload page template"
```

---

## Task 7: UI Redesign — base.html (sidebar nav, clean theme)

**Files:**
- Modify: `templates/base.html`

**Step 1: Replace base.html entirely**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Job Search Agent{% endblock %}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            brand: { 50:'#eef2ff', 100:'#e0e7ff', 500:'#6366f1', 600:'#4f46e5', 700:'#4338ca' }
          }
        }
      }
    }
  </script>
  <style>
    [x-cloak] { display: none !important; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif; }
    .sidebar-link { @apply flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-colors; }
    .sidebar-link.active { @apply bg-indigo-50 text-indigo-700 font-semibold; }
    .stat-card { @apply bg-white border border-gray-200 rounded-xl p-5; }
  </style>
  {% block head %}{% endblock %}
</head>
<body class="bg-gray-50 min-h-screen flex">

  <!-- Sidebar (desktop) -->
  <aside class="hidden md:flex flex-col w-56 bg-white border-r border-gray-200 min-h-screen fixed left-0 top-0 z-20">
    <div class="px-4 py-5 border-b border-gray-100">
      <div class="flex items-center gap-2">
        <div class="w-7 h-7 bg-indigo-600 rounded-lg flex items-center justify-center">
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2-2v2m8 0H8m8 0a2 2 0 012 2v6a2 2 0 01-2 2H8a2 2 0 01-2-2V8a2 2 0 012-2"/>
          </svg>
        </div>
        <span class="font-semibold text-gray-900 text-sm tracking-tight">Job Agent</span>
      </div>
    </div>

    <nav class="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
      <a href="{{ url_for('dashboard') }}"
         class="sidebar-link {% if request.endpoint == 'dashboard' %}active{% endif %}">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/>
        </svg>
        Dashboard
      </a>
      <a href="{{ url_for('jobs') }}"
         class="sidebar-link {% if request.endpoint == 'jobs' %}active{% endif %}">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
        </svg>
        Jobs
      </a>
      <a href="{{ url_for('cv_page') }}"
         class="sidebar-link {% if request.endpoint == 'cv_page' %}active{% endif %}">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
        </svg>
        My CV
      </a>
      <a href="{{ url_for('scraper') }}"
         class="sidebar-link {% if request.endpoint == 'scraper' %}active{% endif %}">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
        Run Scraper
      </a>
      <a href="{{ url_for('digests') }}"
         class="sidebar-link {% if request.endpoint == 'digests' %}active{% endif %}">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
        </svg>
        Digests
      </a>

      <div class="pt-3 mt-3 border-t border-gray-100">
        <a href="{{ url_for('preferences') }}"
           class="sidebar-link {% if request.endpoint == 'preferences' %}active{% endif %}">
          <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/>
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
          </svg>
          Settings
        </a>
      </div>
    </nav>
  </aside>

  <!-- Mobile bottom nav -->
  <nav class="md:hidden fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 z-20 flex">
    {% set mob_links = [
      ('dashboard', 'Dashboard', 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6'),
      ('jobs', 'Jobs', 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2'),
      ('cv_page', 'CV', 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z'),
      ('scraper', 'Scrape', 'M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15'),
      ('preferences', 'Settings', 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z')
    ] %}
    {% for endpoint, label, path in mob_links %}
    <a href="{{ url_for(endpoint) }}" class="flex-1 flex flex-col items-center py-2 text-xs
       {% if request.endpoint == endpoint %}text-indigo-600{% else %}text-gray-500{% endif %}">
      <svg class="w-5 h-5 mb-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="{{ path }}"/>
      </svg>
      {{ label }}
    </a>
    {% endfor %}
  </nav>

  <!-- Main content area -->
  <div class="flex-1 md:ml-56 min-h-screen flex flex-col">
    <!-- Flash messages -->
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        <div class="px-6 pt-4">
          {% for category, message in messages %}
            <div class="rounded-lg px-4 py-3 mb-2 text-sm
              {% if category == 'success' %}bg-green-50 text-green-800 border border-green-200
              {% elif category == 'error' %}bg-red-50 text-red-800 border border-red-200
              {% else %}bg-blue-50 text-blue-800 border border-blue-200{% endif %}">
              {{ message }}
            </div>
          {% endfor %}
        </div>
      {% endif %}
    {% endwith %}

    <!-- Page content -->
    <main class="flex-1 px-6 py-6 pb-20 md:pb-6">
      {% block content %}{% endblock %}
    </main>
  </div>

  {% block scripts %}{% endblock %}
</body>
</html>
```

**Step 2: Verify the app still runs**

```bash
cd /Users/gaurav/job-search-agent
python app.py &
sleep 2
curl -s http://localhost:5001/dashboard | grep -c "sidebar" && echo "Sidebar rendered"
kill %1
```

Expected: output > 0 then `Sidebar rendered`

**Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat: redesign base.html with Notion-style sidebar nav and clean light theme"
```

---

## Task 8: UI Redesign — jobs.html (new cards + gap analysis panel)

**Files:**
- Modify: `templates/jobs.html`

This is the most significant template change. The key changes:
- Left colored border per status
- CV match progress bar replaces score circle
- Gap analysis expandable panel
- Status pill moved to top-right
- Cleaner action buttons

**Step 1: Replace the job card section** in `templates/jobs.html`

Find the `<!-- Job Cards -->` comment and replace everything from there to the `{% else %}` empty state (keeping the empty state block). Replace with:

```html
<!-- Job Cards -->
{% if jobs %}
<div class="space-y-3" id="job-list">
  {% for job in jobs %}
  {% set status_colors = {
    0: 'border-gray-200',
    1: 'border-green-400',
    2: 'border-blue-400',
    3: 'border-cyan-400',
    4: 'border-orange-400',
    5: 'border-emerald-500',
    6: 'border-red-400'
  } %}
  {% set status_labels = {
    0: ('New', 'bg-gray-100 text-gray-600'),
    1: ('Applied', 'bg-green-100 text-green-700'),
    2: ('Saved', 'bg-blue-100 text-blue-700'),
    3: ('Phone Screen', 'bg-cyan-100 text-cyan-700'),
    4: ('Interview', 'bg-orange-100 text-orange-700'),
    5: ('Offer', 'bg-emerald-100 text-emerald-700'),
    6: ('Rejected', 'bg-red-100 text-red-600')
  } %}
  {% set s = job.applied_status | int %}
  <div class="bg-white border-l-4 {{ status_colors.get(s, 'border-gray-200') }} border border-gray-100 rounded-xl shadow-sm hover:shadow-md transition-shadow"
       id="job-{{ job.job_id }}">
    <div class="p-5">

      <!-- Top row: badges + status pill -->
      <div class="flex items-start justify-between gap-3 mb-2">
        <div class="flex flex-wrap gap-1.5">
          <span class="px-2 py-0.5 rounded-md text-xs font-medium bg-indigo-50 text-indigo-700">{{ job.portal }}</span>
          {% if job.remote_status == 'remote' %}
          <span class="px-2 py-0.5 rounded-md text-xs font-medium bg-green-50 text-green-700">Remote</span>
          {% elif job.remote_status == 'hybrid' %}
          <span class="px-2 py-0.5 rounded-md text-xs font-medium bg-amber-50 text-amber-700">Hybrid</span>
          {% else %}
          <span class="px-2 py-0.5 rounded-md text-xs font-medium bg-gray-100 text-gray-600">On-site</span>
          {% endif %}
          {% if job.company_funding_stage %}
          <span class="px-2 py-0.5 rounded-md text-xs font-medium bg-teal-50 text-teal-700">{{ job.company_funding_stage }}</span>
          {% endif %}
        </div>
        <!-- Status pill -->
        {% set label_text, label_cls = status_labels.get(s, ('New', 'bg-gray-100 text-gray-600')) %}
        <span class="flex-shrink-0 px-2.5 py-0.5 rounded-full text-xs font-semibold {{ label_cls }}">{{ label_text }}</span>
      </div>

      <!-- Role + Company -->
      <h3 class="text-base font-semibold text-gray-900 leading-snug">{{ job.role }}</h3>
      <p class="text-sm text-gray-500 mt-0.5">
        {{ job.company }}
        {% if job.location %} &middot; {{ job.location }}{% endif %}
        {% if job.date_posted %} &middot; <span class="text-indigo-500">{{ job.date_posted }}</span>{% endif %}
      </p>

      <!-- Info row -->
      <div class="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-gray-500">
        {% if job.salary %}<span class="text-green-700 font-medium">{{ job.salary_currency }} {{ job.salary }}</span>{% endif %}
        {% if job.experience_min is not none %}<span>{{ job.experience_min }}-{{ job.experience_max }}y exp</span>{% endif %}
        {% if job.company_size %}<span>{{ job.company_size }}</span>{% endif %}
      </div>

      <!-- CV Match Bar -->
      {% set cv = job.cv_score | default(0) | int %}
      <div class="mt-3">
        <div class="flex items-center justify-between text-xs text-gray-500 mb-1">
          <span>CV Match</span>
          <span class="font-semibold {% if cv >= 70 %}text-green-700{% elif cv >= 40 %}text-amber-600{% else %}text-gray-400{% endif %}">
            {{ cv }}%
          </span>
        </div>
        <div class="w-full bg-gray-100 rounded-full h-1.5">
          <div class="h-1.5 rounded-full {% if cv >= 70 %}bg-green-500{% elif cv >= 40 %}bg-amber-400{% else %}bg-gray-300{% endif %}"
               style="width: {{ cv }}%"></div>
        </div>
      </div>

      <!-- Description snippet -->
      {% if job.job_description %}
      <p class="text-xs text-gray-400 mt-2 line-clamp-2">{{ job.job_description[:180] }}{% if job.job_description|length > 180 %}...{% endif %}</p>
      {% endif %}

      <!-- Contact Info -->
      {% if job.poster_email or job.poster_linkedin %}
      <div class="mt-2 px-3 py-1.5 bg-blue-50 rounded-lg border border-blue-100 text-xs flex flex-wrap gap-x-3 gap-y-1">
        <span class="font-medium text-blue-700">Contact:</span>
        {% if job.poster_name %}<span class="text-blue-700">{{ job.poster_name }}</span>{% endif %}
        {% if job.poster_email %}<a href="mailto:{{ job.poster_email }}" class="text-blue-600 hover:underline">{{ job.poster_email }}</a>{% endif %}
        {% if job.poster_linkedin %}<a href="{{ job.poster_linkedin }}" target="_blank" rel="noopener" class="text-blue-600 hover:underline">LinkedIn</a>{% endif %}
      </div>
      {% endif %}

      <!-- Actions row -->
      <div class="flex flex-wrap items-center gap-2 mt-3 pt-3 border-t border-gray-100">
        {% if job.apply_url %}
        <a href="{{ job.apply_url }}" target="_blank" rel="noopener"
           class="inline-flex items-center gap-1 px-3 py-1.5 bg-indigo-600 text-white text-xs font-semibold rounded-lg hover:bg-indigo-700">
          Apply
          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
          </svg>
        </a>
        {% endif %}

        <select onchange="updateJobStatus('{{ job.job_id }}', this.value)"
                class="text-xs rounded-lg border-gray-200 bg-gray-50 px-2 py-1.5 border focus:border-indigo-400 focus:ring-0 cursor-pointer">
          <option value="0" {% if s == 0 %}selected{% endif %}>Set Status</option>
          <option value="1" {% if s == 1 %}selected{% endif %}>Applied</option>
          <option value="2" {% if s == 2 %}selected{% endif %}>Save</option>
          <option value="3" {% if s == 3 %}selected{% endif %}>Phone Screen</option>
          <option value="4" {% if s == 4 %}selected{% endif %}>Interview</option>
          <option value="5" {% if s == 5 %}selected{% endif %}>Offer</option>
          <option value="6" {% if s == 6 %}selected{% endif %}>Rejected</option>
        </select>

        <button onclick="toggleGapAnalysis('{{ job.job_id }}', this)"
                class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-200 bg-white text-gray-600 hover:bg-gray-50">
          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
          </svg>
          Gap Analysis
        </button>

        <button onclick="fetchTailoredPoints('{{ job.job_id }}', this)"
                class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-200 bg-white text-gray-600 hover:bg-gray-50">
          Tailored Points
        </button>

        <span class="ml-auto text-xs text-gray-300 font-mono">{{ job.relevance_score }}pt</span>
      </div>

      <!-- Gap Analysis Panel (hidden by default) -->
      <div id="gap-{{ job.job_id }}" class="hidden mt-3 p-4 bg-slate-50 rounded-xl border border-slate-200">
        <div class="flex items-center justify-between mb-3">
          <p class="text-xs font-semibold text-slate-700">Gap Analysis</p>
          <div id="gap-score-{{ job.job_id }}" class="text-xs font-bold text-slate-500"></div>
        </div>
        <div id="gap-content-{{ job.job_id }}" class="text-xs text-slate-500 italic">Loading...</div>
      </div>

      <!-- Tailored Points Panel (hidden by default) -->
      <div id="tailored-{{ job.job_id }}" class="hidden mt-3 p-4 bg-amber-50 rounded-xl border border-amber-200">
        <p class="text-xs font-semibold text-amber-800 mb-2">Tailored Resume Points</p>
        <ul id="tailored-list-{{ job.job_id }}" class="space-y-1 text-xs text-amber-900"></ul>
      </div>

    </div>
  </div>
  {% endfor %}
</div>
```

**Step 2: Replace the `updateJobStatus` and add `toggleGapAnalysis` in the `{% block scripts %}` section**

Find the existing `function updateJobStatus` in the scripts block and replace with:

```javascript
function updateJobStatus(jobId, status) {
  if (!status || status === '0') return; // Ignore "Set Status" placeholder
  fetch(`/api/jobs/${jobId}/status`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: parseInt(status)}),
  })
  .then(r => r.json())
  .then(data => { if (data.ok) window.location.reload(); })
  .catch(err => console.error('Error updating job status:', err));
}

function toggleGapAnalysis(jobId, btn) {
  const panel = document.getElementById('gap-' + jobId);
  const content = document.getElementById('gap-content-' + jobId);
  const scoreEl = document.getElementById('gap-score-' + jobId);

  if (!panel.classList.contains('hidden')) {
    panel.classList.add('hidden');
    return;
  }

  panel.classList.remove('hidden');

  // Already loaded
  if (content.dataset.loaded) return;

  fetch(`/api/jobs/${jobId}/gap-analysis`)
    .then(r => r.json())
    .then(data => {
      content.dataset.loaded = '1';
      if (!data.ok) {
        content.innerHTML = `<span class="text-red-500">${escapeHtml(data.error || 'Failed to load')}</span>`;
        return;
      }

      const cvPct = data.cv_score || 0;
      scoreEl.textContent = `${cvPct}% match`;
      scoreEl.className = `text-xs font-bold ${cvPct >= 70 ? 'text-green-600' : cvPct >= 40 ? 'text-amber-600' : 'text-gray-400'}`;

      const matchedHtml = (data.matched_skills || []).length > 0
        ? `<div class="mb-2">
            <p class="font-semibold text-green-700 mb-1">✓ Matched (${data.matched_skills.length})</p>
            <div class="flex flex-wrap gap-1">${data.matched_skills.map(s =>
              `<span class="px-1.5 py-0.5 bg-green-100 text-green-700 rounded text-xs">${escapeHtml(s)}</span>`
            ).join('')}</div>
           </div>`
        : '';

      const missingHtml = (data.missing_skills || []).length > 0
        ? `<div class="mb-2">
            <p class="font-semibold text-red-600 mb-1">✗ Missing (${data.missing_skills.length})</p>
            <div class="flex flex-wrap gap-1">${data.missing_skills.map(s =>
              `<span class="px-1.5 py-0.5 bg-red-50 text-red-600 rounded text-xs border border-red-100">${escapeHtml(s)}</span>`
            ).join('')}</div>
           </div>`
        : '';

      const tipsHtml = (data.action_steps || []).length > 0
        ? `<div>
            <p class="font-semibold text-slate-600 mb-1">→ Action Steps</p>
            <ul class="space-y-1">${data.action_steps.map(t =>
              `<li class="text-slate-500">${t.replace(/\*\*(.*?)\*\*/g, '<strong class="text-slate-700">$1</strong>')}</li>`
            ).join('')}</ul>
           </div>`
        : '';

      content.innerHTML = matchedHtml + missingHtml + tipsHtml ||
        '<span class="text-slate-400">Upload your CV to see gap analysis.</span>';
    })
    .catch(err => {
      content.innerHTML = `<span class="text-red-500">Error: ${escapeHtml(err.message)}</span>`;
    });
}
```

**Step 3: Start the app and navigate to /jobs to visually verify the new cards**

```bash
cd /Users/gaurav/job-search-agent
python app.py
```

Open `http://localhost:5001/jobs` in a browser. Verify:
- Cards have left colored border
- CV match progress bar visible
- Gap Analysis button present
- Status pill shows in top-right

**Step 4: Commit**

```bash
git add templates/jobs.html
git commit -m "feat: redesign job cards with status borders, CV match bar, and gap analysis panel"
```

---

## Task 9: UI Redesign — dashboard.html (clean stats layout)

**Files:**
- Modify: `templates/dashboard.html`

**Step 1: Update the stat cards section** — find the `<!-- Stat Cards -->` block and replace the 4 stat cards with:

```html
<!-- Stat Cards -->
<div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
  <div class="stat-card">
    <p class="text-xs font-medium text-gray-400 uppercase tracking-wide">Total Jobs</p>
    <p class="text-3xl font-bold text-gray-900 mt-1">{{ stats.total_jobs }}</p>
  </div>
  <div class="stat-card">
    <p class="text-xs font-medium text-gray-400 uppercase tracking-wide">Today</p>
    <p class="text-3xl font-bold text-gray-900 mt-1">{{ stats.jobs_today }}</p>
  </div>
  <div class="stat-card">
    <p class="text-xs font-medium text-gray-400 uppercase tracking-wide">This Week</p>
    <p class="text-3xl font-bold text-gray-900 mt-1">{{ stats.jobs_this_week }}</p>
  </div>
  <div class="stat-card">
    <p class="text-xs font-medium text-gray-400 uppercase tracking-wide">Applied / Saved</p>
    <p class="text-3xl font-bold text-gray-900 mt-1">{{ stats.applied_count }}<span class="text-lg text-gray-400">/{{ stats.saved_count }}</span></p>
  </div>
</div>
```

**Step 2: Update Quick Actions at the bottom** — replace with:

```html
<!-- Quick Actions -->
<div class="mt-8 flex flex-wrap gap-3">
  <a href="{{ url_for('scraper') }}" class="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700">
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
    </svg>
    Run Scraper
  </a>
  <a href="{{ url_for('jobs') }}" class="inline-flex items-center gap-2 px-4 py-2 bg-white text-gray-700 text-sm font-medium rounded-lg border border-gray-200 hover:bg-gray-50">
    Browse Jobs
  </a>
  <a href="{{ url_for('cv_page') }}" class="inline-flex items-center gap-2 px-4 py-2 bg-white text-gray-700 text-sm font-medium rounded-lg border border-gray-200 hover:bg-gray-50">
    Upload CV
  </a>
</div>
```

**Step 3: Start app and verify dashboard renders cleanly**

```bash
python app.py
```

Open `http://localhost:5001/dashboard`. Verify stat cards look clean with the new `.stat-card` class.

**Step 4: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat: refresh dashboard layout with clean stat cards and CV link"
```

---

## Task 10: Wire up contact_scraper in app.py pipeline

**Files:**
- Modify: `app.py` (update `_run_scraper_pipeline` and `_run_live_search` to use `contact_scraper` instead of `apollo_enricher`)

**Step 1: Find all references to `apollo_enricher` in app.py**

```bash
grep -n "apollo\|enrich_jobs" /Users/gaurav/job-search-agent/app.py
```

**Step 2: Update the `_run_apollo_enrichment` function** — find this function (around line 163) and update the signature + body:

```python
def _run_apollo_enrichment(job_ids, *args):
    """Run contact enrichment for a list of job IDs."""
    conn = get_connection()
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in job_ids)
    cursor.execute(
        f"SELECT job_id, company, job_description, apply_url FROM job_listings "
        f"WHERE job_id IN ({placeholders}) "
        f"AND (poster_email IS NULL OR poster_email = '')",
        job_ids,
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not rows:
        return

    contacts = enrich_jobs_with_contacts(rows)
    for jid, info in contacts.items():
        update_job_contacts(
            jid,
            info.get("poster_name", ""),
            info.get("poster_email", ""),
            info.get("poster_phone", ""),
            info.get("poster_linkedin", ""),
        )
```

**Step 3: In `_run_scraper_pipeline`, update the Phase 3.6 block** — change `apollo_key = preferences.get("apollo_api_key", "").strip()` and the `if apollo_key:` condition:

```python
        # Phase 3.6: Contact enrichment (via scraper, no API key needed)
        with scraper_lock:
            scraper_status["phase"] = "enriching_contacts"
        all_job_ids = [j["job_id"] for j in all_analyzed if j.get("job_id")]
        if all_job_ids:
            _run_apollo_enrichment(all_job_ids)
```

Do the same change in `_run_live_search` Phase 4 block.

**Step 4: Restart and verify no import errors**

```bash
python app.py
```

Expected: starts without `ImportError` or `ModuleNotFoundError`.

**Step 5: Commit**

```bash
git add app.py
git commit -m "feat: wire contact_scraper into scraper pipeline, remove apollo API key dependency"
```

---

## Task 11: Final integration test

**Step 1: Run all tests**

```bash
cd /Users/gaurav/job-search-agent
python -m pytest tests/ -v
```

Expected: all tests pass.

**Step 2: Start the app and do a manual walkthrough**

```bash
python app.py
```

Checklist:
- [ ] `http://localhost:5001/` → redirects to dashboard ✓
- [ ] Dashboard shows clean stat cards with sidebar nav ✓
- [ ] Jobs page shows cards with left status border, CV match bar, gap analysis button ✓
- [ ] CV page (`/cv`) loads with upload zone ✓
- [ ] Upload a `.txt` file (create a test one: `echo "Skills: SQL Python Agile" > /tmp/test_cv.txt`) ✓
- [ ] After upload, skills appear and "Re-score All Jobs" button becomes active ✓
- [ ] Click "Re-score All Jobs" → shows updated count ✓
- [ ] On a job card, click "Gap Analysis" → panel expands with matched/missing skills ✓
- [ ] Status dropdown on job card → changing value updates status ✓

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: complete job search agent upgrade - CV scoring, gap analysis, new UI, contact scraper"
```

---

## Summary of files changed

| File | Status |
|---|---|
| `requirements.txt` | Modified — add pdfplumber, python-docx, pytest |
| `database.py` | Modified — add cv_score column |
| `analyzer.py` | Modified — add parse_cv_text, cv_score, compute_gap_analysis, SKILL_TIPS |
| `contact_scraper.py` | Created — replaces apollo_enricher.py |
| `app.py` | Modified — CV routes, gap analysis route, use contact_scraper |
| `templates/base.html` | Modified — sidebar nav, clean light theme |
| `templates/jobs.html` | Modified — new cards, CV match bar, gap analysis panel |
| `templates/cv.html` | Created — CV upload page |
| `templates/dashboard.html` | Modified — stat cards, CV link |
| `tests/test_database.py` | Created |
| `tests/test_analyzer.py` | Created |
| `tests/test_contact_scraper.py` | Created |

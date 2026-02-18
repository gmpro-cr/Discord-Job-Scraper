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

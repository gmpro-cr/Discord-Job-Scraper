"""
scrapers.py - Web scrapers for multiple job portals.
Each scraper returns a list of job dicts with standardized keys:
  portal, company, role, salary, salary_currency, location,
  job_description, apply_url
"""

import time
import random
import logging
import hashlib
import json
import os
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urlencode

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# --- Cache ---
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(os.environ.get("DATA_DIR", _BASE_DIR), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]


def random_ua():
    return random.choice(USER_AGENTS)


def random_delay(config):
    """Sleep for a random delay between configured min and max seconds."""
    lo = config.get("scraping", {}).get("request_delay_min", 2)
    hi = config.get("scraping", {}).get("request_delay_max", 5)
    delay = random.uniform(lo, hi)
    time.sleep(delay)


def get_cache_path(url):
    h = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{h}.html")


def get_cached(url, expiry_hours=12):
    """Return cached HTML if it exists and hasn't expired."""
    path = get_cache_path(url)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < expiry_hours * 3600:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
    return None


def set_cache(url, html):
    path = get_cache_path(url)
    with open(path, "w", encoding="utf-8", errors="ignore") as f:
        f.write(html)


def fetch_url(url, config, use_selenium=False, retries=3):
    """
    Fetch a URL with retry logic and exponential backoff.
    Returns HTML string or None on failure.
    """
    timeout = config.get("scraping", {}).get("portal_timeout", 30)

    # Check cache first
    cached = get_cached(url, config.get("scraping", {}).get("cache_expiry_hours", 12))
    if cached:
        logger.debug("Cache hit for %s", url)
        return cached

    if use_selenium:
        return fetch_with_selenium(url, timeout, retries)

    for attempt in range(1, retries + 1):
        try:
            headers = {
                "User-Agent": random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
            resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            resp.raise_for_status()
            html = resp.text
            set_cache(url, html)
            return html
        except requests.RequestException as e:
            wait = 2 ** attempt + random.uniform(0, 1)
            logger.warning(
                "Attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                attempt, retries, url, e, wait,
            )
            if attempt < retries:
                time.sleep(wait)
    logger.error("All %d attempts failed for %s", retries, url)
    return None


def fetch_with_selenium(url, timeout=30, retries=3):
    """Fetch a JavaScript-heavy page using Selenium with bot-detection evasion."""
    for attempt in range(1, retries + 1):
        driver = None
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By

            chrome_bin = os.environ.get("CHROME_BIN")
            chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")

            if chromedriver_path:
                service = Service(executable_path=chromedriver_path)
            else:
                try:
                    from webdriver_manager.chrome import ChromeDriverManager
                    service = Service(ChromeDriverManager().install())
                except Exception:
                    service = Service()

            options = Options()
            if chrome_bin:
                options.binary_location = chrome_bin
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument(f"user-agent={random_ua()}")
            options.add_argument("--window-size=1920,1080")
            # Bot-detection evasion
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
            )
            driver.set_page_load_timeout(timeout)
            driver.get(url)

            # Wait until page source is substantial (JS rendered), up to 10s
            try:
                WebDriverWait(driver, 10).until(
                    lambda d: len(d.page_source) > 5000
                )
            except Exception:
                pass  # Fall through - page may still be usable
            time.sleep(3)  # Additional settle time

            html = driver.page_source
            set_cache(url, html)
            return html
        except Exception as e:
            wait = 2 ** attempt
            logger.warning(
                "Selenium attempt %d/%d failed for %s: %s", attempt, retries, url, e
            )
            if attempt < retries:
                time.sleep(wait)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
    logger.error("All Selenium attempts failed for %s", url)
    return None


def check_portal_health(portal_name, url, config):
    """Quick health check - see if portal is reachable."""
    try:
        resp = requests.head(
            url,
            headers={"User-Agent": random_ua()},
            timeout=10,
            allow_redirects=True,
        )
        ok = resp.status_code < 400
        logger.info("Portal %s health: %s (status %d)", portal_name, "OK" if ok else "DOWN", resp.status_code)
        return ok
    except requests.RequestException as e:
        logger.warning("Portal %s health check failed: %s", portal_name, e)
        return False


# =============================================================================
# Individual Portal Scrapers
# =============================================================================


def scrape_linkedin(job_titles, locations, config):
    """
    Scrape LinkedIn public job listings.
    LinkedIn heavily blocks scraping, so this uses their public job search page
    which doesn't require login for initial listings.
    """
    portal_config = config.get("portals", {}).get("linkedin", {})
    if not portal_config.get("enabled", True):
        logger.info("LinkedIn scraping disabled in config")
        return []

    jobs = []
    base_url = portal_config.get("base_url", "https://www.linkedin.com/jobs/search/")
    max_pages = portal_config.get("max_pages", 3)
    use_selenium = portal_config.get("use_selenium", True)

    for title in job_titles:
        for location in locations:
            for page in range(max_pages):
                params = {
                    "keywords": title,
                    "location": location,
                    "start": page * 25,
                    "f_TPR": "r259200",  # Past 3 days
                }
                url = f"{base_url}?{urlencode(params)}"
                logger.info("Scraping LinkedIn: %s in %s (page %d)", title, location, page + 1)

                html = fetch_url(url, config, use_selenium=use_selenium)
                if not html:
                    continue

                try:
                    soup = BeautifulSoup(html, "lxml")

                    # LinkedIn public search cards
                    cards = soup.select("div.base-card, div.job-search-card, li.result-card")
                    if not cards:
                        cards = soup.select("[data-entity-urn]")

                    for card in cards:
                        try:
                            title_el = card.select_one(
                                "h3.base-search-card__title, "
                                "h3.job-search-card__title, "
                                "span.sr-only, "
                                "a.job-card-list__title"
                            )
                            company_el = card.select_one(
                                "h4.base-search-card__subtitle, "
                                "a.job-search-card__subtitle-link, "
                                "h4.job-search-card__company-name"
                            )
                            location_el = card.select_one(
                                "span.job-search-card__location, "
                                "span.base-search-card__metadata"
                            )
                            link_el = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
                            date_el = card.select_one("time[datetime], time")

                            role = title_el.get_text(strip=True) if title_el else None
                            company = company_el.get_text(strip=True) if company_el else None
                            loc = location_el.get_text(strip=True) if location_el else location
                            apply_url = link_el["href"] if link_el and link_el.has_attr("href") else None
                            date_posted = date_el.get("datetime", "") if date_el else ""

                            if role and company:
                                jobs.append({
                                    "portal": "LinkedIn",
                                    "company": company,
                                    "role": role,
                                    "salary": None,
                                    "salary_currency": "INR",
                                    "location": loc,
                                    "job_description": "",
                                    "apply_url": apply_url or f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(role + ' ' + company)}",
                                    "date_posted": date_posted,
                                })
                        except Exception as e:
                            logger.debug("Error parsing LinkedIn card: %s", e)
                            continue

                except Exception as e:
                    logger.error("Error parsing LinkedIn page: %s", e)

                random_delay(config)

    logger.info("LinkedIn: found %d jobs", len(jobs))
    return jobs


def _parse_indeed_initial_data(html):
    """Extract jobs from Indeed's embedded JSON: _initialData + mosaic providerData."""
    import re as _re
    jobs = []

    def _extract_json_at(html, start_idx):
        """Extract a balanced JSON object starting at start_idx."""
        depth = 0
        for i in range(start_idx, min(start_idx + 3_000_000, len(html))):
            if html[i] == "{":
                depth += 1
            elif html[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start_idx:i + 1])
                    except (json.JSONDecodeError, TypeError):
                        return None
        return None

    def _find_job_results(obj, depth=0):
        """Recursively find a 'results' list containing job dicts."""
        if depth > 8 or not isinstance(obj, dict):
            return None
        for k, v in obj.items():
            if k == "results" and isinstance(v, list) and len(v) > 0:
                first = v[0]
                if isinstance(first, dict) and ("job" in first or "title" in first or "jobkey" in first):
                    return v
            if isinstance(v, dict):
                found = _find_job_results(v, depth + 1)
                if found:
                    return found
        return None

    # Collect JSON data sources to search for job results
    data_sources = []

    # Source 1: window._initialData
    for marker in ("window._initialData=", "window._initialData ="):
        idx = html.find(marker)
        if idx != -1:
            json_start = html.index("{", idx)
            data = _extract_json_at(html, json_start)
            if data and isinstance(data, dict):
                data_sources.append(data)
            break

    # Source 2: mosaic-provider-jobcards (primary source for card-level data with dates)
    for m in _re.finditer(r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*', html):
        json_start = html.index("{", m.end() - 1)
        data = _extract_json_at(html, json_start)
        if data and isinstance(data, dict):
            data_sources.append(data)

    # Extract results from all sources, dedup by jobkey
    seen_keys = set()
    all_results = []
    for data in data_sources:
        results = _find_job_results(data) or []
        for item in results:
            job_data = item.get("job") or item
            key = job_data.get("jobkey") or job_data.get("key", "")
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            all_results.append(item)

    for item in all_results:
        try:
            job_data = item.get("job") or item
            title = job_data.get("title") or job_data.get("displayTitle", "")
            company = job_data.get("sourceEmployerName") or job_data.get("company") or job_data.get("truncatedCompany", "")
            raw_loc = job_data.get("formattedLocation") or job_data.get("location", "")
            if isinstance(raw_loc, dict):
                loc = (raw_loc.get("formatted") or {}).get("long") or raw_loc.get("fullAddress") or raw_loc.get("city", "")
            else:
                loc = raw_loc

            # Date: try datePublished (ms), pubDate (ms), createDate (ms), or formattedRelativeTime
            date_posted = ""
            for date_field in ("datePublished", "pubDate", "createDate"):
                date_ms = job_data.get(date_field)
                if date_ms and isinstance(date_ms, (int, float)) and date_ms > 1_000_000_000:
                    # Treat as ms if > 10^12, else seconds
                    ts = date_ms / 1000 if date_ms > 1e12 else date_ms
                    date_posted = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    break
            if not date_posted:
                date_posted = _parse_relative_date(job_data.get("formattedRelativeTime", ""))

            job_key = job_data.get("key") or job_data.get("jobkey", "")
            apply_url = f"https://in.indeed.com/viewjob?jk={job_key}" if job_key else ""

            salary = job_data.get("salary") or job_data.get("salarySnippet") or {}
            salary_text = ""
            if isinstance(salary, dict):
                salary_text = salary.get("text") or salary.get("salaryTextFormatted") or ""

            # Strip HTML from snippet
            snippet = job_data.get("snippet") or ""
            if "<" in snippet:
                snippet = _re.sub(r"<[^>]+>", " ", snippet).strip()

            if title and company:
                jobs.append({
                    "portal": "Indeed",
                    "company": company,
                    "role": title,
                    "salary": salary_text or None,
                    "salary_currency": "INR",
                    "location": loc,
                    "job_description": snippet[:500],
                    "apply_url": apply_url,
                    "date_posted": date_posted,
                })
        except (TypeError, KeyError, AttributeError):
            continue

    return jobs


def _parse_relative_date(text):
    """Parse relative date text like '1 day ago', 'Today', 'Just posted' into YYYY-MM-DD."""
    if not text:
        return ""
    text_lower = text.lower()
    if "today" in text_lower or "just" in text_lower:
        return datetime.now().strftime("%Y-%m-%d")
    if "day" in text_lower:
        try:
            days = int("".join(c for c in text if c.isdigit()) or "0")
            return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    if "hour" in text_lower:
        return datetime.now().strftime("%Y-%m-%d")
    if "week" in text_lower:
        try:
            weeks = int("".join(c for c in text if c.isdigit()) or "1")
            return (datetime.now() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    if "month" in text_lower:
        try:
            months = int("".join(c for c in text if c.isdigit()) or "1")
            return (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def scrape_indeed(job_titles, locations, config):
    """Scrape Indeed job listings."""
    portal_config = config.get("portals", {}).get("indeed", {})
    if not portal_config.get("enabled", True):
        logger.info("Indeed scraping disabled in config")
        return []

    jobs = []
    base_url = portal_config.get("base_url", "https://in.indeed.com/jobs")
    max_pages = portal_config.get("max_pages", 3)
    use_selenium = portal_config.get("use_selenium", False)

    for title in job_titles:
        for location in locations:
            for page in range(max_pages):
                params = {
                    "q": title,
                    "l": location,
                    "start": page * 10,
                    "fromage": 3,  # Past 3 days
                }
                url = f"{base_url}?{urlencode(params)}"
                logger.info("Scraping Indeed: %s in %s (page %d)", title, location, page + 1)

                html = fetch_url(url, config, use_selenium=use_selenium)
                if not html:
                    continue

                try:
                    # Strategy 1: Parse window._initialData JSON (best for dates)
                    json_jobs = _parse_indeed_initial_data(html)
                    if json_jobs:
                        jobs.extend(json_jobs)
                        logger.info("Indeed: extracted %d jobs via JSON from %s", len(json_jobs), url)
                        random_delay(config)
                        continue

                    # Strategy 2: Fallback to CSS card parsing
                    soup = BeautifulSoup(html, "lxml")

                    cards = soup.select(
                        "div.job_seen_beacon, "
                        "div.jobsearch-SerpJobCard, "
                        "div.cardOutline, "
                        "td.resultContent"
                    )

                    for card in cards:
                        try:
                            title_el = card.select_one(
                                "h2.jobTitle span[title], "
                                "h2.jobTitle a, "
                                "a.jcs-JobTitle"
                            )
                            company_el = card.select_one(
                                "span[data-testid='company-name'], "
                                "span.companyName, "
                                "span.company"
                            )
                            location_el = card.select_one(
                                "div[data-testid='text-location'], "
                                "div.companyLocation, "
                                "span.location"
                            )
                            salary_el = card.select_one(
                                "div.salary-snippet-container, "
                                "div.metadata.salary-snippet-container, "
                                "span.salary-snippet"
                            )
                            link_el = card.select_one("a[href*='/rc/clk'], a[data-jk], h2.jobTitle a")
                            date_el = card.select_one(
                                "span.date, "
                                "span[data-testid='myJobsStateDate'], "
                                "span.css-qvloho"
                            )

                            role = title_el.get_text(strip=True) if title_el else None
                            company = company_el.get_text(strip=True) if company_el else None
                            loc = location_el.get_text(strip=True) if location_el else location
                            salary = salary_el.get_text(strip=True) if salary_el else None
                            date_text = date_el.get_text(strip=True) if date_el else ""

                            href = None
                            if link_el and link_el.has_attr("href"):
                                href = link_el["href"]
                                if href.startswith("/"):
                                    href = f"https://in.indeed.com{href}"

                            date_posted = _parse_relative_date(date_text)

                            if role and company:
                                jobs.append({
                                    "portal": "Indeed",
                                    "company": company,
                                    "role": role,
                                    "salary": salary,
                                    "salary_currency": "INR",
                                    "location": loc,
                                    "job_description": "",
                                    "apply_url": href or f"https://in.indeed.com/jobs?q={quote_plus(role + ' ' + company)}",
                                    "date_posted": date_posted,
                                })
                        except Exception as e:
                            logger.debug("Error parsing Indeed card: %s", e)
                            continue

                except Exception as e:
                    logger.error("Error parsing Indeed page: %s", e)

                random_delay(config)

    logger.info("Indeed: found %d jobs", len(jobs))
    return jobs


def _naukri_ld_json_to_job(item):
    """Convert a JSON-LD JobPosting object to our standard job dict."""
    return {
        "portal": "Naukri",
        "company": (item.get("hiringOrganization") or {}).get("name", ""),
        "role": item.get("title", ""),
        "salary": None,
        "salary_currency": "INR",
        "location": (
            (item.get("jobLocation") or [{}])[0]
            .get("address", {})
            .get("addressLocality", "")
            if isinstance(item.get("jobLocation"), list)
            else (item.get("jobLocation") or {}).get("address", {}).get("addressLocality", "")
        ),
        "job_description": (item.get("description") or "")[:500],
        "apply_url": item.get("url", ""),
        "date_posted": item.get("datePosted", ""),
    }


def _parse_naukri_json(soup):
    """Try to extract jobs from JSON-LD or __NEXT_DATA__ before falling back to CSS."""
    jobs = []

    # Strategy 1: application/ld+json with JobPosting
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "JobPosting" and item.get("title"):
                    job = _naukri_ld_json_to_job(item)
                    if job["role"] and job["company"]:
                        jobs.append(job)
        except (json.JSONDecodeError, TypeError):
            continue

    if jobs:
        return jobs

    # Strategy 2: __NEXT_DATA__ (Naukri is a React SPA)
    next_script = soup.select_one('script#__NEXT_DATA__')
    if next_script:
        try:
            next_data = json.loads(next_script.string or "")
            # Navigate the typical Naukri NEXT_DATA structure
            page_props = next_data.get("props", {}).get("pageProps", {})
            # Try common keys where job data lives
            for key in ("jobDetails", "searchResult", "jobfeed", "initialJobs", "jobs"):
                raw = page_props.get(key)
                if not raw:
                    continue
                items = raw if isinstance(raw, list) else raw.get("jobDetails", raw.get("jobs", []))
                if not isinstance(items, list):
                    continue
                for item in items:
                    role = item.get("title") or item.get("jobTitle") or item.get("designations") or ""
                    company = item.get("companyName") or item.get("company") or ""
                    if role and company:
                        raw_date = item.get("createdDate") or item.get("datePosted") or item.get("footerPlaceholderLabel", "")
                        # createdDate may be ISO format or relative text like "1 day ago"
                        date_posted = ""
                        if raw_date:
                            if len(raw_date) >= 10 and raw_date[4:5] == "-":
                                date_posted = raw_date[:10]  # Already YYYY-MM-DD
                            else:
                                date_posted = _parse_relative_date(raw_date)
                        jobs.append({
                            "portal": "Naukri",
                            "company": company,
                            "role": role,
                            "salary": item.get("salary") or item.get("placeholders", {}).get("salary"),
                            "salary_currency": "INR",
                            "location": item.get("location") or item.get("placeholders", {}).get("location", ""),
                            "job_description": (item.get("description") or item.get("jobDescription") or "")[:500],
                            "apply_url": item.get("jdURL") or item.get("url") or "",
                            "date_posted": date_posted,
                        })
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    return jobs


def scrape_naukri(job_titles, locations, config):
    """Scrape Naukri.com job listings (India's largest job portal)."""
    portal_config = config.get("portals", {}).get("naukri", {})
    if not portal_config.get("enabled", True):
        logger.info("Naukri scraping disabled in config")
        return []

    jobs = []
    max_pages = portal_config.get("max_pages", 3)
    use_selenium = portal_config.get("use_selenium", True)

    for title in job_titles:
        for location in locations:
            for page in range(1, max_pages + 1):
                title_slug = title.lower().replace(" ", "-")
                location_slug = location.lower().replace(" ", "-")
                url = f"https://www.naukri.com/{title_slug}-jobs-in-{location_slug}-{page}?jobAge=3"

                logger.info("Scraping Naukri: %s in %s (page %d)", title, location, page)

                html = fetch_url(url, config, use_selenium=use_selenium)
                if not html:
                    continue

                try:
                    soup = BeautifulSoup(html, "lxml")

                    # Try JSON-first parsing
                    json_jobs = _parse_naukri_json(soup)
                    if json_jobs:
                        jobs.extend(json_jobs)
                        logger.info("Naukri: extracted %d jobs via JSON from %s", len(json_jobs), url)
                        random_delay(config)
                        continue

                    # Fallback: broadened CSS selectors
                    cards = soup.select(
                        "article.jobTuple, "
                        "div.srp-jobtuple-wrapper, "
                        "div.cust-job-tuple, "
                        "div[class*='jobTuple'], "
                        "div[class*='job-tuple'], "
                        "div[class*='srp-tuple']"
                    )

                    for card in cards:
                        try:
                            title_el = card.select_one(
                                "a.title, a[class*='title'], h2 a, "
                                "a[class*='jobTitle'], a[class*='designation']"
                            )
                            company_el = card.select_one(
                                "a.subTitle, a[class*='comp-name'], "
                                "span[class*='comp-name'], a[class*='companyName']"
                            )
                            location_el = card.select_one(
                                "span[class*='locWdth'], span[class*='loc-wrap'], "
                                "span[class*='location'], span[class*='loc'] span"
                            )
                            salary_el = card.select_one(
                                "span[class*='sal-wrap'] span, "
                                "span[class*='salary'], li[class*='salary'] span"
                            )
                            desc_el = card.select_one(
                                "div[class*='job-description'], "
                                "span[class*='job-description'], "
                                "div[class*='description']"
                            )
                            date_el = card.select_one(
                                "span.job-post-day, "
                                "span[class*='job-post-day'], "
                                "span[class*='postDay']"
                            )

                            role = title_el.get_text(strip=True) if title_el else None
                            company = company_el.get_text(strip=True) if company_el else None
                            loc = location_el.get_text(strip=True) if location_el else location
                            salary = salary_el.get_text(strip=True) if salary_el else None
                            description = desc_el.get_text(strip=True) if desc_el else ""
                            apply_url = title_el["href"] if title_el and title_el.has_attr("href") else None
                            date_text = date_el.get_text(strip=True) if date_el else ""
                            date_posted = _parse_relative_date(date_text)

                            if role and company:
                                jobs.append({
                                    "portal": "Naukri",
                                    "company": company,
                                    "role": role,
                                    "salary": salary,
                                    "salary_currency": "INR",
                                    "location": loc,
                                    "job_description": description,
                                    "apply_url": apply_url or f"https://www.naukri.com/{title_slug}-jobs",
                                    "date_posted": date_posted,
                                })
                        except Exception as e:
                            logger.debug("Error parsing Naukri card: %s", e)
                            continue

                except Exception as e:
                    logger.error("Error parsing Naukri page: %s", e)

                random_delay(config)

    logger.info("Naukri: found %d jobs", len(jobs))
    return jobs


def scrape_hiringcafe(job_titles, locations, config):
    """Scrape HiringCafe (startup hiring platform)."""
    portal_config = config.get("portals", {}).get("hiringcafe", {})
    if not portal_config.get("enabled", True):
        logger.info("HiringCafe scraping disabled in config")
        return []

    jobs = []
    base_url = portal_config.get("base_url", "https://hiring.cafe")
    max_pages = portal_config.get("max_pages", 2)

    for title in job_titles:
        url = f"{base_url}/search?q={quote_plus(title)}"
        logger.info("Scraping HiringCafe: %s", title)

        html = fetch_url(url, config)
        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "lxml")

            cards = soup.select(
                "div.job-card, "
                "div.job-listing, "
                "article.job, "
                "div[class*='job'], "
                "div[class*='listing']"
            )

            for card in cards:
                try:
                    title_el = card.select_one("h2, h3, a[class*='title'], span[class*='title']")
                    company_el = card.select_one("span[class*='company'], div[class*='company'], p[class*='company']")
                    location_el = card.select_one("span[class*='location'], div[class*='location']")
                    salary_el = card.select_one("span[class*='salary'], div[class*='salary']")
                    link_el = card.select_one("a[href]")

                    role = title_el.get_text(strip=True) if title_el else None
                    company = company_el.get_text(strip=True) if company_el else None
                    loc = location_el.get_text(strip=True) if location_el else "India"
                    salary = salary_el.get_text(strip=True) if salary_el else None
                    apply_url = link_el["href"] if link_el and link_el.has_attr("href") else None
                    if apply_url and not apply_url.startswith("http"):
                        apply_url = f"{base_url}{apply_url}"

                    if role and company:
                        jobs.append({
                            "portal": "HiringCafe",
                            "company": company,
                            "role": role,
                            "salary": salary,
                            "salary_currency": "INR",
                            "location": loc,
                            "job_description": "",
                            "apply_url": apply_url or url,
                        })
                except Exception as e:
                    logger.debug("Error parsing HiringCafe card: %s", e)
                    continue

        except Exception as e:
            logger.error("Error parsing HiringCafe page: %s", e)

        random_delay(config)

    logger.info("HiringCafe: found %d jobs", len(jobs))
    return jobs


def scrape_angellist(job_titles, locations, config):
    """Scrape Wellfound (formerly AngelList) for startup jobs."""
    portal_config = config.get("portals", {}).get("angellist", {})
    if not portal_config.get("enabled", True):
        logger.info("AngelList/Wellfound scraping disabled in config")
        return []

    jobs = []
    base_url = portal_config.get("base_url", "https://wellfound.com/jobs")
    use_selenium = portal_config.get("use_selenium", True)

    for title in job_titles:
        for location in locations:
            url = f"{base_url}?keywords={quote_plus(title)}&locations={quote_plus(location)}"
            logger.info("Scraping Wellfound: %s in %s", title, location)

            html = fetch_url(url, config, use_selenium=use_selenium)
            if not html:
                continue

            try:
                soup = BeautifulSoup(html, "lxml")

                cards = soup.select(
                    "div[class*='jobListing'], "
                    "div[class*='StartupResult'], "
                    "div[data-test='job-listing']"
                )

                for card in cards:
                    try:
                        title_el = card.select_one("h2, a[class*='title'], span[class*='jobTitle']")
                        company_el = card.select_one("h3, span[class*='company'], a[class*='company']")
                        location_el = card.select_one("span[class*='location']")
                        salary_el = card.select_one("span[class*='salary'], span[class*='compensation']")
                        link_el = card.select_one("a[href*='/jobs/']")

                        role = title_el.get_text(strip=True) if title_el else None
                        company = company_el.get_text(strip=True) if company_el else None
                        loc = location_el.get_text(strip=True) if location_el else location
                        salary = salary_el.get_text(strip=True) if salary_el else None
                        apply_url = link_el["href"] if link_el and link_el.has_attr("href") else None
                        if apply_url and not apply_url.startswith("http"):
                            apply_url = f"https://wellfound.com{apply_url}"

                        if role and company:
                            jobs.append({
                                "portal": "Wellfound",
                                "company": company,
                                "role": role,
                                "salary": salary,
                                "salary_currency": "USD",
                                "location": loc,
                                "job_description": "",
                                "apply_url": apply_url or url,
                            })
                    except Exception as e:
                        logger.debug("Error parsing Wellfound card: %s", e)
                        continue

            except Exception as e:
                logger.error("Error parsing Wellfound page: %s", e)

            random_delay(config)

    logger.info("Wellfound: found %d jobs", len(jobs))
    return jobs


def _iimjobs_ld_json_to_job(item):
    """Convert a JSON-LD JobPosting object from IIMJobs to our standard dict."""
    return {
        "portal": "IIMJobs",
        "company": (item.get("hiringOrganization") or {}).get("name", ""),
        "role": item.get("title", ""),
        "salary": None,
        "salary_currency": "INR",
        "location": (
            (item.get("jobLocation") or [{}])[0]
            .get("address", {})
            .get("addressLocality", "")
            if isinstance(item.get("jobLocation"), list)
            else (item.get("jobLocation") or {}).get("address", {}).get("addressLocality", "")
        ),
        "job_description": (item.get("description") or "")[:500],
        "apply_url": item.get("url", ""),
    }


def _parse_iimjobs_nextdata(soup, base_url):
    """Try to extract jobs from __NEXT_DATA__ or JSON-LD on IIMJobs pages."""
    jobs = []

    # Strategy 1: application/ld+json
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "JobPosting" and item.get("title"):
                    job = _iimjobs_ld_json_to_job(item)
                    if job["role"] and job["company"]:
                        jobs.append(job)
        except (json.JSONDecodeError, TypeError):
            continue

    if jobs:
        return jobs

    # Strategy 2: __NEXT_DATA__
    next_script = soup.select_one('script#__NEXT_DATA__')
    if next_script:
        try:
            next_data = json.loads(next_script.string or "")
            page_props = next_data.get("props", {}).get("pageProps", {})
            for key in ("jobfeed", "jobs", "searchResults", "jobList", "initialJobs"):
                raw = page_props.get(key)
                if not raw:
                    continue
                items = raw if isinstance(raw, list) else raw.get("jobs", raw.get("data", []))
                if not isinstance(items, list):
                    continue
                for item in items:
                    role = item.get("title") or item.get("jobTitle") or item.get("heading") or ""
                    company = item.get("company") or item.get("companyName") or item.get("organization") or ""
                    if role and company:
                        link = item.get("url") or item.get("jobUrl") or item.get("slug", "")
                        if link and not link.startswith("http"):
                            link = f"{base_url}{link}"
                        jobs.append({
                            "portal": "IIMJobs",
                            "company": company,
                            "role": role,
                            "salary": item.get("salary") or item.get("ctc"),
                            "salary_currency": "INR",
                            "location": item.get("location") or item.get("city") or "",
                            "job_description": (item.get("description") or item.get("snippet") or "")[:500],
                            "apply_url": link,
                        })
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    return jobs


def scrape_iimjobs(job_titles, locations, config):
    """Scrape IIMJobs for MBA/experienced professional jobs."""
    portal_config = config.get("portals", {}).get("iimjobs", {})
    if not portal_config.get("enabled", True):
        logger.info("IIMJobs scraping disabled in config")
        return []

    jobs = []
    base_url = portal_config.get("base_url", "https://www.iimjobs.com")
    max_pages = portal_config.get("max_pages", 2)
    use_selenium = portal_config.get("use_selenium", True)

    for title in job_titles:
        for location in locations:
            url = f"{base_url}/search?q={quote_plus(title)}&l={quote_plus(location)}"
            logger.info("Scraping IIMJobs: %s in %s", title, location)

            html = fetch_url(url, config, use_selenium=use_selenium)
            if not html:
                continue

            try:
                soup = BeautifulSoup(html, "lxml")

                # Try JSON-first parsing
                json_jobs = _parse_iimjobs_nextdata(soup, base_url)
                if json_jobs:
                    jobs.extend(json_jobs)
                    logger.info("IIMJobs: extracted %d jobs via JSON from %s", len(json_jobs), url)
                    random_delay(config)
                    continue

                # Fallback: broadened CSS selectors
                cards = soup.select(
                    "div.job-listing, "
                    "div.jobTuple, "
                    "div[class*='job-card'], "
                    "div[class*='job-listing'], "
                    "div[class*='jobCard'], "
                    "li.listing, "
                    "li[class*='job']"
                )

                for card in cards:
                    try:
                        title_el = card.select_one(
                            "h2 a, h3 a, a.job-title, a[class*='title'], "
                            "a[class*='jobTitle'], a[class*='heading']"
                        )
                        company_el = card.select_one(
                            "span.company, div.company, a[class*='company'], "
                            "span[class*='company'], span[class*='org']"
                        )
                        location_el = card.select_one(
                            "span.location, div.location, span[class*='loc'], "
                            "span[class*='location'], span[class*='city']"
                        )
                        salary_el = card.select_one(
                            "span.salary, div.salary, span[class*='sal'], "
                            "span[class*='salary'], span[class*='ctc']"
                        )
                        desc_el = card.select_one(
                            "div.description, p.desc, span.desc, "
                            "div[class*='description'], span[class*='snippet']"
                        )

                        role = title_el.get_text(strip=True) if title_el else None
                        company = company_el.get_text(strip=True) if company_el else None
                        loc = location_el.get_text(strip=True) if location_el else location
                        salary = salary_el.get_text(strip=True) if salary_el else None
                        description = desc_el.get_text(strip=True) if desc_el else ""
                        apply_url = title_el["href"] if title_el and title_el.has_attr("href") else None
                        if apply_url and not apply_url.startswith("http"):
                            apply_url = f"{base_url}{apply_url}"

                        if role and company:
                            jobs.append({
                                "portal": "IIMJobs",
                                "company": company,
                                "role": role,
                                "salary": salary,
                                "salary_currency": "INR",
                                "location": loc,
                                "job_description": description,
                                "apply_url": apply_url or url,
                            })
                    except Exception as e:
                        logger.debug("Error parsing IIMJobs card: %s", e)
                        continue

            except Exception as e:
                logger.error("Error parsing IIMJobs page: %s", e)

            random_delay(config)

    logger.info("IIMJobs: found %d jobs", len(jobs))
    return jobs


# =============================================================================
# Orchestrator
# =============================================================================

SCRAPER_MAP = {
    "linkedin": scrape_linkedin,
    "indeed": scrape_indeed,
    "naukri": scrape_naukri,
    "hiringcafe": scrape_hiringcafe,
    "angellist": scrape_angellist,
    "iimjobs": scrape_iimjobs,
}


def deduplicate_jobs(jobs):
    """Remove duplicate jobs based on company + role + location."""
    seen = set()
    unique = []
    for job in jobs:
        key = (
            job["company"].lower().strip(),
            job["role"].lower().strip(),
            (job.get("location") or "").lower().strip(),
        )
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def scrape_all_portals(job_titles, locations, config, progress_callback=None):
    """
    Scrape all enabled portals using threading for parallelism.
    Returns (all_jobs, portal_results) where portal_results is a dict of
    portal_name -> {"status": "success"/"failed", "count": int, "time": float}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    thread_count = config.get("scraping", {}).get("thread_count", 4)
    portal_results = {}
    all_jobs = []

    enabled_portals = []
    for portal_name, portal_conf in config.get("portals", {}).items():
        if portal_conf.get("enabled", True) and portal_name in SCRAPER_MAP:
            enabled_portals.append(portal_name)

    total = len(enabled_portals)
    completed = 0

    def run_scraper(portal_name):
        start_time = time.time()
        try:
            scraper_fn = SCRAPER_MAP[portal_name]
            jobs = scraper_fn(job_titles, locations, config)
            elapsed = time.time() - start_time
            return portal_name, jobs, "success", elapsed
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("Portal %s failed: %s", portal_name, e)
            return portal_name, [], "failed", elapsed

    # Run health checks first
    logger.info("Running portal health checks...")
    for portal_name in enabled_portals:
        base_url = config["portals"][portal_name].get("base_url", "")
        if base_url:
            check_portal_health(portal_name, base_url, config)

    logger.info("Starting scraping from %d portals with %d threads", total, thread_count)

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = {
            executor.submit(run_scraper, name): name
            for name in enabled_portals
        }
        for future in as_completed(futures):
            portal_name, jobs, status, elapsed = future.result()
            completed += 1
            portal_results[portal_name] = {
                "status": status,
                "count": len(jobs),
                "time": round(elapsed, 1),
            }
            all_jobs.extend(jobs)
            if progress_callback:
                progress_callback(portal_name, status, len(jobs), completed, total)
            logger.info(
                "Portal %s: %s (%d jobs in %.1fs) [%d/%d]",
                portal_name, status, len(jobs), elapsed, completed, total,
            )

    # Deduplicate
    before_dedup = len(all_jobs)
    all_jobs = deduplicate_jobs(all_jobs)
    dupes_removed = before_dedup - len(all_jobs)

    succeeded = sum(1 for r in portal_results.values() if r["status"] == "success")
    failed = sum(1 for r in portal_results.values() if r["status"] == "failed")

    logger.info(
        "Scraping session ended: %d portals succeeded, %d failed. "
        "Found %d jobs, %d duplicates removed, %d unique jobs.",
        succeeded, failed, before_dedup, dupes_removed, len(all_jobs),
    )

    return all_jobs, portal_results

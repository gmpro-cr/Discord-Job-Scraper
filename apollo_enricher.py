"""
apollo_enricher.py - Apollo.io contact enrichment for job listings.
Searches for recruiter/HR contacts at companies using the Apollo People Search API.
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/search"

HR_TITLES = [
    "recruiter",
    "HR",
    "talent acquisition",
    "hiring manager",
    "people operations",
]


def search_company_contacts(company_name, api_key, max_results=3):
    """
    Search Apollo.io for recruiter/HR contacts at a given company.

    Returns list of dicts: [{name, email, phone, linkedin_url}, ...]
    """
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    payload = {
        "api_key": api_key,
        "q_organization_name": company_name,
        "person_titles": HR_TITLES,
        "page": 1,
        "per_page": max_results,
    }

    try:
        resp = requests.post(APOLLO_SEARCH_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning("Apollo API error for %s: %s", company_name, e)
        return []

    contacts = []
    for person in data.get("people", []):
        contact = {
            "name": person.get("name", ""),
            "email": person.get("email", ""),
            "phone": "",
            "linkedin_url": person.get("linkedin_url", ""),
        }
        # Phone can be in phone_numbers list or organization phone
        phone_numbers = person.get("phone_numbers") or []
        if phone_numbers:
            contact["phone"] = phone_numbers[0].get("sanitized_number", "")
        contacts.append(contact)

    return contacts


def enrich_jobs_with_contacts(jobs_needing_contacts, api_key):
    """
    Batch-enrich jobs with Apollo contact data.

    Args:
        jobs_needing_contacts: list of dicts with at least {job_id, company}
        api_key: Apollo.io API key

    Returns:
        dict mapping job_id -> {poster_name, poster_email, poster_phone, poster_linkedin}
    """
    if not api_key:
        logger.info("No Apollo API key configured, skipping contact enrichment")
        return {}

    results = {}
    # Cache by company name to avoid duplicate API calls
    company_cache = {}

    for job in jobs_needing_contacts:
        company = job.get("company", "").strip()
        job_id = job.get("job_id", "")

        if not company or not job_id:
            continue

        # Check cache first
        if company.lower() not in company_cache:
            contacts = search_company_contacts(company, api_key, max_results=3)
            company_cache[company.lower()] = contacts
            # Rate limit: 0.5s between API calls
            time.sleep(0.5)
        else:
            contacts = company_cache[company.lower()]

        if contacts:
            best = contacts[0]
            results[job_id] = {
                "poster_name": best.get("name", ""),
                "poster_email": best.get("email", ""),
                "poster_phone": best.get("phone", ""),
                "poster_linkedin": best.get("linkedin_url", ""),
            }

    logger.info(
        "Apollo enrichment: %d/%d jobs got contacts (%d unique companies queried)",
        len(results),
        len(jobs_needing_contacts),
        len(company_cache),
    )
    return results

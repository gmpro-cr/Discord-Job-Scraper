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

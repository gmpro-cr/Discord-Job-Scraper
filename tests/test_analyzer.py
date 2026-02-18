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

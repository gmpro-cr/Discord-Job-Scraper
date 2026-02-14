"""
analyzer.py - Job analysis and scoring using Ollama (mistral) with keyword-based fallback.
Scores each job 0-100 based on relevance to user preferences.
"""

import logging
import re
import json

logger = logging.getLogger(__name__)


# =============================================================================
# Keyword-based scoring (fallback when Ollama is unavailable)
# =============================================================================

REMOTE_KEYWORDS = {
    "remote": 10,
    "work from home": 10,
    "wfh": 10,
    "flexible": 5,
    "hybrid": 7,
    "work from anywhere": 10,
}

ONSITE_KEYWORDS = {"on-site": 0, "onsite": 0, "office": 0, "in-office": 0}

FINTECH_KEYWORDS = {
    "fintech": 15,
    "banking": 12,
    "credit": 10,
    "payments": 12,
    "lending": 12,
    "upi": 10,
    "neobank": 15,
    "financial services": 10,
    "nbfc": 12,
    "saas": 8,
    "insurance": 8,
    "wealth management": 8,
    "defi": 6,
    "blockchain": 5,
    "crypto": 5,
}

PM_KEYWORDS = {
    "product manager": 20,
    "product management": 18,
    "product lead": 18,
    "associate product manager": 20,
    "apm": 15,
    "product owner": 15,
    "product strategy": 15,
    "product roadmap": 12,
    "user stories": 8,
    "agile": 3,
    "scrum": 3,
    "sprint": 3,
    "stakeholder": 3,
}

STARTUP_KEYWORDS = [
    "startup", "early stage", "series a", "series b", "seed",
    "pre-seed", "founded in", "co-founder", "founding team",
    "fast-paced", "0 to 1", "greenfield",
]

CORPORATE_KEYWORDS = [
    "fortune 500", "mnc", "established", "global leader",
    "publicly traded", "enterprise", "large scale",
]

GROWTH_KEYWORDS = {
    "leadership": 3,
    "mentorship": 3,
    "career growth": 5,
    "learning": 2,
    "promotion": 3,
    "impact": 3,
    "ownership": 5,
    "autonomy": 3,
    "cross-functional": 3,
}

# Negative signals: roles that match "product manager" or "project manager" keywords
# but are clearly in unrelated domains
IRRELEVANT_KEYWORDS = [
    "sheet pile", "construction", "civil engineer", "mechanical engineer",
    "electrical engineer", "lab equipment", "laboratory", "chemical",
    "clinical", "pharmaceutical", "oil and gas", "oil & gas", "mining",
    "real estate agent", "property dealer", "interior design",
    "garment", "textile", "apparel", "food processing",
    "hvac", "plumbing", "welding", "carpentry",
]


def detect_remote_status(text):
    """Detect whether a job is remote, hybrid, or on-site."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["remote", "work from home", "wfh", "work from anywhere"]):
        return "remote"
    if "hybrid" in text_lower:
        return "hybrid"
    return "on-site"


def detect_company_type(text):
    """Detect whether a company is a startup or corporate."""
    text_lower = text.lower()
    startup_score = sum(1 for kw in STARTUP_KEYWORDS if kw in text_lower)
    corporate_score = sum(1 for kw in CORPORATE_KEYWORDS if kw in text_lower)
    if startup_score > corporate_score:
        return "startup"
    if corporate_score > startup_score:
        return "corporate"
    return "corporate"


def extract_skills(text, max_skills=8):
    """Extract key skills from job description text."""
    skill_patterns = [
        r"SQL", r"Python", r"Excel", r"Tableau", r"Power BI", r"Jira",
        r"Figma", r"Analytics", r"A/B testing", r"Data analysis",
        r"Product strategy", r"Roadmap", r"Agile", r"Scrum",
        r"Stakeholder management", r"User research", r"UX",
        r"API", r"REST", r"Microservices", r"AWS", r"GCP", r"Azure",
        r"Machine Learning", r"AI", r"NLP", r"React", r"JavaScript",
        r"TypeScript", r"Node\.?js", r"Java", r"Go", r"Kubernetes",
        r"Docker", r"CI/CD", r"Git", r"MongoDB", r"PostgreSQL",
        r"Redis", r"Kafka", r"Spark", r"Hadoop",
    ]
    found = []
    for pattern in skill_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            # Use the pattern as-is for display, clean up regex chars
            clean = pattern.replace(r"\.", ".").replace(r"\.?", "")
            if clean not in found:
                found.append(clean)
        if len(found) >= max_skills:
            break
    return found


def keyword_score(job, preferences):
    """
    Score a job 0-100 using keyword matching.
    This is the fallback scorer when Ollama is unavailable.

    Scoring breakdown:
      - Title match:     0-30  (exact match in role title is heavily rewarded)
      - Location match:  0-10
      - Remote bonus:    0-10
      - Industry match:  0-20  (fintech/banking/lending keywords)
      - PM keywords:     0-20  (product management terms in description)
      - Growth signals:  0-10
      - Penalty:         -20   (irrelevant domain detected)
    """
    score = 0
    role_lower = job.get("role", "").lower()
    text = " ".join([
        role_lower,
        job.get("company", ""),
        job.get("job_description", ""),
        job.get("location", ""),
        job.get("salary", "") or "",
    ]).lower()

    # --- Irrelevance penalty: bail early for obviously wrong domains ---
    for kw in IRRELEVANT_KEYWORDS:
        if kw in text:
            return max(0, score - 20)

    # Title match (0-30) — strongest signal
    user_titles = [t.lower().strip() for t in preferences.get("job_titles", [])]
    best_title_score = 0
    for title in user_titles:
        if title in role_lower:
            # Exact phrase match in role title
            best_title_score = max(best_title_score, 30)
        else:
            # Partial: check how many words from the preferred title appear in the role
            title_words = [w for w in title.split() if len(w) > 2]
            if title_words:
                matches = sum(1 for w in title_words if w in role_lower)
                ratio = matches / len(title_words)
                if ratio >= 0.8:
                    best_title_score = max(best_title_score, 22)
                elif ratio >= 0.5:
                    best_title_score = max(best_title_score, 12)
    score += best_title_score

    # Location match (0-10)
    user_locations = [loc.lower().strip() for loc in preferences.get("locations", [])]
    job_loc = job.get("location", "").lower()
    for loc in user_locations:
        if loc in job_loc or job_loc in loc:
            score += 10
            break

    # Remote work bonus (0-10)
    for kw, pts in REMOTE_KEYWORDS.items():
        if kw in text:
            score += pts
            break

    # Industry/domain relevance (0-20) — accumulate multiple matches
    industry_score = 0
    for kw, pts in FINTECH_KEYWORDS.items():
        if kw in text:
            industry_score += pts
    score += min(industry_score, 20)

    # PM keywords in description/title (0-20) — accumulate
    pm_score = 0
    for kw, pts in PM_KEYWORDS.items():
        if kw in text:
            pm_score += pts
    score += min(pm_score, 20)

    # Career growth (0-10)
    growth_score = 0
    for kw, pts in GROWTH_KEYWORDS.items():
        if kw in text:
            growth_score += pts
    score += min(growth_score, 10)

    return min(score, 100)


# =============================================================================
# Ollama-based scoring
# =============================================================================

def ollama_score(job, preferences, config):
    """
    Use Ollama (mistral) to score a job and generate analysis.
    Returns (score, analysis_text) or None if Ollama fails.
    """
    try:
        import ollama as ollama_client
    except ImportError:
        logger.warning("ollama package not installed, falling back to keyword scoring")
        return None

    model = config.get("scoring", {}).get("ollama_model", "mistral")
    timeout = config.get("scoring", {}).get("ollama_timeout", 60)

    prompt = f"""Analyze this job posting and score it 0-100 for a candidate with the following profile:
- Career transitioner from banking/financial services to Product Management
- Looking for roles: {', '.join(preferences.get('job_titles', ['Product Manager']))}
- Preferred locations: {', '.join(preferences.get('locations', ['Remote']))}
- Industries of interest: {', '.join(preferences.get('industries', ['Fintech']))}

Job Details:
- Title: {job.get('role', 'Unknown')}
- Company: {job.get('company', 'Unknown')}
- Location: {job.get('location', 'Unknown')}
- Salary: {job.get('salary', 'Not specified')}
- Description: {job.get('job_description', 'No description available')[:500]}

Score based on:
1. Role match with preferred titles (0-25 points)
2. Location match (0-15 points)
3. Remote/hybrid flexibility (0-15 points)
4. Domain relevance - banking/fintech background advantage (0-15 points)
5. Career growth potential for PM transition (0-15 points)
6. Company type suitability - startup vs corporate (0-15 points)

Respond ONLY with valid JSON in this exact format:
{{"score": <number 0-100>, "remote_status": "<remote|hybrid|on-site>", "company_type": "<startup|corporate>", "reason": "<one sentence explanation>"}}"""

    try:
        response = ollama_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1},
        )
        content = response["message"]["content"].strip()

        # Extract JSON from response
        json_match = re.search(r'\{[^}]+\}', content)
        if json_match:
            result = json.loads(json_match.group())
            return result
        else:
            logger.warning("Ollama returned non-JSON response: %s", content[:200])
            return None
    except ConnectionError:
        logger.warning("Ollama not running. Falling back to keyword scoring.")
        return None
    except Exception as e:
        logger.warning("Ollama scoring failed: %s. Falling back to keyword scoring.", e)
        return None


# =============================================================================
# Main analysis pipeline
# =============================================================================

def generate_application_email(job, preferences):
    """Generate a short personalized application email draft."""
    role = job.get("role", "the role")
    company = job.get("company", "your company")
    description = job.get("job_description", "")

    # Extract a few skills from the description
    skills = extract_skills(description, max_skills=3)
    skills_text = ", ".join(skills) if skills else "product strategy and data-driven decision making"

    email = (
        f"Dear Hiring Team at {company},\n\n"
        f"I am writing to express my interest in the {role} position. "
        f"With my background in banking and financial services, I bring a strong foundation in "
        f"analytical thinking, stakeholder management, and customer-centric problem solving. "
        f"My experience with {skills_text} aligns well with this role's requirements. "
        f"I am excited about the opportunity to leverage my domain expertise "
        f"to drive product impact at {company}.\n\n"
        f"I would welcome the chance to discuss how my skills can contribute to your team.\n\n"
        f"Best regards"
    )
    return email


def analyze_jobs(jobs, preferences, config, progress_callback=None):
    """
    Analyze and score all jobs. Uses Ollama if available, falls back to keywords.
    Returns list of jobs enriched with relevance_score, remote_status,
    company_type, skills, and application_email.
    """
    use_ollama = config.get("scoring", {}).get("use_ollama", True)
    min_score = config.get("scoring", {}).get("min_relevance_score", 65)
    ollama_available = False

    if use_ollama:
        try:
            import ollama as ollama_client
            # Check that both Ollama is running AND the model exists
            model = config.get("scoring", {}).get("ollama_model", "mistral")
            models = ollama_client.list()
            model_names = [m.model.split(":")[0] for m in models.models] if hasattr(models, "models") else []
            if model in model_names:
                ollama_available = True
                logger.info("Ollama is available with model '%s', using AI-based scoring", model)
            else:
                logger.warning(
                    "Ollama is running but model '%s' not found (available: %s). "
                    "Using keyword-based scoring. Run 'ollama pull %s' to enable AI scoring.",
                    model, model_names, model,
                )
        except Exception:
            logger.warning("Ollama is not available, using keyword-based scoring fallback")

    analyzed = []
    total = len(jobs)

    for i, job in enumerate(jobs):
        text = " ".join([
            job.get("role", ""),
            job.get("job_description", ""),
            job.get("location", ""),
        ])

        # Try Ollama first, fall back to keywords
        if ollama_available:
            result = ollama_score(job, preferences, config)
            if result:
                job["relevance_score"] = min(max(int(result.get("score", 0)), 0), 100)
                job["remote_status"] = result.get("remote_status", detect_remote_status(text))
                job["company_type"] = result.get("company_type", detect_company_type(text))
            else:
                # Ollama failed for this job, use keywords
                job["relevance_score"] = keyword_score(job, preferences)
                job["remote_status"] = detect_remote_status(text)
                job["company_type"] = detect_company_type(text)
        else:
            job["relevance_score"] = keyword_score(job, preferences)
            job["remote_status"] = detect_remote_status(text)
            job["company_type"] = detect_company_type(text)

        # Extract skills and generate email for all jobs
        job["skills"] = extract_skills(job.get("job_description", ""))
        job["application_email"] = generate_application_email(job, preferences)

        analyzed.append(job)

        if progress_callback:
            progress_callback(i + 1, total, job.get("role", ""), job["relevance_score"])

    # Filter by minimum score
    qualified = [j for j in analyzed if j["relevance_score"] >= min_score]

    # Sort by relevance score descending
    qualified.sort(key=lambda x: x["relevance_score"], reverse=True)

    logger.info(
        "Analysis complete: %d/%d jobs passed minimum score of %d",
        len(qualified), total, min_score,
    )

    return qualified, analyzed

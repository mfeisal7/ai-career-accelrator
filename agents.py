"""
AI helpers for the AI Career Accelerator app.
All Gemini calls, PDF extraction, and structured generation happen here.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Any, Optional

import google.generativeai as genai
import pypdf
from tenacity import retry, stop_after_attempt, wait_exponential


# ============================================================
# API KEY RESOLUTION
# ============================================================

def _get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key.strip()
    raise RuntimeError(
        "Gemini API key not found. Set 'GEMINI_API_KEY' as an environment variable."
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _get_gemini_model():
    api_key = _get_api_key()
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()
    try:
        return genai.GenerativeModel(model_name)
    except Exception as e:
        raise RuntimeError(
            f"Failed to load Gemini model '{model_name}'. "
            f"Set GEMINI_MODEL to a valid model. Original error: {e}"
        )


# ============================================================
# PDF EXTRACTION
# ============================================================

def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = pypdf.PdfReader(file_bytes)
    texts: List[str] = []
    for page in reader.pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:
            texts.append("")
    return "\n".join(texts).strip()


# ============================================================
# CORE GEMINI HELPERS
# ============================================================

def _safe_json_loads(text: str) -> Any:
    if not text:
        return None
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = text.replace("```", "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def gemini_generate_text(prompt: str, system_prompt: Optional[str] = None) -> str:
    model = _get_gemini_model()
    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
    resp = model.generate_content(full_prompt)
    return (getattr(resp, "text", None) or "").strip()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def gemini_generate_json(prompt: str, system_prompt: Optional[str] = None) -> Dict[str, Any]:
    model = _get_gemini_model()
    full_prompt = (
        f"{system_prompt}\n\nReturn ONLY valid JSON.\n\n{prompt}"
        if system_prompt
        else f"Return ONLY valid JSON.\n\n{prompt}"
    )
    resp = model.generate_content(full_prompt)
    text = (getattr(resp, "text", None) or "").strip()
    parsed = _safe_json_loads(text)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"items": parsed}
    return {"raw": text}


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_RECRUITER_PROMPT = """
You are an expert Kenyan recruiter, HR analyst, and career coach with 15+ years of experience
placing candidates at top Kenyan employers including Safaricom, KCB, Equity Bank, NGOs, UN
agencies, and high-growth startups. You deeply understand:
- What Kenyan hiring managers look for
- ATS (Applicant Tracking Systems) used in Kenya
- The Kenyan job market, salary ranges, and role expectations
- How to translate volunteer work, internships, and projects into compelling achievements
- STAR format for achievements (Situation, Task, Action, Result)

Be specific, quantified, practical, and grounded in the Kenyan job market context.
""".strip()


# ============================================================
# STEP 1: ANALYZE JOB DESCRIPTION
# ============================================================

def analyze_job(jd: str) -> Dict[str, Any]:
    """
    Analyze a job description and extract key requirements, keywords, and insights.
    Returns a structured dict passed to all other generation functions.
    """
    prompt = f"""
Analyze this job description for a Kenyan role.

JOB DESCRIPTION:
{jd}

Return JSON with these exact keys:
- job_title (string): The role title
- employer (string): Company/organisation name if mentioned, else "Not specified"
- sector (string): e.g. Banking, NGO, Tech, Telecoms, Healthcare, Government, etc.
- seniority (string): Entry-level / Mid-level / Senior / Manager / Director
- key_requirements (array of strings): Top 8-10 must-have skills, qualifications, or experiences
- ats_keywords (array of strings): 15-20 ATS keywords from the JD
- soft_skills (array of strings): 5-6 soft skills the employer values
- salary_estimate (string): Estimated KSh monthly range for this role in Kenya
- application_tips (array of strings): 3-5 specific tips to stand out for this role
- red_flags (array of strings): Any unusual requirements or things to clarify at interview
""".strip()

    return gemini_generate_json(prompt, system_prompt=SYSTEM_RECRUITER_PROMPT)


# ============================================================
# STEP 2: REWRITE RESUME / CV
# ============================================================

def rewrite_resume(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """
    Rewrite the candidate's CV tailored to the analyzed job.
    Returns markdown-formatted CV text.
    """
    keywords = ", ".join(job_analysis.get("ats_keywords", []))
    job_title = job_analysis.get("job_title", "the target role")
    sector = job_analysis.get("sector", "")
    seniority = job_analysis.get("seniority", "")

    prompt = f"""
Rewrite this Kenyan candidate's CV to maximise their chances at this role.

TARGET ROLE: {job_title} ({seniority}) in {sector}
ATS KEYWORDS TO WEAVE IN: {keywords}

CANDIDATE'S EXISTING CV / NOTES:
{resume_text}

Rewrite in professional Kenyan CV format as clean Markdown with these sections:
1. # [Full Name] (use "YOUR NAME" as placeholder if not clear)
2. Contact line: Phone | Email | Nairobi, Kenya | LinkedIn
3. ## Professional Summary (3-4 lines, punchy, keyword-rich, tailored to the role)
4. ## Key Skills (2-column list of 10-12 skills using ATS keywords)
5. ## Work Experience (reverse chronological; STAR-format bullet points with metrics)
6. ## Education (degree, institution, year; add relevant coursework if fresh grad)
7. ## Certifications & Training (if any)
8. ## Volunteer Work & Projects (reframe as professional achievements)

Rules:
- Every bullet starts with a strong action verb
- Include numbers/percentages wherever possible
- Keep to 1-2 pages of content
- Use ATS keywords naturally throughout
- Do NOT add placeholder text like [Insert here] — infer from context
""".strip()

    return gemini_generate_text(prompt, system_prompt=SYSTEM_RECRUITER_PROMPT)


# ============================================================
# STEP 3: GENERATE COVER LETTER
# ============================================================

def generate_cover_letter(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """
    Generate a tailored cover letter for the analyzed role.
    Returns markdown-formatted cover letter.
    """
    job_title = job_analysis.get("job_title", "the position")
    employer = job_analysis.get("employer", "your organisation")
    sector = job_analysis.get("sector", "")
    key_reqs = "\n".join(f"- {r}" for r in job_analysis.get("key_requirements", [])[:5])

    prompt = f"""
Write a compelling, tailored cover letter for a Kenyan candidate applying for:
ROLE: {job_title}
EMPLOYER: {employer}
SECTOR: {sector}

KEY REQUIREMENTS TO ADDRESS:
{key_reqs}

CANDIDATE BACKGROUND:
{resume_text}

Write a professional cover letter in markdown:
- Today's date (write [Date])
- Dear [Hiring Manager Name / Hiring Manager],
- Opening paragraph: Hook + why this specific role/employer (not generic)
- 2 body paragraphs: Match top 2-3 requirements with specific examples + metrics
- Closing paragraph: Confidence + call to action + enthusiasm
- Yours sincerely, [Name]

Tone: Professional but warm. Kenyan context where natural.
Length: 300-380 words.
Do NOT start with "I am writing to express my interest..." — start strong and specific.
""".strip()

    return gemini_generate_text(prompt, system_prompt=SYSTEM_RECRUITER_PROMPT)


# ============================================================
# STEP 4: GENERATE EMAIL STRATEGY
# ============================================================

def generate_emails(job_analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Generate 4 strategic job-search emails.
    Returns a list of email dicts with subject, body, timing, and purpose.
    """
    job_title = job_analysis.get("job_title", "the target role")
    sector = job_analysis.get("sector", "")
    employer = job_analysis.get("employer", "target company")

    prompt = f"""
Generate 4 strategic job-search emails for a Kenyan candidate targeting:
ROLE: {job_title} | SECTOR: {sector} | EMPLOYER: {employer}

Return a JSON array of exactly 4 email objects, each with keys:
- purpose (string)
- timing (string)
- subject (string)
- body (string)

The 4 emails:
1. Application follow-up (7 days after applying with no response)
2. Networking outreach (cold email to someone at the company)
3. Post-interview thank you (within 24 hours of interview)
4. Rejection recovery (graceful response, keeping the door open)

Each email under 200 words. Professional but warm Kenyan tone.
""".strip()

    result = gemini_generate_json(prompt, system_prompt=SYSTEM_RECRUITER_PROMPT)

    if isinstance(result, dict):
        for key in ("items", "emails", "email_strategy"):
            if key in result and isinstance(result[key], list):
                return result[key]
        raw = result.get("raw", "")
        if raw:
            parsed = _safe_json_loads(raw)
            if isinstance(parsed, list):
                return parsed
        return []
    return result if isinstance(result, list) else []


# ============================================================
# LINKEDIN OPTIMIZATION (Pro & Executive tiers)
# ============================================================

def generate_linkedin_optimization(candidate_text: str, job_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Optimize LinkedIn profile for the target role."""
    job_title = job_analysis.get("job_title", "target role")
    keywords = ", ".join(job_analysis.get("ats_keywords", [])[:10])

    prompt = f"""
Optimize this Kenyan candidate's LinkedIn profile for their target role: {job_title}
Keywords to incorporate: {keywords}

CANDIDATE PROFILE:
{candidate_text}

Return JSON with:
- headline (string): Optimized LinkedIn headline (max 220 chars, keyword-rich)
- about (string): Compelling About section (~300 words, first-person, ends with CTA)
- experience_bullets (array of strings): 5-6 rewritten bullet points for top role (STAR + metrics)
- featured_skills (array of strings): Top 10 skills to add/prioritize
- connection_message (string): 300-char connection request to send to recruiters
- inmail_template (string): Cold InMail to a hiring manager (~150 words)
""".strip()

    return gemini_generate_json(prompt, system_prompt=SYSTEM_RECRUITER_PROMPT)


# ============================================================
# INTERVIEW PREPARATION (Pro & Executive tiers)
# ============================================================

def generate_interview_prep(resume_text: str, job_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Generate comprehensive interview preparation guide."""
    job_title = job_analysis.get("job_title", "the role")
    sector = job_analysis.get("sector", "")
    key_reqs = job_analysis.get("key_requirements", [])

    prompt = f"""
Create an interview prep guide for a Kenyan candidate interviewing for:
ROLE: {job_title} | SECTOR: {sector}
KEY REQUIREMENTS: {', '.join(key_reqs[:6])}

CANDIDATE BACKGROUND:
{resume_text}

Return JSON with:
- common_questions (array of 5 objects: question, strong_answer, tip)
- technical_questions (array of 4 objects: question, strong_answer, tip) tailored to role
- behavioural_questions (array of 4 objects: question, strong_answer, tip) STAR format
- questions_to_ask_employer (array of 5 strings): Smart questions for end of interview
- salary_negotiation_script (string): How to negotiate salary in Kenya for this role
- first_impression_tips (array of 5 strings): Before/during the Kenyan interview
""".strip()

    return gemini_generate_json(prompt, system_prompt=SYSTEM_RECRUITER_PROMPT)


# ============================================================
# GAP ANALYSIS (all tiers)
# ============================================================

def generate_gap_analysis(resume_text: str, job_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Identify gaps between the candidate's profile and job requirements."""
    prompt = f"""
Perform a gap analysis for a Kenyan candidate vs. this job.

JOB REQUIREMENTS: {json.dumps(job_analysis.get('key_requirements', []))}
ATS KEYWORDS NEEDED: {json.dumps(job_analysis.get('ats_keywords', []))}

CANDIDATE PROFILE:
{resume_text}

Return JSON with:
- match_score (integer 0-100): Overall match percentage
- strengths (array of strings): Top 4-5 things that fit the role
- gaps (array of strings): Missing skills, qualifications, or experience
- quick_wins (array of strings): Things to add to CV/profile NOW
- thirty_day_plan (array of strings): Specific actions in 30 days to close gaps
- keywords_missing (array of strings): ATS keywords not in their profile
""".strip()

    return gemini_generate_json(prompt, system_prompt=SYSTEM_RECRUITER_PROMPT)


# ============================================================
# LEGACY COMPOSITE (backwards compatibility)
# ============================================================

def build_career_pack(job_description: str, candidate_text: str) -> Dict[str, Any]:
    """Full career pack in one call."""
    job_analysis = analyze_job(job_description)
    return {
        "ats_cv": rewrite_resume(candidate_text, job_analysis),
        "cover_letter": generate_cover_letter(candidate_text, job_analysis),
        "interview_prep": generate_interview_prep(candidate_text, job_analysis).get("common_questions", []),
        "gap_analysis": generate_gap_analysis(candidate_text, job_analysis),
        "job_analysis": job_analysis,
    }

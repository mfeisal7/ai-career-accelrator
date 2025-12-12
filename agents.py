"""
AI helpers for the AI Career Accelerator app.
All Gemini calls, PDF extraction, and structured generation happen here.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Any, Optional

import streamlit as st
import google.generativeai as genai
import pypdf
from tenacity import retry, stop_after_attempt, wait_exponential


# ============================================================
# Gemini Configuration
# ============================================================

def _secrets_file_exists() -> bool:
    """
    Return True if a .streamlit/secrets.toml file physically exists.

    This prevents Streamlit from printing the scary red warning:
    'No secrets found. Valid paths for a secrets.toml file...'
    because we never touch st.secrets unless a file exists.
    """
    candidates = [
        os.path.join(os.getcwd(), ".streamlit", "secrets.toml"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".streamlit", "secrets.toml"),
    ]

    # Also check one directory above (sometimes agents.py is in a subfolder)
    parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(parent, ".streamlit", "secrets.toml"))

    return any(os.path.exists(p) for p in candidates)


def _get_api_key() -> str:
    """
    Resolve the Gemini API key.

    SERVER MODE PRIORITY:
    1. Environment Variable (Railway / Cloud Deployment)
    2. Streamlit Secrets (Local Development via .streamlit/secrets.toml) — ONLY if file exists
    """
    # 1) Environment variable (Railway / production)
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key.strip()

    # 2) Local secrets.toml — only if it physically exists
    if _secrets_file_exists():
        try:
            key = st.secrets.get("GEMINI_API_KEY")
            if key:
                return str(key).strip()
        except Exception:
            # If secrets parsing fails, fall through to error below
            pass

    raise RuntimeError(
        "Gemini API key not found. "
        "Set 'GEMINI_API_KEY' as an environment variable (on Railway) "
        "or add it to .streamlit/secrets.toml for local development."
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _get_gemini_model():
    """
    Configure the Gemini client and return a GenerativeModel instance.
    """
    api_key = _get_api_key()
    genai.configure(api_key=api_key)

    # Default model – override via GEMINI_MODEL if needed
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()

    try:
        return genai.GenerativeModel(model_name)
    except Exception as e:
        raise RuntimeError(
            f"Failed to load Gemini model '{model_name}'. "
            f"Set GEMINI_MODEL to a valid model. "
            f"Original error: {e}"
        )


# ============================================================
# Response Helpers
# ============================================================

def _extract_json_block(raw: str) -> str:
    """
    Try to pull out a JSON object/array from a possibly messy LLM response.
    Supports fenced code blocks and bare { ... } / [ ... ].
    """
    if not raw:
        return raw

    # ```json ... ```
    fenced = re.search(r"```json(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    # ``` ... ``` (any language)
    fenced_any = re.search(r"```(.*?)```", raw, flags=re.DOTALL)
    if fenced_any:
        candidate = fenced_any.group(1).strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate

    # First {...} block
    brace = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if brace:
        return brace.group(0)

    # First [...] block
    array = re.search(r"\[.*\]", raw, flags=re.DOTALL)
    if array:
        return array.group(0)

    # Fallback: return as-is
    return raw.strip()


def _safe_json_loads(raw: Optional[str]) -> Any:
    """
    Attempt to parse JSON from a possibly noisy model response.
    Raises ValueError if nothing usable is found.
    """
    if not raw:
        raise ValueError("Empty response from model")

    # First attempt: direct JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        candidate = _extract_json_block(raw)
        return json.loads(candidate)


def _get_response_text(response: Any, label: str) -> str:
    """
    Safely extract text from a google-generativeai response.
    """
    candidates = getattr(response, "candidates", None)
    if candidates:
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if parts:
                texts: List[str] = []
                for p in parts:
                    if hasattr(p, "text") and p.text:
                        texts.append(p.text)
                    elif isinstance(p, dict) and p.get("text"):
                        texts.append(str(p["text"]))
                if texts:
                    return "\n".join(texts).strip()

        first = candidates[0]
        finish_reason = getattr(first, "finish_reason", None)
        raise RuntimeError(
            f"Gemini returned no text for {label} (finish_reason={finish_reason})."
        )

    txt = getattr(response, "text", None)
    if txt:
        return str(txt).strip()

    raise RuntimeError(
        f"Gemini returned an empty response object for {label} (no candidates, no text)."
    )


# ============================================================
# PDF Extraction
# ============================================================

def extract_text_from_pdf(file) -> str:
    """
    Extract text from an uploaded PDF (Streamlit's UploadedFile or a file-like object).
    Returns the concatenated text of all pages, separated by blank lines.
    """
    if file is None:
        return ""

    try:
        reader = pypdf.PdfReader(file)
        texts: List[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                texts.append(text.strip())
        return "\n\n".join(texts)
    except Exception as e:
        raise RuntimeError(f"Failed to read PDF: {e}")


# ============================================================
# Job Analysis
# ============================================================

def analyze_job(job_description: str) -> Dict[str, Any]:
    """
    Call Gemini to analyze a job description into a structured JSON schema.
    """
    if not job_description.strip():
        raise ValueError("Job description is empty")

    model = _get_gemini_model()

    prompt = f"""
You are an expert Kenyan recruiter and HR analyst.

Analyze the following job description and return ONLY a valid JSON object.
No markdown. No explanations. JSON only.

JOB DESCRIPTION:
\"\"\"{job_description}\"\"\"

Return JSON with exactly these keys:

{{
  "role_name": "string - best guess at job title",
  "company_name": "string - employer name or 'Confidential'",
  "seniority": "Entry-level | Mid-level | Senior | Internship | Graduate Trainee",
  "summary": "2-4 sentence plain English summary of the role (Kenyan context)",
  "hard_skills": ["Python", "Driving", "Excel", ...],
  "soft_skills": ["Leadership", "Communication", ...],
  "keywords": ["Agile", "Stakeholder Management", ...],
  "inferred_profile": "Early-career Analyst | Driver | Nurse | Teacher | etc."
}}

Rules:
- Must be valid JSON (double quotes, no trailing commas).
- If unsure, make a reasonable Kenyan-context assumption.
"""

    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.25,
            max_output_tokens=1024,
        ),
    )

    raw = _get_response_text(response, "job analysis")
    return _safe_json_loads(raw)


# ============================================================
# Resume Rewrite – PREMIUM CV GENERATOR
# ============================================================

def rewrite_resume(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """
    Generate a premium, professional Kenyan CV tailored to the job.
    Output is ATS-friendly Markdown with strong achievements, metrics,
    and fully written sections (no placeholders).
    """
    if not (resume_text or "").strip():
        raise ValueError("Resume text is empty")

    model = _get_gemini_model()

    role = (job_analysis.get("role_name") or "").strip() or "the role"
    company = (job_analysis.get("company_name") or "").strip() or "the company"
    seniority = (job_analysis.get("seniority") or "").strip() or "Not specified"
    hard_skills = job_analysis.get("hard_skills", []) or []
    soft_skills = job_analysis.get("soft_skills", []) or []
    keywords = job_analysis.get("keywords", []) or []

    role_lower = role.lower()

    role_specific_rules: List[str] = []

    if any(word in role_lower for word in ["driver", "chauffeur", "dispatch", "rider"]):
        role_specific_rules += [
            "- Highlight safety, defensive driving, route planning, timekeeping, customer service, and basic vehicle maintenance.",
            "- Include licence class/category and any relevant training (defensive driving, first aid).",
            "- Show reliability: accident-free record, on-time delivery/transport, daily checks, logbooks.",
        ]

    if any(word in role_lower for word in ["teacher", "tutor", "lecturer"]):
        role_specific_rules += [
            "- Highlight lesson planning, classroom management, learner outcomes, CBC familiarity (if relevant), assessments and reporting.",
            "- Include co-curricular involvement, mentoring, and measurable improvements (results, attendance, engagement).",
        ]

    if any(word in role_lower for word in ["nurse", "clinical", "health", "medical"]):
        role_specific_rules += [
            "- Highlight patient safety, infection prevention, triage, documentation, teamwork with clinicians, and high-volume shift handling.",
            "- Include registrations/licensing if mentioned; use realistic clinical responsibilities and outcomes.",
        ]

    if any(word in role_lower for word in ["sales", "marketing", "business development", "account"]):
        role_specific_rules += [
            "- Focus on targets, revenue, pipeline, conversions, client acquisition/retention, and market expansion.",
            "- Always include numbers where reasonable: % growth, KES amounts, number of accounts, leads handled.",
        ]

    if any(word in role_lower for word in ["customer service", "call centre", "call center", "reception"]):
        role_specific_rules += [
            "- Highlight service quality, issue resolution, professionalism, and measurable workload (calls/day, tickets/week, turnaround time).",
            "- Include tools like CRM, email support, MS Office if relevant.",
        ]

    role_rules_block = ""
    if role_specific_rules:
        role_rules_block = "\nROLE-SPECIFIC GUIDELINES:\n" + "\n".join(role_specific_rules)

    prompt = f"""
You are a senior Kenyan CV writer who produces high-conversion, interview-winning CVs.
Write like a real paid consultant.

TARGET ROLE:
- Role: {role}
- Company: {company}
- Seniority: {seniority}

JOB ANALYSIS:
{json.dumps(job_analysis, indent=2)}

CANDIDATE RESUME (raw text):
<<<RESUME_START>>>
{resume_text}
<<<RESUME_END>>>

===========================
OUTPUT FORMAT (Markdown)
===========================

## FULL NAME
Phone | Email | Location (Kenya) | LinkedIn (if provided)

## PROFESSIONAL SUMMARY
3–5 strong sentences. No fluff. Specific to the target role.

## CORE COMPETENCIES
6–14 bullets combining:
- Hard skills: {hard_skills}
- Soft skills: {soft_skills}
- Keywords: {keywords}

## PROFESSIONAL EXPERIENCE
For each role:
**Job Title** | Company | Location | Dates
- Bullets MUST be achievement-based: Action → Result → Metric
- Use realistic metrics if missing (volume, frequency, time saved, quality, scale)
- 3–6 bullets per role

If the candidate has little/no formal work experience:
Create a section named **RELEVANT EXPERIENCE** and use internships, attachments,
volunteering, gigs, community work, driving work, teaching practice, clinical placement,
projects, etc.

## EDUCATION
- Qualification | Institution | Year (or Dates Not Provided)

## CERTIFICATIONS / LICENSES / TRAINING (if applicable)

## PROJECTS / VOLUNTEERING (if applicable)

## LANGUAGES
- English (Fluent)
- Kiswahili (Fluent)
- Others (if any)

===========================
STRICT RULES
===========================
- Never output placeholders like: [Add], [Replace], (Insert), "To be added".
- Never instruct the user to add details later.
- Use professional Kenyan corporate tone.
- Ensure output is ATS-friendly (simple text, no tables, no emojis).
{role_rules_block}

Now write the final CV in Markdown.
"""

    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.35,
            max_output_tokens=2500,
        ),
    )

    text = _get_response_text(response, "resume rewrite")
    if not text:
        raise RuntimeError("Gemini returned an empty response for resume rewrite")

    return text.strip()


# ============================================================
# Cover Letter & Emails
# ============================================================

def generate_cover_letter(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """
    Generate a tailored cover letter (plain text / Markdown).
    """
    model = _get_gemini_model()
    role = job_analysis.get("role_name", "this position")
    company = job_analysis.get("company_name", "your esteemed organisation")

    prompt = f"""
Write a professional Kenyan cover letter (4–6 short paragraphs) for the role below.

Role: {role}
Company: {company}
Job Analysis: {json.dumps(job_analysis, indent=2)}

Candidate resume:
<<<RESUME_START>>>
{resume_text}
<<<RESUME_END>>>

Rules:
- Confident but respectful tone
- Mention 2–3 relevant strengths/achievements (use numbers if possible)
- End with a strong call to action
- Return plain text or light Markdown only (no code fences, no JSON)
"""

    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.55,
            max_output_tokens=1024,
        ),
    )

    text = _get_response_text(response, "cover letter")
    if not text:
        raise RuntimeError("Gemini returned an empty response for cover letter")
    return text.strip()


def generate_emails(job_analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Generate a 3-email follow-up sequence as a JSON array.
    """
    model = _get_gemini_model()
    role = job_analysis.get("role_name", "the position")
    company = job_analysis.get("company_name", "your organisation")

    prompt = f"""
Create a 3-email follow-up sequence for a candidate who applied to {role} at {company}.

Return ONLY a JSON array with objects containing:
- "label"
- "subject"
- "body"

Rules:
- Very professional Kenyan corporate tone
- Clear, polite, short, and confident
- JSON only (no markdown)

Example:
[
  {{
    "label": "Day 3 Follow-Up",
    "subject": "Following up on my application for {role}",
    "body": "Dear Hiring Manager..."
  }}
]
"""

    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.45,
            max_output_tokens=1024,
        ),
    )

    raw = _get_response_text(response, "follow-up emails")
    if not raw:
        return []

    try:
        data = _safe_json_loads(raw)
        if not isinstance(data, list):
            return []

        normalized: List[Dict[str, str]] = []
        for item in data[:3]:
            if not isinstance(item, dict):
                continue
            subject = (item.get("subject") or "").strip()
            body = (item.get("body") or "").strip()
            if not subject or not body:
                continue
            normalized.append(
                {
                    "label": (item.get("label") or "Email").strip(),
                    "subject": subject,
                    "body": body,
                }
            )
        return normalized
    except Exception:
        return []

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

def _get_api_key() -> str:
    """
    Resolve the Gemini API key from (in order):
    1. st.secrets["GEMINI_API_KEY"]
    2. env var GEMINI_API_KEY
    3. st.session_state["GEMINI_API_KEY"] (for manual input if you wire it in the UI)

    Raises a RuntimeError with a clear message if not found.
    """
    key: Optional[str] = None

    # 1) Streamlit secrets
    try:
        if "GEMINI_API_KEY" in st.secrets:
            candidate = st.secrets["GEMINI_API_KEY"]
            if candidate:
                key = candidate
    except Exception:
        # If secrets not configured, just fall through
        pass

    # 2) Environment variable
    if not key:
        env_key = os.getenv("GEMINI_API_KEY")
        if env_key:
            key = env_key

    # 3) Session state (optional, for manual-input UI)
    if not key:
        session_key = st.session_state.get("GEMINI_API_KEY")
        if session_key:
            key = session_key

    if not key:
        raise RuntimeError(
            "Gemini API key is not configured. "
            "Set GEMINI_API_KEY in Streamlit secrets, environment, "
            "or st.session_state['GEMINI_API_KEY']."
        )

    return key


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def _get_gemini_model():
    """
    Configure the Gemini client and return a GenerativeModel instance.

    We hard-code a model name that works with AI Studio keys.
    If you want to change it, edit `model_name` below.
    """
    api_key = _get_api_key()
    genai.configure(api_key=api_key)

    # You can change this if your key supports a different model
    # Common options: "gemini-1.5-flash-8b", "gemini-1.5-pro", "gemini-1.0-pro"
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    try:
        return genai.GenerativeModel(model_name)
    except Exception as e:
        raise RuntimeError(
            f"Failed to load Gemini model '{model_name}'. "
            f"Set GEMINI_MODEL to a valid model or adjust agents._get_gemini_model(). "
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
        # Second attempt: extract JSON-looking block from raw
        candidate = _extract_json_block(raw)
        return json.loads(candidate)


def _get_response_text(response: Any, label: str) -> str:
    """
    Safely extract text from a google-generativeai response.

    Avoids using response.text directly, which raises:
    'Invalid operation: The response.text quick accessor requires the response
    to contain a valid Part, but none were returned.'
    """
    # Prefer candidates → content.parts → text
    candidates = getattr(response, "candidates", None)
    if candidates:
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if parts:
                texts: List[str] = []
                for p in parts:
                    # Newer SDK: Part objects with .text
                    if hasattr(p, "text") and p.text:
                        texts.append(p.text)
                    # Just in case it's dict-like
                    elif isinstance(p, dict) and p.get("text"):
                        texts.append(str(p["text"]))
                if texts:
                    return "\n".join(texts).strip()

        # No parts with text; inspect finish_reason for better error
        first = candidates[0]
        finish_reason = getattr(first, "finish_reason", None)
        raise RuntimeError(
            f"Gemini returned no text for {label} (finish_reason={finish_reason}). "
            "Try again with a more detailed input or slightly different wording."
        )

    # Fallback: some responses still expose .text directly
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
You are an expert Kenyan recruiter working for top employers like Safaricom, KCB, Equity Bank, Deloitte, PwC, UN, Microsoft ADC, and Twiga Foods.

Analyze the following job description and return ONLY a valid JSON object with no markdown, no explanation.

JOB DESCRIPTION:
\"\"\"{job_description}\"\"\"

Return JSON with exactly these keys:

{{
  "role_name": "string - best guess at job title",
  "company_name": "string - employer name or 'Confidential'",
  "seniority": "Entry-level | Mid-level | Senior | Internship | Graduate Trainee",
  "summary": "2-3 sentence plain English summary of the role",
  "hard_skills": ["Python", "SQL", "Excel", ...],
  "soft_skills": ["Leadership", "Communication", ...],
  "keywords": ["Agile", "Stakeholder Management", ...],
  "inferred_profile": "Early-career Analyst | Mid-level Manager | Senior Engineer | etc."
}}

Return only the JSON. No backticks, no explanation.
"""

    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.3,
            max_output_tokens=1024,
        ),
    )

    raw = _get_response_text(response, "job analysis")
    return _safe_json_loads(raw)


# ============================================================
# Resume Rewrite
# ============================================================

def rewrite_resume(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """
    Rewrite the user's resume as ATS-friendly Markdown tailored to the job.
    """
    model = _get_gemini_model()

    role = job_analysis.get("role_name", "this role")

    prompt = f"""
You are Kenya's top resume writer for candidates applying to {role} roles.

Using the user's raw resume and the job analysis below, rewrite their resume in clean, ATS-friendly Markdown.

JOB ANALYSIS:
{json.dumps(job_analysis, indent=2)}

USER RESUME (raw text):
\"\"\"{resume_text}\"\"\"


RULES:
- Use Kenyan corporate language and realistic achievements.
- Add metrics where possible (e.g., "Increased sales by 43%", "Managed team of 12").
- If dates are missing, write "(Dates Not Provided)".
- Assume early-career unless seniority says otherwise.

OUTPUT FORMAT (Markdown only, no JSON):

## SUMMARY
3–5 sentences about experience and value.

## EXPERIENCE
**Job Title** | Company | Location | Dates
- Action → Result → Metric
- ...

## EDUCATION
- Degree | Institution | Year

## KEY SKILLS
- Technical: Python, SQL, Power BI
- Business: Financial Modeling, Strategy
- Tools: Excel, Tableau

## CORE COMPETENCIES
- Stakeholder Management
- Data-Driven Decision Making
- ...

Use only simple Markdown. No tables, no emojis, no images.
"""

    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.5,
            max_output_tokens=2048,
        ),
    )

    text = _get_response_text(response, "resume rewrite")
    if not text:
        raise RuntimeError("Gemini returned an empty response for resume rewrite")
    return text


# ============================================================
# Cover Letter & Emails
# ============================================================

def generate_cover_letter(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """
    Generate a tailored cover letter (plain text / Markdown).
    """
    model = _get_gemini_model()
    role = job_analysis.get("role_name", "this position")
    company = job_analysis.get("company_name", "your esteemed organization")

    prompt = f"""
Write a professional, warm Kenyan cover letter (4–6 short paragraphs) for the role below.

Role: {role}
Company: {company}
Analysis: {json.dumps(job_analysis)}

Candidate resume:
\"\"\"{resume_text}\"\"\"


Tone: Confident but respectful. Reference 2–3 achievements with metrics. End with a strong call to action.
Return plain text or light Markdown only (no JSON, no code fences).
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
    return text


def generate_emails(job_analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Generate a 3-email follow-up sequence as a JSON array.

    Returns a list of dicts with keys: label, subject, body.
    If parsing fails, returns an empty list.
    """
    model = _get_gemini_model()
    role = job_analysis.get("role_name", "the position")
    company = job_analysis.get("company_name", "your organization")

    prompt = f"""
Create a 3-email follow-up sequence for a candidate who applied to {role} at {company}.

Return ONLY a JSON array with objects containing:
- "label": e.g. "3 days after application"
- "subject":
- "body": full polite Kenyan corporate email

Example:
[
  {{
    "label": "Day 3 Follow-Up",
    "subject": "Following up on my application for {role}",
    "body": "Dear Hiring Manager..."
  }}
]

No extra text. No explanation. JSON only.
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
        # If parsing fails, don't crash the app – just return no emails.
        return []
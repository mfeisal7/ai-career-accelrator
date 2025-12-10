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
    Resolve the Gemini API key.

    SERVER MODE PRIORITY:
    1. Environment Variable (Railway / Cloud Deployment)
    2. Streamlit Secrets (Local Development via .streamlit/secrets.toml)
    """
    # 1. Try Environment Variable first (server mode)
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key

    # 2. Fallback: Try Streamlit Secrets (local dev)
    try:
        key = st.secrets["GEMINI_API_KEY"]
        if key:
            return key
    except (FileNotFoundError, KeyError):
        # No secrets configured locally
        pass

    # 3. If neither found, raise error
    raise RuntimeError(
        "Gemini API key not found. "
        "Set 'GEMINI_API_KEY' as an environment variable (on Railway) "
        "or in .streamlit/secrets.toml for local development."
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def _get_gemini_model():
    """
    Configure the Gemini client and return a GenerativeModel instance.
    """
    api_key = _get_api_key()
    genai.configure(api_key=api_key)

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

Analyze the following job description and return ONLY a valid JSON object.

JOB DESCRIPTION:
\"\"\"{job_description}\"\"\"


Return JSON:
{{
  "role_name": "",
  "company_name": "",
  "seniority": "",
  "summary": "",
  "hard_skills": [],
  "soft_skills": [],
  "keywords": [],
  "inferred_profile": ""
}}
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
Rewrite the user's resume in ATS-friendly Markdown based on this role:

ROLE: {role}

JOB ANALYSIS:
{json.dumps(job_analysis, indent=2)}

RESUME:
\"\"\"{resume_text}\"\"\"
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
        raise RuntimeError("Gemini returned empty resume rewrite")
    return text


# ============================================================
# Cover Letter
# ============================================================

def generate_cover_letter(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """
    Generate a tailored cover letter (plain text / Markdown).
    """
    model = _get_gemini_model()
    role = job_analysis.get("role_name", "this position")
    company = job_analysis.get("company_name", "your organization")

    prompt = f"""
Write a Kenyan-style cover letter for:

Role: {role}
Company: {company}

Resume:
\"\"\"{resume_text}\"\"\"

Keep tone professional and warm. No JSON.
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
        raise RuntimeError("Gemini returned empty cover letter")
    return text


# ============================================================
# Follow-Up Emails
# ============================================================

def generate_emails(job_analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Generate a 3-email follow-up sequence as a JSON array.

    Returns a list of dicts with keys: label, subject, body.
    If parsing fails, returns an empty list.
    """
    model = _get_gemini_model()
    role = job_analysis.get("role_name", "the position")
    company = job_analysis.get("company_name", "the company")

    prompt = f"""
Write 3 follow-up emails for the role {role} at {company}.
Return ONLY a JSON array.
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
            if isinstance(item, dict):
                normalized.append({
                    "label": item.get("label", "").strip(),
                    "subject": item.get("subject", "").strip(),
                    "body": item.get("body", "").strip(),
                })
        return [e for e in normalized if e["subject"] and e["body"]]
    except Exception:
        return []

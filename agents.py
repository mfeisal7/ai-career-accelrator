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
# API KEY RESOLUTION (ENV ONLY — NO STREAMLIT SECRETS)
# ============================================================

def _get_api_key() -> str:
    """Resolve the Gemini API key (environment variables only).

    This avoids Streamlit's red 'No secrets found' warning on hosts like Railway
    by never touching Streamlit secrets.
    """
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key.strip()

    raise RuntimeError(
        "Gemini API key not found. Set 'GEMINI_API_KEY' as an environment variable."
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
# PDF EXTRACTION
# ============================================================

def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract text from a PDF file given its bytes.
    """
    reader = pypdf.PdfReader(file_bytes)
    texts: List[str] = []
    for page in reader.pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:
            texts.append("")
    return "\n".join(texts).strip()


# ============================================================
# PROMPTS
# ============================================================

SYSTEM_RECRUITER_PROMPT = """
You are an expert Kenyan recruiter and HR analyst.

Analyze the following job description and candidate profile, and produce:
1) A concise ATS-optimized CV tailored for the job.
2) A tailored cover letter.
3) A short list of interview prep questions and strong answers.
4) A gap analysis: missing skills/keywords + suggestions to improve.

Use Kenyan market context where relevant.
Be specific, quantified, and practical.
""".strip()


# ============================================================
# GEMINI CALL HELPERS
# ============================================================

def _safe_json_loads(text: str) -> Any:
    """
    Best-effort JSON parse from model output.
    Handles common wrapping like ```json ... ``` blocks.
    """
    if not text:
        return None

    # Remove code fences
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = text.replace("```", "").strip()

    # Try direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try to extract first JSON object/array in text
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None

    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def gemini_generate_text(prompt: str, system_prompt: Optional[str] = None) -> str:
    """
    Generate plain text from Gemini.
    """
    model = _get_gemini_model()

    if system_prompt:
        full_prompt = f"{system_prompt}\n\n{prompt}"
    else:
        full_prompt = prompt

    resp = model.generate_content(full_prompt)
    return (getattr(resp, "text", None) or "").strip()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def gemini_generate_json(prompt: str, system_prompt: Optional[str] = None) -> Dict[str, Any]:
    """
    Generate JSON output from Gemini (best effort).
    """
    model = _get_gemini_model()

    if system_prompt:
        full_prompt = f"{system_prompt}\n\nReturn ONLY valid JSON.\n\n{prompt}"
    else:
        full_prompt = f"Return ONLY valid JSON.\n\n{prompt}"

    resp = model.generate_content(full_prompt)
    text = (getattr(resp, "text", None) or "").strip()

    parsed = _safe_json_loads(text)
    if isinstance(parsed, dict):
        return parsed

    # fallback minimal structure
    return {"raw": text}


# ============================================================
# HIGH-LEVEL APP FUNCTIONS
# ============================================================

def build_career_pack(
    job_description: str,
    candidate_text: str,
) -> Dict[str, Any]:
    """
    Produce a structured career pack:
    - ATS CV
    - Cover letter
    - Interview Q&A
    - Gap analysis
    """
    prompt = f"""
JOB DESCRIPTION:
{job_description}

CANDIDATE PROFILE (CV / notes):
{candidate_text}

Return JSON with keys:
- ats_cv (string)
- cover_letter (string)
- interview_prep (array of objects: question, strong_answer)
- gap_analysis (object: missing_keywords (array), suggestions (array))
""".strip()

    return gemini_generate_json(prompt, system_prompt=SYSTEM_RECRUITER_PROMPT)


def generate_linkedin_optimization(candidate_text: str) -> Dict[str, Any]:
    """
    Suggest LinkedIn improvements from candidate profile.
    """
    prompt = f"""
CANDIDATE PROFILE (CV / notes):
{candidate_text}

Return JSON with keys:
- headline (string)
- about (string)
- experience_bullets (array of strings)
- skills (array of strings)
- keywords (array of strings)
- networking_message (string)
""".strip()

    return gemini_generate_json(prompt, system_prompt=SYSTEM_RECRUITER_PROMPT)


def generate_interview_answers(job_description: str, candidate_text: str) -> Dict[str, Any]:
    """
    Generate interview prep Q&A tailored to JD and candidate.
    """
    prompt = f"""
JOB DESCRIPTION:
{job_description}

CANDIDATE PROFILE:
{candidate_text}

Return JSON with key:
- interview_prep (array of objects: question, strong_answer, follow_up, follow_up_answer)
""".strip()

    return gemini_generate_json(prompt, system_prompt=SYSTEM_RECRUITER_PROMPT)

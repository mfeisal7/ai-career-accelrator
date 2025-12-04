# agents.py
"""
AI helpers for the AI Career Accelerator app.

This module is responsible for:
- Configuring Gemini
- Extracting text from uploaded PDFs
- Analyzing job descriptions
- Rewriting resumes to premium Kenyan-standard
- Generating tailored cover letters
- Generating follow-up email sequences
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Any

import streamlit as st
import google.generativeai as genai
import pypdf


# ============================================================
# Gemini Configuration Helpers
# ============================================================

def _get_api_key() -> str:
    """
    Load Gemini API key from secure backend only.

    Priority:
      1. st.secrets["GEMINI_API_KEY"]
      2. Environment variable GEMINI_API_KEY

    Raises:
        RuntimeError if not configured.
    """
    # 1) Streamlit secrets
    try:
        key = st.secrets["GEMINI_API_KEY"]
        if key:
            return key
    except Exception:
        pass

    # 2) Environment variable
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key

    raise RuntimeError(
        "Gemini API key is not configured. "
        "Set GEMINI_API_KEY in .streamlit/secrets.toml or as an environment variable."
    )


def _get_gemini_model() -> genai.GenerativeModel:
    """
    Configure Gemini with the backend API key and return a single, known-good model.

    We use 'gemini-1.5-flash', which is supported on the v1beta consumer API.
    """
    api_key = _get_api_key()
    genai.configure(api_key=api_key)

    model_name = "gemini-1.5-flash"

    try:
        model = genai.GenerativeModel(model_name)
        return model
    except Exception as e:
        raise RuntimeError(
            f"Failed to initialize Gemini model '{model_name}'. "
            f"Check your API key, billing, or project settings. Underlying error: {e}"
        )


# ============================================================
# JSON Extraction Helper
# ============================================================

def _extract_json_block(raw: str) -> str:
    """
    Attempt to extract the first plausible JSON object or array from a
    free-form LLM response.

    Handles cases where the model wraps JSON in markdown fences or adds
    explanatory text.
    """
    if not raw:
        return raw

    # Common pattern: ```json { ... } ```
    fenced = re.search(r"```json(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    # Any fenced code block: ``` { ... } ```
    fenced_any = re.search(r"```(.*?)```", raw, flags=re.DOTALL)
    if fenced_any:
        candidate = fenced_any.group(1).strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate

    # Fallback: first {...} block
    brace_match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if brace_match:
        return brace_match.group(0).strip()

    # Or array [...]
    array_match = re.search(r"\[.*\]", raw, flags=re.DOTALL)
    if array_match:
        return array_match.group(0).strip()

    return raw.strip()


def _safe_json_loads(raw: str) -> Any:
    """
    Robust JSON loader for LLM output.
    """
    if not raw:
        raise ValueError("Empty LLM response when JSON was expected.")

    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try extracting a JSON block
    candidate = _extract_json_block(raw)
    return json.loads(candidate)


# ============================================================
# PDF Text Extraction
# ============================================================

def extract_text_from_pdf(file) -> str:
    """
    Extract text from an uploaded PDF file using pypdf.

    Returns a single string containing the combined text.
    If the PDF appears to be scanned / image-only, the text
    may be empty and the caller should handle that case.
    """
    if file is None:
        return ""

    try:
        reader = pypdf.PdfReader(file)
    except Exception as e:
        raise RuntimeError(f"Failed to read PDF file: {e}")

    texts: List[str] = []
    for page_index, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:  # defensive for weird PDFs
            text = ""
        if text.strip():
            texts.append(text.strip())

    return "\n\n".join(texts)


# ============================================================
# Job Analysis
# ============================================================

def analyze_job(job_description: str) -> Dict[str, Any]:
    """
    Analyze a job description and return a structured JSON summary.

    The structure is designed to be:
    - Human-readable when shown in the UI
    - Machine-usable by rewrite_resume / generate_cover_letter / generate_emails
    """
    if not job_description or not job_description.strip():
        raise ValueError("Job description is empty.")

    model = _get_gemini_model()

    prompt = f"""
You are an expert Kenyan recruiter and hiring manager for top employers
like Safaricom, KCB, Deloitte, Microsoft ADC, Twiga and UN agencies.

Your task is to analyze the following job description and return a
single JSON object ONLY (no commentary, no markdown).

JOB DESCRIPTION:
\"\"\"{job_description}\"\"\"


Return JSON with the following shape:

{{
  "role_name": "string - best guess at the role title",
  "company_name": "string - best guess at the employer (or 'Confidential' if unknown)",
  "seniority": "Entry-level / Mid-level / Senior / Internship / Graduate Trainee",
  "summary": "2-3 sentence summary of what the role is about in plain language",

  "hard_skills": [
    "Finance modeling",
    "Advanced Excel",
    "Python",
    "SQL",
    "Data analysis",
    "Audit & tax compliance"
  ],

  "soft_skills": [
    "Communication",
    "Stakeholder management",
    "Attention to detail",
    "Problem-solving"
  ],

  "keywords": [
    "KRA",
    "IFRS",
    "Power BI",
    "SAP",
    "CRM"
  ],

  "nice_to_have": [
    "CPA(K)",
    "ACCA",
    "Experience in telecoms industry"
  ],

  "red_flags": [
    "Any potential mismatch risks between a generic graduate profile and this role",
    "e.g. 'role seems senior compared to limited experience'"
  ],

  "application_strategy": [
    "Key points to emphasize in CV",
    "Key points to emphasize in cover letter",
    "What to highlight in interviews"
  ]
}}

Remember: return ONLY valid JSON, no markdown fences or commentary.
    """.strip()

    generation_config = genai.types.GenerationConfig(
        temperature=0.4,
        max_output_tokens=1024,
    )

    response = model.generate_content(
        prompt,
        generation_config=generation_config,
    )
    raw = getattr(response, "text", "") or ""

    try:
        data = _safe_json_loads(raw)
    except Exception as e:
        raise RuntimeError(f"Failed to parse job analysis JSON: {e}\nRaw response:\n{raw}") from e

    if not isinstance(data, dict):
        raise RuntimeError("Job analysis response was not a JSON object.")

    # Normalise some keys to avoid KeyError downstream.
    data.setdefault("hard_skills", [])
    data.setdefault("soft_skills", [])
    data.setdefault("keywords", [])
    data.setdefault("nice_to_have", [])
    data.setdefault("red_flags", [])
    data.setdefault("application_strategy", [])

    return data


# ============================================================
# Resume Rewriting (Premium Kenyan Style)
# ============================================================

def rewrite_resume(
    resume_text: str,
    job_analysis: Dict[str, Any],
) -> str:
    """
    Rewrite the user's resume into a premium, ATS-optimized document.

    The output is Markdown so we can:
    - Render in Streamlit nicely
    - Convert to DOCX/PDF later
    """
    if not resume_text or not resume_text.strip():
        raise ValueError("Resume text is empty.")

    profile_hint = job_analysis.get(
        "inferred_profile",
        "Early-career Business / Operations / Analyst",
    )

    model = _get_gemini_model()

    prompt = f"""
You are an expert Kenyan resume writer trained on CVs from Safaricom, KCB,
Deloitte, Microsoft ADC, Twiga, banks, and UN agencies.

Rewrite the user's resume into a PREMIUM, ATS-optimized CV tailored to
the job analysis below.

PROFILE HINT (approximate target profile):
{profile_hint}

JOB ANALYSIS (JSON):
{json.dumps(job_analysis, indent=2)}

USER RESUME (RAW TEXT):
\"\"\"{resume_text}\"\"\"


GOALS:
- Make the candidate look like a strong fit for the role above.
- Use Kenyan corporate language and realistic, high-impact achievements.
- Add metrics where sensible (KES amounts, %, time saved, accuracy improved, team size, etc.).
- If dates are missing, use neutral phrasing like "(Dates Not Provided)".
- Assume early-career professional unless job_analysis.seniority clearly indicates senior.

OUTPUT FORMAT (MARKDOWN, NO JSON):
Exactly follow this structure and headings:

## SUMMARY
- 3–5 sentences summarising experience, domain, and value to Kenyan employers.

## EXPERIENCE
For each role (inferred or explicit):
**Job Title** | Company | Location | Dates (or "Dates Not Provided")
- Bullet 1 (Action → Task → Result → Metric)
- Bullet 2
- Bullet 3
(4–6 bullets per role is ideal)

## EDUCATION
- Degree | Institution | Location | Year (if known)

## KEY SKILLS
- Grouped bullets like:
- Technical: ...
- Business/Finance/Domain: ...
- Tools: ...

## CORE COMPETENCIES
- 6–10 bullet points of competencies aligned to the analyzed role.

Keep it ATS-friendly:
- No tables, emojis, icons, or images.
- Use plain text and simple Markdown only.
    """.strip()

    generation_config = genai.types.GenerationConfig(
        temperature=0.5,
        max_output_tokens=2048,
    )

    response = model.generate_content(
        prompt,
        generation_config=generation_config,
    )
    text = getattr(response, "text", "") or ""
    return text.strip()


# ============================================================
# Cover Letter Generation
# ============================================================

def generate_cover_letter(
    resume_text: str,
    job_analysis: Dict[str, Any],
) -> str:
    """
    Generate a tailored cover letter in plain text (Markdown allowed).
    """
    model = _get_gemini_model()

    role_name = job_analysis.get("role_name", "this position")
    company_name = job_analysis.get("company_name", "your organization")
    profile_hint = job_analysis.get(
        "inferred_profile",
        "Early-career Business / Operations / Analyst",
    )

    prompt = f"""
You are an expert Kenyan cover letter writer.

Using the user's resume and the job analysis below, write a strong,
one-page cover letter tailored to the Kenyan job market.

JOB ANALYSIS JSON:
{json.dumps(job_analysis, indent=2)}

ASSUMED TARGET ROLE:
Role: {role_name}
Company: {company_name}
Profile: {profile_hint}

USER RESUME (RAW TEXT):
\"\"\"{resume_text}\"\"\"


GUIDELINES:
- 4–6 short paragraphs.
- Professional but warm tone, not robotic.
- Show understanding of the specific role and company.
- Reference 2–3 hard skills and 2–3 soft skills from the analysis.
- Mention 2–3 achievements with metrics where possible.
- Close with a confident, polite call to action.

FORMAT:
Return plain text or Markdown only, no JSON, no bullet list cover letters.
    """.strip()

    generation_config = genai.types.GenerationConfig(
        temperature=0.55,
        max_output_tokens=1024,
    )

    response = model.generate_content(
        prompt,
        generation_config=generation_config,
    )
    text = getattr(response, "text", "") or ""
    return text.strip()


# ============================================================
# Follow-up Email Strategy
# ============================================================

def generate_emails(job_analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Generate a small sequence (up to 3) of follow-up emails.

    Each email is represented as:
    {
        "label": "After application (Day 3)",
        "subject": "Following up on my application for ...",
        "body": "Full email text..."
    }
    """
    model = _get_gemini_model()

    role_name = job_analysis.get("role_name", "the position")
    company_name = job_analysis.get("company_name", "your organization")

    prompt = f"""
You are an expert Kenyan career coach.

Based on the following job analysis, create a short follow-up email
sequence for a candidate who has applied to this role:

JOB ANALYSIS JSON:
{json.dumps(job_analysis, indent=2)}

ROLE:
{role_name} at {company_name}

Return ONLY a JSON array of 2–3 objects. Each object must have:
- "label": short descriptor, e.g. "After application (Day 3)"
- "subject": email subject line
- "body": full email body (plain text, polite, Kenyan corporate tone)

Example structure (do NOT include comments):

[
  {{
    "label": "After application (Day 3)",
    "subject": "Following up on my application for the Data Analyst role",
    "body": "Dear Hiring Manager, ... Kind regards, ..."
  }},
  ...
]

Return ONLY JSON.
    """.strip()

    generation_config = genai.types.GenerationConfig(
        temperature=0.45,
        max_output_tokens=1024,
    )

    response = model.generate_content(
        prompt,
        generation_config=generation_config,
    )
    raw = getattr(response, "text", "") or ""

    try:
        data = _safe_json_loads(raw)
    except Exception as e:
        raise RuntimeError(f"Failed to parse follow-up emails JSON: {e}\nRaw response:\n{raw}") from e

    if not isinstance(data, list):
        raise RuntimeError("Expected a JSON array of email objects from the model.")

    emails: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        subject = item.get("subject", "").strip()
        body = item.get("body", "").strip()
        label = item.get("label", "").strip() or "Email"
        if subject and body:
            emails.append(
                {
                    "label": label,
                    "subject": subject,
                    "body": body,
                }
            )

    return emails[:3]

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
    # 1. Try Environment Variable first (server)
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key

    # 2. Fallback: Streamlit secrets for local dev
    try:
        key = st.secrets["GEMINI_API_KEY"]
        if key:
            return key
    except (FileNotFoundError, KeyError):
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

    # Default model – override via GEMINI_MODEL if needed
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
You are an expert Kenyan recruiter working for top employers like Safaricom, KCB,
Equity Bank, Deloitte, PwC, UN, Microsoft ADC, and Twiga Foods.

Analyze the following job description and return ONLY a valid JSON object.

JOB DESCRIPTION:
\"\"\"{job_description}\"\"\"

Return JSON with exactly these keys:

{{
  "role_name": "string - best guess at job title",
  "company_name": "string - employer name or 'Confidential'",
  "seniority": "Entry-level | Mid-level | Senior | Internship | Graduate Trainee",
  "summary": "2-3 sentence plain English summary of the role",
  "hard_skills": ["Python", "Driving", "Excel", ...],
  "soft_skills": ["Leadership", "Communication", ...],
  "keywords": ["Agile", "Stakeholder Management", ...],
  "inferred_profile": "Early-career Analyst | Mid-level Manager | Senior Engineer | etc."
}}

Rules:
- The output MUST be valid JSON. No comments, no markdown, no explanations.
- If you are unsure, make a reasonable Kenyan-context assumption.
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
# Resume Rewrite – PREMIUM CV GENERATOR
# ============================================================

def rewrite_resume(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """
    Generate a premium, professional Kenyan CV tailored to the job.
    Output is ATS-friendly Markdown with strong achievements, metrics,
    and fully written sections (no placeholders, no missing content).
    """
    model = _get_gemini_model()

    role = (job_analysis.get("role_name") or "").strip() or "the role"
    company = (job_analysis.get("company_name") or "").strip() or "the company"
    seniority = (job_analysis.get("seniority") or "").strip()
    hard_skills = job_analysis.get("hard_skills", []) or []
    soft_skills = job_analysis.get("soft_skills", []) or []
    keywords = job_analysis.get("keywords", []) or []

    role_lower = role.lower()

    # Role-specific intelligence for major job categories
    role_specific_rules: List[str] = []

    # Drivers / Logistics
    if any(word in role_lower for word in ["driver", "chauffeur"]):
        role_specific_rules.append(
            "- Highlight accident-free driving record, licence categories, defensive "
            "driving, off-road capability, and experience transporting staff/equipment "
            "in Kenyan contexts (Nairobi traffic, upcountry routes, park roads)."
        )
        role_specific_rules.append(
            "- If no formal job history exists, create a strong 'Relevant Driving Experience' "
            "section with realistic scenarios (family driver, community vehicle, church van, "
            "company errands, long-distance trips)."
        )
        role_specific_rules.append(
            "- Include a 'LICENSES & TRAININGS' section with Kenyan Driving Licence "
            "classes and any safety/first-aid training."
        )

    # Customer Service / Call Centre / Front Office
    if any(word in role_lower for word in [
        "customer service", "call center", "call centre",
        "contact center", "front office", "receptionist"
    ]):
        role_specific_rules.append(
            "- Emphasise customer satisfaction, query resolution time, call volumes handled, "
            "and professionalism with difficult clients."
        )
        role_specific_rules.append(
            "- Include metrics like average calls per day, satisfaction scores, or reduced complaints."
        )

    # Sales / Business Development / Marketing
    if any(word in role_lower for word in [
        "sales", "business development", "bdm", "account manager", "marketing"
    ]):
        role_specific_rules.append(
            "- Focus heavily on revenue, targets, pipeline growth, number of accounts managed, "
            "and market expansion."
        )
        role_specific_rules.append(
            "- Use concrete numbers: KES amounts, % growth, number of new clients, conversion rates."
        )

    # Finance / Accounting / Audit
    if any(word in role_lower for word in [
        "accountant", "accounts", "finance", "auditor", "financial analyst"
    ]):
        role_specific_rules.append(
            "- Highlight accuracy, compliance, reconciliations, reporting deadlines, and "
            "controls/process improvements."
        )
        role_specific_rules.append(
            "- Emphasise tools (e.g., Excel, ERPs), reports produced, and support to decision-making."
        )

    # IT / Software / Data / Engineering
    if any(word in role_lower for word in [
        "developer", "engineer", "software", "data", "analytics",
        "analyst", "ict", "it support"
    ]):
        role_specific_rules.append(
            "- Emphasise technologies used, systems built, incidents resolved, and "
            "performance/reliability improvements."
        )
        role_specific_rules.append(
            "- Use metrics like reduced downtime, faster response times, performance gains, "
            "or data-driven decisions."
        )

    # Admin / Office / Operations
    if any(word in role_lower for word in [
        "administrator", "admin", "office assistant", "operations", "office manager"
    ]):
        role_specific_rules.append(
            "- Highlight organisation, coordination of teams/meetings, document management, "
            "and support to management."
        )
        role_specific_rules.append(
            "- Show how you reduced chaos, improved turnaround times, or supported smooth operations."
        )

    # NGO / Development / Humanitarian
    if any(word in role_lower for word in [
        "ngo", "project officer", "field officer", "community", "development", "humanitarian"
    ]):
        role_specific_rules.append(
            "- Emphasise community impact, beneficiaries reached, quality of reporting, and "
            "donor/stakeholder coordination."
        )
        role_specific_rules.append(
            "- Use numbers where possible: number of beneficiaries, locations covered, trainings done."
        )

    # Healthcare / Nursing / Clinical
    if any(word in role_lower for word in [
        "nurse", "clinical", "health", "medical", "pharmacy", "laboratory"
    ]):
        role_specific_rules.append(
            "- Highlight patient care quality, safety, adherence to protocols, and support "
            "to doctors/clinical teams."
        )
        role_specific_rules.append(
            "- Emphasise workload (number of patients per shift), reduced errors, and collaboration."
        )

    # Project Management / Coordination
    if any(word in role_lower for word in [
        "project manager", "project officer", "coordinator", "scrum", "agile"
    ]):
        role_specific_rules.append(
            "- Highlight planning, timelines, budgets, stakeholder coordination, and delivery "
            "of specific projects."
        )
        role_specific_rules.append(
            "- Use metrics: budget sizes, timelines met, risks mitigated, or scope delivered."
        )

    role_rules_block = ""
    if role_specific_rules:
        joined = "\n".join(f"* {r}" for r in role_specific_rules)
        role_rules_block = (
            "\nROLE-SPECIFIC GUIDELINES:\n"
            f"{joined}\n"
        )

    prompt = f"""
You are a senior Kenyan CV writer who builds high-conversion CVs used to win interviews
at organisations such as Safaricom, KCB, Equity, UN, Deloitte, KWS, NGOs and tech startups.

Your task is to fully rewrite the user's resume into a premium, professional,
ATS-optimized CV tailored to the following role:

ROLE: {role}
COMPANY: {company}
SENIORITY: {seniority}

JOB ANALYSIS:
{json.dumps(job_analysis, indent=2)}

USER RESUME (raw text):
<<<RESUME_START>>>
{resume_text}
<<<RESUME_END>>>


===========================
OUTPUT REQUIREMENTS
===========================

Write a complete CV in clean Markdown (no tables, no emojis).

Sections MUST be:

## PROFESSIONAL SUMMARY
A strong 3–5 sentence summary highlighting experience, strengths, and fit for the role.

## CORE COMPETENCIES
A bullet list of 6–14 skills combining:
- Job-specific hard skills: {hard_skills}
- Soft skills: {soft_skills}
- Keywords employers filter for: {keywords}

## PROFESSIONAL EXPERIENCE
Include 1–4 roles OR a 'Relevant Experience' section if no formal work history exists.
Each role MUST use action → result → metric style.
Example style:
- Delivered X by doing Y, resulting in Z (% improvement, cost/time savings, reliability, etc.).

## EDUCATION
List the best available details, even if incomplete (KCSE, diploma, degree, certifications).

## LICENSES & TRAININGS
Include if relevant (e.g., Driving Licence classes, professional certifications, short courses).

## LANGUAGES
English, Kiswahili, and any others.

(If a section truly does not apply, you may omit it, but only after considering reasonable assumptions.)

===========================
STRICT RULES
===========================

- NEVER output placeholders like: [Add here], [Replace], [Insert], "Lorem ipsum", or "To be added".
- NEVER tell the user to "add this later". The CV must be delivered as if a paid consultant finished it.
- If information is missing, create realistic, professional Kenyan content instead of leaving blanks.
- Bullets MUST be achievement-oriented, not just a copy of the job description.
- Use measurable results wherever possible (%, numbers, frequency, volumes, time saved, reliability).
- Keep tone formal, concise, and Kenyan corporate-friendly.
- Ensure the final output looks like a CV delivered by a human consultant that the user paid for.

{role_rules_block}

===========================
NOW PRODUCE THE FINAL CV:
===========================
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
Write a professional, warm Kenyan cover letter (4–6 short paragraphs) for the role below.

Role: {role}
Company: {company}
Analysis: {json.dumps(job_analysis, indent=2)}

Candidate resume:
<<<RESUME_START>>>
{resume_text}
<<<RESUME_END>>>

Tone:
- Confident but respectful
- Specific, with 2–3 achievements that include numbers where possible
- End with a clear call to action (invite to interview / discussion)

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
    return text.strip()


def generate_emails(job_analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Generate a 3-email follow-up sequence as a JSON array.

    Returns a list of dicts with keys: label, subject, body.
    If parsing fails, returns an empty list.
    """
    model = _get_gemini_model()
    role = job_analysis.get("role_name", "the position")
    company = job_analysis.get("company_name", "your organisation")

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

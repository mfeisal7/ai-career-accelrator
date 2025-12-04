# agents.py
import json
import re
from typing import Dict, List, Any

import requests
import pypdf


# ============================================================
# Proxy Configuration
# ============================================================

# Your public proxy URL (behind which the real Gemini key is stored securely)
PROXY_URL = (
    "https://gemini-proxy.mycompany.com/"
    "v1/models/gemini-1.5-pro:generateContent"
)


# ============================================================
# JSON Extraction Helper (Robust Parsing)
# ============================================================

def _extract_json_block(raw: str) -> str:
    """
    Attempt to robustly extract a JSON object/array from a model response.

    Handles cases like:
      - ```json ... ```
      - ``` ... ```
      - Extra natural language before/after the JSON
      - Plain JSON with no fences

    Returns:
        A JSON string (object or array) that can be passed to json.loads.

    Raises:
        ValueError if no plausible JSON block can be found.
    """
    if not raw:
        raise ValueError("Empty response; cannot extract JSON.")

    text = raw.strip()

    # 1. Try fenced ```json ... ``` block
    fence_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate:
            return candidate

    # 2. Try generic fenced ``` ... ``` block (in case it omits 'json')
    generic_fence = re.search(r"```(.*?)```", text, re.DOTALL)
    if generic_fence:
        candidate = generic_fence.group(1).strip()
        if candidate:
            return candidate

    # 3. Fallback: try to find first {...} or [...] block in the text
    brace_match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if brace_match:
        candidate = brace_match.group(1).strip()
        if candidate:
            return candidate

    raise ValueError("Could not locate a JSON block in the model response.")


# ============================================================
# Low-level Proxy Call Helpers
# ============================================================

def _post_to_proxy(
    prompt: str,
    temperature: float = 0.7,
    response_mime_type: str | None = "text/plain",
) -> Dict[str, Any]:
    """
    Send a generateContent request to the Gemini proxy.

    - If the proxy is down or returns 5xx, raise RuntimeError("Service temporarily unavailable").
    - Otherwise, return the decoded JSON body.
    """
    payload: Dict[str, Any] = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "temperature": temperature,
        },
    }

    # REST uses camelCase 'responseMimeType'
    if response_mime_type:
        payload["generationConfig"]["responseMimeType"] = response_mime_type

    try:
        resp = requests.post(PROXY_URL, json=payload, timeout=40.0)
    except requests.RequestException:
        # Network / DNS / connection error to proxy
        raise RuntimeError("Service temporarily unavailable")

    # Treat proxy 5xx (and forwarded upstream 5xx) as "temporarily unavailable"
    if resp.status_code >= 500:
        raise RuntimeError("Service temporarily unavailable")

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("Unexpected response from language service.")

    # Some Gemini error responses come as JSON with 'error'
    if "error" in data:
        # If it’s a 4xx with an explicit error, surface something slightly readable
        message = data["error"].get("message") or "Upstream error."
        raise RuntimeError(message)

    return data


def _extract_text_from_candidates(data: Dict[str, Any]) -> str:
    """
    Extract the main text from Gemini's generateContent response.
    """
    candidates = data.get("candidates") or []
    if not candidates:
        return ""

    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []

    texts: List[str] = []
    for part in parts:
        if isinstance(part, dict):
            t = part.get("text")
            if t:
                texts.append(t)

    return "".join(texts).strip()


def _call_gemini_text(
    prompt: str,
    temperature: float = 0.7,
) -> str:
    """
    Call Gemini (via proxy) for free-form text/markdown responses.
    """
    data = _post_to_proxy(
        prompt=prompt,
        temperature=temperature,
        response_mime_type="text/plain",
    )
    text = _extract_text_from_candidates(data)
    return text.strip()


def _call_gemini_json(
    prompt: str,
    temperature: float = 0.3,
) -> Any:
    """
    Call Gemini (via proxy) and parse JSON.

    - Requests application/json from the model.
    - First tries json.loads(raw_text) directly.
    - On failure, falls back to _extract_json_block + json.loads().
    """
    data = _post_to_proxy(
        prompt=prompt,
        temperature=temperature,
        response_mime_type="application/json",
    )

    raw = _extract_text_from_candidates(data)
    if not raw:
        raise RuntimeError("Empty response from language service.")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        json_str = _extract_json_block(raw)
        return json.loads(json_str)


# ============================================================
# PDF Text Extraction
# ============================================================

def extract_text_from_pdf(uploaded_file) -> str:
    """
    Extract text from an uploaded PDF file (Streamlit UploadedFile object).

    Returns:
        - Extracted text as a single string, or
        - An empty string if text could not be extracted or an error occurred.

    This function is intentionally resilient and never raises exceptions
    to the caller; failures are represented by returning "".
    """
    if uploaded_file is None:
        return ""

    try:
        reader = pypdf.PdfReader(uploaded_file)
        pages_text: List[str] = []

        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages_text.append(page_text.strip())

        return "\n\n".join(pages_text).strip()
    except Exception:
        return ""


# ============================================================
# 1. Job Analysis Agent (JSON Mode)
# ============================================================

def analyze_job(job_text: str) -> Dict[str, Any]:
    """
    Analyze the job description and return a structured JSON object.

    Expected JSON schema:
    {
      "role_name": "exact or inferred job title",
      "hard_skills": ["top 5 technical skills required", ...],
      "soft_skills": ["top 3 soft skills required", ...],
      "pain_points": ["what problems is the company trying to solve", ...],
      "keywords": ["combined set of role-specific ATS keywords", ...]
    }
    """
    if not job_text or not job_text.strip():
        raise ValueError("Job description cannot be empty.")

    prompt = f"""
You are an expert talent acquisition and hiring strategist.

Analyze the following job description and return a SINGLE JSON object with EXACTLY these keys:

{{
  "role_name": "string - exact or inferred job title",
  "hard_skills": [
    "string - top technical skill #1",
    "string - top technical skill #2",
    "string - top technical skill #3",
    "string - top technical skill #4",
    "string - top technical skill #5"
  ],
  "soft_skills": [
    "string - top soft skill #1",
    "string - top soft skill #2",
    "string - top soft skill #3"
  ],
  "pain_points": [
    "string - what business or technical problem this role is meant to solve",
    "string - another likely pain point"
  ],
  "keywords": [
    "string - ATS keyword or phrase",
    "string - ATS keyword or phrase",
    "..."
  ]
}}

Requirements:
- All values must be valid JSON (double quotes, no comments).
- Do not include any additional keys.
- Do not include any explanations or text outside the JSON.

Job Description:
\"\"\"{job_text}\"\"\""""

    result = _call_gemini_json(prompt=prompt, temperature=0.2)

    if not isinstance(result, dict):
        raise RuntimeError("Job analysis result is not a JSON object.")

    for key in ["role_name", "hard_skills", "soft_skills", "pain_points", "keywords"]:
        result.setdefault(key, [] if key != "role_name" else "")

    return result


# ============================================================
# 2. Resume Rewriting Agent (Text/Markdown Mode)
# ============================================================

def rewrite_resume(current_resume: str, job_analysis: Dict[str, Any]) -> str:
    """
    Rewrite the resume in clean, professional Markdown format tailored to the job.
    """
    if not current_resume or not current_resume.strip():
        raise ValueError("Current resume text cannot be empty.")

    role_name = job_analysis.get("role_name", "this role")
    pain_points = ", ".join(job_analysis.get("pain_points", []))
    hard_skills = ", ".join(job_analysis.get("hard_skills", []))
    soft_skills = ", ".join(job_analysis.get("soft_skills", []))
    keywords = ", ".join(job_analysis.get("keywords", []))

    prompt = f"""
You are a world-class resume writer specializing in high-performing, ATS-optimized resumes.

Task:
Rewrite the candidate's entire resume in **clean, professional Markdown** tailored for the specific role below.

Role: {role_name}

Use this information from the job analysis:
- Pain points to address: {pain_points}
- Hard skills to highlight: {hard_skills}
- Soft skills to demonstrate: {soft_skills}
- Important ATS keywords: {keywords}

Markdown formatting requirements (very important):
- Use clear Markdown section headers, e.g.:
  - `## SUMMARY`
  - `## EXPERIENCE`
  - `## PROJECTS`
  - `## EDUCATION`
  - `## SKILLS`
- Use bullet lists for responsibilities and achievements under each role.
- Use **bold** for company names, job titles, and key technologies.
- Use concise, impact-focused bullet points starting with strong action verbs.
- Make the document one-page friendly (concise but powerful).

Guidelines:
- Keep it truthful; do NOT invent experience or skills.
- Emphasize quantifiable achievements and impact.
- Prioritize relevance to the role and pain points.
- Maintain a professional, modern tone suitable for ATS and human reviewers.

Original Resume:
\"\"\"{current_resume}\"\"\"


Return ONLY the rewritten resume in Markdown. Do NOT include any additional commentary or explanation.
"""

    return _call_gemini_text(
        prompt=prompt,
        temperature=0.6,
    )


# ============================================================
# 3. Cover Letter Agent (Text Mode)
# ============================================================

def generate_cover_letter(current_resume: str, job_analysis: Dict[str, Any]) -> str:
    """
    Generate a highly tailored cover letter (plain text).
    """
    if not current_resume or not current_resume.strip():
        raise ValueError("Current resume text cannot be empty.")

    role_name = job_analysis.get("role_name", "this position")
    pain_points = ", ".join(job_analysis.get("pain_points", []))
    hard_skills = ", ".join(job_analysis.get("hard_skills", []))
    soft_skills = ", ".join(job_analysis.get("soft_skills", []))

    prompt = f"""
You are a senior career coach and professional writer.

Write a compelling, professional cover letter (3–4 paragraphs) that:
- Opens with a strong hook and specific excitement for the role "{role_name}".
- Connects the candidate's prior achievements directly to these company pain points: {pain_points}
- Naturally incorporates these hard skills: {hard_skills}
- Demonstrates these soft skills in action: {soft_skills}
- Ends with a confident, courteous call to action.

Candidate's resume for context:
\"\"\"{current_resume}\"\"\"


Job analysis (for additional context):
\"\"\"{json.dumps(job_analysis)}\"\"\"


Formatting requirements:
- Plain text only (no markdown headers or bullet characters).
- Include appropriate paragraphs separated by blank lines.
- Do NOT include salutations placeholders like [Hiring Manager]; instead, use "Hi Hiring Manager,".
- Do NOT include any explanation outside of the cover letter itself.

Return ONLY the cover letter text.
"""

    return _call_gemini_text(
        prompt=prompt,
        temperature=0.7,
    )


# ============================================================
# 4. Follow-up Email Strategy Agent (JSON Mode)
# ============================================================

def generate_emails(job_analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Generate a strategic follow-up email sequence as JSON.

    Expected JSON schema (array of 3 email objects):
    [
      {
        "label": "initial_application",
        "subject": "Subject line",
        "body": "Full email body..."
      },
      {
        "label": "thank_you_after_interview",
        "subject": "Subject line",
        "body": "Full email body..."
      },
      {
        "label": "polite_check_in",
        "subject": "Subject line",
        "body": "Full email body..."
      }
    ]
    """
    role_name = job_analysis.get("role_name", "this role")
    pain_points = ", ".join(job_analysis.get("pain_points", []))

    prompt = f"""
You are a senior recruiter and career strategist.

Create a sequence of exactly 3 professional follow-up emails for a candidate applying to the role "{role_name}".

Each email must be represented as a JSON object with EXACTLY these keys:
- "label": a short identifier such as "initial_application", "thank_you_after_interview", or "polite_check_in"
- "subject": a concise, professional subject line
- "body": the full email body, including greeting, 2–4 short paragraphs, and a professional sign-off

The JSON response must be an ARRAY like:

[
  {{
    "label": "initial_application",
    "subject": "Application for {role_name}",
    "body": "Full email body..."
  }},
  {{
    "label": "thank_you_after_interview",
    "subject": "Thank you for the opportunity – {role_name}",
    "body": "Full email body..."
  }},
  {{
    "label": "polite_check_in",
    "subject": "Checking in on my application – {role_name}",
    "body": "Full email body..."
  }}
]

Guidelines:
- Tone: warm, confident, concise, value-adding.
- Subtly reference the company's pain points: {pain_points}
- Do NOT use placeholders like [Role] or [Company]; instead, embed the role name directly: "{role_name}".
- All strings must be valid JSON strings (double quotes, no trailing commas).
- Do NOT include any explanatory text outside the JSON array.
"""

    result = _call_gemini_json(prompt=prompt, temperature=0.4)

    if not isinstance(result, list):
        raise RuntimeError("Email generation result is not a JSON array.")

    emails: List[Dict[str, str]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        subject = item.get("subject", "").strip()
        body = item.get("body", "").strip()
        label = item.get("label", "").strip() or "email"
        if subject and body:
            emails.append(
                {
                    "label": label,
                    "subject": subject,
                    "body": body,
                }
            )

    return emails[:3]

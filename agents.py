# agents.py
import json
import re
from typing import Dict, List, Any, Tuple

import streamlit as st
import google.generativeai as genai
import pypdf


# ============================================================
# Gemini Configuration
# ============================================================

def _configure_gemini(api_key: str) -> None:
    """
    Configure the Gemini client with the provided API key.
    """
    if not api_key:
        raise ValueError("Google Gemini API key is required.")
    genai.configure(api_key=api_key)


@st.cache_resource(show_spinner=False)
def _get_working_model(api_key: str) -> Tuple[str, Any]:
    """
    Dynamic Model Discovery + Live Model Validator.

    Steps:
    1. Configure Gemini with the provided API key.
    2. Call genai.list_models() to discover which models are available.
    3. Filter to models that support the 'generateContent' method.
    4. Prefer models whose name contains 'flash' (fast), but accept any
       generateContent-capable model if no flash model is available.
    5. For each candidate model, perform a minimal 'ping' generate_content
       call to ensure the model actually works with this key/region.
    6. Return (model_name, model_instance) for the first working model.

    If no working model is found, raise a detailed RuntimeError instructing
    the user to check their API key and project configuration.
    """
    _configure_gemini(api_key)

    try:
        available_models = list(genai.list_models())
    except Exception as e:
        raise RuntimeError(
            "Failed to list available Gemini models. "
            "Please verify your API key, project, and region configuration."
        ) from e

    if not available_models:
        raise RuntimeError(
            "No Gemini models are visible to this API key. "
            "Please ensure your Google Cloud project has Gemini access enabled."
        )

    # Filter to models that support generateContent
    generate_capable = [
        m
        for m in available_models
        if "generateContent" in getattr(m, "supported_generation_methods", [])
    ]

    if not generate_capable:
        names = [m.name for m in available_models]
        raise RuntimeError(
            "Your API key does not have access to any Gemini models that support "
            "'generateContent'. Available models: "
            f"{names}. Please enable text generation models in your project."
        )

    # Sort for deterministic order
    generate_capable = sorted(generate_capable, key=lambda m: m.name)

    # Prefer 'flash' models, then fall back to any other generateContent-capable model
    flash_candidates = [m for m in generate_capable if "flash" in m.name.lower()]
    other_candidates = [m for m in generate_capable if m not in flash_candidates]

    ordered_candidates = flash_candidates + other_candidates

    last_error: Exception | None = None
    tried_names: List[str] = []

    for meta in ordered_candidates:
        model_name = meta.name
        tried_names.append(model_name)
        try:
            model = genai.GenerativeModel(model_name)
            # Minimal "ping" to validate model works with this key/region
            resp = model.generate_content(
                "ping",
                generation_config={
                    "max_output_tokens": 1,
                },
            )
            _ = getattr(resp, "text", None)
            return model_name, model
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(
        "Could not find a working Gemini model that supports generateContent "
        "for this API key/region.\n"
        f"Models tried: {tried_names}\n"
        f"Last error: {last_error}"
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
# Gemini Call Helpers (Text & JSON)
# ============================================================

def _call_gemini_text(
    api_key: str,
    prompt: str,
    temperature: float = 0.7,
    response_mime_type: str = "text/plain",
) -> str:
    """
    Call Gemini for free-form text/markdown responses.
    Uses the dynamically discovered working model from _get_working_model.
    """
    model_name, model = _get_working_model(api_key)

    try:
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": temperature,
                "response_mime_type": response_mime_type,
            },
        )
        text = getattr(response, "text", "") or ""
        return text.strip()
    except Exception as e:
        raise RuntimeError(
            f"Gemini text generation error using model '{model_name}': {str(e)}"
        ) from e


def _call_gemini_json(
    api_key: str,
    prompt: str,
    temperature: float = 0.3,
) -> Any:
    """
    Call Gemini and parse JSON, with smart handling for models that support
    native JSON mode vs. those that do not.

    Logic:
    - Use _get_working_model to dynamically discover an available model.
    - If the selected model is a 1.5 / 'flash' variant, send
      response_mime_type='application/json' to request structured output.
    - Otherwise, do NOT set response_mime_type (some legacy models will
      reject it) and treat the response as plain text, extracting JSON
      with regex as needed.

    In all cases:
    - First attempt json.loads(raw) directly.
    - On failure, fall back to _extract_json_block(raw) and json.loads().
    """
    model_name, model = _get_working_model(api_key)

    # Decide whether the model natively supports JSON mime-type:
    # Heuristic: 1.5 models and those with "flash" usually support it.
    name_lower = model_name.lower()
    is_json_native = ("1.5" in model_name) or ("flash" in name_lower)

    generation_config: Dict[str, Any] = {
        "temperature": temperature,
    }
    if is_json_native:
        generation_config["response_mime_type"] = "application/json"

    try:
        response = model.generate_content(
            prompt,
            generation_config=generation_config,
        )
        raw = getattr(response, "text", "") or ""

        # First, try to parse raw directly as JSON.
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: regex-based extraction, important for models that
            # return chatty text or markdown-wrapped JSON.
            json_str = _extract_json_block(raw)
            return json.loads(json_str)

    except json.JSONDecodeError as je:
        raise RuntimeError(
            f"Failed to parse JSON from Gemini response using model '{model_name}': "
            f"{je}\nRaw response: {raw}"
        ) from je
    except Exception as e:
        raise RuntimeError(
            f"Gemini JSON generation error using model '{model_name}': {str(e)}"
        ) from e


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

def analyze_job(job_text: str, api_key: str) -> Dict[str, Any]:
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

    result = _call_gemini_json(api_key=api_key, prompt=prompt, temperature=0.2)

    if not isinstance(result, dict):
        raise RuntimeError("Job analysis result is not a JSON object.")

    for key in ["role_name", "hard_skills", "soft_skills", "pain_points", "keywords"]:
        result.setdefault(key, [] if key != "role_name" else "")

    return result


# ============================================================
# 2. Resume Rewriting Agent (Text/Markdown Mode)
# ============================================================

def rewrite_resume(current_resume: str, job_analysis: Dict[str, Any], api_key: str) -> str:
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

    # IMPORTANT: Gemini 2.0 only accepts "text/plain" or "application/json".
    # We still get Markdown formatting because the prompt explicitly demands it.
    return _call_gemini_text(
        api_key=api_key,
        prompt=prompt,
        temperature=0.6,
        response_mime_type="text/plain",
    )


# ============================================================
# 3. Cover Letter Agent (Text Mode)
# ============================================================

def generate_cover_letter(current_resume: str, job_analysis: Dict[str, Any], api_key: str) -> str:
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
        api_key=api_key,
        prompt=prompt,
        temperature=0.7,
        response_mime_type="text/plain",
    )


# ============================================================
# 4. Follow-up Email Strategy Agent (JSON Mode)
# ============================================================

def generate_emails(job_analysis: Dict[str, Any], api_key: str) -> List[Dict[str, str]]:
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

    result = _call_gemini_json(api_key=api_key, prompt=prompt, temperature=0.4)

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

"""
agents.py — AI Career Accelerator (Zero-AI Template Engine)
============================================================
Fully self-contained CV generation system. No API keys required.

If GEMINI_API_KEY or OPENAI_API_KEY is set in the environment,
the system automatically upgrades to AI-enhanced output.
Otherwise it uses the professional template engine below —
which produces high-quality, ATS-optimised documents for free.

Architecture:
  1. JD Parser         — extracts keywords, sector, requirements from job descriptions
  2. CV Builder        — template engine that formats input into professional CVs
  3. Cover Letter Engine — smart templates with variable substitution
  4. Email Library     — 4 strategic follow-up emails per job
  5. Interview Bank    — curated Q&A library by sector
  6. LinkedIn Engine   — profile optimization templates
  7. Gap Analyser      — keyword matching between resume and JD
"""

from __future__ import annotations

import io
import os
import re
import json
import random
from collections import Counter
from typing import Dict, List, Any, Optional

import pypdf


# ════════════════════════════════════════════════════════════════
# SECTION 0: AI UPGRADE LAYER (optional — gracefully degrades)
# ════════════════════════════════════════════════════════════════

def _ai_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip())


def _gemini_generate(prompt: str) -> Optional[str]:
    """Try Gemini if key is set. Returns None if unavailable."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    try:
        import google.generativeai as genai
        from tenacity import retry, stop_after_attempt, wait_exponential
        genai.configure(api_key=key)
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()
        model = genai.GenerativeModel(model_name)
        resp = model.generate_content(prompt)
        return (getattr(resp, "text", None) or "").strip() or None
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════
# SECTION 1: KENYAN JOB MARKET DATA
# ════════════════════════════════════════════════════════════════

# Sector detection keyword map
SECTOR_KEYWORDS: Dict[str, List[str]] = {
    "Banking & Finance": [
        "bank", "kCB", "equity", "cooperative bank", "dtb", "absa", "standard chartered",
        "microfinance", "sacco", "mfb", "financial services", "credit", "loan", "treasury",
        "accounting", "audit", "reconciliation", "forex", "trade finance", "kyc", "aml",
        "compliance", "actuarial", "underwriting", "insurance"
    ],
    "NGO & Development": [
        "ngo", "non-governmental", "nonprofit", "un ", "usaid", "unicef", "undp", "who",
        "world bank", "dfid", "giz", "care", "save the children", "oxfam", "irc",
        "donor", "grant", "m&e", "monitoring and evaluation", "humanitarian", "community",
        "development", "programme", "beneficiary", "safeguarding", "livelihoods"
    ],
    "Technology & IT": [
        "software", "developer", "engineer", "python", "java", "react", "nodejs",
        "cloud", "aws", "azure", "gcp", "devops", "kubernetes", "docker", "api",
        "cybersecurity", "data science", "machine learning", "ai", "blockchain",
        "database", "sql", "postgresql", "mongodb", "agile", "scrum", "git"
    ],
    "Telecoms": [
        "safaricom", "airtel", "telkom", "telecommunications", "telecom", "network",
        "rf engineer", "fiber", "mpesa", "mobile money", "tower", "4g", "5g", "voip"
    ],
    "Healthcare": [
        "hospital", "clinic", "medical", "nurse", "doctor", "physician", "pharmacy",
        "pharmacist", "lab", "laboratory", "clinical", "patient", "health", "aga khan",
        "kenyatta", "mater", "nairobi hospital", "public health", "epidemiology"
    ],
    "Education": [
        "school", "university", "college", "teacher", "lecturer", "tutor", "education",
        "curriculum", "academic", "student", "faculty", "campus", "research", "professor"
    ],
    "Government & Public Sector": [
        "government", "ministry", "county government", "public service", "civil service",
        "national government", "parastatal", "kenya revenue authority", "kra", "kebs",
        "kenha", "kirdi", "county", "ward", "constituency"
    ],
    "FMCG & Retail": [
        "fmcg", "retail", "sales", "marketing", "brand", "consumer goods", "distribution",
        "supply chain", "merchandising", "trade marketing", "key accounts", "fmcg"
    ],
    "Consulting & Professional Services": [
        "consulting", "pwc", "deloitte", "kpmg", "ey ", "ernst & young", "mckinsey",
        "advisory", "strategy", "management consulting", "business development"
    ],
    "Agriculture & Environment": [
        "agriculture", "agri", "farm", "crop", "livestock", "horticulture", "agrovet",
        "environment", "conservation", "wildlife", "forestry", "climate", "sustainability"
    ],
}

# Kenyan salary ranges by sector + seniority
SALARY_RANGES: Dict[str, Dict[str, str]] = {
    "Banking & Finance": {
        "Entry": "KSh 40,000 – 80,000/month",
        "Mid": "KSh 80,000 – 200,000/month",
        "Senior": "KSh 200,000 – 500,000/month",
        "Manager": "KSh 300,000 – 700,000/month",
    },
    "NGO & Development": {
        "Entry": "KSh 50,000 – 100,000/month",
        "Mid": "KSh 100,000 – 250,000/month",
        "Senior": "KSh 250,000 – 500,000/month",
        "Manager": "KSh 350,000 – 800,000/month",
    },
    "Technology & IT": {
        "Entry": "KSh 60,000 – 120,000/month",
        "Mid": "KSh 120,000 – 300,000/month",
        "Senior": "KSh 300,000 – 600,000/month",
        "Manager": "KSh 400,000 – 900,000/month",
    },
    "Telecoms": {
        "Entry": "KSh 50,000 – 100,000/month",
        "Mid": "KSh 100,000 – 250,000/month",
        "Senior": "KSh 250,000 – 500,000/month",
        "Manager": "KSh 350,000 – 700,000/month",
    },
    "Healthcare": {
        "Entry": "KSh 35,000 – 80,000/month",
        "Mid": "KSh 80,000 – 200,000/month",
        "Senior": "KSh 150,000 – 400,000/month",
        "Manager": "KSh 250,000 – 600,000/month",
    },
    "Government & Public Sector": {
        "Entry": "KSh 30,000 – 60,000/month",
        "Mid": "KSh 60,000 – 150,000/month",
        "Senior": "KSh 150,000 – 350,000/month",
        "Manager": "KSh 250,000 – 500,000/month",
    },
    "default": {
        "Entry": "KSh 35,000 – 80,000/month",
        "Mid": "KSh 80,000 – 180,000/month",
        "Senior": "KSh 180,000 – 400,000/month",
        "Manager": "KSh 300,000 – 600,000/month",
    },
}

# Seniority detection
SENIORITY_PATTERNS = {
    "Manager": r"\b(manager|head of|director|vp |vice president|chief|ceo|cto|cfo|lead)\b",
    "Senior":  r"\b(senior|sr\.|principal|specialist|experienced|5\+|7\+|8\+|10\+)\b",
    "Mid":     r"\b(mid[- ]?level|associate|3\+|4\+|5 years)\b",
    "Entry":   r"\b(entry[- ]?level|graduate|junior|jr\.|fresh|intern|trainee|0[- ]?2|1[- ]?2 years)\b",
}

# Strong ATS action verbs by category
ACTION_VERBS = {
    "leadership":   ["Led", "Managed", "Directed", "Supervised", "Coordinated", "Oversaw", "Spearheaded"],
    "achievement":  ["Achieved", "Delivered", "Exceeded", "Surpassed", "Attained", "Secured", "Won"],
    "development":  ["Developed", "Built", "Created", "Designed", "Launched", "Established", "Implemented"],
    "analysis":     ["Analysed", "Researched", "Evaluated", "Assessed", "Investigated", "Monitored"],
    "improvement":  ["Improved", "Optimised", "Streamlined", "Enhanced", "Reduced", "Increased", "Transformed"],
    "support":      ["Supported", "Assisted", "Facilitated", "Enabled", "Provided", "Delivered", "Maintained"],
    "collaboration":["Collaborated", "Partnered", "Liaised", "Engaged", "Coordinated", "Negotiated"],
}

# Stop words for keyword extraction
STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "shall", "must", "can",
    "this", "that", "these", "those", "it", "its", "we", "our", "you", "your", "they",
    "their", "he", "she", "his", "her", "as", "by", "from", "into", "about", "than",
    "also", "any", "all", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "not", "only", "own", "same", "so", "than", "too", "very", "just", "work",
    "working", "experience", "required", "requirements", "responsibilities", "duties",
    "ability", "skills", "knowledge", "strong", "good", "excellent", "proven", "demonstrated",
    "minimum", "preferred", "desired", "including", "related", "relevant", "ensure",
    "provide", "support", "manage", "develop", "assist", "help", "use", "using", "through",
    "across", "within", "under", "over", "well", "key", "main", "primary", "role",
}


# ════════════════════════════════════════════════════════════════
# SECTION 2: JD PARSER (no AI required)
# ════════════════════════════════════════════════════════════════

def _detect_sector(text: str) -> str:
    text_lower = text.lower()
    scores = {sector: 0 for sector in SECTOR_KEYWORDS}
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                scores[sector] += 1
    best = max(scores, key=lambda s: scores[s])
    return best if scores[best] > 0 else "General"


def _detect_seniority(text: str) -> str:
    text_lower = text.lower()
    for level, pattern in SENIORITY_PATTERNS.items():
        if re.search(pattern, text_lower, re.IGNORECASE):
            return level
    return "Mid"


_ROLE_PATTERN = re.compile(
    r"\b((?:senior|junior|lead|chief|head of|principal|associate|assistant|deputy|group)?\s*"
    r"(?:data|software|business|financial|credit|project|program|sales|marketing|human resources?|hr|"
    r"supply chain|procurement|logistics|operations|customer|legal|compliance|it|ict|network|security|"
    r"civil|structural|electrical|mechanical|environmental|agricultural|public health|clinical|medical|"
    r"monitoring|evaluation|research|communications?|media|graphic|brand|product|accounts?|finance|audit|"
    r"tax|treasury|investment|risk|internal control|strategy|planning|digital|cloud|devops|machine learning|ai)?\s*"
    r"(?:officer|analyst|manager|engineer|developer|coordinator|specialist|associate|director|"
    r"consultant|advisor|assistant|executive|lead|architect|scientist|officer|intern|"
    r"accountant|auditor|lawyer|advocate|nurse|doctor|pharmacist|teacher|lecturer|trainer|"
    r"administrator|supervisor|technician|designer|writer|editor|reporter))",
    re.IGNORECASE,
)

_EMPLOYER_PATTERN = re.compile(
    r"(?:join(?:ing)? (?:our team at|us at|the team at)|working at|based at|at )\s*([A-Z][A-Za-z\s&,\.]+?)(?:\.|,|\n|$)",
    re.IGNORECASE,
)

_KNOWN_EMPLOYERS = re.compile(
    r"\b(Safaricom|KCB|Equity Bank|Co-op Bank|NCBA|Absa|Standard Chartered|Stanbic|DTB|"
    r"Family Bank|Prime Bank|I&M Bank|HF Group|CIC Insurance|Britam|UAP|Jubilee|AAR|"
    r"Nation Media|Standard Group|Royal Media|Mediamax|"
    r"Unilever|Procter|Nestlé|EABL|BAT|Bamburi|Savola|"
    r"Kenya Power|Kenya Pipeline|Kenya Airways|Kenya Ports|KenGen|KETRACO|KPLC|"
    r"USAID|UNDP|UNHCR|UNICEF|WHO|IOM|WFP|Save the Children|World Vision|Oxfam|"
    r"Catholic Relief|CARE|Mercy Corps|Aga Khan|Amref|"
    r"Microsoft|Google|IBM|Oracle|Huawei|Ericsson|Nokia|"
    r"Ministry of|County Government of|National Treasury|KNBS|CBK|CMA|IRA)\b",
)


def _detect_job_title(text: str) -> str:
    # First: look for "Position:", "Role:", "Job Title:" label
    m = re.search(r"(?:position|role|job title|vacancy|hiring for|recruiting for)[:\s]+([^\n]{3,80})", text, re.IGNORECASE)
    if m:
        title = m.group(1).strip().strip(":-– ").split("\n")[0][:80]
        if title:
            return title

    # Second: short standalone lines (headers) containing role keywords
    lines = text.strip().split("\n")
    for line in lines[:10]:
        line = line.strip()
        if 3 < len(line) < 70 and not any(w in line.lower() for w in ["about us", "company", "location", "salary", "we are looking", "we seek", "we need"]):
            m2 = _ROLE_PATTERN.search(line)
            if m2:
                return line.strip(":-– ").strip()

    # Third: extract just the role name from narrative sentences
    m3 = re.search(
        r"(?:looking for|seeking|recruiting|hiring)(?: a| an)?\s*([A-Za-z\s]{3,60}?)(?:\s+to join|\s+who will|\s+to lead|\s+to manage|\.|,)",
        text, re.IGNORECASE,
    )
    if m3:
        candidate = m3.group(1).strip()
        if _ROLE_PATTERN.search(candidate):
            return candidate[:80]

    # Fourth: find any role keyword match and return surrounding words
    m4 = _ROLE_PATTERN.search(text[:500])
    if m4:
        return m4.group(0).strip()[:80]

    return lines[0].strip()[:80] if lines else "Position"


def _detect_employer(text: str) -> str:
    # Explicit label: "Company: ...", "Organisation: ..." — highest confidence
    m2 = re.search(r"(?:company|organisation|organization|employer|about us|client)[:\s]+([^\n]{3,80})", text, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()[:80]

    # Known Kenyan employers — very reliable
    m = _KNOWN_EMPLOYERS.search(text)
    if m:
        matched = m.group(0).strip()
        end = m.end()
        suffix_match = re.match(r"\s*(PLC|Ltd|Limited|Group|Kenya|Holdings|Foundation|Africa|International)\.?", text[end:end + 40], re.IGNORECASE)
        if suffix_match:
            matched = matched + " " + suffix_match.group(1)
        return matched.strip()[:80]

    # Inline: "join our team at X", "based at X" — only trust if starts uppercase (not "our office")
    m3 = _EMPLOYER_PATTERN.search(text)
    if m3:
        candidate = m3.group(1).strip()
        candidate = re.split(r"\s+(?:is|are|was|has|will|to |in |and |&)\b", candidate, 1)[0].strip()
        if 2 < len(candidate) < 80 and candidate[0].isupper():
            return candidate

    return "Not specified"


def _extract_requirements(text: str) -> List[str]:
    """Extract bullet-point requirements from JD."""
    requirements = []
    # Find requirement sections
    sections = re.split(r"\n(?:requirements?|qualifications?|what we (?:look for|need)|must[- ]haves?|experience required)[:\s]*\n", text, flags=re.IGNORECASE)
    req_text = sections[1] if len(sections) > 1 else text

    # Extract bullets
    bullets = re.findall(r"[•\-\*\✓]\s*(.+?)(?=\n[•\-\*\✓]|\n\n|\Z)", req_text, re.DOTALL)
    for b in bullets[:10]:
        cleaned = b.strip().replace("\n", " ")
        if 10 < len(cleaned) < 200:
            requirements.append(cleaned)

    # Also try numbered lists
    if len(requirements) < 3:
        numbered = re.findall(r"\d+\.\s*(.+?)(?=\n\d+\.|\n\n|\Z)", req_text, re.DOTALL)
        for b in numbered[:10]:
            cleaned = b.strip().replace("\n", " ")
            if 10 < len(cleaned) < 200:
                requirements.append(cleaned)

    # Fallback: lines containing key qualifiers
    if len(requirements) < 3:
        for line in text.split("\n"):
            line = line.strip()
            if any(k in line.lower() for k in ["degree", "diploma", "bachelor", "master", "years of", "experience in", "proficient", "certification"]):
                if 10 < len(line) < 200:
                    requirements.append(line)

    return requirements[:10]


def _extract_ats_keywords(text: str, top_n: int = 20) -> List[str]:
    """Extract high-value ATS keywords using frequency + known keyword patterns."""
    # Extract noun phrases and technical terms
    text_lower = text.lower()

    # Pre-seeded high-value keywords to look for
    known_keywords = [
        # Technical tools
        "microsoft excel", "ms excel", "power bi", "tableau", "sql", "python", "r programming",
        "spss", "stata", "google analytics", "salesforce", "sap", "quickbooks", "sage",
        "erp", "crm", "jira", "confluence", "github", "docker", "kubernetes", "aws", "azure",
        # Business skills
        "project management", "stakeholder management", "budget management", "team management",
        "risk management", "change management", "strategic planning", "business development",
        "financial analysis", "data analysis", "market research", "report writing",
        # NGO/Development
        "monitoring and evaluation", "m&e", "logframe", "results-based management",
        "donor reporting", "grant management", "community mobilization", "capacity building",
        "gender mainstreaming", "safeguarding", "vulnerability assessment",
        # Kenyan market specific
        "kenyan market", "east africa", "nairobi", "county government", "mpesa", "mobile money",
        "swahili", "kiswahili",
        # Soft skills (frequently searched by ATS)
        "communication skills", "problem solving", "analytical skills", "leadership",
        "teamwork", "attention to detail", "time management", "customer service",
        "presentation skills", "negotiation", "conflict resolution",
        # Qualifications
        "bachelor's degree", "master's degree", "mba", "cpa", "acca", "cfa",
        "pmp", "prince2", "itil", "cism", "cissp",
    ]

    found_keywords = []
    for kw in known_keywords:
        if kw in text_lower:
            found_keywords.append(kw.title() if len(kw) > 4 else kw.upper())

    # Also extract frequent meaningful words
    words = re.findall(r"\b[a-zA-Z][a-zA-Z\-]{3,}\b", text)
    word_freq = Counter(w.lower() for w in words if w.lower() not in STOP_WORDS)
    top_words = [w.capitalize() for w, _ in word_freq.most_common(30) if len(w) > 4]

    combined = list(dict.fromkeys(found_keywords + top_words))  # deduplicate, preserve order
    return combined[:top_n]


def _extract_soft_skills(text: str) -> List[str]:
    soft_map = {
        "communication": "Strong communication skills",
        "teamwork": "Team player",
        "leadership": "Leadership ability",
        "problem": "Problem-solving",
        "analytical": "Analytical thinking",
        "attention to detail": "Attention to detail",
        "time management": "Time management",
        "customer service": "Customer service orientation",
        "negotiation": "Negotiation skills",
        "presentation": "Presentation skills",
        "adaptab": "Adaptability",
        "innovat": "Innovative thinking",
        "collaborat": "Collaboration",
        "organis": "Organisational skills",
        "self-motiv": "Self-motivation",
    }
    text_lower = text.lower()
    found = []
    for key, label in soft_map.items():
        if key in text_lower:
            found.append(label)
    return found[:6] if found else ["Communication", "Teamwork", "Problem-solving", "Attention to detail"]


def _extract_application_tips(sector: str, seniority: str, requirements: List[str]) -> List[str]:
    tips = [
        f"Tailor your CV specifically to match the keywords in this job description.",
        "Quantify your achievements wherever possible (e.g. 'increased revenue by 30%').",
        "Follow up via email 7 days after applying if you hear nothing.",
    ]
    if "NGO" in sector or "Development" in sector:
        tips.append("Highlight donor reporting, M&E, and logframe experience prominently.")
        tips.append("Mention any experience with specific donors (USAID, UNICEF, EU, etc.).")
    elif "Banking" in sector or "Finance" in sector:
        tips.append("Emphasise compliance knowledge, AML/KYC awareness if applicable.")
        tips.append("Highlight any CPA, ACCA, or CFA qualifications prominently.")
    elif "Tech" in sector:
        tips.append("Include a link to your GitHub profile or portfolio if you have one.")
        tips.append("List specific tools and programming languages clearly in a skills section.")
    if seniority in ("Entry", "Mid"):
        tips.append("If you're a fresh graduate, front-load your education and internship achievements.")
    return tips[:5]


def analyze_job(jd: str) -> Dict[str, Any]:
    """
    Analyse a job description without AI.
    Returns the same schema as the AI version for full compatibility.
    """
    if not jd or not jd.strip():
        return _empty_analysis()

    # Try AI upgrade first
    if _ai_available():
        ai_result = _try_ai_analyze(jd)
        if ai_result:
            return ai_result

    # Template engine
    sector = _detect_sector(jd)
    seniority = _detect_seniority(jd)
    job_title = _detect_job_title(jd)
    employer = _detect_employer(jd)
    requirements = _extract_requirements(jd)
    keywords = _extract_ats_keywords(jd)
    soft_skills = _extract_soft_skills(jd)
    tips = _extract_application_tips(sector, seniority, requirements)
    salary = SALARY_RANGES.get(sector, SALARY_RANGES["default"]).get(seniority, "KSh 50,000 – 150,000/month")

    red_flags = []
    if re.search(r"salary negotiable|competitive salary", jd, re.IGNORECASE):
        red_flags.append("Salary listed as 'negotiable' — research the market rate before accepting any offer.")
    if re.search(r"immediate(ly)?|asap|urgent", jd, re.IGNORECASE):
        red_flags.append("'Urgent hire' listed — may signal high turnover or instability. Research the company first.")
    if re.search(r"commission[- ]?based|no basic", jd, re.IGNORECASE):
        red_flags.append("Commission-based or no basic salary mentioned — clarify compensation structure upfront.")

    return {
        "job_title": job_title,
        "employer": employer,
        "sector": sector,
        "seniority": seniority,
        "key_requirements": requirements if requirements else ["See job description for full requirements."],
        "ats_keywords": keywords,
        "soft_skills": soft_skills,
        "salary_estimate": salary,
        "application_tips": tips,
        "red_flags": red_flags,
    }


def _empty_analysis() -> Dict[str, Any]:
    return {
        "job_title": "Position",
        "employer": "Not specified",
        "sector": "General",
        "seniority": "Mid",
        "key_requirements": [],
        "ats_keywords": [],
        "soft_skills": [],
        "salary_estimate": "KSh 50,000 – 150,000/month",
        "application_tips": [],
        "red_flags": [],
    }


def _try_ai_analyze(jd: str) -> Optional[Dict[str, Any]]:
    prompt = f"""Analyze this Kenyan job description.
Return JSON with keys: job_title, employer, sector, seniority, key_requirements (array),
ats_keywords (array of 15-20), soft_skills (array), salary_estimate, application_tips (array), red_flags (array).
JD:\n{jd}"""
    text = _gemini_generate(prompt)
    if not text:
        return None
    try:
        import json as _json
        text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
        result = _json.loads(text)
        if isinstance(result, dict) and "job_title" in result:
            return result
    except Exception:
        pass
    return None


# ════════════════════════════════════════════════════════════════
# SECTION 3: RESUME / CV PARSER
# ════════════════════════════════════════════════════════════════

def extract_text_from_pdf(file_bytes) -> str:
    reader = pypdf.PdfReader(file_bytes)
    texts = []
    for page in reader.pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:
            texts.append("")
    return "\n".join(texts).strip()


def _parse_resume_sections(text: str) -> Dict[str, str]:
    """Split resume text into logical sections."""
    sections = {
        "header": "", "summary": "", "experience": "",
        "education": "", "skills": "", "certifications": "", "other": "",
    }
    section_patterns = {
        "summary": r"(?:professional summary|summary|objective|profile|about me)",
        "experience": r"(?:work experience|experience|employment|career history|professional experience)",
        "education": r"(?:education|academic|qualifications|degrees?)",
        "skills": r"(?:skills?|competencies|expertise|technical skills|core competencies)",
        "certifications": r"(?:certifications?|licen[cs]es?|courses?|training|professional development)",
    }
    lines = text.split("\n")
    current = "header"
    current_content = []

    for line in lines:
        matched = False
        for section_name, pattern in section_patterns.items():
            if re.match(rf"^(?:{pattern})[:\s]*$", line.strip(), re.IGNORECASE):
                sections[current] = "\n".join(current_content).strip()
                current = section_name
                current_content = []
                matched = True
                break
        if not matched:
            current_content.append(line)
    sections[current] = "\n".join(current_content).strip()
    return sections


def _extract_contact_info(text: str) -> Dict[str, str]:
    info = {"name": "", "phone": "", "email": "", "location": "", "linkedin": ""}

    # Email
    m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", text)
    info["email"] = m.group(0) if m else ""

    # Phone (Kenyan formats)
    m = re.search(r"(?:\+254|0)[17]\d{8}", text)
    info["phone"] = m.group(0) if m else ""

    # LinkedIn
    m = re.search(r"linkedin\.com/in/[\w\-]+", text, re.IGNORECASE)
    info["linkedin"] = m.group(0) if m else ""

    # Location
    locations = ["nairobi", "mombasa", "kisumu", "nakuru", "eldoret", "thika", "kenya"]
    for loc in locations:
        if loc in text.lower():
            info["location"] = loc.capitalize() + ", Kenya"
            break

    # Name — try to get from first non-empty, non-contact line
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines[:5]:
        if (
            not re.search(r"[@\d/|]", line)
            and len(line.split()) in (2, 3, 4)
            and not any(w in line.lower() for w in ["curriculum", "vitae", "resume", "cv"])
        ):
            info["name"] = line
            break

    return info


def _extract_skills_from_text(text: str) -> List[str]:
    """Extract skills mentioned in the text."""
    skill_patterns = [
        r"microsoft (?:word|excel|powerpoint|office|access|teams|outlook)",
        r"google (?:workspace|sheets|docs|drive|analytics|ads)",
        r"python|java|javascript|typescript|react|node|angular|vue",
        r"sql|postgresql|mysql|mongodb|sqlite|oracle",
        r"power bi|tableau|qlik|looker|excel|spss|stata|r\b",
        r"aws|azure|gcp|cloud|docker|kubernetes|terraform",
        r"salesforce|sap|oracle|quickbooks|sage|pastel|tally",
        r"photoshop|illustrator|indesign|figma|canva",
        r"project management|agile|scrum|kanban|pmp|prince2",
        r"cpa|acca|cfa|cima|cia|itil|cism|cissp",
        r"french|swahili|kiswahili|arabic|spanish|portuguese",
        r"driving licen[cs]e|class [abc]",
    ]
    found = []
    text_lower = text.lower()
    for pattern in skill_patterns:
        matches = re.findall(pattern, text_lower)
        for m in matches:
            found.append(m.title())
    return list(dict.fromkeys(found))[:20]


# ════════════════════════════════════════════════════════════════
# SECTION 4: CV BUILDER (template engine)
# ════════════════════════════════════════════════════════════════

def rewrite_resume(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """
    Build a professional, ATS-optimised CV from resume text + job analysis.
    Falls back to AI if available.
    """
    if _ai_available():
        ai_result = _try_ai_resume(resume_text, job_analysis)
        if ai_result:
            return ai_result

    return _template_cv(resume_text, job_analysis)


def _try_ai_resume(resume_text: str, job_analysis: Dict[str, Any]) -> Optional[str]:
    keywords = ", ".join(job_analysis.get("ats_keywords", [])[:12])
    job_title = job_analysis.get("job_title", "target role")
    prompt = (
        f"Rewrite this CV as ATS-optimised Markdown for a {job_title} role in Kenya. "
        f"Weave in these keywords: {keywords}. Use STAR bullets with metrics. "
        f"Sections: Name, Contact, Professional Summary, Key Skills, Work Experience, "
        f"Education, Certifications, Volunteer/Projects.\n\nCV:\n{resume_text}"
    )
    return _gemini_generate(prompt)


def _template_cv(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """Template-based professional CV builder."""
    contact = _extract_contact_info(resume_text)
    sections = _parse_resume_sections(resume_text)
    job_title = job_analysis.get("job_title", "Professional")
    sector = job_analysis.get("sector", "General")
    seniority = job_analysis.get("seniority", "Mid")
    keywords = job_analysis.get("ats_keywords", [])
    extracted_skills = _extract_skills_from_text(resume_text)

    name = contact["name"] or "YOUR NAME"
    phone = contact["phone"] or "0700 000 000"
    email = contact["email"] or "your.email@gmail.com"
    location = contact["location"] or "Nairobi, Kenya"
    linkedin = f" | linkedin.com/in/yourprofile" if not contact["linkedin"] else f" | {contact['linkedin']}"

    # Professional summary
    summary = _build_summary(resume_text, sections, job_title, sector, seniority, keywords)

    # Skills — merge extracted + top keywords
    skills = list(dict.fromkeys(extracted_skills + [k for k in keywords[:10] if len(k) > 3]))[:16]
    if not skills:
        skills = ["Microsoft Office", "Communication", "Teamwork", "Problem Solving", "Report Writing"]
    skill_pairs = _format_skills_table(skills)

    # Experience section
    experience = _format_experience(sections["experience"], keywords)

    # Education section
    education = _format_education(sections["education"])

    # Certifications
    certs = _format_certs(sections["certifications"])

    # Other (volunteer, projects)
    other = _format_other(sections["other"])

    # Build CV
    cv = f"""# {name}
{phone} | {email} | {location}{linkedin}

---

## Professional Summary
{summary}

---

## Key Skills
{skill_pairs}

---

## Work Experience
{experience if experience else "_Add your work experience details here._"}

---

## Education
{education if education else "_Add your education details here._"}
"""

    if certs:
        cv += f"\n---\n\n## Certifications & Training\n{certs}\n"

    if other:
        cv += f"\n---\n\n## Volunteer Work & Projects\n{other}\n"

    cv += f"\n---\n*CV tailored for: {job_title} | Generated by AI Career Accelerator Kenya*"
    return cv.strip()


def _build_summary(resume_text: str, sections: Dict, job_title: str,
                   sector: str, seniority: str, keywords: List[str]) -> str:
    """Build a keyword-rich professional summary."""
    # Detect years of experience from text
    years_match = re.search(r"(\d+)\+?\s*(?:years?|yrs?)\s*(?:of)?\s*(?:experience|exp)", resume_text, re.IGNORECASE)
    years = f"{years_match.group(1)}+ years'" if years_match else "Proven"

    # Top 3 keywords for summary
    kw1, kw2, kw3 = (keywords + ["strong analytical skills", "stakeholder management", "results delivery"])[:3]

    seniority_labels = {
        "Entry": "motivated graduate", "Mid": "results-driven professional",
        "Senior": "seasoned professional", "Manager": "strategic leader",
    }
    label = seniority_labels.get(seniority, "results-driven professional")

    existing_summary = sections.get("summary", "").strip()
    if existing_summary and len(existing_summary) > 50:
        # Enhance existing summary with keywords
        first_sentences = ". ".join(existing_summary.split(".")[:2]) + "."
        return (
            f"{first_sentences} "
            f"Skilled in {kw1}, {kw2}, and {kw3}. "
            f"Seeking to bring these competencies to a challenging {job_title} role "
            f"and deliver measurable impact in the {sector} sector."
        )

    return (
        f"{years} experience {label} with a strong track record in {kw1} and {kw2}. "
        f"Adept at {kw3} within the Kenyan {sector} landscape. "
        f"Committed to delivering measurable results and driving organisational success. "
        f"Seeking a {job_title} role where I can leverage my expertise to create tangible impact."
    )


def _format_skills_table(skills: List[str]) -> str:
    """Format skills in a 2-column markdown table."""
    pairs = []
    mid = (len(skills) + 1) // 2
    col1 = skills[:mid]
    col2 = skills[mid:]
    for i in range(max(len(col1), len(col2))):
        a = f"✓ {col1[i]}" if i < len(col1) else ""
        b = f"✓ {col2[i]}" if i < len(col2) else ""
        pairs.append(f"| {a:<35} | {b:<35} |")
    header = f"| {'Skill':<35} | {'Skill':<35} |\n|{'-'*37}|{'-'*37}|"
    return header + "\n" + "\n".join(pairs)


def _format_experience(exp_text: str, keywords: List[str]) -> str:
    """Format and enhance work experience section."""
    if not exp_text or len(exp_text) < 20:
        return ""

    # Try to detect job entries
    lines = exp_text.split("\n")
    formatted = []
    kw_lower = [k.lower() for k in keywords]

    for line in lines:
        line = line.strip()
        if not line:
            formatted.append("")
            continue

        # Enhance bullet points with action verbs
        if line.startswith(("-", "•", "*", "·")):
            content = line.lstrip("-•*· ").strip()
            # Add action verb if missing
            if content and not any(content.startswith(v) for v_list in ACTION_VERBS.values() for v in v_list):
                verb = random.choice(ACTION_VERBS["achievement"] + ACTION_VERBS["development"])
                content = f"{verb} {content[0].lower()}{content[1:]}"
            formatted.append(f"- {content}")
        else:
            formatted.append(line)

    return "\n".join(formatted)


def _format_education(edu_text: str) -> str:
    if not edu_text or len(edu_text) < 10:
        return ""
    lines = [l.strip() for l in edu_text.split("\n") if l.strip()]
    return "\n".join(lines)


def _format_certs(cert_text: str) -> str:
    if not cert_text or len(cert_text) < 5:
        return ""
    lines = [l.strip() for l in cert_text.split("\n") if l.strip()]
    return "\n".join(f"- {l.lstrip('-•* ')}" for l in lines)


def _format_other(other_text: str) -> str:
    if not other_text or len(other_text) < 10:
        return ""
    lines = [l.strip() for l in other_text.split("\n") if l.strip()]
    return "\n".join(lines[:20])


# ════════════════════════════════════════════════════════════════
# SECTION 5: COVER LETTER ENGINE
# ════════════════════════════════════════════════════════════════

COVER_LETTER_OPENINGS = [
    "Having followed {employer}'s work in {sector} for some time, I was immediately drawn to this {job_title} opportunity.",
    "The {job_title} role at {employer} aligns precisely with my professional trajectory and passion for {sector}.",
    "When I came across the {job_title} vacancy at {employer}, I recognised it as the ideal next step in my {sector} career.",
    "Your {job_title} role caught my attention because it combines the technical depth and sector impact I have been actively seeking in my next role.",
]

COVER_LETTER_CLOSINGS = [
    "I would welcome the opportunity to discuss how my background aligns with your team's goals. I am available at your earliest convenience for an interview.",
    "I am confident that my skills and enthusiasm for {sector} make me a strong candidate. I look forward to the possibility of contributing to {employer}.",
    "Thank you for considering my application. I would be delighted to elaborate on how I can add value to your organisation in an interview.",
]


def generate_cover_letter(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    """Generate a tailored cover letter. Uses AI if available."""
    if _ai_available():
        ai_result = _try_ai_cover_letter(resume_text, job_analysis)
        if ai_result:
            return ai_result

    return _template_cover_letter(resume_text, job_analysis)


def _try_ai_cover_letter(resume_text: str, job_analysis: Dict[str, Any]) -> Optional[str]:
    job_title = job_analysis.get("job_title", "")
    employer = job_analysis.get("employer", "your organisation")
    reqs = ", ".join(job_analysis.get("key_requirements", [])[:4])
    prompt = (
        f"Write a professional Kenyan cover letter for a {job_title} role at {employer}. "
        f"Address requirements: {reqs}. Use candidate background: {resume_text[:1000]}. "
        f"~350 words. Markdown. Start strong, not with 'I am writing to...'."
    )
    return _gemini_generate(prompt)


def _template_cover_letter(resume_text: str, job_analysis: Dict[str, Any]) -> str:
    contact = _extract_contact_info(resume_text)
    job_title = job_analysis.get("job_title", "this position")
    employer = job_analysis.get("employer", "your organisation")
    sector = job_analysis.get("sector", "the industry")
    seniority = job_analysis.get("seniority", "Mid")
    requirements = job_analysis.get("key_requirements", [])[:3]
    keywords = job_analysis.get("ats_keywords", [])[:4]

    name = contact["name"] or "YOUR NAME"
    phone = contact["phone"] or "0700 000 000"
    email = contact["email"] or "your.email@gmail.com"

    opening = random.choice(COVER_LETTER_OPENINGS).format(
        employer=employer, sector=sector, job_title=job_title
    )
    closing = random.choice(COVER_LETTER_CLOSINGS).format(
        sector=sector, employer=employer
    )

    # Build body paragraphs from requirements
    req_para = ""
    if requirements:
        req1 = requirements[0]
        req2 = requirements[1] if len(requirements) > 1 else "delivering results"
        kw_str = " and ".join(keywords[:2]) if keywords else "relevant technical skills"
        req_para = (
            f"Throughout my career, I have developed strong capabilities in {req1.lower() if len(req1) > 4 else req1}. "
            f"My professional experience has also equipped me with {kw_str}, "
            f"which I believe directly addresses your requirement for {req2.lower() if len(req2) > 4 else req2}. "
            f"I am particularly skilled at translating technical knowledge into tangible business outcomes — "
            f"a quality that I understand is essential in this role."
        )
    else:
        req_para = (
            f"My experience in {sector} has equipped me with a strong foundation in the skills and competencies "
            f"you are seeking. I am adept at working in dynamic environments, managing competing priorities, "
            f"and delivering results that drive organisational success."
        )

    seniority_body = {
        "Entry": (
            "As a recent graduate, I bring fresh perspectives, a strong academic foundation, and the drive "
            "to grow rapidly within a professional environment. I have complemented my studies with practical "
            "experience through internships and volunteer work, developing real-world skills that translate "
            "directly to the demands of this role."
        ),
        "Mid": (
            "Over the course of my career, I have had the opportunity to work on challenging projects that "
            "have sharpened my analytical thinking, stakeholder management, and execution capabilities. "
            "I consistently deliver on objectives while maintaining a focus on quality and continuous improvement."
        ),
        "Senior": (
            "With extensive experience in {sector}, I have a proven track record of leading complex initiatives, "
            "developing high-performing teams, and delivering strategic outcomes. I bring a thoughtful, "
            "data-driven approach to problem-solving that has enabled me to consistently exceed targets."
        ).format(sector=sector),
        "Manager": (
            "Throughout my leadership career, I have built and motivated teams to achieve ambitious goals "
            "in challenging environments. I bring a balance of strategic vision and operational rigour, "
            "enabling me to bridge organisational objectives with day-to-day execution effectively."
        ),
    }.get(seniority, "")

    return f"""[Date]

Dear Hiring Manager,

**Re: Application for {job_title} — {employer}**

{opening}

{req_para}

{seniority_body}

I am attracted to {employer} specifically because of your reputation in the {sector} sector and the calibre of work your team produces. I am eager to bring my skills, dedication, and fresh perspective to your organisation and contribute to your continued success.

{closing}

Yours sincerely,
**{name}**
{phone} | {email}
"""


# ════════════════════════════════════════════════════════════════
# SECTION 6: EMAIL STRATEGY LIBRARY
# ════════════════════════════════════════════════════════════════

def generate_emails(job_analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    """Generate 4 strategic follow-up emails. Uses AI if available."""
    if _ai_available():
        ai_result = _try_ai_emails(job_analysis)
        if ai_result:
            return ai_result

    return _template_emails(job_analysis)


def _try_ai_emails(job_analysis: Dict[str, Any]) -> Optional[List]:
    job_title = job_analysis.get("job_title", "")
    sector = job_analysis.get("sector", "")
    employer = job_analysis.get("employer", "target company")
    prompt = (
        f"Generate 4 strategic job-search emails for a Kenyan candidate targeting {job_title} at {employer} in {sector}. "
        f"Return JSON array with objects: purpose, timing, subject, body. "
        f"Emails: 1) Application follow-up (Day 7), 2) Networking outreach, 3) Post-interview thank you, 4) Rejection recovery. "
        f"Each under 200 words. Professional Kenyan tone."
    )
    text = _gemini_generate(prompt)
    if not text:
        return None
    try:
        text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
        result = json.loads(text)
        if isinstance(result, list) and len(result) >= 2:
            return result
        if isinstance(result, dict):
            for key in ("items", "emails"):
                if key in result and isinstance(result[key], list):
                    return result[key]
    except Exception:
        pass
    return None


def _template_emails(job_analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    job_title = job_analysis.get("job_title", "the position")
    employer = job_analysis.get("employer", "your organisation")
    sector = job_analysis.get("sector", "the sector")

    return [
        {
            "purpose": "Application follow-up",
            "timing": "Day 7 after applying with no response",
            "subject": f"Following Up — {job_title} Application",
            "body": (
                f"Dear Hiring Manager,\n\n"
                f"I hope this message finds you well. I recently submitted my application for the {job_title} role "
                f"at {employer} and wanted to briefly follow up to reiterate my strong interest in the position.\n\n"
                f"I am particularly excited about this opportunity because of {employer}'s impact in the {sector} sector. "
                f"I am confident that my background aligns closely with what you are looking for, and I would welcome "
                f"the chance to discuss this further.\n\n"
                f"Please let me know if you need any additional information. I look forward to hearing from you.\n\n"
                f"Kind regards,\n[Your Name]\n[Your Phone]"
            ),
        },
        {
            "purpose": "Networking outreach (cold email to someone at the company)",
            "timing": "Same day or day after applying",
            "subject": f"Connecting — {job_title} Candidate Interested in {employer}",
            "body": (
                f"Hi [Their First Name],\n\n"
                f"I came across your profile while researching {employer} — I have recently applied for the {job_title} "
                f"role and I am very excited about the opportunity.\n\n"
                f"I would love to learn more about your experience at {employer} and the {sector} team. "
                f"Would you be open to a brief 15-minute conversation at your convenience?\n\n"
                f"No pressure at all — I simply admire the work your team does and would value any insights you could share.\n\n"
                f"Thank you for considering this. I hope to connect soon.\n\n"
                f"Best,\n[Your Name]\n[LinkedIn URL]"
            ),
        },
        {
            "purpose": "Post-interview thank you",
            "timing": "Within 24 hours of your interview",
            "subject": f"Thank You — {job_title} Interview",
            "body": (
                f"Dear [Interviewer's Name],\n\n"
                f"Thank you sincerely for taking the time to meet with me today to discuss the {job_title} role "
                f"at {employer}. It was a pleasure learning more about the team and the exciting work happening "
                f"in your {sector} division.\n\n"
                f"Our conversation reinforced my enthusiasm for this opportunity. I was particularly inspired by "
                f"[specific thing they mentioned — e.g., your focus on X / the team's approach to Y]. "
                f"I am confident that my background in [key skill] positions me well to contribute meaningfully.\n\n"
                f"Please do not hesitate to reach out if you need any additional information. "
                f"I look forward to hearing about the next steps.\n\n"
                f"Kind regards,\n[Your Name]\n[Phone] | [Email]"
            ),
        },
        {
            "purpose": "Rejection recovery (graceful, door-open response)",
            "timing": "Within 48 hours of receiving a rejection",
            "subject": f"Re: {job_title} Application — Thank You",
            "body": (
                f"Dear [Hiring Manager's Name],\n\n"
                f"Thank you for letting me know about your decision regarding the {job_title} role. "
                f"While I am disappointed not to be moving forward, I genuinely appreciate you taking the time "
                f"to review my application and keep me informed.\n\n"
                f"I continue to have great admiration for the work {employer} does in {sector}, and I would love "
                f"to be considered for future opportunities that match my profile. "
                f"If it would be appropriate, I would also welcome any feedback that could help me strengthen "
                f"my future applications.\n\n"
                f"Thank you again for your time and consideration. I hope our paths cross again.\n\n"
                f"Warm regards,\n[Your Name]\n[Phone] | [Email]"
            ),
        },
    ]


# ════════════════════════════════════════════════════════════════
# SECTION 7: INTERVIEW PREP LIBRARY
# ════════════════════════════════════════════════════════════════

COMMON_QA = [
    {
        "question": "Tell me about yourself.",
        "strong_answer": (
            "I am a [your profession] with [X] years of experience in [sector/field]. "
            "I hold a [degree] from [institution], which gave me a strong foundation in [relevant skills]. "
            "In my most recent role at [company], I [key achievement with metric]. "
            "I am particularly drawn to this role because [specific reason related to this employer/role]. "
            "Outside of work, I [brief personal note that adds dimension]."
        ),
        "tip": "Keep this to 90 seconds. Practice it aloud until it sounds natural, not rehearsed.",
    },
    {
        "question": "Why do you want to work at this company?",
        "strong_answer": (
            "I have followed [company]'s work in [sector] for some time and I am particularly impressed by "
            "[specific achievement, product, or initiative]. The culture of [value you admire] resonates strongly "
            "with how I approach my own work. Beyond that, this specific role would allow me to [what it enables you to do], "
            "which is precisely the direction I want to grow in professionally."
        ),
        "tip": "Research the company before the interview. Mention one specific recent news item or achievement.",
    },
    {
        "question": "What is your greatest weakness?",
        "strong_answer": (
            "Early in my career, I found it difficult to delegate tasks — I wanted to ensure everything was done "
            "to a high standard, which sometimes slowed me down. I recognised this pattern and actively worked on it "
            "by [specific action — e.g., working with a mentor, taking a course, or systematically delegating with clear "
            "briefs]. As a result, [positive outcome]. It is still something I remain conscious of, but it has become "
            "much more of a strength than a limitation."
        ),
        "tip": "Choose a real weakness. Make sure your answer shows self-awareness AND a concrete action you took to address it.",
    },
    {
        "question": "Describe a challenge you faced and how you overcame it.",
        "strong_answer": (
            "In my role as [title] at [company], we faced [specific challenge — e.g., a project deadline that moved up "
            "by three weeks due to a client request]. I [action — e.g., immediately reorganised the team's priorities, "
            "identified the critical path, and negotiated scope with the client]. As a result, we [outcome — e.g., "
            "delivered the project on the new timeline, which led to a contract extension worth KSh X]."
        ),
        "tip": "Use the STAR method: Situation → Task → Action → Result. Always end with a measurable result.",
    },
    {
        "question": "Where do you see yourself in 5 years?",
        "strong_answer": (
            "In five years, I see myself having grown into a [senior/lead/specialist] role within [sector]. "
            "My immediate goal is to master [specific skill or area relevant to this job] and demonstrate my ability "
            "to [contribute to team goal]. Over time, I would love to take on more responsibility — whether leading "
            "projects or mentoring junior team members. Ultimately, I want to be someone who has made a real, "
            "measurable difference to [employer or sector]."
        ),
        "tip": "Show ambition but make it realistic and tied to this company's growth trajectory.",
    },
]

SECTOR_QA: Dict[str, List[Dict]] = {
    "Banking & Finance": [
        {
            "question": "How do you manage financial risk in your work?",
            "strong_answer": (
                "I take a structured approach to risk management: I identify potential financial risks early, "
                "quantify their impact using [tools — e.g., risk matrices, financial models], and put in place "
                "mitigating controls. For example, in my previous role I [specific example — e.g., flagged a KYC "
                "gap that could have exposed the bank to regulatory risk, and worked with compliance to implement "
                "a remediation process that resolved 98% of cases within 30 days]."
            ),
            "tip": "Mention specific tools: AML software, reconciliation systems, risk matrices.",
        },
        {
            "question": "Walk me through a time you identified an error or discrepancy and what you did.",
            "strong_answer": (
                "While reviewing the monthly reconciliation at [company], I noticed a KSh [X] discrepancy between "
                "two ledgers. I immediately flagged it to my supervisor, traced the source to a journal entry error, "
                "and corrected it before month-end reporting. I then proposed a two-person sign-off process for "
                "future journal entries, which was adopted and reduced similar errors by [%]."
            ),
            "tip": "Banks love candidates who catch errors proactively and suggest systemic fixes.",
        },
    ],
    "NGO & Development": [
        {
            "question": "How do you approach monitoring and evaluation (M&E)?",
            "strong_answer": (
                "I believe M&E is most effective when it is built into project design from the start, not added "
                "as an afterthought. My approach involves developing a clear logframe with measurable indicators, "
                "establishing baseline data, and setting up data collection systems. In my previous role, I developed "
                "an M&E framework for a [project name] that tracked [X] indicators across [Y] counties, enabling "
                "the programme team to adjust interventions in real time and report accurate results to the donor."
            ),
            "tip": "Mention specific donor reporting formats you have used (e.g., USAID PEPFAR, EU logframe, DFID ROLF).",
        },
        {
            "question": "How do you work with communities and build trust?",
            "strong_answer": (
                "I always start by listening — not arriving with solutions but with questions. In [project], I spent "
                "the first two weeks conducting community dialogues to understand local power structures, existing "
                "initiatives, and community priorities. This built trust and ensured our intervention complemented "
                "rather than duplicated existing efforts. As a result, community participation rates reached [X]% "
                "against a target of [Y]%."
            ),
            "tip": "NGOs want to see you understand community ownership and participation principles.",
        },
    ],
    "Technology & IT": [
        {
            "question": "Describe a technical project you are proud of.",
            "strong_answer": (
                "I built [project name] — a [brief description] using [tech stack]. The main challenge was [technical "
                "problem], which I solved by [approach]. The result was [outcome — e.g., a 40% reduction in processing "
                "time, serving 10,000 daily active users, reducing infrastructure cost by KSh X per month]. "
                "I can walk you through the architecture if you would like."
            ),
            "tip": "Have a GitHub link or demo ready. Quantify the impact of your technical work.",
        },
        {
            "question": "How do you stay up to date with technology?",
            "strong_answer": (
                "I follow [specific sources — e.g., Hacker News, TechCrunch Africa, Dev.to], take regular courses "
                "on [Coursera/Udemy/YouTube], and participate in developer communities like [Nairobi Dev, Kenya "
                "Python Users Group, Andela alumni]. I recently completed [specific course or certification] and "
                "applied what I learned to [specific project or task]."
            ),
            "tip": "Name actual platforms and communities. Vague answers like 'I read blogs' do not stand out.",
        },
    ],
}

QUESTIONS_TO_ASK = {
    "default": [
        "What does success look like for this role in the first 90 days?",
        "Can you tell me about the team I would be working with most closely?",
        "What are the biggest challenges facing the team right now?",
        "How does the organisation support professional development and continuous learning?",
        "What do you enjoy most about working here?",
    ],
    "Banking & Finance": [
        "How does the risk and compliance function interact with this team day-to-day?",
        "What systems and tools does the team primarily use?",
        "What are the key regulatory developments you are preparing for in the next 12 months?",
    ],
    "NGO & Development": [
        "What is the funding landscape for this programme, and how stable is it?",
        "How much autonomy does this role have in programme design versus following donor requirements?",
        "How does the organisation handle difficult conversations with donors or implementing partners?",
    ],
    "Technology & IT": [
        "What does the engineering culture look like here — how do you handle code reviews, deployments, and technical debt?",
        "What is the tech stack and how often does it change?",
        "What are the most exciting technical challenges the team is working on right now?",
    ],
}

FIRST_IMPRESSION_TIPS = [
    "Arrive 10-15 minutes early (not earlier). For a Zoom interview, log in 5 minutes before and test audio/video the night before.",
    "Research the organisation thoroughly: recent news, key staff, annual reports, and their social media presence.",
    "Bring printed copies of your CV (2-3 copies), your certificates, and a notepad and pen.",
    "Dress one level above what you expect the office dress code to be.",
    "Start with a firm handshake, good eye contact, and a genuine smile. First impressions are made in the first 30 seconds.",
    "Turn your phone off completely — not on silent — before entering the interview room.",
    "When answering questions, pause for 2-3 seconds before speaking. It shows thoughtfulness, not hesitation.",
]

SALARY_SCRIPTS: Dict[str, str] = {
    "default": (
        "When asked about salary expectations, research the market range first using Glassdoor Kenya, LinkedIn Salary, "
        "and industry contacts. Then respond: 'Based on my research into the market for this role in Nairobi, "
        "I was expecting a range of [KSh X – Y]. Is that aligned with what you had budgeted?' "
        "This anchors high, invites them to confirm the budget, and opens a collaborative conversation. "
        "Never be the first to give a hard number. Always negotiate — the first offer is rarely the final offer. "
        "Once you have an offer, you can negotiate non-salary benefits: extra leave days, flexible working, "
        "professional development budget, or a performance review at 6 months."
    ),
}


def generate_interview_prep(resume_text: str, job_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Generate interview prep guide. Uses AI if available."""
    if _ai_available():
        ai_result = _try_ai_interview_prep(resume_text, job_analysis)
        if ai_result:
            return ai_result

    return _template_interview_prep(resume_text, job_analysis)


def _try_ai_interview_prep(resume_text: str, job_analysis: Dict[str, Any]) -> Optional[Dict]:
    job_title = job_analysis.get("job_title", "")
    sector = job_analysis.get("sector", "")
    reqs = ", ".join(job_analysis.get("key_requirements", [])[:5])
    prompt = (
        f"Create interview prep for a Kenyan candidate for {job_title} in {sector}. "
        f"Requirements: {reqs}. Candidate background: {resume_text[:800]}. "
        f"Return JSON with: common_questions, technical_questions, behavioural_questions (each array of objects: "
        f"question, strong_answer, tip), questions_to_ask_employer (array of strings), "
        f"salary_negotiation_script (string), first_impression_tips (array of strings)."
    )
    text = _gemini_generate(prompt)
    if not text:
        return None
    try:
        text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
        result = json.loads(text)
        if isinstance(result, dict) and "common_questions" in result:
            return result
    except Exception:
        pass
    return None


def _template_interview_prep(resume_text: str, job_analysis: Dict[str, Any]) -> Dict[str, Any]:
    sector = job_analysis.get("sector", "default")
    job_title = job_analysis.get("job_title", "the role")

    sector_qa = SECTOR_QA.get(sector, [])
    if not sector_qa:
        # Try partial match
        for key in SECTOR_QA:
            if any(word in sector for word in key.split()):
                sector_qa = SECTOR_QA[key]
                break

    questions_to_ask = QUESTIONS_TO_ASK.get(sector, []) + QUESTIONS_TO_ASK["default"]

    return {
        "common_questions": COMMON_QA,
        "technical_questions": sector_qa if sector_qa else [
            {
                "question": f"What specific experience do you have that is relevant to this {job_title} role?",
                "strong_answer": (
                    "In my role at [company], I [specific relevant experience]. "
                    "I also [second experience]. These have given me a strong foundation in [key skills for this role]."
                ),
                "tip": "Map your experience explicitly to the job requirements. Do not make the interviewer guess the connection.",
            },
        ],
        "behavioural_questions": [
            {
                "question": "Tell me about a time you worked under pressure and met a tight deadline.",
                "strong_answer": (
                    "In [specific situation], we had [describe pressure/deadline]. "
                    "I [action taken — prioritised, delegated, worked extra hours, negotiated scope]. "
                    "As a result, [specific measurable outcome]. "
                    "I learned that [lesson — e.g., proactive communication prevents most deadline crises]."
                ),
                "tip": "STAR: Situation, Task, Action, Result. Always end with a result.",
            },
            {
                "question": "Describe a time you disagreed with your manager or a colleague.",
                "strong_answer": (
                    "My manager and I had different views on [specific situation]. "
                    "I approached the conversation privately, clearly explaining my perspective and the data supporting it: "
                    "[brief reasoning]. We had a constructive discussion and ultimately [outcome — either they agreed, "
                    "or we found a middle ground, or I deferred and learned X]. "
                    "The relationship remained strong and [positive result]."
                ),
                "tip": "Show you can disagree professionally. Avoid making yourself sound like a pushover or a rebel.",
            },
            {
                "question": "Give an example of when you had to manage multiple priorities simultaneously.",
                "strong_answer": (
                    "In [role], I was simultaneously responsible for [task A], [task B], and [task C]. "
                    "I used [method — e.g., a prioritisation matrix, project management software, weekly planning] "
                    "to track progress. I communicated proactively with all stakeholders about timelines. "
                    "All three were completed [on time / within X days / under budget] and [positive outcome]."
                ),
                "tip": "Specific tools and methods matter here. Mention Trello, Asana, Excel, or whatever you used.",
            },
            {
                "question": "Tell me about a time you went above and beyond for a customer or stakeholder.",
                "strong_answer": (
                    "A [client/beneficiary/stakeholder] approached me with [problem], which was outside my usual scope. "
                    "I [what you did that was extra — e.g., stayed late, connected them to the right person, "
                    "followed up proactively, solved the issue personally]. "
                    "The result was [specific outcome — e.g., they renewed their contract, wrote a commendation, "
                    "the project continued successfully]."
                ),
                "tip": "This tests customer service orientation and initiative. Show a concrete extra step you took.",
            },
        ],
        "questions_to_ask_employer": questions_to_ask[:5],
        "salary_negotiation_script": SALARY_SCRIPTS.get(sector, SALARY_SCRIPTS["default"]),
        "first_impression_tips": FIRST_IMPRESSION_TIPS,
    }


# ════════════════════════════════════════════════════════════════
# SECTION 8: LINKEDIN OPTIMIZER
# ════════════════════════════════════════════════════════════════

def generate_linkedin_optimization(candidate_text: str, job_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Generate LinkedIn optimization. Uses AI if available."""
    if _ai_available():
        ai_result = _try_ai_linkedin(candidate_text, job_analysis)
        if ai_result:
            return ai_result

    return _template_linkedin(candidate_text, job_analysis)


def _try_ai_linkedin(candidate_text: str, job_analysis: Dict[str, Any]) -> Optional[Dict]:
    job_title = job_analysis.get("job_title", "")
    keywords = ", ".join(job_analysis.get("ats_keywords", [])[:10])
    prompt = (
        f"Optimize LinkedIn profile for Kenyan candidate targeting {job_title}. "
        f"Keywords: {keywords}. Background: {candidate_text[:800]}. "
        f"Return JSON: headline, about, experience_bullets (array), featured_skills (array), "
        f"connection_message (string, 300 chars max), inmail_template (string, 150 words max)."
    )
    text = _gemini_generate(prompt)
    if not text:
        return None
    try:
        text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
        result = json.loads(text)
        if isinstance(result, dict) and "headline" in result:
            return result
    except Exception:
        pass
    return None


def _template_linkedin(candidate_text: str, job_analysis: Dict[str, Any]) -> Dict[str, Any]:
    contact = _extract_contact_info(candidate_text)
    job_title = job_analysis.get("job_title", "Professional")
    sector = job_analysis.get("sector", "Kenya")
    keywords = job_analysis.get("ats_keywords", [])[:6]
    skills = _extract_skills_from_text(candidate_text)

    kw_str = " | ".join(keywords[:4]) if keywords else "Strategic Thinking | Results-Driven"
    top_skills = list(dict.fromkeys((skills + keywords)))[:10]

    return {
        "headline": f"{job_title} | {kw_str} | {sector} | Open to Opportunities",
        "about": (
            f"I am a {sector} professional with a passion for delivering measurable impact and driving "
            f"organisational success in Kenya and the broader East African region.\n\n"
            f"My expertise spans {', '.join(keywords[:3]) if keywords else 'my professional field'}, "
            f"and I thrive in environments that demand both strategic thinking and hands-on execution.\n\n"
            f"What drives me: I believe that every challenge is an opportunity to innovate, and I bring "
            f"that mindset to every project and team I work with.\n\n"
            f"Currently [employed at / looking for opportunities in] the {sector} space. "
            f"If you are looking for someone who combines technical depth with a genuine commitment to results, "
            f"let's connect.\n\n"
            f"📩 Reach me at {contact['email'] or '[your email]'}"
        ),
        "experience_bullets": [
            f"Delivered [key project] resulting in [measurable outcome] — [metric].",
            f"Led cross-functional team of [X] to implement [initiative], reducing [cost/time] by [%].",
            f"Developed and maintained [system/process] that improved [outcome] by [metric].",
            f"Collaborated with [stakeholder type] to design [deliverable] adopted across [scope].",
            f"Recognised for [achievement] — [award/commendation/promotion] in [year].",
        ],
        "featured_skills": top_skills,
        "connection_message": (
            f"Hi [Name], I came across your profile while researching {sector} professionals in Kenya. "
            f"I am a {job_title} with a strong background in {', '.join(keywords[:2]) if keywords else 'my field'}. "
            f"I would love to connect and learn from your experience. Looking forward to it!"
        )[:300],
        "inmail_template": (
            f"Dear [Name],\n\n"
            f"I recently came across your profile and was impressed by your work in {sector} at [their company]. "
            f"I am actively seeking {job_title} opportunities in Nairobi and would love to hear any advice "
            f"you might have about breaking into [their company/sector].\n\n"
            f"I have [X years] of experience in {', '.join(keywords[:2]) if keywords else 'the field'} and "
            f"I believe I could add real value to a team like yours. "
            f"Would you be open to a brief 15-minute chat?\n\n"
            f"Thank you for your time.\n[Your name]"
        ),
    }


# ════════════════════════════════════════════════════════════════
# SECTION 9: GAP ANALYSER
# ════════════════════════════════════════════════════════════════

def generate_gap_analysis(resume_text: str, job_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Keyword-matching gap analysis. Uses AI if available."""
    if _ai_available():
        ai_result = _try_ai_gap(resume_text, job_analysis)
        if ai_result:
            return ai_result

    return _template_gap_analysis(resume_text, job_analysis)


def _try_ai_gap(resume_text: str, job_analysis: Dict[str, Any]) -> Optional[Dict]:
    reqs = json.dumps(job_analysis.get("key_requirements", []))
    kws = json.dumps(job_analysis.get("ats_keywords", []))
    prompt = (
        f"Gap analysis for Kenyan candidate. "
        f"JD requirements: {reqs}. Keywords needed: {kws}. Profile: {resume_text[:800]}. "
        f"Return JSON: match_score (0-100 int), strengths (array), gaps (array), "
        f"quick_wins (array), thirty_day_plan (array), keywords_missing (array)."
    )
    text = _gemini_generate(prompt)
    if not text:
        return None
    try:
        text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
        result = json.loads(text)
        if isinstance(result, dict) and "match_score" in result:
            return result
    except Exception:
        pass
    return None


def _template_gap_analysis(resume_text: str, job_analysis: Dict[str, Any]) -> Dict[str, Any]:
    resume_lower = resume_text.lower()
    keywords = job_analysis.get("ats_keywords", [])
    requirements = job_analysis.get("key_requirements", [])

    # Keyword match score
    matched_kws = [kw for kw in keywords if kw.lower() in resume_lower]
    missing_kws = [kw for kw in keywords if kw.lower() not in resume_lower]

    kw_score = (len(matched_kws) / max(len(keywords), 1)) * 100

    # Requirement match score
    req_matches = sum(1 for r in requirements if any(
        word in resume_lower for word in r.lower().split() if len(word) > 4
    ))
    req_score = (req_matches / max(len(requirements), 1)) * 100

    overall_score = int((kw_score * 0.6 + req_score * 0.4))
    overall_score = min(max(overall_score, 5), 95)  # Cap between 5-95

    # Strengths (what the candidate does have)
    strengths = []
    if matched_kws:
        strengths.append(f"Your profile already contains {len(matched_kws)} of {len(keywords)} key ATS keywords for this role.")
    for kw in matched_kws[:3]:
        strengths.append(f"Strong signal for '{kw}' — this keyword appears in your profile.")
    if not strengths:
        strengths = ["You have submitted an application — that takes initiative."]

    # Gaps
    gaps = []
    if missing_kws:
        gaps.append(f"{len(missing_kws)} keywords from the JD are missing from your CV: {', '.join(missing_kws[:5])}.")
    for req in requirements[:3]:
        req_words = [w for w in req.lower().split() if len(w) > 5]
        if not any(w in resume_lower for w in req_words):
            gaps.append(f"Requirement not clearly addressed in your CV: '{req[:80]}'")
    if not gaps:
        gaps = ["Minor gaps only — focus on quantifying your achievements more specifically."]

    # Quick wins
    quick_wins = [
        f"Add these missing keywords naturally into your CV: {', '.join(missing_kws[:5])}." if missing_kws else "Add more specific metrics to your bullet points (numbers, percentages, KSh values).",
        "Ensure your Professional Summary contains at least 3-4 of the JD keywords.",
        "Review your bullet points — each one should follow the format: [Action verb] + [what you did] + [result with metric].",
        "If you have a LinkedIn profile, update it to match the keywords in your updated CV.",
    ]

    thirty_day_plan = [
        f"Week 1: Rewrite your CV using the AI Career Accelerator tool for this specific role and apply.",
        f"Week 1: Apply to this role and 5-10 similar roles on LinkedIn, BrighterMonday, and Fuzu.",
        f"Week 2: Research {len(missing_kws)} missing skills and add any you actually have to your CV.",
        f"Week 2: Reach out to 3 people in {job_analysis.get('sector', 'your target industry')} on LinkedIn.",
        f"Week 3: Follow up on all applications sent in week 1. Send personalised follow-up emails.",
        f"Week 4: Apply to 10 more roles. Track everything in a simple spreadsheet.",
    ]

    return {
        "match_score": overall_score,
        "strengths": strengths[:5],
        "gaps": gaps[:5],
        "quick_wins": quick_wins,
        "thirty_day_plan": thirty_day_plan,
        "keywords_missing": missing_kws[:10],
    }

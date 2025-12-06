# app.py
import json
import re
import uuid
from io import BytesIO

import os
import streamlit as st
from docx import Document
from fpdf import FPDF

from agents import (
    extract_text_from_pdf,
    analyze_job,
    rewrite_resume,
    generate_cover_letter,
    generate_emails,
)

# Payments DB (no IntaSend now, just manual “paid” flag)
from payments_db import init_db, is_user_paid, mark_user_paid

# ============================================================
# One-time initialization: DB only
# ============================================================

init_db()

# ============================================================
# Session / User ID helpers
# ============================================================

def get_or_create_user_id() -> str:
    """
    Generate a persistent user_id for this browser.

    - Stored in st.session_state["user_id"]
    - Also persisted in URL query params using st.query_params
    - Best-effort non-HTTP-only cookie set via JS
    """
    # If already in session, return it
    if "user_id" in st.session_state and st.session_state["user_id"]:
        return st.session_state["user_id"]

    # Read from URL query params (new Streamlit API)
    query_params = st.query_params
    existing_uid = query_params.get("user_id")

    if existing_uid:
        user_id = existing_uid
    else:
        # Generate new UUID
        user_id = str(uuid.uuid4())
        # Update URL query params
        st.query_params.update({"user_id": user_id})

    # Persist in session
    st.session_state["user_id"] = user_id

    # Best-effort cookie (not httpOnly, but better than nothing)
    st.markdown(
        f"""
        <script>
        (function() {{
            const cookies = document.cookie || "";
            if (!cookies.includes("user_id=")) {{
                const expires = new Date();
                expires.setFullYear(expires.getFullYear() + 1);
                document.cookie =
                    "user_id={user_id};expires=" +
                    expires.toUTCString() +
                    ";path=/;SameSite=Lax";
            }}
        }})();
        </script>
        """,
        unsafe_allow_html=True,
    )

    return user_id


# ============================================================
# Session State Initialization
# ============================================================

def init_state() -> None:
    """
    Initialize Streamlit session state with default values
    for all application artifacts.
    """
    defaults = {
        "resume_text": "",
        "job_description": "",
        "job_analysis_json": {},
        "final_resume_text": "",
        "cover_letter_text": "",
        "follow_up_emails": [],
        "last_uploaded_filename": "",      # track last uploaded resume file name
        "resume_input_mode": "Upload PDF Resume",  # resume input mode
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ============================================================
# CONVERSION UTILITIES
# ============================================================

def _add_markdown_runs_to_paragraph(paragraph, text: str) -> None:
    """
    Add runs to a python-docx paragraph, applying bold to segments wrapped in **...**.
    Example: "Hello **World**" -> "Hello " (normal) + "World" (bold).
    """
    parts = re.split(r"(\*\*.*?\*\*)", text)
    for part in parts:
        if not part:
            continue
        bold_match = re.fullmatch(r"\*\*(.*?)\*\*", part)
        if bold_match:
            run_text = bold_match.group(1)
            run = paragraph.add_run(run_text)
            run.bold = True
        else:
            paragraph.add_run(part)


def to_docx(markdown_text: str) -> BytesIO:
    """
    Convert a Markdown-like string into a DOCX file.
    """
    doc = Document()

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip("\n")

        # Blank line -> empty paragraph for spacing
        if not line.strip():
            doc.add_paragraph("")
            continue

        stripped = line.lstrip()

        # Heading level 2 (###)
        if stripped.startswith("### "):
            text = stripped[4:]
            p = doc.add_paragraph()
            p.style = "Heading 2"
            _add_markdown_runs_to_paragraph(p, text)

        # Heading level 1 (##)
        elif stripped.startswith("## "):
            text = stripped[3:]
            p = doc.add_paragraph()
            p.style = "Heading 1"
            _add_markdown_runs_to_paragraph(p, text)

        # Bullet points (- or *)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            text = stripped[2:]
            p = doc.add_paragraph(style="List Bullet")
            _add_markdown_runs_to_paragraph(p, text)

        # Normal paragraph
        else:
            p = doc.add_paragraph()
            _add_markdown_runs_to_paragraph(p, stripped)

    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def _strip_markdown_bold(text: str) -> str:
    """Remove **bold** markers for PDF rendering."""
    return text.replace("**", "")


def to_pdf(markdown_text: str) -> bytes:
    """
    Convert a Markdown-like string into a styled PDF using fpdf2.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)

    # Some fpdf versions use .epw, older ones don't; guard it
    effective_width = getattr(pdf, "epw", pdf.w - 2 * pdf.l_margin)

    def sanitize(text: str) -> str:
        """
        Make text safe for the built-in Helvetica font (Latin-1 only).
        """
        text = _strip_markdown_bold(text)
        text = text.replace("\t", "    ")

        replacements = {
            "“": '"',
            "”": '"',
            "„": '"',
            "‟": '"',
            "‘": "'",
            "’": "'",
            "‚": "'",
            "–": "-",
            "—": "-",
            "…": "...",
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)

        text = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", text)
        text = text.encode("latin-1", "replace").decode("latin-1")
        return text

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip("\n")

        if not line.strip():
            pdf.set_x(pdf.l_margin)
            pdf.ln(4)
            continue

        stripped = line.lstrip()

        if stripped.startswith("### "):
            text = sanitize(stripped[4:])
            pdf.set_text_color(0, 0, 128)
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(effective_width, 8, text)
            pdf.ln(2)

        elif stripped.startswith("## "):
            text = sanitize(stripped[3:])
            pdf.set_text_color(0, 0, 128)
            pdf.set_font("Helvetica", "B", 16)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(effective_width, 8, text)
            pdf.ln(2)

        elif stripped.startswith("- ") or stripped.startswith("* "):
            text = sanitize(stripped[2:])
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "", 12)
            bullet_indent = 4
            pdf.set_x(pdf.l_margin + bullet_indent)
            pdf.multi_cell(effective_width - bullet_indent, 6, "- " + text)

        else:
            text = sanitize(stripped)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "", 12)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(effective_width, 6, text)

    # fpdf2 >= 2.7.9 returns a bytearray for dest="S"
    result = pdf.output(dest="S")
    if isinstance(result, bytes):
        return result
    return result.encode("latin-1")


# ============================================================
# Payment / Premium UI Helpers (NO IntaSend)
# ============================================================

def premium_download_section(
    title: str,
    markdown_content: str,
    filename_base: str,
    amount: int = 1000,
    *,
    user_is_paid: bool,
    user_id: str,
) -> None:
    """
    Reusable premium section:

    - Copy-protected preview of markdown_content
    - If user is paid -> show 3 download buttons (MD, DOCX, PDF)
    - If not paid -> show WhatsApp instructions
    """
    if not markdown_content.strip():
        st.info(f"Your {title.lower()} will appear here after you run the analysis in the Setup tab.")
        return

    st.markdown(
        f"Below is your **{title}**. "
        "Preview is free. To download in any format, unlock Premium access."
    )

    # Copy-protected preview wrapper
    st.markdown('<div class="no-copy-container">', unsafe_allow_html=True)
    st.markdown(markdown_content)
    st.markdown("</div>", unsafe_allow_html=True)

    col_md, col_docx, col_pdf = st.columns(3)

    if user_is_paid:
        # Premium: ALL downloads (Markdown, DOCX, PDF)
        docx_bytes = to_docx(markdown_content)
        pdf_bytes = to_pdf(markdown_content)

        with col_md:
            st.download_button(
                label="⬇️ Download as Markdown",
                data=markdown_content,
                file_name=f"{filename_base}.md",
                mime="text/markdown",
            )

        with col_docx:
            st.download_button(
                label="⬇️ Download as Word (DOCX)",
                data=docx_bytes,
                file_name=f"{filename_base}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

        with col_pdf:
            st.download_button(
                label="⬇️ Download as PDF",
                data=pdf_bytes,
                file_name=f"{filename_base}.pdf",
                mime="application/pdf",
            )
    else:
        # Manual WhatsApp flow
        with col_docx:
            st.markdown("### 🔓 Premium Unlock (Manual)")
            st.write(f"To unlock **all {title.lower()} downloads** (Markdown, Word, PDF) for:")
            st.write(f"**KES {amount:,}**")

            whatsapp_number = st.secrets.get("WHATSAPP_NUMBER", "254722285538")
            whatsapp_message = (
                "Hi, I want to unlock AI Career Accelerator downloads. "
                f"My user_id is: {user_id}"
            )
            whatsapp_link = (
                f"https://wa.me/{whatsapp_number}?text={whatsapp_message.replace(' ', '%20')}"
            )

            st.write("1. Click the button below to message me on WhatsApp.")
            st.write("2. Pay KES 1,000 via M-Pesa manually.")
            st.write("3. I will mark your account as paid, then you refresh this page.")

            st.link_button("💬 Open WhatsApp & Pay", whatsapp_link)

            st.caption(
                f"Your user ID (send this to me on WhatsApp): `{user_id}`"
            )

        with col_pdf:
            st.info(f"After I mark your user as paid, refresh this page and downloads will unlock automatically.")


# ============================================================
# Main App
# ============================================================

st.set_page_config(page_title="AI Career Accelerator", layout="wide")
init_state()

# Ensure we have a persistent user id
user_id = get_or_create_user_id()
user_is_paid = is_user_paid(user_id)

# ====== Copy Protection CSS (Preview Only) ==================
st.markdown(
    """
    <style>
    /* A reusable "no-copy" wrapper for preview content */
    .no-copy-container, .no-copy-container * {
        -webkit-user-select: none !important;  /* Safari/Chrome */
        -moz-user-select: none !important;     /* Firefox */
        -ms-user-select: none !important;      /* IE/Edge */
        user-select: none !important;
        -webkit-touch-callout: none;           /* iOS long-press */
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🚀 AI Career Accelerator")
st.caption("Upload your resume, paste a job description, and generate a complete application kit in minutes.")
st.info(f"Your user ID (for payment verification): `{user_id}`")

# ============================================================
# Sidebar - Resume Input
# ============================================================

with st.sidebar:
    st.header("1. Upload & Configure")

    input_mode = st.radio(
        "How do you want to provide your details?",
        ["Upload PDF Resume", "Enter Text Manually"],
        key="resume_input_mode",
    )

    if input_mode == "Upload PDF Resume":
        uploaded_resume = st.file_uploader(
            "Upload your Resume (PDF)",
            type=["pdf"],
            help="Only machine-readable PDF resumes are supported (no scanned images).",
        )

        if uploaded_resume is not None:
            current_filename = getattr(uploaded_resume, "name", "")

            should_extract = (
                not st.session_state.resume_text.strip()
                or current_filename != st.session_state.last_uploaded_filename
            )

            if should_extract:
                with st.spinner("Extracting text from resume..."):
                    extracted_text = extract_text_from_pdf(uploaded_resume)

                if not extracted_text.strip():
                    st.error(
                        "I couldn't extract any text from this PDF. "
                        "Please ensure it's not a scanned image or try another file."
                    )
                    st.session_state.resume_text = ""
                    st.session_state.last_uploaded_filename = ""
                else:
                    st.session_state.resume_text = extracted_text
                    st.session_state.last_uploaded_filename = current_filename
                    st.success(f"Resume '{current_filename}' loaded and parsed successfully!")

        if st.session_state.resume_text.strip():
            st.caption("✅ Resume text is loaded. You can switch to manual mode to tweak it if you like.")

    else:
        st.text_area(
            "Enter your resume details",
            key="resume_text",
            height=250,
            placeholder="Paste your LinkedIn About section or rough work history here...",
        )
        st.caption("Tip: A rough outline is enough. The AI will polish it into a professional resume.")

# ============================================================
# Main Tabs
# ============================================================

tab_setup, tab_resume, tab_cover, tab_emails, tab_admin = st.tabs(
    ["Setup", "Resume", "Cover Letter", "Email Strategy", "Admin"]
)

# ------------------------------------------------------------
# TAB 1: Setup (Job Description & Analysis)
# ------------------------------------------------------------
with tab_setup:
    st.header("2. Job Description & Analysis")

    st.text_area(
        "Paste the full Job Description here",
        key="job_description",
        height=350,
        placeholder="Paste the complete job posting...",
    )

    col_btn, col_status = st.columns([1, 3])

    with col_btn:
        analyze_btn = st.button(
            "Analyze Job & Generate Kit",
            type="primary",
            use_container_width=True,
        )

    if analyze_btn:
        if not st.session_state.resume_text.strip():
            st.error("Please provide your resume details (upload a PDF or enter text manually).")
        elif not st.session_state.job_description.strip():
            st.error("Please paste the full job description.")
        else:
            try:
                with st.spinner("Analyzing job description..."):
                    job_analysis = analyze_job(
                        st.session_state.job_description,
                    )
                    st.session_state.job_analysis_json = job_analysis

                with st.spinner("Rewriting your resume for this role."):
                    rewritten_resume = rewrite_resume(
                        st.session_state.resume_text,
                        st.session_state.job_analysis_json,
                    )
                st.session_state.final_resume_text = rewritten_resume

                with st.spinner("Generating tailored cover letter."):
                    cover_letter = generate_cover_letter(
                        st.session_state.resume_text,
                        st.session_state.job_analysis_json,
                    )
                st.session_state.cover_letter_text = cover_letter

                with st.spinner("Creating follow-up email strategy."):
                    emails = generate_emails(
                        st.session_state.job_analysis_json,
                    )
                st.session_state.follow_up_emails = emails

                st.success("✅ All done! Check the other tabs for your Application Kit.")
                st.balloons()

            except Exception as e:
                msg = str(e)
                st.error(f"❌ Error during processing: {msg}")

    if st.session_state.job_analysis_json:
        st.subheader("Job Analysis Summary")

        analysis = st.session_state.job_analysis_json
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("Role", analysis.get("role_name", "N/A"))
        with col2:
            st.metric("Hard Skills", len(analysis.get("hard_skills", [])))
        with col3:
            st.metric("Total Keywords", len(analysis.get("keywords", [])))

        with st.expander("View Full Analysis JSON"):
            st.json(analysis)

# ------------------------------------------------------------
# TAB 2: Tailored Resume (Premium section)
# ------------------------------------------------------------
with tab_resume:
    st.header("3. Tailored Resume")
    premium_download_section(
        title="ATS-Optimized Resume",
        markdown_content=st.session_state.final_resume_text or "",
        filename_base="tailored_resume",
        amount=1000,
        user_is_paid=user_is_paid,
        user_id=user_id,
    )

# ------------------------------------------------------------
# TAB 3: Custom Cover Letter (Premium section)
# ------------------------------------------------------------
with tab_cover:
    st.header("4. Custom Cover Letter")
    premium_download_section(
        title="Custom Cover Letter",
        markdown_content=st.session_state.cover_letter_text or "",
        filename_base="cover_letter",
        amount=1000,
        user_is_paid=user_is_paid,
        user_id=user_id,
    )

# ------------------------------------------------------------
# TAB 4: Follow-Up Email Strategy
# ------------------------------------------------------------
with tab_emails:
    st.header("5. Follow-Up Email Strategy")

    emails = st.session_state.follow_up_emails or []

    if emails:
        st.markdown(
            "Use this **email sequence** to follow up strategically after applying and interviewing."
        )
        for idx, email in enumerate(emails, start=1):
            label = email.get("label", f"email_{idx}") if isinstance(email, dict) else f"email_{idx}"
            subject = email.get("subject", "No subject") if isinstance(email, dict) else "No subject"
            body = email.get("body", "") if isinstance(email, dict) else str(email)

            with st.expander(f"{idx}. {label} – {subject}"):
                st.markdown(f"**Subject:** {subject}")
                st.markdown("---")
                st.text_area(
                    "Email body",
                    value=body,
                    height=250,
                    key=f"email_body_{idx}",
                )
                st.code(body)
    else:
        st.info("Your follow-up email sequence will appear here after you run the analysis in the Setup tab.")

# ------------------------------------------------------------
# TAB 5: Admin – manually mark users as paid
# ------------------------------------------------------------
with tab_admin:
    st.header("Admin Panel (Manual Unlock)")

    expected_admin_pw = st.secrets.get("ADMIN_PASSWORD")

    if not expected_admin_pw:
        st.warning("ADMIN_PASSWORD is not configured in secrets. Set it before using the admin panel.")
    else:
        admin_pw = st.text_input("Admin password", type="password")
        if admin_pw == expected_admin_pw:
            st.success("Admin authenticated.")

            target_user_id = st.text_input("User ID to mark as paid")
            if st.button("Mark this user as PAID"):
                if not target_user_id.strip():
                    st.error("Please enter a user_id.")
                else:
                    ok = mark_user_paid(target_user_id.strip())
                    if ok:
                        st.success(f"User {target_user_id.strip()} marked as paid.")
                    else:
                        st.error("Failed to mark user as paid (see logs).")
        else:
            if admin_pw:
                st.error("Incorrect admin password.")
            st.info("Enter the admin password to manage paid users.")

# app.py
import json
import re
import time
from io import BytesIO

import streamlit as st

from agents import (
    extract_text_from_pdf,
    analyze_job,
    rewrite_resume,
    generate_cover_letter,
    generate_emails,
)

from docx import Document
from fpdf import FPDF

# IntaSend backend helpers
from payments import trigger_mpesa_payment, check_payment_status


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
        "api_key": "",
        "last_uploaded_filename": "",      # track last uploaded resume file name
        "resume_input_mode": "Upload PDF Resume",  # resume input mode
        "is_paid": False,                  # Freemium flag
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ============================================================
# 6. CONVERSION UTILITIES
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

    effective_width = pdf.epw  # safe printable width

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
    return bytes(pdf.output(dest="S"))


# ============================================================
# Main App
# ============================================================

st.set_page_config(page_title="AI Career Accelerator", layout="wide")
init_state()

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


# ============================================================
# Sidebar - Resume Input & API Key (SaaS Mode)
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

    # API key for Gemini
    if "GEMINI_API_KEY" in st.secrets:
        st.session_state.api_key = st.secrets["GEMINI_API_KEY"]
        st.markdown("✅ **Pro Access Enabled**")
    else:
        st.text_input(
            "Google Gemini API Key",
            type="password",
            key="api_key",
            help="Get your key at https://aistudio.google.com/app/apikey",
        )
        if st.session_state.api_key:
            st.success("API Key detected.")


# ============================================================
# Main Tabs
# ============================================================

tab_setup, tab_resume, tab_cover, tab_emails = st.tabs(
    ["Setup", "Resume", "Cover Letter", "Email Strategy"]
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
        if not st.session_state.api_key:
            st.error("Please enter your Google Gemini API key in the sidebar.")
        elif not st.session_state.resume_text.strip():
            st.error("Please provide your resume details (upload a PDF or enter text manually).")
        elif not st.session_state.job_description.strip():
            st.error("Please paste the full job description.")
        else:
            try:
                with st.spinner("Analyzing job description..."):
                    job_analysis = analyze_job(
                        st.session_state.job_description,
                        st.session_state.api_key,
                    )
                    st.session_state.job_analysis_json = job_analysis

                with st.spinner("Rewriting your resume for this role."):
                    rewritten_resume = rewrite_resume(
                        st.session_state.resume_text,
                        st.session_state.job_analysis_json,
                        st.session_state.api_key,
                    )
                st.session_state.final_resume_text = rewritten_resume

                with st.spinner("Generating tailored cover letter."):
                    cover_letter = generate_cover_letter(
                        st.session_state.resume_text,
                        st.session_state.job_analysis_json,
                        st.session_state.api_key,
                    )
                    st.session_state.cover_letter_text = cover_letter

                with st.spinner("Creating follow-up email strategy."):
                    emails = generate_emails(
                        st.session_state.job_analysis_json,
                        st.session_state.api_key,
                    )
                    st.session_state.follow_up_emails = emails

                st.success("✅ All done! Check the other tabs for your Application Kit.")
                st.balloons()

            except Exception as e:
                st.error(f"❌ Error during processing: {str(e)}")

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
# TAB 2: Tailored Resume (Hard Paywall + IntaSend + Copy Protection)
# ------------------------------------------------------------
with tab_resume:
    st.header("3. Tailored Resume")

    if st.session_state.final_resume_text:
        resume_md = st.session_state.final_resume_text

        st.markdown(
            "Below is your **ATS-optimized resume** in Markdown. "
            "Preview is free. To download in any format, unlock Premium access."
        )

        # Copy-protected preview wrapper
        st.markdown('<div class="no-copy-container">', unsafe_allow_html=True)
        st.markdown(resume_md)
        st.markdown("</div>", unsafe_allow_html=True)

        col_md, col_docx, col_pdf = st.columns(3)

        if st.session_state.is_paid:
            # Premium: ALL downloads (Markdown, DOCX, PDF)
            resume_docx = to_docx(resume_md)
            resume_pdf = to_pdf(resume_md)

            with col_md:
                st.download_button(
                    label="⬇️ Download as Markdown",
                    data=resume_md,
                    file_name="tailored_resume.md",
                    mime="text/markdown",
                )

            with col_docx:
                st.download_button(
                    label="⬇️ Download as Word (DOCX)",
                    data=resume_docx,
                    file_name="tailored_resume.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )

            with col_pdf:
                st.download_button(
                    label="⬇️ Download as PDF",
                    data=resume_pdf,
                    file_name="tailored_resume.pdf",
                    mime="application/pdf",
                )
        else:
            # Hard paywall: no downloads, only Premium Unlock UI
            with col_docx:
                st.markdown("### 🔓 Premium Unlock")
                st.write("Unlock **all resume downloads** (Markdown, Word, PDF) for:")
                st.write("**KES 1,500**")

                phone_input_resume = st.text_input(
                    "M-Pesa Number (via IntaSend)",
                    placeholder="0712345678",
                    key="mpesa_phone_resume",
                )

                if st.button("Pay KES 1,500", key="pay_resume"):
                    if not phone_input_resume:
                        st.error("Please enter your M-Pesa phone number.")
                    else:
                        with st.spinner("Sending IntaSend payment request to your phone..."):
                            invoice_id = trigger_mpesa_payment(
                                phone_number=phone_input_resume,
                                amount=1500,
                            )

                        if not invoice_id:
                            st.error("Unable to initiate payment via IntaSend. Please try again.")
                        else:
                            st.info("📲 STK Push sent via IntaSend! Check your phone and enter your M-Pesa PIN.")
                            progress_bar = st.progress(0)
                            payment_success = False
                            status = None

                            # Poll up to 45 seconds (15 × 3s)
                            for i in range(15):
                                time.sleep(3)
                                status = check_payment_status(invoice_id)
                                progress_bar.progress(int((i + 1) / 15 * 100))

                                if status is True:
                                    payment_success = True
                                    break
                                elif status is False:
                                    st.error("Payment failed or was cancelled.")
                                    break

                            if payment_success:
                                st.success("✅ Payment confirmed! Unlocking downloads...")
                                st.session_state.is_paid = True
                                time.sleep(1)
                                st.rerun()
                            elif status is None:
                                st.warning("⚠️ Payment timed out. If you completed payment, please refresh.")

            with col_pdf:
                st.info("Premium required to download your resume in any format.")
    else:
        st.info("Your tailored resume will appear here after you run the analysis in the Setup tab.")


# ------------------------------------------------------------
# TAB 3: Custom Cover Letter (Hard Paywall + IntaSend + Copy Protection)
# ------------------------------------------------------------
with tab_cover:
    st.header("4. Custom Cover Letter")

    if st.session_state.cover_letter_text:
        cover_text = st.session_state.cover_letter_text

        st.markdown(
            "Here is your **tailored cover letter**. "
            "Preview is free. To download in any format, unlock Premium."
        )

        # Copy-protected preview wrapper (read-only textarea)
        st.markdown('<div class="no-copy-container">', unsafe_allow_html=True)
        st.text_area(
            "Cover Letter (Preview Only)",
            value=cover_text,
            height=350,
            disabled=True,
            key="cover_preview",
        )
        st.markdown("</div>", unsafe_allow_html=True)

        col_txt, col_docx, col_pdf = st.columns(3)

        if st.session_state.is_paid:
            # Premium: ALL downloads (Text, DOCX, PDF)
            cover_docx = to_docx(cover_text)
            cover_pdf = to_pdf(cover_text)

            with col_txt:
                st.download_button(
                    label="⬇️ Download as Text",
                    data=cover_text,
                    file_name="cover_letter.txt",
                    mime="text/plain",
                )

            with col_docx:
                st.download_button(
                    label="⬇️ Download as Word (DOCX)",
                    data=cover_docx,
                    file_name="cover_letter.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )

            with col_pdf:
                st.download_button(
                    label="⬇️ Download as PDF",
                    data=cover_pdf,
                    file_name="cover_letter.pdf",
                    mime="application/pdf",
                )
        else:
            # Hard paywall: no downloads, only Premium Unlock UI
            with col_docx:
                st.markdown("### 🔓 Premium Unlock")
                st.write("Unlock **all cover letter downloads** (Text, Word, PDF) for:")
                st.write("**KES 1,500**")

                phone_input_cover = st.text_input(
                    "M-Pesa Number (via IntaSend)",
                    placeholder="0712345678",
                    key="mpesa_phone_cover",
                )

                if st.button("Pay KES 1,500", key="pay_cover"):
                    if not phone_input_cover:
                        st.error("Please enter your M-Pesa phone number.")
                    else:
                        with st.spinner("Sending IntaSend payment request to your phone..."):
                            invoice_id = trigger_mpesa_payment(
                                phone_number=phone_input_cover,
                                amount=1500,
                            )

                        if not invoice_id:
                            st.error("Unable to initiate payment via IntaSend. Please try again.")
                        else:
                            st.info("📲 STK Push sent via IntaSend! Check your phone and enter your M-Pesa PIN.")
                            progress_bar = st.progress(0)
                            payment_success = False
                            status = None

                            for i in range(15):
                                time.sleep(3)
                                status = check_payment_status(invoice_id)
                                progress_bar.progress(int((i + 1) / 15 * 100))

                                if status is True:
                                    payment_success = True
                                    break
                                elif status is False:
                                    st.error("Payment failed or was cancelled.")
                                    break

                            if payment_success:
                                st.success("✅ Payment confirmed! Unlocking downloads...")
                                st.session_state.is_paid = True
                                time.sleep(1)
                                st.rerun()
                            elif status is None:
                                st.warning("⚠️ Payment timed out. If you completed payment, please refresh.")

            with col_pdf:
                st.info("Premium required to download your cover letter in any format.")
    else:
        st.info("Your cover letter will appear here after you run the analysis in the Setup tab.")


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
            label = email.get("label", f"email_{idx}")
            subject = email.get("subject", "No subject")
            body = email.get("body", "")

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

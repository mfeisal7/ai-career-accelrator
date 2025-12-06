# app.py
import re
import uuid
import threading
import time
import os
from io import BytesIO

import streamlit as st
from docx import Document
from fpdf import FPDF

# Local imports
from agents import (
    extract_text_from_pdf,
    analyze_job,
    rewrite_resume,
    generate_cover_letter,
    generate_emails,
)
from payments import trigger_mpesa_payment, check_payment_status
from payments_db import init_db, create_payment, is_user_paid
from webhook_server import run as run_webhook_server


# ============================================================
# Initialization
# ============================================================

init_db()


@st.cache_resource(show_spinner=False)
def _start_webhook_server_once() -> bool:
    """
    Start the webhook server in a background thread exactly once.

    Wrapped in a try/except so that if FastAPI/uvicorn are missing,
    the Streamlit app still runs instead of crashing.
    """
    def _run():
        try:
            run_webhook_server(host="0.0.0.0", port=8000)
        except Exception as e:
            # You can log this somewhere; for now we just print
            print(f"[webhook_server] Failed to start: {e}")

    thread = threading.Thread(
        target=_run,
        daemon=True,
    )
    thread.start()
    return True


# Start webhook server once per Streamlit session
try:
    _start_webhook_server_once()
except Exception as e:
    # Fail soft – allow rest of app to work even if webhook server can't start
    print(f"[webhook_server] Error in cache_resource wrapper: {e}")


# ============================================================
# User ID & Session Helpers
# ============================================================

def get_or_create_user_id() -> str:
    if "user_id" not in st.session_state or not st.session_state.user_id:
        st.session_state.user_id = str(uuid.uuid4())
    return st.session_state.user_id


def init_state() -> None:
    defaults = {
        "resume_text": "",
        "job_description": "",
        "job_analysis_json": {},
        "final_resume_text": "",
        "cover_letter_text": "",
        "follow_up_emails": [],
        "last_uploaded_filename": "",
        "resume_input_mode": "Upload PDF Resume",
        "waiting_for_payment": False,
        "pending_invoice_id": None,
        "analysis_uses": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ============================================================
# Document Export Functions
# ============================================================

def _add_markdown_runs_to_paragraph(paragraph, text: str) -> None:
    """
    Very small markdown → docx converter focusing on **bold** sections.
    """
    parts = re.split(r"(\*\*.*?\*\*)", text)
    for part in parts:
        if not part:
            continue
        bold_match = re.fullmatch(r"\*\*(.*?)\*\*", part)
        if bold_match:
            run = paragraph.add_run(bold_match.group(1))
            run.bold = True
        else:
            paragraph.add_run(part)


def to_docx(markdown_text: str) -> BytesIO:
    doc = Document()
    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            doc.add_paragraph("")
            continue

        stripped = line.lstrip()
        if stripped.startswith("## "):
            p = doc.add_paragraph()
            p.style = "Heading 1"
            _add_markdown_runs_to_paragraph(p, stripped[3:])
        elif stripped.startswith("### "):
            p = doc.add_paragraph()
            p.style = "Heading 2"
            _add_markdown_runs_to_paragraph(p, stripped[4:])
        elif stripped.startswith(("- ", "* ")):
            p = doc.add_paragraph(style="List Bullet")
            _add_markdown_runs_to_paragraph(p, stripped[2:])
        else:
            p = doc.add_paragraph()
            _add_markdown_runs_to_paragraph(p, stripped)

    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def to_pdf(markdown_text: str) -> bytes:
    """
    Convert markdown-ish text into a simple PDF.

    Different versions of fpdf/fpdf2 behave slightly differently for output().
    We guard for both `str` and `bytes` here to avoid `.encode` errors.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    line_height = 6

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            pdf.ln(line_height)
            continue

        stripped = line.lstrip()
        text = stripped.replace("**", "")

        if stripped.startswith("## "):
            pdf.set_font("Helvetica", "B", 16)
            pdf.multi_cell(0, 10, text[3:])
            pdf.ln(3)
            pdf.set_font("Helvetica", size=12)
        elif stripped.startswith("### "):
            pdf.set_font("Helvetica", "B", 14)
            pdf.multi_cell(0, 8, text[4:])
            pdf.ln(2)
            pdf.set_font("Helvetica", size=12)
        elif stripped.startswith(("- ", "* ")):
            pdf.set_font("Helvetica", size=12)
            pdf.multi_cell(0, line_height, "• " + text[2:])
        else:
            pdf.multi_cell(0, line_height, text)

        pdf.ln(1)

    result = pdf.output(dest="S")
    # fpdf2 returns str here; some older versions may return bytes
    if isinstance(result, str):
        return result.encode("latin-1")
    return result


# ============================================================
# Premium Download Section (Non-blocking)
# ============================================================

def _is_valid_kenyan_phone(phone: str) -> bool:
    """
    More robust Kenyan mobile validation.
    """
    phone = phone.strip()
    pattern = r"^(?:\+?254|0)?[17]\d{8}$"
    return bool(re.fullmatch(pattern, phone))


def premium_download_section(
    title: str,
    markdown_content: str,
    filename_base: str,
    amount: int,
    user_is_paid: bool,
    user_id: str,
) -> None:
    st.subheader(title)

    if not markdown_content:
        st.info("Content will appear here after analysis.")
        return

    st.markdown(markdown_content)

    if user_is_paid:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.download_button(
                "Download Markdown",
                markdown_content,
                f"{filename_base}.md",
                "text/markdown",
            )
        with col2:
            st.download_button(
                "Download DOCX",
                to_docx(markdown_content),
                f"{filename_base}.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        with col3:
            st.download_button(
                "Download PDF",
                to_pdf(markdown_content),
                f"{filename_base}.pdf",
                "application/pdf",
            )
    else:
        st.warning("Unlock Premium to download your documents in all formats.")
        phone = st.text_input(
            "Enter your Safaricom phone number (e.g. 07xxxxxxxx)",
            key=f"phone_{filename_base}",
        )

        if st.button(
            f"Pay KES {amount} via M-Pesa",
            type="primary",
            key=f"pay_{filename_base}",
        ):
            if not _is_valid_kenyan_phone(phone):
                st.error("Please enter a valid Kenyan mobile number")
            else:
                with st.spinner("Sending STK push..."):
                    invoice_id = trigger_mpesa_payment(
                        phone_number=phone,
                        amount=amount,
                        reference=f"{filename_base}_{user_id}",
                    )
                    if invoice_id:
                        create_payment(user_id, phone, invoice_id, amount)
                        st.session_state.pending_invoice_id = invoice_id
                        st.session_state.waiting_for_payment = True
                        st.success("STK push sent! Complete payment on your phone.")
                    else:
                        st.error("Payment failed. Try again.")

        if st.session_state.waiting_for_payment and st.session_state.pending_invoice_id:
            invoice_id = st.session_state.pending_invoice_id
            with st.spinner("Checking payment status..."):
                # small delay so IntaSend has time to update
                time.sleep(3)
                status = check_payment_status(invoice_id)
                if status is True:
                    st.success("Payment confirmed! Downloads unlocked.")
                    st.session_state.waiting_for_payment = False
                    # Let the rest of the app pick up the new paid status
                    st.rerun()
                elif status is False:
                    st.error("Payment failed or cancelled.")
                    st.session_state.waiting_for_payment = False
                # status is None → still pending, the UI will let user check again


# ============================================================
# Main App
# ============================================================

st.set_page_config(page_title="AI Career Accelerator", layout="wide")
st.title("AI Career Accelerator")
st.markdown("### Transform your job applications with AI — Kenyan jobs, Kenyan standards.")

init_state()
user_id = get_or_create_user_id()
user_is_paid = is_user_paid(user_id)

# DEV OVERRIDE: if you want to bypass payments while testing:
if os.getenv("DISABLE_PAYMENTS_DEV") == "1":
    user_is_paid = True

col1, col2 = st.columns([1, 2])
with col1:
    st.image("https://via.placeholder.com/150", caption="Premium Resume AI")
with col2:
    st.markdown(
        """
        Get a **fully tailored, ATS-optimized resume + cover letter + follow-up emails**  
        in under 60 seconds — built for Kenyan employers.
        """
    )

# ------------------------------------------------------------
# Resume Input
# ------------------------------------------------------------
st.header("1. Your Current Resume")
mode = st.radio(
    "Input method",
    ["Upload PDF Resume", "Enter Text Manually"],
    key="resume_input_mode",
)
if mode == "Upload PDF Resume":
    uploaded = st.file_uploader("Upload your resume (PDF)", type="pdf")
    if uploaded:
        with st.spinner("Extracting text..."):
            text = extract_text_from_pdf(uploaded)
            if text.strip():
                st.session_state.resume_text = text
                st.session_state.last_uploaded_filename = uploaded.name
                st.success("Resume loaded!")
            else:
                st.error("No text found in the PDF. Try manual input.")
else:
    st.text_area(
        "Paste your resume or LinkedIn summary",
        key="resume_text",
        height=300,
    )

# ------------------------------------------------------------
# Tabs
# ------------------------------------------------------------
tab_setup, tab_resume, tab_cover, tab_emails = st.tabs(
    ["Setup", "Resume", "Cover Letter", "Email Strategy"]
)

with tab_setup:
    st.header("2. Job Description & Analysis")
    st.text_area(
        "Paste the full job description",
        key="job_description",
        height=350,
    )

    if st.button("Analyze Job & Generate Kit", type="primary", use_container_width=True):
        if st.session_state.analysis_uses >= 3 and not user_is_paid:
            st.error("Free limit reached. Upgrade to Premium for unlimited use.")
        elif not st.session_state.resume_text.strip():
            st.error("Please provide your resume first.")
        elif not st.session_state.job_description.strip():
            st.error("Please paste the job description.")
        else:
            with st.spinner("Analyzing... This takes 20–40 seconds"):
                try:
                    job_analysis = analyze_job(st.session_state.job_description)
                    st.session_state.job_analysis_json = job_analysis

                    rewritten = rewrite_resume(
                        st.session_state.resume_text,
                        job_analysis,
                    )
                    st.session_state.final_resume_text = rewritten

                    cover = generate_cover_letter(
                        st.session_state.resume_text,
                        job_analysis,
                    )
                    st.session_state.cover_letter_text = cover

                    emails = generate_emails(job_analysis)
                    st.session_state.follow_up_emails = emails or []

                    st.session_state.analysis_uses += 1
                    st.success("All done! Check the tabs.")
                    st.balloons()
                except Exception as e:
                    st.error(f"Error during analysis: {e}")

    if st.session_state.job_analysis_json:
        analysis = st.session_state.job_analysis_json
        c1, c2, c3 = st.columns(3)
        c1.metric("Role", analysis.get("role_name", "N/A"))
        c2.metric("Hard Skills", len(analysis.get("hard_skills", [])))
        c3.metric("Keywords", len(analysis.get("keywords", [])))

with tab_resume:
    premium_download_section(
        title="ATS-Optimized Resume",
        markdown_content=st.session_state.final_resume_text,
        filename_base="tailored_resume",
        amount=1000,   # CHANGED from 1500 → 1000
        user_is_paid=user_is_paid,
        user_id=user_id,
    )

with tab_cover:
    premium_download_section(
        title="Custom Cover Letter",
        markdown_content=st.session_state.cover_letter_text,
        filename_base="cover_letter",
        amount=1000,   # CHANGED from 1500 → 1000
        user_is_paid=user_is_paid,
        user_id=user_id,
    )

with tab_emails:
    st.header("Follow-Up Email Strategy")
    for i, email in enumerate(st.session_state.follow_up_emails or [], 1):
        # Be defensive: email might be dict or plain string depending on agents.generate_emails
        if isinstance(email, dict):
            label = email.get("label", f"Email {i}")
            subject = email.get("subject", "")
            body = email.get("body", "")
        else:
            label = f"Email {i}"
            subject = ""
            body = str(email)

        with st.expander(f"{i}. {label} — {subject}"):
            st.code(body, language="text")

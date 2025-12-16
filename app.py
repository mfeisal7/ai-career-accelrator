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
from payments_db import (
    init_db,
    get_user_payment_status,
    save_user_output,
    load_user_output,
)

# Optional: auto-refresh helper (best UX). If not installed, app still works.
try:
    from streamlit_autorefresh import st_autorefresh  # pip install streamlit-autorefresh
except Exception:
    st_autorefresh = None


st.set_page_config(
    page_title="AI Career Accelerator – Kenyan Job Market",
    page_icon="🧠",
    layout="wide",
)

# ============================================================
# Utility: Safe WhatsApp Number
# ============================================================

def _get_whatsapp_number() -> str:
    """Get the WhatsApp number from environment only."""
    env_num = os.getenv("WHATSAPP_NUMBER")
    if env_num:
        return env_num.strip()

    # Fallback (keeps existing behavior)
    return "254722285538"


def _build_whatsapp_link(user_id: str, amount: int = 1000) -> str:
    """
    Build a WhatsApp link with a prefilled message including user_id.
    """
    whatsapp_number = _get_whatsapp_number()
    msg = (
        f"Hi, I have paid KES {amount} for AI Career Accelerator.\n"
        f"User ID: {user_id}\n"
        f"Please confirm and unlock my downloads."
    )
    # Encode minimal characters for wa.me
    msg_encoded = msg.replace("\n", "%0A").replace(" ", "%20")
    return f"https://wa.me/{whatsapp_number}?text={msg_encoded}"


# ============================================================
# Session State Initialization
# ============================================================

def _ensure_user_id() -> str:
    """
    Ensure we have a stable user_id for tracking payments.
    Priority:
      1. URL query param ?user_id=...
      2. Cookie (if you decide to use cookies later)
      3. Streamlit session_state
      4. Fresh UUID
    """
    if "user_id" in st.session_state and st.session_state["user_id"]:
        return st.session_state["user_id"]

    qp = st.query_params
    user_id = qp.get("user_id", [None])
    if isinstance(user_id, list):
        user_id = user_id[0]
    if not user_id:
        user_id = str(uuid.uuid4())
        st.query_params.update({"user_id": user_id})

    st.session_state["user_id"] = user_id

    # Optional: cookie persistence (non-critical)
    st.markdown(
        f"""
        <script>
        (function() {{
            const cookies = document.cookie || "";
            if (!cookies.includes("user_id=")) {{
                const expires = new Date();
                expires.setFullYear(expires.getFullYear() + 1);
                document.cookie = "user_id={user_id}; expires=" + expires.toUTCString() + "; path=/";
            }}
        }})();
        </script>
        """,
        unsafe_allow_html=True,
    )

    return user_id


def _hydrate_session_from_db(user_id: str) -> None:
    """
    Restore generated content from DB into st.session_state.
    This makes refresh safe (paid or unpaid).
    """
    saved = load_user_output(user_id)
    if not saved:
        return

    # Only set if missing, to avoid overwriting current in-session edits
    if not st.session_state.get("ai_resume_markdown") and saved.get("ai_resume_markdown"):
        st.session_state["ai_resume_markdown"] = saved["ai_resume_markdown"]

    if not st.session_state.get("ai_cover_letter") and saved.get("ai_cover_letter"):
        st.session_state["ai_cover_letter"] = saved["ai_cover_letter"]

    if not st.session_state.get("ai_emails") and saved.get("ai_emails") is not None:
        st.session_state["ai_emails"] = saved.get("ai_emails", [])


# ============================================================
# Download Helpers
# ============================================================

def _markdown_to_docx(markdown_text: str) -> bytes:
    """Convert simple Markdown to a .docx binary using python-docx."""
    doc = Document()
    lines = markdown_text.splitlines()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            doc.add_paragraph("")
            continue

        if stripped.startswith("## "):
            doc.add_heading(stripped[3:], level=2)
        elif stripped.startswith("# "):
            doc.add_heading(stripped[2:], level=1)
        else:
            doc.add_paragraph(stripped)

    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def _markdown_to_pdf(markdown_text: str) -> bytes:
    """Convert simple Markdown text into a basic PDF using fpdf."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    for line in markdown_text.splitlines():
        if not line.strip():
            pdf.ln(5)
            continue
        pdf.multi_cell(0, 6, line)

    buffer = BytesIO()
    pdf.output(buffer)
    buffer.seek(0)
    return buffer.getvalue()


# ============================================================
# Premium Download Section
# ============================================================

def premium_download_section(
    user_id: str,
    title: str,
    markdown_content: str,
    paid: bool,
    amount: int = 1000,
) -> None:
    """
    Show download options for Markdown / Word / PDF, gated by payment status.
    If unpaid, display manual WhatsApp flow + auto-unlock polling.
    """
    st.markdown("---")
    st.subheader(title)

    col_md, col_docx, col_pdf = st.columns(3)

    if paid:
        with col_md:
            st.markdown("### 📄 Download as Markdown")
            st.download_button(
                label="⬇️ Download .md",
                data=markdown_content.encode("utf-8"),
                file_name=f"{title.lower().replace(' ', '_')}.md",
                mime="text/markdown",
            )

        with col_docx:
            st.markdown("### 📝 Download as Word (.docx)")
            docx_bytes = _markdown_to_docx(markdown_content)
            st.download_button(
                label="⬇️ Download .docx",
                data=docx_bytes,
                file_name=f"{title.lower().replace(' ', '_')}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

        with col_pdf:
            st.markdown("### 📕 Download as PDF")
            pdf_bytes = _markdown_to_pdf(markdown_content)
            st.download_button(
                label="⬇️ Download .pdf",
                data=pdf_bytes,
                file_name=f"{title.lower().replace(' ', '_')}.pdf",
                mime="application/pdf",
            )
        return

    # =======================
    # UNPAID FLOW (manual + auto-unlock)
    # =======================

    # We store a flag when user says "I've paid" so we can poll for unlock
    if "waiting_for_unlock" not in st.session_state:
        st.session_state["waiting_for_unlock"] = False

    with col_md:
        st.markdown("### 🔒 Locked")
        st.info(
            "Downloads are locked until payment is confirmed.\n\n"
            "You can still see the content on the page above."
        )

    with col_docx:
        st.markdown("### 🔓 Premium Unlock (Manual)")
        st.write(
            f"To unlock all {title.lower()} downloads (Markdown, Word, PDF):"
        )
        st.write(f"**KES {amount:,}**")

        whatsapp_link = _build_whatsapp_link(user_id=user_id, amount=amount)

        st.write("1. Pay via M-Pesa manually.")
        st.write("2. Tap the button below to message me on WhatsApp with your User ID.")
        st.write("3. I will confirm payment in admin. This page will unlock automatically.")

        st.link_button("💬 Pay / Notify on WhatsApp", whatsapp_link)
        st.caption(f"Your user ID: `{user_id}`")

        # Explicit "I've paid" toggle to start polling (better UX)
        if st.button("✅ I have paid — start unlock check", key=f"paidbtn_{title}"):
            st.session_state["waiting_for_unlock"] = True

    with col_pdf:
        if st.session_state.get("waiting_for_unlock"):
            st.markdown("### ⏳ Waiting for admin confirmation…")

            # Auto-refresh polling every 4 seconds (best UX)
            if st_autorefresh is not None:
                st_autorefresh(interval=4000, key=f"unlockpoll_{user_id}_{title}")

            # Re-check payment status on each run
            paid_now = get_user_payment_status(user_id)
            if paid_now:
                st.success("✅ Payment confirmed! Downloads are now unlocked.")
                # Stop polling and force rerun so paid downloads render instantly
                st.session_state["waiting_for_unlock"] = False
                st.rerun()
            else:
                st.info("Not confirmed yet. If you already messaged on WhatsApp, please wait a moment…")

                if st_autorefresh is None:
                    st.warning(
                        "Auto-unlock helper not installed. If unlock doesn't appear, refresh once.\n\n"
                        "Fix: add `streamlit-autorefresh` to requirements.txt."
                    )
        else:
            st.info(
                "After payment is confirmed in the admin dashboard, "
                "click 'I have paid' above to auto-unlock (or refresh)."
            )


# ============================================================
# Main App Logic
# ============================================================

def main():
    st.title("AI Career Accelerator – Built for Kenyan Graduates")

    init_db()

    user_id = _ensure_user_id()
    st.caption(f"Your user ID: `{user_id}` (used to unlock premium downloads)")

    # Payment status
    paid = get_user_payment_status(user_id)

    # Hydrate any previously generated content so refresh never loses it
    _hydrate_session_from_db(user_id)

    st.markdown(
        """
        AI Career Accelerator helps you turn your Kenyan degree into your **dream job** –
        whether that's tech, data, finance, teaching, healthcare, NGO work, or government.

        1️⃣ Paste a job description  
        2️⃣ Upload your current CV (or type it in)  
        3️⃣ Get a Kenyan-market-optimised CV, cover letter and follow-up emails
        """
    )

    tab_job, tab_resume, tab_results = st.tabs(
        ["1. Job Description", "2. Your Resume", "3. AI Output & Downloads"]
    )

    with tab_job:
        st.markdown("### Step 1: Paste the Job Description")
        job_description = st.text_area(
            "Paste a Kenyan job description (from BrighterMonday, LinkedIn, company site, etc.)",
            height=260,
        )

        if st.button("🔍 Analyse Job"):
            if not job_description.strip():
                st.error("Please paste a job description first.")
            else:
                with st.spinner("Analysing job description with AI..."):
                    try:
                        analysis = analyze_job(job_description)
                        st.session_state["job_analysis"] = analysis
                        st.success("Job analysed successfully.")
                    except Exception as e:
                        st.error(f"Failed to analyse job: {e}")

        if "job_analysis" in st.session_state:
            st.markdown("#### Job Analysis (for your reference)")
            st.json(st.session_state["job_analysis"])

    with tab_resume:
        st.markdown("### Step 2: Provide Your Resume")

        option = st.radio(
            "How do you want to provide your CV?",
            ["Upload PDF Resume", "Enter Text Manually"],
        )

        resume_text = ""

        if option == "Upload PDF Resume":
            uploaded = st.file_uploader(
                "Upload your current resume in PDF format", type=["pdf"]
            )
            if uploaded is not None:
                with st.spinner("Extracting text from PDF..."):
                    try:
                        resume_text = extract_text_from_pdf(uploaded)
                        st.success("Successfully extracted text from PDF.")
                        st.text_area(
                            "Extracted Resume Text (you can edit before AI uses it)",
                            resume_text,
                            height=260,
                            key="resume_pdf_text",
                        )
                    except Exception as e:
                        st.error(f"Failed to read PDF: {e}")
        else:
            resume_text = st.text_area(
                "Paste or type your resume here (a rough outline is enough).",
                height=260,
                key="resume_manual_text",
            )

        if st.button("✍️ Generate AI Resume & Cover Letter"):
            if "job_analysis" not in st.session_state:
                st.error("Please analyse a job description first (Step 1).")
            elif not (resume_text or "").strip():
                st.error("Please provide your resume text or upload a PDF.")
            else:
                with st.spinner("Generating resume and cover letter..."):
                    try:
                        analysis = st.session_state["job_analysis"]
                        ai_resume = rewrite_resume(resume_text, analysis)
                        ai_cover = generate_cover_letter(resume_text, analysis)
                        ai_emails = generate_emails(analysis)

                        st.session_state["ai_resume_markdown"] = ai_resume
                        st.session_state["ai_cover_letter"] = ai_cover
                        st.session_state["ai_emails"] = ai_emails

                        # Persist outputs immediately so refresh is safe
                        save_user_output(
                            user_id=user_id,
                            resume=ai_resume,
                            cover_letter=ai_cover,
                            emails=ai_emails,
                        )

                        st.success("AI resume, cover letter and emails generated.")
                    except Exception as e:
                        st.error(f"Failed to generate AI content: {e}")

    with tab_results:
        st.markdown("### Step 3: Review & Download")

        ai_resume = st.session_state.get("ai_resume_markdown")
        ai_cover = st.session_state.get("ai_cover_letter")
        ai_emails = st.session_state.get("ai_emails", [])

        if not ai_resume and not ai_cover:
            st.info(
                "No AI content yet. Complete Steps 1 and 2 to generate your resume and cover letter."
            )
            return

        # Re-check paid status each time results tab renders (helps unlock feel)
        paid = get_user_payment_status(user_id)

        if ai_resume:
            st.markdown("## 🧾 AI-Optimised Resume (Preview)")
            st.markdown(ai_resume)
            premium_download_section(
                user_id=user_id,
                title="AI-Optimised Resume",
                markdown_content=ai_resume,
                paid=paid,
                amount=1000,
            )

        if ai_cover:
            st.markdown("## 📮 Tailored Cover Letter (Preview)")
            st.markdown(ai_cover)
            premium_download_section(
                user_id=user_id,
                title="Cover Letter",
                markdown_content=ai_cover,
                paid=paid,
                amount=1000,
            )

        if ai_emails:
            st.markdown("## ✉️ Follow-up Email Sequence")
            for idx, email in enumerate(ai_emails, start=1):
                st.markdown(f"### Email {idx}: {email.get('label', 'Follow-up')}")
                st.markdown(f"**Subject:** {email.get('subject', '')}")
                st.markdown(email.get("body", ""))
                st.markdown("---")

    st.markdown("---")
    st.caption(
        "AI Career Accelerator • Built for Kenyan graduates • This tool does not guarantee employment, "
        "but it gives you a much stronger shot at interviews in the local job market."
    )


if __name__ == "__main__":
    main()

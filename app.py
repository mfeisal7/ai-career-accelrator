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
    get_or_create_user,
)

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None


st.set_page_config(
    page_title="AI Career Accelerator – Kenyan Job Market",
    page_icon="🧠",
    layout="wide",
)


def _get_whatsapp_number() -> str:
    env_num = os.getenv("WHATSAPP_NUMBER")
    return env_num.strip() if env_num else "254722285538"


def _build_whatsapp_link(user_id: str, phone: str, email: str, amount: int = 1000) -> str:
    whatsapp_number = _get_whatsapp_number()
    msg = (
        f"Hi, I have paid KES {amount} for AI Career Accelerator.\n"
        f"User ID: {user_id}\n"
        f"Phone: {phone}\n"
        f"Email: {email}\n"
        f"Please confirm and unlock my downloads."
    )
    msg_encoded = msg.replace("\n", "%0A").replace(" ", "%20")
    return f"https://wa.me/{whatsapp_number}?text={msg_encoded}"


def _hydrate_session_from_db(user_id: str) -> None:
    saved = load_user_output(user_id)
    if not saved:
        return
    if not st.session_state.get("ai_resume_markdown") and saved.get("ai_resume_markdown"):
        st.session_state["ai_resume_markdown"] = saved["ai_resume_markdown"]
    if not st.session_state.get("ai_cover_letter") and saved.get("ai_cover_letter"):
        st.session_state["ai_cover_letter"] = saved["ai_cover_letter"]
    if not st.session_state.get("ai_emails") and saved.get("ai_emails") is not None:
        st.session_state["ai_emails"] = saved.get("ai_emails", [])


def _markdown_to_docx(markdown_text: str) -> bytes:
    doc = Document()
    for line in markdown_text.splitlines():
        s = line.strip()
        if not s:
            doc.add_paragraph("")
        elif s.startswith("## "):
            doc.add_heading(s[3:], level=2)
        elif s.startswith("# "):
            doc.add_heading(s[2:], level=1)
        else:
            doc.add_paragraph(s)

    buff = BytesIO()
    doc.save(buff)
    buff.seek(0)
    return buff.getvalue()


def _markdown_to_pdf(markdown_text: str) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    for line in markdown_text.splitlines():
        if not line.strip():
            pdf.ln(5)
        else:
            pdf.multi_cell(0, 6, line)
    buff = BytesIO()
    pdf.output(buff)
    buff.seek(0)
    return buff.getvalue()


def _require_login():
    """
    Hard-gate the app until the user logs in with phone+email.
    This version ALWAYS shows feedback on click (no more dead button feeling).
    """
    # Already logged in
    if (
        st.session_state.get("user_id")
        and st.session_state.get("user_phone")
        and st.session_state.get("user_email")
    ):
        return

    st.title("AI Career Accelerator – Login")
    st.info("Login using your **phone number** and **email** to access your saved CV, cover letter and email strategy.")

    # Always-visible click feedback
    st.session_state.setdefault("login_attempts", 0)

    with st.form("login_form", clear_on_submit=False):
        phone = st.text_input(
            "Phone Number (Kenya)",
            placeholder="e.g. 0722 123 456 or +254722123456",
            key="login_phone",
        )
        email = st.text_input(
            "Email",
            placeholder="e.g. you@gmail.com",
            key="login_email",
        )
        submitted = st.form_submit_button("Login")

    if not submitted:
        st.caption("Enter your phone and email, then click **Login**.")
        st.stop()

    # If they clicked, we MUST show something change
    st.session_state["login_attempts"] += 1
    st.caption(f"Login attempt #{st.session_state['login_attempts']}")

    phone = (phone or "").strip()
    email = (email or "").strip()

    if not phone or not email:
        st.error("Please enter BOTH phone number and email.")
        st.stop()

    with st.spinner("Signing you in…"):
        try:
            user = get_or_create_user(phone, email)
        except Exception as e:
            st.error(f"Login failed (DB/user lookup error): {e}")
            st.stop()

    if not user:
        st.error("Please enter a valid phone number and email.")
        st.stop()

    st.session_state["user_id"] = user["user_id"]
    st.session_state["user_phone"] = user["phone"]
    st.session_state["user_email"] = user["email"]

    # Keep user_id in URL (but never break login if this fails)
    try:
        st.query_params.update({"user_id": user["user_id"]})
    except Exception:
        pass

    st.success("✅ Logged in successfully.")
    st.rerun()


def premium_download_section(user_id: str, title: str, markdown_content: str, paid: bool, amount: int = 1000) -> None:
    st.markdown("---")
    st.subheader(title)

    col_md, col_docx, col_pdf = st.columns(3)

    if paid:
        with col_md:
            st.download_button(
                "⬇️ Download .md",
                data=markdown_content.encode("utf-8"),
                file_name=f"{title.lower().replace(' ', '_')}.md",
                mime="text/markdown",
            )
        with col_docx:
            st.download_button(
                "⬇️ Download .docx",
                data=_markdown_to_docx(markdown_content),
                file_name=f"{title.lower().replace(' ', '_')}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        with col_pdf:
            st.download_button(
                "⬇️ Download .pdf",
                data=_markdown_to_pdf(markdown_content),
                file_name=f"{title.lower().replace(' ', '_')}.pdf",
                mime="application/pdf",
            )
        return

    if "waiting_for_unlock" not in st.session_state:
        st.session_state["waiting_for_unlock"] = False

    phone = st.session_state.get("user_phone", "")
    email = st.session_state.get("user_email", "")

    with col_md:
        st.info("🔒 Downloads are locked until payment is confirmed.")

    with col_docx:
        st.write(f"Unlock premium downloads for **KES {amount:,}**")
        st.link_button("💬 Pay / Notify on WhatsApp", _build_whatsapp_link(user_id, phone, email, amount))
        st.caption(f"Your user ID: `{user_id}`")

        if st.button("✅ I have paid — start unlock check", key=f"paidbtn_{title}"):
            st.session_state["waiting_for_unlock"] = True

    with col_pdf:
        if st.session_state.get("waiting_for_unlock"):
            if st_autorefresh is not None:
                st_autorefresh(interval=4000, key=f"unlockpoll_{user_id}_{title}")

            if get_user_payment_status(user_id):
                st.success("✅ Payment confirmed! Downloads unlocked.")
                st.session_state["waiting_for_unlock"] = False
                st.rerun()
            else:
                st.info("Waiting for admin confirmation…")
                if st_autorefresh is None:
                    st.warning("Auto-unlock helper missing. Add `streamlit-autorefresh` to requirements.txt.")


def main():
    init_db()

    _require_login()

    user_id = st.session_state["user_id"]
    phone = st.session_state["user_phone"]
    email = st.session_state["user_email"]

    st.title("AI Career Accelerator – Built for Kenyan Graduates")
    st.caption(f"Logged in as: **{phone}** • **{email}**")
    st.caption(f"User ID: `{user_id}`")

    # Restore saved outputs so refresh is safe
    _hydrate_session_from_db(user_id)

    tab_job, tab_resume, tab_results = st.tabs(
        ["1. Job Description", "2. Your Resume", "3. AI Output & Downloads"]
    )

    with tab_job:
        job_description = st.text_area("Paste the job description", height=260)
        if st.button("🔍 Analyse Job"):
            if not job_description.strip():
                st.error("Please paste a job description first.")
            else:
                st.session_state["job_analysis"] = analyze_job(job_description)
                st.success("Job analysed successfully.")
        if "job_analysis" in st.session_state:
            st.json(st.session_state["job_analysis"])

    with tab_resume:
        option = st.radio("Provide your CV", ["Upload PDF Resume", "Enter Text Manually"])
        resume_text = ""

        if option == "Upload PDF Resume":
            uploaded = st.file_uploader("Upload resume PDF", type=["pdf"])
            if uploaded is not None:
                resume_text = extract_text_from_pdf(uploaded)
                st.text_area("Extracted text (editable)", resume_text, height=260, key="resume_pdf_text")
        else:
            resume_text = st.text_area("Paste your resume", height=260, key="resume_manual_text")

        if st.button("✍️ Generate AI Resume & Cover Letter"):
            if "job_analysis" not in st.session_state:
                st.error("Analyse a job description first.")
            elif not (resume_text or "").strip():
                st.error("Provide your resume text.")
            else:
                analysis = st.session_state["job_analysis"]
                ai_resume = rewrite_resume(resume_text, analysis)
                ai_cover = generate_cover_letter(resume_text, analysis)
                ai_emails = generate_emails(analysis)

                st.session_state["ai_resume_markdown"] = ai_resume
                st.session_state["ai_cover_letter"] = ai_cover
                st.session_state["ai_emails"] = ai_emails

                save_user_output(user_id, ai_resume, ai_cover, ai_emails)
                st.success("Generated and saved successfully ✅")

    with tab_results:
        paid = get_user_payment_status(user_id)

        ai_resume = st.session_state.get("ai_resume_markdown")
        ai_cover = st.session_state.get("ai_cover_letter")
        ai_emails = st.session_state.get("ai_emails", [])

        if not ai_resume and not ai_cover:
            st.info("Generate content first in Step 2.")
            return

        if ai_resume:
            st.markdown("## 🧾 AI Resume")
            st.markdown(ai_resume)
            premium_download_section(user_id, "AI Resume", ai_resume, paid, 1000)

        if ai_cover:
            st.markdown("## 📮 Cover Letter")
            st.markdown(ai_cover)
            premium_download_section(user_id, "Cover Letter", ai_cover, paid, 1000)

        if ai_emails:
            st.markdown("## ✉️ Email Strategy")
            for i, e in enumerate(ai_emails, start=1):
                st.markdown(f"### Email {i}: {e.get('label','Follow-up')}")
                st.markdown(f"**Subject:** {e.get('subject','')}")
                st.markdown(e.get("body", ""))
                st.markdown("---")


if __name__ == "__main__":
    main()

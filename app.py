# app.py
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

st.set_page_config(page_title="AI Career Accelerator", page_icon="🧠", layout="wide")


def _get_whatsapp_number() -> str:
    return (os.getenv("WHATSAPP_NUMBER") or "254722285538").strip()


def _whatsapp_link(user_id: str, phone: str, email: str, amount: int = 1000) -> str:
    msg = (
        f"Hi, I have paid KES {amount} for AI Career Accelerator.\n"
        f"User ID: {user_id}\nPhone: {phone}\nEmail: {email}\n"
        "Please confirm and unlock my downloads."
    )
    msg_encoded = msg.replace("\n", "%0A").replace(" ", "%20")
    return f"https://wa.me/{_get_whatsapp_number()}?text={msg_encoded}"


def _hydrate(user_id: str):
    saved = load_user_output(user_id)
    if not saved:
        return
    st.session_state.setdefault("ai_resume_markdown", saved.get("ai_resume_markdown", ""))
    st.session_state.setdefault("ai_cover_letter", saved.get("ai_cover_letter", ""))
    st.session_state.setdefault("ai_emails", saved.get("ai_emails", []))


def _markdown_to_docx(text: str) -> bytes:
    doc = Document()
    for line in text.splitlines():
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


def _markdown_to_pdf(text: str) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    for line in text.splitlines():
        if not line.strip():
            pdf.ln(5)
        else:
            pdf.multi_cell(0, 6, line)
    buff = BytesIO()
    pdf.output(buff)
    buff.seek(0)
    return buff.getvalue()


def _require_login():
    if st.session_state.get("user_id"):
        return

    st.title("Login")
    st.info("All users must login using **phone number** + **email**.")

    st.session_state.setdefault("login_attempts", 0)

    with st.form("login_form", clear_on_submit=False):
        phone = st.text_input("Phone", key="login_phone", placeholder="0722 123 456 or +254722123456")
        email = st.text_input("Email", key="login_email", placeholder="user@gmail.com")
        submitted = st.form_submit_button("Login")

    if not submitted:
        st.stop()

    st.session_state["login_attempts"] += 1
    phone = (phone or "").strip()
    email = (email or "").strip()

    if not phone or not email:
        st.error("Enter BOTH phone and email.")
        st.stop()

    with st.spinner("Signing you in…"):
        user = get_or_create_user(phone, email)

    if not user:
        st.error("Invalid phone/email. Try again.")
        st.stop()

    st.session_state["user_id"] = user["user_id"]
    st.session_state["user_phone"] = user["phone"]
    st.session_state["user_email"] = user["email"]
    st.success("✅ Logged in")
    st.rerun()


def premium_download(title: str, content: str, paid: bool, user_id: str):
    st.subheader(title)
    if paid:
        st.download_button("⬇️ Download .md", content.encode("utf-8"), f"{title}.md", "text/markdown")
        st.download_button("⬇️ Download .docx", _markdown_to_docx(content), f"{title}.docx",
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        st.download_button("⬇️ Download .pdf", _markdown_to_pdf(content), f"{title}.pdf", "application/pdf")
        return

    st.info("🔒 Locked until payment is confirmed.")
    st.link_button("💬 Pay / Notify on WhatsApp", _whatsapp_link(
        user_id,
        st.session_state.get("user_phone", ""),
        st.session_state.get("user_email", ""),
        1000
    ))

    if st.button("✅ I have paid — start unlock check", key=f"wait_{title}"):
        st.session_state["waiting_for_unlock"] = True

    if st.session_state.get("waiting_for_unlock"):
        if st_autorefresh is not None:
            st_autorefresh(interval=4000, key=f"poll_{user_id}_{title}")

        if get_user_payment_status(user_id):
            st.session_state["waiting_for_unlock"] = False
            st.success("✅ Payment confirmed! Downloads unlocked.")
            st.rerun()
        else:
            st.caption("Waiting for admin confirmation…")


def main():
    init_db()
    _require_login()

    user_id = st.session_state["user_id"]
    _hydrate(user_id)

    st.title("AI Career Accelerator")
    st.caption(f"Logged in: {st.session_state.get('user_phone')} • {st.session_state.get('user_email')}")
    paid = get_user_payment_status(user_id)

    tab_job, tab_resume, tab_results = st.tabs(["1) Job", "2) Resume", "3) Downloads"])

    with tab_job:
        jd = st.text_area("Paste job description", height=240)
        if st.button("Analyse Job"):
            if not jd.strip():
                st.error("Paste a job description.")
            else:
                st.session_state["job_analysis"] = analyze_job(jd)
                st.success("Analysed ✅")
        if "job_analysis" in st.session_state:
            st.json(st.session_state["job_analysis"])

    with tab_resume:
        mode = st.radio("How to provide CV?", ["Upload PDF", "Paste Text"])
        resume_text = ""
        if mode == "Upload PDF":
            f = st.file_uploader("Upload PDF", type=["pdf"])
            if f is not None:
                resume_text = extract_text_from_pdf(f)
                st.text_area("Extracted text (editable)", resume_text, height=240, key="resume_pdf_text")
        else:
            resume_text = st.text_area("Paste resume", height=240, key="resume_manual_text")

        if st.button("Generate"):
            if "job_analysis" not in st.session_state:
                st.error("Analyse the job first.")
            elif not (resume_text or "").strip():
                st.error("Provide resume text.")
            else:
                a = st.session_state["job_analysis"]
                ai_resume = rewrite_resume(resume_text, a)
                ai_cover = generate_cover_letter(resume_text, a)
                ai_emails = generate_emails(a)

                st.session_state["ai_resume_markdown"] = ai_resume
                st.session_state["ai_cover_letter"] = ai_cover
                st.session_state["ai_emails"] = ai_emails

                save_user_output(user_id, ai_resume, ai_cover, ai_emails)
                st.success("Generated + saved ✅")

    with tab_results:
        paid = get_user_payment_status(user_id)
        resume = st.session_state.get("ai_resume_markdown", "")
        cover = st.session_state.get("ai_cover_letter", "")
        emails = st.session_state.get("ai_emails", [])

        if not resume and not cover:
            st.info("Generate first.")
            return

        if resume:
            st.markdown(resume)
            premium_download("Resume", resume, paid, user_id)

        if cover:
            st.markdown(cover)
            premium_download("Cover Letter", cover, paid, user_id)

        if emails:
            st.markdown("## Email Strategy")
            st.json(emails)


if __name__ == "__main__":
    main()

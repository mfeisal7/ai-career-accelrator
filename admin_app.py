# admin_app.py
"""
Admin panel for manually unlocking paid users.

Enhancements:
- Search users by phone OR email
- Resolve stable user_id
- Show payment status
- Preview saved AI outputs
- One-click WhatsApp message copy
"""

import os
import streamlit as st

from payments_db import (
    is_user_paid,
    mark_user_paid,
    get_user_payments,
    load_user_output,
    get_or_create_user,
    normalize_phone,
    normalize_email,
)


# ============================================================
# Admin Auth
# ============================================================

def _get_admin_password() -> str | None:
    pw = os.getenv("ADMIN_PASSWORD")
    return pw.strip() if pw else None


# ============================================================
# Helpers
# ============================================================

def _copy_whatsapp_message(user_id: str, phone: str, email: str):
    message = (
        "Hi, I’ve received payment for the AI Career Accelerator.\n"
        f"User ID: {user_id}\n"
        f"Phone: {phone}\n"
        f"Email: {email}"
    )

    st.text_area(
        "WhatsApp Unlock Message (copy & send)",
        message,
        height=100,
        disabled=True,
    )

    st.markdown(
        f"""
        <button onclick="navigator.clipboard.writeText(`{message}`)"
                style="
                    padding:10px 16px;
                    border-radius:8px;
                    border:none;
                    background:#25D366;
                    color:white;
                    font-weight:600;
                    cursor:pointer;
                ">
            📋 Copy WhatsApp Message
        </button>
        """,
        unsafe_allow_html=True,
    )


def _render_saved_outputs(user_id: str):
    saved = load_user_output(user_id)

    if not saved:
        st.info("🗂️ No saved AI outputs found for this user.")
        return

    st.success("🗂️ Saved AI outputs found ✅")

    with st.expander("Preview Saved Resume"):
        resume = saved.get("ai_resume_markdown", "")
        st.markdown(resume) if resume.strip() else st.caption("No resume stored.")

    with st.expander("Preview Saved Cover Letter"):
        cover = saved.get("ai_cover_letter", "")
        st.markdown(cover) if cover.strip() else st.caption("No cover letter stored.")

    with st.expander("Preview Saved Emails (JSON)"):
        emails = saved.get("ai_emails", [])
        st.json(emails) if emails else st.caption("No emails stored.")


# ============================================================
# Admin App
# ============================================================

def run_admin_panel():
    st.set_page_config(
        page_title="Admin – AI Career Accelerator",
        page_icon="🔐",
        layout="wide",
    )

    st.title("🔐 Admin Panel – Premium Unlocks")

    expected_pw = _get_admin_password()
    if not expected_pw:
        st.error("ADMIN_PASSWORD not set in environment variables.")
        st.stop()

    with st.sidebar:
        st.header("Admin Login")
        entered_pw = st.text_input("Password", type="password")
        if entered_pw != expected_pw:
            st.warning("Enter the admin password to continue.")
            st.stop()

    st.success("✅ Logged in")

    st.markdown("---")
    st.subheader("Find User (Phone or Email)")

    phone = st.text_input("User Phone (optional)", placeholder="0722 123 456 or +254722123456")
    email = st.text_input("User Email (optional)", placeholder="user@gmail.com")

    user = None
    if st.button("🔍 Find User"):
        phone_n = normalize_phone(phone) if phone else ""
        email_n = normalize_email(email) if email else ""

        if not phone_n and not email_n:
            st.error("Enter at least a phone number or email.")
        else:
            user = get_or_create_user(phone_n, email_n)
            if not user:
                st.error("User not found or invalid input.")
            else:
                st.success("User found ✅")
                st.session_state["admin_user"] = user

    user = st.session_state.get("admin_user")

    if user:
        user_id = user["user_id"]
        phone = user["phone"]
        email = user["email"]

        st.markdown("---")
        st.subheader("User Details")

        st.write(f"**User ID:** `{user_id}`")
        st.write(f"**Phone:** {phone}")
        st.write(f"**Email:** {email}")

        paid = is_user_paid(user_id)
        st.success("✅ User is PAID") if paid else st.info("🔒 User is NOT paid")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### Saved Outputs")
            _render_saved_outputs(user_id)

        with col2:
            st.markdown("### Actions")
            if st.button("✅ Mark as Paid"):
                ok = mark_user_paid(user_id)
                if ok:
                    st.success("User marked as PAID. Downloads will unlock immediately.")
                else:
                    st.error("Failed to mark user as paid.")

            st.markdown("### WhatsApp Message")
            _copy_whatsapp_message(user_id, phone, email)

    st.markdown("---")
    st.subheader("Recent Payments / Unlocks")

    try:
        rows = get_user_payments(limit=50)
        if rows:
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No payments recorded yet.")
    except Exception as e:
        st.error(f"Failed to load payments: {e}")


if __name__ == "__main__":
    run_admin_panel()

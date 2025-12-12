# admin_app.py
"""
Admin panel for manually unlocking paid users.

Enhancements:
- Shows whether user has generated AI outputs saved in DB
- Allows preview of saved outputs
- Provides a one-click WhatsApp message copy button (includes user_id)
"""

import os
import streamlit as st

from payments_db import (
    is_user_paid,
    mark_user_paid,
    get_user_payments,
    load_user_output,
)


# ============================================================
# Admin Auth
# ============================================================

def _get_admin_password() -> str | None:
    """Load ADMIN_PASSWORD from environment only."""
    pw = os.getenv("ADMIN_PASSWORD")
    return pw.strip() if pw else None


# ============================================================
# Helpers
# ============================================================

def _copy_whatsapp_message(user_id: str):
    """
    Render a copy-to-clipboard button for WhatsApp unlock message.
    """
    message = (
        "Hi, I’ve received payment for the AI Career Accelerator. "
        f"My user ID is: {user_id}"
    )

    st.text_area(
        "WhatsApp Unlock Message (copy & send)",
        message,
        height=80,
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
    """
    Show whether user has saved AI outputs and allow preview.
    """
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
    st.subheader("User Status & Manual Unlock")

    user_id = st.text_input("User ID (from user app)").strip()

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Check Status"):
            if not user_id:
                st.error("Please enter a user_id")
            else:
                paid = is_user_paid(user_id)
                st.success("✅ User is PAID") if paid else st.info("🔒 User is NOT paid")

                st.markdown("### Saved Outputs")
                _render_saved_outputs(user_id)

                st.markdown("### WhatsApp Message")
                _copy_whatsapp_message(user_id)

    with col2:
        if st.button("Mark as Paid"):
            if not user_id:
                st.error("Please enter a user_id")
            else:
                ok = mark_user_paid(user_id)
                if ok:
                    st.success("✅ User marked as PAID. Tell them to refresh.")
                else:
                    st.error("Failed to mark user as paid.")

                st.markdown("### Saved Outputs")
                _render_saved_outputs(user_id)

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

# admin_app.py
"""
Admin panel for manually unlocking paid users.
Can be run directly with `streamlit run admin_app.py`
or imported from app.py when IS_ADMIN_PANEL=1.
"""

import os
import streamlit as st

from payments_db import (
    is_user_paid,
    mark_user_paid,
    get_user_payments,
)


def _get_admin_password() -> str | None:
    """Load ADMIN_PASSWORD from environment only.

    Avoids touching st.secrets, which triggers Streamlit's "No secrets found"
    warning when secrets.toml is absent (Railway production).
    """
    env_pw = os.getenv("ADMIN_PASSWORD")
    if env_pw:
        return env_pw.strip()
    return None


def run_admin_panel():
    st.set_page_config(
        page_title="Admin – AI Career Accelerator",
        page_icon="🔐",
        layout="wide",
    )

    st.title("🔐 Admin Panel – Premium Unlocks")

    expected_admin_pw = _get_admin_password()
    if not expected_admin_pw:
        st.error(
            "ADMIN_PASSWORD is not configured in environment variables. "
            "Set it before using the admin panel."
        )
        st.stop()

    with st.sidebar:
        st.header("Admin Login")
        entered_pw = st.text_input("Password", type="password")

        if entered_pw != expected_admin_pw:
            st.warning("Enter the admin password to continue.")
            st.stop()

    st.success("✅ Logged in")

    st.markdown("---")
    st.subheader("Find User Payment Status")

    user_id = st.text_input("User ID (from app)", value="")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Check Status"):
            if not user_id.strip():
                st.error("Please enter a user_id")
            else:
                paid = is_user_paid(user_id.strip())
                if paid:
                    st.success("✅ User is PAID (downloads unlocked)")
                else:
                    st.info("🔒 User is NOT paid yet")

    with col2:
        if st.button("Mark as Paid"):
            if not user_id.strip():
                st.error("Please enter a user_id")
            else:
                mark_user_paid(user_id.strip())
                st.success("✅ User marked as PAID. They can refresh to unlock downloads.")

    st.markdown("---")
    st.subheader("Recent Payments / Unlocks")

    try:
        rows = get_user_payments(limit=50)
        if not rows:
            st.info("No payments recorded yet.")
        else:
            st.dataframe(rows, use_container_width=True)
    except Exception as e:
        st.error(f"Failed to load payments: {e}")


if __name__ == "__main__":
    run_admin_panel()

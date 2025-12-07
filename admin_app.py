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
    """
    Try to load ADMIN_PASSWORD from environment first, then from st.secrets.
    Never crash if secrets.toml is missing.
    """
    env_pw = os.getenv("ADMIN_PASSWORD")
    if env_pw:
        return env_pw

    # Only try st.secrets if a secrets file exists
    try:
        # st.secrets behaves like a dict, but will raise FileNotFoundError
        # if no secrets file is configured.
        return st.secrets["ADMIN_PASSWORD"]
    except FileNotFoundError:
        return None
    except KeyError:
        return None


def run_admin_panel():
    st.set_page_config(
        page_title="AI Career Accelerator – Admin",
        page_icon="🔐",
        layout="centered",
    )

    st.title("🔐 AI Career Accelerator – Admin Panel")

    # ------------------------------
    # Authentication
    # ------------------------------
    expected_admin_pw = _get_admin_password()

    if not expected_admin_pw:
        st.error(
            "ADMIN_PASSWORD is not configured in environment variables or Streamlit secrets. "
            "Set it before using the admin panel."
        )
        st.stop()

    if "admin_authenticated" not in st.session_state:
        st.session_state["admin_authenticated"] = False

    if not st.session_state["admin_authenticated"]:
        admin_pw = st.text_input("Admin password", type="password")

        if st.button("Log in"):
            if admin_pw == expected_admin_pw:
                st.session_state["admin_authenticated"] = True
                st.success("Admin authenticated.")
            else:
                st.error("Incorrect admin password.")

        st.stop()

    st.success("You are logged in as admin.")

    # ------------------------------
    # User lookup & manual unlock
    # ------------------------------
    st.subheader("Lookup & Unlock User")

    user_id_input = st.text_input(
        "User ID from the client (copy-pasted from WhatsApp)",
        placeholder="e.g. 8136f4cb-bd00-43a9-bcda-5d6489098c55",
    )

    col1, col2 = st.columns(2)

    if col1.button("Check payment status", use_container_width=True):
        if not user_id_input.strip():
            st.error("Please enter a user_id.")
        else:
            paid = is_user_paid(user_id_input.strip())
            if paid:
                st.success("✅ This user is already marked as PAID.")
            else:
                st.warning("❌ This user is currently NOT marked as paid.")

    if col2.button("Mark as PAID", use_container_width=True):
        if not user_id_input.strip():
            st.error("Please enter a user_id.")
        else:
            ok = mark_user_paid(user_id_input.strip())
            if ok:
                st.success(f"User {user_id_input.strip()} has been marked as PAID.")
            else:
                st.error("Failed to mark user as paid (see logs).")

    if user_id_input.strip():
        with st.expander("Show payment records for this user"):
            records = get_user_payments(user_id_input.strip())
            if not records:
                st.info("No payment records found for this user.")
            else:
                st.write(records)


# Allow running admin app directly (optional)
if __name__ == "__main__":
    run_admin_panel()

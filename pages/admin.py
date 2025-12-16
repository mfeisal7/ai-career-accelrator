# pages/admin.py
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

st.set_page_config(page_title="Admin – AI Career Accelerator", page_icon="🔐", layout="wide")


def _get_admin_password() -> str | None:
    pw = os.getenv("ADMIN_PASSWORD")
    return pw.strip() if pw else None


def _copy_whatsapp_message(user_id: str, phone: str, email: str):
    message = (
        "Hi, I’ve received payment for the AI Career Accelerator.\n"
        f"User ID: {user_id}\n"
        f"Phone: {phone}\n"
        f"Email: {email}"
    )

    st.text_area("WhatsApp Message (copy & send)", message, height=110, disabled=True)

    st.markdown(
        f"""
        <button onclick="navigator.clipboard.writeText(`{message}`)"
                style="
                    padding:10px 16px;border-radius:8px;border:none;
                    background:#25D366;color:white;font-weight:600;cursor:pointer;">
            📋 Copy WhatsApp Message
        </button>
        """,
        unsafe_allow_html=True,
    )


def _render_saved_outputs(user_id: str):
    saved = load_user_output(user_id)

    if not saved:
        st.info("🗂️ No saved outputs for this user yet.")
        return

    st.success("🗂️ Saved outputs found ✅")

    with st.expander("Preview Saved Resume"):
        resume = saved.get("ai_resume_markdown", "")
        st.markdown(resume) if resume.strip() else st.caption("No resume stored.")

    with st.expander("Preview Saved Cover Letter"):
        cover = saved.get("ai_cover_letter", "")
        st.markdown(cover) if cover.strip() else st.caption("No cover letter stored.")

    with st.expander("Preview Saved Emails (JSON)"):
        emails = saved.get("ai_emails", [])
        st.json(emails) if emails else st.caption("No emails stored.")


def main():
    st.title("🔐 Admin – Premium Unlocks")

    expected_pw = _get_admin_password()
    if not expected_pw:
        st.error("ADMIN_PASSWORD is not set in Railway variables.")
        st.stop()

    with st.sidebar:
        st.header("Admin Login")
        entered = st.text_input("Password", type="password")
        if entered != expected_pw:
            st.warning("Enter the admin password to continue.")
            st.stop()

    st.success("✅ Logged in")

    st.markdown("---")
    st.subheader("Find User (Phone + Email)")

    phone = st.text_input("Phone", placeholder="0722 123 456 or +254722123456")
    email = st.text_input("Email", placeholder="user@gmail.com")

    if st.button("🔍 Find User"):
        phone_n = normalize_phone(phone)
        email_n = normalize_email(email)

        if not phone_n or not email_n:
            st.error("Enter BOTH phone and email (same as user login).")
            st.stop()

        user = get_or_create_user(phone_n, email_n)
        if not user:
            st.error("User not found / invalid input.")
            st.stop()

        st.session_state["admin_user"] = user

    user = st.session_state.get("admin_user")
    if user:
        user_id = user["user_id"]
        st.markdown("---")
        st.subheader("User Details")
        st.write(f"**User ID:** `{user_id}`")
        st.write(f"**Phone:** {user['phone']}")
        st.write(f"**Email:** {user['email']}")

        paid = is_user_paid(user_id)
        st.success("✅ PAID") if paid else st.info("🔒 NOT PAID")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### Saved Outputs")
            _render_saved_outputs(user_id)

        with col2:
            st.markdown("### Actions")
            if st.button("✅ Mark as Paid"):
                ok = mark_user_paid(user_id)
                st.success("Marked as paid ✅") if ok else st.error("Failed to mark paid.")

            st.markdown("### WhatsApp Message")
            _copy_whatsapp_message(user_id, user["phone"], user["email"])

    st.markdown("---")
    st.subheader("Recent Payments / Unlocks")
    rows = get_user_payments(limit=50)
    st.dataframe(rows, use_container_width=True) if rows else st.info("No payments yet.")


if __name__ == "__main__":
    main()

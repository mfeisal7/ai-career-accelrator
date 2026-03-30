# pages/admin.py — AI Career Accelerator Admin Panel (Rebuilt)
import os
import streamlit as st

from payments_db import (
    is_user_paid,
    get_user_tier,
    mark_user_paid,
    get_user_payments,
    get_all_leads,
    get_dashboard_stats,
    load_user_output,
    get_or_create_user,
    normalize_phone,
    normalize_email,
    TIER_PRICES,
)

st.set_page_config(page_title="Admin — AI Career Accelerator", page_icon="🔐", layout="wide")

TIERS = ["starter", "pro", "executive"]


def _get_admin_password() -> str | None:
    pw = os.getenv("ADMIN_PASSWORD")
    return pw.strip() if pw else None


def _render_revenue_dashboard():
    st.markdown("## 📊 Revenue Dashboard")
    stats = get_dashboard_stats()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Users", stats["total_users"])
    c2.metric("Paid Customers", stats["total_paid"])
    c3.metric("Total Revenue", f"KSh {stats['total_revenue_ksh']:,.0f}")
    c4.metric("Leads Captured", stats["total_leads"])

    st.markdown("")

    tier_counts = stats.get("tier_counts", {})
    ct1, ct2, ct3 = st.columns(3)
    with ct1:
        rev = tier_counts.get("starter", 0) * TIER_PRICES.get("starter", 1999)
        st.markdown(f"""
        <div style='background:#1e293b; border-radius:12px; padding:16px; text-align:center; border:1px solid #334155;'>
            <div style='font-size:12px; color:#64748b; text-transform:uppercase; letter-spacing:1px;'>Starter</div>
            <div style='font-size:28px; font-weight:800; color:#3b82f6;'>{tier_counts.get("starter", 0)}</div>
            <div style='font-size:11px; color:#94a3b8;'>KSh {rev:,} revenue</div>
        </div>
        """, unsafe_allow_html=True)
    with ct2:
        rev = tier_counts.get("pro", 0) * TIER_PRICES.get("pro", 3999)
        st.markdown(f"""
        <div style='background:#1e293b; border-radius:12px; padding:16px; text-align:center; border:2px solid #10b981;'>
            <div style='font-size:12px; color:#10b981; text-transform:uppercase; letter-spacing:1px;'>Pro 🔥</div>
            <div style='font-size:28px; font-weight:800; color:#10b981;'>{tier_counts.get("pro", 0)}</div>
            <div style='font-size:11px; color:#94a3b8;'>KSh {rev:,} revenue</div>
        </div>
        """, unsafe_allow_html=True)
    with ct3:
        rev = tier_counts.get("executive", 0) * TIER_PRICES.get("executive", 7999)
        st.markdown(f"""
        <div style='background:#1e293b; border-radius:12px; padding:16px; text-align:center; border:1px solid #7c3aed;'>
            <div style='font-size:12px; color:#a78bfa; text-transform:uppercase; letter-spacing:1px;'>Executive</div>
            <div style='font-size:28px; font-weight:800; color:#a78bfa;'>{tier_counts.get("executive", 0)}</div>
            <div style='font-size:11px; color:#94a3b8;'>KSh {rev:,} revenue</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")


def _render_saved_outputs(user_id: str):
    saved = load_user_output(user_id)
    if not saved:
        st.info("No saved outputs for this user yet.")
        return

    st.success("Saved outputs found ✅")
    with st.expander("Preview Resume"):
        resume = saved.get("ai_resume_markdown", "")
        st.markdown(resume) if resume.strip() else st.caption("No resume stored.")

    with st.expander("Preview Cover Letter"):
        cover = saved.get("ai_cover_letter", "")
        st.markdown(cover) if cover.strip() else st.caption("No cover letter stored.")

    with st.expander("Preview Emails (JSON)"):
        emails = saved.get("ai_emails", [])
        st.json(emails) if emails else st.caption("No emails stored.")


def main():
    st.title("🔐 Admin — AI Career Accelerator")

    expected_pw = _get_admin_password()
    if not expected_pw:
        st.error("ADMIN_PASSWORD environment variable is not set.")
        st.stop()

    with st.sidebar:
        st.header("Admin Login")
        entered = st.text_input("Password", type="password")
        if entered != expected_pw:
            st.warning("Enter the admin password to continue.")
            st.stop()
        st.success("✅ Logged in")
        st.markdown("---")
        st.markdown("**Quick links:**")
        st.markdown("- [Dashboard](#revenue-dashboard)")
        st.markdown("- [Find User](#find-user)")
        st.markdown("- [All Payments](#recent-payments)")
        st.markdown("- [Leads](#email-leads)")

    # ── Revenue Dashboard ──
    _render_revenue_dashboard()

    # ── Find & Manage User ──
    st.markdown("## 🔍 Find User")
    col_p, col_e = st.columns(2)
    with col_p:
        phone = st.text_input("Phone", placeholder="0722 123 456 or +254722123456")
    with col_e:
        email = st.text_input("Email", placeholder="user@gmail.com")

    if st.button("🔍 Find User", type="primary"):
        phone_n = normalize_phone(phone)
        email_n = normalize_email(email)

        if not phone_n or not email_n:
            st.error("Enter BOTH phone and email (same as the user used to log in).")
        else:
            user = get_or_create_user(phone_n, email_n)
            if user:
                st.session_state["admin_user"] = user
            else:
                st.error("Could not find or create user with these credentials.")

    user = st.session_state.get("admin_user")
    if user:
        user_id = user["user_id"]
        current_tier = get_user_tier(user_id)

        st.markdown("---")
        col_info, col_actions = st.columns([1, 1])

        with col_info:
            st.markdown("### User Details")
            st.write(f"**User ID:** `{user_id}`")
            st.write(f"**Phone:** {user['phone']}")
            st.write(f"**Email:** {user['email']}")

            tier_color = {"none": "⬜", "starter": "🔵", "pro": "🟢", "executive": "🟣"}.get(current_tier, "⬜")
            st.write(f"**Current Plan:** {tier_color} **{current_tier.upper()}**")

            if current_tier == "none":
                st.warning("Not paid yet")
            else:
                st.success(f"✅ Paid — {current_tier.upper()} plan")

            st.markdown("### Saved Outputs")
            _render_saved_outputs(user_id)

        with col_actions:
            st.markdown("### Unlock / Grant Plan Access")
            st.caption("Use this after confirming M-Pesa payment from the customer.")

            selected_tier = st.selectbox("Grant access to", TIERS, index=TIERS.index("pro"))

            if st.button(f"✅ Mark as {selected_tier.upper()} PAID", type="primary"):
                ok = mark_user_paid(user_id, tier=selected_tier)
                if ok:
                    st.success(f"✅ {user['phone']} is now unlocked for {selected_tier.upper()}")
                    st.session_state["admin_user"]["tier"] = selected_tier
                    st.rerun()
                else:
                    st.error("Failed to mark as paid. Check logs.")

            st.markdown("---")
            st.markdown("### WhatsApp Confirmation Message")
            msg = (
                f"Hi! ✅ Your AI Career Accelerator {selected_tier.capitalize()} plan has been unlocked.\n\n"
                f"Go to https://app.aicareer.co.ke, log in with your phone ({user['phone']}) and email, "
                f"then click 'refresh my access'. All {selected_tier} features are now available.\n\n"
                f"Any issues? Reply to this message."
            )
            st.text_area("Send this on WhatsApp to the user", msg, height=150)

            wa_link = f"https://wa.me/{user['phone']}?text={msg.replace(chr(10), '%0A').replace(' ', '%20')}"
            st.link_button("💬 Open WhatsApp to send", wa_link, use_container_width=True)

        # Payment history for this user
        payments = get_user_payments(user_id=user_id)
        if payments:
            st.markdown("### Payment History")
            st.dataframe(payments, use_container_width=True)

    # ── All Recent Payments ──
    st.markdown("---")
    st.markdown("## 💳 Recent Payments")
    rows = get_user_payments(limit=100)
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("No payments recorded yet.")

    # ── Email Leads ──
    st.markdown("---")
    st.markdown("## 📧 Email Leads")
    st.caption("Everyone who has logged into the app (whether paid or not). Use for follow-up marketing.")
    leads = get_all_leads(limit=200)
    if leads:
        st.dataframe(leads, use_container_width=True)

        import csv
        from io import StringIO
        buf = StringIO()
        writer = csv.DictWriter(buf, fieldnames=["id", "phone", "email", "source", "created_at"])
        writer.writeheader()
        writer.writerows(leads)
        st.download_button(
            "⬇️ Download Leads as CSV",
            buf.getvalue().encode("utf-8"),
            "leads.csv",
            "text/csv",
        )
    else:
        st.info("No leads yet. They will appear here as users log into the app.")


if __name__ == "__main__":
    main()

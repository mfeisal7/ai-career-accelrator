"""
follow_up.py — AI Career Accelerator Lead Follow-Up Tool
========================================================
Run this script to see all unconverted leads and get ready-made
WhatsApp message templates to send them.

Usage:
    python follow_up.py              # Shows all leads + messages
    python follow_up.py --export     # Exports to leads_followup.csv
    python follow_up.py --days 7     # Only leads from last 7 days

This is your most direct path to revenue: these are real people who
already know about the product and created an account. They just
haven't paid yet.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Make sure we can import payments_db from the project root
sys.path.insert(0, str(Path(__file__).parent))
from payments_db import get_all_leads, get_user_tier, make_user_id, normalize_phone, normalize_email

WA_NUMBER = "254722285538"


# ─────────────────────────────────────────────
# MESSAGE TEMPLATES
# ─────────────────────────────────────────────

def _message_day1(phone: str) -> str:
    return (
        f"Hi! 👋 You recently created an account on AI Career Accelerator (aicareer.co.ke).\n\n"
        f"Just checking in — did you get a chance to generate your AI job kit? "
        f"The app analyses any job description and builds you a full Kenyan CV, cover letter, "
        f"and interview prep guide in under 10 minutes.\n\n"
        f"Try it free → https://app.aicareer.co.ke\n\n"
        f"Any questions? Reply here 🙏"
    )


def _message_day3(phone: str) -> str:
    return (
        f"Hi again! 👋 Quick one from AI Career Accelerator.\n\n"
        f"The Pro Plan (KSh 3,999) is still available at launch price. "
        f"It includes your AI CV, cover letter, LinkedIn optimization, AND a full "
        f"13-question interview prep guide with strong answers — all tailored to the exact job you're applying for.\n\n"
        f"Thousands of Kenyan graduates send the same generic CV. This makes yours impossible to ignore.\n\n"
        f"Get Pro → https://aicareer.co.ke/#pricing\n\n"
        f"Or ask me anything here 💬"
    )


def _message_day7(phone: str) -> str:
    return (
        f"Hey! 👋 Final nudge from AI Career Accelerator.\n\n"
        f"Launch pricing ends soon. After that, Pro goes from KSh 3,999 → KSh 8,000.\n\n"
        f"If you're currently job hunting in Kenya and not getting replies, "
        f"the problem is almost certainly your CV — not your skills. "
        f"The AI fixes that in 10 minutes.\n\n"
        f"3 interviews in 45 days or full M-Pesa refund. Zero risk.\n\n"
        f"Get in before the price goes up → https://aicareer.co.ke/#pricing\n\n"
        f"Questions? I'm on WhatsApp all day 📲"
    )


def _message_win_back(phone: str) -> str:
    return (
        f"Hi! We noticed you created an account on AI Career Accelerator a while ago. 👋\n\n"
        f"Are you still job hunting in Kenya? We've added new features:\n"
        f"✅ LinkedIn profile optimizer\n"
        f"✅ 13-question interview prep guide with Kenyan market context\n"
        f"✅ Salary negotiation script\n\n"
        f"Still just KSh 3,999 one-time. 3 interviews in 45 days or refund.\n\n"
        f"Log back in → https://app.aicareer.co.ke"
    )


TEMPLATES = {
    "Day 1 — Welcome & Try": _message_day1,
    "Day 3 — Feature highlight": _message_day3,
    "Day 7 — Urgency / closing": _message_day7,
    "Win-back (2+ weeks)": _message_win_back,
}


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def get_unconverted_leads(days_back: int = 0) -> list:
    """
    Returns all leads who have not paid for any tier.
    Optionally filter to leads created in the last `days_back` days.
    """
    all_leads = get_all_leads(limit=1000)
    results = []

    cutoff = None
    if days_back > 0:
        cutoff = datetime.utcnow() - timedelta(days=days_back)

    for lead in all_leads:
        # Check if they've paid
        phone_n = normalize_phone(lead.get("phone", ""))
        email_n = normalize_email(lead.get("email", ""))

        if not phone_n or not email_n:
            continue

        user_id = make_user_id(phone_n, email_n)
        tier = get_user_tier(user_id)

        if tier != "none":
            continue  # Already paid — skip

        # Date filter
        if cutoff:
            created = lead.get("created_at", "")
            try:
                lead_dt = datetime.fromisoformat(created.replace("Z", "+00:00").replace("Z", ""))
                if lead_dt < cutoff:
                    continue
            except Exception:
                pass

        results.append({
            "phone": phone_n,
            "email": email_n,
            "source": lead.get("source", ""),
            "created_at": lead.get("created_at", ""),
            "user_id": user_id,
            "tier": tier,
        })

    return results


def print_follow_up_report(leads: list, template_name: str = "Day 1 — Welcome & Try"):
    template_fn = TEMPLATES.get(template_name, _message_day1)

    print(f"\n{'='*60}")
    print(f"  AI CAREER ACCELERATOR — LEAD FOLLOW-UP REPORT")
    print(f"{'='*60}")
    print(f"  Unconverted leads: {len(leads)}")
    print(f"  Template: {template_name}")
    print(f"{'='*60}\n")

    if not leads:
        print("✅ No unconverted leads found. Everyone has either paid or there are no leads yet.")
        return

    for i, lead in enumerate(leads, 1):
        phone = lead["phone"]
        email = lead["email"]
        wa_link = f"https://wa.me/{phone}"
        message = template_fn(phone)

        print(f"[{i}] Phone: {phone}  |  Email: {email}")
        print(f"     WhatsApp: {wa_link}")
        print(f"     Created: {lead['created_at']}")
        print(f"     Message to send:")
        print("     " + "\n     ".join(message.split("\n")))
        print()

    print(f"\n{'='*60}")
    print(f"  SENDING GUIDE:")
    print(f"  1. Open WhatsApp on your phone")
    print(f"  2. Click each link above — it opens a chat with that person")
    print(f"  3. Copy & paste the message above")
    print(f"  4. Send. That's it.")
    print(f"\n  Aim for 5-10 messages per day. Even a 20% conversion rate")
    print(f"  on {len(leads)} leads = {max(1, int(len(leads)*0.2))} new customers.")
    print(f"  At KSh 3,999 each = KSh {max(1, int(len(leads)*0.2)) * 3999:,}")
    print(f"{'='*60}\n")


def export_to_csv(leads: list, filename: str = "leads_followup.csv"):
    if not leads:
        print("No leads to export.")
        return

    output_path = Path(__file__).parent / filename
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["phone", "email", "source", "created_at", "wa_link", "message_day1"])
        writer.writeheader()
        for lead in leads:
            writer.writerow({
                "phone": lead["phone"],
                "email": lead["email"],
                "source": lead["source"],
                "created_at": lead["created_at"],
                "wa_link": f"https://wa.me/{lead['phone']}",
                "message_day1": _message_day1(lead["phone"]).replace("\n", " | "),
            })
    print(f"✅ Exported {len(leads)} leads to {output_path}")


def print_revenue_projection(leads: list):
    n = len(leads)
    print(f"\n{'='*60}")
    print("  REVENUE PROJECTION (unconverted leads)")
    print(f"{'='*60}")
    for rate, label in [(0.05, "5% conversion (conservative)"),
                        (0.10, "10% conversion (realistic)"),
                        (0.20, "20% conversion (strong follow-up)")]:
        conversions = max(0, int(n * rate))
        starter_rev = conversions * 1999
        pro_rev = conversions * 3999
        print(f"  {label}:")
        print(f"    {conversions} customers × Starter (KSh 1,999) = KSh {starter_rev:,}")
        print(f"    {conversions} customers × Pro     (KSh 3,999) = KSh {pro_rev:,}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Career Accelerator — Lead Follow-Up Tool")
    parser.add_argument("--days", type=int, default=0, help="Only show leads from last N days (0 = all)")
    parser.add_argument("--template", type=str, default="Day 1 — Welcome & Try",
                        choices=list(TEMPLATES.keys()), help="Message template to use")
    parser.add_argument("--export", action="store_true", help="Export leads to CSV")
    parser.add_argument("--projection", action="store_true", help="Show revenue projection")
    args = parser.parse_args()

    leads = get_unconverted_leads(days_back=args.days)

    print_follow_up_report(leads, template_name=args.template)

    if args.projection:
        print_revenue_projection(leads)

    if args.export:
        export_to_csv(leads)

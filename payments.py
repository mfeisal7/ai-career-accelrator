# payments.py
"""
Backend IntaSend M-Pesa helper functions.

This module is UI-agnostic. It only exposes:
- get_intasend_config()
- trigger_mpesa_payment()
- check_payment_status()

All Streamlit UI code must live in app.py.
"""

from typing import Optional

import requests
import streamlit as st


def normalize_phone(phone_number: str) -> str:
    """
    Normalize Kenyan phone numbers to '2547XXXXXXXX' format.

    Accepts:
      - 07XXXXXXXX
      - 7XXXXXXXX
      - 2547XXXXXXXX
      - +2547XXXXXXXX
    """
    msisdn = phone_number.strip().replace(" ", "")

    # Strip leading '+'
    if msisdn.startswith("+"):
        msisdn = msisdn[1:]

    # 07XXXXXXXX -> 2547XXXXXXXX
    if msisdn.startswith("0") and len(msisdn) == 10:
        msisdn = "254" + msisdn[1:]
    # 7XXXXXXXX  -> 2547XXXXXXXX
    elif msisdn.startswith("7") and len(msisdn) == 9:
        msisdn = "254" + msisdn
    # If it already starts with 254, leave it
    # Otherwise leave as-is (IntaSend will validate)

    return msisdn


def get_intasend_config() -> dict:
    """
    Load IntaSend configuration from Streamlit secrets using FLAT keys.

    Expected structure in .streamlit/secrets.toml:

    INTASEND_PUBLISHABLE_KEY = "pk_live_..."
    INTASEND_API_KEY         = "sk_live_..."
    INTASEND_BASE_URL        = "https://payment.intasend.com/api/v1"  # optional
    INTASEND_WEBHOOK_URL     = "https://your-domain.com/intasend/webhook"  # optional
    """
    publishable_key = st.secrets["INTASEND_PUBLISHABLE_KEY"]
    api_key = st.secrets["INTASEND_API_KEY"]

    # Optional values
    base_url_secret = st.secrets.get("INTASEND_BASE_URL", "").strip()
    # If not provided, fall back to IntaSend's default public API base
    base_url = base_url_secret or "https://payment.intasend.com/api/v1"

    webhook_url = st.secrets.get(
        "INTASEND_WEBHOOK_URL",
        "https://example.com/intasend/webhook",
    )

    return {
        "publishable_key": publishable_key,
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "webhook_url": webhook_url,
    }


def trigger_mpesa_payment(phone_number: str, amount: int) -> Optional[str]:
    """
    Initiate an M-Pesa STK push via IntaSend.

    Args:
        phone_number: User's phone number (e.g. 0712..., 7..., 2547..., +2547...).
        amount: Amount in KES.

    Returns:
        invoice_id (str) if the request was accepted by IntaSend,
        or None if there was an error.
    """
    try:
        cfg = get_intasend_config()
    except Exception as e:
        print(f"[IntaSend] Missing or invalid configuration: {e}")
        return None

    msisdn = normalize_phone(phone_number)

    url = f'{cfg["base_url"]}/payment/mpesa/'

    payload = {
        "public_key": cfg["publishable_key"],
        "amount": str(int(amount)),
        "phone_number": msisdn,
        "currency": "KES",
        # You can add "email", "name", etc., if you collect them
    }

    headers = {
        "Authorization": f'Bearer {cfg["api_key"]}',
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        # IntaSend usually returns an invoice or reference ID
        invoice_id = data.get("invoice", data.get("invoice_id"))
        if invoice_id:
            return invoice_id

        print(f"[IntaSend] Unexpected payment response: {data}")
        return None
    except Exception as e:
        print(f"[IntaSend] STK push failed: {e}")
        return None


def check_payment_status(invoice_id: str) -> Optional[bool]:
    """
    Check IntaSend payment status for a given invoice.

    Returns:
        True  -> Payment successful
        False -> Payment failed / cancelled
        None  -> Still pending or unknown
    """
    try:
        cfg = get_intasend_config()
    except Exception as e:
        print(f"[IntaSend] Missing or invalid configuration: {e}")
        return None

    url = f'{cfg["base_url"]}/payment/status/{invoice_id}/'

    headers = {
        "Authorization": f'Bearer {cfg["api_key"]}',
        "Accept": "application/json",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # Common IntaSend statuses: "PENDING", "PAID", "FAILED", etc.
        status = (data.get("state") or data.get("status") or "").upper()

        if status in {"PAID", "COMPLETED", "SUCCESS"}:
            return True
        if status in {"FAILED", "CANCELLED", "DECLINED"}:
            return False

        # Any other state (including PENDING) -> still in progress
        return None
    except Exception as e:
        print(f"[IntaSend] Status check failed: {e}")
        return None

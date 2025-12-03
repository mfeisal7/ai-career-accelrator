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
import re

import requests
import streamlit as st


def normalize_phone(phone_number: str) -> str:
    """
    Normalize Kenyan phone numbers to '2547XXXXXXXX' / '2541XXXXXXXX' format.

    Accepts common input formats:
      - "07XXXXXXXX"       -> "2547XXXXXXXX"
      - "01XXXXXXXX"       -> "2541XXXXXXXX" (new Safaricom prefixes)
      - "7XXXXXXXX"        -> "2547XXXXXXXX"
      - "1XXXXXXXX"        -> "2541XXXXXXXX"
      - "2547XXXXXXXX"     -> "2547XXXXXXXX"
      - "2541XXXXXXXX"     -> "2541XXXXXXXX"
      - "+2547XXXXXXXX"    -> "2547XXXXXXXX"
      - "+254 7XX XXX XXX" -> "2547XXXXXXXX"

    Non-digit characters (spaces, dashes, parentheses, +) are stripped.
    """
    # Keep digits only (drops spaces, +, dashes, etc.)
    digits = re.sub(r"\D", "", phone_number or "")

    # 07XXXXXXXX or 01XXXXXXXX -> 2547XXXXXXXX / 2541XXXXXXXX
    if len(digits) == 10 and digits.startswith(("07", "01")):
        return "254" + digits[1:]

    # 7XXXXXXXX or 1XXXXXXXX -> 2547XXXXXXXX / 2541XXXXXXXX
    if len(digits) == 9 and digits[0] in {"7", "1"}:
        return "254" + digits

    # Already in 2547XXXXXXXX / 2541XXXXXXXX form (or 254... something else)
    if digits.startswith("254") and len(digits) >= 9:
        return digits

    # Fallback: return whatever digits we got; IntaSend will validate
    return digits


def get_intasend_config() -> dict:
    """
    Load IntaSend configuration from Streamlit secrets using FLAT keys.

    Expected structure in .streamlit/secrets.toml:

        INTASEND_PUBLISHABLE_KEY = "pk_live_..."
        INTASEND_API_KEY         = "sk_live_..."

    Optional overrides (not required; safe defaults are used if missing):

        INTASEND_BASE_URL    = "https://payment.intasend.com/api/v1"
        INTASEND_WEBHOOK_URL = "https://your-domain.com/intasend/webhook"

    Returns:
        dict with:
            - publishable_key (str)
            - api_key (str)
            - base_url (str)    -> defaults to IntaSend public API base
            - webhook_url (str | None)
    """
    # Required keys – will raise a KeyError if missing, which the caller handles.
    publishable_key = st.secrets["INTASEND_PUBLISHABLE_KEY"]
    api_key = st.secrets["INTASEND_API_KEY"]

    # Optional: base URL override
    raw_base_url = st.secrets.get("INTASEND_BASE_URL", "")
    if isinstance(raw_base_url, str):
        raw_base_url = raw_base_url.strip()
    else:
        raw_base_url = ""

    # IntaSend default base URL if not provided
    base_url = raw_base_url or "https://payment.intasend.com/api/v1"

    # Optional: webhook URL (not currently used by this module, but returned for completeness)
    raw_webhook = st.secrets.get("INTASEND_WEBHOOK_URL")
    if isinstance(raw_webhook, str):
        raw_webhook = raw_webhook.strip() or None
    else:
        raw_webhook = None

    return {
        "publishable_key": publishable_key,
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "webhook_url": raw_webhook,
    }


def trigger_mpesa_payment(phone_number: str, amount: int) -> Optional[str]:
    """
    Initiate an M-Pesa STK push via IntaSend.

    This function is self-contained: it loads its own configuration via
    get_intasend_config() and does NOT require API keys to be passed in.

    Args:
        phone_number: User's phone number; accepts 07/01, +2547/1, 7/1, etc.
        amount: Amount in KES (will be coerced to an integer string).

    Returns:
        invoice_id (str) if the request was accepted by IntaSend,
        or None if there was a configuration or network/API error.
    """
    try:
        cfg = get_intasend_config()
    except Exception as e:  # KeyError, etc.
        # Fail gracefully so the UI can show a friendly error
        print(f"[IntaSend] Missing or invalid configuration: {e}")
        return None

    msisdn = normalize_phone(phone_number)

    url = f'{cfg["base_url"]}/payment/mpesa/'

    payload = {
        "public_key": cfg["publishable_key"],
        "amount": str(int(amount)),  # ensure it's a stringified integer
        "phone_number": msisdn,
        "currency": "KES",
        # Optional: "email", "name", etc. if you collect them in the UI.
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

        # IntaSend typically returns an "invoice" or "invoice_id" field.
        invoice_id = data.get("invoice") or data.get("invoice_id")
        if invoice_id:
            return invoice_id

        print(f"[IntaSend] Unexpected payment response payload: {data}")
        return None

    except Exception as e:
        print(f"[IntaSend] STK push failed: {e}")
        return None


def check_payment_status(invoice_id: str) -> Optional[bool]:
    """
    Check IntaSend payment status for a given invoice.

    This function is also self-contained and loads configuration internally.

    Args:
        invoice_id: The invoice / reference ID returned by trigger_mpesa_payment().

    Returns:
        True   -> Payment successful ("PAID"/"COMPLETED"/"SUCCESS")
        False  -> Payment failed or cancelled ("FAILED"/"CANCELLED"/"DECLINED")
        None   -> Still pending, unknown status, or any error occurred.
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

        # IntaSend commonly uses "state" or "status": PAID, PENDING, FAILED, etc.
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

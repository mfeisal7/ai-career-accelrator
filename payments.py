"""
Backend IntaSend M-Pesa helper functions.

This module is UI-agnostic. It only exposes:
- normalize_phone()
- get_intasend_config()
- trigger_mpesa_payment()
- check_payment_status()

All Streamlit UI code must live in app.py.
"""

from typing import Optional
import os
import re

import requests


# ------------------------------------------------------------
# Phone Normalization
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

def get_intasend_config() -> dict:
    """
    Load IntaSend configuration from environment variables.

    Recommended env setup:

        INTASEND_PUBLISHABLE_KEY = "ISPubKey_live_..."
        INTASEND_API_KEY         = "ISSecretKey_live_..."
        INTASEND_BASE_URL        = "https://api.intasend.com/api/v1"      # optional
        INTASEND_WEBHOOK_URL     = "https://your-domain.com/intasend/webhook"  # optional

    Returns:
        dict with:
            - publishable_key (str)
            - api_key (str)        -> used as Bearer token
            - base_url (str)
            - webhook_url (str | None)

    Raises:
        KeyError if required variables are missing.
    """
    publishable_key = os.environ["INTASEND_PUBLISHABLE_KEY"]
    api_key = os.environ["INTASEND_API_KEY"]

    raw_base_url = os.environ.get("INTASEND_BASE_URL", "").strip()
    base_url = raw_base_url or "https://api.intasend.com/api/v1"

    raw_webhook = os.environ.get("INTASEND_WEBHOOK_URL", "") or None
    if isinstance(raw_webhook, str):
        raw_webhook = raw_webhook.strip() or None

    return {
        "publishable_key": publishable_key,
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "webhook_url": raw_webhook,
    }


# ------------------------------------------------------------
# STK Push
# ------------------------------------------------------------

def trigger_mpesa_payment(
    phone_number: str,
    amount: int,
    api_ref: str = "career-accelerator-premium-v1",
) -> Optional[str]:
    """
    Initiate an M-Pesa STK push via IntaSend.

    This function is self-contained: it loads its own configuration via
    get_intasend_config() and does NOT require API keys to be passed in.

    Args:
        phone_number: User's phone number; accepts 07/01, +2547/1, 7/1, etc.
        amount: Amount in KES (will be coerced to an integer string).
        api_ref: Optional reference string for your own tracking.

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

    # Correct IntaSend MPesa STK Push endpoint
    url = f'{cfg["base_url"]}/payment/mpesa-stk-push/'

    payload = {
        "amount": str(int(amount)),        # must be a string
        "phone_number": msisdn,
        "api_ref": api_ref,
        # Optional extras if you later collect them:
        # "wallet_id": "...",
        # "mobile_tarrif": "CUSTOMER_PAYS",
    }

    headers = {
        "Authorization": f'Bearer {cfg["api_key"]}',  # secret token
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        # For debugging in logs if something goes wrong:
        print(f"[IntaSend] STK push response {resp.status_code}: {resp.text}")

        resp.raise_for_status()
        data = resp.json()

        # IntaSend returns an invoice_id used for status checks
        invoice_id = data.get("invoice_id") or data.get("invoice")
        if invoice_id:
            return invoice_id

        print(f"[IntaSend] Unexpected payment response payload (no invoice_id): {data}")
        return None

    except Exception as e:
        print(f"[IntaSend] STK push failed: {e}")
        return None


# ------------------------------------------------------------
# Payment Status Polling
# ------------------------------------------------------------

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

    # Correct status endpoint: POST to /payment/status/ with invoice_id in JSON body
    url = f'{cfg["base_url"]}/payment/status/'

    headers = {
        "Authorization": f'Bearer {cfg["api_key"]}',
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {"invoice_id": invoice_id}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        print(f"[IntaSend] Status response {resp.status_code}: {resp.text}")

        resp.raise_for_status()
        data = resp.json()

        # IntaSend commonly uses "state" or "status": PAID, PENDING, FAILED, etc.
        status = (data.get("state") or data.get("status") or "").upper()

        if status in {"PAID", "COMPLETED", "SUCCESS"}:
            return True
        if status in {"FAILED", "CANCELLED", "DECLINED"}:
            return False

        # Any other state (including PENDING) -> still in progress / unknown
        return None

    except Exception as e:
        print(f"[IntaSend] Status check failed: {e}")
        return None

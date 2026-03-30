"""
intasend_client.py — IntaSend M-Pesa STK Push integration
AI Career Accelerator Kenya

Handles:
- Initiating STK push (M-Pesa prompt to user's phone)
- Polling payment status (no webhook server needed)
- Mapping IntaSend states to our payment DB

Environment variables required:
    INTASEND_PUBLISHABLE_KEY   — from IntaSend dashboard
    INTASEND_SECRET_KEY        — from IntaSend dashboard
    INTASEND_ENV               — "sandbox" (default) or "production"

IntaSend docs: https://developers.intasend.com/docs/mpesa-stk-push
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

_ENV = os.getenv("INTASEND_ENV", "sandbox").strip().lower()

_BASE_URLS = {
    "sandbox":    "https://sandbox.intasend.com/api/v1",
    "production": "https://payment.intasend.com/api/v1",
}

BASE_URL = _BASE_URLS.get(_ENV, _BASE_URLS["sandbox"])

# Payment states IntaSend can return
STATE_PENDING    = "PENDING"
STATE_PROCESSING = "PROCESSING"
STATE_COMPLETE   = "COMPLETE"
STATE_FAILED     = "FAILED"
STATE_CANCELLED  = "CANCELLED"

TERMINAL_STATES = {STATE_COMPLETE, STATE_FAILED, STATE_CANCELLED}


# ─────────────────────────────────────────────
# KEY HELPERS
# ─────────────────────────────────────────────

def _pub_key() -> str:
    key = os.getenv("INTASEND_PUBLISHABLE_KEY", "").strip()
    if not key:
        raise RuntimeError("INTASEND_PUBLISHABLE_KEY is not set. Get your key at https://intasend.com")
    return key


def _secret_key() -> str:
    key = os.getenv("INTASEND_SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError("INTASEND_SECRET_KEY is not set. Get your key at https://intasend.com")
    return key


def is_configured() -> bool:
    """Returns True if IntaSend keys are set and the system is ready."""
    return bool(os.getenv("INTASEND_PUBLISHABLE_KEY", "").strip())


# ─────────────────────────────────────────────
# PHONE NORMALIZATION
# ─────────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    """
    Converts any Kenyan phone format to 254XXXXXXXXX (IntaSend format).
    Examples:
        0722285538  → 254722285538
        +254722285538 → 254722285538
        254722285538  → 254722285538
        0722 285 538  → 254722285538
    """
    import re
    p = re.sub(r"[^\d+]", "", phone.strip())
    if p.startswith("+"):
        p = p[1:]
    if p.startswith("0") and len(p) == 10:
        p = "254" + p[1:]
    if len(p) == 9 and (p.startswith("7") or p.startswith("1")):
        p = "254" + p
    return p


# ─────────────────────────────────────────────
# STK PUSH — INITIATE
# ─────────────────────────────────────────────

def initiate_stk_push(
    phone: str,
    amount: int,
    tier: str,
    user_id: str,
    narrative: str = "AI Career Accelerator",
) -> dict:
    """
    Sends an M-Pesa STK push to the user's phone.

    Returns a dict with:
        success (bool)
        invoice_id (str)    — store this, use it to poll status
        message (str)       — human-readable status
        raw (dict)          — full IntaSend response

    Usage:
        result = initiate_stk_push("0722285538", 3999, "pro", "user_abc123")
        if result["success"]:
            invoice_id = result["invoice_id"]
            # → poll_payment_status(invoice_id) until COMPLETE
    """
    phone_fmt = _normalize_phone(phone)
    tier_label = tier.capitalize()
    api_ref = f"{user_id}_{tier}"

    payload = {
        "public_key": _pub_key(),
        "amount": amount,
        "phone_number": phone_fmt,
        "api_ref": api_ref,
        "narrative": f"{narrative} — {tier_label} Plan",
    }

    headers = {
        "Content-Type": "application/json",
        "X-IntaSend-Public-API-Key": _pub_key(),
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/payment/mpesa-stk-push/",
            json=payload,
            headers=headers,
            timeout=30,
        )
        data = resp.json()

        if resp.status_code in (200, 201):
            invoice = data.get("invoice", {})
            invoice_id = invoice.get("invoice_id") or data.get("id", "")
            state = invoice.get("state", STATE_PENDING)

            logger.info(f"STK push initiated: user={user_id} tier={tier} phone={phone_fmt} invoice={invoice_id}")
            return {
                "success": True,
                "invoice_id": invoice_id,
                "state": state,
                "message": "M-Pesa payment prompt sent to your phone. Enter your PIN to complete.",
                "raw": data,
            }
        else:
            err_msg = data.get("detail") or data.get("message") or str(data)
            logger.error(f"STK push failed: {resp.status_code} — {err_msg}")
            return {
                "success": False,
                "invoice_id": "",
                "state": STATE_FAILED,
                "message": f"Payment initiation failed: {err_msg}",
                "raw": data,
            }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "invoice_id": "",
            "state": STATE_FAILED,
            "message": "Request timed out. Please try again.",
            "raw": {},
        }
    except Exception as e:
        logger.error(f"STK push exception: {e}")
        return {
            "success": False,
            "invoice_id": "",
            "state": STATE_FAILED,
            "message": f"Unexpected error: {str(e)}",
            "raw": {},
        }


# ─────────────────────────────────────────────
# STK PUSH — POLL STATUS
# ─────────────────────────────────────────────

def poll_payment_status(invoice_id: str) -> dict:
    """
    Check the current status of a previously initiated STK push.

    Returns:
        state (str)    — PENDING | PROCESSING | COMPLETE | FAILED | CANCELLED
        paid (bool)    — True only when state == COMPLETE
        message (str)
        raw (dict)

    Call this every 3-5 seconds after initiating STK push.
    Stop polling when paid=True or state is in TERMINAL_STATES.
    """
    if not invoice_id:
        return {"state": STATE_FAILED, "paid": False, "message": "No invoice ID provided.", "raw": {}}

    headers = {
        "Authorization": f"Bearer {_secret_key()}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.get(
            f"{BASE_URL}/payment/mpesa-stk-push/{invoice_id}/",
            headers=headers,
            timeout=15,
        )
        data = resp.json()

        if resp.status_code == 200:
            invoice = data.get("invoice", data)
            state = invoice.get("state", STATE_PENDING)
            paid = (state == STATE_COMPLETE)

            msg_map = {
                STATE_PENDING:    "Waiting for payment… Check your phone for the M-Pesa prompt.",
                STATE_PROCESSING: "Payment received, processing…",
                STATE_COMPLETE:   "✅ Payment confirmed! Your access is now unlocked.",
                STATE_FAILED:     "❌ Payment failed. Please try again.",
                STATE_CANCELLED:  "Payment was cancelled. Try again if this was a mistake.",
            }

            return {
                "state": state,
                "paid": paid,
                "message": msg_map.get(state, f"Status: {state}"),
                "raw": data,
            }
        else:
            return {
                "state": STATE_PENDING,
                "paid": False,
                "message": "Checking payment status…",
                "raw": data,
            }

    except Exception as e:
        logger.error(f"poll_payment_status error: {e}")
        return {
            "state": STATE_PENDING,
            "paid": False,
            "message": "Checking payment status…",
            "raw": {},
        }


# ─────────────────────────────────────────────
# BLOCKING POLL (for background use)
# ─────────────────────────────────────────────

def wait_for_payment(
    invoice_id: str,
    timeout_seconds: int = 120,
    poll_interval: int = 4,
) -> dict:
    """
    Blocking poll loop. Checks every `poll_interval` seconds until
    payment is complete, failed, or `timeout_seconds` is reached.

    Use this in a background thread, not directly in Streamlit.
    For Streamlit UI, use `poll_payment_status()` with st_autorefresh.
    """
    start = time.time()
    while time.time() - start < timeout_seconds:
        result = poll_payment_status(invoice_id)
        if result["state"] in TERMINAL_STATES:
            return result
        time.sleep(poll_interval)

    return {
        "state": STATE_FAILED,
        "paid": False,
        "message": "Payment confirmation timed out. If you paid, contact support on WhatsApp.",
        "raw": {},
    }


# ─────────────────────────────────────────────
# SANDBOX TEST HELPER
# ─────────────────────────────────────────────

def test_connection() -> bool:
    """
    Quick ping to verify IntaSend credentials work.
    Returns True if auth succeeds, False otherwise.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/payment/collection/",
            headers={"Authorization": f"Bearer {_secret_key()}"},
            timeout=10,
        )
        ok = resp.status_code in (200, 201, 404)  # 404 = auth OK but empty
        logger.info(f"IntaSend connection test: {'OK' if ok else 'FAILED'} ({resp.status_code})")
        return ok
    except Exception as e:
        logger.error(f"IntaSend connection test failed: {e}")
        return False

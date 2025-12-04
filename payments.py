"""
IntaSend M-Pesa integration with proper logging, retries, and validation.
"""

import os
import re
import logging
from typing import Optional

import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

logger = logging.getLogger(__name__)
if not logger.handlers:
    # Basic config only if not already configured by the host app
    logging.basicConfig(level=logging.INFO)


# ------------------------------------------------------------
# Requests session with retries
# ------------------------------------------------------------

session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("http://", adapter)
session.mount("https://", adapter)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def normalize_phone(phone_number: str) -> str:
    """
    Normalize to 2547XXXXXXXX format.

    Accepts:
    - 07xxxxxxxx
    - 01xxxxxxxx
    - 7xxxxxxxx
    - 1xxxxxxxx
    - 2547xxxxxxxx
    - 2541xxxxxxxx
    - +2547xxxxxxxx
    - +2541xxxxxxxx
    """
    if not phone_number:
        return ""

    digits = re.sub(r"\D", "", phone_number)

    # 07xxxxxxxx or 01xxxxxxxx → 2547/1xxxxxxxx
    if len(digits) == 10 and digits.startswith(("07", "01")):
        return "254" + digits[1:]

    # 7xxxxxxxx or 1xxxxxxxx → 2547/1xxxxxxxx
    if len(digits) == 9 and digits[0] in {"7", "1"}:
        return "254" + digits

    # Already in 2547xxxxxxxx or 2541xxxxxxxx format
    if digits.startswith("254") and len(digits) == 12:
        return digits

    logger.warning(f"Invalid phone number format: {phone_number} -> {digits}")
    return ""


def get_intasend_config() -> dict:
    """
    Read IntaSend config from environment variables.

    Raises RuntimeError if required keys are missing.
    """
    publishable_key = os.getenv("INTASEND_PUBLISHABLE_KEY")
    api_key = os.getenv("INTASEND_API_KEY")
    base_url = os.getenv("INTASEND_BASE_URL", "https://api.intasend.com/api/v1").rstrip("/")

    if not publishable_key or not api_key:
        raise RuntimeError("IntaSend keys not configured in environment")

    return {
        "publishable_key": publishable_key,
        "api_key": api_key,
        "base_url": base_url,
    }


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def trigger_mpesa_payment(
    phone_number: str,
    amount: int,
    api_ref: str = "career-accelerator-premium",
    *,
    reference: Optional[str] = None,
) -> Optional[str]:
    """
    Initiate an M-Pesa STK push via IntaSend.

    - `phone_number`: user input (07..., +2547..., etc.), normalized internally.
    - `amount`: integer amount in KES.
    - `api_ref` / `reference`: invoice / tracking reference.

    NOTE: `reference` is accepted so calls like trigger_mpesa_payment(..., reference="...")
    from app.py work without error. If both are provided, `reference` wins.
    """
    if amount <= 0:
        logger.error("Amount must be positive")
        return None

    msisdn = normalize_phone(phone_number)
    if len(msisdn) != 12:
        logger.error(f"Invalid phone after normalization: {msisdn}")
        return None

    # Allow app.py to pass `reference=` or fall back to `api_ref`
    ref = reference or api_ref or "career-accelerator-premium"

    try:
        cfg = get_intasend_config()
    except RuntimeError as e:
        # Don't crash the app if IntaSend isn't configured; just log and return None
        logger.error(f"IntaSend configuration error: {e}")
        return None

    url = f"{cfg['base_url']}/payment/mpesa-stk-push/"

    payload = {
        "amount": str(int(amount)),
        "phone_number": msisdn,
        "api_ref": ref,
    }

    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    try:
        resp = session.post(url, json=payload, headers=headers, timeout=20)
        logger.info(f"STK Push Response: {resp.status_code} {resp.text}")
        resp.raise_for_status()
        data = resp.json()
        # IntaSend may return invoice_id or invoice
        invoice_id = data.get("invoice_id") or data.get("invoice")
        if invoice_id:
            return str(invoice_id)

        logger.error(f"No invoice_id in response: {data}")
    except Exception as e:
        logger.error(f"STK push failed: {e}")

    return None


def check_payment_status(invoice_id: str) -> Optional[bool]:
    """
    Check the payment status for a given invoice_id.

    Returns:
    - True  → payment completed
    - False → payment failed or cancelled
    - None  → still pending or unknown / error
    """
    if not invoice_id:
        return None

    try:
        cfg = get_intasend_config()
    except RuntimeError as e:
        logger.error(f"IntaSend configuration error on status check: {e}")
        return None

    url = f"{cfg['base_url']}/payment/status/"

    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    payload = {"invoice_id": invoice_id}

    try:
        resp = session.post(url, json=payload, headers=headers, timeout=15)
        logger.info(f"Status check: {resp.status_code} {resp.text}")
        resp.raise_for_status()

        data = resp.json()
        status = (data.get("state") or data.get("status") or "").upper()

        if status in {"PAID", "COMPLETED", "SUCCESS"}:
            return True
        if status in {"FAILED", "CANCELLED", "DECLINED"}:
            return False

        # PENDING / PROCESSING / UNKNOWN
        return None

    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return None

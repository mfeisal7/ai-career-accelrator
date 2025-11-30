# payments.py
"""
Backend M-Pesa STK helper functions.

This module is UI-agnostic. It only exposes:
- get_mpesa_config()
- get_access_token()
- trigger_mpesa_stk()
- check_payment_status()

All Streamlit UI code must live in app.py.
"""

import base64
import json
from datetime import datetime
from typing import Optional

import requests
import streamlit as st


def get_mpesa_config() -> dict:
    """
    Load M-Pesa configuration from Streamlit secrets using FLAT keys.

    Expected structure in .streamlit/secrets.toml:

    GEMINI_API_KEY       = "..."
    MPESA_CONSUMER_KEY   = "..."
    MPESA_CONSUMER_SECRET= "..."
    MPESA_SHORTCODE      = "174379"
    MPESA_PASSKEY        = "..."
    MPESA_CALLBACK_URL   = "https://your-domain.com/mpesa/callback"  # optional but recommended
    MPESA_ENVIRONMENT    = "sandbox"  # or "production" (optional, defaults to sandbox)
    """
    # Required flat keys
    consumer_key = st.secrets["MPESA_CONSUMER_KEY"]
    consumer_secret = st.secrets["MPESA_CONSUMER_SECRET"]
    shortcode = st.secrets["MPESA_SHORTCODE"]
    passkey = st.secrets["MPESA_PASSKEY"]

    # Optional flat keys with sensible defaults
    callback_url = st.secrets.get(
        "MPESA_CALLBACK_URL",
        "https://example.com/mpesa/callback",
    )
    environment = st.secrets.get("MPESA_ENVIRONMENT", "sandbox").lower()

    if environment == "production":
        base_url = "https://api.safaricom.co.ke"
    else:
        base_url = "https://sandbox.safaricom.co.ke"

    return {
        "consumer_key": consumer_key,
        "consumer_secret": consumer_secret,
        "shortcode": shortcode,
        "passkey": passkey,
        "callback_url": callback_url,
        "base_url": base_url,
    }


def get_access_token() -> Optional[str]:
    """
    Get OAuth access token from M-Pesa API.

    Returns:
        access_token (str) or None on failure.
    """
    cfg = get_mpesa_config()
    url = f'{cfg["base_url"]}/oauth/v1/generate?grant_type=client_credentials'

    try:
        response = requests.get(
            url,
            auth=(cfg["consumer_key"], cfg["consumer_secret"]),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("access_token")
    except Exception as e:
        print(f"[M-Pesa] Failed to get access token: {e}")
        return None


def trigger_mpesa_stk(phone_number: str, amount: int) -> Optional[str]:
    """
    Initiate an M-Pesa STK push.

    Args:
        phone_number: User's phone number (e.g. 0712..., +2547..., or 2547...).
        amount: Amount in KES.

    Returns:
        checkout_id (str) if the request was accepted by M-Pesa,
        or None if there was an error.
    """
    cfg = get_mpesa_config()
    token = get_access_token()
    if not token:
        return None

    # Normalize Kenyan phone numbers to 2547XXXXXXXX
    msisdn = phone_number.strip().replace(" ", "")
    if msisdn.startswith("+"):
        msisdn = msisdn[1:]
    if msisdn.startswith("0") and len(msisdn) == 10:
        msisdn = "254" + msisdn[1:]
    elif not msisdn.startswith("254"):
        # Fallback: assume it's a local number missing the '254'
        if msisdn.startswith("7") and len(msisdn) == 9:
            msisdn = "254" + msisdn
        # otherwise leave as-is

    url = f'{cfg["base_url"]}/mpesa/stkpush/v1/processrequest'

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    raw_password = f'{cfg["shortcode"]}{cfg["passkey"]}{timestamp}'
    password = base64.b64encode(raw_password.encode("utf-8")).decode("utf-8")

    payload = {
        "BusinessShortCode": cfg["shortcode"],
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": msisdn,
        "PartyB": cfg["shortcode"],
        "PhoneNumber": msisdn,
        "CallBackURL": cfg["callback_url"],
        "AccountReference": "AI Career Accelerator",
        "TransactionDesc": "Premium Resume Unlock",
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # On success, Safaricom returns a CheckoutRequestID
        checkout_id = data.get("CheckoutRequestID")
        if checkout_id:
            return checkout_id

        print(f"[M-Pesa] Unexpected STK response: {json.dumps(data)}")
        return None
    except Exception as e:
        print(f"[M-Pesa] STK push failed: {e}")
        return None


def check_payment_status(checkout_id: str) -> Optional[bool]:
    """
    Check the status of a previous STK push.

    Returns:
        True  -> Payment successful
        False -> Payment failed / cancelled
        None  -> Still pending or unknown
    """
    cfg = get_mpesa_config()
    token = get_access_token()
    if not token:
        return None

    url = f'{cfg["base_url"]}/mpesa/stkpushquery/v1/query'

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    raw_password = f'{cfg["shortcode"]}{cfg["passkey"]}{timestamp}'
    password = base64.b64encode(raw_password.encode("utf-8")).decode("utf-8")

    payload = {
        "BusinessShortCode": cfg["shortcode"],
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_id,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # Typical fields:
        # ResultCode == 0  -> success
        # ResultCode != 0  -> failure/cancelled
        result_code = data.get("ResultCode")

        if result_code is None:
            # Some flows still use ResponseCode; treat non-0 as failure
            response_code = data.get("ResponseCode")
            if response_code == "0":
                return None  # still processing
            elif response_code is not None:
                return False
            return None

        if str(result_code) == "0":
            return True
        elif str(result_code) in {"1032", "1", "2001"}:
            return False
        else:
            return False

    except Exception as e:
        print(f"[M-Pesa] Status check failed: {e}")
        return None

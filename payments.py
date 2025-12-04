"""
Secure FastAPI webhook server for IntaSend with HMAC signature verification.
Deploy this separately in production (e.g. Railway, Fly.io, Render).
"""

import os
import hmac
import hashlib
import logging
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse

from payments_db import mark_invoice_paid

app = FastAPI(title="AI Career Accelerator – IntaSend Webhook")

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


def verify_intasend_signature(payload: bytes, signature_header: str | None) -> bool:
    """
    Verify IntaSend webhook signature using your secret key.

    - If INTASEND_API_KEY is NOT set, we log a warning and skip verification
      (fail-open) to make local dev easier.
    - In production, you MUST set INTASEND_API_KEY so signatures are enforced.
    """
    secret = os.getenv("INTASEND_API_KEY")
    if not secret:
        logger.warning(
            "[Webhook] INTASEND_API_KEY not set — skipping signature verification "
            "(development mode)."
        )
        return True  # FAIL-OPEN in dev only

    if not signature_header:
        logger.warning("[Webhook] Missing X-IntaSend-Signature header")
        return False

    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), signature_header.lower())


@app.post("/intasend/webhook")
async def intasend_webhook(request: Request):
    """
    IntaSend webhook endpoint.

    Expects JSON payload with at least:
    - invoice_id / invoice
    - state / status
    """
    signature = request.headers.get("X-IntaSend-Signature", "")

    try:
        raw_payload = await request.body()
        if not verify_intasend_signature(raw_payload, signature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid signature",
            )

        # Parse JSON after signature verification
        payload = await request.json()
    except HTTPException:
        # Re-raise HTTPException as-is
        raise
    except Exception as e:
        logger.error(f"[Webhook] Invalid payload: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    invoice_id = payload.get("invoice_id") or payload.get("invoice")
    state = (payload.get("state") or payload.get("status") or "").upper()

    if not invoice_id:
        raise HTTPException(status_code=400, detail="Missing invoice_id")

    logger.info(f"[Webhook] Received: invoice_id={invoice_id}, state={state}")

    if state in {"PAID", "COMPLETED", "SUCCESS"}:
        updated = mark_invoice_paid(invoice_id)
        return JSONResponse(
            {
                "ok": True,
                "updated": updated,
                "action": "payment_marked_paid" if updated else "already_paid",
            }
        )

    # For non-paid states we just acknowledge so IntaSend stops retrying
    return JSONResponse(
        {"ok": True, "ignored": True, "reason": f"state={state!r} not paid"}
    )


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "intasend-webhook"}


def run(host: str = "0.0.0.0", port: int = 8000):
    """
    Start the webhook server with uvicorn.

    - Used by app.py in a background thread.
    - We import uvicorn lazily so that the rest of the app can run even if
      uvicorn is not installed (e.g. some dev environments).
    """
    try:
        import uvicorn  # type: ignore
    except ImportError:
        logger.error(
            "[Webhook] uvicorn is not installed. "
            "Install it with `pip install uvicorn` to run the webhook server."
        )
        return

    logger.info(f"[Webhook] Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()

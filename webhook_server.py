# webhook_server.py
"""
FastAPI server to receive IntaSend webhooks and mark payments as paid.

app.py starts this in a background thread:

    from webhook_server import run as run_webhook_server
    ...
    threading.Thread(target=run_webhook_server, ...)

Expose a POST /intasend/webhook endpoint and configure that URL in your
IntaSend dashboard as the webhook target.
"""

from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from payments_db import mark_invoice_paid

app = FastAPI(title="IntaSend Webhook Server")


@app.post("/intasend/webhook")
async def intasend_webhook(request: Request):
    """
    Handle IntaSend webhook callbacks.

    Expected payload (fields may vary slightly depending on IntaSend version):

        {
          "invoice_id": "INV_12345",
          "invoice": "INV_12345",         # sometimes used instead
          "state": "PAID",                # or "status": "COMPLETED", etc.
          ...
        }

    We only care about:
        - invoice_id / invoice
        - state / status
    """
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)

    invoice_id = payload.get("invoice_id") or payload.get("invoice")
    status_raw = payload.get("state") or payload.get("status") or ""
    status = str(status_raw).upper()

    if not invoice_id:
        return JSONResponse({"detail": "Missing invoice_id"}, status_code=400)

    # Treat PAID/COMPLETED/SUCCESS as success
    if status in {"PAID", "COMPLETED", "SUCCESS"}:
        mark_invoice_paid(invoice_id)
        return {"ok": True, "updated": True}

    # For other states we just acknowledge the webhook
    return {"ok": True, "updated": False, "status": status}


def run(host: str = "0.0.0.0", port: int = 8000) -> None:
    """
    Entrypoint used by app.py's background thread.

    Example (already in your app.py):

        thread = threading.Thread(
            target=run_webhook_server,
            kwargs={"host": "0.0.0.0", "port": 8000},
            daemon=True,
        )
        thread.start()
    """
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()

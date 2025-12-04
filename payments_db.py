# payments_db.py
"""
SQLite-backed payment storage for the AI Career Accelerator.

Schema:
    payments(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    TEXT NOT NULL,
        phone      TEXT NOT NULL,
        invoice_id TEXT NOT NULL UNIQUE,
        amount     REAL NOT NULL,
        paid_at    TEXT,                 -- ISO timestamp when marked paid
        is_paid    INTEGER NOT NULL      -- 0 = not paid, 1 = paid
    )

Public API:
    - init_db()
    - create_payment(user_id, phone, invoice_id, amount)
    - mark_invoice_paid(invoice_id)
    - is_user_paid(user_id) -> bool
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# DB file lives alongside this module
DB_PATH = Path(__file__).with_name("payments.db")


@contextmanager
def get_connection():
    # Isolation level None enables autocommit; we’ll manage commits explicitly.
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create the payments table if it does not exist."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                phone      TEXT NOT NULL,
                invoice_id TEXT NOT NULL UNIQUE,
                amount     REAL NOT NULL,
                paid_at    TEXT,
                is_paid    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()


def create_payment(
    user_id: str,
    phone: str,
    invoice_id: str,
    amount: float,
) -> None:
    """
    Insert a new payment intent row.

    If the invoice_id already exists, this is a no-op to avoid blowing up
    on retries.
    """
    if not invoice_id:
        return

    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO payments (user_id, phone, invoice_id, amount, is_paid)
            VALUES (?, ?, ?, ?, 0)
            """,
            (user_id, phone, invoice_id, float(amount)),
        )
        conn.commit()


def mark_invoice_paid(invoice_id: str) -> None:
    """Mark a given invoice as paid and set paid_at timestamp."""
    if not invoice_id:
        return

    now_iso = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE payments
               SET is_paid = 1,
                   paid_at = ?
             WHERE invoice_id = ?
            """,
            (now_iso, invoice_id),
        )
        conn.commit()


def is_user_paid(user_id: str) -> bool:
    """
    Return True if this user has at least one successful payment.

    This is what app.py uses to decide whether to show premium downloads.
    """
    if not user_id:
        return False

    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT 1
              FROM payments
             WHERE user_id = ?
               AND is_paid = 1
             LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
    return row is not None

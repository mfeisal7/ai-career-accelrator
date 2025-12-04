"""
Secure, thread-safe SQLite payment database for AI Career Accelerator.
Used by both Streamlit (app.py) and FastAPI webhook (webhook_server.py).
"""

import os
import sqlite3
import threading
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# ----------------------------------------------------------------------
# Database location — works locally and on Streamlit Cloud / Railway / Render
# ----------------------------------------------------------------------

DB_PATH = Path(os.getenv("PAYMENTS_DB_PATH", Path(__file__).with_name("payments.db")))

# Thread lock to prevent SQLite "database is locked" errors under concurrent access
_db_lock = threading.Lock()


@contextmanager
def get_connection():
    """
    Thread-safe connection context manager.

    - Uses WAL mode for better concurrency (critical when webhook + Streamlit hit DB).
    - check_same_thread=False so we can share the DB across threads (webhook + app).
    - isolation_level=None → autocommit mode (each statement is its own transaction).
    """
    with _db_lock:
        conn = sqlite3.connect(
            str(DB_PATH),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,  # autocommit; commit/rollback are mostly no-ops
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            # In autocommit mode, this is mostly harmless, but kept for clarity
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    """Create table + performance indexes if they don't exist."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                phone       TEXT NOT NULL,
                invoice_id  TEXT NOT NULL UNIQUE,
                amount      REAL NOT NULL CHECK(amount > 0),
                paid_at     TEXT,
                is_paid     INTEGER NOT NULL DEFAULT 0 CHECK(is_paid IN (0, 1)),
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            )
            """
        )

        # Critical indexes for fast lookups
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON payments(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_id ON payments(invoice_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_paid "
            "ON payments(user_id, is_paid) WHERE is_paid = 1"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON payments(created_at)")

        logger.info(f"[payments_db] Initialized DB at {DB_PATH}")


def create_payment(user_id: str, phone: str, invoice_id: str, amount: float) -> bool:
    """
    Insert payment record (idempotent).
    Returns True if a new row was inserted, False otherwise.
    """
    if not all([user_id, phone, invoice_id, amount]):
        return False

    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO payments (user_id, phone, invoice_id, amount, is_paid)
                VALUES (?, ?, ?, ?, 0)
                """,
                (user_id, phone, invoice_id, float(amount)),
            )
            inserted = cursor.rowcount > 0
            if inserted:
                logger.info(
                    f"[payments_db] Created payment: user_id={user_id}, "
                    f"invoice_id={invoice_id}, amount={amount}"
                )
            return inserted
    except sqlite3.IntegrityError:
        # Duplicate invoice_id — expected and safe (INSERT OR IGNORE handles it)
        logger.warning(
            f"[payments_db] Duplicate invoice_id (ignored): invoice_id={invoice_id}"
        )
        return False
    except Exception as e:
        logger.error(f"[payments_db] create_payment error: {e}")
        return False


def mark_invoice_paid(invoice_id: str) -> bool:
    """
    Atomically mark invoice as paid.
    Returns True only if a row was actually updated (prevents double-marking).
    """
    if not invoice_id:
        return False

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE payments
                SET is_paid = 1,
                    paid_at = ?,
                    updated_at = datetime('now')
                WHERE invoice_id = ? AND is_paid = 0
                """,
                (now, invoice_id),
            )
            updated = cursor.rowcount > 0
            if updated:
                logger.info(
                    f"[payments_db] Payment confirmed: invoice_id={invoice_id}, "
                    f"paid_at={now}"
                )
            else:
                logger.info(
                    f"[payments_db] mark_invoice_paid: no rows updated "
                    f"(already paid or unknown invoice_id={invoice_id})"
                )
            return updated
    except Exception as e:
        logger.error(f"[payments_db] mark_invoice_paid error: {e}")
        return False


def is_user_paid(user_id: str) -> bool:
    """
    Fast check: does this user have at least one confirmed payment?
    """
    if not user_id:
        return False

    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM payments
                WHERE user_id = ? AND is_paid = 1
                LIMIT 1
                """,
                (user_id,),
            )
            paid = cursor.fetchone() is not None
            return paid
    except Exception as e:
        logger.error(f"[payments_db] is_user_paid error: {e}")
        return False


def get_user_payments(user_id: str):
    """
    Debug/admin function — list all payments for a user.
    Returns a list of dicts.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        return rows


# Auto-init on import so both app.py and webhook_server.py
# can safely assume the table exists.
init_db()

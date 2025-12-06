"""
Secure, thread-safe SQLite payment database for AI Career Accelerator.
Used by Streamlit app to store who has paid (manually marked via admin panel).
"""

import os
import sqlite3
import threading
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# Resolve database path (can be overridden via PAYMENTS_DB_PATH)
DB_PATH = Path(os.getenv("PAYMENTS_DB_PATH", Path(__file__).with_name("payments.db")))

# Ensure parent directory exists (important for paths like /data/payments.db on Railway)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_db_lock = threading.Lock()


@contextmanager
def get_connection():
    """
    Thread-safe connection context manager with WAL mode and sane timeouts.
    """
    with _db_lock:
        conn = sqlite3.connect(
            str(DB_PATH),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """
    Add any missing columns to the payments table for backwards compatibility.
    """
    cursor = conn.execute("PRAGMA table_info(payments)")
    cols = {row["name"] for row in cursor.fetchall()}

    if "created_at" not in cols:
        logger.info("[payments_db] Adding missing 'created_at' column")
        conn.execute(
            "ALTER TABLE payments ADD COLUMN created_at TEXT DEFAULT (datetime('now'))"
        )

    if "updated_at" not in cols:
        logger.info("[payments_db] Adding missing 'updated_at' column")
        conn.execute(
            "ALTER TABLE payments ADD COLUMN updated_at TEXT DEFAULT (datetime('now'))"
        )

    if "paid_at" not in cols:
        logger.info("[payments_db] Adding missing 'paid_at' column")
        conn.execute("ALTER TABLE payments ADD COLUMN paid_at TEXT")

    if "is_paid" not in cols:
        logger.info("[payments_db] Adding missing 'is_paid' column")
        conn.execute(
            "ALTER TABLE payments ADD COLUMN is_paid INTEGER NOT NULL DEFAULT 0"
        )


def init_db() -> None:
    """
    Create the payments table and indexes if they don't exist yet,
    and run lightweight migrations to ensure schema compatibility.
    """
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

        _ensure_columns(conn)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON payments(user_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoice_id ON payments(invoice_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_paid "
            "ON payments(user_id, is_paid) WHERE is_paid = 1"
        )
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_created_at ON payments(created_at)"
            )
        except sqlite3.OperationalError as e:
            logger.warning(f"[payments_db] Could not create idx_created_at index: {e}")

        logger.info(f"[payments_db] Initialized/migrated DB at {DB_PATH}")


def create_payment(user_id: str, phone: str, invoice_id: str, amount: float) -> bool:
    """
    Create a pending payment record for a user.

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
        logger.warning(
            f"[payments_db] Duplicate invoice_id (ignored): invoice_id={invoice_id}"
        )
        return False
    except Exception as e:
        logger.error(f"[payments_db] create_payment error: {e}")
        return False


def mark_invoice_paid(invoice_id: str) -> bool:
    """
    Mark a specific invoice as paid (typically from webhooks).

    Returns True if at least one row was updated.
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
                    "[payments_db] mark_invoice_paid: no rows updated "
                    f"(already paid or unknown invoice_id={invoice_id})"
                )
            return updated
    except Exception as e:
        logger.error(f"[payments_db] mark_invoice_paid error: {e}")
        return False


def is_user_paid(user_id: str) -> bool:
    """
    Return True if the given user has at least one paid payment row.
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
            return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"[payments_db] is_user_paid error: {e}")
        return False


def mark_user_paid(user_id: str) -> bool:
    """
    Manually mark a user as paid (for WhatsApp/manual payments).

    If the user already has payment rows, we mark them paid.
    If they don't, we insert a synthetic row so is_user_paid() becomes True.
    """
    if not user_id:
        return False

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    try:
        with get_connection() as conn:
            # Try to update existing unpaid rows first
            cursor = conn.execute(
                """
                UPDATE payments
                SET is_paid = 1,
                    paid_at = ?,
                    updated_at = datetime('now')
                WHERE user_id = ? AND is_paid = 0
                """,
                (now, user_id),
            )
            updated = cursor.rowcount > 0

            if not updated:
                # No existing rows → create a manual "whatsapp" payment
                invoice_id = (
                    f"manual-whatsapp-{user_id}-{int(datetime.utcnow().timestamp())}"
                )
                amount = 1000.0  # current price
                conn.execute(
                    """
                    INSERT OR IGNORE INTO payments (
                        user_id, phone, invoice_id, amount, is_paid, paid_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 1, ?, datetime('now'), datetime('now'))
                    """,
                    (user_id, "WHATSAPP", invoice_id, amount, now),
                )
                logger.info(
                    "[payments_db] Manually marked user as paid via WhatsApp: "
                    f"user_id={user_id}, invoice_id={invoice_id}"
                )
            else:
                logger.info(
                    f"[payments_db] Updated existing rows as paid for user_id={user_id}"
                )

            return True
    except Exception as e:
        logger.error(f"[payments_db] mark_user_paid error: {e}")
        return False


def get_user_payments(user_id: str):
    """
    Return a list of all payment records for a user, newest first.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


# Auto-init on import
init_db()

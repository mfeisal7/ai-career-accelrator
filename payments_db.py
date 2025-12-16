# payments_db.py
import os
import json
import sqlite3
import threading
import logging
import hashlib
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

DB_PATH = Path(os.getenv("PAYMENTS_DB_PATH", Path(__file__).with_name("payments.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_db_lock = threading.Lock()


@contextmanager
def get_connection():
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


def init_db() -> None:
    with get_connection() as conn:
        # USERS (for phone+email login)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id     TEXT PRIMARY KEY,
                phone       TEXT NOT NULL,
                email       TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")

        # PAYMENTS
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON payments(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_id ON payments(invoice_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_paid "
            "ON payments(user_id, is_paid) WHERE is_paid = 1"
        )

        # OUTPUTS (persist generated content)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_outputs (
                user_id         TEXT PRIMARY KEY,
                ai_resume_md     TEXT,
                ai_cover_letter  TEXT,
                ai_emails_json   TEXT,
                created_at       TEXT DEFAULT (datetime('now')),
                updated_at       TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_outputs_updated_at ON user_outputs(updated_at)"
        )


# =========================
# Login helpers
# =========================

def normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    p = phone.strip().replace(" ", "").replace("-", "")
    p = re.sub(r"[^\d+]", "", p)
    if p.startswith("+"):
        p = p[1:]
    if p.startswith("0") and len(p) >= 10:
        p = "254" + p[1:]
    if (p.startswith("7") or p.startswith("1")) and len(p) == 9:
        p = "254" + p
    return p


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def make_user_id(phone: str, email: str) -> str:
    key = f"{normalize_phone(phone)}|{normalize_email(email)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def get_or_create_user(phone: str, email: str) -> Optional[Dict[str, str]]:
    phone_n = normalize_phone(phone)
    email_n = normalize_email(email)
    if not phone_n or not email_n:
        return None

    user_id = make_user_id(phone_n, email_n)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT user_id, phone, email FROM users WHERE user_id = ? LIMIT 1",
            (user_id,),
        ).fetchone()
        if row:
            return {"user_id": row["user_id"], "phone": row["phone"], "email": row["email"]}

        conn.execute(
            """
            INSERT INTO users (user_id, phone, email, created_at, updated_at)
            VALUES (?, ?, ?, datetime('now'), datetime('now'))
            """,
            (user_id, phone_n, email_n),
        )
        return {"user_id": user_id, "phone": phone_n, "email": email_n}


# =========================
# Payments
# =========================

def is_user_paid(user_id: str) -> bool:
    if not user_id:
        return False
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM payments WHERE user_id = ? AND is_paid = 1 LIMIT 1",
            (user_id,),
        ).fetchone()
        return row is not None


def mark_user_paid(user_id: str) -> bool:
    if not user_id:
        return False
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE payments
                SET is_paid = 1, paid_at = ?, updated_at = datetime('now')
                WHERE user_id = ? AND is_paid = 0
                """,
                (now, user_id),
            )
            if cur.rowcount == 0:
                invoice_id = f"manual-whatsapp-{user_id}-{int(datetime.utcnow().timestamp())}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO payments
                    (user_id, phone, invoice_id, amount, is_paid, paid_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, datetime('now'), datetime('now'))
                    """,
                    (user_id, "WHATSAPP", invoice_id, 1000.0, now),
                )
        return True
    except Exception as e:
        logger.error(f"mark_user_paid error: {e}")
        return False


def get_user_payments(user_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        if user_id:
            cur = conn.execute("SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
        else:
            cur = conn.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (int(limit),))
        return [dict(r) for r in cur.fetchall()]


# =========================
# Outputs persistence
# =========================

def save_user_output(user_id: str, resume: str, cover_letter: str, emails) -> bool:
    if not user_id:
        return False
    try:
        emails_json = json.dumps(emails or [], ensure_ascii=False)
    except Exception:
        emails_json = json.dumps([], ensure_ascii=False)

    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_outputs (user_id, ai_resume_md, ai_cover_letter, ai_emails_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    ai_resume_md = excluded.ai_resume_md,
                    ai_cover_letter = excluded.ai_cover_letter,
                    ai_emails_json = excluded.ai_emails_json,
                    updated_at = datetime('now')
                """,
                (user_id, resume or "", cover_letter or "", emails_json),
            )
        return True
    except Exception as e:
        logger.error(f"save_user_output error: {e}")
        return False


def load_user_output(user_id: str) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT ai_resume_md, ai_cover_letter, ai_emails_json FROM user_outputs WHERE user_id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
            if not row:
                return None
            try:
                emails = json.loads(row["ai_emails_json"] or "[]")
            except Exception:
                emails = []
            return {
                "ai_resume_markdown": row["ai_resume_md"] or "",
                "ai_cover_letter": row["ai_cover_letter"] or "",
                "ai_emails": emails or [],
            }
    except Exception as e:
        logger.error(f"load_user_output error: {e}")
        return None


# Backwards aliases
def get_user_payment_status(user_id: str) -> bool:
    return is_user_paid(user_id)


init_db()

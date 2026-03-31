# payments_db.py — AI Career Accelerator (Rebuilt with tiers & leads)
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

# Tier hierarchy (ascending value)
TIER_ORDER = ["none", "starter", "pro", "executive"]
TIER_PRICES = {"starter": 1999, "pro": 3999, "executive": 7999}


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
        # USERS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     TEXT PRIMARY KEY,
                phone       TEXT NOT NULL,
                email       TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")

        # PAYMENTS (now with tier column)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                phone       TEXT NOT NULL,
                invoice_id  TEXT NOT NULL UNIQUE,
                amount      REAL NOT NULL CHECK(amount > 0),
                tier        TEXT NOT NULL DEFAULT 'starter',
                paid_at     TEXT,
                is_paid     INTEGER NOT NULL DEFAULT 0 CHECK(is_paid IN (0, 1)),
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        # Add tier column if it doesn't exist (migration for existing DBs)
        try:
            conn.execute("ALTER TABLE payments ADD COLUMN tier TEXT NOT NULL DEFAULT 'starter'")
        except Exception:
            pass  # Column already exists

        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON payments(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_id ON payments(invoice_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_paid ON payments(user_id, is_paid) WHERE is_paid = 1")

        # OUTPUTS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_outputs (
                user_id         TEXT PRIMARY KEY,
                ai_resume_md     TEXT,
                ai_cover_letter  TEXT,
                ai_emails_json   TEXT,
                created_at       TEXT DEFAULT (datetime('now')),
                updated_at       TEXT DEFAULT (datetime('now'))
            )
        """)

        # LEADS — email capture from landing page and login
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                phone      TEXT,
                email      TEXT NOT NULL,
                source     TEXT DEFAULT 'app_login',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email)")

        # PENDING STK INVOICES — tracks in-progress IntaSend M-Pesa payments
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_invoices (
                invoice_id  TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                phone       TEXT NOT NULL,
                tier        TEXT NOT NULL DEFAULT 'starter',
                amount      REAL NOT NULL,
                state       TEXT NOT NULL DEFAULT 'PENDING',
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_user ON pending_invoices(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_state ON pending_invoices(state)")


# ─────────────────────────────────────────────
# PHONE / EMAIL NORMALIZATION
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# USER MANAGEMENT
# ─────────────────────────────────────────────

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
            "INSERT INTO users (user_id, phone, email, created_at, updated_at) VALUES (?, ?, ?, datetime('now'), datetime('now'))",
            (user_id, phone_n, email_n),
        )
        return {"user_id": user_id, "phone": phone_n, "email": email_n}


def find_user(phone: str, email: str) -> Optional[Dict[str, str]]:
    """
    Look up an existing user by phone+email — NEVER creates a new record.
    Returns None if no match found.
    """
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
        if not row:
            return None
        return {"user_id": row["user_id"], "phone": row["phone"], "email": row["email"]}


def save_lead(phone: str, email: str, source: str = "app_login") -> None:
    """Save an email lead for follow-up marketing."""
    try:
        email_n = normalize_email(email)
        phone_n = normalize_phone(phone) if phone else ""
        if not email_n:
            return
        with get_connection() as conn:
            # Don't duplicate leads
            existing = conn.execute(
                "SELECT id FROM leads WHERE email = ? LIMIT 1", (email_n,)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO leads (phone, email, source, created_at) VALUES (?, ?, ?, datetime('now'))",
                    (phone_n, email_n, source),
                )
    except Exception as e:
        logger.error(f"save_lead error: {e}")


# ─────────────────────────────────────────────
# TIER MANAGEMENT
# ─────────────────────────────────────────────

def get_user_tier(user_id: str) -> str:
    """
    Returns the highest tier the user has paid for.
    Returns 'none' if no paid plan.
    """
    if not user_id:
        return "none"
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT tier FROM payments WHERE user_id = ? AND is_paid = 1",
            (user_id,),
        ).fetchall()
        if not rows:
            return "none"

        tiers_paid = [row["tier"] for row in rows if row["tier"] in TIER_ORDER]
        if not tiers_paid:
            return "starter"  # Legacy: if paid but no tier recorded, give starter

        # Return the highest tier
        best = max(tiers_paid, key=lambda t: TIER_ORDER.index(t))
        return best


def mark_user_paid(user_id: str, tier: str = "starter") -> bool:
    """Mark a user as paid for a specific tier."""
    if not user_id:
        return False
    if tier not in TIER_ORDER or tier == "none":
        tier = "starter"

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    try:
        with get_connection() as conn:
            # Update existing unpaid record
            cur = conn.execute(
                "UPDATE payments SET is_paid = 1, paid_at = ?, tier = ?, updated_at = datetime('now') WHERE user_id = ? AND is_paid = 0 AND tier = ?",
                (now, tier, user_id, tier),
            )
            if cur.rowcount == 0:
                # Insert new paid record
                invoice_id = f"manual-{tier}-{user_id}-{int(datetime.utcnow().timestamp())}"
                conn.execute(
                    "INSERT OR IGNORE INTO payments (user_id, phone, invoice_id, amount, tier, is_paid, paid_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, datetime('now'), datetime('now'))",
                    (user_id, "WHATSAPP", invoice_id, TIER_PRICES.get(tier, 1999), tier, now),
                )
        return True
    except Exception as e:
        logger.error(f"mark_user_paid error: {e}")
        return False


# ─────────────────────────────────────────────
# BACKWARDS COMPATIBILITY
# ─────────────────────────────────────────────

def get_user_payment_status(user_id: str) -> bool:
    """Returns True if user has any paid tier (backwards compatible)."""
    return get_user_tier(user_id) != "none"


def is_user_paid(user_id: str) -> bool:
    return get_user_payment_status(user_id)


# ─────────────────────────────────────────────
# OUTPUTS
# ─────────────────────────────────────────────

def save_user_output(user_id: str, resume: str, cover_letter: str, emails) -> bool:
    if not user_id:
        return False
    try:
        emails_json = json.dumps(emails or [], ensure_ascii=False)
    except Exception:
        emails_json = "[]"

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
                "ai_emails": emails,
            }
    except Exception as e:
        logger.error(f"load_user_output error: {e}")
        return None


# ─────────────────────────────────────────────
# ADMIN QUERIES
# ─────────────────────────────────────────────

def get_user_payments(user_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        if user_id:
            cur = conn.execute("SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
        else:
            cur = conn.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (int(limit),))
        return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────
# STK INVOICE TRACKING (IntaSend M-Pesa)
# ─────────────────────────────────────────────

def create_pending_invoice(
    invoice_id: str,
    user_id: str,
    phone: str,
    tier: str,
    amount: float,
) -> bool:
    """Record a newly initiated STK push invoice as PENDING."""
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_invoices
                (invoice_id, user_id, phone, tier, amount, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'PENDING', datetime('now'), datetime('now'))
                """,
                (invoice_id, user_id, phone, tier, amount),
            )
        return True
    except Exception as e:
        logger.error(f"create_pending_invoice error: {e}")
        return False


def update_invoice_state(invoice_id: str, new_state: str) -> bool:
    """Update the state of a pending invoice (PENDING→COMPLETE/FAILED/CANCELLED)."""
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE pending_invoices SET state = ?, updated_at = datetime('now') WHERE invoice_id = ?",
                (new_state, invoice_id),
            )
        return True
    except Exception as e:
        logger.error(f"update_invoice_state error: {e}")
        return False


def confirm_invoice_payment(invoice_id: str) -> bool:
    """
    Called when IntaSend confirms COMPLETE.
    Marks the invoice state and creates a confirmed payment record.
    Returns True if successful.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT user_id, phone, tier, amount FROM pending_invoices WHERE invoice_id = ? LIMIT 1",
                (invoice_id,),
            ).fetchone()
            if not row:
                logger.warning(f"confirm_invoice_payment: invoice {invoice_id} not found")
                return False

            user_id = row["user_id"]
            phone = row["phone"]
            tier = row["tier"]
            amount = row["amount"]

            # Mark invoice as complete
            conn.execute(
                "UPDATE pending_invoices SET state = 'COMPLETE', updated_at = datetime('now') WHERE invoice_id = ?",
                (invoice_id,),
            )

            # Record confirmed payment
            now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            conn.execute(
                """
                INSERT OR IGNORE INTO payments
                (user_id, phone, invoice_id, amount, tier, is_paid, paid_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, datetime('now'), datetime('now'))
                """,
                (user_id, phone, invoice_id, amount, tier, now),
            )
        logger.info(f"Payment confirmed: user={user_id} tier={tier} invoice={invoice_id}")
        return True
    except Exception as e:
        logger.error(f"confirm_invoice_payment error: {e}")
        return False


def get_pending_invoice_for_user(user_id: str) -> Optional[Dict[str, Any]]:
    """Get the most recent pending/processing invoice for a user (for UI polling)."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT invoice_id, tier, amount, state, created_at
                FROM pending_invoices
                WHERE user_id = ? AND state IN ('PENDING', 'PROCESSING')
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"get_pending_invoice_for_user error: {e}")
        return None


def get_all_leads(limit: int = 200) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM leads ORDER BY created_at DESC LIMIT ?", (int(limit),))
        return [dict(r) for r in cur.fetchall()]


def get_dashboard_stats() -> Dict[str, Any]:
    with get_connection() as conn:
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        total_paid = conn.execute("SELECT COUNT(DISTINCT user_id) as c FROM payments WHERE is_paid = 1").fetchone()["c"]
        total_revenue = conn.execute("SELECT COALESCE(SUM(amount), 0) as s FROM payments WHERE is_paid = 1").fetchone()["s"]
        total_leads = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()["c"]

        tier_counts = {}
        for tier in ["starter", "pro", "executive"]:
            count = conn.execute(
                "SELECT COUNT(DISTINCT user_id) as c FROM payments WHERE is_paid = 1 AND tier = ?",
                (tier,),
            ).fetchone()["c"]
            tier_counts[tier] = count

        return {
            "total_users": total_users,
            "total_paid": total_paid,
            "total_revenue_ksh": total_revenue,
            "total_leads": total_leads,
            "tier_counts": tier_counts,
        }


# Auto-init on import
init_db()

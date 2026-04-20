# ================================================================
# BISTBULL TERMINAL -- STORAGE
# infra/storage.py
#
# Lightweight SQLite persistence. Zero heavy deps, thread-safe via
# check_same_thread=False.
#
# Phase 7 baseline: watchlist, alerts, symbol_snapshots.
# Phase 1 additions:
#   - users table (argon2id password_hash)
#   - last_accessed_at column on watchlist and alerts (migration-safe)
#   - user_* CRUD + session_migrate_to_user (for anon -> user signup)
#
# On Railway: mount a volume at /data for cross-deploy persistence.
# Without volume: data resets on each deploy (acceptable for v1).
# ================================================================

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import threading
from typing import Optional

from infra.migrations import apply_migrations, _ensure_column

log = logging.getLogger("bistbull.storage")

DB_PATH = os.environ.get("BISTBULL_DB_PATH", "/data/bistbull.db")
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=3000")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db() -> None:
    """Initialize the schema. Three-step process as of Phase 2:

      1. Create "baseline" tables that pre-date the migrations pattern
         (watchlist, alerts, symbol_snapshots -- Phase 7 vintage). These
         stay here for backwards compatibility with long-running DBs
         whose _schema_migrations tracking started only at Phase 2.
      2. _ensure_column the Phase 1 last_accessed_at additions. The
         corresponding migration file (002) is a no-op marker because
         SQLite lacks ADD COLUMN IF NOT EXISTS and we need a runtime
         check against PRAGMA table_info regardless of tracking.
      3. apply_migrations(conn) -- run any new migrations from
         infra/migrations/. NEW schema goes here, not inline.

    Safe to call multiple times (every step is idempotent).
    """
    conn = _get_conn()

    # Step 1: baseline / legacy tables (pre-migrations-pattern).
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id          TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            last_accessed_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            alert_type       TEXT NOT NULL,
            severity         TEXT NOT NULL DEFAULT 'info',
            title            TEXT NOT NULL,
            message          TEXT NOT NULL DEFAULT '',
            metadata         TEXT NOT NULL DEFAULT '{}',
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            last_accessed_at TEXT NOT NULL DEFAULT (datetime('now')),
            dedupe_key       TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_alerts_user
            ON alerts(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_alerts_dedupe
            ON alerts(dedupe_key);

        CREATE TABLE IF NOT EXISTS symbol_snapshots (
            user_id    TEXT NOT NULL,
            symbol     TEXT NOT NULL,
            snapshot   TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, symbol)
        );
        """
    )

    # Step 2: runtime backfill for Phase 1 columns on pre-Phase-1 installs.
    _ensure_column(conn, "watchlist", "last_accessed_at",
                   "TEXT NOT NULL DEFAULT (datetime('now'))")
    _ensure_column(conn, "alerts", "last_accessed_at",
                   "TEXT NOT NULL DEFAULT (datetime('now'))")
    conn.commit()

    # Step 3: run versioned migrations (001 users, 002 marker, 003 score_history, ...).
    apply_migrations(conn)

    log.info(f"SQLite storage initialized: {DB_PATH}")


# ================================================================
# USER OPERATIONS (Phase 1)
# ================================================================
def user_create(email: str, password_hash: str) -> str:
    """Insert a new user and return the generated user_id.

    Raises sqlite3.IntegrityError on duplicate email -- callers should
    normally check first via user_get_by_email (see api/auth.py:register).
    """
    user_id = f"u_{secrets.token_urlsafe(16)}"
    conn = _get_conn()
    conn.execute(
        "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
        (user_id, email, password_hash),
    )
    conn.commit()
    return user_id


def user_get(user_id: str) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT user_id, email, password_hash, created_at, last_login_at, is_active "
        "FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "email": row["email"],
        "password_hash": row["password_hash"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
        "is_active": row["is_active"],
    }


def user_get_by_email(email: str) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT user_id, email, password_hash, created_at, last_login_at, is_active "
        "FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "email": row["email"],
        "password_hash": row["password_hash"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
        "is_active": row["is_active"],
    }


def user_update_last_login(user_id: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE users SET last_login_at = datetime('now') WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()


def session_migrate_to_user(session_id: str, new_user_id: str) -> dict:
    """Transfer all rows owned by an anonymous session_id to a real user.

    Called by /api/auth/register when body.session_id is present
    (FAZ 1.5.5). Runs as a single transaction across watchlist,
    alerts, and symbol_snapshots. The session_id row in users is NOT
    created or deleted -- session ids are cookie-scoped and do not
    live in users.

    Conflict strategy: if the target user already has a row with the
    same natural key (e.g., watchlist user_id + symbol), the anonymous
    row is dropped rather than raising. This matches signup flow where
    a migrated user may already have a fresh session row from during
    the registration request.

    Returns a dict of counts per table actually migrated.
    """
    if not session_id or not new_user_id:
        return {"watchlist": 0, "alerts": 0, "snapshots": 0}

    conn = _get_conn()
    try:
        # Start an explicit transaction so partial migrations can't
        # land -- python's sqlite3 auto-commits DDL but with BEGIN we
        # control the boundary.
        conn.execute("BEGIN IMMEDIATE")

        # Watchlist: UPDATE OR IGNORE so conflicts (unlikely) are
        # silently dropped; then DELETE any remaining anonymous rows
        # (the ones that couldn't migrate).
        cur = conn.execute(
            "UPDATE OR IGNORE watchlist SET user_id = ? WHERE user_id = ?",
            (new_user_id, session_id),
        )
        wl_count = cur.rowcount
        conn.execute("DELETE FROM watchlist WHERE user_id = ?", (session_id,))

        cur = conn.execute(
            "UPDATE alerts SET user_id = ? WHERE user_id = ?",
            (new_user_id, session_id),
        )
        al_count = cur.rowcount

        cur = conn.execute(
            "UPDATE OR IGNORE symbol_snapshots SET user_id = ? WHERE user_id = ?",
            (new_user_id, session_id),
        )
        sn_count = cur.rowcount
        conn.execute("DELETE FROM symbol_snapshots WHERE user_id = ?", (session_id,))

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    log.info(
        f"session_migrate_to_user: {session_id[:8]}... -> {new_user_id} "
        f"(wl={wl_count}, al={al_count}, sn={sn_count})"
    )
    return {"watchlist": wl_count, "alerts": al_count, "snapshots": sn_count}


# ================================================================
# WATCHLIST OPERATIONS
# ================================================================
def watchlist_add(user_id: str, symbol: str) -> bool:
    """Add symbol to watchlist. True if added, False if already exists."""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO watchlist (user_id, symbol) VALUES (?, ?)",
            (user_id, symbol.upper()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def watchlist_remove(user_id: str, symbol: str) -> bool:
    """Remove symbol from watchlist. True if removed."""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM watchlist WHERE user_id = ? AND symbol = ?",
        (user_id, symbol.upper()),
    )
    conn.commit()
    return cur.rowcount > 0


def watchlist_list(user_id: str) -> list[dict]:
    """List watchlist symbols for user. Updates last_accessed_at on read.

    Phase 1: updates every returned row's last_accessed_at to now, in
    the same connection as the SELECT. Prepares for Phase 6 sweep jobs.
    """
    conn = _get_conn()
    # Touch before read so the SELECT sees the current access time.
    conn.execute(
        "UPDATE watchlist SET last_accessed_at = datetime('now') WHERE user_id = ?",
        (user_id,),
    )
    rows = conn.execute(
        "SELECT symbol, created_at FROM watchlist WHERE user_id = ? ORDER BY created_at",
        (user_id,),
    ).fetchall()
    conn.commit()
    return [{"symbol": r["symbol"], "created_at": r["created_at"]} for r in rows]


# ================================================================
# ALERT OPERATIONS
# ================================================================
def alert_exists(dedupe_key: str) -> bool:
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM alerts WHERE dedupe_key = ?", (dedupe_key,)
    ).fetchone()
    return row is not None


def alert_save(user_id: str, alert: dict) -> bool:
    """Save an alert if dedupe_key is new. True if saved."""
    dk = alert.get("dedupe_key", "")
    if not dk or alert_exists(dk):
        return False
    conn = _get_conn()
    conn.execute(
        """INSERT INTO alerts (user_id, symbol, alert_type, severity, title,
                                message, metadata, dedupe_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            alert.get("symbol", ""),
            alert.get("alert_type", ""),
            alert.get("severity", "info"),
            alert.get("title", ""),
            alert.get("message", ""),
            alert.get("metadata", "{}"),
            dk,
        ),
    )
    conn.commit()
    return True


def alert_save_batch(user_id: str, alerts: list[dict]) -> int:
    saved = 0
    for a in alerts:
        if alert_save(user_id, a):
            saved += 1
    return saved


def alerts_get(user_id: str, limit: int = 50) -> list[dict]:
    """Get recent alerts for user. Updates last_accessed_at on read."""
    conn = _get_conn()
    # Touch the rows we're about to return -- newest LIMIT rows by id.
    conn.execute(
        """UPDATE alerts SET last_accessed_at = datetime('now')
           WHERE id IN (SELECT id FROM alerts WHERE user_id = ?
                        ORDER BY created_at DESC LIMIT ?)""",
        (user_id, limit),
    )
    rows = conn.execute(
        """SELECT symbol, alert_type, severity, title, message, metadata, created_at, dedupe_key
           FROM alerts WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    conn.commit()
    return [
        {
            "symbol": r["symbol"],
            "alert_type": r["alert_type"],
            "severity": r["severity"],
            "title": r["title"],
            "message": r["message"],
            "metadata": r["metadata"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# ================================================================
# SNAPSHOT OPERATIONS -- for alert change detection
# ================================================================
def snapshot_get(user_id: str, symbol: str) -> Optional[str]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT snapshot FROM symbol_snapshots WHERE user_id = ? AND symbol = ?",
        (user_id, symbol.upper()),
    ).fetchone()
    return row["snapshot"] if row else None


def snapshot_save(user_id: str, symbol: str, snapshot_json: str) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO symbol_snapshots (user_id, symbol, snapshot, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(user_id, symbol) DO UPDATE
             SET snapshot = excluded.snapshot, updated_at = excluded.updated_at""",
        (user_id, symbol.upper(), snapshot_json),
    )
    conn.commit()

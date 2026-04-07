# ================================================================
# BISTBULL TERMINAL — STORAGE (Phase 7)
# infra/storage.py
#
# Lightweight SQLite persistence for watchlist and alerts.
# Zero new dependencies. Thread-safe via check_same_thread=False.
#
# On Railway: mount a volume at /data for cross-deploy persistence.
# Without volume: data resets on each deploy (acceptable for v1).
# ================================================================

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Optional

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
    return _local.conn


def init_db():
    """Create tables if they don't exist. Safe to call multiple times."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id   TEXT NOT NULL,
            symbol    TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            symbol     TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            severity   TEXT NOT NULL DEFAULT 'info',
            title      TEXT NOT NULL,
            message    TEXT NOT NULL DEFAULT '',
            metadata   TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            dedupe_key TEXT NOT NULL
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

        CREATE TABLE IF NOT EXISTS score_history (
            symbol     TEXT NOT NULL,
            snap_date  TEXT NOT NULL,
            score      REAL,
            momentum   REAL,
            risk       REAL,
            fa_score   REAL,
            ivme       REAL,
            decision   TEXT,
            PRIMARY KEY (symbol, snap_date)
        );
    """)
    conn.commit()
    log.info(f"SQLite storage initialized: {DB_PATH}")


# ================================================================
# WATCHLIST OPERATIONS
# ================================================================
def watchlist_add(user_id: str, symbol: str) -> bool:
    """Add symbol to watchlist. Returns True if added, False if already exists."""
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
    """Remove symbol from watchlist. Returns True if removed."""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM watchlist WHERE user_id = ? AND symbol = ?",
        (user_id, symbol.upper()),
    )
    conn.commit()
    return cur.rowcount > 0


def watchlist_list(user_id: str) -> list[dict]:
    """List all watchlist symbols for user."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT symbol, created_at FROM watchlist WHERE user_id = ? ORDER BY created_at",
        (user_id,),
    ).fetchall()
    return [{"symbol": r["symbol"], "created_at": r["created_at"]} for r in rows]


# ================================================================
# ALERT OPERATIONS
# ================================================================
def alert_exists(dedupe_key: str) -> bool:
    """Check if an alert with this dedupe key already exists."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM alerts WHERE dedupe_key = ?", (dedupe_key,)
    ).fetchone()
    return row is not None


def alert_save(user_id: str, alert: dict) -> bool:
    """Save an alert if dedupe_key is new. Returns True if saved."""
    dk = alert.get("dedupe_key", "")
    if not dk or alert_exists(dk):
        return False
    conn = _get_conn()
    conn.execute(
        """INSERT INTO alerts (user_id, symbol, alert_type, severity, title, message, metadata, dedupe_key)
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
    """Save multiple alerts. Returns count of new alerts saved."""
    saved = 0
    for a in alerts:
        if alert_save(user_id, a):
            saved += 1
    return saved


def alerts_get(user_id: str, limit: int = 50) -> list[dict]:
    """Get recent alerts for user, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT symbol, alert_type, severity, title, message, metadata, created_at, dedupe_key
           FROM alerts WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
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
# SNAPSHOT OPERATIONS — for alert change detection
# ================================================================
def snapshot_get(user_id: str, symbol: str) -> Optional[str]:
    """Get the stored JSON snapshot for a symbol."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT snapshot FROM symbol_snapshots WHERE user_id = ? AND symbol = ?",
        (user_id, symbol.upper()),
    ).fetchone()
    return row["snapshot"] if row else None


def snapshot_save(user_id: str, symbol: str, snapshot_json: str):
    """Upsert the snapshot for a symbol."""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO symbol_snapshots (user_id, symbol, snapshot, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(user_id, symbol) DO UPDATE SET snapshot = excluded.snapshot, updated_at = excluded.updated_at""",
        (user_id, symbol.upper(), snapshot_json),
    )
    conn.commit()

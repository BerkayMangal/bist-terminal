"""SQLite + Redis storage for BullWatch list-membership events.

A second, lighter-weight alarm table that sits alongside the existing
`bullwatch_alerts` (which only records high-conviction CONVICTION fires).
Every scan compares the new top-N to the previous top-N and records:

  • ENTRY          — ticker appeared in the list this scan
  • EXIT           — ticker dropped out of the list this scan
  • ZONE_UPGRADE   — zone moved up (EARLY → CONFIRMED → CONVICTION)
  • ZONE_DOWNGRADE — zone moved down

These are intentionally separated from the conviction alarms so the
high-signal "system is very sure" feed doesn't get drowned by routine
list churn.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import sqlite3
import threading
from typing import Any, Optional

from core import redis_client

log = logging.getLogger("bistbull.bw_membership_storage")

KEY_EVENT = "bb:bwm:event:{}"
KEY_RECENT_ZSET = "bb:bwm:recent"
KEY_BY_TICKER = "bb:bwm:by_ticker:{}"
EVENT_TTL_SEC = 90 * 24 * 3600          # 90 days hot retention
RECENT_ZSET_LIMIT = 1000

EVENT_TYPES = ("ENTRY", "EXIT", "ZONE_UPGRADE", "ZONE_DOWNGRADE")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS bullwatch_membership_events (
    event_id        TEXT PRIMARY KEY,
    ticker          TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    occurred_at     TEXT NOT NULL,
    scan_id         TEXT,
    prev_score      REAL,
    new_score       REAL,
    prev_zone       TEXT,
    new_zone        TEXT,
    prev_pattern    TEXT,
    new_pattern     TEXT
);
CREATE INDEX IF NOT EXISTS idx_bwm_ticker
    ON bullwatch_membership_events(ticker, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_bwm_recent
    ON bullwatch_membership_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_bwm_type
    ON bullwatch_membership_events(event_type, occurred_at DESC);
"""


_local = threading.local()


def _conn() -> sqlite3.Connection:
    if getattr(_local, "conn", None) is None:
        try:
            from infra.storage import DB_PATH
        except ImportError:
            DB_PATH = "/data/bistbull.db"
        c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        c.row_factory = sqlite3.Row
        _local.conn = c
    return _local.conn


def init_db() -> None:
    try:
        c = _conn()
        c.executescript(_CREATE_SQL)
        c.commit()
        log.info("bullwatch_membership_events table ready")
    except Exception as exc:
        log.warning("bullwatch_membership_events init failed: %r", exc)


def _to_unix(iso: str) -> float:
    try:
        d = _dt.datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except Exception:
        return _dt.datetime.now(_dt.timezone.utc).timestamp()


def save_event(event: dict[str, Any]) -> bool:
    """Persist one event. Idempotent — same event_id is a no-op on re-run.

    Returns True iff at least ONE backend (SQLite, Redis) accepted the
    write. On Railway's read-only /data volume, Redis alone is enough."""
    sqlite_ok = _save_sqlite(event)
    redis_ok = _save_redis(event)
    return sqlite_ok or redis_ok


def _save_sqlite(event: dict[str, Any]) -> bool:
    try:
        c = _conn()
        c.execute(
            "INSERT OR IGNORE INTO bullwatch_membership_events "
            "(event_id, ticker, event_type, occurred_at, scan_id, "
            " prev_score, new_score, prev_zone, new_zone, "
            " prev_pattern, new_pattern) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.get("event_id"),
                event.get("ticker"),
                event.get("event_type"),
                event.get("occurred_at"),
                event.get("scan_id"),
                event.get("prev_score"),
                event.get("new_score"),
                event.get("prev_zone"),
                event.get("new_zone"),
                event.get("prev_pattern"),
                event.get("new_pattern"),
            ),
        )
        c.commit()
        return True
    except Exception as exc:
        log.debug("save_event sqlite: %r", exc)
        return False


def _save_redis(event: dict[str, Any]) -> bool:
    client = redis_client.get_client()
    if client is None:
        return False
    try:
        eid = event.get("event_id")
        if not eid:
            return False
        client.set(KEY_EVENT.format(eid),
                   json.dumps(event), ex=EVENT_TTL_SEC)
        score = _to_unix(event.get("occurred_at") or _now_iso())
        client.zadd(KEY_RECENT_ZSET, {eid: score})
        client.zremrangebyrank(KEY_RECENT_ZSET, 0,
                               -1 - RECENT_ZSET_LIMIT)
        sym = (event.get("ticker") or "").upper()
        if sym:
            client.zadd(KEY_BY_TICKER.format(sym), {eid: score})
        return True
    except Exception as exc:
        log.debug("save_event redis: %r", exc)
        return False


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def get_recent(
    limit: int = 100,
    since_days: Optional[int] = None,
    event_type: Optional[str] = None,
    tickers: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Pull recent events with optional filters."""
    try:
        c = _conn()
        q = "SELECT * FROM bullwatch_membership_events"
        params: list = []
        where: list[str] = []
        if since_days is not None:
            cutoff = (
                _dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(days=max(0, since_days))
            ).isoformat()
            where.append("occurred_at >= ?")
            params.append(cutoff)
        if event_type:
            where.append("event_type = ?")
            params.append(event_type)
        if tickers:
            placeholders = ",".join("?" * len(tickers))
            where.append(f"ticker IN ({placeholders})")
            params.extend([t.upper() for t in tickers])
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(int(limit))
        rows = c.execute(q, params).fetchall()
        if rows:
            return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("get_recent sqlite: %r", exc)

    # Redis fallback (lighter — only honors since/limit, not type/tickers)
    client = redis_client.get_client()
    if client is None:
        return []
    try:
        ids = client.zrevrange(KEY_RECENT_ZSET, 0, max(0, limit - 1))
        if not ids:
            return []
        raws = client.mget([KEY_EVENT.format(i) for i in ids])
        out: list[dict[str, Any]] = []
        for raw in raws:
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event_type and ev.get("event_type") != event_type:
                continue
            if tickers and (ev.get("ticker") or "").upper() not in {
                t.upper() for t in tickers
            }:
                continue
            out.append(ev)
        return out
    except Exception as exc:
        log.warning("get_recent redis: %r", exc)
        return []


def get_by_ticker(ticker: str, limit: int = 50) -> list[dict[str, Any]]:
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        return []
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM bullwatch_membership_events "
            "WHERE ticker = ? ORDER BY occurred_at DESC LIMIT ?",
            (sym, int(limit)),
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("get_by_ticker sqlite %s: %r", sym, exc)

    client = redis_client.get_client()
    if client is None:
        return []
    try:
        ids = client.zrevrange(KEY_BY_TICKER.format(sym), 0, max(0, limit - 1))
        if not ids:
            return []
        raws = client.mget([KEY_EVENT.format(i) for i in ids])
        out: list[dict[str, Any]] = []
        for raw in raws:
            if raw:
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        return out
    except Exception:
        return []


def get_stats() -> dict[str, Any]:
    """Counts by type for the Alarmlar chip badges."""
    out: dict[str, Any] = {
        "by_type": {t: 0 for t in EVENT_TYPES},
        "total_30d": 0,
        "newest": None,
    }
    try:
        c = _conn()
        cutoff = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
        ).isoformat()
        for t in EVENT_TYPES:
            r = c.execute(
                "SELECT COUNT(*) FROM bullwatch_membership_events "
                "WHERE event_type = ? AND occurred_at >= ?",
                (t, cutoff),
            ).fetchone()
            out["by_type"][t] = int(r[0] or 0)
        out["total_30d"] = sum(out["by_type"].values())
        row = c.execute(
            "SELECT MAX(occurred_at) FROM bullwatch_membership_events"
        ).fetchone()
        out["newest"] = row[0] if row else None
    except Exception as exc:
        log.debug("get_stats: %r", exc)
    return out

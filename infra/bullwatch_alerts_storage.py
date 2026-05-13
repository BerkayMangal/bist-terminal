# ================================================================
# BISTBULL TERMINAL — BULLWATCH ALARM STORAGE
# infra/bullwatch_alerts_storage.py
#
# Two-tier persistence for BullWatchAlert records (KAP storage pattern):
#   SQLite (cold, durable) — bullwatch_alerts table, never expires
#   Redis (hot) — bb:bwa:alert:{id}, ZSET bb:bwa:recent
#                 (Faz 4 reaction backfill mutates these in place)
#
# Idempotent inserts by alert_id. Dedupe by ticker is enforced at the
# engine layer (engine.bullwatch_alerts.dispatch_scan_alerts) via
# was_alarmed_within(ticker, days).
# ================================================================

from __future__ import annotations

import datetime as _dt
import json
import logging
import sqlite3
import threading
from typing import Any, Optional

from core import redis_client
from engine.bullwatch_alerts import BullWatchAlert

log = logging.getLogger("bistbull.bw_alerts_storage")

# Redis keys
KEY_ALERT = "bb:bwa:alert:{}"
KEY_RECENT_ZSET = "bb:bwa:recent"
KEY_BY_TICKER = "bb:bwa:by_ticker:{}"
ALERT_TTL_SEC = 180 * 24 * 3600       # 6 months hot retention
RECENT_ZSET_LIMIT = 500

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS bullwatch_alerts (
    alert_id              TEXT PRIMARY KEY,
    ticker                TEXT NOT NULL,
    alarmed_at            TEXT NOT NULL,
    score_at_alarm        REAL,
    zone_at_alarm         TEXT,
    pattern_at_alarm      TEXT,
    data_quality_at_alarm TEXT,
    engines_fired         INTEGER,
    sector_tr             TEXT,
    price_at_alarm        REAL,
    reaction_1d_pct       REAL,
    reaction_1w_pct       REAL,
    reaction_1m_pct       REAL,
    reaction_updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_bwa_ticker ON bullwatch_alerts(ticker, alarmed_at DESC);
CREATE INDEX IF NOT EXISTS idx_bwa_recent ON bullwatch_alerts(alarmed_at DESC);
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
    """Create the bullwatch_alerts table — called once at app startup."""
    try:
        c = _conn()
        c.executescript(_CREATE_SQL)
        c.commit()
        log.info("bullwatch_alerts table ready")
    except Exception as exc:
        log.warning("bullwatch_alerts init_db failed: %r", exc)


# ── Helpers ─────────────────────────────────────────────────────────


def _to_unix(iso: str) -> float:
    try:
        d = _dt.datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except Exception:
        return _dt.datetime.now(_dt.timezone.utc).timestamp()


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ── Save ────────────────────────────────────────────────────────────


def save_alert(alert: BullWatchAlert) -> bool:
    """Persist one alarm to SQLite + Redis. Returns True iff at least
    one tier accepted the write (mirrors KAP storage so Railway's
    occasionally-unwritable SQLite volume doesn't block the feature)."""
    ok_sql = _save_sqlite(alert)
    ok_redis = _save_redis(alert)
    return ok_sql or ok_redis


def _save_sqlite(alert: BullWatchAlert) -> bool:
    try:
        c = _conn()
        cur = c.execute(
            """
            INSERT OR IGNORE INTO bullwatch_alerts (
                alert_id, ticker, alarmed_at,
                score_at_alarm, zone_at_alarm, pattern_at_alarm,
                data_quality_at_alarm, engines_fired,
                sector_tr, price_at_alarm
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert.alert_id, alert.ticker, alert.alarmed_at,
                alert.score_at_alarm, alert.zone_at_alarm, alert.pattern_at_alarm,
                alert.data_quality_at_alarm, alert.engines_fired,
                alert.sector_tr, alert.price_at_alarm,
            ),
        )
        c.commit()
        return cur.rowcount > 0
    except Exception as exc:
        log.warning("save_alert sqlite %s: %r", alert.alert_id, exc)
        return False


def _save_redis(alert: BullWatchAlert) -> bool:
    client = redis_client.get_client()
    if client is None:
        return False
    try:
        client.set(
            KEY_ALERT.format(alert.alert_id),
            json.dumps(alert.to_dict(), ensure_ascii=False, default=str),
            ex=ALERT_TTL_SEC, nx=True,
        )
        score = _to_unix(alert.alarmed_at)
        client.zadd(KEY_RECENT_ZSET, {alert.alert_id: score})
        client.zadd(KEY_BY_TICKER.format(alert.ticker),
                    {alert.alert_id: score})
        try:
            client.zremrangebyrank(KEY_RECENT_ZSET, 0, -(RECENT_ZSET_LIMIT + 1))
        except Exception:
            pass
        return True
    except Exception as exc:
        log.warning("save_alert redis %s: %r", alert.alert_id, exc)
        return False


# ── Dedupe / read ───────────────────────────────────────────────────


def was_alarmed_within(ticker: str, days: int) -> bool:
    """True iff this ticker has an alarm in the past `days` days.
    Used by dispatcher to prevent re-alarming the same name daily
    while it stays in CONVICTION."""
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        return False
    cutoff = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=max(0, days))
    ).isoformat()
    try:
        c = _conn()
        row = c.execute(
            "SELECT 1 FROM bullwatch_alerts "
            "WHERE ticker = ? AND alarmed_at >= ? LIMIT 1",
            (sym, cutoff),
        ).fetchone()
        if row:
            return True
    except Exception as exc:
        log.debug("was_alarmed_within sqlite %s: %r", sym, exc)
    # Redis fallback: check the ticker's ZSET for a member with score >=
    # cutoff_unix (handles environments where SQLite isn't writable)
    client = redis_client.get_client()
    if client is not None:
        try:
            cutoff_unix = _to_unix(cutoff)
            count = client.zcount(KEY_BY_TICKER.format(sym), cutoff_unix, "+inf")
            return bool(count and int(count) > 0)
        except Exception:
            return False
    return False


def get_recent(limit: int = 50,
               since_days: Optional[int] = None) -> list[dict[str, Any]]:
    """Latest alarms across all tickers. Used by /api/bullwatch/alerts/recent."""
    try:
        c = _conn()
        if since_days is not None:
            cutoff = (
                _dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(days=max(0, since_days))
            ).isoformat()
            rows = c.execute(
                "SELECT * FROM bullwatch_alerts WHERE alarmed_at >= ? "
                "ORDER BY alarmed_at DESC LIMIT ?",
                (cutoff, int(limit)),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM bullwatch_alerts "
                "ORDER BY alarmed_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        if rows:
            return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("get_recent sqlite: %r", exc)
    # Redis fallback
    client = redis_client.get_client()
    if client is None:
        return []
    try:
        ids = client.zrevrange(KEY_RECENT_ZSET, 0, max(0, limit - 1))
        if not ids:
            return []
        keys = [KEY_ALERT.format(i) for i in ids]
        raws = client.mget(keys)
        out: list[dict[str, Any]] = []
        for raw in raws:
            if raw:
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        return out
    except Exception as exc:
        log.warning("get_recent redis: %r", exc)
        return []


def get_by_ticker(ticker: str, limit: int = 20) -> list[dict[str, Any]]:
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        return []
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM bullwatch_alerts WHERE ticker = ? "
            "ORDER BY alarmed_at DESC LIMIT ?",
            (sym, int(limit)),
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]
    except Exception:
        pass
    client = redis_client.get_client()
    if client is None:
        return []
    try:
        ids = client.zrevrange(KEY_BY_TICKER.format(sym), 0, max(0, limit - 1))
        if not ids:
            return []
        raws = client.mget([KEY_ALERT.format(i) for i in ids])
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


def get_by_id(alert_id: str) -> Optional[dict[str, Any]]:
    try:
        c = _conn()
        row = c.execute(
            "SELECT * FROM bullwatch_alerts WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()
        if row:
            return dict(row)
    except Exception:
        pass
    client = redis_client.get_client()
    if client is None:
        return None
    try:
        raw = client.get(KEY_ALERT.format(alert_id))
        if raw:
            return json.loads(raw)
    except Exception:
        return None
    return None


def get_stats() -> dict[str, Any]:
    out: dict[str, Any] = {"total_in_sqlite": None,
                            "total_in_redis": None,
                            "newest_alarmed_at": None,
                            "last_30d_count": None}
    try:
        c = _conn()
        row = c.execute(
            "SELECT COUNT(*) AS n, MAX(alarmed_at) AS newest "
            "FROM bullwatch_alerts"
        ).fetchone()
        if row:
            out["total_in_sqlite"] = int(row["n"] or 0)
            out["newest_alarmed_at"] = row["newest"]
        cutoff = (_dt.datetime.now(_dt.timezone.utc)
                  - _dt.timedelta(days=30)).isoformat()
        row2 = c.execute(
            "SELECT COUNT(*) AS n FROM bullwatch_alerts WHERE alarmed_at >= ?",
            (cutoff,),
        ).fetchone()
        if row2:
            out["last_30d_count"] = int(row2["n"] or 0)
    except Exception:
        pass
    client = redis_client.get_client()
    if client is not None:
        try:
            out["total_in_redis"] = int(client.zcard(KEY_RECENT_ZSET))
        except Exception:
            pass
    return out


# ── Faz 4 hooks (used by engine/bullwatch_alert_reactions.py) ──────


def save_reactions(alert_id: str, cols: dict[str, float],
                   ts_iso: str) -> None:
    """Patch reaction_*_pct columns on a single alert. Mirrors
    infra.kap_storage save_reactions."""
    if not cols:
        return
    try:
        parts = [f"{k} = ?" for k in cols.keys()] + ["reaction_updated_at = ?"]
        params = list(cols.values()) + [ts_iso, alert_id]
        c = _conn()
        c.execute(
            f"UPDATE bullwatch_alerts SET {', '.join(parts)} "
            f"WHERE alert_id = ?",
            params,
        )
        c.commit()
    except Exception as exc:
        log.warning("save_reactions %s: %r", alert_id, exc)
    # Redis mirror — patch JSON in place
    client = redis_client.get_client()
    if client is None:
        return
    key = KEY_ALERT.format(alert_id)
    try:
        raw = client.get(key)
        if raw:
            obj = json.loads(raw)
            obj.update(cols)
            obj["reaction_updated_at"] = ts_iso
            client.set(key, json.dumps(obj, ensure_ascii=False, default=str),
                       ex=ALERT_TTL_SEC)
    except Exception as exc:
        log.debug("save_reactions redis %s: %r", alert_id, exc)


def fetch_needs_reaction_refresh(limit: int = 200) -> list[dict[str, Any]]:
    """Alarms older than 1 day with any NULL reaction column. Same
    pattern as infra.kap_storage._fetch_needs_refresh."""
    try:
        c = _conn()
        rows = c.execute(
            """
            SELECT alert_id, ticker, alarmed_at, price_at_alarm,
                   reaction_1d_pct, reaction_1w_pct, reaction_1m_pct
            FROM bullwatch_alerts
            WHERE (
                reaction_1d_pct IS NULL
                OR reaction_1w_pct IS NULL
                OR reaction_1m_pct IS NULL
            )
            AND alarmed_at < datetime('now', '-1 day')
            AND alarmed_at > datetime('now', '-60 days')
            ORDER BY alarmed_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("fetch_needs_reaction_refresh: %r", exc)
        return []

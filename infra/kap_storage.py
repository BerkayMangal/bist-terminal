# ================================================================
# BISTBULL TERMINAL — KAP DISCLOSURE STORAGE
# infra/kap_storage.py
#
# Two-tier persistence for KAP disclosure events:
#
#   Redis (hot, fast)
#     bb:kap:last_seen_index            STRING  — high-water mark for
#                                                 incremental polling
#     bb:kap:disclosure:{index}         STRING  — JSON DisclosureRecord
#                                                 (90-day TTL)
#     bb:kap:recent                     ZSET    — score=publish_unix,
#                                                 member=disclosure_index
#                                                 (last 1000 entries)
#     bb:kap:by_ticker:{TICKER}         ZSET    — same shape, per-ticker
#
#   SQLite (cold, durable archive)
#     kap_disclosures table — same shape, never expires. Survives Redis
#     flushes and Railway restarts.
#
# Inserts are idempotent — disclosure_index is the primary key on both
# tiers. So a poller that re-fetches an old day is a no-op.
# ================================================================

from __future__ import annotations

import datetime as _dt
import json
import logging
import sqlite3
import threading
from typing import Any, Optional

from core import redis_client
from data.kap_client import DisclosureRecord

log = logging.getLogger("bistbull.kap_storage")

# ── Redis keys ──────────────────────────────────────────────────────

KEY_LAST_SEEN = "bb:kap:last_seen_index"
KEY_DISCLOSURE = "bb:kap:disclosure:{}"
KEY_RECENT_ZSET = "bb:kap:recent"
KEY_BY_TICKER = "bb:kap:by_ticker:{}"

DISCLOSURE_TTL_SEC = 90 * 24 * 3600     # 90 days hot retention
RECENT_ZSET_LIMIT = 1000                # cap the global recent list

# ── SQLite schema ───────────────────────────────────────────────────

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS kap_disclosures (
    disclosure_index    INTEGER PRIMARY KEY,
    ticker              TEXT NOT NULL,
    kap_title           TEXT,
    subject             TEXT,
    disclosure_type     TEXT,
    disclosure_class    TEXT,
    publish_date        TEXT NOT NULL,           -- ISO8601 UTC
    publish_date_raw    TEXT,
    rule_type           TEXT,
    period              INTEGER,
    year                INTEGER,
    attachment_count    INTEGER DEFAULT 0,
    is_late             INTEGER DEFAULT 0,
    url                 TEXT,
    fetched_at          TEXT NOT NULL,           -- ISO8601 UTC
    ai_analyzed_at      TEXT,                    -- populated by Faz 3
    ai_summary          TEXT,                    -- populated by Faz 3
    price_at_disclosure REAL,                    -- Faz 4: close on event day
    reaction_1d_pct     REAL,                    -- Faz 4: 1 trading day later
    reaction_1w_pct     REAL,                    -- Faz 4: 5 trading days later
    reaction_1m_pct     REAL,                    -- Faz 4: 21 trading days later
    reaction_updated_at TEXT                     -- last reaction backfill timestamp
);
CREATE INDEX IF NOT EXISTS idx_kap_ticker ON kap_disclosures(ticker, publish_date DESC);
CREATE INDEX IF NOT EXISTS idx_kap_publish ON kap_disclosures(publish_date DESC);
"""

# Lightweight ALTER TABLE migration for the Faz 4 reaction columns.
# SQLite's CREATE TABLE IF NOT EXISTS doesn't add columns to existing
# tables, so we have to do this with a try/except per column for users
# whose DB was already initialized under Faz 1-3.
_FAZ4_MIGRATIONS = [
    "ALTER TABLE kap_disclosures ADD COLUMN price_at_disclosure REAL",
    "ALTER TABLE kap_disclosures ADD COLUMN reaction_1d_pct REAL",
    "ALTER TABLE kap_disclosures ADD COLUMN reaction_1w_pct REAL",
    "ALTER TABLE kap_disclosures ADD COLUMN reaction_1m_pct REAL",
    "ALTER TABLE kap_disclosures ADD COLUMN reaction_updated_at TEXT",
]


# ── Thread-local SQLite connection (mirrors infra/storage.py) ───────

_local = threading.local()


def _conn() -> sqlite3.Connection:
    """Reuse the same DB file as the rest of the app (BISTBULL_DB_PATH)."""
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
    """Create the kap_disclosures table if missing. Called once at
    app startup, after infra.storage.init_db()."""
    try:
        c = _conn()
        c.executescript(_CREATE_SQL)
        c.commit()
        # Idempotent Faz 4 column adds — IGNORE if they already exist
        # (SQLite raises OperationalError on "duplicate column name").
        for sql in _FAZ4_MIGRATIONS:
            try:
                c.execute(sql)
                c.commit()
            except sqlite3.OperationalError:
                pass
        log.info("kap_disclosures table ready (with reaction columns)")
    except Exception as exc:
        log.warning("kap_storage init_db failed: %r", exc)


# ── Helpers ─────────────────────────────────────────────────────────


def _to_publish_unix(rec: DisclosureRecord) -> float:
    """Unix timestamp from ISO publish_date — used as ZSET score."""
    try:
        d = _dt.datetime.fromisoformat(rec.publish_date)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except Exception:
        return _dt.datetime.now(_dt.timezone.utc).timestamp()


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ── Insertion ───────────────────────────────────────────────────────


def save_disclosure(rec: DisclosureRecord) -> bool:
    """Persist one disclosure to both tiers. Idempotent — pre-existing
    rows are skipped (Redis NX, SQLite INSERT OR IGNORE).

    Returns True iff this was actually new (the caller may want to
    trigger downstream side-effects on the new case only).
    """
    new_to_sqlite = _save_sqlite(rec)
    _save_redis(rec)
    return new_to_sqlite


def _save_sqlite(rec: DisclosureRecord) -> bool:
    try:
        c = _conn()
        cur = c.execute(
            """
            INSERT OR IGNORE INTO kap_disclosures (
                disclosure_index, ticker, kap_title, subject,
                disclosure_type, disclosure_class,
                publish_date, publish_date_raw, rule_type,
                period, year, attachment_count, is_late, url,
                fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.disclosure_index, rec.ticker, rec.kap_title, rec.subject,
                rec.disclosure_type, rec.disclosure_class,
                rec.publish_date, rec.publish_date_raw, rec.rule_type,
                rec.period, rec.year, rec.attachment_count,
                1 if rec.is_late else 0, rec.url,
                _now_iso(),
            ),
        )
        c.commit()
        return cur.rowcount > 0
    except Exception as exc:
        log.warning("save_disclosure sqlite %s/%s: %r",
                    rec.ticker, rec.disclosure_index, exc)
        return False


def _save_redis(rec: DisclosureRecord) -> None:
    client = redis_client.get_client()
    if client is None:
        return
    try:
        key = KEY_DISCLOSURE.format(rec.disclosure_index)
        # SET NX so we don't overwrite an existing entry mid-poll
        client.set(key, json.dumps(rec.to_dict(), ensure_ascii=False, default=str),
                   ex=DISCLOSURE_TTL_SEC, nx=True)
        score = _to_publish_unix(rec)
        client.zadd(KEY_RECENT_ZSET, {str(rec.disclosure_index): score})
        client.zadd(KEY_BY_TICKER.format(rec.ticker),
                    {str(rec.disclosure_index): score})
        # Trim global recent list — keep only the latest N
        try:
            client.zremrangebyrank(KEY_RECENT_ZSET, 0, -(RECENT_ZSET_LIMIT + 1))
        except Exception:
            pass
    except Exception as exc:
        log.warning("save_disclosure redis %s/%s: %r",
                    rec.ticker, rec.disclosure_index, exc)


# ── High-water mark for incremental polling ────────────────────────


def get_last_seen_index() -> int:
    client = redis_client.get_client()
    if client is None:
        return 0
    try:
        v = client.get(KEY_LAST_SEEN)
        return int(v) if v else 0
    except Exception:
        return 0


def set_last_seen_index(value: int) -> None:
    if value <= 0:
        return
    client = redis_client.get_client()
    if client is None:
        return
    try:
        client.set(KEY_LAST_SEEN, str(int(value)))
    except Exception as exc:
        log.warning("set_last_seen_index: %r", exc)


# ── Read paths ──────────────────────────────────────────────────────


def get_recent(limit: int = 50) -> list[dict[str, Any]]:
    """Return up to `limit` most-recent disclosures across all tickers.
    Reads Redis hot tier first; falls back to SQLite when Redis empty
    or unavailable (e.g. cold start post-restart)."""
    client = redis_client.get_client()
    if client is not None:
        try:
            indices = client.zrevrange(KEY_RECENT_ZSET, 0, max(0, limit - 1))
            if indices:
                keys = [KEY_DISCLOSURE.format(i) for i in indices]
                raws = client.mget(keys)
                out: list[dict[str, Any]] = []
                for raw in raws:
                    if not raw:
                        continue
                    try:
                        out.append(json.loads(raw))
                    except json.JSONDecodeError:
                        continue
                if out:
                    return out
        except Exception as exc:
            log.warning("get_recent redis: %r", exc)
    # SQLite fallback
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM kap_disclosures ORDER BY publish_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("get_recent sqlite: %r", exc)
        return []


def get_by_ticker(ticker: str, limit: int = 20) -> list[dict[str, Any]]:
    """Disclosure history for one ticker. Same hot/cold pattern."""
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        return []
    client = redis_client.get_client()
    if client is not None:
        try:
            indices = client.zrevrange(KEY_BY_TICKER.format(sym), 0, max(0, limit - 1))
            if indices:
                keys = [KEY_DISCLOSURE.format(i) for i in indices]
                raws = client.mget(keys)
                out: list[dict[str, Any]] = []
                for raw in raws:
                    if raw:
                        try:
                            out.append(json.loads(raw))
                        except json.JSONDecodeError:
                            continue
                if out:
                    return out
        except Exception as exc:
            log.warning("get_by_ticker redis %s: %r", sym, exc)
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM kap_disclosures WHERE ticker = ? "
            "ORDER BY publish_date DESC LIMIT ?",
            (sym, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("get_by_ticker sqlite %s: %r", sym, exc)
        return []


def save_ai_summary(disclosure_index: int, summary_text: str) -> bool:
    """Persist the AI-generated analysis for one disclosure. Faz 3 calls
    this from engine.kap_dispatcher after a successful Grok run.

    Returns True iff at least ONE of (SQLite, Redis) accepted the write.
    On Railway the SQLite volume may not be writable, so Redis alone
    is enough — the disclosure row is read back via get_by_index which
    falls through to Redis when SQLite is empty.
    """
    if not summary_text:
        return False
    ts = _now_iso()
    ok_sql = False
    try:
        c = _conn()
        cur = c.execute(
            "UPDATE kap_disclosures SET ai_summary = ?, ai_analyzed_at = ? "
            "WHERE disclosure_index = ?",
            (summary_text, ts, int(disclosure_index)),
        )
        c.commit()
        ok_sql = cur.rowcount > 0
    except Exception as exc:
        log.warning("save_ai_summary sqlite %s: %r", disclosure_index, exc)

    # Redis mirror — patch the JSON in place so /api/kap/recent picks it up
    ok_redis = False
    client = redis_client.get_client()
    if client is not None:
        key = KEY_DISCLOSURE.format(disclosure_index)
        try:
            raw = client.get(key)
            if raw:
                obj = json.loads(raw)
                obj["ai_summary"] = summary_text
                obj["ai_analyzed_at"] = ts
                client.set(key, json.dumps(obj, ensure_ascii=False, default=str),
                           ex=DISCLOSURE_TTL_SEC)
                ok_redis = True
        except Exception as exc:
            log.warning("save_ai_summary redis %s: %r", disclosure_index, exc)
    return ok_sql or ok_redis


def get_by_index(disclosure_index: int) -> Optional[dict[str, Any]]:
    """Fetch one disclosure by index. Tries SQLite first, falls back
    to Redis. On Railway the SQLite volume isn't always writable, so
    the Redis hot tier may be the only place the row landed."""
    try:
        c = _conn()
        row = c.execute(
            "SELECT * FROM kap_disclosures WHERE disclosure_index = ?",
            (int(disclosure_index),),
        ).fetchone()
        if row:
            return dict(row)
    except Exception as exc:
        log.warning("get_by_index %s: %r", disclosure_index, exc)
    # Redis fallback — read the JSON we wrote in _save_redis
    client = redis_client.get_client()
    if client is not None:
        try:
            raw = client.get(KEY_DISCLOSURE.format(int(disclosure_index)))
            if raw:
                return json.loads(raw)
        except Exception as exc:
            log.debug("get_by_index redis fallback %s: %r",
                      disclosure_index, exc)
    return None


def get_stats() -> dict[str, Any]:
    """Lightweight feed-health summary — used by the /api/kap/health
    endpoint and surfaced in the admin UI later."""
    out: dict[str, Any] = {
        "last_seen_index": get_last_seen_index(),
        "redis_available": False,
        "total_in_redis": None,
        "total_in_sqlite": None,
        "newest_publish_date": None,
    }
    client = redis_client.get_client()
    if client is not None:
        out["redis_available"] = True
        try:
            out["total_in_redis"] = int(client.zcard(KEY_RECENT_ZSET))
        except Exception:
            pass
    try:
        c = _conn()
        row = c.execute(
            "SELECT COUNT(*) AS n, MAX(publish_date) AS newest "
            "FROM kap_disclosures"
        ).fetchone()
        if row:
            out["total_in_sqlite"] = int(row["n"] or 0)
            out["newest_publish_date"] = row["newest"]
    except Exception:
        pass
    return out

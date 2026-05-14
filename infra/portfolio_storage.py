"""Portfolio position storage — açık tradeler + tarihçe.

Kullanıcı BullWatch kartından "+ Aldım" tıklayınca bir pozisyon açar.
Sistem her scan'den sonra exit signal hesaplar; sat zamanı geldiğinde
UI banner çıkartır.

Schema (`portfolio_positions`):
  position_id      TEXT PRIMARY KEY   — uuid
  ticker           TEXT NOT NULL
  entry_date       TEXT NOT NULL      — ISO8601 UTC
  entry_price      REAL NOT NULL
  lot              REAL NOT NULL      — adet
  notes            TEXT
  status           TEXT NOT NULL      — open | closed
  exit_date        TEXT
  exit_price       REAL
  exit_reason      TEXT
  -- BullWatch context at entry (used by exit signal engine for delta)
  score_at_entry   REAL
  zone_at_entry    TEXT
  pattern_at_entry TEXT
  kap_at_entry     REAL               — kap_activity sub-score
  own_at_entry     REAL               — ownership sub-score
  -- User-defined exit triggers
  stop_loss_pct    REAL               — default -8.0 (negative = below entry)
  take_profit_pct  REAL               — default +15.0 (positive = above)

Single source of truth: SQLite. Redis is not used here (positions are
user-specific and we don't want eventual-consistency surprises on a
small dataset).
"""
from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
import threading
import uuid
from typing import Any, Optional

log = logging.getLogger("bistbull.portfolio_storage")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_positions (
    position_id      TEXT PRIMARY KEY,
    ticker           TEXT NOT NULL,
    entry_date       TEXT NOT NULL,
    entry_price      REAL NOT NULL,
    lot              REAL NOT NULL,
    notes            TEXT,
    status           TEXT NOT NULL DEFAULT 'open',
    exit_date        TEXT,
    exit_price       REAL,
    exit_reason      TEXT,
    score_at_entry   REAL,
    zone_at_entry    TEXT,
    pattern_at_entry TEXT,
    kap_at_entry     REAL,
    own_at_entry     REAL,
    stop_loss_pct    REAL DEFAULT -8.0,
    take_profit_pct  REAL DEFAULT 15.0,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_portfolio_status
    ON portfolio_positions(status, entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_portfolio_ticker
    ON portfolio_positions(ticker, status, entry_date DESC);
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
        log.info("portfolio_positions table ready")
    except Exception as exc:
        log.warning("portfolio_positions init failed: %r", exc)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def open_position(
    ticker: str,
    entry_price: float,
    lot: float,
    *,
    entry_date: Optional[str] = None,
    notes: Optional[str] = None,
    score_at_entry: Optional[float] = None,
    zone_at_entry: Optional[str] = None,
    pattern_at_entry: Optional[str] = None,
    kap_at_entry: Optional[float] = None,
    own_at_entry: Optional[float] = None,
    stop_loss_pct: float = -8.0,
    take_profit_pct: float = 15.0,
) -> Optional[dict[str, Any]]:
    """Yeni pozisyon aç. Returns the inserted row dict or None on error."""
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym or entry_price is None or entry_price <= 0 or lot is None or lot <= 0:
        return None
    pid = uuid.uuid4().hex[:16]
    ed = entry_date or _now_iso()
    try:
        c = _conn()
        c.execute(
            "INSERT INTO portfolio_positions "
            "(position_id, ticker, entry_date, entry_price, lot, notes, "
            " status, score_at_entry, zone_at_entry, pattern_at_entry, "
            " kap_at_entry, own_at_entry, stop_loss_pct, take_profit_pct) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pid, sym, ed, float(entry_price), float(lot), notes,
                "open", score_at_entry, zone_at_entry, pattern_at_entry,
                kap_at_entry, own_at_entry,
                float(stop_loss_pct), float(take_profit_pct),
            ),
        )
        c.commit()
    except Exception as exc:
        log.warning("open_position failed: %r", exc)
        return None
    return get_by_id(pid)


def close_position(
    position_id: str,
    exit_price: float,
    *,
    exit_reason: Optional[str] = None,
    exit_date: Optional[str] = None,
) -> bool:
    if not position_id or exit_price is None or exit_price <= 0:
        return False
    ed = exit_date or _now_iso()
    try:
        c = _conn()
        cur = c.execute(
            "UPDATE portfolio_positions SET status='closed', "
            "exit_date=?, exit_price=?, exit_reason=? "
            "WHERE position_id=? AND status='open'",
            (ed, float(exit_price), exit_reason, position_id),
        )
        c.commit()
        return cur.rowcount > 0
    except Exception as exc:
        log.warning("close_position failed: %r", exc)
        return False


def get_by_id(position_id: str) -> Optional[dict[str, Any]]:
    if not position_id:
        return None
    try:
        c = _conn()
        row = c.execute(
            "SELECT * FROM portfolio_positions WHERE position_id=?",
            (position_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.debug("get_by_id failed: %r", exc)
        return None


def get_open(limit: int = 100) -> list[dict[str, Any]]:
    """Tüm açık pozisyonlar — yeniden eskiye."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM portfolio_positions WHERE status='open' "
            "ORDER BY entry_date DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("get_open failed: %r", exc)
        return []


def get_history(
    limit: int = 100,
    ticker: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Closed pozisyonlar."""
    try:
        c = _conn()
        if ticker:
            sym = ticker.upper().strip().replace(".IS", "")
            rows = c.execute(
                "SELECT * FROM portfolio_positions WHERE status='closed' "
                "AND ticker=? ORDER BY exit_date DESC LIMIT ?",
                (sym, int(limit)),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM portfolio_positions WHERE status='closed' "
                "ORDER BY exit_date DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("get_history failed: %r", exc)
        return []


def get_stats() -> dict[str, Any]:
    out: dict[str, Any] = {
        "open_count": 0, "closed_count": 0,
        "winners": 0, "losers": 0, "breakeven": 0,
        "total_pnl_pct": 0.0,
    }
    try:
        c = _conn()
        out["open_count"] = c.execute(
            "SELECT COUNT(*) FROM portfolio_positions WHERE status='open'"
        ).fetchone()[0]
        out["closed_count"] = c.execute(
            "SELECT COUNT(*) FROM portfolio_positions WHERE status='closed'"
        ).fetchone()[0]
        rows = c.execute(
            "SELECT entry_price, exit_price FROM portfolio_positions "
            "WHERE status='closed' AND exit_price IS NOT NULL"
        ).fetchall()
        total_pnl = 0.0
        for r in rows:
            ep = float(r["entry_price"] or 0)
            xp = float(r["exit_price"] or 0)
            if ep > 0:
                pnl = (xp - ep) / ep * 100.0
                total_pnl += pnl
                if pnl > 0.5:
                    out["winners"] += 1
                elif pnl < -0.5:
                    out["losers"] += 1
                else:
                    out["breakeven"] += 1
        out["total_pnl_pct"] = round(total_pnl, 2)
        if out["closed_count"]:
            out["win_rate"] = round(
                out["winners"] / out["closed_count"] * 100.0, 1,
            )
    except Exception as exc:
        log.debug("get_stats failed: %r", exc)
    return out

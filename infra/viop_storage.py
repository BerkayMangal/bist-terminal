"""VIOP snapshot storage — daily option/futures contract data.

borsapy's `VIOP` class returns DataFrames with:
  code, contract, price, change, volume_tl, volume_qty, category

We snapshot the whole universe daily and persist each row so the UOA
engine can compute volume z-scores (anomaly detection) against a rolling
N-day baseline.

Schema (`viop_snapshots`):
  fetched_at      ISO8601 UTC
  snap_date       'YYYY-MM-DD' (in TR timezone, for daily grouping)
  code            "O_BIMASE0526C33.00" / "F_XU0300626"
  contract        "BIMAS Mayis 2026 Call 33.00 E"
  category        stock | index | currency | commodity
  kind            option | future        (derived from code prefix)
  underlying      BIMAS / XU030 / USDTRY  (parsed)
  side            C | P | F               (call / put / future)
  strike          REAL (None for futures)
  expiry          'YYYY-MM' string (e.g. '2026-05')
  price           REAL
  change          REAL
  volume_tl       REAL
  volume_qty      REAL

PK: (snap_date, code). Idempotent INSERT OR REPLACE — if intraday
re-fetch happens, the latest wins for that day.
"""
from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
import threading
from typing import Any, Optional

log = logging.getLogger("bistbull.viop_storage")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS viop_snapshots (
    fetched_at  TEXT NOT NULL,
    snap_date   TEXT NOT NULL,
    code        TEXT NOT NULL,
    contract    TEXT,
    category    TEXT,
    kind        TEXT,
    underlying  TEXT,
    side        TEXT,
    strike      REAL,
    expiry      TEXT,
    price       REAL,
    change      REAL,
    volume_tl   REAL,
    volume_qty  REAL,
    PRIMARY KEY (snap_date, code)
);
CREATE INDEX IF NOT EXISTS idx_viop_underlying
    ON viop_snapshots(underlying, snap_date DESC);
CREATE INDEX IF NOT EXISTS idx_viop_kind_date
    ON viop_snapshots(kind, snap_date DESC);
CREATE INDEX IF NOT EXISTS idx_viop_code_date
    ON viop_snapshots(code, snap_date DESC);
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
        log.info("viop_snapshots table ready")
    except Exception as exc:
        log.warning("viop_snapshots init failed: %r", exc)


def parse_code(code: str) -> dict[str, Any]:
    """Parse a borsapy VIOP code into structured fields.

    Code formats (observed from live borsapy output):
      OPTION:  O_{UNDERLYING}{E|A}{MM}{YY}{C|P}{STRIKE}
        e.g. O_BIMASE0526C33.00        → BIMAS, May 2026, Call, 33.00
        e.g. O_XU030E0826C17500.00     → XU030, Aug 2026, Call, 17500.00
      FUTURE:  F_{UNDERLYING}{MM}{YY}
        e.g. F_XU0300626                → XU030, Jun 2026
        e.g. F_USDTRY0826               → USDTRY, Aug 2026
        e.g. F_X10XB1226                → X10XB, Dec 2026

    The underlying CAN contain digits (XU030, X10XB), so a greedy regex
    can't find the boundary reliably. Manual parse: anchor on C/P for
    options (the side marker is unambiguous mid-code), and on the last
    4 digits for futures (MMYY).

    Unknown/malformed codes return mostly-None values — the row still
    persists, we just can't slice it by underlying.
    """
    out: dict[str, Any] = {
        "kind": None, "underlying": None, "side": None,
        "strike": None, "expiry": None,
    }
    if not code:
        return out
    code = code.strip().upper()

    if code.startswith("O_"):
        body = code[2:]
        # Find rightmost C or P that has digits + dot after it (= strike).
        # Walk from end backwards: strike chars are [0-9.], then C/P,
        # then MMYY (4 digits), then style (1 char E/A), then underlying.
        side_idx = -1
        for i in range(len(body) - 1, -1, -1):
            ch = body[i]
            if ch in ("C", "P"):
                # Verify what follows looks like a strike (digits + dot)
                after = body[i + 1:]
                if after and all(c.isdigit() or c == "." for c in after):
                    side_idx = i
                    break
        if side_idx <= 5:                    # need room for underlying+style+date
            return out
        side = body[side_idx]
        strike_str = body[side_idx + 1:]
        date_str = body[side_idx - 4:side_idx]   # MMYY
        if not date_str.isdigit():
            return out
        # The char immediately before MMYY is the style marker (E/A);
        # everything before that is the underlying.
        style_idx = side_idx - 5
        underlying = body[:style_idx] if style_idx > 0 else body[:side_idx - 4]
        out["kind"] = "option"
        out["underlying"] = underlying
        out["side"] = side
        try:
            out["strike"] = float(strike_str)
        except ValueError:
            pass
        out["expiry"] = f"20{date_str[2:]}-{date_str[:2]}"
        return out

    if code.startswith("F_"):
        body = code[2:]
        if len(body) < 5:
            return out
        date_str = body[-4:]
        if not date_str.isdigit():
            return out
        underlying = body[:-4]
        out["kind"] = "future"
        out["underlying"] = underlying
        out["side"] = "F"
        out["expiry"] = f"20{date_str[2:]}-{date_str[:2]}"
        return out

    return out


def _tr_today_str() -> str:
    """Today's date in Turkey (Europe/Istanbul, UTC+3) as 'YYYY-MM-DD'.
    BIST trading day boundary — TZ-aware so a fetch at 23:30 UTC on
    2026-05-13 still maps to 2026-05-14 in Türkiye."""
    tz = _dt.timezone(_dt.timedelta(hours=3))
    return _dt.datetime.now(tz).strftime("%Y-%m-%d")


def save_snapshot(rows: list[dict[str, Any]]) -> int:
    """Persist a batch of contract rows for today. Returns rows written.

    Each row needs at minimum: code, price, volume_tl, volume_qty.
    Missing optional fields are tolerated.
    """
    if not rows:
        return 0
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    snap_date = _tr_today_str()
    written = 0
    try:
        c = _conn()
        for r in rows:
            code = (r.get("code") or "").strip()
            if not code:
                continue
            parsed = parse_code(code)
            c.execute(
                "INSERT OR REPLACE INTO viop_snapshots "
                "(fetched_at, snap_date, code, contract, category, kind, "
                " underlying, side, strike, expiry, "
                " price, change, volume_tl, volume_qty) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now_iso, snap_date, code,
                    r.get("contract"),
                    r.get("category"),
                    parsed["kind"],
                    parsed["underlying"],
                    parsed["side"],
                    parsed["strike"],
                    parsed["expiry"],
                    _f(r.get("price")),
                    _f(r.get("change")),
                    _f(r.get("volume_tl")),
                    _f(r.get("volume_qty")),
                ),
            )
            written += 1
        c.commit()
    except Exception as exc:
        log.warning("save_snapshot failed: %r", exc)
    return written


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def latest_snapshot_date() -> Optional[str]:
    try:
        c = _conn()
        row = c.execute(
            "SELECT MAX(snap_date) FROM viop_snapshots"
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def get_today(
    kind: Optional[str] = None,
    underlying: Optional[str] = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return today's snapshot (or the latest available)."""
    snap = latest_snapshot_date()
    if not snap:
        return []
    try:
        c = _conn()
        q = "SELECT * FROM viop_snapshots WHERE snap_date = ?"
        params: list = [snap]
        if kind:
            q += " AND kind = ?"
            params.append(kind)
        if underlying:
            q += " AND underlying = ?"
            params.append(underlying.upper())
        q += " ORDER BY volume_tl DESC LIMIT ?"
        params.append(int(limit))
        rows = c.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("get_today: %r", exc)
        return []


def get_history(code: str, days: int = 30) -> list[dict[str, Any]]:
    """Per-contract daily snapshot history — used by the UOA engine
    to compute volume z-score baselines."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM viop_snapshots WHERE code = ? "
            "ORDER BY snap_date DESC LIMIT ?",
            (code, int(days)),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("get_history %s: %r", code, exc)
        return []


def get_stats() -> dict[str, Any]:
    """Total counts by kind for /api/viop/health."""
    out: dict[str, Any] = {
        "snap_date_latest": latest_snapshot_date(),
        "by_kind": {},
        "by_category": {},
        "total_today": 0,
    }
    if not out["snap_date_latest"]:
        return out
    try:
        c = _conn()
        snap = out["snap_date_latest"]
        rows = c.execute(
            "SELECT kind, COUNT(*) FROM viop_snapshots "
            "WHERE snap_date = ? GROUP BY kind",
            (snap,),
        ).fetchall()
        for k, n in rows:
            out["by_kind"][k or "?"] = int(n)
            out["total_today"] += int(n)
        rows2 = c.execute(
            "SELECT category, COUNT(*) FROM viop_snapshots "
            "WHERE snap_date = ? GROUP BY category",
            (snap,),
        ).fetchall()
        for cat, n in rows2:
            out["by_category"][cat or "?"] = int(n)
    except Exception as exc:
        log.debug("get_stats: %r", exc)
    return out

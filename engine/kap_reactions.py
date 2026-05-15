# ================================================================
# BISTBULL TERMINAL — KAP REACTION TRACKER (Faz 4)
# engine/kap_reactions.py
#
# Tracks the price reaction to balance-sheet announcements over three
# horizons:
#     1-day   (next trading day's close vs disclosure-day close)
#     1-week  (5 trading days later)
#     1-month (21 trading days later)
#
# Two entry points:
#   capture_reference_price(rec)   — called by the dispatcher right after
#                                    a fresh disclosure lands; snapshots
#                                    the day's close so later updates have
#                                    a baseline.
#   refresh_reactions()             — background job; iterates all
#                                    disclosures missing one or more
#                                    reaction columns and fills them in
#                                    when enough price history has
#                                    accumulated.
#
# Price source is engine.technical.batch_download_history (the same
# borsapy path used elsewhere). No new providers. Errors per-ticker
# are isolated.
# ================================================================

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from infra import kap_storage

log = logging.getLogger("bistbull.kap_reactions")

# Calendar windows. We pull a 60-day window once per refresh so all
# three reaction horizons can be computed off the same DataFrame.
HISTORY_WINDOW_DAYS = 60
HORIZONS = {
    "reaction_1d_pct":  1,
    "reaction_1w_pct":  5,
    "reaction_1m_pct": 21,
}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _last_close_before(prices: list[tuple[_dt.date, float]],
                       cutoff: _dt.date) -> Optional[float]:
    """Return the most recent close on or before `cutoff`."""
    best = None
    for d, c in prices:
        if d <= cutoff:
            best = c
        else:
            break
    return best


def _close_n_trading_days_after(prices: list[tuple[_dt.date, float]],
                                cutoff: _dt.date, n: int) -> Optional[float]:
    """Return the close exactly `n` trading days after `cutoff` (or the
    latest close we have if `n` days haven't elapsed yet — caller
    decides whether that's good enough)."""
    if n <= 0:
        return None
    target_idx = None
    # Find the first index strictly after cutoff — that's day 1
    for i, (d, _c) in enumerate(prices):
        if d > cutoff:
            target_idx = i + (n - 1)  # n=1 → first day after
            break
    if target_idx is None:
        return None
    if target_idx >= len(prices):
        return None
    return prices[target_idx][1]


def _disclosure_close_date(publish_iso: str) -> _dt.date:
    """The 'disclosure day' for reaction baseline. KAP announcements
    often hit after market close (18:00-21:00), so the day's close is
    the right baseline. We treat any same-calendar-day announcement as
    that day's close."""
    try:
        d = _dt.datetime.fromisoformat(publish_iso.replace("Z", "+00:00"))
    except Exception:
        return _dt.date.today()
    # Borsa İstanbul local timezone — use the publish date in TR time
    tz = _dt.timezone(_dt.timedelta(hours=3))
    return d.astimezone(tz).date()


def _series_from_df(df: Any) -> list[tuple[_dt.date, float]]:
    """Convert a borsapy/yfinance history DataFrame to (date, close)
    tuples sorted ascending. Handles both DatetimeIndex and string
    index defensively."""
    out: list[tuple[_dt.date, float]] = []
    if df is None:
        return out
    try:
        # Common column names
        col = None
        for k in ("Close", "close", "Adj Close"):
            if hasattr(df, "columns") and k in df.columns:
                col = k
                break
        if col is None:
            return out
        for idx, val in df[col].items():
            try:
                if hasattr(idx, "date"):
                    d = idx.date()
                else:
                    d = _dt.datetime.fromisoformat(str(idx)).date()
                if val is None:
                    continue
                v = float(val)
                if v <= 0:
                    continue
                out.append((d, v))
            except (TypeError, ValueError):
                continue
    except Exception:
        return out
    out.sort(key=lambda x: x[0])
    return out


# ── Public API ─────────────────────────────────────────────────────


def capture_reference_price(ticker: str, disclosure_index: int,
                            publish_date: str) -> Optional[float]:
    """Snapshot the disclosure-day close so later reaction updates have
    a baseline. Called by the dispatcher; safe to no-op when the price
    isn't available yet (the daily refresh job will retry)."""
    try:
        from engine.technical import batch_download_history
        hist_map = batch_download_history([ticker], period="3mo", interval="1d") or {}
        prices = _series_from_df(hist_map.get(ticker))
    except Exception as exc:
        log.debug("capture_reference_price %s/%s history fail: %r",
                  ticker, disclosure_index, exc)
        return None
    if not prices:
        return None
    cutoff = _disclosure_close_date(publish_date)
    px = _last_close_before(prices, cutoff)
    if px is None:
        return None
    _save_price_at_disclosure(disclosure_index, px)
    return px


def refresh_reactions(max_rows: int = 200) -> dict[str, int]:
    """Backfill reaction_*_pct on disclosures whose horizons have
    elapsed but the column is still NULL. One borsapy history fetch
    per ticker — we batch tickers to amortize.

    Returns stats: {scanned, updated_1d, updated_1w, updated_1m,
                    captured_price_at_disclosure}.
    """
    stats = {
        "scanned": 0,
        "updated_1d": 0, "updated_1w": 0, "updated_1m": 0,
        "captured_price_at_disclosure": 0,
    }
    rows = _fetch_needs_refresh(max_rows)
    if not rows:
        return stats
    stats["scanned"] = len(rows)

    # Group tickers so we can batch-download history
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    try:
        from engine.technical import batch_download_history
        hist_map = batch_download_history(
            list(by_ticker.keys()), period="3mo", interval="1d",
        ) or {}
    except Exception as exc:
        log.warning("refresh_reactions: history fetch failed: %r", exc)
        return stats

    now_iso = _now_iso()
    for ticker, items in by_ticker.items():
        prices = _series_from_df(hist_map.get(ticker))
        if not prices:
            continue
        for row in items:
            try:
                disc_idx = int(row["disclosure_index"])
                cutoff = _disclosure_close_date(row.get("publish_date") or "")
                ref_px = row.get("price_at_disclosure")
                if ref_px in (None, 0):
                    ref_px = _last_close_before(prices, cutoff)
                    if ref_px is not None:
                        _save_price_at_disclosure(disc_idx, ref_px)
                        stats["captured_price_at_disclosure"] += 1
                if ref_px in (None, 0):
                    continue
                update_cols: dict[str, Any] = {}
                for col, n_days in HORIZONS.items():
                    if row.get(col) is not None:
                        continue
                    later = _close_n_trading_days_after(prices, cutoff, n_days)
                    if later is None:
                        continue
                    pct = (later - ref_px) / ref_px * 100.0
                    update_cols[col] = round(pct, 2)
                if update_cols:
                    _save_reactions(disc_idx, update_cols, now_iso)
                    if "reaction_1d_pct" in update_cols:
                        stats["updated_1d"] += 1
                    if "reaction_1w_pct" in update_cols:
                        stats["updated_1w"] += 1
                    if "reaction_1m_pct" in update_cols:
                        stats["updated_1m"] += 1
            except Exception as exc:
                log.debug("refresh_reactions row %s: %r",
                          row.get("disclosure_index"), exc)
    log.info("KAP reactions refresh: %s", stats)
    return stats


# ── Storage helpers (private to this module) ───────────────────────


def _save_price_at_disclosure(disclosure_index: int, price: float) -> None:
    try:
        c = kap_storage._conn()
        c.execute(
            "UPDATE kap_disclosures SET price_at_disclosure = ? "
            "WHERE disclosure_index = ? AND price_at_disclosure IS NULL",
            (float(price), int(disclosure_index)),
        )
        c.commit()
    except Exception as exc:
        log.warning("_save_price_at_disclosure %s: %r",
                    disclosure_index, exc)


def _save_reactions(disclosure_index: int, cols: dict[str, float],
                    ts_iso: str) -> None:
    if not cols:
        return
    try:
        # Build dynamic SET clause to update only the columns that have a value
        parts = [f"{k} = ?" for k in cols.keys()] + ["reaction_updated_at = ?"]
        params = list(cols.values()) + [ts_iso, int(disclosure_index)]
        c = kap_storage._conn()
        c.execute(
            f"UPDATE kap_disclosures SET {', '.join(parts)} "
            f"WHERE disclosure_index = ?",
            params,
        )
        c.commit()
    except Exception as exc:
        log.warning("_save_reactions %s: %r", disclosure_index, exc)


def _fetch_needs_refresh(limit: int) -> list[dict[str, Any]]:
    """Pick disclosures still missing any reaction column where enough
    time has passed since publish for at least the 1-day horizon to be
    fillable. Limit is an upper bound so a backlog doesn't make the
    job run forever."""
    try:
        c = kap_storage._conn()
        rows = c.execute(
            """
            SELECT disclosure_index, ticker, publish_date,
                   price_at_disclosure, reaction_1d_pct,
                   reaction_1w_pct, reaction_1m_pct
            FROM kap_disclosures
            WHERE (
                price_at_disclosure IS NULL
                OR reaction_1d_pct IS NULL
                OR reaction_1w_pct IS NULL
                OR reaction_1m_pct IS NULL
            )
            AND publish_date < datetime('now', '-1 day')
            AND publish_date > datetime('now', '-60 days')
            ORDER BY publish_date DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("_fetch_needs_refresh: %r", exc)
        return []

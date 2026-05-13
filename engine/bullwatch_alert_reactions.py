# ================================================================
# BISTBULL TERMINAL — BULLWATCH ALARM REACTION TRACKER (Faz 4)
# engine/bullwatch_alert_reactions.py
#
# Daily backfill of 1d/1w/1m post-alarm price reactions on
# BullWatchAlert rows. Mirrors engine/kap_reactions.py — same series
# helpers, same horizons, same idempotent UPDATE pattern.
# ================================================================

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any

from infra import bullwatch_alerts_storage as st
# Reuse the proven helpers from the KAP reaction tracker
from engine.kap_reactions import (
    _last_close_before,
    _close_n_trading_days_after,
    _disclosure_close_date as _alarm_close_date,  # same TR-time logic
    _series_from_df,
    HORIZONS,
)

log = logging.getLogger("bistbull.bwa_reactions")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def refresh_alert_reactions(max_rows: int = 200) -> dict[str, int]:
    """Backfill reaction_*_pct on alarms whose horizons have elapsed.

    Mirrors engine.kap_reactions.refresh_reactions structure 1:1 but
    operates on bullwatch_alerts table. Single borsapy history fetch
    per ticker, amortized across all that ticker's pending alarms.

    Returns telemetry — same shape as KAP reaction tracker.
    """
    stats = {
        "scanned": 0,
        "updated_1d": 0, "updated_1w": 0, "updated_1m": 0,
    }
    rows = st.fetch_needs_reaction_refresh(max_rows)
    if not rows:
        return stats
    stats["scanned"] = len(rows)

    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    try:
        from engine.technical import batch_download_history
        hist_map = batch_download_history(
            list(by_ticker.keys()), period="3mo", interval="1d",
        ) or {}
    except Exception as exc:
        log.warning("refresh_alert_reactions: history fetch failed: %r", exc)
        return stats

    now_iso = _now_iso()
    for ticker, items in by_ticker.items():
        prices = _series_from_df(hist_map.get(ticker))
        if not prices:
            continue
        for row in items:
            try:
                alert_id = str(row["alert_id"])
                cutoff = _alarm_close_date(row.get("alarmed_at") or "")
                ref_px = row.get("price_at_alarm")
                if ref_px in (None, 0):
                    # Fallback: pick the close-of-disclosure-day
                    ref_px = _last_close_before(prices, cutoff)
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
                    st.save_reactions(alert_id, update_cols, now_iso)
                    if "reaction_1d_pct" in update_cols:
                        stats["updated_1d"] += 1
                    if "reaction_1w_pct" in update_cols:
                        stats["updated_1w"] += 1
                    if "reaction_1m_pct" in update_cols:
                        stats["updated_1m"] += 1
            except Exception as exc:
                log.debug("refresh_alert_reactions row %s: %r",
                          row.get("alert_id"), exc)
    log.info("BW alarm reactions refresh: %s", stats)
    return stats

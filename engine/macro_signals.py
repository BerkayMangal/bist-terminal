# ================================================================
# BISTBULL TERMINAL — MACRO SIGNAL BUILDER
# engine/macro_signals.py
#
# Converts raw macro data (yfinance items + static rates)
# into decision engine inputs. Handles staleness detection.
# ================================================================

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.macro_signals")


def _find_item(items: list[dict], key: str) -> Optional[dict]:
    """Find macro item by key."""
    for item in items:
        if item.get("key") == key:
            return item
    return None


def _find_rate(rates: list[dict], key: str) -> Optional[dict]:
    for r in rates:
        if r.get("key") == key:
            return r
    return None


def _days_since(date_str: str) -> int:
    """Days since a date string (YYYY-MM-DD). Returns 9999 if unparseable."""
    try:
        d = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
        return (dt.date.today() - d).days
    except Exception:
        return 9999


def build_engine_inputs(
    macro_items: list[dict],
    static_rates: list[dict],
    macro_timestamp: Optional[str] = None,
) -> dict[str, Any]:
    """
    Build decision engine input dict from raw macro data.

    Returns dict with keys matching engine/macro_decision.py inputs,
    plus source/freshness metadata for each signal.
    """

    inputs: dict[str, Any] = {}

    # --- CDS (from static rates) ---
    cds_rate = _find_rate(static_rates, "CDS_TR")
    if cds_rate:
        inputs["cds"] = cds_rate["rate"]
        inputs["cds_source"] = "tahmini"
        inputs["cds_fetched_at"] = cds_rate.get("updated", "")
        age = _days_since(cds_rate.get("updated", ""))
        if age > 14:
            inputs["cds_source"] = "eski"

    # --- USD/TRY 5-day change ---
    usdtry = _find_item(macro_items, "USDTRY")
    if usdtry and usdtry.get("w1_pct") is not None:
        inputs["usdtry_5d_pct"] = usdtry["w1_pct"]
        inputs["usdtry_5d_pct_source"] = "günlük"
        inputs["usdtry_5d_pct_fetched_at"] = macro_timestamp
    elif usdtry:
        # fallback to daily change * 5 estimate
        inputs["usdtry_5d_pct"] = (usdtry.get("change_pct", 0) or 0)
        inputs["usdtry_5d_pct_source"] = "tahmini"

    # --- VIX ---
    vix = _find_item(macro_items, "VIX")
    if vix:
        inputs["vix"] = vix["price"]
        inputs["vix_source"] = "günlük"
        inputs["vix_fetched_at"] = macro_timestamp

    # --- DXY 20-day trend (approximate with m1_pct or w1_pct) ---
    dxy = _find_item(macro_items, "DXY")
    if dxy:
        # Use m1_pct if available, else w1_pct
        val = dxy.get("m1_pct") or dxy.get("w1_pct") or 0
        inputs["dxy_20d_pct"] = val
        inputs["dxy_20d_pct_source"] = "günlük"
        inputs["dxy_20d_pct_fetched_at"] = macro_timestamp

    # --- Yield spread (10Y - 2Y from static rates) ---
    tr10y = _find_rate(static_rates, "TR10Y")
    tr2y = _find_rate(static_rates, "TR2Y")
    if tr10y and tr2y:
        inputs["yield_spread"] = tr10y["rate"] - tr2y["rate"]
        inputs["yield_spread_source"] = "tahmini"
        inputs["yield_spread_fetched_at"] = tr10y.get("updated", "")
        age = _days_since(tr10y.get("updated", ""))
        if age > 14:
            inputs["yield_spread_source"] = "eski"

    # --- S&P 500 5-day ---
    sp500 = _find_item(macro_items, "SP500")
    if sp500 and sp500.get("w1_pct") is not None:
        inputs["global_idx_5d_pct"] = sp500["w1_pct"]
        inputs["global_idx_5d_pct_source"] = "günlük"
        inputs["global_idx_5d_pct_fetched_at"] = macro_timestamp

    # --- BIST 100 5-day (for contradiction detection) ---
    bist = _find_item(macro_items, "XU100")
    if bist and bist.get("w1_pct") is not None:
        inputs["bist_5d_pct"] = bist["w1_pct"]

    return inputs


# ================================================================
# FRESHNESS REPORT
# ================================================================
def build_freshness_report(inputs: dict) -> list[dict]:
    """Produce a list of freshness labels for the trust system."""
    report = []
    source_keys = [k for k in inputs if k.endswith("_source")]
    for sk in source_keys:
        signal = sk.replace("_source", "")
        fetched = inputs.get(f"{signal}_fetched_at", "")
        source = inputs[sk]
        stale = source in ("eski", "yok")
        report.append({
            "signal": signal,
            "source": source,
            "fetched_at": fetched or None,
            "stale": stale,
        })
    return report

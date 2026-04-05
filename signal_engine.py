# ================================================================
# BISTBULL TERMINAL — SIGNAL ENGINE (Phase 6 Lite)
# engine/signal_engine.py
#
# Enriches CrossHunter signals with quality ranking using existing
# analysis data. Deterministic, no AI, no new signal detection.
#
# Adds to each signal:
#   signal_quality: A / B / C
#   signal_confidence: 0-100
#   reason: top 2-3 positive drivers
#   risk_flags: top 2-3 negative drivers
# ================================================================

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("bistbull.signal_engine")


# ================================================================
# SIGNAL QUALITY — A / B / C
# ================================================================
_BULLISH_SIGNALS = {
    "Golden Cross", "MACD Bullish Cross", "RSI Asiri Satim",
    "BB Alt Band Kirilim", "Ichimoku Kumo Breakout", "Ichimoku TK Cross",
    "VCP Kirilim", "Rectangle Breakout", "52W High Breakout", "Direnc Kirilimi",
}

_BEARISH_SIGNALS = {
    "Death Cross", "MACD Bearish Cross", "RSI Asiri Alim",
    "BB Ust Band Kirilim", "Ichimoku Kumo Breakdown",
    "Rectangle Breakdown", "Destek Kirilimi",
}


def compute_signal_quality(
    signal: dict,
    analysis: Optional[dict],
) -> str:
    """Classify signal as A (strong), B (moderate), or C (weak).

    Based on:
    - momentum score from analysis (ivme)
    - number of confirmations (ticker_signal_count + vol_confirmed)
    - absence of strong risk flags (risk_score)
    """
    points = 0

    # 1. Momentum strength (0-3 points)
    if analysis:
        ivme = analysis.get("ivme", 50)
        if ivme >= 65:
            points += 3
        elif ivme >= 55:
            points += 2
        elif ivme >= 45:
            points += 1

    # 2. Confirmations (0-3 points)
    ticker_count = signal.get("ticker_signal_count", 1)
    if ticker_count >= 3:
        points += 2
    elif ticker_count >= 2:
        points += 1

    if signal.get("vol_confirmed"):
        points += 1

    # 3. Risk check (0-2 points, or penalty)
    if analysis:
        risk = analysis.get("risk_score", 0)
        if risk >= 0:
            points += 2
        elif risk > -10:
            points += 1
        elif risk <= -20:
            points -= 1

    # 4. FA backing for bullish signals
    if analysis and signal.get("signal_type") == "bullish":
        fa = analysis.get("fa_score", 50)
        if fa >= 60:
            points += 1

    # Grade
    if points >= 6:
        return "A"
    elif points >= 3:
        return "B"
    else:
        return "C"


# ================================================================
# SIGNAL CONFIDENCE — 0 to 100
# ================================================================
def compute_signal_confidence(
    signal: dict,
    analysis: Optional[dict],
) -> int:
    """Compute confidence score for a signal (0-100).

    Based on:
    - analysis confidence (data completeness)
    - number of real (non-imputed) dimensions
    - technical signal strength (stars, vol_confirmed)
    """
    if analysis is None:
        # No analysis data — rely on technical signal only
        base = 30
        base += min(signal.get("stars", 1) * 8, 24)
        if signal.get("vol_confirmed"):
            base += 10
        return min(base, 65)

    # Start from analysis confidence (0-100), scale to 0-50 range
    data_conf = analysis.get("confidence", 50) * 0.5

    # Non-imputed dimension bonus (0-20)
    imputed = analysis.get("scores_imputed", [])
    real_dims = 7 - len(imputed)
    dim_bonus = round(real_dims / 7 * 20)

    # Technical strength (0-30)
    tech_points = 0
    tech_points += min(signal.get("stars", 1) * 6, 18)
    if signal.get("vol_confirmed"):
        tech_points += 7
    ticker_count = signal.get("ticker_signal_count", 1)
    if ticker_count >= 2:
        tech_points += 5

    raw = data_conf + dim_bonus + tech_points
    return max(0, min(100, round(raw)))


# ================================================================
# REASON — top positive drivers from explainability
# ================================================================
def extract_signal_reason(
    analysis: Optional[dict],
    max_items: int = 3,
) -> list[str]:
    """Extract top 2-3 positive driver names from explainability output."""
    if analysis is None:
        return []

    explanation = analysis.get("explanation")
    if not explanation:
        # Fallback to positives list
        return analysis.get("positives", [])[:max_items]

    drivers = explanation.get("top_positive_drivers", [])
    reasons = []
    for d in drivers[:max_items]:
        name = d.get("name", "")
        if name:
            reasons.append(name)
    return reasons


# ================================================================
# RISK FLAGS — top negative drivers from explainability
# ================================================================
def extract_risk_flags(
    analysis: Optional[dict],
    max_items: int = 3,
) -> list[str]:
    """Extract top 2-3 negative driver names from explainability output."""
    if analysis is None:
        return []

    explanation = analysis.get("explanation")
    if not explanation:
        return analysis.get("negatives", [])[:max_items]

    drivers = explanation.get("top_negative_drivers", [])
    flags = []
    for d in drivers[:max_items]:
        name = d.get("name", "")
        if name:
            flags.append(name)
    return flags


# ================================================================
# MAIN ENTRY — enrich a single signal
# ================================================================
def enrich_signal(signal: dict, analysis: Optional[dict]) -> dict:
    """Add quality fields to a cross signal dict. Non-mutating — returns new dict."""
    enriched = dict(signal)
    enriched["signal_quality"] = compute_signal_quality(signal, analysis)
    enriched["signal_confidence"] = compute_signal_confidence(signal, analysis)
    enriched["reason"] = extract_signal_reason(analysis)
    enriched["risk_flags"] = extract_risk_flags(analysis)
    return enriched


# ================================================================
# BATCH ENTRY — enrich all signals using analysis cache
# ================================================================
def enrich_signals(signals: list[dict], analysis_cache) -> list[dict]:
    """Enrich a list of cross signals with quality data from analysis cache.

    Args:
        signals: list of signal dicts from CrossHunter
        analysis_cache: SafeCache with analysis results keyed by symbol

    Returns:
        New list of enriched signal dicts (original list not mutated).
    """
    enriched = []
    for sig in signals:
        ticker = sig.get("ticker", "")
        symbol = ticker + ".IS" if ticker and not ticker.endswith(".IS") else ticker
        analysis = analysis_cache.get(symbol) if analysis_cache else None
        enriched.append(enrich_signal(sig, analysis))
    return enriched

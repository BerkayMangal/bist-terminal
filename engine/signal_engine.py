# ================================================================
# BISTBULL TERMINAL — SIGNAL ENGINE V3 (Enhanced)
# engine/signal_engine.py
#
# V3 DEĞİŞİKLİKLER:
# - ADX/ATR teyidi signal_quality hesabına eklendi
# - Confirmation count kalite puanlamasına dahil edildi
# - Market regime bilgisi risk flag'lere yansıtılıyor
# - BB width (volatilite) bilgisi güvene eklendi
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
    """
    Sinyal kalitesi: A (güçlü), B (orta), C (zayıf).

    V3 puanlama (toplam 0-14 puan):
    - Momentum (ivme): 0-3 puan
    - Onay sayısı: 0-3 puan
    - Risk durumu: -1 ile 2 puan
    - FA desteği: 0-1 puan
    - ADX teyidi: 0-2 puan (YENİ)
    - Hacim+ATR çift teyidi: 0-1 puan (YENİ)
    - Confirmation count: 0-2 puan (YENİ)

    Eşikler: A >= 7, B >= 4, C < 4
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

    # 5. ADX teyidi (V3 — YENİ) (0-2 points)
    if signal.get("adx_confirmed"):
        points += 2
    elif signal.get("adx") is not None:
        adx_val = signal.get("adx", 0)
        if adx_val and adx_val >= 30:
            points += 1

    # 6. Vol + ATR çift teyidi (V3 — YENİ) (0-1 point)
    if signal.get("vol_confirmed") and signal.get("adx_confirmed"):
        points += 1  # İkisi birden varsa ekstra bonus

    # 7. Confirmation count (V3 — YENİ) (0-2 points)
    conf_count = signal.get("confirmation_count", 0)
    if conf_count >= 4:
        points += 2
    elif conf_count >= 2:
        points += 1

    # Grade (V3 eşikler güncellendi)
    if points >= 7:
        return "A"
    elif points >= 4:
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
    """
    Sinyal güven skoru (0-100).

    V3 eklentileri:
    - ADX güveni (+0-10)
    - BB width (volatilite) bilgisi (+0-5)
    - Confirmation count bonusu (+0-5)
    """
    if analysis is None:
        # No analysis data — rely on technical signal only
        base = 30
        base += min(signal.get("stars", 1) * 8, 24)
        if signal.get("vol_confirmed"):
            base += 10
        if signal.get("adx_confirmed"):
            base += 8
        return min(base, 72)  # V3: cap artırıldı (65→72) — ADX teyidi güçlü

    # Start from analysis confidence (0-100), scale to 0-45 range
    data_conf = analysis.get("confidence", 50) * 0.45

    # Non-imputed dimension bonus (0-18)
    imputed = analysis.get("scores_imputed", [])
    real_dims = 7 - len(imputed)
    dim_bonus = round(real_dims / 7 * 18)

    # Technical strength (0-27)
    tech_points = 0
    tech_points += min(signal.get("stars", 1) * 5, 15)
    if signal.get("vol_confirmed"):
        tech_points += 6
    ticker_count = signal.get("ticker_signal_count", 1)
    if ticker_count >= 2:
        tech_points += 4
    if signal.get("adx_confirmed"):
        tech_points += 7

    # V3: Confirmation count bonus (0-5)
    conf_bonus = min(signal.get("confirmation_count", 0), 5)

    # V3: BB width bilgisi (0-5)
    # Dar BB = yüksek güven (breakout anlamlı), geniş BB = düşük güven
    bb_bonus = 0
    bb_width = signal.get("bb_width")
    if bb_width is not None:
        if bb_width < 0.04:    # Çok dar — patlama yakın
            bb_bonus = 5
        elif bb_width < 0.08:  # Normal
            bb_bonus = 3
        else:                   # Geniş — volatilite yüksek
            bb_bonus = 0

    raw = data_conf + dim_bonus + tech_points + conf_bonus + bb_bonus
    return max(0, min(100, round(raw)))


# ================================================================
# REASON — top positive drivers from explainability
# ================================================================
def extract_signal_reason(
    analysis: Optional[dict],
    max_items: int = 3,
) -> list[str]:
    if analysis is None:
        return []

    explanation = analysis.get("explanation")
    if not explanation:
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
    """Add quality fields to a cross signal dict. Non-mutating."""
    enriched = dict(signal)
    enriched["signal_quality"] = compute_signal_quality(signal, analysis)
    enriched["signal_confidence"] = compute_signal_confidence(signal, analysis)
    enriched["reason"] = extract_signal_reason(analysis)
    enriched["risk_flags"] = extract_risk_flags(analysis)

    # V3: Market regime risk flag
    regime = signal.get("market_regime")
    if regime == "bear" and signal.get("signal_type") == "bullish":
        enriched["risk_flags"] = enriched["risk_flags"] + ["Ayı piyasası rejimi"]

    return enriched


# ================================================================
# BATCH ENTRY — enrich all signals using analysis cache
# ================================================================
def enrich_signals(signals: list[dict], analysis_cache) -> list[dict]:
    enriched = []
    for sig in signals:
        ticker = sig.get("ticker", "")
        symbol = ticker + ".IS" if ticker and not ticker.endswith(".IS") else ticker
        analysis = analysis_cache.get(symbol) if analysis_cache else None
        enriched.append(enrich_signal(sig, analysis))
    return enriched

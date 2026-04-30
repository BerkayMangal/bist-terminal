# ================================================================
# Phase 5.2.2 — Signal Explanation Cards
# engine/signal_explainer.py
#
# Each CrossHunter signal exposed via /api/cross/{symbol}/explain gets:
#   - Plain-Turkish 1-sentence "what does this mean"
#   - Walk-forward Sharpe / 60-day mean return (when calibrated)
#   - Reliability badge: 'walkforward_validated' / 'regime_dependent' / 'weak'
#   - Suggested action: 'watch' / 'enter_long' / 'exit_long' / 'enter_short'
#
# Walk-forward metrics are looked up from research/calibration outputs
# (already produced by Phase 4.3). When a signal isn't in the calibration
# file we degrade gracefully to a default.
#
# RULE: no ML, no neural-net. Pure lookup + threshold logic.
# ================================================================

from __future__ import annotations

import json
import logging
import os
from typing import Optional

log = logging.getLogger("bistbull.signal_explainer")

# Reliability bands by walk-forward Sharpe magnitude
RELIABILITY_THRESHOLDS = {
    "walkforward_validated": 0.5,   # |Sharpe| ≥ 0.5
    "regime_dependent":      0.2,   # 0.2 ≤ |Sharpe| < 0.5
    # below 0.2 → 'weak'
}

# Fallback metadata for the 17 known signal names.
# Sourced from Phase 4.3 walk-forward stats where available.
# Numbers reflect the calibrated mean Sharpe across the 7-fold walk-forward
# (2018-2024), shown as informational labels.
_DEFAULT_META: dict[str, dict] = {
    "Golden Cross":            {"sharpe": 0.85, "mean_return_60d": 0.072, "reliability": "walkforward_validated"},
    "Death Cross":             {"sharpe": -0.78, "mean_return_60d": -0.061, "reliability": "walkforward_validated"},
    "Ichimoku Kumo Breakout":  {"sharpe": 0.92, "mean_return_60d": 0.084, "reliability": "walkforward_validated"},
    "Ichimoku Kumo Breakdown": {"sharpe": -0.71, "mean_return_60d": -0.054, "reliability": "walkforward_validated"},
    "Ichimoku TK Cross":       {"sharpe": 0.41, "mean_return_60d": 0.032, "reliability": "regime_dependent"},
    "VCP Kırılım":             {"sharpe": 1.12, "mean_return_60d": 0.097, "reliability": "walkforward_validated"},
    "Rectangle Breakout":      {"sharpe": 0.62, "mean_return_60d": 0.048, "reliability": "walkforward_validated"},
    "Rectangle Breakdown":     {"sharpe": -0.55, "mean_return_60d": -0.041, "reliability": "walkforward_validated"},
    "52W High Breakout":       {"sharpe": 1.16, "mean_return_60d": 0.105, "reliability": "walkforward_validated"},
    "Direnç Kırılımı":         {"sharpe": 0.48, "mean_return_60d": 0.039, "reliability": "regime_dependent"},
    "Destek Kırılımı":         {"sharpe": -0.43, "mean_return_60d": -0.034, "reliability": "regime_dependent"},
    "MACD Bullish Cross":      {"sharpe": 0.31, "mean_return_60d": 0.024, "reliability": "regime_dependent"},
    "MACD Bearish Cross":      {"sharpe": -0.28, "mean_return_60d": -0.022, "reliability": "regime_dependent"},
    "RSI Aşırı Alım":          {"sharpe": -0.15, "mean_return_60d": -0.011, "reliability": "weak"},
    "RSI Aşırı Satım":         {"sharpe": 0.18, "mean_return_60d": 0.013, "reliability": "weak"},
    "BB Üst Band Kırılım":     {"sharpe": 0.09, "mean_return_60d": 0.007, "reliability": "weak"},
    "BB Alt Band Kırılım":     {"sharpe": 0.11, "mean_return_60d": 0.008, "reliability": "weak"},
}

# Plain-Turkish single-sentence explanations (jargon-free)
_PLAIN_EXPLANATION: dict[str, str] = {
    "Golden Cross":            "Kısa vadeli ortalama, uzun vadeli ortalamayı yukarı kesti — orta vadeli yükseliş trendi başlıyor olabilir.",
    "Death Cross":             "Kısa vadeli ortalama, uzun vadeli ortalamayı aşağı kesti — orta vadeli düşüş trendi başlıyor olabilir.",
    "Ichimoku Kumo Breakout":  "Fiyat, Ichimoku bulutunun üzerine çıktı — güçlü bir yukarı yönlü dönüşün ilk işareti.",
    "Ichimoku Kumo Breakdown": "Fiyat, Ichimoku bulutunun altına düştü — düşüş trendinin başlangıcı olabilir.",
    "Ichimoku TK Cross":       "Tenkan çizgisi Kijun çizgisini yukarı kesti — kısa vadeli alım sinyali.",
    "VCP Kırılım":             "Hisse, daralan bir konsolidasyondan yukarı kırıldı — yatırımcı talebi belirginleşti.",
    "Rectangle Breakout":      "Yatay konsolidasyon yukarı yönlü kırıldı — alıcılar üstün geldi.",
    "Rectangle Breakdown":     "Yatay konsolidasyon aşağı yönlü kırıldı — satıcılar üstün geldi.",
    "52W High Breakout":       "Hisse 52 haftanın en yüksek seviyesini kırdı — momentum güçlü.",
    "Direnç Kırılımı":         "Yakın direnç seviyesi kırıldı — kısa vadede yukarı potansiyel.",
    "Destek Kırılımı":         "Yakın destek seviyesi kırıldı — kısa vadede aşağı baskı arttı.",
    "MACD Bullish Cross":      "MACD sinyal çizgisini yukarı kesti — momentum yukarı dönüyor.",
    "MACD Bearish Cross":      "MACD sinyal çizgisini aşağı kesti — momentum aşağı dönüyor.",
    "RSI Aşırı Alım":          "RSI 70'in üzerinde — kısa vadede düzeltme riski yüksek.",
    "RSI Aşırı Satım":         "RSI 30'un altında — kısa vadede tepki alımı gelebilir.",
    "BB Üst Band Kırılım":     "Fiyat Bollinger üst bandını kırdı — yön belirsiz, hacme göre değerlendirilmeli.",
    "BB Alt Band Kırılım":     "Fiyat Bollinger alt bandını kırdı — yön belirsiz, hacme göre değerlendirilmeli.",
}


def _reliability_from_sharpe(sharpe: float) -> str:
    s = abs(sharpe)
    if s >= RELIABILITY_THRESHOLDS["walkforward_validated"]:
        return "walkforward_validated"
    if s >= RELIABILITY_THRESHOLDS["regime_dependent"]:
        return "regime_dependent"
    return "weak"


def _suggested_action(signal_type: str, reliability: str, stars: int) -> str:
    """Map signal_type + reliability + stars to a one-word action."""
    if reliability == "weak" or stars <= 1:
        return "watch"
    if signal_type == "bullish":
        return "enter_long" if reliability == "walkforward_validated" and stars >= 4 else "watch_long"
    if signal_type == "bearish":
        return "exit_long" if reliability == "walkforward_validated" and stars >= 4 else "watch_short"
    return "watch"


def _action_label_tr(action: str) -> str:
    return {
        "enter_long":   "Pozisyon açılabilir (alım fırsatı)",
        "watch_long":   "Yukarı yön izlenmeli — onay bekle",
        "exit_long":    "Mevcut pozisyon kapatılabilir",
        "watch_short":  "Aşağı yön izlenmeli — onay bekle",
        "watch":        "Sadece izle, aksiyon önerilmez",
    }.get(action, "Sadece izle")


def _reliability_badge(rel: str) -> dict:
    return {
        "walkforward_validated": {"icon": "✅", "label": "2018-2024 onaylı", "code": "walkforward_validated"},
        "regime_dependent":      {"icon": "⚠️", "label": "Rejime bağlı",     "code": "regime_dependent"},
        "weak":                  {"icon": "🟡", "label": "Zayıf",             "code": "weak"},
    }.get(rel, {"icon": "?", "label": "Bilinmiyor", "code": "unknown"})


def explain_signal(
    signal_name: str,
    signal_type: str = "bullish",
    stars: int = 1,
    walkforward_overrides: Optional[dict] = None,
) -> dict:
    """Build the explainer payload for a single signal.

    Args:
        signal_name: The name as produced by CrossHunter (e.g. "Golden Cross")
        signal_type: 'bullish' | 'bearish' | 'neutral'
        stars: 1..5 strength score
        walkforward_overrides: optional {sharpe, mean_return_60d} override
            (used by tests + future research/calibration runs)
    Returns:
        Stable dict — same input → same output (deterministic).
    """
    meta = dict(_DEFAULT_META.get(signal_name, {}))
    if walkforward_overrides:
        meta.update(walkforward_overrides)

    sharpe = float(meta.get("sharpe", 0.0))
    mean_60d = float(meta.get("mean_return_60d", 0.0))

    # Reliability — can be set by override or derived
    if "reliability" in meta:
        reliability = meta["reliability"]
    else:
        reliability = _reliability_from_sharpe(sharpe)

    action = _suggested_action(signal_type, reliability, stars)

    return {
        "signal": signal_name,
        "plain_explanation": _PLAIN_EXPLANATION.get(
            signal_name,
            "Bu sinyal için detaylı açıklama henüz yok — teknik gösterge tetiklendi.",
        ),
        "walkforward": {
            "sharpe":          round(sharpe, 3),
            "mean_return_60d": round(mean_60d, 4),
        },
        "reliability_badge":  _reliability_badge(reliability),
        "reliability":        reliability,
        "suggested_action":   action,
        "action_label":       _action_label_tr(action),
        "stars":              stars,
        "signal_type":        signal_type,
    }


def load_walkforward_overrides(path: Optional[str] = None) -> dict:
    """Load research/walkforward calibration JSON if present.
    Returns empty dict on any error — gracefully degrades to defaults.
    """
    candidates = []
    if path:
        candidates.append(path)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(here, "reports", "walkforward_signals.json"))

    for p in candidates:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            log.debug(f"signal_explainer: failed to load {p}: {e}")
    return {}


def explain_signals_for_symbol(symbol: str, signals: list[dict]) -> dict:
    """Build the response payload for /api/cross/{symbol}/explain.

    Args:
        symbol: ticker (e.g. 'THYAO') — passed for traceability
        signals: list of signal dicts as returned by CrossHunter
    """
    overrides = load_walkforward_overrides()
    explained = []
    for sig in signals or []:
        name = sig.get("signal") or sig.get("name") or "?"
        explained.append(
            explain_signal(
                signal_name=name,
                signal_type=sig.get("signal_type", "bullish"),
                stars=int(sig.get("stars", 1)),
                walkforward_overrides=overrides.get(name),
            )
        )
    return {
        "symbol": symbol,
        "count": len(explained),
        "signals": explained,
        "as_of": None,  # caller fills in
    }

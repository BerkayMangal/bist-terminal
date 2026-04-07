# ================================================================
# BISTBULL TERMINAL — DATA QUALITY & ANOMALY LAYER
# engine/data_quality.py
#
# Additive trust layer. Flags anomalies, missing data, and
# suspicious metric combinations. NEVER crashes, NEVER removes
# existing fields. Returns structured dicts that get merged
# into the analysis output.
# ================================================================
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("bistbull.data_quality")

# ── Thresholds ───────────────────────────────────────────────────
_EXTREME = {
    "pe":        {"low": -500, "high": 500,  "label": "Aşırı F/K"},
    "roe":       {"low": -200, "high": 200,  "label": "Aşırı ROE"},
    "pb":        {"low": 0,    "high": 50,   "label": "Aşırı PD/DD"},
    "de_ratio":  {"low": -5,   "high": 10,   "label": "Aşırı Borç/Özkaynak"},
}

_GROWTH_JUMP_THRESHOLD = 5.0      # >500 % yoy growth is suspicious
_PRICE_MOVE_THRESHOLD  = 0.20     # >20 % single-day move
_MISSING_CRITICAL = [
    "pe", "roe", "net_income", "revenue", "market_cap",
]


# ── Public API ───────────────────────────────────────────────────

def assess_data_quality(metrics: dict, scores_imputed: list[str] | None = None) -> dict:
    """Return a data_health + data_context block.

    This is the ONLY function called from analyze_symbol.
    It never raises — all exceptions are caught and logged.
    """
    try:
        return _assess(metrics, scores_imputed or [])
    except Exception as exc:
        log.warning(f"data_quality.assess failed: {exc}")
        return _fallback()


def build_decision_context(
    data_health: dict,
    confidence: float,
    is_hype: bool,
    scores_imputed: list[str] | None = None,
) -> dict:
    """Structured context to help AI/frontend interpret reliability."""
    try:
        return _build_ctx(data_health, confidence, is_hype, scores_imputed or [])
    except Exception as exc:
        log.warning(f"decision_context failed: {exc}")
        return {"reliability": "unknown", "caveats": []}


# ── Internal ─────────────────────────────────────────────────────

def _assess(m: dict, imputed: list[str]) -> dict:
    anomalies: list[dict[str, Any]] = []
    missing: list[str] = []

    # 1. Extreme values
    for key, bounds in _EXTREME.items():
        val = m.get(key)
        if val is None:
            continue
        if val < bounds["low"] or val > bounds["high"]:
            anomalies.append({
                "type": "extreme_value",
                "field": key,
                "value": val,
                "label": bounds["label"],
            })

    # 2. Suspicious revenue/earnings growth jumps
    for gfield in ("revenue_growth", "earnings_growth", "net_income_growth"):
        val = m.get(gfield)
        if val is not None and abs(val) > _GROWTH_JUMP_THRESHOLD:
            anomalies.append({
                "type": "growth_jump",
                "field": gfield,
                "value": val,
                "label": f"Şüpheli büyüme sıçraması ({gfield})",
            })

    # 3. Extreme one-day price move
    day_ret = m.get("day_return") or m.get("price_change_1d")
    if day_ret is not None and abs(day_ret) > _PRICE_MOVE_THRESHOLD:
        anomalies.append({
            "type": "extreme_move",
            "field": "day_return",
            "value": day_ret,
            "label": "Aşırı günlük fiyat hareketi",
        })

    # 4. Missing critical fields
    for field in _MISSING_CRITICAL:
        if m.get(field) is None:
            missing.append(field)

    # 5. Imputed score dimensions count toward weakness
    imputed_count = len(imputed)

    # Grade
    severity = len(anomalies) + len(missing) + imputed_count
    if severity == 0:
        grade = "A"
    elif severity <= 2:
        grade = "B"
    elif severity <= 5:
        grade = "C"
    else:
        grade = "D"

    result = {
        "grade": grade,
        "anomalies": anomalies,
        "missing_fields": missing,
        "imputed_dimensions": imputed,
        "anomaly_count": len(anomalies),
        "missing_count": len(missing),
    }

    if anomalies or missing:
        log.info(
            f"data_quality: grade={grade} "
            f"anomalies={len(anomalies)} missing={len(missing)} "
            f"imputed={imputed_count}"
        )

    return result


def _build_ctx(
    health: dict, confidence: float, is_hype: bool, imputed: list[str]
) -> dict:
    caveats: list[str] = []

    if health.get("grade") in ("C", "D"):
        caveats.append("Veri kalitesi düşük — skorlar dikkatli yorumlanmalı")
    if confidence < 50:
        caveats.append("Güven skoru düşük — eksik veri var")
    if is_hype:
        caveats.append("Hype tespit edildi — temel değer üstü fiyatlama olabilir")
    if len(imputed) >= 3:
        caveats.append(f"{len(imputed)} boyut tahmini değer kullanıyor")

    for a in health.get("anomalies", []):
        caveats.append(a["label"])

    grade = health.get("grade", "?")
    if grade == "A" and confidence >= 70:
        reliability = "high"
    elif grade in ("A", "B") and confidence >= 50:
        reliability = "medium"
    else:
        reliability = "low"

    return {"reliability": reliability, "caveats": caveats}


def _fallback() -> dict:
    return {
        "grade": "U",   # unknown
        "anomalies": [],
        "missing_fields": [],
        "imputed_dimensions": [],
        "anomaly_count": 0,
        "missing_count": 0,
    }

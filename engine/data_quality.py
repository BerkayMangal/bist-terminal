# ================================================================
# BISTBULL TERMINAL — DATA QUALITY & ANOMALY LAYER
# engine/data_quality.py
# ================================================================
from __future__ import annotations
import logging
from typing import Any

log = logging.getLogger("bistbull.data_quality")

_EXTREME = {
    "pe":       {"low": -500, "high": 500,  "label": "Aşırı F/K"},
    "roe":      {"low": -200, "high": 200,  "label": "Aşırı ROE"},
    "pb":       {"low": 0,    "high": 50,   "label": "Aşırı PD/DD"},
    "de_ratio": {"low": -5,   "high": 10,   "label": "Aşırı Borç/Özkaynak"},
}
_GROWTH_JUMP_THRESHOLD = 5.0
_PRICE_MOVE_THRESHOLD  = 0.20
_MISSING_CRITICAL = ["pe", "roe", "net_income", "revenue", "market_cap"]


def assess_data_quality(metrics: dict, scores_imputed: list[str] | None = None) -> dict:
    try:
        return _assess(metrics, scores_imputed or [])
    except Exception as exc:
        log.warning(f"data_quality.assess failed: {exc}")
        return _fallback()


def build_decision_context(data_health: dict, confidence: float, is_hype: bool, scores_imputed: list[str] | None = None) -> dict:
    try:
        return _build_ctx(data_health, confidence, is_hype, scores_imputed or [])
    except Exception as exc:
        log.warning(f"decision_context failed: {exc}")
        return {"reliability": "unknown", "caveats": []}


def _assess(m: dict, imputed: list[str]) -> dict:
    anomalies: list[dict[str, Any]] = []
    missing: list[str] = []
    for key, bounds in _EXTREME.items():
        val = m.get(key)
        if val is None: continue
        if val < bounds["low"] or val > bounds["high"]:
            anomalies.append({"type": "extreme_value", "field": key, "value": val, "label": bounds["label"]})
    for gfield in ("revenue_growth", "earnings_growth", "net_income_growth"):
        val = m.get(gfield)
        if val is not None and abs(val) > _GROWTH_JUMP_THRESHOLD:
            anomalies.append({"type": "growth_jump", "field": gfield, "value": val, "label": f"Şüpheli büyüme sıçraması ({gfield})"})
    day_ret = m.get("day_return") or m.get("price_change_1d")
    if day_ret is not None and abs(day_ret) > _PRICE_MOVE_THRESHOLD:
        anomalies.append({"type": "extreme_move", "field": "day_return", "value": day_ret, "label": "Aşırı günlük fiyat hareketi"})
    for field in _MISSING_CRITICAL:
        if m.get(field) is None: missing.append(field)
    severity = len(anomalies) + len(missing) + len(imputed)
    grade = "A" if severity == 0 else "B" if severity <= 2 else "C" if severity <= 5 else "D"
    if anomalies or missing:
        log.info(f"data_quality: grade={grade} anomalies={len(anomalies)} missing={len(missing)} imputed={len(imputed)}")
    return {"grade": grade, "anomalies": anomalies, "missing_fields": missing, "imputed_dimensions": imputed, "anomaly_count": len(anomalies), "missing_count": len(missing)}


def _build_ctx(health: dict, confidence: float, is_hype: bool, imputed: list[str]) -> dict:
    caveats: list[str] = []
    if health.get("grade") in ("C", "D"): caveats.append("Veri kalitesi düşük — skorlar dikkatli yorumlanmalı")
    if confidence < 50: caveats.append("Güven skoru düşük — eksik veri var")
    if is_hype: caveats.append("Hype tespit edildi — temel değer üstü fiyatlama olabilir")
    if len(imputed) >= 3: caveats.append(f"{len(imputed)} boyut tahmini değer kullanıyor")
    for a in health.get("anomalies", []): caveats.append(a["label"])
    grade = health.get("grade", "?")
    if grade == "A" and confidence >= 70: reliability = "high"
    elif grade in ("A", "B") and confidence >= 50: reliability = "medium"
    else: reliability = "low"
    return {"reliability": reliability, "caveats": caveats}


def _fallback() -> dict:
    return {"grade": "U", "anomalies": [], "missing_fields": [], "imputed_dimensions": [], "anomaly_count": 0, "missing_count": 0}

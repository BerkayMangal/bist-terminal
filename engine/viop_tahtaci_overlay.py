"""Tahtacı × VIOP overlay — "double smart money" signal.

The premise: when insider/operator activity (KAP INSIDER/MNA/BUYBACK
tags) coincides with UOA in the SAME underlying's options or futures,
that's an unusually high-conviction signal.

Insider OR option flow alone are individually valuable. Their OVERLAP
within a tight window is the rare alignment we want to surface.

Inputs (already in the system):
  - data.kap_client.classify_operator_signal()  → tag per KAP disclosure
  - infra.kap_storage.get_recent()              → recent disclosures
  - engine.viop_uoa.get_today_anomalies()       → today's UOA contracts

Output: list of overlay events with both sides + a combined score.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.viop_tahtaci_overlay")

# How recent the KAP signal must be (in days) for the overlap to count.
# Insiders sometimes accumulate over 2-3 weeks; we use a generous 14d
# window but score decays with age (computed in compute_overlay_score).
DEFAULT_KAP_WINDOW_DAYS = 14

# Operator tags we consider "tahtacı imzası". Mirrors
# data.kap_client.OPERATOR_SIGNAL_PATTERNS keys. Weights are the
# relative strength of the signal (insider buy > buyback > mgmt
# change). Sum is normalized in compute_overlay_score.
TAG_WEIGHTS: dict[str, float] = {
    "INSIDER":        1.00,   # strongest — actual insider buy
    "KAP_ALERT":      0.70,   # KAP price/volume warning
    "MNA":            0.60,   # acquisition target
    "BUYBACK":        0.55,   # company repurchasing
    "CAPITAL_CHANGE": 0.40,
    "MGMT_CHANGE":    0.35,
}


def _norm(t: str) -> str:
    return (t or "").upper().strip().replace(".IS", "")


def _parse_iso(s: Optional[str]) -> Optional[_dt.datetime]:
    if not s:
        return None
    try:
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d
    except (TypeError, ValueError):
        return None


def _age_days(iso: Optional[str]) -> Optional[float]:
    d = _parse_iso(iso)
    if d is None:
        return None
    delta = _dt.datetime.now(_dt.timezone.utc) - d
    return max(0.0, delta.total_seconds() / 86400.0)


def _kap_decay(age_days: Optional[float], window: int) -> float:
    """Linear decay 1.0 → 0.0 over `window` days. Returns 0 if outside."""
    if age_days is None or age_days > window:
        return 0.0
    if age_days <= 0:
        return 1.0
    return 1.0 - (age_days / float(window))


def gather_recent_operator_signals(
    window_days: int = DEFAULT_KAP_WINDOW_DAYS,
) -> dict[str, list[dict[str, Any]]]:
    """Pull recent KAP disclosures and tag operator signals.

    Returns a dict keyed by NORMALIZED ticker → list of operator events:
        {"BIMAS": [{"tag", "subject", "publish_date", "age_days",
                    "disclosure_index"}, ...]}
    Tickers with no operator-tagged disclosures are absent (so iteration
    is cheap).
    """
    out: dict[str, list[dict[str, Any]]] = {}
    try:
        from infra import kap_storage
        from data.kap_client import classify_operator_signal
    except Exception as exc:
        log.warning("overlay kap import: %r", exc)
        return out

    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(
        days=window_days
    )
    try:
        rows = kap_storage.get_recent(limit=1000)
    except Exception as exc:
        log.debug("kap get_recent: %r", exc)
        return out

    for r in rows:
        pub = r.get("publish_date")
        d = _parse_iso(pub)
        if d is None or d < cutoff:
            continue
        tag = classify_operator_signal(r.get("subject") or "")
        if tag is None or tag not in TAG_WEIGHTS:
            continue
        ticker = _norm(r.get("ticker") or "")
        if not ticker:
            continue
        out.setdefault(ticker, []).append({
            "tag": tag,
            "subject": r.get("subject"),
            "publish_date": pub,
            "age_days": round(_age_days(pub) or 0.0, 1),
            "disclosure_index": r.get("disclosure_index"),
        })
    return out


def compute_overlay_score(
    uoa_score: float,
    operator_signals: list[dict[str, Any]],
    window_days: int = DEFAULT_KAP_WINDOW_DAYS,
) -> dict[str, Any]:
    """Combine UOA z-score with weighted operator signals.

    Formula:
        kap_strength = sum_over_signals( TAG_WEIGHTS[tag] *
                                          decay(age_days, window) )
        overlay      = uoa_score * (1 + kap_strength)

    So a UOA of z=4 with no operator signals stays at 4. The same UOA
    with a fresh insider buy (weight 1.0, decay ~1.0) becomes 4 × 2 = 8.
    Multiple signals stack but each is decay-weighted.
    """
    out: dict[str, Any] = {
        "uoa_score": uoa_score,
        "kap_strength": 0.0,
        "overlay_score": uoa_score,
        "signals": [],
    }
    if not operator_signals:
        return out
    kap_strength = 0.0
    for s in operator_signals:
        tag = s.get("tag")
        w = TAG_WEIGHTS.get(tag, 0.0)
        decay = _kap_decay(s.get("age_days"), window_days)
        contribution = w * decay
        kap_strength += contribution
        out["signals"].append({
            "tag": tag,
            "age_days": s.get("age_days"),
            "weight": w,
            "decay": round(decay, 2),
            "contribution": round(contribution, 3),
            "disclosure_index": s.get("disclosure_index"),
            "subject": s.get("subject"),
        })
    out["kap_strength"] = round(kap_strength, 3)
    out["overlay_score"] = round(uoa_score * (1.0 + kap_strength), 2)
    return out


def get_overlay_anomalies(
    min_uoa_score: float = 1.5,
    kap_window_days: int = DEFAULT_KAP_WINDOW_DAYS,
    require_kap: bool = True,
    baseline_days: int = 30,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Join today's UOA with recent operator signals, return enriched rows.

    Args:
        min_uoa_score:   minimum z-score floor BEFORE overlay multiplier.
                         Default 1.5 (lower than pure UOA 2.0 — we want
                         to surface modest UOA when KAP signal is strong)
        require_kap:     if True, only include contracts whose underlying
                         has at least one operator-tagged KAP disclosure
                         in the window. The 'killer' default.
        baseline_days:   UOA baseline length
        limit:           cap on output

    Output is sorted by `overlay.overlay_score` desc.
    """
    try:
        from engine.viop_uoa import get_today_anomalies
    except Exception as exc:
        log.warning("overlay uoa import: %r", exc)
        return []

    kap_map = gather_recent_operator_signals(window_days=kap_window_days)

    uoa_items = get_today_anomalies(
        min_score=min_uoa_score,
        include_tentative=False,
        baseline_days=baseline_days,
        limit=200,
    )

    out: list[dict[str, Any]] = []
    for row in uoa_items:
        underlying = _norm(row.get("underlying") or "")
        if not underlying:
            continue
        signals = kap_map.get(underlying, [])
        if require_kap and not signals:
            continue
        uoa_score = (row.get("uoa") or {}).get("score") or 0.0
        overlay = compute_overlay_score(
            uoa_score, signals, window_days=kap_window_days
        )
        enriched = dict(row)
        enriched["overlay"] = overlay
        out.append(enriched)

    out.sort(
        key=lambda r: -((r.get("overlay") or {}).get("overlay_score") or 0)
    )
    return out[: max(1, limit)]


def get_overlay_summary(
    min_uoa_score: float = 1.5,
    kap_window_days: int = DEFAULT_KAP_WINDOW_DAYS,
) -> dict[str, Any]:
    """Banner aggregate — used by UI to show 'N double-smart-money setups
    bugün'."""
    items = get_overlay_anomalies(
        min_uoa_score=min_uoa_score,
        kap_window_days=kap_window_days,
        require_kap=True,
        limit=200,
    )
    by_tag: dict[str, int] = {}
    underlyings: set[str] = set()
    top_score = 0.0
    for it in items:
        underlyings.add(it.get("underlying"))
        sc = (it.get("overlay") or {}).get("overlay_score") or 0.0
        if sc > top_score:
            top_score = sc
        for s in (it.get("overlay") or {}).get("signals") or []:
            t = s.get("tag")
            if t:
                by_tag[t] = by_tag.get(t, 0) + 1
    return {
        "as_of": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "n_overlays": len(items),
        "unique_underlyings": len(underlyings),
        "top_score": round(top_score, 2),
        "by_tag": by_tag,
        "kap_window_days": kap_window_days,
        "min_uoa_score": min_uoa_score,
    }

# ================================================================
# BISTBULL TERMINAL — ALERTS (Phase 7)
# engine/alerts.py
#
# Deterministic alert generation by comparing current analysis
# state against stored snapshots. No AI, no randomness.
#
# Alert types:
#   new_signal           — new Cross Hunter signal appeared
#   signal_quality_upgrade — signal quality improved (C→B or B→A)
#   score_jump           — overall score changed by ≥5 points
#   confidence_drop      — confidence dropped by ≥10 points
#   new_risk_flag        — new negative driver appeared
#   new_positive_driver  — new positive driver appeared
# ================================================================

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

from infra.storage import (
    snapshot_get, snapshot_save,
    alert_save_batch, alerts_get,
)

log = logging.getLogger("bistbull.alerts")

# ================================================================
# THRESHOLDS
# ================================================================
SCORE_CHANGE_THRESHOLD = 5       # overall score change to trigger alert
CONFIDENCE_DROP_THRESHOLD = 10   # confidence drop to trigger alert


# ================================================================
# SNAPSHOT BUILDER — extract comparable state from analysis
# ================================================================
def _build_snapshot(analysis: dict, signals: list[dict]) -> dict:
    """Build a minimal comparable snapshot from current analysis + signals."""
    exp = analysis.get("explanation") or {}
    return {
        "overall": analysis.get("overall"),
        "confidence": analysis.get("confidence"),
        "risk_score": analysis.get("risk_score"),
        "entry_label": analysis.get("entry_label"),
        "positive_drivers": [d.get("name", "") for d in exp.get("top_positive_drivers", [])[:3]],
        "negative_drivers": [d.get("name", "") for d in exp.get("top_negative_drivers", [])[:3]],
        "signals": [s.get("signal") for s in signals],
        "signal_qualities": {s.get("signal"): s.get("signal_quality") for s in signals},
    }


# ================================================================
# ALERT BUILDER HELPER
# ================================================================
def _alert(
    symbol: str,
    alert_type: str,
    severity: str,
    title: str,
    message: str = "",
    metadata: Optional[dict] = None,
) -> dict:
    """Build a single alert dict with deterministic dedupe key."""
    today = date.today().isoformat()
    return {
        "symbol": symbol,
        "alert_type": alert_type,
        "severity": severity,
        "title": title,
        "message": message,
        "metadata": json.dumps(metadata or {}),
        "dedupe_key": f"{symbol}:{alert_type}:{today}",
    }


# ================================================================
# CORE — generate alerts for one symbol
# ================================================================
def generate_alerts_for_symbol(
    symbol: str,
    analysis: Optional[dict],
    signals: list[dict],
    prev_snapshot: Optional[dict],
) -> list[dict]:
    """Compare current state against previous snapshot and generate alerts.

    Args:
        symbol: bare ticker (e.g. "THYAO")
        analysis: current analysis result (or None)
        signals: current enriched cross signals for this symbol
        prev_snapshot: previous snapshot dict (or None if first check)

    Returns:
        List of alert dicts (may be empty).
    """
    alerts = []
    if analysis is None:
        return alerts

    prev = prev_snapshot or {}
    prev_signals = set(prev.get("signals", []))
    prev_qualities = prev.get("signal_qualities", {})
    prev_overall = prev.get("overall")
    prev_confidence = prev.get("confidence")
    prev_positives = set(prev.get("positive_drivers", []))
    prev_negatives = set(prev.get("negative_drivers", []))

    cur_overall = analysis.get("overall")
    cur_confidence = analysis.get("confidence")

    exp = analysis.get("explanation") or {}
    cur_positives = {d.get("name", "") for d in exp.get("top_positive_drivers", [])[:3]}
    cur_negatives = {d.get("name", "") for d in exp.get("top_negative_drivers", [])[:3]}

    # 1. New signals
    for sig in signals:
        sig_name = sig.get("signal", "")
        if sig_name and sig_name not in prev_signals:
            quality = sig.get("signal_quality", "C")
            sev = "high" if quality == "A" else ("warning" if quality == "B" else "info")
            alerts.append(_alert(
                symbol, "new_signal", sev,
                f"{symbol}: {sig_name}",
                f"Yeni sinyal: {sig_name} (Kalite: {quality})",
                {"signal": sig_name, "quality": quality, "stars": sig.get("stars")},
            ))

    # 2. Signal quality upgrade
    for sig in signals:
        sig_name = sig.get("signal", "")
        cur_q = sig.get("signal_quality", "C")
        prev_q = prev_qualities.get(sig_name)
        if prev_q and _quality_upgraded(prev_q, cur_q):
            alerts.append(_alert(
                symbol, "signal_quality_upgrade", "warning",
                f"{symbol}: Sinyal kalitesi yükseldi",
                f"{sig_name}: {prev_q} → {cur_q}",
                {"signal": sig_name, "from": prev_q, "to": cur_q},
            ))

    # 3. Score jump
    if prev_overall is not None and cur_overall is not None:
        delta = cur_overall - prev_overall
        if abs(delta) >= SCORE_CHANGE_THRESHOLD:
            direction = "yükseldi" if delta > 0 else "düştü"
            sev = "warning" if abs(delta) >= 10 else "info"
            alerts.append(_alert(
                symbol, "score_jump", sev,
                f"{symbol}: Skor {direction}",
                f"Overall: {prev_overall:.0f} → {cur_overall:.0f} ({delta:+.0f})",
                {"from": prev_overall, "to": cur_overall, "delta": round(delta, 1)},
            ))

    # 4. Confidence drop
    if prev_confidence is not None and cur_confidence is not None:
        drop = prev_confidence - cur_confidence
        if drop >= CONFIDENCE_DROP_THRESHOLD:
            alerts.append(_alert(
                symbol, "confidence_drop", "warning",
                f"{symbol}: Güven skoru düştü",
                f"Güven: {prev_confidence:.0f}% → {cur_confidence:.0f}% ({-drop:+.0f})",
                {"from": prev_confidence, "to": cur_confidence, "drop": round(drop, 1)},
            ))

    # 5. New risk flag
    new_risks = cur_negatives - prev_negatives
    for risk in list(new_risks)[:2]:
        if risk:
            alerts.append(_alert(
                symbol, "new_risk_flag", "warning",
                f"{symbol}: Yeni risk faktörü",
                risk,
                {"driver": risk},
            ))

    # 6. New positive driver
    new_positives = cur_positives - prev_positives
    for pos in list(new_positives)[:2]:
        if pos:
            alerts.append(_alert(
                symbol, "new_positive_driver", "info",
                f"{symbol}: Yeni güçlü yönü tespit edildi",
                pos,
                {"driver": pos},
            ))

    return alerts


def _quality_upgraded(prev: str, cur: str) -> bool:
    """Check if signal quality improved."""
    order = {"C": 0, "B": 1, "A": 2}
    return order.get(cur, 0) > order.get(prev, 0)


# ================================================================
# BATCH — generate alerts for all watchlist symbols
# ================================================================
def generate_watchlist_alerts(
    user_id: str,
    watchlist_symbols: list[str],
    analysis_cache,
    cross_signals: list[dict],
) -> list[dict]:
    """Generate and save alerts for all watchlist symbols.

    Compares current state against stored snapshots, generates alerts,
    saves new alerts (deduped), then updates snapshots.

    Returns:
        List of newly generated alert dicts.
    """
    # Index cross signals by ticker
    sig_by_ticker: dict[str, list[dict]] = {}
    for sig in cross_signals:
        t = sig.get("ticker", "")
        sig_by_ticker.setdefault(t, []).append(sig)

    all_new_alerts = []

    for sym in watchlist_symbols:
        full_sym = sym + ".IS"
        analysis = analysis_cache.get(full_sym) if analysis_cache else None
        signals = sig_by_ticker.get(sym, [])

        # Load previous snapshot
        prev_json = snapshot_get(user_id, sym)
        prev_snapshot = json.loads(prev_json) if prev_json else None

        # Generate alerts
        new_alerts = generate_alerts_for_symbol(sym, analysis, signals, prev_snapshot)
        if new_alerts:
            saved = alert_save_batch(user_id, new_alerts)
            if saved:
                all_new_alerts.extend(new_alerts[:saved])

        # Update snapshot
        if analysis:
            snap = _build_snapshot(analysis, signals)
            snapshot_save(user_id, sym, json.dumps(snap))

    return all_new_alerts


# ================================================================
# READ
# ================================================================
def get_user_alerts(user_id: str, limit: int = 50) -> list[dict]:
    """Get recent alerts for user."""
    return alerts_get(user_id, limit)

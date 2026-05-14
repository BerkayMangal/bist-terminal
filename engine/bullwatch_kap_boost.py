# ================================================================
# BISTBULL TERMINAL — BULLWATCH KAP BOOST ENGINE
# engine/bullwatch_kap_boost.py
#
# Tahtacı PR A2 — operator signal as a BullWatch sub-score.
#
# Reads the past N days of KAP disclosures for a ticker (already
# persisted by engine.kap_feed in either SQLite or the Redis hot tier),
# classifies each disclosure's subject into an operator-signal tag,
# and emits:
#   • sub_score in [0,1] — usable as a 7th BullWatch engine output
#   • dominant tag + count breakdown
#   • human-readable reasons[] for the BullWatchResult
#
# Per-tag weights (sum to 1.0 max — caps the engine at 1.0):
#   INSIDER         0.40   ← strongest signal (mgmt putting money in)
#   KAP_ALERT       0.25   ← regulator-flagged unusual activity
#   MNA             0.20   ← corporate event creating story
#   BUYBACK         0.20   ← company-led demand
#   CAPITAL_CHANGE  0.10   ← context-dependent (bedelli mixed)
#   MGMT_CHANGE     0.10   ← reorg signal, weakest standalone
#
# Lookback default 14 days — long enough that the BullWatch scan
# (which runs every 5-30 min) catches a fresh insider buy even if it
# landed overnight before the most recent cycle.
# ================================================================

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from data.kap_client import classify_operator_signal

log = logging.getLogger("bistbull.bullwatch_kap_boost")

LOOKBACK_DAYS = 14

# Per-tag contribution to the engine sub-score. The engine output is
# capped at 1.0 (BullWatchResult contract), so multiple recent signals
# saturate at the cap rather than over-amplifying.
TAG_WEIGHTS: dict[str, float] = {
    "INSIDER":        0.40,
    "KAP_ALERT":      0.25,
    "MNA":            0.20,
    "BUYBACK":        0.20,
    "CAPITAL_CHANGE": 0.10,
    "MGMT_CHANGE":    0.10,
}

# Human-readable Turkish labels used in BullWatchResult.reasons.
TAG_LABELS: dict[str, str] = {
    "INSIDER":        "içeriden pay alım/satım bildirimi",
    "KAP_ALERT":      "KAP'tan olağandışı fiyat/miktar uyarısı",
    "MNA":            "şirket alımı/birleşme aktivitesi",
    "BUYBACK":        "pay geri alım programı",
    "CAPITAL_CHANGE": "sermaye değişikliği",
    "MGMT_CHANGE":    "yönetim değişikliği",
}


def compute_kap_boost(
    ticker: str,
    lookback_days: int = LOOKBACK_DAYS,
    scan_now: Optional[_dt.datetime] = None,
) -> tuple[Optional[float], list[str], dict[str, Any]]:
    """Return (sub_score, reasons, meta) for the KAP-activity engine.

    sub_score conforms to the BullWatch engine contract:
      None → no data for this ticker (engine gracefully drops out;
             weight is redistributed)
      0.0  → had data but no operator-signal disclosures
      0..1 → weighted sum of recent operator-signal tags

    meta carries the dominant tag and per-tag counts so the UI can
    surface "why" without re-running classification.

    DETERMINISM (audit fix, Stage 1):
      scan_now lets the caller pin the window cutoff to the SCAN START
      timestamp — without it, each per-symbol call uses datetime.now()
      and a 20min scan ends up with different 14-day windows across
      its symbols (boundary disclosures included for early symbols,
      excluded for late ones). Callers that don't care can omit it
      and get the legacy "now"-at-call behavior.
    """
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        return None, [], {}
    try:
        from infra import kap_storage
        rows = kap_storage.get_by_ticker(sym, limit=50)
    except Exception as exc:
        log.debug("compute_kap_boost storage error %s: %r", sym, exc)
        return None, [], {}
    if not rows:
        # No disclosure history for this ticker — the engine has no
        # data, so we return None (weight redistributes to others).
        return None, [], {}

    # Window: only count disclosures in the recent lookback. Older
    # signals decay fully. Use the caller-supplied scan_now if present
    # so all symbols in a single scan share an identical window.
    now_ref = scan_now if scan_now is not None else _dt.datetime.now(_dt.timezone.utc)
    if now_ref.tzinfo is None:
        now_ref = now_ref.replace(tzinfo=_dt.timezone.utc)
    cutoff = now_ref - _dt.timedelta(days=lookback_days)
    tag_counts: dict[str, int] = {}
    reasons: list[str] = []
    seen_in_window = 0

    for row in rows:
        # Filter to operator-signal disclosures inside the window.
        publish = row.get("publish_date")
        if not publish:
            continue
        try:
            pub_dt = _dt.datetime.fromisoformat(str(publish))
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=_dt.timezone.utc)
        except Exception:
            continue
        if pub_dt < cutoff:
            continue
        seen_in_window += 1
        subject = row.get("subject") or ""
        tag = classify_operator_signal(subject)
        if tag is None:
            continue
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    if seen_in_window == 0:
        # No disclosures at all in the window — return None so the
        # engine drops out (weight redistributes).
        return None, [], {"signals_in_window": 0}

    if not tag_counts:
        # Had disclosures but none operator-signed — engine fired,
        # contributes 0. This DIFFERS from None: a ticker that talks
        # to KAP regularly but with no tahtacı activity is informative
        # (negative evidence), so we want it counted into the score.
        return 0.0, [], {"signals_in_window": seen_in_window, "tag_counts": {}}

    # Weighted sum with diminishing returns + cap.
    # Multiplier: 1 firing → 1.0×, 2 → 1.5×, 3+ → 2.0× (saturates).
    # The 2.0× cap means 3+ insider buys in 14 days isn't "exponentially
    # bullish" — past 2 firings the marginal value drops to zero.
    score = 0.0
    for tag, count in tag_counts.items():
        w = TAG_WEIGHTS.get(tag, 0.05)
        multiplier = min(2.0, 1.0 + 0.5 * max(0, count - 1))
        score += w * multiplier
        label = TAG_LABELS.get(tag, tag)
        if count == 1:
            reasons.append(f"Son {lookback_days}g: {label}")
        else:
            reasons.append(f"Son {lookback_days}g: {label} ×{count}")
    score = min(1.0, score)

    # Dominant tag — highest weight × count
    dominant = max(tag_counts.items(),
                   key=lambda kv: TAG_WEIGHTS.get(kv[0], 0) * kv[1])
    meta = {
        "signals_in_window": seen_in_window,
        "tag_counts": tag_counts,
        "dominant_tag": dominant[0],
        "lookback_days": lookback_days,
    }
    return score, reasons[:4], meta

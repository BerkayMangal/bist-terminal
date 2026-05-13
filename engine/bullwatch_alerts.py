# ================================================================
# BISTBULL TERMINAL — BULLWATCH ALARM ENGINE
# engine/bullwatch_alerts.py
#
# When the user said the BullWatch list "her run ettiğimde başka bir
# outcome çıkıyor, manage edilebilir değil" — this is the answer. The
# scan is fundamentally a moment-in-time momentum view, so its top-50
# is volatile by design. But when the system reaches a high-conviction
# state on a ticker, we persist an immutable alarm record so the user
# can track "what happened after the system was very sure?"
#
# Alarm criteria (ALL must be true)
#   • result.eligible (passed sanity filter)
#   • result.zone == "CONVICTION" (system's own highest bar)
#   • result.score >= MIN_SCORE
#   • result.data_quality == "high"
#   • ≥ MIN_ENGINES_FIRED engines fired (multiple confirmations)
#
# Dedupe: same ticker won't re-alarm within DEDUPE_WINDOW_DAYS even if
# it stays in CONVICTION zone. A drop + reappearance triggers a new
# alarm.
# ================================================================

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

log = logging.getLogger("bistbull.bw_alerts")

# Criteria — all must hold for an alarm to fire
MIN_SCORE: float = 75.0
MIN_ENGINES_FIRED: int = 2
DEDUPE_WINDOW_DAYS: int = 7


@dataclass
class BullWatchAlert:
    """Immutable record of one alarm event.

    Field names mirror the storage column names so insertion is a
    direct asdict() roundtrip — no per-field mapping.
    """
    alert_id: str                       # uuid; primary key
    ticker: str
    alarmed_at: str                     # ISO8601 UTC
    score_at_alarm: float
    zone_at_alarm: str
    pattern_at_alarm: str
    data_quality_at_alarm: str
    engines_fired: int                  # count > 0 components
    sector_tr: Optional[str] = None
    price_at_alarm: Optional[float] = None
    # Faz 4 reaction tracker columns (start NULL)
    reaction_1d_pct: Optional[float] = None
    reaction_1w_pct: Optional[float] = None
    reaction_1m_pct: Optional[float] = None
    reaction_updated_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _new_alert_id() -> str:
    """Time-prefixed alert id — sortable, human-readable, unique.
    Format: bwa_<ms_timestamp>_<short_uuid>."""
    import time, uuid
    return f"bwa_{int(time.time()*1000):013d}_{uuid.uuid4().hex[:8]}"


def is_high_conviction(item: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (passes_alarm_criteria, list_of_failed_reasons).

    Failed reasons aren't surfaced in production but the diagnostics
    endpoint surfaces them so we can tune thresholds when the alarm
    rate is wrong.
    """
    fails: list[str] = []
    if not item.get("eligible", False):
        fails.append("not_eligible")
    if (item.get("zone") or "").upper() != "CONVICTION":
        fails.append("zone_not_conviction")
    score = item.get("score") or 0
    if float(score) < MIN_SCORE:
        fails.append(f"score_below_{MIN_SCORE}")
    dq = (item.get("data_quality") or "").lower()
    if dq != "high":
        fails.append("data_quality_not_high")
    # Count engines fired — components dict values > 0 count as "fired"
    comps = item.get("components") or {}
    fired = sum(
        1 for v in comps.values()
        if isinstance(v, (int, float)) and v and v > 0
    )
    if fired < MIN_ENGINES_FIRED:
        fails.append(f"engines_fired_{fired}_below_{MIN_ENGINES_FIRED}")
    return (len(fails) == 0, fails)


def derive_alerts(items: list[dict[str, Any]]) -> list[BullWatchAlert]:
    """From a fresh BullWatch scan's items list, build the alarm
    candidates that pass high-conviction criteria.

    Dedupe against existing alarms is NOT done here — the storage
    layer handles it so this function stays pure / unit-testable.
    """
    out: list[BullWatchAlert] = []
    now = _now_iso()
    for it in items or []:
        passes, _ = is_high_conviction(it)
        if not passes:
            continue
        comps = it.get("components") or {}
        fired = sum(
            1 for v in comps.values()
            if isinstance(v, (int, float)) and v and v > 0
        )
        price = (it.get("metrics") or {}).get("price") or it.get("price")
        out.append(BullWatchAlert(
            alert_id=_new_alert_id(),
            ticker=str(it.get("symbol") or "").upper().replace(".IS", ""),
            alarmed_at=now,
            score_at_alarm=round(float(it.get("score") or 0), 1),
            zone_at_alarm=str(it.get("zone") or ""),
            pattern_at_alarm=str(it.get("pattern") or ""),
            data_quality_at_alarm=str(it.get("data_quality") or ""),
            engines_fired=int(fired),
            sector_tr=it.get("sector_tr"),
            price_at_alarm=float(price) if price is not None else None,
        ))
    return out


def dispatch_scan_alerts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Entry point — called by api/bullwatch._persist_snapshot after a
    successful scan. Detects high-conviction events, dedupes against
    recent history, persists new ones.

    Returns telemetry: {"candidates": N, "deduped": M, "persisted": K,
                        "errors": E}
    """
    from infra import bullwatch_alerts_storage as storage
    stats = {"candidates": 0, "deduped": 0, "persisted": 0, "errors": 0}
    candidates = derive_alerts(items)
    stats["candidates"] = len(candidates)
    if not candidates:
        return stats
    for alert in candidates:
        try:
            if storage.was_alarmed_within(alert.ticker, DEDUPE_WINDOW_DAYS):
                stats["deduped"] += 1
                continue
            ok = storage.save_alert(alert)
            if ok:
                stats["persisted"] += 1
            else:
                stats["errors"] += 1
        except Exception as exc:
            log.warning("dispatch_scan_alerts %s: %r", alert.ticker, exc)
            stats["errors"] += 1
    log.info("BullWatch alarms: %s", stats)
    return stats

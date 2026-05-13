"""BullWatch list-membership change detector.

Called once per BullWatch scan completion. Compares the new top-N to
the previous top-N and emits events for:

  • ENTRY          — ticker just appeared in the list
  • EXIT           — ticker dropped out of the list
  • ZONE_UPGRADE   — zone moved EARLY → CONFIRMED → CONVICTION
  • ZONE_DOWNGRADE — zone moved the other way

Pure deterministic function — easy to test without touching storage.
A separate wrapper handles the storage write so we can test detection
independently of persistence.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.bw_membership")

# Zone rank for upgrade/downgrade detection.
_ZONE_RANK = {"EARLY": 1, "CONFIRMED": 2, "CONVICTION": 3}


def _norm(t: str) -> str:
    return (t or "").upper().strip().replace(".IS", "")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def detect_changes(
    prev_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    scan_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Compare prev vs new BullWatch item lists, return event records.

    Each event dict has the shape persisted by
    `infra.bullwatch_membership_storage.save_event`:

        {
            "event_id":     str,    # ticker:scan_id:event_type
            "ticker":       str,
            "event_type":   "ENTRY" | "EXIT" | "ZONE_UPGRADE" | "ZONE_DOWNGRADE",
            "occurred_at":  ISO8601 str,
            "scan_id":      str | None,
            "prev_score":   float | None,
            "new_score":    float | None,
            "prev_zone":    str | None,
            "new_zone":     str | None,
            "prev_pattern": str | None,
            "new_pattern":  str | None,
        }
    """
    ts = occurred_at or _now_iso()

    def _index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for it in items or []:
            sym = _norm(it.get("symbol") or it.get("ticker") or "")
            if sym:
                out[sym] = it
        return out

    prev_map = _index(prev_items)
    new_map = _index(new_items)

    events: list[dict[str, Any]] = []

    def _eid(ticker: str, kind: str) -> str:
        # scan_id makes this uniquely identifiable per scan;
        # without one, fall back to the timestamp so re-running the same
        # diff doesn't double-insert.
        suffix = scan_id or ts.replace(":", "").replace("-", "")[:14]
        return f"{ticker}:{suffix}:{kind}"

    # ENTRY — in new, not in prev
    for sym, it in new_map.items():
        if sym in prev_map:
            continue
        events.append({
            "event_id": _eid(sym, "ENTRY"),
            "ticker": sym,
            "event_type": "ENTRY",
            "occurred_at": ts,
            "scan_id": scan_id,
            "prev_score": None,
            "new_score": _safe_score(it),
            "prev_zone": None,
            "new_zone": it.get("zone"),
            "prev_pattern": None,
            "new_pattern": it.get("pattern"),
        })

    # EXIT — in prev, not in new
    for sym, it in prev_map.items():
        if sym in new_map:
            continue
        events.append({
            "event_id": _eid(sym, "EXIT"),
            "ticker": sym,
            "event_type": "EXIT",
            "occurred_at": ts,
            "scan_id": scan_id,
            "prev_score": _safe_score(it),
            "new_score": None,
            "prev_zone": it.get("zone"),
            "new_zone": None,
            "prev_pattern": it.get("pattern"),
            "new_pattern": None,
        })

    # ZONE_UPGRADE / DOWNGRADE — in both, zone changed
    for sym, new_it in new_map.items():
        if sym not in prev_map:
            continue
        prev_it = prev_map[sym]
        pz = prev_it.get("zone")
        nz = new_it.get("zone")
        if not pz or not nz or pz == nz:
            continue
        pr = _ZONE_RANK.get(pz, 0)
        nr = _ZONE_RANK.get(nz, 0)
        if nr > pr:
            kind = "ZONE_UPGRADE"
        elif nr < pr:
            kind = "ZONE_DOWNGRADE"
        else:
            continue
        events.append({
            "event_id": _eid(sym, kind),
            "ticker": sym,
            "event_type": kind,
            "occurred_at": ts,
            "scan_id": scan_id,
            "prev_score": _safe_score(prev_it),
            "new_score": _safe_score(new_it),
            "prev_zone": pz,
            "new_zone": nz,
            "prev_pattern": prev_it.get("pattern"),
            "new_pattern": new_it.get("pattern"),
        })

    return events


def _safe_score(it: dict[str, Any]) -> Optional[float]:
    try:
        v = it.get("score")
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def detect_and_persist(
    prev_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    scan_id: Optional[str] = None,
) -> dict[str, Any]:
    """Detect changes, write them, return a small summary suitable
    for logging by the BullWatch refresh loop.
    """
    events = detect_changes(prev_items, new_items, scan_id=scan_id)
    if not events:
        return {"events": 0, "by_type": {}, "saved": 0}
    by_type: dict[str, int] = {}
    saved = 0
    try:
        from infra import bullwatch_membership_storage as storage
        for ev in events:
            by_type[ev["event_type"]] = by_type.get(ev["event_type"], 0) + 1
            if storage.save_event(ev):
                saved += 1
    except Exception as exc:
        log.warning("membership persist failed: %r", exc)
    log.info(
        "bw_membership: scan_id=%s events=%d saved=%d by_type=%s",
        scan_id, len(events), saved, by_type,
    )
    return {"events": len(events), "by_type": by_type, "saved": saved}

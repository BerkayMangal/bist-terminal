"""Holding-group activity engine.

Premise: When an operator crew warms up one stock in a holding family
(Yıldız, Koç, Sabancı...), nearby names in the same family tend to
follow. If 2+ of a ticker's group peers fired a CONVICTION-band
BullWatch alert in the last 14 days, the ticker itself becomes a
higher-probability candidate for the next rotation leg.

This module produces a small additive boost on top of the base score.
"""
from __future__ import annotations
import datetime as _dt
import logging
from typing import Any, Optional

from engine.bullwatch_holding_groups import get_group, get_peers

log = logging.getLogger("bullwatch.group_activity")

# Boost ceiling — small relative to the 100-point scale, since this is
# a contextual nudge not a primary signal.
_MAX_BOOST = 6.0


def compute_group_activity_boost(
    ticker: str,
    lookback_days: int = 14,
    scan_now: Optional[_dt.datetime] = None,
) -> dict[str, Any]:
    """Return a boost dict for `ticker` based on recent group activity.

    DETERMINISM (audit fix, Stage 1):
      scan_now pins the window cutoff to a caller-supplied timestamp
      so all symbols in a single scan share an identical 14-day
      window. Without it, datetime.now() at call time may shift the
      window mid-scan.

    Output schema:
        {
            "boost": float,            # 0..6
            "group": str | None,       # group name (e.g. "yildiz")
            "peer_count": int,         # peers in the same group
            "peer_alerts_14d": int,    # CONVICTION-band peer alerts in window
            "peer_tickers_active": list[str],
        }
    """
    out = {
        "boost": 0.0,
        "group": None,
        "peer_count": 0,
        "peer_alerts_14d": 0,
        "peer_tickers_active": [],
    }
    group = get_group(ticker)
    if not group:
        return out
    peers = get_peers(ticker)
    out["group"] = group
    out["peer_count"] = len(peers)
    if not peers:
        return out

    try:
        from infra import bullwatch_alerts_storage as storage
    except Exception as exc:  # pragma: no cover
        log.debug("storage import failed: %r", exc)
        return out

    now_ref = scan_now if scan_now is not None else _dt.datetime.now(_dt.timezone.utc)
    if now_ref.tzinfo is None:
        now_ref = now_ref.replace(tzinfo=_dt.timezone.utc)
    cutoff = now_ref - _dt.timedelta(days=max(1, lookback_days))

    active: set[str] = set()
    try:
        recent = storage.get_recent(limit=200, since_days=lookback_days)
    except Exception as exc:
        log.debug("get_recent failed: %r", exc)
        recent = []

    for alert in recent:
        try:
            sym = (alert.get("ticker") or "").upper().strip().replace(".IS", "")
            if sym in peers:
                # Already filtered by since_days; double-check just in case.
                stamp = alert.get("alarmed_at")
                if stamp:
                    try:
                        ts = _dt.datetime.fromisoformat(stamp)
                        if ts < cutoff:
                            continue
                    except Exception:
                        pass
                active.add(sym)
        except Exception:
            continue

    n_active = len(active)
    out["peer_alerts_14d"] = n_active
    out["peer_tickers_active"] = sorted(active)

    # Boost curve: 1 peer alert => small (1.5), 2 => 3.5, 3+ => 5.5,
    # capped at 6.0. Diminishing returns to avoid letting group noise
    # dominate stand-alone signal.
    if n_active <= 0:
        out["boost"] = 0.0
    elif n_active == 1:
        out["boost"] = 1.5
    elif n_active == 2:
        out["boost"] = 3.5
    elif n_active == 3:
        out["boost"] = 5.0
    else:
        out["boost"] = _MAX_BOOST
    return out

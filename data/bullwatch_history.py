"""
BullWatch history store — snapshot persistence and delta computation.

Why this exists
---------------
BullWatch is a tape-reading tool: it tells you what's *quiet* and what's
*moving*, not what to buy. But "moving compared to what?" is the whole
point — without history, every scan looks like a fresh start. This
module keeps a 30-day rolling history of scan results so we can:

  * mark fresh entrants:  "this symbol wasn't eligible yesterday"
  * mark score changes:   "score went 32 → 58, +26 today"
  * mark zone shifts:     "EARLY → CONFIRMED, this is meaningful"
  * mark cooled-off:      "was eligible yesterday, dropped today"
  * draw a 7-day sparkline of how a symbol's score evolved

Storage
-------
Redis. Keyed by date (YYYY-MM-DD in Europe/Istanbul, BIST timezone).
30-day TTL — old snapshots fall off automatically.

Compatibility note
------------------
Save MUST be a no-op when Redis isn't available (local dev, Redis
outage). Trend rozets just don't show; the rest of BullWatch keeps
working. Never crash the scan because history can't be persisted.

Design intentions
-----------------
* Stateless re: trading decisions. We compute change-of-state, never
  emit recommendations or directional language.
* Defensive: any failure in this module degrades gracefully to "no
  history available" — never propagates up.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

from core import redis_client

log = logging.getLogger("bistbull.bullwatch_history")

# Snapshots persist for 30 days — enough for monthly trend analysis,
# short enough to keep Redis lean. Adjust if we add weekly comparisons.
SNAPSHOT_TTL_SEC: int = 30 * 24 * 3600

# Key format: bullwatch:snapshot:2026-05-08
KEY_PREFIX = "bullwatch:snapshot:"


def _today_ist_date() -> dt.date:
    """Today in BIST market timezone (UTC+3, no DST since 2016)."""
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)).date()


def _key_for_date(d: dt.date) -> str:
    return KEY_PREFIX + d.isoformat()


def _slim_item(result_dict: dict) -> dict:
    """
    Distill a full BullWatch result into the minimum needed for trend
    comparison. We don't need narrative, components, full metrics —
    just the few fields that change day-to-day and that we'd want to
    show as a delta or in a sparkline.
    """
    metrics = result_dict.get("metrics") or {}
    return {
        "score": result_dict.get("score"),
        "zone": result_dict.get("zone"),
        "pattern": result_dict.get("pattern"),
        "eligible": bool(result_dict.get("eligible")),
        "float_market_cap": metrics.get("float_market_cap"),
        "rvol": metrics.get("rvol"),
    }


def save_snapshot(results: list[dict], date: Optional[dt.date] = None) -> bool:
    """
    Persist today's scan output to Redis.

    `results` is a list of `BullWatchResult.to_dict()` outputs (the
    same shape returned by /api/bullwatch). We store only ELIGIBLE
    items — keeps the snapshot small and "what cooled off" detection
    is implicit (yesterday eligible, today missing → cooled).

    Returns True if persisted, False otherwise (Redis down, write
    failed, etc). Never raises.
    """
    if not redis_client.is_available():
        return False
    d = date or _today_ist_date()
    try:
        # Index by symbol for O(1) lookup during delta computation
        snapshot = {
            r["symbol"]: _slim_item(r)
            for r in results
            if r.get("eligible") and r.get("symbol")
        }
        ok = redis_client.set_json(
            _key_for_date(d),
            snapshot,
            ttl=SNAPSHOT_TTL_SEC,
        )
        if ok:
            log.info(
                "BullWatch snapshot saved: %d symbols for %s",
                len(snapshot), d.isoformat(),
            )
        return bool(ok)
    except Exception as e:
        log.warning("snapshot save failed: %r", e)
        return False


def get_snapshot(date: dt.date) -> Optional[dict]:
    """Return the full snapshot dict for the given date, or None."""
    if not redis_client.is_available():
        return None
    try:
        return redis_client.get_json(_key_for_date(date))
    except Exception as e:
        log.debug("snapshot load failed for %s: %r", date.isoformat(), e)
        return None


def get_yesterday_snapshot() -> Optional[dict]:
    """Convenience: previous trading-day snapshot. Falls back to whatever
    of the last 5 calendar days has data — handles weekends/holidays
    cleanly without us tracking a market calendar."""
    today = _today_ist_date()
    for offset in (1, 2, 3, 4, 5):
        d = today - dt.timedelta(days=offset)
        snap = get_snapshot(d)
        if snap:
            return snap
    return None


# ----------------------------------------------------------------
# Delta computation — per-symbol change classification.
#
# Output is descriptive, not prescriptive. "score rose +14" is fine;
# "buy this" never appears. The frontend renders these as neutral
# observation badges.
# ----------------------------------------------------------------
DELTA_TYPE_NEW = "new"                  # not in yesterday's snapshot
DELTA_TYPE_ZONE_UP = "zone_up"          # EARLY → CONFIRMED (or higher)
DELTA_TYPE_ZONE_DOWN = "zone_down"      # CONFIRMED → EARLY
DELTA_TYPE_SCORE_UP = "score_up"        # score increased meaningfully
DELTA_TYPE_SCORE_DOWN = "score_down"    # score decreased meaningfully
DELTA_TYPE_STABLE = "stable"            # no change worth flagging

# Thresholds. We don't flag micro-jitters (±2 points is noise from
# minor volume / float fluctuations).
SCORE_DELTA_THRESHOLD = 5.0

_ZONE_RANK = {"EARLY": 1, "CONFIRMED": 2, "CONVICTION": 3}


def _zone_direction(prev: Optional[str], curr: Optional[str]) -> Optional[str]:
    """Return 'up', 'down', or None if zones are missing or equal."""
    if not prev or not curr or prev == curr:
        return None
    p = _ZONE_RANK.get(prev, 0)
    c = _ZONE_RANK.get(curr, 0)
    if p == 0 or c == 0:
        return None
    return "up" if c > p else "down"


def compute_delta_for_item(
    current: dict,
    prior_snapshot: Optional[dict],
) -> dict:
    """
    Classify how this single symbol changed since the last snapshot.

    Returns a dict with keys: type, score_change, prev_score,
    prev_zone, label_short. Always returns something (even if the
    snapshot is missing — type='stable' with no prior data).

    The frontend uses `type` for badge styling, `score_change` for the
    "+N" or "-N" number, and `label_short` for fallback display.
    """
    symbol = current.get("symbol")
    score = current.get("score") or 0
    zone = current.get("zone")

    if not prior_snapshot or symbol not in prior_snapshot:
        # No history (or symbol is fresh today)
        if prior_snapshot is None:
            # We have NO snapshot at all — don't flag anything as "new"
            # because everything would be flagged. Stay quiet.
            return {
                "type": DELTA_TYPE_STABLE,
                "score_change": None,
                "prev_score": None,
                "prev_zone": None,
                "label_short": "—",
            }
        return {
            "type": DELTA_TYPE_NEW,
            "score_change": None,
            "prev_score": None,
            "prev_zone": None,
            "label_short": "yeni eligible",
        }

    prior = prior_snapshot[symbol]
    prev_score = prior.get("score") or 0
    prev_zone = prior.get("zone")
    score_change = round(score - prev_score, 1)

    # Zone changes are the strongest signal — promote them above
    # raw score changes. EARLY→CONFIRMED matters even if score only
    # moved +3, because the categorical interpretation flipped.
    zd = _zone_direction(prev_zone, zone)
    if zd == "up":
        return {
            "type": DELTA_TYPE_ZONE_UP,
            "score_change": score_change,
            "prev_score": prev_score,
            "prev_zone": prev_zone,
            "label_short": f"{prev_zone} → {zone}",
        }
    if zd == "down":
        return {
            "type": DELTA_TYPE_ZONE_DOWN,
            "score_change": score_change,
            "prev_score": prev_score,
            "prev_zone": prev_zone,
            "label_short": f"{prev_zone} → {zone}",
        }

    # No zone change — look at score magnitude
    if score_change >= SCORE_DELTA_THRESHOLD:
        return {
            "type": DELTA_TYPE_SCORE_UP,
            "score_change": score_change,
            "prev_score": prev_score,
            "prev_zone": prev_zone,
            "label_short": f"+{score_change:.0f}",
        }
    if score_change <= -SCORE_DELTA_THRESHOLD:
        return {
            "type": DELTA_TYPE_SCORE_DOWN,
            "score_change": score_change,
            "prev_score": prev_score,
            "prev_zone": prev_zone,
            "label_short": f"{score_change:.0f}",  # already has minus sign
        }

    return {
        "type": DELTA_TYPE_STABLE,
        "score_change": score_change,
        "prev_score": prev_score,
        "prev_zone": prev_zone,
        "label_short": "—",
    }


def annotate_with_deltas(
    results: list[dict],
    prior_snapshot: Optional[dict] = None,
) -> list[dict]:
    """
    Mutate each result dict to include a `delta` field. Reads
    yesterday's snapshot once (or accepts an injected one for tests),
    computes per-item delta, attaches.

    Returns the same list object (mutated in place + returned for
    chaining).
    """
    if prior_snapshot is None:
        prior_snapshot = get_yesterday_snapshot()
    for r in results:
        r["delta"] = compute_delta_for_item(r, prior_snapshot)
    return results


# ----------------------------------------------------------------
# Score history for sparklines
# ----------------------------------------------------------------
def get_score_history(symbol: str, days: int = 7) -> list[Optional[float]]:
    """
    Return last N days' scores for `symbol`, oldest first.
    Days the symbol wasn't eligible (or there's no snapshot) come
    back as None — frontend renders gaps appropriately.

    Length is always exactly `days`. Aligned to today's date going
    backwards.
    """
    out: list[Optional[float]] = []
    today = _today_ist_date()
    sym = symbol.upper()
    for offset in range(days - 1, -1, -1):  # oldest first
        d = today - dt.timedelta(days=offset)
        snap = get_snapshot(d)
        if not snap or sym not in snap:
            out.append(None)
            continue
        out.append(snap[sym].get("score"))
    return out


def get_history_stats() -> dict[str, Any]:
    """Surface in /api/bullwatch/health: how many snapshots we have."""
    if not redis_client.is_available():
        return {"snapshots_available": 0, "redis_available": False}
    today = _today_ist_date()
    count = 0
    oldest = None
    for offset in range(0, 30):
        d = today - dt.timedelta(days=offset)
        if get_snapshot(d) is not None:
            count += 1
            oldest = d.isoformat()
    return {
        "snapshots_available": count,
        "oldest_snapshot": oldest,
        "redis_available": True,
    }

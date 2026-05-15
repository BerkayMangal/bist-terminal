# ================================================================
# BISTBULL TERMINAL — SCAN SCHEDULE
# engine/scan_schedule.py
#
# Cron-style schedule helper for BullWatch refresh loops.
#
# Replaces the old "every N minutes" cadence with a clock-anchored
# schedule: scans run at specific Istanbul times that line up with
# market activity, not arbitrary intervals.
#
# Default schedule (Istanbul time):
#   09:30 — 30 min before market open. Cache is fresh for the day.
#   13:30 — midday, captures the morning session move.
#   18:30 — 30 min after close. Daily candle final; bulletin generator
#           runs after this.
#
# Weekends (Sat/Sun) are skipped — BIST is closed.
#
# Design notes:
#   - Istanbul time is UTC+3 with no DST. Hard-coded — Turkey doesn't
#     switch clocks since 2016.
#   - Schedule is a flat list of (hour, minute, label) tuples. To
#     change the cadence, add/remove entries and update tests. No
#     runtime config required.
#   - Tests use injected `now_utc` so they're deterministic.
# ================================================================

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

log = logging.getLogger("bistbull.scan_schedule")


# Istanbul = UTC+3, no DST since 2016.
ISTANBUL_TZ: dt.timezone = dt.timezone(dt.timedelta(hours=3), name="TRT")


# Schedule entries: (hour, minute, label). Edit here to change cadence.
# Weekends (Sat/Sun, weekday >= 5) are always skipped.
DEFAULT_SCHEDULE: list[tuple[int, int, str]] = [
    (9, 30, "morning"),
    (13, 30, "midday"),
    (18, 30, "post_close"),  # bulletin generator anchor (Stage 7b)
]


# When the next scan slot is more than this far away, log it at INFO
# so operators can see the gap. Mostly a "we paused for the weekend"
# observability lever.
LONG_SLEEP_LOG_THRESHOLD_SEC: int = 4 * 3600


def next_scan_time(
    now_utc: Optional[dt.datetime] = None,
    schedule: Optional[list[tuple[int, int, str]]] = None,
) -> tuple[dt.datetime, str]:
    """Return (next_scan_dt_utc, label) for the next scheduled scan
    relative to `now_utc`.

    Args:
        now_utc: Current UTC time. Defaults to datetime.now(timezone.utc).
            Injectable for deterministic testing.
        schedule: Override the default schedule list. Each entry is
            (hour, minute, label) in Istanbul local time.

    Returns:
        (next_scan_dt_utc, label) — the next scan datetime in UTC and
        a human label like "morning" or "midday".
    """
    if now_utc is None:
        now_utc = dt.datetime.now(dt.timezone.utc)
    if schedule is None:
        schedule = DEFAULT_SCHEDULE

    now_ist = now_utc.astimezone(ISTANBUL_TZ)

    # Try today's remaining slots first.
    for hh, mm, label in schedule:
        candidate_ist = now_ist.replace(
            hour=hh, minute=mm, second=0, microsecond=0,
        )
        if candidate_ist <= now_ist:
            continue
        if candidate_ist.weekday() >= 5:  # Sat/Sun — fall through
            continue
        return candidate_ist.astimezone(dt.timezone.utc), label

    # Nothing left today. Walk forward until we hit a weekday, then
    # pick the first scheduled slot.
    next_day = now_ist + dt.timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += dt.timedelta(days=1)
    first_hh, first_mm, first_label = schedule[0]
    next_dt_ist = next_day.replace(
        hour=first_hh, minute=first_mm, second=0, microsecond=0,
    )
    return next_dt_ist.astimezone(dt.timezone.utc), first_label


def seconds_until_next_scan(
    now_utc: Optional[dt.datetime] = None,
    schedule: Optional[list[tuple[int, int, str]]] = None,
) -> tuple[float, str]:
    """Convenience wrapper: returns (seconds_to_sleep, label) for the
    next scheduled scan."""
    if now_utc is None:
        now_utc = dt.datetime.now(dt.timezone.utc)
    next_dt, label = next_scan_time(now_utc, schedule)
    delta = (next_dt - now_utc).total_seconds()
    # Never return negative — clock skew or scheduling race condition
    # could trip this. Floor at 1s so the caller always actually sleeps.
    return max(1.0, delta), label


def is_market_day(now_utc: Optional[dt.datetime] = None) -> bool:
    """True if `now_utc` falls on a BIST trading day (Mon-Fri Istanbul)."""
    if now_utc is None:
        now_utc = dt.datetime.now(dt.timezone.utc)
    return now_utc.astimezone(ISTANBUL_TZ).weekday() < 5

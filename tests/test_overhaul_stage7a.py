# ================================================================
# tests/test_overhaul_stage7a.py
#
# Great Overhaul Stage 7a: clock-anchored BullWatch refresh schedule.
#
# User feedback: "1 kere piyasa kapaninca run etsin … acaba oglen de
# run etsek mi? veya sabahlari run etsek mi". The previous "every 30
# min" loop burned borsapy quota overnight when nobody was looking.
#
# New schedule (Istanbul time, weekdays only):
#   09:30  morning   — 30 min before market open
#   13:30  midday    — captures morning-session move
#   18:30  post_close — daily candle final; Stage 7b bulletin anchor
# ================================================================

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

UTC = dt.timezone.utc


def _ist(year, month, day, h, m=0):
    """Helper: build a UTC datetime that represents the given Istanbul
    wall-clock time. Istanbul is UTC+3 so we subtract 3 to get UTC."""
    return dt.datetime(year, month, day, h - 3, m, tzinfo=UTC)


# ────────────────────────────────────────────────────────────────
# Default schedule sanity
# ────────────────────────────────────────────────────────────────


class TestDefaultSchedule:
    def test_schedule_has_morning_midday_postclose(self):
        from engine.scan_schedule import DEFAULT_SCHEDULE
        labels = {label for _, _, label in DEFAULT_SCHEDULE}
        assert "morning" in labels
        assert "midday" in labels
        assert "post_close" in labels

    def test_schedule_in_chronological_order(self):
        from engine.scan_schedule import DEFAULT_SCHEDULE
        times = [(hh, mm) for hh, mm, _ in DEFAULT_SCHEDULE]
        assert times == sorted(times)

    def test_istanbul_tz_is_utc_plus_3(self):
        from engine.scan_schedule import ISTANBUL_TZ
        # Istanbul has no DST since 2016 — always +3.
        assert ISTANBUL_TZ.utcoffset(None) == dt.timedelta(hours=3)


# ────────────────────────────────────────────────────────────────
# next_scan_time — basic transitions inside a single weekday
# ────────────────────────────────────────────────────────────────


class TestWeekdayTransitions:
    def test_before_morning_returns_morning_today(self):
        from engine.scan_schedule import next_scan_time
        # Monday 2026-05-11 at 06:00 Istanbul → next slot 09:30 today
        now = _ist(2026, 5, 11, 6, 0)
        next_dt, label = next_scan_time(now)
        assert label == "morning"
        assert next_dt == _ist(2026, 5, 11, 9, 30)

    def test_just_after_morning_returns_midday(self):
        from engine.scan_schedule import next_scan_time
        now = _ist(2026, 5, 11, 9, 31)  # 1 min after morning slot
        next_dt, label = next_scan_time(now)
        assert label == "midday"
        assert next_dt == _ist(2026, 5, 11, 13, 30)

    def test_between_midday_and_close_returns_postclose(self):
        from engine.scan_schedule import next_scan_time
        now = _ist(2026, 5, 11, 16, 0)
        next_dt, label = next_scan_time(now)
        assert label == "post_close"
        assert next_dt == _ist(2026, 5, 11, 18, 30)

    def test_exact_match_skips_to_next(self):
        """If now is exactly the schedule time, we treat it as past
        (the slot is gone). Next slot fires."""
        from engine.scan_schedule import next_scan_time
        now = _ist(2026, 5, 11, 9, 30)
        next_dt, label = next_scan_time(now)
        assert label == "midday"

    def test_after_postclose_rolls_to_next_morning(self):
        from engine.scan_schedule import next_scan_time
        # Monday 23:00 Istanbul → Tuesday 09:30
        now = _ist(2026, 5, 11, 23, 0)
        next_dt, label = next_scan_time(now)
        assert label == "morning"
        assert next_dt == _ist(2026, 5, 12, 9, 30)


# ────────────────────────────────────────────────────────────────
# Weekend handling
# ────────────────────────────────────────────────────────────────


class TestWeekendSkip:
    def test_friday_postclose_rolls_to_monday(self):
        """Friday 18:30+ → Monday 09:30 (skip Sat/Sun entirely)."""
        from engine.scan_schedule import next_scan_time
        # Friday 2026-05-15, 20:00 Istanbul
        now = _ist(2026, 5, 15, 20, 0)
        next_dt, label = next_scan_time(now)
        assert label == "morning"
        # Monday 2026-05-18
        assert next_dt == _ist(2026, 5, 18, 9, 30)

    def test_saturday_anytime_rolls_to_monday(self):
        from engine.scan_schedule import next_scan_time
        # Saturday 2026-05-16, 12:00 Istanbul
        now = _ist(2026, 5, 16, 12, 0)
        next_dt, label = next_scan_time(now)
        assert label == "morning"
        assert next_dt == _ist(2026, 5, 18, 9, 30)

    def test_sunday_anytime_rolls_to_monday(self):
        from engine.scan_schedule import next_scan_time
        # Sunday 2026-05-17, 22:00 Istanbul
        now = _ist(2026, 5, 17, 22, 0)
        next_dt, label = next_scan_time(now)
        assert label == "morning"
        assert next_dt == _ist(2026, 5, 18, 9, 30)


# ────────────────────────────────────────────────────────────────
# Convenience wrappers
# ────────────────────────────────────────────────────────────────


class TestSecondsUntilNextScan:
    def test_returns_positive_delta(self):
        from engine.scan_schedule import seconds_until_next_scan
        # 2 hours before next slot
        now = _ist(2026, 5, 11, 7, 30)  # 2h before morning
        sec, label = seconds_until_next_scan(now)
        assert label == "morning"
        # 2 hours = 7200s ±60s tolerance
        assert 7100 < sec < 7300

    def test_never_returns_negative(self):
        """Clock-skew protection — even when called right at the slot
        boundary, sleep must be positive."""
        from engine.scan_schedule import seconds_until_next_scan
        now = _ist(2026, 5, 11, 9, 30, )
        sec, _ = seconds_until_next_scan(now)
        assert sec >= 1.0


class TestIsMarketDay:
    def test_weekday_is_market_day(self):
        from engine.scan_schedule import is_market_day
        assert is_market_day(_ist(2026, 5, 11, 12, 0)) is True  # Mon
        assert is_market_day(_ist(2026, 5, 15, 12, 0)) is True  # Fri

    def test_weekend_is_not_market_day(self):
        from engine.scan_schedule import is_market_day
        assert is_market_day(_ist(2026, 5, 16, 12, 0)) is False  # Sat
        assert is_market_day(_ist(2026, 5, 17, 12, 0)) is False  # Sun


# ────────────────────────────────────────────────────────────────
# Custom schedule injection — allows future overrides without code edits
# ────────────────────────────────────────────────────────────────


class TestCustomSchedule:
    def test_custom_schedule_respected(self):
        from engine.scan_schedule import next_scan_time
        custom = [(10, 0, "ten"), (15, 0, "fifteen")]
        now = _ist(2026, 5, 11, 8, 0)
        next_dt, label = next_scan_time(now, schedule=custom)
        assert label == "ten"
        assert next_dt == _ist(2026, 5, 11, 10, 0)

    def test_single_entry_schedule(self):
        from engine.scan_schedule import next_scan_time
        custom = [(20, 0, "close")]
        # Before today's close
        now = _ist(2026, 5, 11, 19, 0)
        _, label = next_scan_time(now, schedule=custom)
        assert label == "close"
        # After today's close → tomorrow
        now = _ist(2026, 5, 11, 21, 0)
        next_dt, label = next_scan_time(now, schedule=custom)
        assert label == "close"
        assert next_dt == _ist(2026, 5, 12, 20, 0)


# ────────────────────────────────────────────────────────────────
# Integration: bullwatch_refresh_loop imports + uses the schedule
# ────────────────────────────────────────────────────────────────


class TestLoopWiring:
    def test_refresh_loop_imports_schedule(self):
        """The loop must reach into engine.scan_schedule. Pin via
        source-level reference so a regression is caught instantly."""
        import inspect
        from engine import background_tasks
        src = inspect.getsource(background_tasks.bullwatch_refresh_loop)
        assert "seconds_until_next_scan" in src, (
            "bullwatch_refresh_loop is not using the new schedule helper"
        )


# ────────────────────────────────────────────────────────────────
# Radar once-daily scan anchor (Radar Overhaul follow-up)
# ────────────────────────────────────────────────────────────────


class TestRadarDailyScan:
    def test_radar_scan_hour_is_after_close(self):
        from engine.scan_schedule import RADAR_SCAN_HOUR_IST
        # BIST closes 18:00 — radar scan must be after close.
        assert 18 <= RADAR_SCAN_HOUR_IST <= 23

    def test_before_target_same_day(self):
        from engine.scan_schedule import (
            seconds_until_next_radar_scan, RADAR_SCAN_HOUR_IST,
        )
        # 10:00 Istanbul = 07:00 UTC. Next radar = 19:00 today.
        utc = dt.datetime(2026, 5, 11, 7, 0, tzinfo=UTC)
        sec = seconds_until_next_radar_scan(utc)
        expected_h = RADAR_SCAN_HOUR_IST - 10
        assert abs(sec - expected_h * 3600) < 120

    def test_after_target_rolls_next_day(self):
        from engine.scan_schedule import seconds_until_next_radar_scan
        # 20:00 Istanbul — past 19:00, next is tomorrow 19:00 (~23h).
        utc = dt.datetime(2026, 5, 11, 17, 0, tzinfo=UTC)
        sec = seconds_until_next_radar_scan(utc)
        assert 22 * 3600 < sec < 24 * 3600

    def test_never_negative(self):
        from engine.scan_schedule import seconds_until_next_radar_scan
        # Exactly at 19:00 Istanbul → rolls to tomorrow, positive.
        utc = dt.datetime(2026, 5, 11, 16, 0, tzinfo=UTC)
        assert seconds_until_next_radar_scan(utc) >= 1.0

    def test_background_scanner_uses_daily_anchor(self):
        with open(
            os.path.join(os.path.dirname(__file__), "..", "app.py"),
            "r", encoding="utf-8",
        ) as fh:
            src = fh.read()
        assert "seconds_until_next_radar_scan" in src, (
            "background scanner not wired to the daily radar anchor"
        )

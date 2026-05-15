# ================================================================
# tests/test_bullwatch_sector_rotation.py
#
# Tahtacı sektör rotasyonu — alarm + membership events üstünden
# per-sektör net aktivite skoru.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine import bullwatch_sector_rotation as rot


def _iso_days_ago(d: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=d)).isoformat()


def _alarm(ticker="X", sector="Endüstri", days_ago=1):
    return {
        "alert_id": f"a-{ticker}-{days_ago}",
        "ticker": ticker,
        "alarmed_at": _iso_days_ago(days_ago),
        "sector_tr": sector,
        "zone_at_alarm": "CONVICTION",
        "score_at_alarm": 82,
        "pattern_at_alarm": "X",
    }


def _mem(ticker="X", event_type="ENTRY", days_ago=1):
    return {
        "event_id": f"e-{ticker}-{event_type}-{days_ago}",
        "ticker": ticker,
        "event_type": event_type,
        "occurred_at": _iso_days_ago(days_ago),
        "prev_zone": "CONFIRMED",
        "new_zone": "CONFIRMED",
    }


def _setup(monkeypatch, alarms=None, mems=None, sector_map=None):
    import infra.bullwatch_alerts_storage as bas
    import infra.bullwatch_membership_storage as bms
    monkeypatch.setattr(bas, "get_recent",
                        lambda limit=500, since_days=None: alarms or [])
    monkeypatch.setattr(bms, "get_recent",
                        lambda limit=1000, since_days=None: mems or [])

    # Patch _sector_for_ticker — membership events don't carry sector
    # in storage so the engine resolves via live cache. Map ticker→sector
    # directly for tests.
    smap = sector_map or {}
    monkeypatch.setattr(
        rot, "_sector_for_ticker",
        lambda t: smap.get((t or "").upper(), "Diğer"),
    )


# ────────────────────────────────────────────────────────────────
# Event weighting
# ────────────────────────────────────────────────────────────────


class TestEventWeights:
    def test_alarm_weight_highest(self):
        # CONVICTION alarm = tahtacının net imzası, en güçlü
        assert rot.EVENT_WEIGHTS["ALARM"] > rot.EVENT_WEIGHTS["ZONE_UPGRADE"]
        assert rot.EVENT_WEIGHTS["ZONE_UPGRADE"] > rot.EVENT_WEIGHTS["ENTRY"]

    def test_negatives_smaller_in_magnitude(self):
        # Exits are noisier than entries; weight them less
        assert abs(rot.EVENT_WEIGHTS["EXIT"]) < rot.EVENT_WEIGHTS["ENTRY"]


# ────────────────────────────────────────────────────────────────
# Single-sector aggregation
# ────────────────────────────────────────────────────────────────


class TestSingleSector:
    def test_one_alarm_only(self, monkeypatch):
        _setup(monkeypatch, alarms=[_alarm("X", "Endüstri", 1)])
        out = rot.compute_rotation()
        secs = {s["sector"]: s for s in out["sectors"]}
        assert "Endüstri" in secs
        assert secs["Endüstri"]["net_score"] == 3.0  # ALARM weight
        assert secs["Endüstri"]["events"]["ALARM"] == 1
        assert secs["Endüstri"]["trend"] == "warm"   # 3.0 < 6

    def test_hot_threshold(self, monkeypatch):
        # 2 ALARM + 1 ZONE_UPGRADE = 3 + 3 + 1.5 = 7.5 → hot
        alarms = [_alarm(f"A{i}", "Endüstri", 1) for i in range(2)]
        mems = [_mem("X", "ZONE_UPGRADE", 1)]
        _setup(monkeypatch, alarms=alarms, mems=mems,
               sector_map={"X": "Endüstri"})
        out = rot.compute_rotation()
        secs = {s["sector"]: s for s in out["sectors"]}
        assert secs["Endüstri"]["net_score"] >= 7
        assert secs["Endüstri"]["trend"] == "hot"

    def test_cooling_threshold(self, monkeypatch):
        # Pure exits + downgrades → cooling
        mems = [
            _mem("X", "EXIT", 1),
            _mem("X", "EXIT", 2),
            _mem("X", "ZONE_DOWNGRADE", 3),
            _mem("Y", "ZONE_DOWNGRADE", 1),
        ]
        _setup(monkeypatch, mems=mems,
               sector_map={"X": "Endüstri", "Y": "Endüstri"})
        out = rot.compute_rotation()
        secs = {s["sector"]: s for s in out["sectors"]}
        # 2 exits (-1.0) + 2 downgrades (-2.0) = -3.0
        assert secs["Endüstri"]["net_score"] <= -2
        assert secs["Endüstri"]["trend"] == "cooling"

    def test_neutral_when_balanced(self, monkeypatch):
        # 1 ENTRY (+1) + 1 EXIT (-0.5) = +0.5 → neutral
        mems = [
            _mem("X", "ENTRY", 1),
            _mem("X", "EXIT", 1),
        ]
        _setup(monkeypatch, mems=mems,
               sector_map={"X": "Endüstri"})
        out = rot.compute_rotation()
        secs = {s["sector"]: s for s in out["sectors"]}
        assert secs["Endüstri"]["trend"] == "neutral"


# ────────────────────────────────────────────────────────────────
# Window filtering
# ────────────────────────────────────────────────────────────────


class TestWindowFiltering:
    def test_events_outside_window_excluded(self, monkeypatch):
        _setup(monkeypatch,
               alarms=[_alarm("X", "Endüstri", 30)])   # > 7 day window
        out = rot.compute_rotation(window_days=7)
        assert out["total_events"] == 0
        assert out["sectors"] == []

    def test_events_just_inside_window(self, monkeypatch):
        _setup(monkeypatch,
               alarms=[_alarm("X", "Endüstri", 6.9)])
        out = rot.compute_rotation(window_days=7)
        assert out["total_events"] == 1


# ────────────────────────────────────────────────────────────────
# Multi-sector ranking
# ────────────────────────────────────────────────────────────────


class TestSorting:
    def test_sectors_sorted_by_net_desc(self, monkeypatch):
        alarms = [
            _alarm("A", "Teknoloji", 1),
            _alarm("B", "Endüstri", 1),
            _alarm("C", "Endüstri", 2),    # 2 alarms in Endüstri
        ]
        _setup(monkeypatch, alarms=alarms)
        out = rot.compute_rotation()
        order = [s["sector"] for s in out["sectors"]]
        # Endüstri (2 alarms = 6) > Teknoloji (1 alarm = 3)
        assert order[0] == "Endüstri"
        assert order[1] == "Teknoloji"


# ────────────────────────────────────────────────────────────────
# Top tickers per sector
# ────────────────────────────────────────────────────────────────


class TestTopTickers:
    def test_top_tickers_ranked_by_activity(self, monkeypatch):
        # Same sector, BIMAS has 2 alarms vs ULKER 1
        alarms = [
            _alarm("BIMAS", "Tüketim", 1),
            _alarm("BIMAS", "Tüketim", 2),
            _alarm("ULKER", "Tüketim", 1),
        ]
        _setup(monkeypatch, alarms=alarms)
        out = rot.compute_rotation()
        sec = next(s for s in out["sectors"] if s["sector"] == "Tüketim")
        # BIMAS should be first
        assert sec["top_tickers"][0] == "BIMAS"


# ────────────────────────────────────────────────────────────────
# Membership events without sector → resolved via cache
# ────────────────────────────────────────────────────────────────


class TestMembershipSectorResolution:
    def test_uses_sector_for_ticker_resolver(self, monkeypatch):
        # Membership event has ticker but no sector — resolver fills it
        mems = [_mem("FORTE", "ENTRY", 1)]
        _setup(monkeypatch, mems=mems,
               sector_map={"FORTE": "Teknoloji"})
        out = rot.compute_rotation()
        secs = {s["sector"]: s for s in out["sectors"]}
        assert "Teknoloji" in secs
        assert secs["Teknoloji"]["events"]["ENTRY"] == 1

    def test_unknown_ticker_falls_into_diger(self, monkeypatch):
        mems = [_mem("UNKNOWN", "ENTRY", 1)]
        _setup(monkeypatch, mems=mems, sector_map={})
        out = rot.compute_rotation()
        secs = {s["sector"]: s for s in out["sectors"]}
        # Resolver returns "Diğer" for unknown
        assert "Diğer" in secs


# ────────────────────────────────────────────────────────────────
# Summary aggregator
# ────────────────────────────────────────────────────────────────


class TestRotationSummary:
    def test_counts_by_trend(self, monkeypatch):
        # 1 hot + 1 cooling + 1 neutral
        alarms = [_alarm(f"A{i}", "Endüstri", 1) for i in range(3)]  # 9 → hot
        mems = [
            _mem("X", "EXIT", 1),
            _mem("Y", "EXIT", 1),
            _mem("Z", "ZONE_DOWNGRADE", 1),
        ]
        _setup(monkeypatch, alarms=alarms, mems=mems,
               sector_map={"X": "Tüketim", "Y": "Tüketim", "Z": "Tüketim"})
        out = rot.get_rotation_summary()
        c = out["trend_counts"]
        assert c["hot"] >= 1
        assert c["cooling"] >= 1

    def test_empty(self, monkeypatch):
        _setup(monkeypatch)
        out = rot.get_rotation_summary()
        assert out["sectors_count"] == 0
        assert out["total_events"] == 0

# ================================================================
# tests/test_viop_uoa.py
#
# VIOP UOA (unusual options activity) z-score engine.
# Tests cover pure math (compute_uoa) + aggregation (get_today_anomalies,
# get_summary) with mocked storage.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine import viop_uoa


def _row(volume_tl, code="F_X0626", snap_date="2026-05-13"):
    return {
        "code": code,
        "contract": "X",
        "snap_date": snap_date,
        "kind": "future",
        "underlying": "X",
        "side": "F",
        "strike": None,
        "expiry": "2026-06",
        "price": 100.0,
        "change": 0,
        "volume_tl": volume_tl,
        "volume_qty": volume_tl / 100.0 if volume_tl else 0,
    }


# ────────────────────────────────────────────────────────────────
# compute_uoa — pure stats
# ────────────────────────────────────────────────────────────────


class TestComputeUOA:
    def test_empty_history(self):
        out = viop_uoa.compute_uoa([])
        assert out["score"] is None
        assert out["baseline_days"] == 0
        assert out["eligible"] is False

    def test_only_today_no_baseline(self):
        # 1 row → today exists, baseline empty → no score
        out = viop_uoa.compute_uoa([_row(1000)])
        assert out["score"] is None
        assert out["baseline_days"] == 0

    def test_flat_baseline_spike_detected(self):
        # 10 days at 1000 TL, today 10000 — clear spike.
        hist = [_row(10000)] + [_row(1000) for _ in range(10)]
        out = viop_uoa.compute_uoa(hist)
        assert out["score"] is not None
        assert out["score"] > 5      # strong anomaly
        assert out["baseline_avg_tl"] == 1000.0
        assert out["ratio"] == 10.0
        assert out["eligible"] is True
        assert out["tentative"] is False

    def test_quiet_baseline_excluded_by_avg_floor(self):
        # Baseline averages <500 TL → eligible=False
        hist = [_row(100)] + [_row(50) for _ in range(10)]
        out = viop_uoa.compute_uoa(hist)
        assert out["eligible"] is False

    def test_tentative_flag_below_min_baseline(self):
        # Only 3 baseline days → tentative=True
        hist = [_row(10000)] + [_row(1000) for _ in range(3)]
        out = viop_uoa.compute_uoa(hist)
        assert out["tentative"] is True
        assert out["score"] is not None  # still computed

    def test_today_normal_low_score(self):
        # Today matches baseline avg → ~zero z-score
        hist = [_row(1000)] + [_row(1000) for _ in range(10)]
        out = viop_uoa.compute_uoa(hist)
        assert abs(out["score"]) < 1

    def test_dead_flat_baseline_uses_stdev_floor(self):
        # stdev=0 on baseline → without floor, z-score is undefined.
        # With STDEV_FLOOR we still get a finite score.
        hist = [_row(5000)] + [_row(1000) for _ in range(10)]
        out = viop_uoa.compute_uoa(hist)
        assert out["score"] is not None
        assert math_isfinite(out["score"])

    def test_override_today_volume(self):
        # Caller can override today's volume — useful when freshness
        # comes from a different code path than the history row.
        hist = [_row(1000)] + [_row(1000) for _ in range(10)]
        out = viop_uoa.compute_uoa(hist, today_volume_tl=20000)
        assert out["today_tl"] == 20000.0
        assert out["score"] > 5


def math_isfinite(x):
    import math
    return math.isfinite(x)


# ────────────────────────────────────────────────────────────────
# get_today_anomalies — orchestrator with mocked storage
# ────────────────────────────────────────────────────────────────


def _setup_storage(monkeypatch, today_rows, history_map):
    """Patch viop_storage.get_today + get_history."""
    import infra.viop_storage as st
    monkeypatch.setattr(st, "get_today",
                        lambda kind=None, underlying=None, limit=500:
                        [r for r in today_rows
                         if (kind is None or r.get("kind") == kind)])
    monkeypatch.setattr(st, "get_history",
                        lambda code, days=30: history_map.get(code, []))


class TestGetTodayAnomalies:
    def test_no_today_rows(self, monkeypatch):
        _setup_storage(monkeypatch, today_rows=[], history_map={})
        out = viop_uoa.get_today_anomalies()
        assert out == []

    def test_filters_below_min_score(self, monkeypatch):
        # Two contracts: one anomalous, one normal
        today = [_row(10000, code="ANOM"), _row(1000, code="NORM")]
        # ANOM: today=10000, baseline=1000 → z-score high
        # NORM: today=1000, baseline=1000 → z-score ~0
        hist = {
            "ANOM": today[:1] + [_row(1000, code="ANOM") for _ in range(10)],
            "NORM": today[1:2] + [_row(1000, code="NORM") for _ in range(10)],
        }
        _setup_storage(monkeypatch, today_rows=today, history_map=hist)
        out = viop_uoa.get_today_anomalies(min_score=2.0)
        codes = [r["code"] for r in out]
        assert "ANOM" in codes
        assert "NORM" not in codes

    def test_sorted_by_score_desc(self, monkeypatch):
        today = [
            _row(5000, code="MID"),
            _row(20000, code="TOP"),
            _row(8000, code="HI"),
        ]
        hist = {
            c: [next(r for r in today if r["code"] == c)]
               + [_row(1000, code=c) for _ in range(10)]
            for c in ("MID", "TOP", "HI")
        }
        _setup_storage(monkeypatch, today_rows=today, history_map=hist)
        out = viop_uoa.get_today_anomalies(min_score=0)
        order = [r["code"] for r in out]
        assert order == ["TOP", "HI", "MID"]

    def test_eligible_filter_drops_quiet_contracts(self, monkeypatch):
        # Quiet contract (avg<500) — even though today's spike is huge,
        # the avg-TL floor drops it.
        today = [_row(50000, code="QUIET")]
        hist = {
            "QUIET": today + [_row(100, code="QUIET") for _ in range(10)],
        }
        _setup_storage(monkeypatch, today_rows=today, history_map=hist)
        out = viop_uoa.get_today_anomalies(min_score=2.0)
        assert out == []

    def test_tentative_excluded_by_default(self, monkeypatch):
        # 3 baseline days → tentative; default include_tentative=False
        today = [_row(20000, code="NEW")]
        hist = {
            "NEW": today + [_row(1000, code="NEW") for _ in range(3)],
        }
        _setup_storage(monkeypatch, today_rows=today, history_map=hist)
        assert viop_uoa.get_today_anomalies() == []
        # But include_tentative=True surfaces it
        out = viop_uoa.get_today_anomalies(include_tentative=True)
        assert len(out) == 1
        assert out[0]["uoa"]["tentative"] is True

    def test_kind_filter_passed_through(self, monkeypatch):
        today = [
            _row(10000, code="O_X"),
            _row(10000, code="F_X"),
        ]
        today[0]["kind"] = "option"
        today[1]["kind"] = "future"
        hist = {
            "O_X": [today[0]] + [_row(1000, code="O_X") for _ in range(10)],
            "F_X": [today[1]] + [_row(1000, code="F_X") for _ in range(10)],
        }
        _setup_storage(monkeypatch, today_rows=today, history_map=hist)
        out = viop_uoa.get_today_anomalies(kind="option")
        assert len(out) == 1
        assert out[0]["code"] == "O_X"

    def test_limit_applied(self, monkeypatch):
        codes = [f"C{i}" for i in range(20)]
        today = [_row(10000 + i, code=c) for i, c in enumerate(codes)]
        hist = {
            c: [t] + [_row(1000, code=c) for _ in range(10)]
            for c, t in zip(codes, today)
        }
        _setup_storage(monkeypatch, today_rows=today, history_map=hist)
        out = viop_uoa.get_today_anomalies(min_score=0, limit=5)
        assert len(out) == 5


# ────────────────────────────────────────────────────────────────
# get_summary — aggregation
# ────────────────────────────────────────────────────────────────


class TestGetSummary:
    def test_counts_by_kind(self, monkeypatch):
        today = [
            _row(10000, code="O1"), _row(15000, code="O2"),
            _row(12000, code="F1"),
        ]
        today[0]["kind"] = "option"; today[0]["underlying"] = "BIMAS"
        today[1]["kind"] = "option"; today[1]["underlying"] = "BIMAS"
        today[2]["kind"] = "future"; today[2]["underlying"] = "XU030"
        hist = {
            c: [t] + [_row(1000, code=c) for _ in range(10)]
            for c, t in zip(("O1", "O2", "F1"), today)
        }
        _setup_storage(monkeypatch, today_rows=today, history_map=hist)
        out = viop_uoa.get_summary()
        assert out["n_options_anomalous"] == 2
        assert out["n_futures_anomalous"] == 1

    def test_top_underlying_ranked_by_score(self, monkeypatch):
        today = [
            _row(15000, code="O1"),     # BIMAS, high score
            _row(8000, code="O2"),       # BIMAS, lower
            _row(10000, code="F1"),     # XU030
        ]
        today[0]["underlying"] = "BIMAS"
        today[1]["underlying"] = "BIMAS"
        today[2]["underlying"] = "XU030"
        hist = {
            c: [t] + [_row(1000, code=c) for _ in range(10)]
            for c, t in zip(("O1", "O2", "F1"), today)
        }
        _setup_storage(monkeypatch, today_rows=today, history_map=hist)
        out = viop_uoa.get_summary()
        top = out["top_underlying"]
        # BIMAS should be first (higher score_max, plus 2 hits)
        assert top[0]["underlying"] == "BIMAS"
        assert top[0]["n"] == 2

    def test_empty_universe(self, monkeypatch):
        _setup_storage(monkeypatch, today_rows=[], history_map={})
        out = viop_uoa.get_summary()
        assert out["n_options_anomalous"] == 0
        assert out["top_underlying"] == []

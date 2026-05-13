# ================================================================
# tests/test_bullwatch_backtest.py
#
# Tahtacı PR C — backtest analytics over alarm history.
# Pure dict aggregation; uses monkeypatched storage.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine.bullwatch_backtest import (
    compute_backtest,
    _bucket_stats,
    _score_band,
    DEFAULT_WIN_THRESHOLD,
)


def _alert(
    ticker: str = "TEST",
    score: float = 82.0,
    zone: str = "CONVICTION",
    pattern: str = "Float Squeeze + Compression",
    sector: str = "Endüstri",
    r1d: float | None = None,
    r1w: float | None = None,
    r1m: float | None = None,
    days_ago: int = 30,
) -> dict:
    stamp = (_dt.datetime.now(_dt.timezone.utc)
             - _dt.timedelta(days=days_ago)).isoformat()
    return {
        "alert_id": f"{ticker}-{days_ago}",
        "ticker": ticker,
        "alarmed_at": stamp,
        "score_at_alarm": score,
        "zone_at_alarm": zone,
        "pattern_at_alarm": pattern,
        "sector_tr": sector,
        "reaction_1d_pct": r1d,
        "reaction_1w_pct": r1w,
        "reaction_1m_pct": r1m,
    }


# ── Pure helpers ─────────────────────────────────────────────────


class TestHelpers:
    def test_empty_bucket(self):
        s = _bucket_stats([])
        assert s["n"] == 0
        assert s["win_rate"] is None

    def test_all_positive_bucket(self):
        s = _bucket_stats([1.0, 2.0, 3.0])
        assert s["n"] == 3
        assert s["win_rate"] == 1.0
        assert s["mean"] == 2.0
        assert s["best"] == 3.0
        assert s["worst"] == 1.0

    def test_mixed_bucket(self):
        s = _bucket_stats([-2.0, 0.0, 1.0, 5.0])
        # >0 only (threshold is 0, NOT inclusive)
        assert s["win_rate"] == 0.5
        assert s["mean"] == 1.0

    def test_score_band_boundaries(self):
        assert _score_band(75) == "75-80"
        assert _score_band(79.99) == "75-80"
        assert _score_band(80) == "80-85"
        assert _score_band(85) == "85-90"
        assert _score_band(90) == "90+"
        assert _score_band(99.9) == "90+"


# ── Empty-history paths ──────────────────────────────────────────


class TestEmptyHistory:
    def test_no_alerts(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=500, since_days=None: [])
        out = compute_backtest(since_days=90)
        assert out["total_alerts"] == 0
        assert out["overall"] == {}
        assert out["by_score_band"] == []

    def test_storage_failure(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage

        def _boom(*a, **kw):
            raise RuntimeError("redis down")

        monkeypatch.setattr(storage, "get_recent", _boom)
        out = compute_backtest(since_days=90)
        assert out["total_alerts"] == 0


# ── Aggregation logic ────────────────────────────────────────────


class TestAggregation:
    @pytest.fixture
    def sample_alerts(self):
        return [
            _alert("AAA", score=78, sector="Endüstri", r1d=2.0, r1w=4.0, r1m=8.0),
            _alert("BBB", score=82, sector="Endüstri", r1d=-1.0, r1w=3.0, r1m=5.0),
            _alert("CCC", score=87, sector="Teknoloji", r1d=3.0, r1w=-1.0, r1m=2.0),
            _alert("DDD", score=92, sector="Teknoloji", r1d=5.0, r1w=10.0, r1m=15.0),
        ]

    def _setup(self, monkeypatch, alerts):
        from infra import bullwatch_alerts_storage as storage
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=500, since_days=None: alerts)

    def test_total_count(self, monkeypatch, sample_alerts):
        self._setup(monkeypatch, sample_alerts)
        out = compute_backtest()
        assert out["total_alerts"] == 4

    def test_overall_win_rate_1d(self, monkeypatch, sample_alerts):
        self._setup(monkeypatch, sample_alerts)
        out = compute_backtest()
        # 1d returns: [2, -1, 3, 5] → 3 wins of 4
        assert out["overall"]["1d"]["n"] == 4
        assert out["overall"]["1d"]["win_rate"] == 0.75

    def test_overall_win_rate_1w(self, monkeypatch, sample_alerts):
        self._setup(monkeypatch, sample_alerts)
        out = compute_backtest()
        # 1w returns: [4, 3, -1, 10] → 3 wins of 4
        assert out["overall"]["1w"]["win_rate"] == 0.75

    def test_by_score_band_split(self, monkeypatch, sample_alerts):
        self._setup(monkeypatch, sample_alerts)
        out = compute_backtest()
        bands = {b["band"]: b for b in out["by_score_band"]}
        # 78 → 75-80, 82 → 80-85, 87 → 85-90, 92 → 90+
        assert bands["75-80"]["n"] == 1
        assert bands["80-85"]["n"] == 1
        assert bands["85-90"]["n"] == 1
        assert bands["90+"]["n"] == 1
        # 90+ band should have 100% 1d win rate (5.0)
        assert bands["90+"]["1d"]["win_rate"] == 1.0

    def test_by_sector_aggregation(self, monkeypatch, sample_alerts):
        self._setup(monkeypatch, sample_alerts)
        out = compute_backtest()
        sectors = {s["sector"]: s for s in out["by_sector"]}
        assert sectors["Endüstri"]["n"] == 2
        assert sectors["Teknoloji"]["n"] == 2

    def test_by_pattern_splits_on_plus(self, monkeypatch):
        alerts = [
            _alert("AAA", pattern="Float Squeeze + Volatility Compression",
                   r1d=2.0),
            _alert("BBB", pattern="Float Squeeze + Walk-Up", r1d=4.0),
            _alert("CCC", pattern="Walk-Up", r1d=1.0),
        ]
        self._setup(monkeypatch, alerts)
        out = compute_backtest()
        patterns = {p["pattern"]: p for p in out["by_pattern"]}
        # Float Squeeze appears in 2 alerts, Walk-Up appears in 2,
        # Volatility Compression in 1.
        assert patterns["Float Squeeze"]["n"] == 2
        assert patterns["Walk-Up"]["n"] == 2
        assert patterns["Volatility Compression"]["n"] == 1

    def test_histogram_buckets_returns(self, monkeypatch, sample_alerts):
        self._setup(monkeypatch, sample_alerts)
        out = compute_backtest()
        # 1d returns: 2.0 → 2..5%, -1.0 → -2..0%, 3.0 → 2..5%, 5.0 → 5..10%
        buckets = {b["bucket"]: b["count"] for b in out["histogram_1d"]}
        assert buckets["-2..0%"] == 1
        assert buckets["2..5%"] == 2
        assert buckets["5..10%"] == 1
        assert buckets["0..2%"] == 0


# ── Fake-pump detector ───────────────────────────────────────────


class TestFakePump:
    def _setup(self, monkeypatch, alerts):
        from infra import bullwatch_alerts_storage as storage
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=500, since_days=None: alerts)

    def test_no_fakes_when_1w_positive(self, monkeypatch):
        alerts = [_alert("AAA", r1d=5.0, r1w=8.0, r1m=12.0)]
        self._setup(monkeypatch, alerts)
        out = compute_backtest()
        assert out["fake_pump"]["count"] == 0
        assert out["fake_pump"]["share"] == 0.0

    def test_classic_pump_dump(self, monkeypatch):
        # 1d +4%, 1w -3% → fake pump
        alerts = [
            _alert("AAA", r1d=4.0, r1w=-3.0, r1m=-5.0),
            _alert("BBB", r1d=3.0, r1w=8.0, r1m=10.0),  # clean
            _alert("CCC", r1d=5.0, r1w=-4.0, r1m=-6.0),  # fake
        ]
        self._setup(monkeypatch, alerts)
        out = compute_backtest()
        assert out["fake_pump"]["count"] == 2
        # 2 fakes of 3 eligible alerts
        assert abs(out["fake_pump"]["share"] - 2/3) < 1e-6
        tickers = {s["ticker"] for s in out["fake_pump"]["samples"]}
        assert tickers == {"AAA", "CCC"}

    def test_below_threshold_not_flagged(self, monkeypatch):
        # 1d only +2% (under +3 threshold) → not eligible
        alerts = [_alert("AAA", r1d=2.0, r1w=-3.0, r1m=-5.0)]
        self._setup(monkeypatch, alerts)
        out = compute_backtest()
        assert out["fake_pump"]["count"] == 0

    def test_missing_reactions_excluded_from_share(self, monkeypatch):
        # 2 with reactions, 1 missing → share computed on 2
        alerts = [
            _alert("AAA", r1d=4.0, r1w=-3.0, r1m=None),
            _alert("BBB", r1d=None, r1w=None, r1m=None),  # excluded
            _alert("CCC", r1d=3.0, r1w=5.0, r1m=8.0),
        ]
        self._setup(monkeypatch, alerts)
        out = compute_backtest()
        assert out["fake_pump"]["count"] == 1
        # 1 fake of 2 alerts with reactions
        assert abs(out["fake_pump"]["share"] - 0.5) < 1e-6


# ── Win threshold customization ──────────────────────────────────


class TestWinThreshold:
    def test_higher_threshold_reduces_win_rate(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage
        alerts = [
            _alert("A", r1d=0.5), _alert("B", r1d=2.0),
            _alert("C", r1d=4.0), _alert("D", r1d=6.0),
        ]
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=500, since_days=None: alerts)
        # Default threshold 0 → 4/4 win
        out_zero = compute_backtest(win_threshold=0.0)
        assert out_zero["overall"]["1d"]["win_rate"] == 1.0
        # Threshold +3% → 2/4 win
        out_three = compute_backtest(win_threshold=3.0)
        assert out_three["overall"]["1d"]["win_rate"] == 0.5

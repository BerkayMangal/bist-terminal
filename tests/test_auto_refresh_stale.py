# ================================================================
# tests/test_auto_refresh_stale.py
#
# Background auto-refresh loop + score velocity helper.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import engine.auto_refresh_stale as ars
from engine.diag_fundamentals import compute_score_velocity


# ── compute_score_velocity ──────────────────────────────────────


class TestScoreVelocity:
    def test_empty_ticker(self):
        out = compute_score_velocity("")
        assert out["n_snapshots"] == 0
        assert out["frozen"] is False

    def test_no_rows(self, monkeypatch):
        # Patch sqlite to return no rows
        import infra.storage as st

        class _C:
            def execute(self, *a, **kw):
                class _R:
                    def fetchall(self): return []
                return _R()

        monkeypatch.setattr(st, "_get_conn", lambda: _C())
        out = compute_score_velocity("FORTE", days=30)
        assert out["n_snapshots"] == 0
        assert out["frozen"] is False

    def test_frozen_when_no_daily_moves(self, monkeypatch):
        import infra.storage as st
        # 10 identical scores → max_jump = 0 → frozen
        rows = [(72.0,)] * 10

        class _C:
            def execute(self, *a, **kw):
                class _R:
                    def fetchall(self): return rows
                return _R()
        monkeypatch.setattr(st, "_get_conn", lambda: _C())
        out = compute_score_velocity("FORTE", days=30)
        assert out["n_snapshots"] == 10
        assert out["frozen"] is True
        assert out["max_jump"] == 0
        assert out["delta"] == 0

    def test_not_frozen_when_one_big_jump(self, monkeypatch):
        import infra.storage as st
        # 7 days flat at 72, then one jump to 78
        rows = [(72.0,)] * 7 + [(78.0,)]

        class _C:
            def execute(self, *a, **kw):
                class _R:
                    def fetchall(self): return rows
                return _R()
        monkeypatch.setattr(st, "_get_conn", lambda: _C())
        out = compute_score_velocity("FORTE")
        assert out["n_snapshots"] == 8
        assert out["frozen"] is False
        assert out["max_jump"] == 6
        assert out["delta"] == 6

    def test_too_few_snapshots_never_frozen(self, monkeypatch):
        import infra.storage as st
        rows = [(72.0,), (72.0,)]   # only 2 snapshots

        class _C:
            def execute(self, *a, **kw):
                class _R:
                    def fetchall(self): return rows
                return _R()
        monkeypatch.setattr(st, "_get_conn", lambda: _C())
        out = compute_score_velocity("FORTE")
        # Below the 5-snapshot floor → cannot claim frozen even with 0 jumps
        assert out["frozen"] is False

    def test_volatile_score(self, monkeypatch):
        import infra.storage as st
        rows = [(70.0,), (75.0,), (68.0,), (78.0,), (72.0,), (80.0,)]

        class _C:
            def execute(self, *a, **kw):
                class _R:
                    def fetchall(self): return rows
                return _R()
        monkeypatch.setattr(st, "_get_conn", lambda: _C())
        out = compute_score_velocity("FORTE")
        assert out["frozen"] is False
        assert out["max_jump"] >= 7
        assert out["abs_mean_jump"] >= 5


# ── run_one_cycle ───────────────────────────────────────────────


class TestAutoRefreshCycle:
    def _setup(self, monkeypatch, universe, summary_items, analysis_score=80.0):
        """Wire stubs so run_one_cycle doesn't touch real data sources."""
        monkeypatch.setattr(ars, "_universe_for_refresh", lambda: universe)
        # Import the function module-locally so monkeypatch hooks
        import engine.diag_fundamentals as df
        monkeypatch.setattr(
            df, "compute_summary",
            lambda tickers: {"items": summary_items, "summary": {}},
        )
        # Patch the kap_dispatcher invalidate to no-op
        import engine.kap_dispatcher as kd
        monkeypatch.setattr(kd, "_invalidate_caches_for_ticker", lambda t: None)
        # Patch analyze_symbol on the import path used inside run_one_cycle
        import engine.analysis as an

        def _fake_analyze(sym):
            return {"score": analysis_score}

        monkeypatch.setattr(an, "analyze_symbol", _fake_analyze)
        # Reset module-level last cycle
        ars._last_cycle = None

    def test_empty_universe_no_op(self, monkeypatch):
        self._setup(monkeypatch, universe=[], summary_items=[])
        res = ars.run_one_cycle()
        assert res.universe_size == 0
        assert res.attempted == 0
        assert res.succeeded == 0

    def test_no_stale_no_attempts(self, monkeypatch):
        items = [
            {"ticker": "A", "age_status": "fresh", "age_hours": 1, "warnings": []},
            {"ticker": "B", "age_status": "fresh", "age_hours": 2, "warnings": []},
        ]
        self._setup(monkeypatch, universe=["A", "B"], summary_items=items)
        res = ars.run_one_cycle()
        assert res.candidates_found == 0
        assert res.attempted == 0

    def test_refreshes_stale_within_budget(self, monkeypatch):
        items = [
            {"ticker": f"S{i}", "age_status": "stale",
             "age_hours": 100 + i, "warnings": []}
            for i in range(5)
        ]
        self._setup(monkeypatch, universe=[f"S{i}" for i in range(5)],
                    summary_items=items)
        res = ars.run_one_cycle(max_per_cycle=3)
        assert res.candidates_found == 5
        assert res.attempted == 3
        assert res.succeeded == 3

    def test_records_score_change_when_significant(self, monkeypatch):
        items = [
            {"ticker": "S1", "age_status": "stale",
             "age_hours": 200, "warnings": []},
        ]
        self._setup(monkeypatch, universe=["S1"], summary_items=items,
                    analysis_score=78.0)
        # Stub previous score to 70 → delta 8 ≥ threshold 2
        monkeypatch.setattr(ars, "_previous_score", lambda t: 70.0)
        res = ars.run_one_cycle()
        assert len(res.score_changes) == 1
        c = res.score_changes[0]
        assert c["ticker"] == "S1"
        assert c["before"] == 70.0 and c["after"] == 78.0
        assert c["delta"] == 8.0

    def test_skips_change_below_threshold(self, monkeypatch):
        items = [{"ticker": "S1", "age_status": "stale",
                  "age_hours": 100, "warnings": []}]
        self._setup(monkeypatch, universe=["S1"], summary_items=items,
                    analysis_score=70.5)
        monkeypatch.setattr(ars, "_previous_score", lambda t: 70.0)
        res = ars.run_one_cycle()
        # Δ 0.5 < 2.0 threshold → not recorded
        assert res.score_changes == []
        # But success counter still ticks
        assert res.succeeded == 1

    def test_analyze_failure_counts_as_failed(self, monkeypatch):
        items = [{"ticker": "S1", "age_status": "stale",
                  "age_hours": 100, "warnings": []}]
        self._setup(monkeypatch, universe=["S1"], summary_items=items)
        # Override analyze_symbol to raise
        import engine.analysis as an

        def _boom(sym):
            raise RuntimeError("borsapy down")

        monkeypatch.setattr(an, "analyze_symbol", _boom)
        res = ars.run_one_cycle()
        assert res.failed == 1
        assert res.succeeded == 0
        assert any("borsapy down" in e for e in res.sample_errors)

    def test_to_dict_shape(self, monkeypatch):
        items = [{"ticker": "S1", "age_status": "stale",
                  "age_hours": 100, "warnings": []}]
        self._setup(monkeypatch, universe=["S1"], summary_items=items)
        monkeypatch.setattr(ars, "_previous_score", lambda t: 70.0)
        res = ars.run_one_cycle()
        d = res.to_dict()
        for k in ("started_at", "finished_at", "duration_sec",
                  "universe_size", "candidates_found", "attempted",
                  "succeeded", "failed", "score_changes",
                  "score_change_count", "avg_abs_delta", "sample_errors"):
            assert k in d

    def test_last_cycle_singleton_updates(self, monkeypatch):
        items = [{"ticker": "S1", "age_status": "stale",
                  "age_hours": 100, "warnings": []}]
        self._setup(monkeypatch, universe=["S1"], summary_items=items)
        ars.run_one_cycle()
        d = ars.get_last_cycle()
        assert d is not None
        assert d["attempted"] == 1

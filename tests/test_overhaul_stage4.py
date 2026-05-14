# ================================================================
# tests/test_overhaul_stage4.py
#
# Great Overhaul Stage 4: Performance — heatmap fetch parallelization.
#
# Audit finding:
#   `_fetch_heatmap_data` iterated UNIVERSE (108 tickers) sequentially
#   through borsapy fast_info — measured ~500ms per ticker → ~54s total
#   wall-time per refresh cycle. With HEATMAP_REFRESH_INTERVAL = 1800s
#   the loop spent ~3% of every cycle blocked on a single, pointlessly
#   serial network walk. Worst-case (one stuck ticker) blocked the
#   whole refresh — no per-ticker timeout existed.
#
# Fix:
#   - ThreadPoolExecutor with HEATMAP_FETCH_WORKERS (=8) workers
#   - Per-ticker HEATMAP_PER_TICKER_TIMEOUT (=6s)
#   - Total HEATMAP_FETCH_BUDGET_SEC (=30s) cap for as_completed
#   - Same return contract (list of dicts) — caller (_build_heatmap_result)
#     unchanged
#
# These tests pin the new contract without touching production borsapy.
# ================================================================

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Constants exposed for tuning observability
# ────────────────────────────────────────────────────────────────


class TestConstantsExposed:
    def test_fetch_workers_constant_exists_and_reasonable(self):
        from engine import background_tasks as bg
        assert hasattr(bg, "HEATMAP_FETCH_WORKERS")
        # 2..16 is a sane band — too few = no speedup, too many = rate-limit
        assert 2 <= bg.HEATMAP_FETCH_WORKERS <= 16

    def test_per_ticker_timeout_exists(self):
        from engine import background_tasks as bg
        assert hasattr(bg, "HEATMAP_PER_TICKER_TIMEOUT")
        # Must be > 0 and shorter than the overall budget — otherwise
        # the budget would never bite first.
        assert 0 < bg.HEATMAP_PER_TICKER_TIMEOUT < bg.HEATMAP_FETCH_BUDGET_SEC

    def test_budget_constant_exists(self):
        from engine import background_tasks as bg
        assert hasattr(bg, "HEATMAP_FETCH_BUDGET_SEC")
        # 30s on 108 tickers @ 8 workers = comfortable headroom
        assert bg.HEATMAP_FETCH_BUDGET_SEC >= 15


# ────────────────────────────────────────────────────────────────
# Parallelism contract — verified by mocking borsapy
# ────────────────────────────────────────────────────────────────


class _FakeFastInfo:
    """Mimics borsapy fast_info — slow .last_price access simulates
    network latency so we can observe parallelism."""
    def __init__(self, ticker: str, delay: float = 0.1):
        self._t = ticker
        self._delay = delay

    @property
    def last_price(self):
        time.sleep(self._delay)
        # Deterministic price per ticker
        return 10.0 + (sum(ord(c) for c in self._t) % 50)

    @property
    def previous_close(self):
        return 9.5 + (sum(ord(c) for c in self._t) % 50)

    @property
    def market_cap(self):
        return 1e9


class _FakeTicker:
    def __init__(self, t: str, delay: float = 0.1):
        self.fast_info = _FakeFastInfo(t, delay=delay)


class TestParallelism:
    def test_runs_in_parallel_not_sequential(self, monkeypatch):
        """The core perf claim: 8 tickers × 0.1s sequential would take
        0.8s; with 8 workers it should take ~0.1-0.2s."""
        # Stub UNIVERSE to a small list so the test is fast
        from engine import background_tasks as bg
        monkeypatch.setattr(bg, "UNIVERSE", ["AAA", "BBB", "CCC", "DDD",
                                              "EEE", "FFF", "GGG", "HHH"])
        monkeypatch.setattr(bg, "BORSAPY_AVAILABLE", True)
        monkeypatch.setattr(bg, "get_top10_items", lambda: [])

        # Stub borsapy in sys.modules
        import types
        fake_bp = types.ModuleType("borsapy")
        fake_bp.Ticker = lambda t: _FakeTicker(t, delay=0.1)
        monkeypatch.setitem(sys.modules, "borsapy", fake_bp)

        t0 = time.time()
        results = bg._fetch_heatmap_data()
        elapsed = time.time() - t0

        # All 8 results should come back
        assert len(results) == 8
        # 8 workers × 0.1s each → wall time should be < 0.5s
        # (well under the 0.8s sequential baseline)
        assert elapsed < 0.5, f"Heatmap fetch took {elapsed:.2f}s — not parallel?"

    def test_returns_partial_on_per_ticker_failure(self, monkeypatch):
        """One ticker raising must not poison the whole batch."""
        from engine import background_tasks as bg
        monkeypatch.setattr(bg, "UNIVERSE", ["GOOD", "BAD", "GOOD2"])
        monkeypatch.setattr(bg, "BORSAPY_AVAILABLE", True)
        monkeypatch.setattr(bg, "get_top10_items", lambda: [])

        def _ticker_factory(t):
            if t == "BAD":
                raise RuntimeError("borsapy 502")
            return _FakeTicker(t, delay=0.01)

        import types
        fake_bp = types.ModuleType("borsapy")
        fake_bp.Ticker = _ticker_factory
        monkeypatch.setitem(sys.modules, "borsapy", fake_bp)

        results = bg._fetch_heatmap_data()
        tickers = {r["ticker"] for r in results}
        assert "BAD" not in tickers
        assert "GOOD" in tickers
        assert "GOOD2" in tickers

    def test_per_ticker_timeout_doesnt_block_others(self, monkeypatch):
        """A single hung ticker (>6s sleep) must not block the others.
        The total elapsed should be close to the per-ticker timeout, not
        108x the timeout."""
        from engine import background_tasks as bg
        # Use 3 fast + 1 slow ticker to keep the test runtime reasonable
        monkeypatch.setattr(bg, "UNIVERSE", ["FAST1", "SLOW", "FAST2", "FAST3"])
        monkeypatch.setattr(bg, "BORSAPY_AVAILABLE", True)
        monkeypatch.setattr(bg, "get_top10_items", lambda: [])
        # Trim timeouts so the test runs in <2s
        monkeypatch.setattr(bg, "HEATMAP_PER_TICKER_TIMEOUT", 1)
        monkeypatch.setattr(bg, "HEATMAP_FETCH_BUDGET_SEC", 5)

        def _factory(t):
            delay = 3.0 if t == "SLOW" else 0.05
            return _FakeTicker(t, delay=delay)

        import types
        fake_bp = types.ModuleType("borsapy")
        fake_bp.Ticker = _factory
        monkeypatch.setitem(sys.modules, "borsapy", fake_bp)

        t0 = time.time()
        results = bg._fetch_heatmap_data()
        elapsed = time.time() - t0

        # Fast tickers all should have returned
        tickers = {r["ticker"] for r in results}
        assert "FAST1" in tickers
        assert "FAST2" in tickers
        assert "FAST3" in tickers
        # The slow one might or might not appear depending on whether
        # it finishes before the budget. Either way is acceptable; the
        # key contract is wall-time stays bounded.
        assert elapsed < bg.HEATMAP_FETCH_BUDGET_SEC + 1, (
            f"Heatmap fetch took {elapsed:.2f}s — budget should have capped it"
        )


# ────────────────────────────────────────────────────────────────
# Return-contract preservation — _build_heatmap_result still works
# ────────────────────────────────────────────────────────────────


class TestReturnContract:
    def test_row_shape_unchanged(self, monkeypatch):
        """Stage 4 changes the loop but must not change the row shape —
        downstream _build_heatmap_result expects ticker/price/change_pct/
        market_cap/sector/score keys."""
        from engine import background_tasks as bg
        monkeypatch.setattr(bg, "UNIVERSE", ["XYZ"])
        monkeypatch.setattr(bg, "BORSAPY_AVAILABLE", True)
        monkeypatch.setattr(bg, "get_top10_items", lambda: [
            {"ticker": "XYZ", "sector": "Banka", "overall": 87.5}
        ])
        import types
        fake_bp = types.ModuleType("borsapy")
        fake_bp.Ticker = lambda t: _FakeTicker(t, delay=0.01)
        monkeypatch.setitem(sys.modules, "borsapy", fake_bp)

        results = bg._fetch_heatmap_data()
        assert len(results) == 1
        row = results[0]
        for k in ("ticker", "price", "change_pct", "market_cap",
                  "sector", "score"):
            assert k in row, f"row missing key {k!r}"
        assert row["ticker"] == "XYZ"
        assert row["sector"] == "Banka"
        assert row["score"] == 87.5

    def test_empty_universe_returns_empty(self, monkeypatch):
        from engine import background_tasks as bg
        monkeypatch.setattr(bg, "UNIVERSE", [])
        monkeypatch.setattr(bg, "BORSAPY_AVAILABLE", True)
        monkeypatch.setattr(bg, "get_top10_items", lambda: [])
        results = bg._fetch_heatmap_data()
        assert results == []

    def test_borsapy_unavailable_returns_empty(self, monkeypatch):
        from engine import background_tasks as bg
        monkeypatch.setattr(bg, "UNIVERSE", ["AAA"])
        monkeypatch.setattr(bg, "BORSAPY_AVAILABLE", False)
        monkeypatch.setattr(bg, "get_top10_items", lambda: [])
        results = bg._fetch_heatmap_data()
        assert results == []


# ────────────────────────────────────────────────────────────────
# Build step still composes correctly with the new fetcher
# ────────────────────────────────────────────────────────────────


class TestBuildStepIntegration:
    def test_build_consumes_new_fetcher_output(self):
        """Downstream _build_heatmap_result must accept the new fetcher's
        output verbatim — full contract test."""
        from engine.background_tasks import _build_heatmap_result
        sample = [
            {"ticker": "A", "price": 10.0, "change_pct": 1.5,
             "market_cap": 1e9, "sector": "Banka", "score": 80.0},
            {"ticker": "B", "price": 20.0, "change_pct": -2.0,
             "market_cap": 5e8, "sector": "Banka", "score": 70.0},
            {"ticker": "C", "price": 5.0, "change_pct": 3.0,
             "market_cap": 2e9, "sector": "Sanayi", "score": 60.0},
        ]
        out = _build_heatmap_result(sample)
        assert "sectors" in out
        assert out["total"] == 3
        # Sectors sorted by avg_change desc
        secs = {s["sector"] for s in out["sectors"]}
        assert "Banka" in secs
        assert "Sanayi" in secs

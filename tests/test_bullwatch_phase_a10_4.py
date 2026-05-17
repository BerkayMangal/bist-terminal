"""Tests for BullWatch v2 Phase A.10 Step 2-B.1 — Scan runtime hardening:
   - retry profile reduced to 2 attempts × (0.3, 0.7)s
   - per-symbol timeout (8s) records timeouts cleanly
   - scan diagnostics (cancelled/timeout symbols, avg/p95) populated
   - stale-while-revalidate end-to-end proof (no 12h wait needed)
   - no v1 regression
   - no scoring regression
"""
from __future__ import annotations

import os, sys, time, threading
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

import pytest


# ============================================================
# 1. Retry profile constants
# ============================================================
class TestRetryProfile:
    # NOTE: the original Phase A.10 "cheap retry" profile (2 attempts,
    # (0.3, 0.7)s backoff) was deliberately reversed by PR #93. At the
    # 622-stock full-BIST universe borsapy throttles under load;
    # surviving the throttle needs MORE retries with longer backoff,
    # not fewer. These tests now guard the throttle-survival profile.
    def test_max_attempts_throttle_survival(self):
        from data.providers import FETCH_RAW_MAX_ATTEMPTS
        assert FETCH_RAW_MAX_ATTEMPTS >= 3

    def test_backoff_profile(self):
        from data.providers import FETCH_RAW_BACKOFF_SEC
        # PR #93 throttle-survival profile.
        assert len(FETCH_RAW_BACKOFF_SEC) >= 3
        assert FETCH_RAW_BACKOFF_SEC == (0.5, 1.5, 3.0)

    def test_backoff_gives_borsapy_room(self):
        """The final backoff must be long enough (≥2s) that a throttled
        borsapy symbol gets real recovery time before the last attempt."""
        from data.providers import FETCH_RAW_BACKOFF_SEC
        assert FETCH_RAW_BACKOFF_SEC[-1] >= 2.0


# ============================================================
# 2. Scan stats module
# ============================================================
class TestScanStatsModule:
    def test_get_scan_stats_returns_dict_with_keys(self):
        from engine.bullwatch import get_scan_stats
        s = get_scan_stats()
        assert isinstance(s, dict)
        for k in (
            "last_scan_started_at",
            "last_scan_completed_at",
            "last_scan_duration_sec",
            "last_scan_total",
            "last_scan_done",
            "last_scan_cancelled_count",
            "last_scan_cancelled_symbols",
            "last_scan_timeout_count",
            "last_scan_timeout_symbols",
            "last_scan_budget_sec",
            "last_scan_avg_symbol_ms",
            "last_scan_p95_symbol_ms",
            "last_scan_per_symbol_timeout_sec",
        ):
            assert k in s, f"missing key {k}"

    def test_per_symbol_timeout_default_is_8(self):
        from engine.bullwatch import PER_SYMBOL_TIMEOUT_SEC
        assert PER_SYMBOL_TIMEOUT_SEC == 8

    def test_record_helpers_cap_lists(self):
        from engine.bullwatch import (
            _record_scan_cancelled, _record_scan_timeout, _SCAN_STATS,
            _SCAN_STATS_LIST_CAP, _reset_scan_stats,
        )
        _reset_scan_stats(total=200, budget_sec=10)
        # Push more than the cap
        for i in range(_SCAN_STATS_LIST_CAP + 50):
            _record_scan_cancelled(f"X{i}")
            _record_scan_timeout(f"Y{i}")
        # Counts increment fully
        assert _SCAN_STATS["last_scan_cancelled_count"] == _SCAN_STATS_LIST_CAP + 50
        assert _SCAN_STATS["last_scan_timeout_count"] == _SCAN_STATS_LIST_CAP + 50
        # But the list itself is capped
        assert len(_SCAN_STATS["last_scan_cancelled_symbols"]) == _SCAN_STATS_LIST_CAP
        assert len(_SCAN_STATS["last_scan_timeout_symbols"]) == _SCAN_STATS_LIST_CAP

    def test_finalize_computes_avg_and_p95(self):
        from engine.bullwatch import _finalize_scan_stats, _SCAN_STATS, _reset_scan_stats
        _reset_scan_stats(total=100, budget_sec=10)
        # Synthesize 100 timings: 50× 100ms, 50× 1000ms
        timings = [100.0] * 50 + [1000.0] * 50
        _finalize_scan_stats(timings)
        assert _SCAN_STATS["last_scan_avg_symbol_ms"] == 550.0
        # p95 of [100×50 + 1000×50] sorted = the 95th index (94 in 0-indexed) → 1000
        assert _SCAN_STATS["last_scan_p95_symbol_ms"] == 1000.0

    def test_finalize_handles_empty_timings(self):
        from engine.bullwatch import _finalize_scan_stats, _SCAN_STATS, _reset_scan_stats
        _reset_scan_stats(total=0, budget_sec=10)
        _finalize_scan_stats([])
        assert _SCAN_STATS["last_scan_avg_symbol_ms"] is None
        assert _SCAN_STATS["last_scan_p95_symbol_ms"] is None


# ============================================================
# 3. Per-symbol timeout — synthetic via scan()
# ============================================================
def _ohlcv(closes, vol=600_000):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.bdate_range(end="2026-05-08", periods=n)
    return pd.DataFrame({
        "Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
        "Close": closes, "Volume": np.full(n, vol),
    }, index=idx)


class TestPerSymbolTimeout:
    """Verify that a hung metrics_fn doesn't block the whole scan and is
    recorded as a timeout in scan stats."""

    def test_hanging_symbol_recorded_as_timeout(self):
        """One symbol's metrics_fn hangs for >8s; scan() must move on,
        and the slow symbol must appear in last_scan_timeout_symbols."""
        from engine import bullwatch as eng

        # Override the per-symbol timeout to something short for the test
        original_timeout = eng.PER_SYMBOL_TIMEOUT_SEC
        eng.PER_SYMBOL_TIMEOUT_SEC = 1  # 1s for test speed

        try:
            def slow_metrics(sym):
                if sym == "SLOW":
                    time.sleep(3)  # exceeds 1s timeout
                return {"symbol": sym, "ticker": sym,
                        "market_cap": 1.5e9, "free_float": 0.35,
                        "shares": 60e6}

            df = _ohlcv(np.linspace(10, 11, 80))

            results = eng.scan(
                ["A", "B", "SLOW", "C"],
                metrics_fn=slow_metrics,
                history_fn=lambda syms: {s: df for s in syms},
                ownership_fn=lambda s: None,
                max_workers=2,
            )
            stats = eng.get_scan_stats()
            assert stats["last_scan_timeout_count"] >= 1
            assert "SLOW" in stats["last_scan_timeout_symbols"]
            # Other symbols still got scored
            symbols_seen = {r.symbol for r in results}
            assert "A" in symbols_seen or "B" in symbols_seen or "C" in symbols_seen
        finally:
            eng.PER_SYMBOL_TIMEOUT_SEC = original_timeout

    def test_normal_scan_records_zero_timeouts(self):
        """Healthy symbols → 0 timeouts."""
        from engine import bullwatch as eng

        def fast_metrics(sym):
            return {"symbol": sym, "ticker": sym,
                    "market_cap": 1.5e9, "free_float": 0.35,
                    "shares": 60e6}

        df = _ohlcv(np.linspace(10, 11, 80))
        eng.scan(
            ["A", "B", "C"],
            metrics_fn=fast_metrics,
            history_fn=lambda syms: {s: df for s in syms},
            ownership_fn=lambda s: None,
            max_workers=2,
        )
        stats = eng.get_scan_stats()
        assert stats["last_scan_timeout_count"] == 0
        assert stats["last_scan_done"] == 3

    def test_scan_records_duration(self):
        from engine import bullwatch as eng

        def fast_metrics(sym):
            return {"symbol": sym, "market_cap": 1e9,
                    "free_float": 0.4, "shares": 50e6}

        df = _ohlcv(np.linspace(10, 11, 80))
        eng.scan(
            ["A", "B"],
            metrics_fn=fast_metrics,
            history_fn=lambda syms: {s: df for s in syms},
            ownership_fn=lambda s: None,
            max_workers=2,
        )
        stats = eng.get_scan_stats()
        assert stats["last_scan_duration_sec"] is not None
        assert stats["last_scan_duration_sec"] >= 0


# ============================================================
# 4. Scan stats reset between runs
# ============================================================
class TestScanStatsReset:
    def test_consecutive_scans_reset_lists(self):
        from engine import bullwatch as eng
        from engine.bullwatch import _record_scan_timeout, _SCAN_STATS, _reset_scan_stats

        _reset_scan_stats(total=10, budget_sec=10)
        _record_scan_timeout("OLD1")
        _record_scan_timeout("OLD2")
        assert _SCAN_STATS["last_scan_timeout_count"] == 2

        # New scan resets
        _reset_scan_stats(total=20, budget_sec=20)
        assert _SCAN_STATS["last_scan_timeout_count"] == 0
        assert _SCAN_STATS["last_scan_timeout_symbols"] == []
        assert _SCAN_STATS["last_scan_total"] == 20
        assert _SCAN_STATS["last_scan_budget_sec"] == 20


# ============================================================
# 5. Stale-while-revalidate — synthetic E2E proof
# ============================================================
class TestStaleFallbackE2E:
    """Synthetic proof that stale fallback works end-to-end without
    waiting 12 hours for a natural cache expiry. Validates the entire
    chain: provider failure → cache lookup → stale entry served →
    correct diagnostic fields → counter increments."""

    @pytest.fixture(autouse=True)
    def reset_cache_stats(self):
        from data.bullwatch_cache import _STATS
        snapshot = dict(_STATS)
        for k in list(_STATS.keys()):
            _STATS[k] = 0
        yield
        _STATS.clear()
        _STATS.update(snapshot)

    def test_provider_fail_plus_stale_returns_stale_complete(self):
        """The headline: provider down + stale cache → stale data with
        all expected fields."""
        from data.bullwatch_cache import (
            cached_compute_metrics, _STATS, CACHE_TTL_SEC,
        )

        ago = time.time() - (CACHE_TTL_SEC + 7200)  # 14h ago = stale
        stale = {
            "market_cap": 1.27e10, "free_float": 0.35, "shares": 254e6,
            "sector": "Industrials", "ticker": "KAPLM",
            "_cached_at": ago,
            "_field_sources": {
                "market_cap": "borsapy.fast_info",
                "free_float": "borsapy.fast_info",
                "shares": "borsapy.fast_info",
            },
        }
        with patch("data.bullwatch_cache._cache_get", return_value=stale), \
             patch("data.bullwatch_cache._cache_set"), \
             patch("data.providers.compute_metrics_v9",
                   side_effect=TimeoutError("provider down")):
            r = cached_compute_metrics("KAPLM")

        # All expected diagnostic fields present
        assert r["_data_status"] == "stale"
        assert r["_provider_used"] == "stale_cache"
        assert r["_cache_age_seconds"] is not None
        assert r["_cache_age_seconds"] > CACHE_TTL_SEC
        assert r["_last_success_at"] == ago
        # Provider error captured
        assert r["_provider_errors"]
        assert r["_provider_errors"][0]["error_type"] == "timeout"
        # Field sources preserved through stale path
        assert r["_field_sources"]["market_cap"] == "borsapy.fast_info"
        # Counters incremented
        assert _STATS["stale_cache_used_count"] == 1
        assert _STATS["data_status_stale"] == 1
        assert _STATS["borsapy_timeout_error"] == 1

    def test_provider_fail_no_cache_raises(self):
        """No fallback path: empty cache + provider fail → propagate."""
        from data.bullwatch_cache import cached_compute_metrics
        with patch("data.bullwatch_cache._cache_get", return_value=None), \
             patch("data.providers.compute_metrics_v9",
                   side_effect=TimeoutError("dead")):
            with pytest.raises(TimeoutError):
                cached_compute_metrics("DOOMED")

    def test_stale_fallback_preserves_override(self):
        """Even on stale path, manual overrides are re-applied. KAPLM has
        free_float manual override; if cache lacks it (legacy entry),
        the stale fallback should still surface the override."""
        from data.bullwatch_cache import cached_compute_metrics, CACHE_TTL_SEC

        ago = time.time() - (CACHE_TTL_SEC + 1000)
        stale_no_override = {
            "market_cap": 1.27e10, "free_float": None,  # not yet overridden
            "shares": 254e6, "_cached_at": ago,
        }
        with patch("data.bullwatch_cache._cache_get",
                   return_value=stale_no_override), \
             patch("data.bullwatch_cache._cache_set"), \
             patch("data.providers.compute_metrics_v9",
                   side_effect=TimeoutError("dead")):
            r = cached_compute_metrics("KAPLM")
        assert r["_data_status"] == "stale"
        # Override applied through stale path
        assert r["free_float"] == 0.35
        assert r["override_applied"] is True


# ============================================================
# 6. No regression: scoring + UI fields preserved
# ============================================================
class TestNoRegression:
    def test_score_symbol_unchanged_with_normal_input(self):
        """Pure score_symbol call — Step 2-B.1 must not change scoring."""
        from engine.bullwatch import score_symbol

        df = _ohlcv(np.linspace(10, 11, 80), 600_000)
        m = {"symbol": "T", "ticker": "T",
             "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8}
        r = score_symbol(m, df=df)
        # Basic invariants: result valid, has all the diagnostic fields
        assert r is not None
        assert 0 <= r.score <= 100
        assert r.zone in ("EARLY", "CONFIRMED", "CONVICTION")
        # Step 2-A.2 fields preserved
        assert r.cycle_state in (
            "TOPLANIYOR", "ATEŞLENİYOR", "DAĞITIM RİSKİ",
            "BOŞALTIYOR", "BELİRSİZ"
        )

    def test_scan_returns_results_in_score_order(self):
        """scan() output ordering invariant must hold."""
        from engine import bullwatch as eng

        def metrics(sym):
            # Different scores via different float pressure
            ff = {"A": 0.1, "B": 0.3, "C": 0.5}.get(sym, 0.4)
            return {"symbol": sym, "ticker": sym,
                    "market_cap": 1.5e9, "free_float": ff,
                    "shares": 60e6}

        df = _ohlcv(np.linspace(10, 11.5, 80), 1_500_000)
        results = eng.scan(
            ["A", "B", "C"],
            metrics_fn=metrics,
            history_fn=lambda syms: {s: df for s in syms},
            ownership_fn=lambda s: None,
            max_workers=2,
        )
        # Sorted by score desc within eligibles
        eligibles = [r for r in results if r.eligible]
        for i in range(len(eligibles) - 1):
            assert eligibles[i].score >= eligibles[i+1].score


# ============================================================
# 7. Synthetic runtime simulation
# ============================================================
class TestRuntimeSimulation:
    """Estimate before/after runtime for the typical 'few-stragglers'
    case (~9 symbols × 3.5s old vs 9 × 0.7s new + per-symbol timeout cap)."""

    def test_old_retry_cost_modeled(self):
        OLD_BACKOFF = (0.5, 1.0, 2.0)
        OLD_ATTEMPTS = 3
        # Per failed symbol: sum of backoffs (only attempts > 0 sleep)
        # OLD profile: attempts 0,1,2 → sleeps 0.5 + 1.0 = wait, not quite.
        # The code: for attempt in range(MAX_ATTEMPTS):
        #             if attempt > 0: sleep(BACKOFF[min(attempt, len-1)])
        # So at attempt=1: sleep BACKOFF[1] = 1.0
        #    at attempt=2: sleep BACKOFF[2] = 2.0
        # Total sleep per failed symbol = 1.0 + 2.0 = 3.0s
        old_total = sum(OLD_BACKOFF[1:OLD_ATTEMPTS])
        assert old_total == 3.0

    def test_retry_cost_modeled(self):
        from data.providers import FETCH_RAW_MAX_ATTEMPTS, FETCH_RAW_BACKOFF_SEC
        # PR #93 throttle-survival profile: 3 attempts → 2 retry sleeps
        # (BACKOFF[1] + BACKOFF[2]) for a symbol that exhausts retries.
        # Trades per-symbol speed for scan completeness under throttle.
        worst = sum(FETCH_RAW_BACKOFF_SEC[1:FETCH_RAW_MAX_ATTEMPTS])
        assert worst == 4.5


# ============================================================
# 8. Health endpoint integration (synthetic)
# ============================================================
class TestHealthEndpointIntegration:
    def test_scan_diagnostics_safe_returns_dict(self):
        """Smoke that the health endpoint helper doesn't crash."""
        from api.bullwatch import _scan_diagnostics_safe
        d = _scan_diagnostics_safe()
        assert isinstance(d, dict)
        # Either real stats or graceful error
        assert "error" in d or "last_scan_total" in d

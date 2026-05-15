"""Tests for the BullWatch metrics cache layer."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from unittest.mock import patch

from data.bullwatch_cache import (
    _apply_sanity, _apply_overrides, _is_bad_market_cap,
    _is_bad_free_float, _is_bad_revenue, _is_bad_shares_outstanding,
    _KNOWN_OVERRIDES, cached_compute_metrics, get_stats,
)


# ============================================================
# SANITY RULES — drop OBVIOUSLY bad values
# ============================================================
class TestSanityMarketCap:
    def test_positive_passes(self):
        assert _is_bad_market_cap(1_000_000_000) is False
        assert _is_bad_market_cap(50_000) is False

    def test_none_passes(self):
        assert _is_bad_market_cap(None) is False

    def test_zero_or_negative_rejected(self):
        assert _is_bad_market_cap(0) is True
        assert _is_bad_market_cap(-1_000_000) is True

    def test_absurd_value_rejected(self):
        assert _is_bad_market_cap(1e15) is True  # 1 quadrillion TL impossible

    def test_non_numeric_rejected(self):
        assert _is_bad_market_cap("abc") is True
        assert _is_bad_market_cap([1, 2]) is True


class TestSanityFreeFloat:
    def test_fraction_form_passes(self):
        assert _is_bad_free_float(0.30) is False
        assert _is_bad_free_float(0.001) is False
        assert _is_bad_free_float(1.0) is False  # 100% exactly

    def test_percentage_form_passes(self):
        assert _is_bad_free_float(30.0) is False
        assert _is_bad_free_float(7.1) is False  # ICBCT case
        assert _is_bad_free_float(100.0) is False

    def test_above_100_rejected(self):
        # Real bug: yfinance returned 18.9 for GLCVY → would be 1890%
        assert _is_bad_free_float(150) is True
        assert _is_bad_free_float(1890) is True
        assert _is_bad_free_float(710) is True

    def test_zero_or_negative_rejected(self):
        assert _is_bad_free_float(0) is True
        assert _is_bad_free_float(-0.5) is True


class TestSanityRevenue:
    def test_positive_passes(self):
        assert _is_bad_revenue(500_000_000) is False

    def test_zero_passes(self):
        # New companies, holdings — zero revenue is plausible
        assert _is_bad_revenue(0) is False

    def test_negative_rejected(self):
        # Revenue is gross sales; can't be negative (vs net income which can)
        assert _is_bad_revenue(-100) is True


class TestSanitySharesOutstanding:
    def test_positive_passes(self):
        assert _is_bad_shares_outstanding(1_000_000_000) is False

    def test_zero_or_negative_rejected(self):
        assert _is_bad_shares_outstanding(0) is True
        assert _is_bad_shares_outstanding(-100) is True

    def test_absurd_rejected(self):
        assert _is_bad_shares_outstanding(1e16) is True


class TestApplySanity:
    def test_drops_bad_fields_only(self):
        metrics = {
            "market_cap": 1_000_000_000,
            "free_float": 1890,        # bad — would render as 189000%
            "revenue": 500_000_000,
            "shares_outstanding": 1e9,
        }
        result = _apply_sanity(metrics, "TEST")
        assert result["market_cap"] == 1_000_000_000      # kept
        assert result["free_float"] is None               # dropped
        assert result["revenue"] == 500_000_000           # kept
        assert result["shares_outstanding"] == 1e9        # kept

    def test_passes_through_when_all_good(self):
        metrics = {
            "market_cap": 1_000_000_000,
            "free_float": 0.30,
            "revenue": 500_000_000,
            "shares_outstanding": 1e9,
        }
        result = _apply_sanity(metrics, "TEST")
        assert result["free_float"] == 0.30  # unchanged

    def test_handles_missing_fields(self):
        metrics = {"market_cap": 1e9}  # other fields absent
        result = _apply_sanity(metrics, "TEST")
        # Should not blow up; missing fields stay missing
        assert result == {"market_cap": 1e9}


# ============================================================
# MANUAL OVERRIDES — known yfinance bugs hard-corrected
# ============================================================
class TestApplyOverrides:
    def test_icbct_free_float_corrected(self):
        # We know ICBCT comes back as 7.1 from yfinance, real value 0.71
        metrics = {"free_float": 7.1, "market_cap": 1e10}
        result = _apply_overrides(metrics, "ICBCT")
        assert result["free_float"] == 0.71
        assert result["market_cap"] == 1e10  # untouched

    def test_glcvy_free_float_corrected(self):
        metrics = {"free_float": 18.9}
        result = _apply_overrides(metrics, "GLCVY")
        assert result["free_float"] == 0.25

    def test_unknown_ticker_passes_through(self):
        metrics = {"free_float": 0.42}
        result = _apply_overrides(metrics, "RANDOM")
        assert result["free_float"] == 0.42  # untouched

    def test_handles_is_suffix(self):
        # yfinance often appends ".IS" to BIST tickers
        metrics = {"free_float": 7.1}
        result = _apply_overrides(metrics, "ICBCT.IS")
        assert result["free_float"] == 0.71


# ============================================================
# CACHE INTEGRATION
# ============================================================
class TestCachedComputeMetrics:
    def test_miss_calls_provider_then_caches(self):
        fake_metrics = {"market_cap": 1e9, "free_float": 0.3, "sector": "Industrials"}
        with patch("data.bullwatch_cache._cache_get", return_value=None) as mock_get, \
             patch("data.bullwatch_cache._cache_set") as mock_set, \
             patch("data.providers.compute_metrics_v9", return_value=fake_metrics):
            result = cached_compute_metrics("TESTSYM")
            mock_get.assert_called_once()
            mock_set.assert_called_once()
            assert result["market_cap"] == 1e9
            assert result["free_float"] == 0.3

    def test_hit_skips_provider(self):
        cached = {"market_cap": 5e8, "free_float": 0.4, "sector": "Tech"}
        with patch("data.bullwatch_cache._cache_get", return_value=cached), \
             patch("data.providers.compute_metrics_v9") as mock_provider:
            result = cached_compute_metrics("TESTSYM")
            mock_provider.assert_not_called()
            assert result["market_cap"] == 5e8

    def test_miss_applies_sanity_before_cache(self):
        bad_metrics = {"market_cap": 1e9, "free_float": 1890}  # bad ff
        with patch("data.bullwatch_cache._cache_get", return_value=None), \
             patch("data.bullwatch_cache._cache_set") as mock_set, \
             patch("data.providers.compute_metrics_v9", return_value=bad_metrics):
            result = cached_compute_metrics("TESTSYM")
            # Bad free_float should be dropped before storage
            assert result["free_float"] is None
            # Cache should have stored the cleaned version
            cached_arg = mock_set.call_args[0][1]
            assert cached_arg["free_float"] is None

    def test_hit_reapplies_overrides_defensively(self):
        # If cache was populated before _KNOWN_OVERRIDES was edited,
        # current call should still benefit from the new override.
        stale_cached = {"market_cap": 1e10, "free_float": 7.1}
        with patch("data.bullwatch_cache._cache_get", return_value=stale_cached):
            result = cached_compute_metrics("ICBCT")
            assert result["free_float"] == 0.71  # override re-applied

    def test_provider_exception_propagates(self):
        with patch("data.bullwatch_cache._cache_get", return_value=None), \
             patch("data.providers.compute_metrics_v9", side_effect=RuntimeError("yfinance dead")):
            with pytest.raises(RuntimeError, match="yfinance dead"):
                cached_compute_metrics("TESTSYM")


# ============================================================
# STATS
# ============================================================
class TestStats:
    def test_get_stats_returns_dict(self):
        stats = get_stats()
        for key in ("hit", "miss", "error", "total_lookups", "ttl_sec", "redis_available"):
            assert key in stats

    def test_known_overrides_documented(self):
        # ICBCT and GLCVY must be in the override dict — these are the
        # production-confirmed bugs we've fixed.
        assert "ICBCT" in _KNOWN_OVERRIDES
        assert "GLCVY" in _KNOWN_OVERRIDES
        assert _KNOWN_OVERRIDES["ICBCT"]["free_float"] == 0.71
        assert _KNOWN_OVERRIDES["GLCVY"]["free_float"] == 0.25

"""Tests for BullWatch v2 Phase A.10 Step 2-A:
   - field source tagging
   - override_applied/source/fields stamping in production code path
   - data_status / missing_fields computation
   - cache layer routes through sanity + override
   - BullWatchResult exposes diagnostic fields
   - no v1 regression
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from engine.bullwatch import score_symbol, BullWatchResult
from data.bullwatch_cache import (
    _apply_overrides,
    _apply_sanity,
    _compute_data_status,
    _classify_borsapy_error,
    cached_compute_metrics,
    get_stats,
    _STATS,
    _KNOWN_OVERRIDES,
)


def _ohlcv(closes, vol=200_000):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.bdate_range(end="2026-05-08", periods=n)
    return pd.DataFrame({
        "Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
        "Close": closes, "Volume": np.full(n, vol),
    }, index=idx)


# ============================================================
# 1. _apply_overrides — Phase A.10 stamping
# ============================================================
class TestApplyOverridesStamping:
    def test_no_override_sym_sets_audit_fields_to_false(self):
        """Even symbols without overrides must have the audit fields set
        so consumers can rely on these keys."""
        m = {"market_cap": 1.5e9, "free_float": 0.4}
        result = _apply_overrides(m, "SOMETHING_NEW")
        assert result["override_applied"] is False
        assert result["override_source"] is None
        assert result["override_fields"] == []

    def test_kaplm_override_stamps_audit_fields(self):
        """KAPLM has free_float=0.35 override. Must stamp metadata + update
        _field_sources."""
        m = {"market_cap": 1.27e10, "free_float": None,
             "_field_sources": {"free_float": "missing"}}
        result = _apply_overrides(m, "KAPLM")
        assert result["override_applied"] is True
        assert result["override_source"] == "manual_override"
        assert "free_float" in result["override_fields"]
        assert result["free_float"] == 0.35
        assert result["_field_sources"]["free_float"] == "manual_override"

    def test_glrmk_override_stamps_audit_fields(self):
        m = {"market_cap": 6.5e10, "free_float": None}
        result = _apply_overrides(m, "GLRMK")
        assert result["override_applied"] is True
        assert "free_float" in result["override_fields"]

    def test_asels_override_stamps_audit_fields(self):
        m = {"market_cap": 1.95e12, "free_float": None}
        result = _apply_overrides(m, "ASELS")
        assert result["override_applied"] is True
        assert result["free_float"] == 0.2593

    def test_override_already_correct_does_not_stamp(self):
        """If the value already matches the override, no stamp happens
        (no DATA QUALITY log line, no override_fields entry)."""
        m = {"market_cap": 1.27e10, "free_float": 0.35}  # already correct
        result = _apply_overrides(m, "KAPLM")
        # override is in dict but value matched → no actual override
        # This test documents current behavior: when old==value, the
        # _field_sources isn't touched and override_fields stays empty.
        assert result["override_fields"] == []
        assert result["override_applied"] is False

    def test_override_sym_normalization(self):
        """Symbol matching must be case-insensitive + suffix-tolerant."""
        m = {"market_cap": 1.27e10, "free_float": None}
        result = _apply_overrides(m, "kaplm.is")
        assert result["override_applied"] is True
        assert result["free_float"] == 0.35


# ============================================================
# 2. _compute_data_status — partial/live/missing classification
# ============================================================
class TestDataStatus:
    def test_all_required_present_is_live(self):
        m = {"market_cap": 1.5e9, "free_float": 0.35, "shares": 60e6}
        status, missing = _compute_data_status(m)
        assert status == "live"
        assert missing == []

    def test_one_missing_is_partial(self):
        m = {"market_cap": 1.5e9, "free_float": None, "shares": 60e6}
        status, missing = _compute_data_status(m)
        assert status == "partial"
        assert "free_float" in missing

    def test_all_missing_is_missing(self):
        m = {"market_cap": None, "free_float": None, "shares": None}
        status, missing = _compute_data_status(m)
        assert status == "missing"
        assert set(missing) == {"market_cap", "free_float", "shares"}


# ============================================================
# 3. _classify_borsapy_error — error categorization
# ============================================================
class TestErrorClassification:
    def test_fast_info_error(self):
        assert _classify_borsapy_error(Exception("fast_info call timeout")) == "fast_info"

    def test_history_error(self):
        assert _classify_borsapy_error(Exception("history fetch failed")) == "history"

    def test_income_stmt_error(self):
        assert _classify_borsapy_error(Exception("income statement parse")) == "income_stmt"

    def test_unknown_error(self):
        assert _classify_borsapy_error(ValueError("random thing")) == "unknown"


# ============================================================
# 4. cached_compute_metrics — central diagnostic stamping
# ============================================================
class TestCachedComputeMetrics:
    """Mock the underlying compute_metrics_v9 to verify the cache layer
    correctly stamps diagnostics + applies override."""

    def _fake_metrics(self, **overrides):
        """Return a synthetic compute_metrics_v9-shaped dict."""
        base = {
            "symbol": "TEST", "ticker": "TEST", "market_cap": 1.5e9,
            "free_float": 0.35, "shares": 60e6, "revenue": 5e8,
            "_field_sources": {
                "market_cap": "borsapy.fast_info",
                "free_float": "borsapy.fast_info",
                "shares": "borsapy.fast_info",
                "revenue": "borsapy.income_stmt_ufrs",
            },
            "data_source": "borsapy",
            "data_quality": "high",
        }
        base.update(overrides)
        return base

    def test_kaplm_direct_call_no_longer_no_data(self):
        """The bug that prompted Step 2-A: /api/bullwatch/KAPLM was
        getting no_data because override layer was bypassed. Now the
        cache layer applies override → KAPLM should have free_float=0.35
        and override_applied=True."""
        with patch("data.providers.compute_metrics_v9") as mock_compute, \
             patch("data.bullwatch_cache._cache_get", return_value=None), \
             patch("data.bullwatch_cache._cache_set"):
            # borsapy returns market_cap but no free_float (the production bug)
            mock_compute.return_value = self._fake_metrics(
                symbol="KAPLM", ticker="KAPLM",
                market_cap=1.27e10, free_float=None,
                _field_sources={"market_cap": "borsapy.fast_info",
                                "free_float": "missing",
                                "shares": "borsapy.fast_info",
                                "revenue": "borsapy.income_stmt_ufrs"},
            )
            result = cached_compute_metrics("KAPLM")

        # Override should have been applied via the cache layer
        assert result["free_float"] == 0.35
        assert result["override_applied"] is True
        assert result["override_source"] == "manual_override"
        assert "free_float" in result["override_fields"]
        # _field_sources should reflect the override
        assert result["_field_sources"]["free_float"] == "manual_override"
        # Diagnostic fields stamped
        assert result["_data_status"] == "live"  # all required filled after override
        assert result["_provider_used"] == "borsapy"
        assert "_missing_fields" in result

    def test_glrmk_direct_call_no_longer_no_data(self):
        with patch("data.providers.compute_metrics_v9") as mock_compute, \
             patch("data.bullwatch_cache._cache_get", return_value=None), \
             patch("data.bullwatch_cache._cache_set"):
            mock_compute.return_value = self._fake_metrics(
                symbol="GLRMK", ticker="GLRMK",
                market_cap=6.5e10, free_float=None,
            )
            result = cached_compute_metrics("GLRMK")

        assert result["free_float"] == 0.35
        assert result["override_applied"] is True

    def test_asels_direct_call_override_applied(self):
        with patch("data.providers.compute_metrics_v9") as mock_compute, \
             patch("data.bullwatch_cache._cache_get", return_value=None), \
             patch("data.bullwatch_cache._cache_set"):
            mock_compute.return_value = self._fake_metrics(
                symbol="ASELS", ticker="ASELS",
                market_cap=1.95e12, free_float=None,
            )
            result = cached_compute_metrics("ASELS")

        assert result["free_float"] == 0.2593
        assert result["override_applied"] is True

    def test_partial_data_classified(self):
        """Symbol with market_cap but no free_float and no override
        should be data_status=partial, not live."""
        with patch("data.providers.compute_metrics_v9") as mock_compute, \
             patch("data.bullwatch_cache._cache_get", return_value=None), \
             patch("data.bullwatch_cache._cache_set"):
            mock_compute.return_value = self._fake_metrics(
                symbol="UNKNOWN_SMALL", ticker="UNKNOWN_SMALL",
                market_cap=2e9, free_float=None,  # no override for this sym
            )
            result = cached_compute_metrics("UNKNOWN_SMALL")

        assert result["_data_status"] == "partial"
        assert "free_float" in result["_missing_fields"]
        # Override fields still set even when no override
        assert result["override_applied"] is False
        assert result["override_fields"] == []


# ============================================================
# 5. BullWatchResult — diagnostic fields surfaced
# ============================================================
class TestBullWatchResultDiagnostics:
    def test_diagnostic_fields_default_none(self):
        r = BullWatchResult(symbol="X", score=0, zone="EARLY", pattern="?")
        assert r.data_status is None
        assert r.provider_used is None
        assert r.field_sources is None
        assert r.missing_fields is None
        assert r.provider_errors is None
        assert r.override_applied is None

    def test_score_symbol_passes_through_diagnostics(self):
        """When metrics_dict has the diagnostic fields, score_symbol
        must surface them on the returned BullWatchResult."""
        df = _ohlcv(np.linspace(10, 11, 80), 600_000)
        m = {"symbol": "T", "ticker": "T", "market_cap": 1.5e9,
             "free_float": 0.35, "shares": 60e6, "revenue": 5e8,
             # Phase A.10 diagnostic fields
             "_data_status": "live",
             "_provider_used": "cached_borsapy",
             "_field_sources": {"market_cap": "borsapy.fast_info",
                                "free_float": "manual_override"},
             "_missing_fields": [],
             "_provider_errors": [],
             "override_applied": True,
             "override_source": "manual_override",
             "override_fields": ["free_float"]}
        r = score_symbol(m, df=df)

        assert r.data_status == "live"
        assert r.provider_used == "cached_borsapy"
        assert r.field_sources["free_float"] == "manual_override"
        assert r.missing_fields == []
        assert r.override_applied is True
        assert r.override_source == "manual_override"
        assert r.override_fields == ["free_float"]

    def test_diagnostics_present_on_rejected_results(self):
        """Even ineligible symbols should surface diagnostics — they
        help debug WHY the symbol failed."""
        df = _ohlcv(np.linspace(50, 55, 80), 5e7)
        m = {"symbol": "BIG", "market_cap": 60e9, "free_float": 0.5,
             "shares": 5e9, "revenue": 1e10,
             "_data_status": "live",
             "_provider_used": "borsapy",
             "_field_sources": {"market_cap": "borsapy.fast_info"},
             "_missing_fields": [],
             "override_applied": False,
             "override_source": None,
             "override_fields": []}
        r = score_symbol(m, df=df)

        assert r.eligible is False
        assert r.universe_tier == "institutional"
        # Diagnostics still present
        assert r.data_status == "live"
        assert r.provider_used == "borsapy"
        assert r.override_applied is False

    def test_legacy_metrics_without_diagnostics_does_not_crash(self):
        """v1 callers that build metrics by hand without the new fields
        must still work — diagnostic fields just default to None."""
        df = _ohlcv(np.linspace(10, 11, 80), 600_000)
        m = {"symbol": "T", "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8}  # NO diagnostic fields
        r = score_symbol(m, df=df)
        # Should not crash
        assert r.data_status is None
        assert r.field_sources is None


# ============================================================
# 6. health endpoint structure (via get_stats)
# ============================================================
class TestHealthDiagnostics:
    def test_get_stats_returns_diagnostics_breakdown(self):
        stats = get_stats()
        assert "diagnostics" in stats
        d = stats["diagnostics"]
        # Expected breakdown keys
        assert "missing_fields" in d
        assert "borsapy_errors" in d
        assert "data_status_distribution" in d
        assert "override_applied_count" in d
        assert "stale_cache_used_count" in d
        # Sub-keys
        assert "ohlcv" in d["missing_fields"]
        assert "free_float" in d["missing_fields"]
        assert "fast_info" in d["borsapy_errors"]
        assert "history" in d["borsapy_errors"]
        assert "income_stmt" in d["borsapy_errors"]
        assert "live" in d["data_status_distribution"]
        assert "partial" in d["data_status_distribution"]
        assert "missing" in d["data_status_distribution"]

    def test_get_stats_backwards_compat(self):
        """Old keys still present so old dashboards don't break."""
        stats = get_stats()
        for k in ("hit", "miss", "error", "sanity_drop",
                  "total_lookups", "hit_pct", "ttl_sec",
                  "redis_available"):
            assert k in stats


# ============================================================
# 7. _apply_sanity (sanity check before override)
# ============================================================
class TestSanityStillWorks:
    def test_sanity_drops_bad_market_cap(self):
        m = {"market_cap": -1, "free_float": 0.3}
        result = _apply_sanity(m, "TEST")
        # market_cap is dropped to None on sanity
        assert result.get("market_cap") is None

    def test_sanity_does_not_drop_good_values(self):
        m = {"market_cap": 1.5e9, "free_float": 0.3}
        result = _apply_sanity(m, "TEST")
        assert result["market_cap"] == 1.5e9


# ============================================================
# 8. v1 regression check — explicit
# ============================================================
class TestNoV1Regression:
    def test_v1_callers_unchanged(self):
        """A standard v1 score_symbol call must produce the same shape
        of BullWatchResult as before. New fields default to None."""
        df = _ohlcv(np.linspace(10, 11, 80), 600_000)
        m = {"symbol": "T", "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8, "data_quality": "high"}
        r = score_symbol(m, df=df)
        assert r.symbol == "T"
        assert isinstance(r.score, (int, float))
        assert r.zone in ("EARLY", "CONFIRMED", "CONVICTION")
        assert isinstance(r.components, dict)
        assert isinstance(r.metrics, dict)
        # Phase A.6 still works
        assert r.universe_tier in ("core", "extended")
        # New fields are None (additive)
        assert r.data_status is None or r.data_status in ("live", "partial", "missing", "stale")

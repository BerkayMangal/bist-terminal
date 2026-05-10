"""Tests for BullWatch v2 Phase A.10 Step 2-B — Data hardening:
   - cache versioning (v3 prefix, schema stamp on stored entries)
   - stale-while-revalidate (provider fail + stale cache → return stale)
   - data_status="stale" + cache_age_seconds + last_success_at
   - missing_shares counter
   - sanity_drop records field-level reason
   - provider error subclassification (timeout, data_not_available)
   - health endpoint returns Step 2-B diagnostics
   - no v1 regression: data_status / field_sources / missing_fields preserved
   - AI quota CB trips fast + suppresses repeat logs
"""
from __future__ import annotations

import os, sys, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest.mock import patch, MagicMock

import pytest

from data.bullwatch_cache import (
    cached_compute_metrics,
    _classify_borsapy_error,
    _apply_sanity,
    _apply_overrides,
    _compute_data_status,
    _bump_status_counters,
    _record_sanity_drop,
    _SANITY_DROP_LOG,
    _STATS,
    get_stats,
    CACHE_KEY_PREFIX,
    CACHE_TTL_SEC,
    CACHE_SCHEMA_VERSION,
    STALE_GRAVE_SEC,
    _cache_age,
    _cache_set,
    _cache_get,
)


@pytest.fixture(autouse=True)
def reset_stats():
    """Reset _STATS + sanity log between tests so counters don't leak."""
    snapshot = dict(_STATS)
    drops = list(_SANITY_DROP_LOG)
    _SANITY_DROP_LOG.clear()
    for k in list(_STATS.keys()):
        _STATS[k] = 0
    yield
    _SANITY_DROP_LOG.clear()
    _SANITY_DROP_LOG.extend(drops)
    _STATS.clear()
    _STATS.update(snapshot)


# ============================================================
# 1. Cache versioning
# ============================================================
class TestCacheVersioning:
    def test_v3_prefix(self):
        assert CACHE_SCHEMA_VERSION == "v3"
        assert CACHE_KEY_PREFIX == "bullwatch:metrics:v3:"

    def test_old_v1_prefix_does_not_match(self):
        """Old v1 keys must not collide — different prefix path."""
        assert "v1" not in CACHE_KEY_PREFIX

    def test_cache_set_stamps_schema_version(self):
        captured = {}
        def fake_set_json(key, val, ttl=None):
            captured[key] = (val, ttl)
        with patch("data.bullwatch_cache.redis_client.is_available", return_value=True), \
             patch("data.bullwatch_cache.redis_client.set_json", side_effect=fake_set_json):
            _cache_set("FOO", {"market_cap": 1e9})
        # The stored value must carry the schema stamp + use STALE_GRAVE_SEC TTL
        key, (val, ttl) = next(iter(captured.items())), captured[next(iter(captured))]
        assert "v3" in next(iter(captured.keys()))
        stored, stored_ttl = list(captured.values())[0]
        assert stored.get("_cache_schema_version") == "v3"
        assert stored_ttl == STALE_GRAVE_SEC


# ============================================================
# 2. Stale-while-revalidate
# ============================================================
class TestStaleWhileRevalidate:
    def test_fresh_cache_returns_immediately_no_provider_call(self):
        """Cache hit with recent _cached_at → skip provider."""
        cached = {"market_cap": 5e8, "free_float": 0.4, "shares": 50e6,
                  "_cached_at": time.time()}  # fresh
        with patch("data.bullwatch_cache._cache_get", return_value=cached), \
             patch("data.providers.compute_metrics_v9") as mock_provider:
            r = cached_compute_metrics("FRESH")
            mock_provider.assert_not_called()
            assert r["_data_status"] == "live"
            assert r["_provider_used"] == "cached_borsapy"

    def test_stale_cache_plus_provider_failure_returns_stale(self):
        """The headline Step 2-B feature: provider crashes, but stale
        cache exists → serve stale with data_status='stale'."""
        ago = time.time() - (CACHE_TTL_SEC + 1000)  # past fresh window
        stale = {
            "market_cap": 1e9, "free_float": 0.35, "shares": 60e6,
            "_cached_at": ago,
        }
        with patch("data.bullwatch_cache._cache_get", return_value=stale), \
             patch("data.bullwatch_cache._cache_set"), \
             patch("data.providers.compute_metrics_v9",
                   side_effect=TimeoutError("provider down")):
            r = cached_compute_metrics("STALE")
            assert r["_data_status"] == "stale"
            assert r["_provider_used"] == "stale_cache"
            assert r["_cache_age_seconds"] is not None
            assert r["_cache_age_seconds"] > CACHE_TTL_SEC
            assert r["_last_success_at"] == ago
            assert r["_provider_errors"]
            assert r["_provider_errors"][0]["error_type"] == "timeout"

    def test_stale_cache_plus_fresh_success_returns_live(self):
        """Stale entry exists but provider works → fresh data, live status."""
        ago = time.time() - (CACHE_TTL_SEC + 1000)
        stale = {"market_cap": 1e9, "free_float": 0.35, "shares": 60e6,
                 "_cached_at": ago}
        fresh = {"market_cap": 1.1e9, "free_float": 0.36, "shares": 61e6}
        with patch("data.bullwatch_cache._cache_get", return_value=stale), \
             patch("data.bullwatch_cache._cache_set"), \
             patch("data.providers.compute_metrics_v9", return_value=fresh):
            r = cached_compute_metrics("REFRESHED")
            assert r["_data_status"] == "live"
            assert r["_provider_used"] == "borsapy"
            # Should be the fresh values, not stale ones
            assert r["market_cap"] == 1.1e9

    def test_no_cache_plus_provider_failure_raises(self):
        """No fallback path: cache empty AND provider fails → propagate."""
        with patch("data.bullwatch_cache._cache_get", return_value=None), \
             patch("data.providers.compute_metrics_v9",
                   side_effect=TimeoutError("dead")):
            with pytest.raises(TimeoutError):
                cached_compute_metrics("DOOMED")


# ============================================================
# 3. _bump_status_counters with shares
# ============================================================
class TestStatusCounters:
    def test_missing_shares_counter_increments(self):
        before = _STATS.get("missing_shares", 0)
        _bump_status_counters("partial", ["shares"])
        assert _STATS["missing_shares"] == before + 1

    def test_missing_market_cap_still_works(self):
        before = _STATS.get("missing_market_cap", 0)
        _bump_status_counters("partial", ["market_cap"])
        assert _STATS["missing_market_cap"] == before + 1

    def test_data_status_stale_counter(self):
        before = _STATS.get("data_status_stale", 0)
        _bump_status_counters("stale", [])
        assert _STATS["data_status_stale"] == before + 1


# ============================================================
# 4. Sanity drop field-level recording
# ============================================================
class TestSanityDropRecording:
    def test_drop_records_field_value_reason(self):
        before_n = len(_SANITY_DROP_LOG)
        bad = {"market_cap": 1e9, "free_float": 1890,  # 1890 = 189000% — bad
               "revenue": -100, "shares_outstanding": 60e6}
        _apply_sanity(bad, "TESTSYM")
        after_n = len(_SANITY_DROP_LOG)
        assert after_n > before_n
        # Last drops should include free_float and revenue
        recent = list(_SANITY_DROP_LOG)[-3:]
        fields_dropped = {d["field"] for d in recent}
        assert "free_float" in fields_dropped
        assert "revenue" in fields_dropped
        # Each drop has the required structure
        for d in recent:
            assert "symbol" in d
            assert "field" in d
            assert "original_value" in d
            assert "reason" in d
            assert d["symbol"] == "TESTSYM"

    def test_per_field_counter_increments(self):
        before = _STATS.get("sanity_drop_free_float", 0)
        _record_sanity_drop("X", "free_float", 1890, "sanity_rule_failed")
        assert _STATS["sanity_drop_free_float"] == before + 1
        assert _STATS["sanity_drop"] >= 1

    def test_drop_log_bounded_at_100(self):
        """Ring buffer caps at 100 entries to avoid memory leak."""
        for i in range(150):
            _record_sanity_drop(f"S{i}", "market_cap", -i, "test")
        assert len(_SANITY_DROP_LOG) == 100


# ============================================================
# 5. Provider error subclassification
# ============================================================
class TestProviderErrorClassification:
    def test_timeout_error_class(self):
        assert _classify_borsapy_error(TimeoutError("anything")) == "timeout"

    def test_timeout_in_message(self):
        # Generic Exception with "timeout" in message
        assert _classify_borsapy_error(Exception("connection timeout")) == "timeout"

    def test_data_not_available_class_name(self):
        class DataNotAvailableError(Exception):
            pass
        assert _classify_borsapy_error(
            DataNotAvailableError("No financial data available for X")
        ) == "data_not_available"

    def test_data_not_available_message(self):
        assert _classify_borsapy_error(
            Exception("No financial data available for SERVE")
        ) == "data_not_available"

    def test_subsystem_specific_overrides_timeout(self):
        """Existing 'fast_info call timeout' should still classify as
        fast_info (more specific) — preserves Hotfix17 semantics."""
        assert _classify_borsapy_error(
            Exception("fast_info call timeout")
        ) == "fast_info"

    def test_fast_info_alone(self):
        assert _classify_borsapy_error(
            Exception("fast_info fetch failed")
        ) == "fast_info"

    def test_unknown_falls_through(self):
        assert _classify_borsapy_error(
            Exception("something weird")
        ) == "unknown"


# ============================================================
# 6. Health endpoint Step 2-B diagnostics
# ============================================================
class TestHealthDiagnostics:
    def test_health_includes_missing_shares(self):
        s = get_stats()
        assert "missing_fields" in s["diagnostics"]
        assert "shares" in s["diagnostics"]["missing_fields"]

    def test_health_includes_stale_distribution(self):
        s = get_stats()
        assert "stale" in s["diagnostics"]["data_status_distribution"]

    def test_health_includes_sanity_drop_breakdown(self):
        s = get_stats()
        bd = s["diagnostics"]["sanity_drop_breakdown"]
        assert "market_cap" in bd
        assert "free_float" in bd
        assert "revenue" in bd
        assert "shares_outstanding" in bd

    def test_health_includes_recent_sanity_drops(self):
        _record_sanity_drop("AAA", "free_float", 5.0, "test")
        s = get_stats()
        assert any(d["symbol"] == "AAA" for d in s["diagnostics"]["recent_sanity_drops"])

    def test_health_includes_borsapy_subcategories(self):
        s = get_stats()
        be = s["diagnostics"]["borsapy_errors"]
        assert "timeout" in be
        assert "data_not_available" in be

    def test_health_includes_cache_schema_version(self):
        s = get_stats()
        assert s["diagnostics"]["cache_schema_version"] == "v3"

    def test_health_includes_stale_grave_window(self):
        s = get_stats()
        assert s["diagnostics"]["stale_grave_sec"] == STALE_GRAVE_SEC

    def test_top_level_stale_hit_count(self):
        s = get_stats()
        assert "stale_hit" in s


# ============================================================
# 7. Backwards compat — diagnostic fields still flow
# ============================================================
class TestBackwardsCompat:
    def test_data_status_still_set_on_fresh(self):
        """Step 2-A diagnostic field surfaces at v3 cache layer too."""
        m = {"market_cap": 1e9, "free_float": 0.35, "shares": 60e6}
        with patch("data.bullwatch_cache._cache_get", return_value=None), \
             patch("data.bullwatch_cache._cache_set"), \
             patch("data.providers.compute_metrics_v9", return_value=m):
            r = cached_compute_metrics("OK")
            assert r["_data_status"] == "live"
            assert r["_missing_fields"] == []
            assert r["_provider_used"] == "borsapy"
            assert "_provider_errors" in r

    def test_override_applied_field_still_set(self):
        m = {"market_cap": 1e9, "free_float": None, "shares": 60e6}
        with patch("data.bullwatch_cache._cache_get", return_value=None), \
             patch("data.bullwatch_cache._cache_set"), \
             patch("data.providers.compute_metrics_v9", return_value=m):
            # KAPLM has an override for free_float
            r = cached_compute_metrics("KAPLM")
            assert r["override_applied"] is True
            assert "free_float" in r["override_fields"]

    def test_field_sources_preserved_through_stale_path(self):
        """Stale fallback must still expose _field_sources stamps."""
        ago = time.time() - (CACHE_TTL_SEC + 1000)
        stale = {
            "market_cap": 1e9, "free_float": 0.35, "shares": 60e6,
            "_cached_at": ago,
            "_field_sources": {
                "market_cap": "borsapy.fast_info",
                "free_float": "borsapy.fast_info",
            },
        }
        with patch("data.bullwatch_cache._cache_get", return_value=stale), \
             patch("data.bullwatch_cache._cache_set"), \
             patch("data.providers.compute_metrics_v9",
                   side_effect=TimeoutError("dead")):
            r = cached_compute_metrics("X")
            assert r["_data_status"] == "stale"
            assert r["_field_sources"]["market_cap"] == "borsapy.fast_info"


# ============================================================
# 8. AI quota CB suppression
# ============================================================
class TestAIQuotaCB:
    def test_quota_error_trips_cb_immediately(self):
        from core.circuit_breaker import CircuitBreaker, CBState
        cb = CircuitBreaker(name="test_quota", failure_threshold=5)
        # Single quota error should trip the CB even if threshold>1
        cb.on_failure(Exception(
            "Error code: 401 - {'error': {'message': "
            "'You exceeded your current quota'}}"
        ))
        assert cb._state == CBState.OPEN

    def test_normal_429_rate_limit_does_not_trip(self):
        from core.circuit_breaker import CircuitBreaker, CBState
        cb = CircuitBreaker(name="test_rate", failure_threshold=5)
        # Bare 429 without quota wording is transient → no state change
        cb.on_failure(Exception("Error code: 429 - Too Many Requests"))
        assert cb._state == CBState.CLOSED

    def test_repeated_quota_errors_silent_after_first(self):
        """Subsequent quota failures while OPEN don't re-emit the OPEN log."""
        from core.circuit_breaker import CircuitBreaker
        import logging
        cb = CircuitBreaker(name="test_silent", failure_threshold=5)
        with patch.object(cb._log if hasattr(cb, "_log") else logging.getLogger("bistbull.circuit_breaker"),
                          "warning") as mock_warn:
            err = Exception("insufficient_quota: spending limit reached")
            cb.on_failure(err)
            n_after_first = mock_warn.call_count
            cb.on_failure(err)
            cb.on_failure(err)
            # No additional warning logs from the CB layer
            assert mock_warn.call_count == n_after_first

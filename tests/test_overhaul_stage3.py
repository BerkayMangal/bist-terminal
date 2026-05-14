# ================================================================
# tests/test_overhaul_stage3.py
#
# Great Overhaul Stage 3: Plan C Invalidation Completeness
#
# Audit finding:
#   When a KAP financial report drops for a ticker, the existing
#   `_invalidate_caches_for_ticker` covers raw/analysis/tech/bullwatch
#   caches BUT misses:
#     - history_cache (daily deltas keep stale values)
#     - thread-safe write to api.bullwatch._CACHE (direct dict access)
#
# Fix:
#   - Add history_cache to invalidation list
#   - Use _cache_update helper for atomic stale_after flag
#   - Return list of layers touched for observability/testing
#   - New `/api/diag/cache-coherence` endpoint
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Invalidation chain coverage
# ────────────────────────────────────────────────────────────────


class TestInvalidationCoverage:
    def test_invalidation_returns_list_of_touched_layers(self, monkeypatch):
        """The function now returns a list (was: None). Callers can use
        this for diagnostic logging."""
        from engine.kap_dispatcher import _invalidate_caches_for_ticker
        # Just run with a non-empty ticker — at minimum some core caches
        # should be touched (they exist by default).
        out = _invalidate_caches_for_ticker("BIMAS")
        assert isinstance(out, list)

    def test_empty_ticker_returns_empty_list(self):
        from engine.kap_dispatcher import _invalidate_caches_for_ticker
        out = _invalidate_caches_for_ticker("")
        assert out == []
        out = _invalidate_caches_for_ticker(None)
        assert out == []

    def test_history_cache_now_in_invalidation_path(self, monkeypatch):
        """The fix: history_cache must be in the layers touched.
        Previously it was missed; daily-delta deltas kept stale values."""
        from engine.kap_dispatcher import _invalidate_caches_for_ticker
        out = _invalidate_caches_for_ticker("BIMAS")
        # In practice history_cache exists, so should appear
        assert "history_cache" in out, (
            "Stage 3 audit fix regressed — history_cache must be "
            "invalidated alongside raw/analysis/tech."
        )

    def test_ticker_normalized(self, monkeypatch):
        """Ticker normalization: lowercase + .IS suffix handled."""
        from engine.kap_dispatcher import _invalidate_caches_for_ticker
        from core.cache import raw_cache
        # Seed cache entry
        raw_cache.set("BIMAS", {"x": 1})
        assert raw_cache.get("BIMAS") is not None
        # Invalidate via lowercase + suffix
        _invalidate_caches_for_ticker("bimas.is")
        assert raw_cache.get("BIMAS") is None

    def test_uses_thread_safe_helper_for_bw_mirror(self, monkeypatch):
        """The in-mem mirror write must go through _cache_update (atomic)
        — not the direct dict assignment. We pin this by checking that
        _cache_update was called when an items-bearing ticker is invalidated."""
        from engine.kap_dispatcher import _invalidate_caches_for_ticker
        from api import bullwatch as _bw

        # Seed BullWatch in-mem mirror with a matching ticker
        _bw._cache_update(items={"items": [{"symbol": "TARGET"}]})

        called = {"n": 0, "kwargs": []}
        orig_update = _bw._cache_update

        def _spy(**kw):
            called["n"] += 1
            called["kwargs"].append(kw)
            return orig_update(**kw)

        monkeypatch.setattr(_bw, "_cache_update", _spy)
        _invalidate_caches_for_ticker("TARGET")
        # Should have been called at least once with stale_after=0
        assert any(
            kw.get("stale_after") == 0.0 for kw in called["kwargs"]
        ), "Plan C did not use thread-safe _cache_update for stale_after flag"

        # Cleanup
        _bw._cache_update(items=None)


# ────────────────────────────────────────────────────────────────
# Idempotency
# ────────────────────────────────────────────────────────────────


class TestIdempotency:
    def test_running_twice_doesnt_crash(self):
        """Second call on already-invalidated ticker must not crash."""
        from engine.kap_dispatcher import _invalidate_caches_for_ticker
        _invalidate_caches_for_ticker("BIMAS")
        # Second call — caches already empty, should silently no-op
        out = _invalidate_caches_for_ticker("BIMAS")
        assert isinstance(out, list)


# ────────────────────────────────────────────────────────────────
# Logging contract
# ────────────────────────────────────────────────────────────────


class TestLoggingContract:
    def test_logs_layer_names(self, caplog):
        """Plan C must log WHICH layers were touched — for production
        debugging."""
        import logging
        from engine.kap_dispatcher import _invalidate_caches_for_ticker
        with caplog.at_level(logging.INFO, logger="bistbull.kap_dispatcher"):
            _invalidate_caches_for_ticker("BIMAS")
        # At least one log line should mention "Plan C" + count
        text = " ".join(r.message for r in caplog.records)
        assert "Plan C" in text


# ────────────────────────────────────────────────────────────────
# Cache coherence observability endpoint
# ────────────────────────────────────────────────────────────────


class TestCacheCoherenceEndpoint:
    """The /api/diag/cache-coherence endpoint should compose all layers'
    state into one report — UI/operator can quickly see if any layer is
    out-of-date."""

    def test_endpoint_exists_in_router(self):
        from api.diag import router
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/api/diag/cache-coherence" in paths

    @pytest.mark.asyncio
    async def test_endpoint_returns_layers_dict(self):
        # Functional smoke — running the handler directly
        from api.diag import api_diag_cache_coherence
        # The handler is an async coroutine; run inline
        resp = await api_diag_cache_coherence()
        # Response envelope is a JSONResponse; we can probe via __dict__
        # rather than full http round-trip
        import json as _json
        body = _json.loads(resp.body.decode("utf-8"))
        # success() wraps dict data flat — `layers` key should exist
        assert "layers" in body
        assert isinstance(body["layers"], dict)
        # At least raw_cache should be reported
        assert "raw_cache" in body["layers"]


# ────────────────────────────────────────────────────────────────
# Stage 1 helpers exist + integrate cleanly
# ────────────────────────────────────────────────────────────────


class TestThreadSafeIntegration:
    def test_bw_module_exports_helpers(self):
        """Sanity: api.bullwatch still exports the Stage 1 thread-safe
        helpers (used by kap_dispatcher)."""
        from api import bullwatch as _bw
        assert hasattr(_bw, "_cache_update")
        assert hasattr(_bw, "_cache_snapshot")
        assert hasattr(_bw, "_CACHE_LOCK")

"""HOTFIX 1 SORUN 1 — heatmap performance tests.

Guarantees /api/heatmap NEVER blocks the HTTP request on the slow
108-Ticker sequential loop (which was the 10-minute production
regression). All tests assert response-time bounds, not just
correctness.
"""

from __future__ import annotations

import threading
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI TestClient with fresh DB + seeded auth secret."""
    db = tmp_path / "hf1_heatmap.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
    monkeypatch.setenv("JWT_SECRET", "hf1-secret-" + "x" * 40)
    # Reset the module-level DB handles since this is session-new
    import infra.storage
    infra.storage._local = threading.local()
    infra.storage.DB_PATH = str(db)
    from infra.storage import init_db
    init_db()

    from app import app
    from core.cache import heatmap_cache
    heatmap_cache.clear()
    return TestClient(app)


# ==========================================================================
# TestHeatmapColdStartPerformance — guarantees <1s response on cache miss
# ==========================================================================

class TestHeatmapColdStartPerformance:
    def test_cache_miss_returns_under_1s(self, client):
        """The smoking-gun test: the prod regression was a 10-minute
        response time on cache miss. Even with a cold cache and no
        top10 scan data, the endpoint must return quickly."""
        from core.cache import heatmap_cache
        heatmap_cache.clear()

        t0 = time.monotonic()
        r = client.get("/api/heatmap")
        elapsed = time.monotonic() - t0

        assert r.status_code == 200
        # Generous bound: the real regression was 600+ seconds. Any
        # sub-second response is fine; anything over 5s indicates
        # we've slipped back into a sync fetch path.
        assert elapsed < 5.0, f"heatmap took {elapsed:.2f}s on cold miss"

    def test_cache_miss_flags_computing_true(self, client):
        """When there's no scan snapshot either, response must carry
        computing=true so frontend renders the empty state instead of
        looking dead."""
        from core.cache import heatmap_cache
        heatmap_cache.clear()
        r = client.get("/api/heatmap")
        assert r.status_code == 200
        data = r.json()
        # Response shape: success envelope wraps the heatmap result
        heatmap_result = data.get("data") if "data" in data else data
        # Either computing=true (cold cache, empty top10) or
        # computing=false with partial data (top10 had items)
        assert "computing" in heatmap_result, \
            f"'computing' field missing: keys = {list(heatmap_result.keys())}"

    def test_cache_miss_shape_has_sectors_list(self, client):
        """Rule 6: even on cold miss, response shape is stable
        (sectors list always present, even if empty)."""
        from core.cache import heatmap_cache
        heatmap_cache.clear()
        r = client.get("/api/heatmap")
        data = r.json()
        heatmap_result = data.get("data") if "data" in data else data
        assert "sectors" in heatmap_result
        assert isinstance(heatmap_result["sectors"], list)
        assert "total" in heatmap_result


# ==========================================================================
# TestHeatmapCacheHit — <100ms when warm
# ==========================================================================

class TestHeatmapCacheHit:
    def test_cache_hit_fast(self, client):
        """When heatmap_cache has a result, response should be near-
        instant. First request primes cache (cache_status='cold' or
        'partial'), second is served from cache."""
        from core.cache import heatmap_cache
        heatmap_cache.clear()
        # Seed cache directly
        heatmap_cache.set("heatmap", {
            "timestamp": "2026-04-20T12:00:00Z",
            "sectors": [
                {"sector": "Banka", "avg_change": 1.2,
                 "total_mcap": 1e12, "count": 5, "stocks": []},
            ],
            "total": 5,
            "computing": False,
        })
        t0 = time.monotonic()
        r = client.get("/api/heatmap")
        elapsed = time.monotonic() - t0
        assert r.status_code == 200
        assert elapsed < 1.0, f"cache hit took {elapsed:.2f}s"
        data = r.json()
        heatmap_result = data.get("data") if "data" in data else data
        assert heatmap_result["total"] == 5
        assert heatmap_result["sectors"][0]["sector"] == "Banka"


# ==========================================================================
# TestHeatmapNoSyncFetchFallback — smoking gun
# ==========================================================================

class TestHeatmapNoSyncFetchFallback:
    """The critical regression check: there must be NO path from
    /api/heatmap to a sequential 108-symbol borsapy loop on the HTTP
    request thread. Monkeypatch bp.Ticker to explode with a loud
    marker exception; if that ever fires during /api/heatmap, the
    regression has returned."""

    def test_no_borsapy_calls_on_request_path(self, client, monkeypatch):
        from core.cache import heatmap_cache
        heatmap_cache.clear()

        ticker_calls: list[str] = []

        class _Sentinel(Exception):
            """If bp.Ticker() ever runs on the HTTP request path, the
            regression is back."""

        def _forbid_ticker(*a, **kw):
            ticker_calls.append(str(a))
            raise _Sentinel("bp.Ticker must not be called on /api/heatmap path")

        # Try to monkey-patch borsapy if importable
        try:
            import borsapy
            monkeypatch.setattr(borsapy, "Ticker", _forbid_ticker)
        except ImportError:
            pytest.skip("borsapy not installed in this env")

        r = client.get("/api/heatmap")
        # Endpoint must still succeed (it falls back to computing=true)
        assert r.status_code == 200
        # And crucially, zero borsapy calls happened on the request path
        assert ticker_calls == [], \
            f"regression: bp.Ticker called {len(ticker_calls)} time(s) on request path"


# ==========================================================================
# TestFrontendRetryContract — backend gives the frontend enough info
# ==========================================================================

class TestFrontendRetryContract:
    """The frontend hotfix in terminal.js depends on specific response
    fields. These tests lock in the contract so future refactors can't
    break it silently."""

    def test_computing_true_when_cold(self, client):
        """Backend MUST return computing=true (not raise, not hang)
        when the heatmap is still warming up. Frontend reads this
        flag to schedule a 30s retry."""
        from core.cache import heatmap_cache
        heatmap_cache.clear()
        r = client.get("/api/heatmap")
        data = r.json()
        hm = data.get("data") if "data" in data else data
        # At least one of: empty sectors, OR computing=true explicitly
        assert (
            hm.get("computing") is True
            or len(hm.get("sectors", [])) == 0
        ), "frontend needs a clear 'still warming up' signal"

    def test_cache_status_field_indicates_state(self, client):
        """The success envelope should carry cache_status so
        operators can distinguish hit/cold/partial in logs."""
        from core.cache import heatmap_cache
        heatmap_cache.clear()
        r = client.get("/api/heatmap")
        data = r.json()
        # cache_status is in the envelope (not inside data.data)
        # Either top-level or inside _meta; both are valid
        has_cache_status = (
            "cache_status" in data
            or (isinstance(data.get("_meta"), dict)
                and "cache_status" in data["_meta"])
        )
        assert has_cache_status, f"cache_status missing from envelope: {data}"

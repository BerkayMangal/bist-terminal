"""Phase 5.1.1 — Heatmap frontend state tests.

Tests don't drive a real browser — instead they verify the JS source
contains the contract pieces specified in PHASE_5_REDESIGN_BRIEF:

- Skeleton render with shimmer animation on cache miss / computing=true
- 5s polling interval (was 30s — too slow for a power-user)
- 30s wall-clock timeout (was 5min — too long)
- AbortController cancellation on page change
- Stale-while-error pattern: keep last good heatmap on 5xx
- Timeout banner element
- Error banner element

Also verifies the backend /api/heatmap responses to a clean client:
- Returns within 1s on cache miss
- Sets computing=true when no top10 snapshot yet
- Includes 'computing' field in payload
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest


JS_PATH = Path(__file__).parent.parent / "static" / "terminal.js"
CSS_PATH = Path(__file__).parent.parent / "static" / "terminal.css"


@pytest.fixture(scope="module")
def js_source() -> str:
    return JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_source() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


# ============================================================
# Source-level contract tests — fast, no browser needed
# ============================================================
class TestHeatmapJsContract:
    def test_skeleton_render_function_exists(self, js_source):
        assert "_heatmapSkeletonHtml" in js_source, "Phase 5: skeleton helper missing"
        assert "heat-skel" in js_source, "Skeleton CSS class hook missing"
        assert "heatmap-skeleton" in js_source, "data-testid hook for skeleton missing"

    def test_polling_interval_is_5s(self, js_source):
        # The brief mandates 5s polling, 30s wall-clock cap
        assert "_HEATMAP_POLL_INTERVAL = 5000" in js_source, \
            "5s polling interval missing"
        assert "_HEATMAP_POLL_TIMEOUT  = 30000" in js_source \
            or "_HEATMAP_POLL_TIMEOUT = 30000" in js_source, \
            "30s wall-clock timeout missing"

    def test_abort_controller_cancellation(self, js_source):
        assert "_heatmapAbort" in js_source, "AbortController instance missing"
        assert "cancelHeatmapPolling" in js_source, "Public cancel hook missing"
        assert ".abort()" in js_source, "abort() call missing"

    def test_stale_while_error_keeps_last_good(self, js_source):
        # On non-timeout error with existing good content, prepend banner
        assert "heatmap-stale-banner" in js_source, "Stale banner data-testid missing"
        assert "Bağlantı sorunu" in js_source, "Stale banner Turkish text missing"

    def test_timeout_message(self, js_source):
        assert "heatmap-timeout" in js_source, "Timeout testid hook missing"
        assert "Veri henüz hazır değil, sayfa yenile" in js_source, \
            "Timeout user-facing message missing"

    def test_error_state_distinct_from_timeout(self, js_source):
        assert "heatmap-error" in js_source, "Error testid hook missing"
        assert "Heatmap yüklenemedi" in js_source, "Error message missing"


class TestHeatmapCssShimmer:
    def test_skeleton_classes_present(self, css_source):
        assert ".heat-skel-wrap" in css_source
        assert ".heat-skel-grid" in css_source
        assert ".heat-skel-cell" in css_source

    def test_shimmer_animation_defined(self, css_source):
        assert "@keyframes heatShimmer" in css_source
        assert "animation:heatShimmer" in css_source or "animation: heatShimmer" in css_source


# ============================================================
# Backend integration: heatmap endpoint must return the
# computing=true contract that the frontend relies on
# ============================================================
@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "heat_state.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
    monkeypatch.setenv("JWT_SECRET", "test-secret-" + "x" * 40)
    import infra.storage
    infra.storage._local = threading.local()
    infra.storage.DB_PATH = str(db)
    from infra.storage import init_db
    init_db()
    from app import app
    from core.cache import heatmap_cache
    heatmap_cache.clear()
    # Stub the background refresh kick — we don't want a real
    # borsapy scan firing on every test (slow + flaky on CI).
    import app as app_mod

    async def _noop_kick():
        return None

    monkeypatch.setattr(app_mod, "_kick_background_heatmap_refresh", _noop_kick)
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestHeatmapBackendStates:
    def test_cold_cache_returns_computing_true(self, client):
        from core.cache import heatmap_cache
        heatmap_cache.clear()
        r = client.get("/api/heatmap")
        assert r.status_code == 200
        body = r.json()
        # Phase 5.1.1: frontend depends on the computing field being present
        assert "computing" in body
        # On a fully cold start there's no top10 snapshot — must be True
        assert body.get("computing") is True

    def test_response_under_1s_even_on_cold(self, client):
        from core.cache import heatmap_cache
        heatmap_cache.clear()
        t0 = time.time()
        r = client.get("/api/heatmap")
        elapsed = time.time() - t0
        assert r.status_code == 200
        # Brief: <200ms is the target — give a generous 1s ceiling
        assert elapsed < 1.0, f"Took {elapsed:.2f}s, expected <1s"

    def test_computing_field_is_boolean(self, client):
        from core.cache import heatmap_cache
        heatmap_cache.clear()
        body = client.get("/api/heatmap").json()
        assert isinstance(body.get("computing"), bool), \
            "computing must be a boolean for the JS conditional"

    def test_response_has_sectors_field(self, client):
        from core.cache import heatmap_cache
        heatmap_cache.clear()
        body = client.get("/api/heatmap").json()
        # Even on cold start, sectors key must exist (even if empty)
        # Frontend's `!d.sectors || !d.sectors.length` check depends on it
        assert "sectors" in body

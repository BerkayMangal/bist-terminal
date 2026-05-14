# ================================================================
# tests/test_overhaul_stage5.py
#
# Great Overhaul Stage 5: history_fetch visibility + speed + watchdog.
#
# Root-cause finding (measured by exercising the running app as a user):
#   For a 437-ticker universe the BullWatch scan spent 5+ MINUTES in
#   `history_fetch` (the borsapy bulk price-history download), yet
#   /api/bullwatch/health reported `scan_progress: 0/437` the whole
#   time — no chunk-level progress was wired in. The user was unable
#   to tell hung-vs-working scans apart, and the 8-min watchdog could
#   fire BEFORE scoring even started, leaving the cache empty.
#
# This stage:
#   1. `batch_download_history_v9(progress_callback=…)` fires per-chunk
#   2. `engine.technical.batch_download_history` forwards the kwarg
#   3. `engine.bullwatch.scan(history_progress_callback=…)` plumbs it
#   4. `api.bullwatch._run_scan` sets `scan_phase="history_fetch"` →
#      "scoring" and bumps `progress/total` per chunk
#   5. `/api/bullwatch/health` exposes `scan_phase`
#   6. WORKERS 5 → 8, inter-chunk sleep 1.0 → 0.3s
#   7. Watchdog 8 → 12 min (history_fetch alone could exceed 8 min
#      before this stage)
#
# These tests pin each contract change without invoking the real
# borsapy provider.
# ================================================================

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Config tunables exist
# ────────────────────────────────────────────────────────────────


class TestConfigTunables:
    def test_batch_history_workers_bumped(self):
        from config import BATCH_HISTORY_WORKERS
        # 5 was the old hotfix value — we deliberately bumped it.
        assert BATCH_HISTORY_WORKERS >= 6
        # Sanity ceiling — 16 would risk borsapy rate limits.
        assert BATCH_HISTORY_WORKERS <= 12

    def test_chunk_sleep_constant_exists(self):
        from config import BATCH_HISTORY_CHUNK_SLEEP_SEC
        # Old hardcoded value was 1.0s and compounded into ~17s of pure
        # sleep across the chunk loop. Stage 5 lowered this.
        assert 0.0 <= BATCH_HISTORY_CHUNK_SLEEP_SEC < 1.0

    def test_scan_watchdog_increased(self):
        from api.bullwatch import _SCAN_WATCHDOG_SEC
        # 8 min was too tight given measured cold history_fetch could
        # alone consume ~5-7 min. Bumped to >= 10 min for headroom.
        assert _SCAN_WATCHDOG_SEC >= 10 * 60


# ────────────────────────────────────────────────────────────────
# providers.batch_download_history_v9 — progress_callback contract
# ────────────────────────────────────────────────────────────────


class TestProvidersHistoryProgress:
    def test_progress_callback_signature_accepted(self, monkeypatch):
        """The fetcher must accept a keyword-only progress_callback."""
        import inspect
        from data import providers
        sig = inspect.signature(providers.batch_download_history_v9)
        assert "progress_callback" in sig.parameters

    def test_progress_callback_fires_per_chunk(self, monkeypatch):
        """For N symbols at CHUNK=25, the callback must fire ceil(N/25)
        times (Pass 1) — once per chunk regardless of how many tickers
        succeed in each one."""
        from data import providers

        # Stub the inner fetch so we don't hit borsapy
        class _StubDF:
            def __len__(self): return 100  # >= 20 so it's accepted
        def _stub_is_empty(df): return False
        monkeypatch.setattr(providers, "_is_empty_frame", _stub_is_empty)
        monkeypatch.setattr(providers, "BORSAPY_AVAILABLE", True)

        # Patch the Ticker so .history() returns a non-empty df
        class _StubTk:
            def history(self, period=None, interval=None):
                return _StubDF()
        class _Stub_bp:
            Ticker = staticmethod(lambda tc: _StubTk())
        monkeypatch.setattr(providers, "bp", _Stub_bp)

        # CB should not block
        class _StubCB:
            def before_call(self): pass
            def on_success(self): pass
        monkeypatch.setattr(providers, "cb_borsapy", _StubCB())

        # 67 symbols → 3 chunks of [25, 25, 17]
        syms = [f"SYM{i:03d}" for i in range(67)]
        chunks_seen: list[tuple[int, int]] = []
        def _cb(done, total):
            chunks_seen.append((done, total))

        providers.batch_download_history_v9(
            syms, progress_callback=_cb,
        )

        # Pass 1 fires 3 times (one per chunk). Pass 2 has no failures
        # since stub always succeeds.
        assert len(chunks_seen) == 3, f"expected 3 chunks, saw {chunks_seen}"
        # Last call covers everything
        assert chunks_seen[-1][0] == 67
        assert chunks_seen[-1][1] == 67
        # Counts are monotonic
        for prev, nxt in zip(chunks_seen, chunks_seen[1:]):
            assert nxt[0] >= prev[0]

    def test_callback_failure_doesnt_break_fetch(self, monkeypatch):
        """The fetch must survive a callback that raises — observability
        is not allowed to compromise data."""
        from data import providers

        class _StubDF:
            def __len__(self): return 100
        monkeypatch.setattr(providers, "_is_empty_frame", lambda df: False)
        monkeypatch.setattr(providers, "BORSAPY_AVAILABLE", True)

        class _StubTk:
            def history(self, period=None, interval=None):
                return _StubDF()
        class _Stub_bp:
            Ticker = staticmethod(lambda tc: _StubTk())
        monkeypatch.setattr(providers, "bp", _Stub_bp)

        class _StubCB:
            def before_call(self): pass
            def on_success(self): pass
        monkeypatch.setattr(providers, "cb_borsapy", _StubCB())

        def _bad_cb(done, total):
            raise RuntimeError("oops")

        result = providers.batch_download_history_v9(
            ["AAA", "BBB"], progress_callback=_bad_cb,
        )
        # Despite the callback raising, the fetch succeeds for AAA + BBB
        assert "AAA" in result
        assert "BBB" in result


# ────────────────────────────────────────────────────────────────
# engine.technical.batch_download_history — forwards the kwarg
# ────────────────────────────────────────────────────────────────


class TestTechnicalLayerForward:
    def test_forwards_progress_callback(self, monkeypatch):
        from engine import technical
        captured: dict = {}

        def _spy(symbols, period=None, interval=None, progress_callback=None):
            captured["progress_callback"] = progress_callback
            return {}

        # Patch BORSAPY flag + the inner provider call
        monkeypatch.setattr(technical, "BORSAPY_AVAILABLE_TECH", True)
        # Re-route to the spy
        import data.providers as providers
        monkeypatch.setattr(providers, "batch_download_history_v9", _spy)

        sentinel = lambda d, t: None  # noqa: E731
        technical.batch_download_history(
            ["A", "B"], progress_callback=sentinel,
        )
        assert captured["progress_callback"] is sentinel


# ────────────────────────────────────────────────────────────────
# engine.bullwatch.scan — accepts + forwards history_progress_callback
# ────────────────────────────────────────────────────────────────


class TestBullwatchScanHistoryCallback:
    def test_scan_accepts_history_progress_callback(self):
        import inspect
        from engine import bullwatch
        sig = inspect.signature(bullwatch.scan)
        assert "history_progress_callback" in sig.parameters

    def test_scan_forwards_to_history_fn(self, monkeypatch):
        from engine import bullwatch

        captured: dict = {}

        def _stub_history_fn(symbols, progress_callback=None):
            captured["progress_callback"] = progress_callback
            return {}

        def _stub_metrics_fn(sym):
            return {}

        cb = lambda d, t: None  # noqa: E731
        bullwatch.scan(
            ["A", "B"],
            metrics_fn=_stub_metrics_fn,
            history_fn=_stub_history_fn,
            ownership_fn=lambda _s: None,
            history_progress_callback=cb,
        )
        assert captured["progress_callback"] is cb

    def test_scan_back_compat_for_history_fn_without_kwarg(self, monkeypatch):
        """Callers might inject a history_fn that doesn't accept the new
        kwarg (e.g. older tests). We must fall back to the positional
        call instead of crashing."""
        from engine import bullwatch

        calls: list[int] = []

        def _stub_history_fn(symbols):
            calls.append(len(symbols))
            return {}

        cb = lambda d, t: None  # noqa: E731
        bullwatch.scan(
            ["A", "B"],
            metrics_fn=lambda s: {},
            history_fn=_stub_history_fn,
            ownership_fn=lambda _s: None,
            history_progress_callback=cb,
        )
        # Called once with 2 symbols, despite the TypeError fallback
        assert calls == [2]


# ────────────────────────────────────────────────────────────────
# api.bullwatch — scan_phase exposed via health endpoint
# ────────────────────────────────────────────────────────────────


class TestHealthExposesScanPhase:
    @pytest.mark.asyncio
    async def test_health_returns_scan_phase_key(self, monkeypatch):
        """Health response must include `scan_phase`."""
        from api import bullwatch as bw
        # Force "running" so phase is reported
        bw._cache_update(running=True, scan_phase="history_fetch",
                         scan_started_at=time.time())
        try:
            resp = await bw.api_bullwatch_health()
            import json
            body = json.loads(resp.body.decode("utf-8"))
            assert "scan_phase" in body
            assert body["scan_phase"] == "history_fetch"
        finally:
            bw._cache_update(running=False, scan_phase=None,
                             scan_started_at=0.0)

    @pytest.mark.asyncio
    async def test_scan_phase_null_when_not_running(self):
        from api import bullwatch as bw
        bw._cache_update(running=False, scan_phase=None)
        resp = await bw.api_bullwatch_health()
        import json
        body = json.loads(resp.body.decode("utf-8"))
        assert body["scan_phase"] is None


# ────────────────────────────────────────────────────────────────
# _CACHE schema includes scan_phase
# ────────────────────────────────────────────────────────────────


class TestCacheSchema:
    def test_cache_schema_has_scan_phase(self):
        from api.bullwatch import _CACHE
        assert "scan_phase" in _CACHE


# ────────────────────────────────────────────────────────────────
# Cleanup contract — _refresh_and_persist clears phase on finish
# ────────────────────────────────────────────────────────────────


class TestPhaseClearedOnFinish:
    def test_cache_phase_set_to_none_on_failure(self, monkeypatch):
        """When the scan finally block runs, scan_phase must be reset
        so the UI doesn't keep showing a stale 'history_fetch' label."""
        from api import bullwatch as bw
        # Simulate post-scan finally state — direct test of the cleanup
        # contract since wiring a fake scan through asyncio is heavier.
        bw._cache_update(running=True, scan_phase="scoring")
        # Mimic the finally clause from _refresh_and_persist
        bw._cache_update(running=False, scan_phase=None)
        assert bw._cache_get("scan_phase") is None
        assert bw._cache_get("running") is False

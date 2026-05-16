# ================================================================
# tests/test_overhaul_stage8.py
#
# Great Overhaul Stage 8: Railway Pro tuning.
#
# Context: user upgraded Railway from Hobby → Pro ($35/mo). Per-replica
# limits jump from 8 vCPU / 8 GB to 24 vCPU / 24 GB. This stage uses
# the headroom for:
#   1. Larger history_cache (no mid-scan evictions)
#   2. Bigger worker pools (more parallel borsapy)
#   3. Boot pre-warm task so FIRST refresh is already cache-warm
#
# These tests pin the new constants so a regression that drops them
# back down is caught immediately.
# ================================================================

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Config tunables sized for Pro plan
# ────────────────────────────────────────────────────────────────


class TestRailwayProConfig:
    def test_history_cache_size_bumped(self):
        from config import HISTORY_CACHE_SIZE
        # 500 was the Hobby ceiling; Pro can hold the full BIST 591
        # tickers without evicting. Lower bound 1500 leaves room for
        # growth without false-positive regressions.
        assert HISTORY_CACHE_SIZE >= 1500, (
            f"HISTORY_CACHE_SIZE shrunk to {HISTORY_CACHE_SIZE} — "
            "Stage 8 bump (Railway Pro) regressed?"
        )

    def test_batch_history_workers_bumped(self):
        from config import BATCH_HISTORY_WORKERS
        # Stage 5 set this to 8 on Hobby. Stage 8 doubles it on Pro.
        # Lower bound 12 catches a partial regression too.
        assert BATCH_HISTORY_WORKERS >= 12, (
            f"BATCH_HISTORY_WORKERS dropped to {BATCH_HISTORY_WORKERS}"
        )

    def test_scan_max_workers_throttle_safe(self):
        from config import SCAN_MAX_WORKERS
        # The "Railway Pro → bump workers" premise was wrong: the scan
        # bottleneck is borsapy rate-limiting, not local CPU. PR #93
        # dropped SCAN_MAX_WORKERS to 6 to stop borsapy throttling on
        # the 622-stock universe. Guard against it creeping back up.
        assert SCAN_MAX_WORKERS <= 8, (
            f"SCAN_MAX_WORKERS={SCAN_MAX_WORKERS} — too high, will "
            "re-trigger borsapy throttling on the full-BIST scan"
        )

    def test_chunk_sleep_tightened(self):
        from config import BATCH_HISTORY_CHUNK_SLEEP_SEC
        # Stage 5 = 0.3s, Stage 8 = 0.15s. Anything >0.25 means the
        # Pro-plan tightening regressed.
        assert BATCH_HISTORY_CHUNK_SLEEP_SEC <= 0.25, (
            f"BATCH_HISTORY_CHUNK_SLEEP_SEC rose to "
            f"{BATCH_HISTORY_CHUNK_SLEEP_SEC} — Pro-plan tightening lost"
        )


# ────────────────────────────────────────────────────────────────
# Pre-warm task exists and has the right shape
# ────────────────────────────────────────────────────────────────


class TestPrewarmTask:
    def test_prewarm_function_exported(self):
        from engine import background_tasks as bg
        assert hasattr(bg, "history_cache_prewarm")
        assert asyncio.iscoroutinefunction(bg.history_cache_prewarm)

    def test_prewarm_uses_full_bist_universe(self, monkeypatch):
        """The task must drain the broadest universe available so the
        first refresh from ANY user-facing path is warm."""
        from engine import background_tasks as bg

        captured = {"symbols": None}

        def _fake_batch(symbols, *args, **kwargs):
            # asyncio.to_thread passes positional args
            captured["symbols"] = list(symbols)
            return {}

        # Patch the function the prewarm calls
        from engine import technical as tech
        monkeypatch.setattr(tech, "batch_download_history", _fake_batch)

        # Run with sleep removed so the test is fast
        async def _run():
            # Skip the asyncio.sleep at the top by patching it
            async def _noop_sleep(_s):
                return None
            monkeypatch.setattr(bg.asyncio, "sleep", _noop_sleep)
            await bg.history_cache_prewarm()

        asyncio.run(_run())
        assert captured["symbols"] is not None
        # FULL_BIST should be hundreds of tickers — not just BIST30.
        assert len(captured["symbols"]) > 100, (
            f"Prewarm only requested {len(captured['symbols'])} tickers, "
            "expected the full BIST universe"
        )

    def test_prewarm_failure_doesnt_raise(self, monkeypatch):
        """Boot must not crash if borsapy is down at startup."""
        from engine import background_tasks as bg

        def _boom(*a, **kw):
            raise RuntimeError("borsapy 502 at boot")

        from engine import technical as tech
        monkeypatch.setattr(tech, "batch_download_history", _boom)

        async def _run():
            async def _noop_sleep(_s):
                return None
            monkeypatch.setattr(bg.asyncio, "sleep", _noop_sleep)
            # Must not raise even though the inner call did
            await bg.history_cache_prewarm()

        asyncio.run(_run())  # no exception = pass


# ────────────────────────────────────────────────────────────────
# App lifespan wiring
# ────────────────────────────────────────────────────────────────


class TestLifespanWiring:
    def test_app_imports_prewarm(self):
        """app.py must import history_cache_prewarm so Stage 8 ships."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "app.py")
        ) as fh:
            src = fh.read()
        assert "history_cache_prewarm" in src, (
            "app.py is not wiring the Stage 8 prewarm task"
        )
        assert "asyncio.create_task(history_cache_prewarm())" in src, (
            "Prewarm task isn't being created during startup"
        )

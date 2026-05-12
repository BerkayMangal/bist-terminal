# ================================================================
# tests/test_radar_snapshot.py
#
# D.2.2 — Radar (/api/top10 + /api/scan) ⇄ shared snapshot store.
#
# Exercises the ScanCoordinator's added snapshot write. The legacy
# set_top10 path (Redis key bb:snapshot:top10) is left intact and is
# NOT tested here — existing tests cover it.
#
# These tests mock analyze_fn / history_fn so we don't have to spin
# up borsapy. FakeRedis stands in for Redis on the snapshot side.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from core.scan_coordinator import ScanCoordinator
from core.snapshot_store import SnapshotStore
from tests._fake_redis import FakeRedis


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def coordinator(monkeypatch, fake_redis):
    import core.snapshot_store as snap_mod
    monkeypatch.setattr(snap_mod, "_default_store", SnapshotStore(client=fake_redis))
    return ScanCoordinator()


def _stub_analyze(score: int):
    """Build an analyze_fn that always returns a constant-shaped result."""
    def fn(ticker: str) -> dict:
        return {
            "ticker": ticker,
            "overall": score,
            "confidence": 80,
            "scores": {"quality": score, "value": score, "growth": score},
            "verdict": "AL",
        }
    return fn


def _stub_history(universe: list[str]) -> dict:
    return {t: None for t in universe}


# ── 1. Successful scan writes a radar snapshot ──────────────────────


def test_scan_writes_radar_snapshot(coordinator, fake_redis):
    universe = ["AAA", "BBB", "CCC"]
    coordinator.start_scan(
        universe=universe,
        analyze_fn=_stub_analyze(score=75),
        history_fn=_stub_history,
    )
    # The snapshot was written under module "radar"
    assert fake_redis.exists("bb:snapshots:radar:latest") == 1
    sid = fake_redis.get("bb:snapshots:radar:latest")
    assert fake_redis.exists(f"bb:snapshots:radar:{sid}:meta") == 1
    assert fake_redis.exists(f"bb:snapshots:radar:{sid}:score") == 1
    # All three tickers present
    for t in universe:
        assert fake_redis.exists(f"bb:snapshots:radar:{sid}:item:{t}") == 1


# ── 2. Snapshot meta carries source_scan_id ─────────────────────────


def test_snapshot_meta_carries_source_scan_id(coordinator, fake_redis):
    universe = ["AAA"]
    coordinator.start_scan(
        universe=universe,
        analyze_fn=_stub_analyze(score=80),
        history_fn=_stub_history,
    )
    sid = fake_redis.get("bb:snapshots:radar:latest")
    meta_raw = fake_redis.get(f"bb:snapshots:radar:{sid}:meta")
    import json
    meta = json.loads(meta_raw)
    assert meta["module"] == "radar"
    assert meta["n_scored"] == 1
    assert "source_scan_id" in meta
    assert meta["source_scan_id"].startswith("scan_")
    assert meta["universe_size"] == 1
    assert meta["ranked_count"] == 1


# ── 3. Snapshot score = overall, sorted desc ────────────────────────


def test_snapshot_zset_ordered_by_overall_desc(coordinator, fake_redis):
    # Build an analyze_fn that returns different overall scores per ticker
    def _scored_by_ticker(ticker: str):
        # Higher hash → higher score, mod 100
        return {
            "ticker": ticker,
            "overall": {"HIGH": 90, "MID": 50, "LOW": 10}[ticker],
            "confidence": 80,
            "scores": {},
        }
    coordinator.start_scan(
        universe=["LOW", "HIGH", "MID"],
        analyze_fn=_scored_by_ticker,
        history_fn=_stub_history,
    )
    sid = fake_redis.get("bb:snapshots:radar:latest")
    members = fake_redis.zrevrange(
        f"bb:snapshots:radar:{sid}:score", 0, -1, withscores=True,
    )
    assert [m for m, _ in members] == ["HIGH", "MID", "LOW"]


# ── 4. Legacy set_top10 path still runs ─────────────────────────────


def test_legacy_top10_still_written(coordinator, fake_redis):
    """The new snapshot write must NOT replace the legacy in-memory
    top10 cache. Existing endpoints depend on get_top10_items()."""
    from core.cache import get_top10_items, get_top10_asof
    universe = ["AAA", "BBB"]
    coordinator.start_scan(
        universe=universe,
        analyze_fn=_stub_analyze(score=70),
        history_fn=_stub_history,
    )
    items = get_top10_items()
    assert len(items) == 2
    assert {it["ticker"] for it in items} == {"AAA", "BBB"}
    assert get_top10_asof() is not None


# ── 5. Snapshot failure doesn't kill the scan ───────────────────────


def test_snapshot_write_failure_does_not_break_scan(
    coordinator, fake_redis, monkeypatch,
):
    # Force the snapshot store write to raise
    import core.snapshot_store as snap_mod

    def _boom(*a, **kw):
        raise RuntimeError("snapshot store offline")

    class _BadStore:
        def write_snapshot(self, *a, **kw):
            raise RuntimeError("simulated outage")

    monkeypatch.setattr(snap_mod, "_default_store", _BadStore())

    from core.cache import get_top10_items
    universe = ["AAA"]
    # Scan should complete successfully even if snapshot write blows up
    coordinator.start_scan(
        universe=universe,
        analyze_fn=_stub_analyze(score=75),
        history_fn=_stub_history,
    )
    assert len(get_top10_items()) == 1


# ── 6. Empty ranked list — no snapshot written ──────────────────────


def test_empty_ranked_no_snapshot(coordinator, fake_redis):
    """If every analyze returns None (e.g. data layer down), no snapshot
    is written — caller can detect via missing latest pointer."""
    def _all_none(_t: str):
        return None
    coordinator.start_scan(
        universe=["AAA", "BBB"],
        analyze_fn=_all_none,
        history_fn=_stub_history,
    )
    # No snapshot key should exist
    assert fake_redis.exists("bb:snapshots:radar:latest") == 0

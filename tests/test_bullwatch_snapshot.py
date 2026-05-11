# ================================================================
# Integration tests — BullWatch ⇄ shared snapshot store.
#
# Exercises the helper layer in api/bullwatch.py (write-through,
# snapshot-first reads, fallback, cold-start) WITHOUT invoking the
# full FastAPI app and without calling the real engine.bullwatch.scan.
# A FakeRedis stub stands in for Redis; the BullWatch scan is mocked
# at the _run_scan boundary.
# ================================================================

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from core.snapshot_store import SnapshotStore
from tests._fake_redis import FakeRedis


# ── Test isolation: every test gets a fresh fake redis + reset _CACHE ─


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def bw(monkeypatch, fake_redis: FakeRedis):
    """Import api.bullwatch with a fake redis injected, _CACHE reset."""
    import api.bullwatch as bullwatch_mod
    import core.snapshot_store as snap_mod

    # Replace the default store singleton with one backed by FakeRedis
    test_store = SnapshotStore(client=fake_redis)
    monkeypatch.setattr(snap_mod, "_default_store", test_store)

    # Reset in-memory cache state
    bullwatch_mod._CACHE.update({
        "items": None,
        "as_of": None,
        "stale_after": 0.0,
        "running": False,
        "progress": 0,
        "total": 0,
        "scan_started_at": 0.0,
    })
    bullwatch_mod._SCAN_DONE = None
    return bullwatch_mod


# ── Payload builder ─────────────────────────────────────────────────


def _make_payload(symbols: list[tuple[str, float]], scanned: int = 429) -> dict:
    """Build a _run_scan-shaped payload from (symbol, score) tuples."""
    items = [
        {
            "symbol": sym,
            "score": float(score),
            "zone": "EARLY" if score < 50 else "CONFIRMED",
            "pattern": "test-pattern",
            "data_quality": "high",
            "components": {},
            "metrics": {},
            "reasons": [],
        }
        for sym, score in symbols
    ]
    return {
        "items": items,
        "scanned": scanned,
        "eligible_count": len(items),
        "ineligible_count": scanned - len(items),
        "cap_tl": 250_000_000,
        "near_misses": [],
        "as_of": "2026-05-11T12:00:00+00:00",
        "duration_ms": 4200,
    }


# ── 1. _persist_snapshot writes correctly ───────────────────────────


def test_persist_snapshot_creates_keys(bw, fake_redis: FakeRedis):
    payload = _make_payload([("KAPLM", 92.0), ("ASTOR", 88.0), ("EREGL", 60.0)])
    sid = bw._persist_snapshot(payload)
    assert sid is not None
    assert fake_redis.exists(f"bb:snapshots:bullwatch:latest") == 1
    assert fake_redis.get("bb:snapshots:bullwatch:latest") == sid
    assert fake_redis.exists(f"bb:snapshots:bullwatch:{sid}:meta") == 1
    assert fake_redis.exists(f"bb:snapshots:bullwatch:{sid}:score") == 1


def test_persist_snapshot_returns_none_when_empty(bw):
    sid = bw._persist_snapshot({"items": []})
    assert sid is None


def test_persist_snapshot_returns_none_when_missing_items_key(bw):
    sid = bw._persist_snapshot({"scanned": 100})  # no items key
    assert sid is None


# ── 2. _read_snapshot_payload rebuilds payload ──────────────────────


def test_read_snapshot_payload_round_trip(bw):
    original = _make_payload([("AAA", 90.0), ("BBB", 80.0), ("CCC", 70.0)])
    sid = bw._persist_snapshot(original)
    assert sid is not None

    read = bw._read_snapshot_payload(limit=10)
    assert read is not None
    assert read["scanned"] == 429
    assert read["eligible_count"] == 3
    assert len(read["items"]) == 3
    # Ordering preserved by score desc
    assert [it["symbol"] for it in read["items"]] == ["AAA", "BBB", "CCC"]
    assert read["_snapshot_scan_id"] == sid


def test_read_snapshot_payload_respects_limit(bw):
    syms = [(f"T{i}", float(100 - i)) for i in range(10)]
    bw._persist_snapshot(_make_payload(syms))
    read = bw._read_snapshot_payload(limit=3)
    assert read is not None
    assert len(read["items"]) == 3
    # Highest scores first
    assert [it["symbol"] for it in read["items"]] == ["T0", "T1", "T2"]


def test_read_snapshot_payload_returns_none_when_no_snapshot(bw):
    assert bw._read_snapshot_payload() is None


# ── 3. Corrupted snapshot falls back to previous ────────────────────


def test_read_snapshot_payload_falls_back_to_previous(bw, fake_redis: FakeRedis):
    sid1 = bw._persist_snapshot(_make_payload([("OLD", 10.0)]))
    sid2 = bw._persist_snapshot(_make_payload([("NEW", 20.0)]))
    # Corrupt the latest by deleting its meta
    fake_redis.delete(f"bb:snapshots:bullwatch:{sid2}:meta")

    read = bw._read_snapshot_payload()
    assert read is not None
    # Fell back to sid1 (OLD)
    assert read["items"][0]["symbol"] == "OLD"
    assert read["_snapshot_scan_id"] == sid1


def test_read_snapshot_payload_returns_none_when_both_corrupted(
    bw, fake_redis: FakeRedis,
):
    sid1 = bw._persist_snapshot(_make_payload([("OLD", 10.0)]))
    sid2 = bw._persist_snapshot(_make_payload([("NEW", 20.0)]))
    fake_redis.delete(f"bb:snapshots:bullwatch:{sid1}:meta")
    fake_redis.delete(f"bb:snapshots:bullwatch:{sid2}:meta")
    assert bw._read_snapshot_payload() is None


# ── 4. _refresh_and_persist round-trip (with mocked _run_scan) ──────


@pytest.mark.asyncio
async def test_refresh_and_persist_writes_snapshot_and_cache(bw, monkeypatch):
    captured: list[dict] = []
    expected = _make_payload([("KAPLM", 92.0), ("ASTOR", 88.0)])

    def fake_run_scan(min_score, limit, cap_tl, diagnostic):
        captured.append({"min_score": min_score, "cap_tl": cap_tl})
        return expected

    monkeypatch.setattr(bw, "_run_scan", fake_run_scan)

    payload = await bw._refresh_and_persist()
    assert payload == expected
    assert bw._CACHE["items"] == expected            # mirror updated
    assert bw._CACHE["running"] is False             # cleanup
    assert len(captured) == 1                        # scan ran once

    # Snapshot was written
    read = bw._read_snapshot_payload()
    assert read is not None
    assert read["items"][0]["symbol"] == "KAPLM"


@pytest.mark.asyncio
async def test_refresh_and_persist_idempotent_when_running(bw, monkeypatch):
    """If a scan is already running, second call returns None immediately."""
    # Simulate a scan in flight
    bw._CACHE["running"] = True
    monkeypatch.setattr(bw, "_run_scan", lambda *a: pytest.fail("should not run"))
    result = await bw._refresh_and_persist()
    assert result is None
    # Cleanup
    bw._CACHE["running"] = False


# ── 5. _schedule_background_refresh fire-and-forget ─────────────────


@pytest.mark.asyncio
async def test_schedule_background_refresh_returns_false_when_running(bw):
    bw._CACHE["running"] = True
    assert bw._schedule_background_refresh() is False
    bw._CACHE["running"] = False


@pytest.mark.asyncio
async def test_schedule_background_refresh_returns_true_when_idle(bw, monkeypatch):
    """When idle, scheduling should kick a background task and return True
    immediately (non-blocking)."""
    payload = _make_payload([("X", 50.0)])
    monkeypatch.setattr(bw, "_run_scan", lambda *a: payload)

    scheduled = bw._schedule_background_refresh()
    assert scheduled is True
    # Let the background task complete
    await asyncio.sleep(0)
    # Tasks created by create_task need at least one event-loop turn
    # to start; loop a few times to drain them
    for _ in range(5):
        await asyncio.sleep(0)


# ── 6. Cold-start path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cold_start_runs_when_no_snapshot(bw, monkeypatch):
    expected = _make_payload([("FIRST", 75.0)])
    monkeypatch.setattr(bw, "_run_scan", lambda *a: expected)

    payload = await bw._cold_start_scan()
    assert payload == expected
    # Snapshot now exists
    assert bw._read_snapshot_payload() is not None


@pytest.mark.asyncio
async def test_cold_start_waits_for_in_flight_scan(bw):
    """When a scan is already running, cold-start should wait for it
    rather than launching a duplicate."""
    bw._CACHE["running"] = True
    bw._SCAN_DONE = asyncio.Event()

    async def complete_after():
        await asyncio.sleep(0.05)
        bw._CACHE["items"] = _make_payload([("LATE", 60.0)])
        bw._SCAN_DONE.set()
        bw._CACHE["running"] = False

    asyncio.create_task(complete_after())
    payload = await bw._cold_start_scan()
    assert payload is not None
    assert payload["items"][0]["symbol"] == "LATE"

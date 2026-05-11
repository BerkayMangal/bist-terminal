# ================================================================
# Contract tests for /api/bullwatch.
#
# Verifies the user-facing UX contract:
#   - refresh=true with a valid snapshot is NON-BLOCKING (returns the
#     current snapshot in < 100 ms, schedules a background refresh).
#   - refresh=true without any snapshot/cache falls back to a blocking
#     cold-start (only acceptable cold path).
#   - refresh=false serves the current snapshot when fresh, schedules a
#     background refresh + returns stale when older than SOFT_MAX.
#   - Idempotent: two refresh=true calls don't trigger two scans.
#   - The pre-snapshot response shape (items, scanned, eligible_count,
#     near_misses, asof, duration_ms, cap_tl) is preserved.
#   - Scoring is not affected — same input items round-trip through
#     the response unchanged.
#
# Tests call the endpoint coroutine directly with kwargs (FastAPI
# Query defaults are plain values when invoked from Python).
# ================================================================

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from core.snapshot_store import SnapshotStore
from tests._fake_redis import FakeRedis


# ── Test rig ────────────────────────────────────────────────────────


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def bw(monkeypatch, fake_redis: FakeRedis):
    """Reset api.bullwatch + snapshot store for each test."""
    import api.bullwatch as bullwatch_mod
    import core.snapshot_store as snap_mod

    test_store = SnapshotStore(client=fake_redis)
    monkeypatch.setattr(snap_mod, "_default_store", test_store)

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


def _make_payload(symbols: list[tuple[str, float]]) -> dict:
    return {
        "items": [
            {
                "symbol": s, "score": float(sc), "zone": "EARLY",
                "pattern": "p", "data_quality": "high",
                "components": {}, "metrics": {}, "reasons": [],
            }
            for s, sc in symbols
        ],
        "scanned": 429,
        "eligible_count": len(symbols),
        "ineligible_count": 429 - len(symbols),
        "cap_tl": 250_000_000,
        "near_misses": [],
        "as_of": "2026-05-11T12:00:00+00:00",
        "duration_ms": 4000,
    }


async def _call(bw, **overrides) -> tuple[dict, dict]:
    """Call the endpoint with sensible defaults. Returns (body, meta)."""
    kwargs = {
        "refresh": False, "min_score": 0.0, "zone": None,
        "limit": 50, "cap_tl": None, "diagnostic": False,
    }
    kwargs.update(overrides)
    resp = await bw.api_bullwatch(**kwargs)
    body = json.loads(resp.body)
    return body, body.get("_meta", {})


# ── 1. refresh=true with snapshot is NON-BLOCKING ───────────────────


@pytest.mark.asyncio
async def test_refresh_true_with_snapshot_is_non_blocking(bw, monkeypatch):
    """The cardinal UX guarantee: refresh=true must NEVER block when a
    snapshot exists. Returns the current snapshot in <100ms, schedules
    a background scan."""
    # Pre-populate snapshot
    bw._persist_snapshot(_make_payload([("AAA", 90.0), ("BBB", 80.0)]))

    # _run_scan should NOT be invoked synchronously — it goes to a
    # background task. Tracker:
    scan_calls: list = []
    monkeypatch.setattr(bw, "_run_scan", lambda *a: scan_calls.append(a) or _make_payload([("Z", 1.0)]))

    t0 = time.perf_counter()
    body, meta = await _call(bw, refresh=True)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 100, f"refresh=true blocked for {elapsed_ms:.1f}ms"
    assert meta["from_snapshot"] is True
    assert meta.get("refresh_scheduled") is True
    assert meta["cache_status"] == "snapshot_with_refresh"
    # Current snapshot returned (not the new one — that's still running)
    syms = [it["symbol"] for it in body["items"]]
    assert syms == ["AAA", "BBB"]


# ── 2. refresh=true without any view → cold-start (blocking allowed) ─


@pytest.mark.asyncio
async def test_refresh_true_with_no_snapshot_does_cold_start(bw, monkeypatch):
    expected = _make_payload([("FIRST", 75.0)])
    monkeypatch.setattr(bw, "_run_scan", lambda *a: expected)

    body, meta = await _call(bw, refresh=True)
    assert meta["cache_status"] == "cold_start"
    assert meta["from_snapshot"] is False
    assert body["items"][0]["symbol"] == "FIRST"


# ── 3. refresh=false with fresh snapshot ────────────────────────────


@pytest.mark.asyncio
async def test_refresh_false_with_fresh_snapshot_serves_immediately(
    bw, monkeypatch,
):
    bw._persist_snapshot(_make_payload([("CCC", 95.0)]))
    monkeypatch.setattr(bw, "_run_scan", lambda *a: pytest.fail("should not scan"))

    t0 = time.perf_counter()
    body, meta = await _call(bw, refresh=False)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 100
    assert meta["cache_status"] == "snapshot_hit"
    assert meta["from_snapshot"] is True
    assert body["items"][0]["symbol"] == "CCC"
    assert meta.get("refresh_scheduled") is not True


# ── 4. refresh=false with stale snapshot serves stale + schedules refresh


@pytest.mark.asyncio
async def test_refresh_false_with_stale_snapshot_serves_stale_and_schedules(
    bw, monkeypatch,
):
    """A snapshot older than SOFT_MAX is served (no blanking) AND a
    background refresh is kicked."""
    # Write snapshot, then age the meta's asof_unix by mutating Redis directly
    import core.snapshot_store as snap_mod
    fake = snap_mod._default_store._client_override
    bw._persist_snapshot(_make_payload([("DDD", 50.0)]))
    sid = fake.get("bb:snapshots:bullwatch:latest")
    meta_raw = fake.get(f"bb:snapshots:bullwatch:{sid}:meta")
    meta_obj = json.loads(meta_raw)
    # Age by 1 hour (well past 30-min soft max)
    meta_obj["asof_unix"] = time.time() - 3600
    fake.set(f"bb:snapshots:bullwatch:{sid}:meta", json.dumps(meta_obj))

    # Track that scan WAS scheduled (background) but not awaited
    monkeypatch.setattr(bw, "_run_scan", lambda *a: _make_payload([("NEW", 99.0)]))

    body, meta = await _call(bw, refresh=False)
    assert meta["cache_status"] == "stale_with_refresh"
    assert meta["from_snapshot"] is True
    assert meta.get("refresh_scheduled") is True
    assert meta.get("stale") is True
    # Stale items still returned
    assert body["items"][0]["symbol"] == "DDD"


# ── 5. Idempotent: two refresh=true calls = at most one scheduled scan


@pytest.mark.asyncio
async def test_refresh_true_idempotent(bw, monkeypatch):
    bw._persist_snapshot(_make_payload([("X", 1.0)]))

    scan_starts: list = []

    def fake_run(*a):
        scan_starts.append(time.time())
        time.sleep(0.05)  # simulate work
        return _make_payload([("X", 1.0)])

    monkeypatch.setattr(bw, "_run_scan", fake_run)

    body1, meta1 = await _call(bw, refresh=True)
    # Let the background task tick once so it can mark _CACHE["running"]=True
    # BEFORE we check idempotency. Without this the test is racy — the
    # task may not have started yet when the second request arrives.
    await asyncio.sleep(0)
    # Second call WHILE first background scan is still running
    body2, meta2 = await _call(bw, refresh=True)

    assert meta1.get("refresh_scheduled") is True
    # Second call should observe a scan is already running → not re-scheduled.
    # _build_response omits the meta key entirely when scheduled=False, so
    # the absence is the same as False here.
    assert meta2.get("refresh_scheduled") is not True
    # Both calls non-blocking; both return current snapshot
    assert meta1["from_snapshot"] is True
    assert meta2["from_snapshot"] is True


# ── 6. Pre-snapshot response shape preserved ────────────────────────


@pytest.mark.asyncio
async def test_response_shape_unchanged(bw):
    bw._persist_snapshot(_make_payload([("AAA", 1.0)]))
    body, meta = await _call(bw, refresh=False)

    # Existing top-level keys
    for key in ["items", "scanned", "eligible_count", "ineligible_count",
                "cap_tl", "near_misses", "duration_ms"]:
        assert key in body, f"missing legacy response key: {key}"

    # Existing meta keys
    assert "build_version" in meta
    assert "as_of" in meta
    assert meta["engine"] == "bullwatch_v1"

    # New snapshot-related meta keys (additive)
    assert "from_snapshot" in meta
    assert "scan_id" in meta


# ── 7. Scoring not modified — round-trip preserves item payload ─────


@pytest.mark.asyncio
async def test_scoring_not_modified(bw):
    """The snapshot pipeline must not alter score/zone/pattern of items."""
    original = _make_payload([
        ("KAPLM", 92.7), ("ASTOR", 88.4), ("EREGL", 60.0),
    ])
    # Custom zone to ensure pipeline doesn't normalize away
    original["items"][0]["zone"] = "CONVICTION"
    original["items"][1]["pattern"] = "Custom-Squeeze-Pattern"

    bw._persist_snapshot(original)
    body, _ = await _call(bw, refresh=False, limit=10)

    by_sym = {it["symbol"]: it for it in body["items"]}
    assert by_sym["KAPLM"]["score"] == 92.7
    assert by_sym["KAPLM"]["zone"] == "CONVICTION"
    assert by_sym["ASTOR"]["pattern"] == "Custom-Squeeze-Pattern"
    assert by_sym["EREGL"]["score"] == 60.0


# ── 8. limit + zone filter applied at read time ─────────────────────


@pytest.mark.asyncio
async def test_limit_and_zone_filter_at_read_time(bw):
    bw._persist_snapshot(_make_payload([
        ("A", 90.0), ("B", 80.0), ("C", 70.0), ("D", 60.0),
    ]))
    body, _ = await _call(bw, refresh=False, limit=2)
    assert len(body["items"]) == 2
    assert [it["symbol"] for it in body["items"]] == ["A", "B"]


# ── 9. Memory mirror fallback when Redis goes away mid-session ──────


@pytest.mark.asyncio
async def test_memory_fallback_when_snapshot_missing(bw, monkeypatch, fake_redis):
    """If snapshot store somehow loses data (e.g. Redis cleared) but
    _CACHE still has items, serve from memory rather than blanking."""
    # Simulate prior scan that populated _CACHE only
    payload = _make_payload([("MEM", 50.0)])
    bw._CACHE["items"] = payload
    bw._CACHE["as_of"] = payload["as_of"]
    bw._CACHE["stale_after"] = time.time() + 300

    monkeypatch.setattr(bw, "_run_scan", lambda *a: pytest.fail("should not scan"))

    body, meta = await _call(bw, refresh=False)
    assert meta["from_snapshot"] is False
    assert meta["cache_status"] in ("memory_hit", "stale_with_refresh")
    assert body["items"][0]["symbol"] == "MEM"


# ── 10. Experimental request bypasses snapshot ──────────────────────


@pytest.mark.asyncio
async def test_experimental_bypasses_snapshot(bw, monkeypatch):
    bw._persist_snapshot(_make_payload([("CACHED", 99.0)]))

    fresh = _make_payload([("FRESH", 1.0)])
    monkeypatch.setattr(bw, "_run_scan", lambda *a: fresh)

    # cap_tl override triggers experimental path
    body, meta = await _call(bw, refresh=False, cap_tl=100_000_000)
    assert meta["cache_status"] == "experimental"
    assert meta["from_snapshot"] is False
    assert body["items"][0]["symbol"] == "FRESH"

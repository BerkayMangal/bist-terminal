# ================================================================
# tests/test_bullwatch_hot_tier.py
#
# D.3 — Tier 1 (hot) cadence.
#
# Verifies:
#   - `_run_bullwatch_hot_tier` reads the top N from `bullwatch` and
#     writes a separate `bullwatch_hot` snapshot.
#   - `/api/bullwatch?tier=hot` reads the hot snapshot when present,
#     falls back to the cold snapshot when it isn't.
#   - No cold snapshot ⇒ hot tier no-op (returns None).
#
# Uses FakeRedis + mocks for engine.bullwatch.scan to keep the
# tests fast and provider-free.
# ================================================================

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from core.snapshot_store import SnapshotStore
from tests._fake_redis import FakeRedis


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def bw(monkeypatch, fake_redis):
    import api.bullwatch as bullwatch_mod
    import core.snapshot_store as snap_mod
    monkeypatch.setattr(snap_mod, "_default_store", SnapshotStore(client=fake_redis))
    bullwatch_mod._CACHE.update({
        "items": None, "as_of": None, "stale_after": 0.0,
        "running": False, "progress": 0, "total": 0, "scan_started_at": 0.0,
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
            } for s, sc in symbols
        ],
        "scanned": 437,
        "eligible_count": len(symbols),
        "ineligible_count": 437 - len(symbols),
        "cap_tl": 250_000_000,
        "near_misses": [],
        "as_of": "2026-05-12T08:00:00+00:00",
        "duration_ms": 600_000,
    }


def _seed_cold_snapshot(bw, symbols: list[tuple[str, float]]) -> None:
    """Persist a fake cold (full universe) snapshot via the existing
    bullwatch helper."""
    bw._persist_snapshot(_make_payload(symbols))


# ── 1. Hot tier reads cold snapshot, writes its own ─────────────────


def test_hot_tier_writes_separate_snapshot(monkeypatch, bw, fake_redis):
    """_run_bullwatch_hot_tier should pick up top N from cold, run a
    subset scan, and persist under `bullwatch_hot`."""
    _seed_cold_snapshot(bw, [
        ("AAA", 90.0), ("BBB", 80.0), ("CCC", 70.0),
        ("DDD", 60.0), ("EEE", 50.0),
    ])

    # Mock engine.bullwatch.scan to return a fake result for each ticker
    class FakeResult:
        def __init__(self, symbol: str, score: float):
            self.symbol = symbol
            self.score = score
            self.eligible = True
            self.zone = "CONFIRMED"

        def to_dict(self) -> dict:
            return {
                "symbol": self.symbol, "score": self.score,
                "zone": self.zone, "pattern": "Hot",
                "components": {}, "metrics": {}, "reasons": [],
            }

    captured: list = []
    def fake_scan(tickers, include_ineligible=False, max_workers=8):
        captured.append(list(tickers))
        return [FakeResult(t, 90.0 - i) for i, t in enumerate(tickers)]

    import engine.bullwatch as engine_bw
    monkeypatch.setattr(engine_bw, "scan", fake_scan)

    # Also patch the BULLWATCH_HOT_SIZE so we exercise a small slice
    import engine.background_tasks as bg
    monkeypatch.setattr(bg, "BULLWATCH_HOT_SIZE", 3)
    payload = bg._run_bullwatch_hot_tier()

    assert payload is not None
    assert captured == [["AAA", "BBB", "CCC"]]   # top 3 by score
    # Hot snapshot written under separate namespace
    assert fake_redis.exists("bb:snapshots:bullwatch_hot:latest") == 1
    hot_sid = fake_redis.get("bb:snapshots:bullwatch_hot:latest")
    assert hot_sid is not None
    # Items present
    for t in ("AAA", "BBB", "CCC"):
        assert fake_redis.exists(f"bb:snapshots:bullwatch_hot:{hot_sid}:item:{t}") == 1
    # Cold snapshot untouched
    cold_sid = fake_redis.get("bb:snapshots:bullwatch:latest")
    assert cold_sid != hot_sid


def test_hot_tier_meta_records_tier_and_size(
    monkeypatch, bw, fake_redis,
):
    _seed_cold_snapshot(bw, [("X", 50.0), ("Y", 40.0)])

    class FakeResult:
        def __init__(self, s, sc):
            self.symbol, self.score, self.eligible = s, sc, True
        def to_dict(self):
            return {"symbol": self.symbol, "score": self.score, "zone": "EARLY"}

    import engine.bullwatch as engine_bw
    monkeypatch.setattr(
        engine_bw, "scan",
        lambda tickers, **kw: [FakeResult(t, 50.0) for t in tickers],
    )

    import engine.background_tasks as bg
    monkeypatch.setattr(bg, "BULLWATCH_HOT_SIZE", 50)
    bg._run_bullwatch_hot_tier()

    sid = fake_redis.get("bb:snapshots:bullwatch_hot:latest")
    meta = json.loads(fake_redis.get(f"bb:snapshots:bullwatch_hot:{sid}:meta"))
    assert meta["tier"] == "hot"
    assert meta["size_target"] == 50
    assert meta["source_module"] == "bullwatch"
    assert meta["module"] == "bullwatch_hot"


# ── 2. No cold snapshot → hot tier no-op ────────────────────────────


def test_hot_tier_skips_when_no_cold_snapshot(bw, fake_redis):
    import engine.background_tasks as bg
    assert bg._run_bullwatch_hot_tier() is None
    assert fake_redis.exists("bb:snapshots:bullwatch_hot:latest") == 0


def test_hot_tier_skips_when_no_eligible(monkeypatch, bw, fake_redis):
    _seed_cold_snapshot(bw, [("AAA", 1.0)])

    class FakeResult:
        def __init__(self, sym):
            self.symbol = sym
            self.score = 0.0
            self.eligible = False  # nothing eligible
        def to_dict(self):
            return {"symbol": self.symbol, "score": 0, "zone": "EARLY"}

    import engine.bullwatch as engine_bw
    monkeypatch.setattr(
        engine_bw, "scan",
        lambda tickers, **kw: [FakeResult(t) for t in tickers],
    )
    import engine.background_tasks as bg
    assert bg._run_bullwatch_hot_tier() is None
    assert fake_redis.exists("bb:snapshots:bullwatch_hot:latest") == 0


# ── 3. /api/bullwatch?tier=hot reads the hot snapshot ───────────────


@pytest.mark.asyncio
async def test_endpoint_tier_hot_reads_hot_snapshot(
    monkeypatch, bw, fake_redis,
):
    # Seed BOTH cold and hot snapshots with different items
    _seed_cold_snapshot(bw, [("COLD_A", 10.0), ("COLD_B", 5.0)])

    # Write a hot snapshot directly
    from core.snapshot_store import get_default_store
    get_default_store().write_snapshot(
        "bullwatch_hot",
        scored=[("HOT_A", 99.0, {
            "symbol": "HOT_A", "score": 99.0, "zone": "CONVICTION",
            "pattern": "hot", "data_quality": "high",
            "components": {}, "metrics": {}, "reasons": [],
        })],
        meta={"tier": "hot"},
    )

    resp = await bw.api_bullwatch(
        refresh=False, min_score=0.0, zone=None, limit=50,
        cap_tl=None, diagnostic=False, tier="hot",
    )
    body = json.loads(resp.body)
    syms = {it["symbol"] for it in body["items"]}
    assert syms == {"HOT_A"}
    assert body["_meta"]["from_snapshot"] is True


@pytest.mark.asyncio
async def test_endpoint_tier_default_reads_cold_snapshot(
    monkeypatch, bw, fake_redis,
):
    _seed_cold_snapshot(bw, [("COLD_A", 10.0), ("COLD_B", 5.0)])
    from core.snapshot_store import get_default_store
    get_default_store().write_snapshot(
        "bullwatch_hot",
        scored=[("HOT_A", 99.0, {
            "symbol": "HOT_A", "score": 99.0, "zone": "EARLY",
        })],
        meta={"tier": "hot"},
    )

    resp = await bw.api_bullwatch(
        refresh=False, min_score=0.0, zone=None, limit=50,
        cap_tl=None, diagnostic=False, tier=None,
    )
    body = json.loads(resp.body)
    syms = {it["symbol"] for it in body["items"]}
    # Default tier ⇒ cold snapshot only
    assert syms == {"COLD_A", "COLD_B"}


@pytest.mark.asyncio
async def test_endpoint_tier_hot_falls_back_to_cold_when_hot_missing(
    monkeypatch, bw, fake_redis,
):
    """During the boot window before the hot loop runs, tier=hot must
    still return useful data — fall back to the cold snapshot."""
    _seed_cold_snapshot(bw, [("ONLY_COLD", 42.0)])

    resp = await bw.api_bullwatch(
        refresh=False, min_score=0.0, zone=None, limit=50,
        cap_tl=None, diagnostic=False, tier="hot",
    )
    body = json.loads(resp.body)
    syms = {it["symbol"] for it in body["items"]}
    assert syms == {"ONLY_COLD"}
    assert body["_meta"]["from_snapshot"] is True

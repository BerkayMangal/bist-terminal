# ================================================================
# tests/test_bullalfa_snapshot.py
#
# D.2 — BullAlpha ⇄ shared snapshot store.
#
# Verifies that the BullAlpha scan persists to bb:snapshots:bullalfa:*
# and that the scan endpoint can warm itself from a snapshot without
# running a live scan. Uses TestClient to drive the endpoint and a
# FakeRedis stub for the snapshot store.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.bullalfa import (
    ScanContext,
    TickerInputs,
    register_data_provider,
    reset_cache,
    reset_data_provider,
    router,
    SNAPSHOT_MODULE,
)
from core.snapshot_store import SnapshotStore
from tests._fake_redis import FakeRedis


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    return a


@pytest.fixture
def client(app, monkeypatch, fake_redis):
    import core.snapshot_store as snap_mod
    monkeypatch.setattr(snap_mod, "_default_store", SnapshotStore(client=fake_redis))
    yield TestClient(app)
    reset_cache()
    reset_data_provider()


# ── Test data builders ──────────────────────────────────────────────


def _hist(seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = np.cumsum(rng.normal(0.20, 0.30, 250)) + 100
    return pd.DataFrame({
        "Open":   closes * 0.99,
        "High":   closes * 1.01,
        "Low":    closes * 0.98,
        "Close":  closes,
        "Volume": rng.integers(800_000, 1_500_000, 250),
    })


def _bench() -> pd.DataFrame:
    rng = np.random.default_rng(43)
    return pd.DataFrame({"Close": np.cumsum(rng.normal(0.05, 0.20, 250)) + 100})


def _ctx() -> ScanContext:
    return ScanContext(
        macro_result={"regime": "risk_on", "tl_vol_pct": 30.0},
        market_status={"status": "open", "ist_time": "12:30"},
        isotonic_fits=None,
    )


def _mk_inputs(ticker: str, seed: int = 42) -> TickerInputs:
    return TickerInputs(
        ticker=ticker,
        hist_df=_hist(seed),
        bench_df=_bench(),
        metrics={"pe": 9.5, "roe": 18.0, "net_income": 1e9,
                 "revenue": 5e9, "market_cap": 5e10},
        sector_raw="Industrials",
        industry_raw=None,
        tech_pre={"atr": 1.5, "rsi": 55.0, "adx": 22.0,
                  "plus_di": 25.0, "minus_di": 18.0},
        days_listed=1000,
        halted_today=False,
    )


def _make_scan_provider(tickers: list[str]):
    async def provider():
        return _ctx(), [_mk_inputs(t, seed=hash(t) & 0xFF) for t in tickers]
    return provider


def _make_ticker_provider():
    async def provider(ticker: str):
        return _ctx(), _mk_inputs(ticker)
    return provider


# ── 1. Scan persists to snapshot store ──────────────────────────────


class TestScanPersistsSnapshot:

    def test_first_scan_writes_snapshot_keys(self, client, fake_redis):
        register_data_provider(
            scan_provider=_make_scan_provider(["AKBNK", "ASELS", "EREGL"]),
            ticker_provider=_make_ticker_provider(),
            name="test",
        )
        # Trigger a scan via the endpoint
        r = client.get("/api/bullalfa/scan")
        assert r.status_code == 200
        # Snapshot keys should now exist
        assert fake_redis.exists("bb:snapshots:bullalfa:latest") == 1
        sid = fake_redis.get("bb:snapshots:bullalfa:latest")
        assert sid is not None
        assert fake_redis.exists(f"bb:snapshots:bullalfa:{sid}:meta") == 1
        assert fake_redis.exists(f"bb:snapshots:bullalfa:{sid}:score") == 1
        # All three tickers should have item keys
        for t in ("AKBNK", "ASELS", "EREGL"):
            assert fake_redis.exists(f"bb:snapshots:bullalfa:{sid}:item:{t}") == 1

    def test_empty_scan_does_not_write_snapshot(self, client, fake_redis):
        # Default stub provider returns no tickers
        r = client.get("/api/bullalfa/scan")
        assert r.status_code == 200
        assert r.json()["meta"]["universe_size"] == 0
        assert fake_redis.exists("bb:snapshots:bullalfa:latest") == 0

    def test_snapshot_score_zset_is_opportunity_score(
        self, client, fake_redis,
    ):
        register_data_provider(
            scan_provider=_make_scan_provider(["AKBNK", "ASELS"]),
            ticker_provider=_make_ticker_provider(),
            name="test",
        )
        client.get("/api/bullalfa/scan")
        sid = fake_redis.get("bb:snapshots:bullalfa:latest")
        # zrevrange returns score-desc — verify both members present
        members = fake_redis.zrevrange(
            f"bb:snapshots:bullalfa:{sid}:score", 0, -1, withscores=True,
        )
        symbols = {m for m, _ in members}
        assert symbols == {"AKBNK", "ASELS"}


# ── 2. Cold-start uses snapshot when available ──────────────────────


class TestSnapshotFirstColdStart:

    def test_cold_start_with_snapshot_skips_live_scan(
        self, client, fake_redis,
    ):
        # First scan populates the snapshot
        register_data_provider(
            scan_provider=_make_scan_provider(["AKBNK", "ASELS"]),
            ticker_provider=_make_ticker_provider(),
            name="test1",
        )
        client.get("/api/bullalfa/scan")
        # Wipe the in-mem cache (simulating a process restart)
        reset_cache()
        # Register a provider that would FAIL if invoked — proves the
        # cold-start path read from snapshot rather than running live.
        async def angry_provider():
            raise RuntimeError("should not be called — snapshot is present")
        register_data_provider(
            scan_provider=angry_provider,
            ticker_provider=_make_ticker_provider(),
            name="test2",
        )
        r = client.get("/api/bullalfa/scan")
        assert r.status_code == 200
        body = r.json()
        # Universe size came from snapshot meta
        assert body["meta"]["universe_size"] == 2
        # New meta fields
        assert body["meta"]["from_snapshot"] is True
        assert "scan_id" in body["meta"]
        # Signals reconstituted
        syms = {s["ticker"] for s in body["signals"]}
        assert syms == {"AKBNK", "ASELS"}

    def test_cold_start_without_snapshot_falls_through_to_live(
        self, client, fake_redis,
    ):
        # No snapshot exists. Default provider returns empty universe.
        r = client.get("/api/bullalfa/scan")
        assert r.status_code == 200
        body = r.json()
        assert body["meta"]["from_snapshot"] is False
        # No scan_id field when not from snapshot
        assert "scan_id" not in body["meta"]


# ── 3. Corrupted snapshot falls back to previous ────────────────────


class TestSnapshotFallback:

    def test_corrupt_latest_falls_back_to_previous(
        self, client, fake_redis,
    ):
        register_data_provider(
            scan_provider=_make_scan_provider(["OLD_T"]),
            ticker_provider=_make_ticker_provider(),
            name="test",
        )
        client.get("/api/bullalfa/scan")
        sid_old = fake_redis.get("bb:snapshots:bullalfa:latest")

        register_data_provider(
            scan_provider=_make_scan_provider(["NEW_T"]),
            ticker_provider=_make_ticker_provider(),
            name="test",
        )
        # Force refresh ↦ second scan
        client.get("/api/bullalfa/scan/refresh")
        sid_new = fake_redis.get("bb:snapshots:bullalfa:latest")
        assert sid_old != sid_new

        # Corrupt the new snapshot
        fake_redis.delete(f"bb:snapshots:bullalfa:{sid_new}:meta")

        # Wipe in-mem cache so the endpoint has to consult the snapshot
        reset_cache()
        # Register a provider that would fail if invoked — fallback must
        # promote the previous snapshot rather than running live
        async def angry_provider():
            raise RuntimeError("should not be called — fallback expected")
        register_data_provider(
            scan_provider=angry_provider,
            ticker_provider=_make_ticker_provider(),
            name="test",
        )
        r = client.get("/api/bullalfa/scan")
        body = r.json()
        assert body["meta"]["from_snapshot"] is True
        # Fallback promoted previous → OLD_T should be back
        syms = {s["ticker"] for s in body["signals"]}
        assert syms == {"OLD_T"}


# ── 4. No scoring regression — opportunity_score round-trips ────────


class TestNoScoringRegression:

    def test_opportunity_score_unchanged_through_snapshot(
        self, client, fake_redis,
    ):
        register_data_provider(
            scan_provider=_make_scan_provider(["AKBNK", "ASELS", "EREGL"]),
            ticker_provider=_make_ticker_provider(),
            name="test",
        )
        r1 = client.get("/api/bullalfa/scan")
        body1 = r1.json()
        scores1 = {s["ticker"]: s["opportunity_score"] for s in body1["signals"]}

        # Cold-start path
        reset_cache()
        async def angry_provider():
            raise RuntimeError("scan provider should not be called")
        register_data_provider(
            scan_provider=angry_provider,
            ticker_provider=_make_ticker_provider(),
            name="test",
        )
        r2 = client.get("/api/bullalfa/scan")
        body2 = r2.json()
        scores2 = {s["ticker"]: s["opportunity_score"] for s in body2["signals"]}

        assert scores1 == scores2

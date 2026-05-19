# ================================================================
# tests/test_bullalfa_api.py
#
# Spec §20 + §21 coverage:
#   - GET /api/bullalfa/scan: pagination, filters, schema, cache TTL
#   - GET /api/bullalfa/{ticker}: live per-ticker (bypasses scan cache)
#   - POST/GET /api/bullalfa/scan/refresh: invalidates and rebuilds
#   - Circuit breaker after 5 consecutive provider failures
#   - Provider DI — register / reset / stub fallback
# ================================================================

from __future__ import annotations

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
)
from engine.bullalfa_params import (
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    SCAN_DEFAULT_PAGE_SIZE,
)


# ================================================================
# Test app + fixtures
# ================================================================

@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    return a


@pytest.fixture
def client(app):
    yield TestClient(app)
    # Clean up between tests so cache + provider don't leak.
    reset_cache()
    reset_data_provider()


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    """These tests exercise BullAlfa endpoint logic, not rate limiting
    (audit M1 added an ops_heavy limit to /scan/refresh). The minimal
    test app has no RateLimitExceeded handler, and the circuit-breaker
    tests legitimately call /scan/refresh more than the limit allows.
    Disable rate limiting here — the dedicated rate-limit tests live in
    their own module."""
    import core.rate_limiter as _rl
    _saved = _rl.RATE_LIMIT_ENABLED
    _rl.RATE_LIMIT_ENABLED = False
    yield
    _rl.RATE_LIMIT_ENABLED = _saved


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


def _metrics() -> dict:
    return {
        "pe": 9.5, "roe": 18.0, "net_income": 1e9,
        "revenue": 5e9, "market_cap": 5e10,
    }


def _tech() -> dict:
    return {"atr": 1.5, "rsi": 55.0, "adx": 22.0,
            "plus_di": 25.0, "minus_di": 18.0}


def _ctx(macro_regime: str = "risk_on") -> ScanContext:
    return ScanContext(
        macro_result={"regime": macro_regime, "tl_vol_pct": 30.0},
        market_status={"status": "open", "ist_time": "12:30"},
        isotonic_fits=None,
    )


def _mk_inputs(ticker: str, sector: str = "Industrials",
               seed: int = 42, halted: bool = False) -> TickerInputs:
    return TickerInputs(
        ticker=ticker,
        hist_df=_hist(seed),
        bench_df=_bench(),
        metrics=_metrics(),
        sector_raw=sector,
        industry_raw=None,
        tech_pre=_tech(),
        days_listed=1000,
        halted_today=halted,
    )


# ----------------------------------------------------------------
# Provider builders
# ----------------------------------------------------------------

def _make_scan_provider(tickers: list[str], halted: set[str] | None = None,
                       macro_regime: str = "risk_on"):
    halted = halted or set()
    async def provider():
        ctx = _ctx(macro_regime)
        inputs = [
            _mk_inputs(t, halted=t in halted, seed=hash(t) & 0xFF)
            for t in tickers
        ]
        return ctx, inputs
    return provider


def _make_ticker_provider(known: dict[str, str] | None = None):
    """Returns inputs for any ticker. Optional `known` dict pins
    sector_raw per ticker."""
    known = known or {}
    async def provider(ticker: str):
        return _ctx(), _mk_inputs(ticker, sector=known.get(ticker, "Industrials"))
    return provider


def _make_failing_scan_provider():
    async def provider():
        raise RuntimeError("simulated upstream outage")
    return provider


# ================================================================
# /api/bullalfa/scan — basic shape
# ================================================================

class TestScanEndpoint:

    def test_empty_when_no_provider_configured(self, client):
        # Default stub provider returns no tickers.
        r = client.get("/api/bullalfa/scan")
        assert r.status_code == 200
        body = r.json()
        assert body["meta"]["universe_size"] == 0
        assert body["signals"] == []

    def test_returns_signals_with_default_pagination(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider(
                ["AKBNK", "ASELS", "EREGL", "FROTO", "BIMAS"]
            ),
            ticker_provider=_make_ticker_provider(),
            name="test",
        )
        r = client.get("/api/bullalfa/scan")
        assert r.status_code == 200
        body = r.json()
        assert body["meta"]["universe_size"] == 5
        assert body["meta"]["schema_version"] == "1.4"
        assert body["meta"]["provider"] == "test"
        assert len(body["signals"]) == 5
        # Default page size is 50, our universe is 5 — single page.
        assert body["meta"]["pagination"]["page"] == 1
        assert body["meta"]["pagination"]["per_page"] == SCAN_DEFAULT_PAGE_SIZE
        assert body["meta"]["pagination"]["total"] == 5

    def test_pagination_first_page(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider(
                [f"T{i:03d}" for i in range(75)]
            ),
            ticker_provider=_make_ticker_provider(),
        )
        r = client.get("/api/bullalfa/scan?page=1&per_page=20")
        assert r.status_code == 200
        body = r.json()
        assert len(body["signals"]) == 20
        assert body["meta"]["pagination"]["total"] == 75

    def test_pagination_last_page_partial(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider(
                [f"T{i:03d}" for i in range(75)]
            ),
            ticker_provider=_make_ticker_provider(),
        )
        r = client.get("/api/bullalfa/scan?page=4&per_page=20")
        body = r.json()
        # 75 total, page 4 with 20 per_page → 15 items
        assert len(body["signals"]) == 15

    def test_per_page_clamped_at_200(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider(["X"]),
            ticker_provider=_make_ticker_provider(),
        )
        r = client.get("/api/bullalfa/scan?per_page=500")
        # FastAPI Query(le=200) rejects 500 with 422.
        assert r.status_code == 422

    def test_invalid_page_rejected(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider(["X"]),
            ticker_provider=_make_ticker_provider(),
        )
        r = client.get("/api/bullalfa/scan?page=0")
        assert r.status_code == 422


# ================================================================
# /api/bullalfa/scan — filters
# ================================================================

class TestScanFilters:

    def test_filter_by_mode(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider(
                ["A", "B", "C", "D"], halted={"D"},
            ),
            ticker_provider=_make_ticker_provider(),
        )
        r = client.get("/api/bullalfa/scan?mode=UZAK DUR")
        body = r.json()
        # Only the halted ticker should match.
        assert all(s["mode"] == "UZAK DUR" for s in body["signals"])
        assert len(body["signals"]) == 1
        assert body["signals"][0]["ticker"] == "D"

    def test_filter_by_sector(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider(["AKBNK", "ASELS", "EREGL"]),
            ticker_provider=_make_ticker_provider({
                "AKBNK": "Financial Services",
                "ASELS": "Industrials",
                "EREGL": "Basic Materials",
            }),
        )
        r = client.get("/api/bullalfa/scan?sector=sanayi")
        body = r.json()
        assert all(s["sector_group"] == "sanayi" for s in body["signals"])

    def test_filter_with_pagination_consistent(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider(
                ["A", "B", "C", "D"], halted={"A", "B", "C", "D"},
            ),
            ticker_provider=_make_ticker_provider(),
        )
        r = client.get("/api/bullalfa/scan?mode=UZAK DUR&per_page=2")
        body = r.json()
        assert len(body["signals"]) == 2
        # Total reflects the FILTERED universe, not the raw scan size.
        assert body["meta"]["pagination"]["total"] == 4

    def test_filter_no_matches_returns_empty(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider(["AKBNK"]),
            ticker_provider=_make_ticker_provider(),
        )
        r = client.get("/api/bullalfa/scan?mode=HIZLI")
        body = r.json()
        # Synthetic uptrend with default tech doesn't fire HIZLI.
        if body["signals"]:
            assert all(s["mode"] == "HIZLI" for s in body["signals"])


# ================================================================
# /api/bullalfa/{ticker}
# ================================================================

class TestPerTickerEndpoint:

    def test_returns_one_signal(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider([]),
            ticker_provider=_make_ticker_provider({"ASELS": "Industrials"}),
        )
        r = client.get("/api/bullalfa/ASELS")
        assert r.status_code == 200
        body = r.json()
        assert body["schema_version"] == "1.4"
        assert body["signal"]["ticker"] == "ASELS"
        assert body["signal"]["sector_group"] == "sanayi"

    def test_ticker_uppercased(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider([]),
            ticker_provider=_make_ticker_provider(),
        )
        r = client.get("/api/bullalfa/asels")
        assert r.status_code == 200
        assert r.json()["signal"]["ticker"] == "ASELS"

    def test_no_provider_returns_503(self, client):
        # Default stub raises 503 with a hint about register_data_provider.
        r = client.get("/api/bullalfa/ASELS")
        assert r.status_code == 503

    def test_provider_exception_returns_502(self, client):
        async def boom(ticker: str):
            raise RuntimeError("network down")
        register_data_provider(
            scan_provider=_make_scan_provider([]),
            ticker_provider=boom,
        )
        r = client.get("/api/bullalfa/X")
        assert r.status_code == 502


# ================================================================
# Cache behavior
# ================================================================

class TestScanCache:

    def test_second_call_reuses_cache(self, client, monkeypatch):
        call_count = {"n": 0}

        async def counting_provider():
            call_count["n"] += 1
            ctx = _ctx()
            return ctx, [_mk_inputs("X")]

        register_data_provider(
            scan_provider=counting_provider,
            ticker_provider=_make_ticker_provider(),
        )

        r1 = client.get("/api/bullalfa/scan")
        r2 = client.get("/api/bullalfa/scan")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert call_count["n"] == 1, "provider should be called once during cache TTL"

    def test_force_refresh_calls_provider_again(self, client):
        call_count = {"n": 0}

        async def counting_provider():
            call_count["n"] += 1
            return _ctx(), [_mk_inputs("X")]

        register_data_provider(
            scan_provider=counting_provider,
            ticker_provider=_make_ticker_provider(),
        )

        client.get("/api/bullalfa/scan")
        client.get("/api/bullalfa/scan/refresh")
        client.get("/api/bullalfa/scan")
        assert call_count["n"] == 2

    def test_per_ticker_does_not_use_scan_cache(self, client):
        scan_calls = {"n": 0}
        ticker_calls = {"n": 0}

        async def s_prov():
            scan_calls["n"] += 1
            return _ctx(), [_mk_inputs("X")]

        async def t_prov(ticker: str):
            ticker_calls["n"] += 1
            return _ctx(), _mk_inputs(ticker)

        register_data_provider(scan_provider=s_prov, ticker_provider=t_prov)

        client.get("/api/bullalfa/scan")
        # Per-ticker call should hit ticker_provider, NOT scan_provider.
        client.get("/api/bullalfa/X")
        client.get("/api/bullalfa/X")
        assert scan_calls["n"] == 1
        assert ticker_calls["n"] == 2


# ================================================================
# Circuit breaker
# ================================================================

class TestCircuitBreaker:

    def test_consecutive_failures_trip_breaker(self, client):
        register_data_provider(
            scan_provider=_make_failing_scan_provider(),
            ticker_provider=_make_ticker_provider(),
        )

        # Force a fresh cache miss N+1 times.
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD + 1):
            client.get("/api/bullalfa/scan/refresh")

        r = client.get("/api/bullalfa/scan/refresh")
        body = r.json()
        # Circuit should be tripped — frozen_reason populated.
        # Direct way to check: query /scan and look at meta.circuit_breaker.
        r2 = client.get("/api/bullalfa/scan")
        assert r2.json()["meta"]["circuit_breaker"]["frozen"] is True

    def test_circuit_breaker_recovers_on_successful_scan(self, client):
        # Start with failures, then swap to a working provider.
        register_data_provider(
            scan_provider=_make_failing_scan_provider(),
            ticker_provider=_make_ticker_provider(),
        )
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD + 1):
            client.get("/api/bullalfa/scan/refresh")

        register_data_provider(
            scan_provider=_make_scan_provider(["AKBNK", "ASELS"]),
            ticker_provider=_make_ticker_provider(),
        )
        # audit M3 — the breaker now stays OPEN for a cooldown before a
        # half-open trial. Age the trip time past the cooldown so the
        # next scan is the half-open trial that recovers the breaker.
        from api.bullalfa import _CACHE
        _CACHE.frozen_at = 0.0
        client.get("/api/bullalfa/scan/refresh")
        r = client.get("/api/bullalfa/scan")
        assert r.json()["meta"]["circuit_breaker"]["frozen"] is False
        assert r.json()["meta"]["circuit_breaker"]["consecutive_failures"] == 0


# ================================================================
# Schema invariants — every endpoint emits §19-shaped payloads
# ================================================================

class TestSchemaInvariants:

    def test_scan_response_has_all_meta_keys(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider(["X"]),
            ticker_provider=_make_ticker_provider(),
        )
        body = client.get("/api/bullalfa/scan").json()
        for key in ("generated_at", "universe_size", "by_mode",
                    "sector_concentration", "warnings", "pagination",
                    "schema_version", "cache_as_of", "provider",
                    "circuit_breaker"):
            assert key in body["meta"], f"missing meta key {key}"

    def test_per_ticker_response_has_signal_and_schema(self, client):
        register_data_provider(
            scan_provider=_make_scan_provider([]),
            ticker_provider=_make_ticker_provider(),
        )
        body = client.get("/api/bullalfa/X").json()
        assert "signal" in body
        assert "schema_version" in body
        # All §19 top-level keys.
        for key in ("ticker", "sector_group", "generated_at", "schema_version",
                    "quality", "macro", "mode", "horizon_bars", "horizon_label",
                    "why_now", "engines", "confidence", "opportunity_score",
                    "risk_frame", "lifecycle", "liquidity", "explainer"):
            assert key in body["signal"], f"missing signal key {key}"

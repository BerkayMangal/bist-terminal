# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# api/bullalfa.py
#
# §20 — `GET /api/bullalfa/scan` and `GET /api/bullalfa/{ticker}`.
#
# Spec §21 caching strategy:
#   - Background batch refresh every 5min; scan endpoint serves cache
#   - Per-ticker endpoint always live (bypasses scan cache)
#   - Pagination default 50 per page
#   - Circuit breaker freezes scan after 5 consecutive external failures
#
# Architecture decision: dependency injection.
#
# `engine.bullalfa.build_bullalfa_signal` is pure given inputs — it
# does NOT fetch data. The orchestrator's data dependencies (hist_df,
# bench_df, metrics, sector_raw, macro_result, market_status) live in
# this layer's adapter, not the engine. We expose `register_data_provider`
# so the consumer (production app) can wire in the real fetchers
# without coupling this module to specific data-layer internals.
#
# Tests pass a mock provider; production wires the existing data
# pipeline. Either way, the orchestrator stays pure.
#
# Backward-compat rule: this module ONLY adds new endpoints. It does
# not import or modify existing endpoint logic. `app.include_router(
# bullalfa_router)` is the user's call to wire it up.
# ================================================================

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException, Query

from engine.bullalfa import build_bullalfa_signal, build_scan_response
from engine.bullalfa_params import (
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    SCAN_BATCH_REFRESH_SEC,
    SCAN_DEFAULT_PAGE_SIZE,
    SCHEMA_VERSION,
)

log = logging.getLogger("bistbull.bullalfa_api")
router = APIRouter()


# ================================================================
# Dependency-injection types
# ================================================================

@dataclass
class TickerInputs:
    """Inputs the orchestrator needs for one ticker.

    The fields mirror `build_bullalfa_signal`'s kwargs so the API
    layer is a thin pass-through. A provider returns this dataclass
    per ticker (or None for "unavailable — skip").
    """

    ticker:        str
    hist_df:       Any                          # pandas.DataFrame
    bench_df:      Optional[Any]                # pandas.DataFrame
    metrics:       Optional[dict]
    sector_raw:    Optional[str]                = None
    industry_raw:  Optional[str]                = None
    short_history: Optional[bool]               = None
    halted_today:  bool                         = False
    tech_pre:      Optional[dict]               = None
    days_listed:   Optional[int]                = None


@dataclass
class ScanContext:
    """Cross-ticker state passed once per scan.

    `macro_result` and `market_status` are computed once per scan and
    reused for all tickers (they're scan-wide, not per-ticker). The
    consumer's data provider produces this once and feeds it to every
    `build_bullalfa_signal` call inside the scan loop.
    """

    macro_result:  Optional[dict] = None
    market_status: Optional[dict] = None
    isotonic_fits: Optional[dict] = None


# Provider signatures.
ScanProvider     = Callable[[], Awaitable[tuple[ScanContext, list[TickerInputs]]]]
TickerProvider   = Callable[[str], Awaitable[tuple[ScanContext, TickerInputs]]]


# ----------------------------------------------------------------
# Provider registry — set at app startup by the consumer
# ----------------------------------------------------------------

_DEFAULT_PROVIDER_NAME = "stub"


async def _stub_scan_provider() -> tuple[ScanContext, list[TickerInputs]]:
    """Default provider — returns no tickers. The API still works
    (returns an empty universe) but warns that no provider is wired."""
    log.warning(
        "bullalfa: no scan_provider registered; returning empty universe. "
        "Call register_data_provider(...) at app startup."
    )
    return ScanContext(), []


async def _stub_ticker_provider(ticker: str) -> tuple[ScanContext, TickerInputs]:
    raise HTTPException(
        status_code=503,
        detail=(
            "bullalfa data provider not configured. "
            "Call register_data_provider(...) with a real ticker provider."
        ),
    )


_PROVIDERS: dict[str, Any] = {
    "scan_provider":   _stub_scan_provider,
    "ticker_provider": _stub_ticker_provider,
    "name":            _DEFAULT_PROVIDER_NAME,
}


def register_data_provider(
    *,
    scan_provider:   ScanProvider,
    ticker_provider: TickerProvider,
    name:            str = "production",
) -> None:
    """Wire real data fetchers into the API.

    Call once at app startup, before any scan request lands.
    `scan_provider` is invoked by the background batch refresher;
    `ticker_provider` is invoked by `GET /api/bullalfa/{ticker}`.

    Both providers are async to allow concurrent fetches and to
    integrate with the existing data layer (which is async in places).
    """
    _PROVIDERS["scan_provider"]   = scan_provider
    _PROVIDERS["ticker_provider"] = ticker_provider
    _PROVIDERS["name"]            = name
    log.info("bullalfa: data provider registered (%s)", name)


def get_provider_name() -> str:
    return str(_PROVIDERS.get("name", _DEFAULT_PROVIDER_NAME))


def reset_data_provider() -> None:
    """Restore the stub provider — used by tests for isolation."""
    _PROVIDERS["scan_provider"]   = _stub_scan_provider
    _PROVIDERS["ticker_provider"] = _stub_ticker_provider
    _PROVIDERS["name"]            = _DEFAULT_PROVIDER_NAME


# ================================================================
# Cache + circuit breaker
# ================================================================

@dataclass
class _ScanCache:
    payload:       Optional[dict] = None
    as_of:         Optional[str]  = None
    expires_at:    float          = 0.0
    consecutive_failures: int     = 0
    is_frozen:     bool           = False
    frozen_reason: Optional[str]  = None


_CACHE = _ScanCache()
_CACHE_LOCK: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    """Lazily allocate a per-event-loop lock — using `asyncio.Lock()`
    at import time can latch onto a loop the app never uses."""
    global _CACHE_LOCK
    if _CACHE_LOCK is None:
        _CACHE_LOCK = asyncio.Lock()
    return _CACHE_LOCK


def _circuit_break(reason: str) -> None:
    _CACHE.is_frozen = True
    _CACHE.frozen_reason = reason
    log.warning("bullalfa scan circuit-breaker tripped: %s", reason)


def _circuit_reset() -> None:
    _CACHE.consecutive_failures = 0
    _CACHE.is_frozen = False
    _CACHE.frozen_reason = None


def reset_cache() -> None:
    """Clear the in-memory cache. Used by tests for isolation."""
    _CACHE.payload = None
    _CACHE.as_of = None
    _CACHE.expires_at = 0.0
    _circuit_reset()


# ----------------------------------------------------------------
# Core scan runner — invoked by the endpoint and by the refresher
# ----------------------------------------------------------------

async def _run_scan() -> dict:
    """Execute one scan: provider → orchestrator per ticker → assemble.

    Returns the §19 ScanResponse dict. Updates the cache on success.
    On failure, increments the consecutive-failure counter; trips the
    circuit breaker if it exceeds the threshold.
    """
    scan_provider = _PROVIDERS["scan_provider"]
    try:
        ctx, ticker_inputs = await scan_provider()
    except Exception as exc:
        log.exception("scan provider failed: %s", exc)
        _CACHE.consecutive_failures += 1
        if _CACHE.consecutive_failures >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            _circuit_break("Veri akışı geçici olarak duraklatıldı")
        # Return last-known cache if any, else empty.
        if _CACHE.payload is not None:
            return _CACHE.payload
        return _empty_payload()

    signals: list[dict] = []
    per_ticker_failures = 0
    for ti in ticker_inputs:
        try:
            sig = build_bullalfa_signal(
                ticker=ti.ticker,
                hist_df=ti.hist_df, bench_df=ti.bench_df,
                metrics=ti.metrics,
                sector_raw=ti.sector_raw, industry_raw=ti.industry_raw,
                short_history=ti.short_history, halted_today=ti.halted_today,
                macro_result=ctx.macro_result,
                market_status=ctx.market_status,
                isotonic_fits=ctx.isotonic_fits,
                tech_pre=ti.tech_pre, days_listed=ti.days_listed,
            )
            signals.append(sig)
        except Exception as exc:
            # build_bullalfa_signal is supposed to never raise (it
            # degrades to SAKİN on every failure path). If one slips
            # through, log and skip — don't kill the whole scan.
            log.warning("build_bullalfa_signal raised for %s: %s", ti.ticker, exc)
            per_ticker_failures += 1

    extra_warnings: list[str] = []
    if per_ticker_failures > 0:
        extra_warnings.append(
            f"{per_ticker_failures} hisse için sinyal üretilemedi"
        )
    if _CACHE.is_frozen and _CACHE.frozen_reason:
        extra_warnings.append(_CACHE.frozen_reason)

    payload = build_scan_response(signals, extra_warnings=extra_warnings)
    _CACHE.payload = payload
    _CACHE.as_of = payload["meta"]["generated_at"]
    _CACHE.expires_at = time.time() + SCAN_BATCH_REFRESH_SEC
    _circuit_reset()
    return payload


def _empty_payload() -> dict:
    """Empty ScanResponse — used when nothing is cached yet and the
    provider failed."""
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "signals": [],
        "meta": {
            "generated_at":         now,
            "universe_size":        0,
            "by_mode":              {},
            "sector_concentration": {},
            "warnings":             ["Veri akışı geçici olarak duraklatıldı"]
                                    if _CACHE.is_frozen else [],
        },
    }


def _cache_is_fresh() -> bool:
    return _CACHE.payload is not None and time.time() < _CACHE.expires_at


# Module-level handle to the in-flight background scan task. The
# endpoint kicks one of these off when the cache is empty or stale,
# but never awaits its completion blocking — the user gets an
# immediate response (empty + warming_up if cache is None, stale
# otherwise).
_BG_SCAN_TASK: Optional[asyncio.Task] = None


async def _ensure_background_scan() -> bool:
    """Trigger a background scan iff one isn't already running.

    Returns True if a new task was scheduled, False if one was already
    in flight.
    """
    global _BG_SCAN_TASK
    if _BG_SCAN_TASK is not None and not _BG_SCAN_TASK.done():
        return False  # already running

    async def _wrapped() -> None:
        try:
            async with _get_lock():
                # Re-check freshness inside the lock — another task may
                # have completed between our trigger and the lock acquire.
                if _cache_is_fresh():
                    return
                await _run_scan()
        except Exception as exc:
            log.exception("background scan failed: %s", exc)

    _BG_SCAN_TASK = asyncio.create_task(_wrapped())
    return True


# ================================================================
# Endpoints
# ================================================================

@router.get("/api/bullalfa/scan")
async def get_scan(
    page:     int = Query(1,  ge=1,  description="1-indexed page number"),
    per_page: int = Query(SCAN_DEFAULT_PAGE_SIZE, ge=1, le=200,
                          description=f"Page size (default {SCAN_DEFAULT_PAGE_SIZE}, max 200)"),
    mode:     Optional[str] = Query(None, description=
                                    "Filter to a specific mode "
                                    "(HIZLI/SWING/POZİSYON/TOPLANIYOR/SAKİN/UZAK DUR)"),
    sector:   Optional[str] = Query(None, description=
                                    "Filter to a specific sector_group "
                                    "(banka/holding/gyo/sanayi/...)"),
) -> dict:
    """Default scan endpoint — paginated, cached.

    Cache TTL: `SCAN_BATCH_REFRESH_SEC` (5 minutes). When stale or
    missing, refreshes inline; the next caller within the TTL gets
    the warm result.

    Filters (mode, sector) are applied AFTER the underlying scan so
    pagination is consistent with the scan view.
    """
    cache_warming = False
    if _CACHE.payload is None:
        # Cold start — kick off the scan, wait briefly for cache to
        # fill. Tests with fast in-memory providers complete the scan
        # in <100ms; production with 437 BIST tickers takes minutes,
        # in which case we fall through to warming_up.
        await _ensure_background_scan()
        if _BG_SCAN_TASK is not None and not _BG_SCAN_TASK.done():
            try:
                # asyncio.shield prevents wait_for from cancelling the
                # bg task on timeout — it continues running in the bg.
                await asyncio.wait_for(asyncio.shield(_BG_SCAN_TASK), timeout=8.0)
            except (asyncio.TimeoutError, Exception):
                pass
        if _CACHE.payload is None:
            cache_warming = True
    elif not _cache_is_fresh():
        # Stale — kick off a refresh, but serve the stale payload now.
        await _ensure_background_scan()

    payload = _CACHE.payload or _empty_payload()
    signals = payload.get("signals", [])

    # Apply filters BEFORE re-paginating so client-side filtering
    # doesn't see torn pages.
    if mode:
        signals = [s for s in signals if s.get("mode") == mode]
    if sector:
        signals = [s for s in signals if s.get("sector_group") == sector]

    # Re-assemble with the requested page parameters. The underlying
    # scan was already sorted by opportunity_score DESC; re-sorting is
    # a no-op for well-behaved input but kept for safety.
    response = build_scan_response(
        signals,
        page=page,
        per_page=per_page,
        extra_warnings=payload.get("meta", {}).get("warnings", []),
    )
    response["meta"]["schema_version"]  = SCHEMA_VERSION
    response["meta"]["cache_as_of"]     = _CACHE.as_of
    response["meta"]["provider"]        = get_provider_name()
    response["meta"]["circuit_breaker"] = {
        "frozen":               _CACHE.is_frozen,
        "consecutive_failures": _CACHE.consecutive_failures,
    }
    if cache_warming:
        response["meta"]["warming_up"] = True
        warnings = list(response["meta"].get("warnings", []))
        msg = "Hisseler hazırlanıyor — ilk scan ~1-3 dakika sürer"
        if msg not in warnings:
            warnings.append(msg)
        response["meta"]["warnings"] = warnings
    return response


@router.get("/api/bullalfa/scan/refresh")
async def force_refresh() -> dict:
    """Manually invalidate + rebuild the scan cache. Useful for ops.

    Note: this only invalidates the freshness window, not the circuit
    breaker state. Consecutive failures continue to accumulate across
    forced refreshes — that's deliberate, so a flapping upstream
    can't be papered over by repeatedly hitting refresh.
    """
    async with _get_lock():
        _CACHE.expires_at = 0.0
        await _run_scan()
    return {
        "ok":             True,
        "as_of":          _CACHE.as_of,
        "universe_size": (
            (_CACHE.payload or {}).get("meta", {}).get("universe_size", 0)
        ),
    }


@router.get("/api/bullalfa/{ticker}")
async def get_ticker(ticker: str) -> dict:
    """Live per-ticker endpoint — bypasses the scan cache.

    Spec §21: "Per-ticker endpoint always live (bypasses scan cache)".
    """
    if not ticker or not ticker.strip():
        raise HTTPException(status_code=400, detail="empty ticker")

    ticker_provider = _PROVIDERS["ticker_provider"]
    try:
        ctx, ti = await ticker_provider(ticker.upper())
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("ticker provider failed for %s: %s", ticker, exc)
        raise HTTPException(
            status_code=502,
            detail=f"data provider error for {ticker}",
        ) from exc

    sig = build_bullalfa_signal(
        ticker=ti.ticker,
        hist_df=ti.hist_df, bench_df=ti.bench_df,
        metrics=ti.metrics,
        sector_raw=ti.sector_raw, industry_raw=ti.industry_raw,
        short_history=ti.short_history, halted_today=ti.halted_today,
        macro_result=ctx.macro_result,
        market_status=ctx.market_status,
        isotonic_fits=ctx.isotonic_fits,
        tech_pre=ti.tech_pre, days_listed=ti.days_listed,
    )
    return {"signal": sig, "schema_version": SCHEMA_VERSION}


# ================================================================
# Background refresher — opt-in
# ================================================================

async def warmup_cache_loop() -> None:
    """Background task that keeps the scan cache warm.

    Wire-up pattern (mirrors `api/bullwatch.warmup_cache_loop`):

        @app.on_event("startup")
        async def _start_bullalfa_refresher() -> None:
            asyncio.create_task(api.bullalfa.warmup_cache_loop())

    The loop refreshes every `SCAN_BATCH_REFRESH_SEC` and never raises
    out. On unexpected error it logs and sleeps the same interval —
    next tick retries.
    """
    while True:
        try:
            async with _get_lock():
                await _run_scan()
        except Exception as exc:
            log.exception("warmup loop iteration failed: %s", exc)
        try:
            await asyncio.sleep(SCAN_BATCH_REFRESH_SEC)
        except asyncio.CancelledError:
            log.info("bullalfa warmup loop cancelled")
            return


# ================================================================
# Module-level exports for convenience
# ================================================================

__all__ = [
    "router",
    "TickerInputs",
    "ScanContext",
    "register_data_provider",
    "reset_data_provider",
    "reset_cache",
    "warmup_cache_loop",
    "get_provider_name",
]

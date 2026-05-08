# ================================================================
# BULLWATCH API — GET /api/bullwatch
#
# Returns ranked low-float BIST candidates with scores, zones, and
# descriptive pattern labels. Cached for 5 minutes (scan is ~30-60s
# end-to-end so we don't want to re-run on every page load).
#
# Backward-compat rule: this module ONLY adds new endpoints. It
# does not import or modify any existing endpoint logic.
# ================================================================

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from core.response_envelope import success, error

log = logging.getLogger("bistbull.bullwatch_api")
router = APIRouter()


# In-memory cache — deliberately simple. The scan is idempotent so
# concurrent requests during a refresh are safe (last-write-wins).
_CACHE: dict[str, Any] = {
    "items": None,
    "as_of": None,
    "stale_after": 0.0,
    "running": False,
}
_CACHE_TTL_SEC = 300  # 5 minutes
_SCAN_DONE: Optional[asyncio.Event] = None  # set by the runner, awaited by waiters
_SCAN_MAX_WAIT_SEC = 300  # hard ceiling — even slow scans should fit (yfinance can be flaky)


def _resolve_universe() -> list[str]:
    """
    BullWatch universe = UNIVERSE_EXTRA + UNIVERSE_EXTENDED only.

    BIST30 (top 30 large-caps) is intentionally EXCLUDED — those names have
    market caps in the tens of billions of TL, and even with 5% free float
    their float-mcap is in the billions, hopelessly above any sensible cap.
    Scanning them just wastes ~30 metrics-fetch calls per scan.

    Power users can still inspect a BIST30 name via /api/bullwatch/{symbol}.
    """
    out: list[str] = []
    try:
        from config import UNIVERSE_EXTRA
        out.extend(UNIVERSE_EXTRA)
    except ImportError:
        pass
    try:
        from config import UNIVERSE_EXTENDED
        out.extend(UNIVERSE_EXTENDED)
    except ImportError:
        pass
    if not out:
        # Fallback: use full UNIVERSE if neither EXTRA nor EXTENDED is exposed
        try:
            from config import UNIVERSE
            out.extend(UNIVERSE)
        except ImportError:
            return []
    # Dedupe while preserving order
    return list(dict.fromkeys(out))


def _run_scan(min_score: float = 0.0,
              limit: Optional[int] = None,
              cap_tl: Optional[float] = None,
              diagnostic: bool = False) -> dict[str, Any]:
    """Synchronous scan — called from a thread executor.

    Always includes a `near_misses` array in the response: the 20
    ineligible-but-closest-to-passing symbols sorted by float mcap
    ascending. This guarantees the empty state is never empty —
    user sees what JUST missed and why, no second request needed.
    The `diagnostic` flag is kept for backward-compat but no longer
    changes the response shape (always full).
    """
    from engine.bullwatch import scan
    from features.bullwatch_features import FLOAT_MARKET_CAP_CAP_TL
    universe = _resolve_universe()
    t0 = time.time()
    # Always include_ineligible=True — it's just a filter on already-computed
    # results, no extra fetches. Lets us surface near_misses for free.
    # max_workers=16 — yfinance fetches are I/O-bound, GIL doesn't apply.
    results = scan(universe, min_score=min_score,
                   include_ineligible=True, cap_tl=cap_tl,
                   max_workers=16)
    eligible = [r for r in results if r.eligible]
    if limit:
        eligible = eligible[:limit]

    # Near-misses: ineligible with KNOWN float mcap, sorted ascending so
    # the smallest (= closest to passing) come first.
    ineligible = [
        r for r in results
        if not r.eligible and r.metrics.get("float_market_cap") is not None
    ]
    ineligible.sort(key=lambda r: r.metrics.get("float_market_cap") or 0)
    near_misses = [
        {
            "symbol": r.symbol,
            "float_market_cap": r.metrics.get("float_market_cap"),
            "market_cap": r.metrics.get("market_cap"),
            "free_float": r.metrics.get("free_float"),
            "avg_traded_value_20d": r.metrics.get("avg_traded_value_20d"),
            "reject_reason": r.reject_reason,
        }
        for r in ineligible[:20]
    ]

    return {
        "items": [r.to_dict() for r in eligible],
        "scanned": len(universe),
        "eligible_count": sum(1 for r in results if r.eligible),
        "ineligible_count": sum(1 for r in results if not r.eligible),
        "cap_tl": cap_tl or FLOAT_MARKET_CAP_CAP_TL,
        "near_misses": near_misses,
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
        "duration_ms": round((time.time() - t0) * 1000, 0),
    }


# ================================================================
# BACKGROUND WARMUP — keep the cache hot so user clicks are instant.
#
# Called from app.py's lifespan, runs in parallel with the main
# background scanner. Strategy:
#   1. Wait WARMUP_INITIAL_DELAY seconds after boot (let the main scan
#      run first — yfinance bandwidth is shared, no point fighting).
#   2. Run a BullWatch scan, populate _CACHE.
#   3. Sleep WARMUP_INTERVAL, repeat.
#
# Errors are logged but never propagate — a failed warmup just means
# the next user click triggers a fresh scan (current behaviour).
# ================================================================
WARMUP_INITIAL_DELAY = 90      # seconds — let main background scan finish first
WARMUP_INTERVAL = 600          # seconds — refresh every 10 minutes
WARMUP_RETRY_AFTER_ERROR = 300 # seconds — back off on yfinance flakiness


async def warmup_cache_loop() -> None:
    """
    Background coroutine: keep _CACHE warm so the BullWatch tab opens
    instantly. Started by app.py lifespan, cancelled on shutdown.
    """
    global _SCAN_DONE

    log.info("BullWatch warmup task scheduled (initial delay: %ds, interval: %ds)",
             WARMUP_INITIAL_DELAY, WARMUP_INTERVAL)
    await asyncio.sleep(WARMUP_INITIAL_DELAY)

    while True:
        # Skip if a user-triggered scan is already running — just wait and try later
        if _CACHE["running"]:
            log.debug("BullWatch warmup: another scan in flight, waiting one cycle")
            await asyncio.sleep(WARMUP_INTERVAL)
            continue

        log.info("BullWatch warmup: starting background scan")
        _CACHE["running"] = True
        _SCAN_DONE = asyncio.Event()
        sleep_for = WARMUP_INTERVAL
        try:
            t0 = time.time()
            payload = await asyncio.get_event_loop().run_in_executor(
                None, _run_scan, 0.0, None, None, False,
            )
            _CACHE["items"] = payload
            _CACHE["as_of"] = payload["as_of"]
            _CACHE["stale_after"] = time.time() + _CACHE_TTL_SEC
            elapsed = time.time() - t0
            log.info(
                "BullWatch warmup: cache refreshed in %.1fs — %d eligible / %d scanned",
                elapsed,
                payload.get("eligible_count", 0),
                payload.get("scanned", 0),
            )
        except asyncio.CancelledError:
            # Graceful shutdown — release waiters and exit
            if _SCAN_DONE is not None:
                _SCAN_DONE.set()
            _CACHE["running"] = False
            log.info("BullWatch warmup: cancelled (shutdown)")
            raise
        except Exception as exc:
            log.warning("BullWatch warmup failed (will retry): %r", exc)
            sleep_for = WARMUP_RETRY_AFTER_ERROR
        finally:
            if _SCAN_DONE is not None:
                _SCAN_DONE.set()
            _CACHE["running"] = False

        await asyncio.sleep(sleep_for)


@router.get("/api/bullwatch")
async def api_bullwatch(
    refresh: bool = Query(False, description="Force a fresh scan, bypassing cache"),
    min_score: float = Query(0.0, ge=0, le=100,
                             description="Minimum BullWatch score to include"),
    zone: Optional[str] = Query(None,
                                description="Filter by zone: EARLY, CONFIRMED, CONVICTION"),
    limit: int = Query(50, ge=1, le=200,
                       description="Max results to return"),
    cap_tl: Optional[float] = Query(None, ge=10_000_000, le=10_000_000_000,
                                     description="Override float-mcap cap (TL). Default 250M."),
    diagnostic: bool = Query(False,
                             description="Include top 20 near-miss ineligible stocks"),
):
    """
    Return ranked BullWatch candidates.

    Response shape (flat, V9.1-compatible — see core.response_envelope):
        {
          "items": [
            {"symbol": "...", "score": 92.0, "zone": "CONFIRMED",
             "pattern": "Float Squeeze + Insider Activity",
             "data_quality": "high",
             "components": {...}, "metrics": {...}, "reasons": [...]}
          ],
          "scanned": 238,
          "eligible_count": 12,
          "as_of": "...",
          "_meta": {...}
        }
    """
    now = time.time()
    # Experimental requests (custom cap or diagnostic mode) bypass the
    # cache entirely — they're for one-off tuning, not user-facing default.
    is_experimental = (cap_tl is not None) or diagnostic
    use_cache = (
        not refresh
        and not is_experimental
        and _CACHE["items"] is not None
        and now < _CACHE["stale_after"]
    )

    global _SCAN_DONE

    if is_experimental:
        # Run fresh, directly. No cache update, no _SCAN_DONE coordination —
        # this is an ad-hoc query.
        try:
            payload = await asyncio.get_event_loop().run_in_executor(
                None, _run_scan, min_score, None, cap_tl, diagnostic,
            )
            cache_status = "experimental"
        except Exception as exc:
            log.exception("BullWatch experimental scan failed: %r", exc)
            return error(f"BullWatch scan failed: {exc}", status_code=500)
    elif use_cache:
        payload = _CACHE["items"]
        cache_status = "hit"
    else:
        if _CACHE["running"]:
            # Another scan is in flight. If we have stale cache, serve it
            # immediately (warm path). Otherwise wait for the running scan
            # to finish — do NOT 503, the user is already waiting.
            if _CACHE["items"] is not None:
                payload = _CACHE["items"]
                cache_status = "stale_during_refresh"
            else:
                evt = _SCAN_DONE
                if evt is None:
                    return error("scan state inconsistent — please retry",
                                 status_code=503)
                try:
                    await asyncio.wait_for(evt.wait(),
                                           timeout=_SCAN_MAX_WAIT_SEC)
                except asyncio.TimeoutError:
                    return error(
                        f"scan still running after {_SCAN_MAX_WAIT_SEC}s — try again",
                        status_code=504)
                if _CACHE["items"] is None:
                    return error("scan finished but cache empty",
                                 status_code=500)
                payload = _CACHE["items"]
                cache_status = "fresh_after_wait"
        else:
            _CACHE["running"] = True
            _SCAN_DONE = asyncio.Event()
            try:
                # Run the blocking scan off the event loop.
                payload = await asyncio.get_event_loop().run_in_executor(
                    None, _run_scan, min_score, None,
                )
                _CACHE["items"] = payload
                _CACHE["as_of"] = payload["as_of"]
                _CACHE["stale_after"] = now + _CACHE_TTL_SEC
                cache_status = "miss"
            except Exception as exc:
                log.exception("BullWatch scan failed: %r", exc)
                # Fall back to stale cache if we have one
                if _CACHE["items"] is not None:
                    payload = _CACHE["items"]
                    cache_status = "stale_after_error"
                else:
                    _SCAN_DONE.set()  # release any waiters before returning
                    _CACHE["running"] = False
                    return error(f"BullWatch scan failed: {exc}", status_code=500)
            finally:
                # Wake up any other requests parked on this scan
                if _SCAN_DONE is not None:
                    _SCAN_DONE.set()
                _CACHE["running"] = False

    items = list(payload["items"])

    # Apply per-request filters (cache stays unfiltered — cheap server-side
    # filter is friendlier than caching every parameter combo).
    if zone:
        zone_norm = zone.strip().upper()
        items = [it for it in items if it.get("zone") == zone_norm]
    if min_score > 0:
        items = [it for it in items if (it.get("score") or 0) >= min_score]
    items = items[:limit]

    return success(
        {
            "items": items,
            "scanned": payload.get("scanned", 0),
            "eligible_count": payload.get("eligible_count", 0),
            "ineligible_count": payload.get("ineligible_count", 0),
            "cap_tl": payload.get("cap_tl"),
            "near_misses": payload.get("near_misses", []),
            "duration_ms": payload.get("duration_ms"),
        },
        as_of=payload.get("as_of"),
        cache_status=cache_status,
        extra_meta={"engine": "bullwatch_v1"},
    )


@router.get("/api/bullwatch/health")
async def api_bullwatch_health():
    """Lightweight health check — does NOT trigger a scan."""
    cached = _CACHE["items"] is not None
    return success({
        "ok": True,
        "engine": "bullwatch_v1",
        "cache_populated": cached,
        "cache_as_of": _CACHE.get("as_of"),
        "cache_age_sec": (time.time() - (_CACHE["stale_after"] - _CACHE_TTL_SEC))
                         if cached else None,
        "scan_running": _CACHE["running"],
    })


@router.get("/api/bullwatch/{symbol}")
async def api_bullwatch_symbol(symbol: str):
    """
    Score a single symbol against BullWatch criteria. Useful for
    inspecting why a stock did or didn't qualify.
    """
    from utils.helpers import normalize_symbol
    from engine.bullwatch import score_symbol

    sym = normalize_symbol(symbol).replace(".IS", "")
    try:
        from data.providers import compute_metrics_v9
        from engine.technical import batch_download_history
    except ImportError as exc:
        return error(f"data layer unavailable: {exc}", status_code=503)

    try:
        metrics = await asyncio.get_event_loop().run_in_executor(
            None, compute_metrics_v9, sym,
        )
    except Exception as exc:
        return error(f"metrics fetch failed: {exc}", status_code=502)

    try:
        hist = await asyncio.get_event_loop().run_in_executor(
            None, batch_download_history, [sym],
        )
        df = (hist or {}).get(sym)
    except Exception as exc:
        log.warning("BullWatch %s: history fetch failed: %r", sym, exc)
        df = None

    try:
        result = score_symbol(metrics, df, ownership=None)
    except Exception as exc:
        log.exception("BullWatch %s: scoring failed", sym)
        return error(f"scoring failed: {exc}", status_code=500)

    return success(result.to_dict(),
                   as_of=dt.datetime.now(dt.timezone.utc).isoformat(),
                   extra_meta={"engine": "bullwatch_v1"})

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

from fastapi import APIRouter, Query, Body
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
    "progress": 0,        # symbols processed so far (during in-flight scan)
    "total": 0,           # total symbols in the in-flight scan
    "scan_started_at": 0.0,
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
    _CACHE["scan_started_at"] = t0
    _CACHE["progress"] = 0
    _CACHE["total"] = len(universe)

    def _on_progress(done: int, total: int) -> None:
        _CACHE["progress"] = done
        _CACHE["total"] = total

    # Always include_ineligible=True — it's just a filter on already-computed
    # results, no extra fetches. Lets us surface near_misses for free.
    # max_workers=16 — yfinance fetches are I/O-bound, GIL doesn't apply.
    results = scan(universe, min_score=min_score,
                   include_ineligible=True, cap_tl=cap_tl,
                   max_workers=16, progress_callback=_on_progress)
    eligible = [r for r in results if r.eligible]
    if limit:
        eligible = eligible[:limit]

    # Near-misses: ineligible with KNOWN float mcap, sorted ascending so
    # the smallest (= closest to passing) come first.
    from features.bullwatch_features import normalize_free_float
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
            "free_float": normalize_free_float(r.metrics.get("free_float")),
            "avg_traded_value_20d": r.metrics.get("avg_traded_value_20d"),
            "reject_reason": r.reject_reason,
        }
        for r in ineligible[:20]
    ]

    items = [r.to_dict() for r in eligible]

    # Annotate each item with a `delta` field (vs yesterday's snapshot).
    # Fail-soft: if Redis is down or no history, items get a stable/no-op
    # delta and the rest of the response is unaffected.
    try:
        from data.bullwatch_history import annotate_with_deltas, save_snapshot
        annotate_with_deltas(items)
        # Persist this scan's results so tomorrow's scan can compute delta.
        # Save AFTER annotation so we don't compare today against itself.
        save_snapshot(items)
    except Exception as e:
        log.warning("BullWatch history annotate/save failed: %r", e)

    return {
        "items": items,
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
    """Lightweight health check — does NOT trigger a scan.

    Frontend polls this every few seconds while a scan is in flight to
    show real-time progress instead of a blank spinner.
    """
    cached = _CACHE["items"] is not None
    running = _CACHE["running"]
    progress = _CACHE.get("progress", 0)
    total = _CACHE.get("total", 0)
    scan_started = _CACHE.get("scan_started_at", 0)
    elapsed_sec = (time.time() - scan_started) if (running and scan_started) else None

    return success({
        "ok": True,
        "engine": "bullwatch_v1",
        "cache_populated": cached,
        "cache_as_of": _CACHE.get("as_of"),
        "cache_age_sec": (time.time() - (_CACHE["stale_after"] - _CACHE_TTL_SEC))
                         if cached else None,
        "scan_running": running,
        "scan_progress": progress,
        "scan_total": total,
        "scan_progress_pct": round(progress / total * 100, 1) if total else None,
        "scan_elapsed_sec": round(elapsed_sec, 1) if elapsed_sec is not None else None,
        "cache": _cache_stats_safe(),
        "history": _history_stats_safe(),
    })


def _cache_stats_safe() -> dict:
    """Safely fetch BullWatch metrics-cache stats — never raise from health check."""
    try:
        from data.bullwatch_cache import get_stats
        return get_stats()
    except Exception:
        return {"error": "stats_unavailable"}


def _history_stats_safe() -> dict:
    """Safely fetch BullWatch snapshot history stats."""
    try:
        from data.bullwatch_history import get_history_stats
        return get_history_stats()
    except Exception:
        return {"error": "stats_unavailable"}


@router.post("/api/bullwatch/watchlist/state")
async def api_bullwatch_watchlist_state(
    payload: dict = Body(...),
):
    """
    Stateless watchlist enrichment.

    The watchlist itself lives in the user's browser (localStorage —
    keeps it private to the device, no server-side personal data).
    The frontend POSTs `{symbols: ["KAPLM", "KARTN", ...]}` and gets
    back the latest known state of each: current eligibility, score,
    zone, delta vs yesterday, plus a 7-day score history for the
    sparkline.

    Items the user is watching but that aren't currently eligible get
    a `cooled_off` flag so the UI can mark them — important signal
    for tape readers ("the absorption setup I was watching has
    dissipated"). NOT a sell recommendation; just a state change.
    """
    symbols = payload.get("symbols") or []
    if not isinstance(symbols, list):
        return error("symbols must be a list")

    # Cap the request size — defensive limit for a tool with no auth.
    symbols = [str(s).upper().strip() for s in symbols if s][:100]

    # Pull current scan results (cache hit if warmup ran recently)
    items_index: dict[str, dict] = {}
    if _CACHE.get("items"):
        for it in _CACHE["items"].get("items", []):
            items_index[it["symbol"]] = it

    # Yesterday's snapshot for delta computation when symbol isn't in
    # today's eligible set
    try:
        from data.bullwatch_history import (
            get_yesterday_snapshot, get_score_history,
            compute_delta_for_item,
        )
        yesterday = get_yesterday_snapshot()
    except Exception:
        yesterday = None
        get_score_history = lambda *_a, **_kw: [None] * 7  # type: ignore

    out = []
    for sym in symbols:
        current = items_index.get(sym)
        if current:
            # Eligible right now
            try:
                history = get_score_history(sym, days=7)
            except Exception:
                history = [None] * 7
            out.append({
                "symbol": sym,
                "eligible": True,
                "score": current.get("score"),
                "zone": current.get("zone"),
                "pattern": current.get("pattern"),
                "sector_tr": current.get("sector_tr"),
                "delta": current.get("delta"),
                "score_history_7d": history,
                "cooled_off": False,
            })
        else:
            # Not in today's eligible set. Was it in yesterday's?
            prior = (yesterday or {}).get(sym)
            try:
                history = get_score_history(sym, days=7)
            except Exception:
                history = [None] * 7
            out.append({
                "symbol": sym,
                "eligible": False,
                "score": None,
                "zone": None,
                "pattern": None,
                "sector_tr": None,
                "delta": None,
                "prev_score": prior.get("score") if prior else None,
                "prev_zone": prior.get("zone") if prior else None,
                "score_history_7d": history,
                "cooled_off": prior is not None,  # was eligible yesterday
            })

    return success({
        "items": out,
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
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

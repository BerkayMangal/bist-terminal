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


def _resolve_universe() -> list[str]:
    """
    BullWatch universe = main UNIVERSE + UNIVERSE_EXTENDED if defined.
    The float-cap filter inside score_symbol will trim 90% of these
    out — we only need a wide enough net to catch micro-caps.
    """
    try:
        from config import UNIVERSE
        try:
            from config import UNIVERSE_EXTENDED
            combined = list(dict.fromkeys(list(UNIVERSE) + list(UNIVERSE_EXTENDED)))
        except ImportError:
            combined = list(UNIVERSE)
        return combined
    except Exception:
        return []


def _run_scan(min_score: float = 0.0,
              limit: Optional[int] = None) -> dict[str, Any]:
    """Synchronous scan — called from a thread executor."""
    from engine.bullwatch import scan
    universe = _resolve_universe()
    t0 = time.time()
    results = scan(universe, min_score=min_score)
    eligible = [r for r in results if r.eligible]
    if limit:
        eligible = eligible[:limit]
    return {
        "items": [r.to_dict() for r in eligible],
        "scanned": len(universe),
        "eligible_count": sum(1 for r in results if r.eligible),
        "ineligible_count": sum(1 for r in results if not r.eligible),
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
        "duration_ms": round((time.time() - t0) * 1000, 0),
    }


@router.get("/api/bullwatch")
async def api_bullwatch(
    refresh: bool = Query(False, description="Force a fresh scan, bypassing cache"),
    min_score: float = Query(0.0, ge=0, le=100,
                             description="Minimum BullWatch score to include"),
    zone: Optional[str] = Query(None,
                                description="Filter by zone: EARLY, CONFIRMED, CONVICTION"),
    limit: int = Query(50, ge=1, le=200,
                       description="Max results to return"),
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
    use_cache = (
        not refresh
        and _CACHE["items"] is not None
        and now < _CACHE["stale_after"]
    )

    if use_cache:
        payload = _CACHE["items"]
        cache_status = "hit"
    else:
        if _CACHE["running"]:
            # Another scan is in flight — serve stale cache if available,
            # otherwise wait briefly and retry once.
            if _CACHE["items"] is not None:
                payload = _CACHE["items"]
                cache_status = "stale_during_refresh"
            else:
                await asyncio.sleep(0.5)
                if _CACHE["items"] is not None:
                    payload = _CACHE["items"]
                    cache_status = "stale_during_refresh"
                else:
                    return error("BullWatch scan in progress, try again shortly",
                                 status_code=503)
        else:
            _CACHE["running"] = True
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
                    return error(f"BullWatch scan failed: {exc}", status_code=500)
            finally:
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

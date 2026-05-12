# ================================================================
# BISTBULL TERMINAL — KAP API ENDPOINTS
# api/kap.py
#
# Public read-only views over the KAP disclosure feed:
#
#   GET /api/kap/recent?limit=50              — latest across all tickers
#   GET /api/kap/by-ticker/{TICKER}?limit=20  — disclosure history per ticker
#   GET /api/kap/calendar/{TICKER}            — forward-looking calendar
#   GET /api/kap/health                       — feed status + last cycle stats
#   POST /api/kap/poll                        — manual poll trigger (admin)
#
# These are added in Faz 1 so the data is queryable even before the
# Faz 2 UI lands. UI consumers in Faz 2 will read these endpoints
# directly.
# ================================================================

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from core.response_envelope import success, error

log = logging.getLogger("bistbull.kap_api")
router = APIRouter()


@router.get("/api/kap/recent")
async def api_kap_recent(
    limit: int = Query(50, ge=1, le=200,
                       description="Max disclosures to return (newest first)"),
):
    """Latest disclosures across all BIST tickers. Hot path (Redis); falls
    back to SQLite when Redis is unavailable."""
    from infra import kap_storage
    items = kap_storage.get_recent(limit=limit)
    return success(
        {"items": items, "count": len(items)},
        extra_meta={"endpoint": "kap.recent"},
    )


@router.get("/api/kap/by-ticker/{ticker}")
async def api_kap_by_ticker(
    ticker: str,
    limit: int = Query(20, ge=1, le=200),
):
    """Per-ticker disclosure history. Useful for the ticker-detail
    UI's 'Geçmiş Bilançolar' tab."""
    if not ticker or not ticker.strip():
        raise HTTPException(status_code=400, detail="empty ticker")
    from infra import kap_storage
    items = kap_storage.get_by_ticker(ticker, limit=limit)
    return success(
        {"ticker": ticker.upper(), "items": items, "count": len(items)},
        extra_meta={"endpoint": "kap.by_ticker"},
    )


@router.get("/api/kap/calendar/{ticker}")
async def api_kap_calendar(ticker: str):
    """Forward-looking calendar entries — what disclosures KAP expects
    this ticker to file in the next few reporting periods. Read straight
    from pykap (no DB)."""
    if not ticker or not ticker.strip():
        raise HTTPException(status_code=400, detail="empty ticker")
    try:
        from data.kap_client import list_expected_disclosures
        items = await asyncio.to_thread(list_expected_disclosures, ticker)
    except Exception as exc:
        log.warning("KAP calendar %s: %r", ticker, exc)
        return error(f"KAP calendar unavailable: {exc}", status_code=502)
    return success(
        {"ticker": ticker.upper(), "items": items, "count": len(items)},
        extra_meta={"endpoint": "kap.calendar"},
    )


@router.get("/api/kap/health")
async def api_kap_health():
    """Feed status + last cycle telemetry."""
    from infra import kap_storage
    from engine.kap_feed import get_last_cycle_stats
    return success(
        {
            "ok": True,
            "storage": kap_storage.get_stats(),
            "last_cycle": get_last_cycle_stats(),
        },
        extra_meta={"endpoint": "kap.health"},
    )


@router.post("/api/kap/poll")
async def api_kap_poll_trigger():
    """Kick a manual poll cycle. Used by ops and by tests that want to
    drive a deterministic cycle instead of waiting for the loop."""
    from engine.kap_feed import run_one_cycle
    try:
        stats = await asyncio.to_thread(run_one_cycle)
    except Exception as exc:
        log.exception("manual KAP poll failed: %r", exc)
        return error(f"KAP poll failed: {exc}", status_code=500)
    return success(
        {"ok": True, "cycle": stats.to_dict()},
        extra_meta={"endpoint": "kap.poll"},
    )

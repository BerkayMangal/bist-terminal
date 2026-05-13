# ================================================================
# BISTBULL TERMINAL — DIAGNOSTIC ENDPOINTS
# api/diag.py
#
# Read-only views into provider/cache state. None of these mutate
# anything — they just join cache + KAP storage into one bundle
# the UI can display next to the Radar table.
#
#   GET /api/diag/fundamentals/{ticker}    full freshness bundle
#   GET /api/diag/fundamentals             batch summary for Radar
# ================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.response_envelope import success, error

log = logging.getLogger("bistbull.diag")
router = APIRouter()


# Literal-path route MUST be registered before the path-param one so
# /diag/fundamentals doesn't get caught as ticker="fundamentals".
@router.get("/api/diag/fundamentals")
async def api_diag_fundamentals_summary(
    tickers: Optional[str] = Query(
        None,
        description="Comma-separated tickers. Defaults to the scan universe.",
    ),
    limit: int = Query(60, ge=1, le=500),
):
    """Batch freshness summary for the Radar table.

    Without `tickers`, falls back to whichever symbols the most recent
    scan produced (so the user sees freshness for the names they're
    actually looking at).
    """
    from engine.diag_fundamentals import compute_summary

    universe: list[str] = []
    if tickers:
        universe = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    else:
        # Pull from the scan coordinator's last result, falling back to
        # config UNIVERSE if nothing is cached.
        try:
            from app import get_top10_items  # type: ignore
            items = get_top10_items() or []
            universe = [
                (i.get("ticker") or i.get("symbol") or "").upper()
                for i in items if (i.get("ticker") or i.get("symbol"))
            ]
        except Exception as exc:
            log.debug("scan items lookup failed: %r", exc)
        if not universe:
            try:
                from config import UNIVERSE
                universe = [u.replace(".IS", "").upper() for u in UNIVERSE]
            except Exception:
                universe = []

    universe = universe[: max(1, limit)]
    if not universe:
        return success({"items": [], "summary": {}})

    data = await asyncio.to_thread(compute_summary, universe)
    return success(
        data,
        extra_meta={"endpoint": "diag.fundamentals.summary"},
    )


@router.get("/api/diag/fundamentals/{ticker}")
async def api_diag_fundamentals_one(ticker: str):
    """Full freshness bundle for one ticker — used by the Radar
    "Veri Tazeliği" modal."""
    if not ticker or not ticker.strip():
        raise HTTPException(status_code=400, detail="empty ticker")
    from engine.diag_fundamentals import compute_data_freshness
    data = await asyncio.to_thread(compute_data_freshness, ticker)
    return success(
        data,
        extra_meta={"endpoint": "diag.fundamentals.one"},
    )

# ================================================================
# BISTBULL TERMINAL — VIOP ENDPOINTS
# api/viop.py
#
# Reads the daily VIOP snapshot store (options + futures across stock,
# index, currency, commodity categories). UOA engine + Tahtacı overlay
# live in separate modules and will be exposed here in later phases.
#
#   GET /api/viop/today               latest snapshot (filterable)
#   GET /api/viop/health              ingestion telemetry + counts
#   GET /api/viop/history/{code}      per-contract daily history
#   POST /api/viop/refresh             manual ingest trigger
# ================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.response_envelope import success, error

log = logging.getLogger("bistbull.viop_api")
router = APIRouter()


@router.get("/api/viop/today")
async def api_viop_today(
    kind: Optional[str] = Query(
        None, description="option | future"
    ),
    underlying: Optional[str] = Query(
        None, description="e.g. BIMAS / XU030 / USDTRY"
    ),
    limit: int = Query(200, ge=1, le=1000),
):
    """Latest VIOP snapshot — sorted by TL volume desc."""
    from infra import viop_storage
    items = viop_storage.get_today(
        kind=kind,
        underlying=underlying,
        limit=limit,
    )
    return success(
        {"items": items, "count": len(items)},
        extra_meta={"endpoint": "viop.today"},
    )


@router.get("/api/viop/health")
async def api_viop_health():
    """Snapshot freshness + per-kind/category counts + last cycle stats."""
    from infra import viop_storage
    from engine.viop_feed import get_last_cycle
    return success(
        {
            "ok": True,
            "stats": viop_storage.get_stats(),
            "last_cycle": get_last_cycle(),
        },
        extra_meta={"endpoint": "viop.health"},
    )


@router.get("/api/viop/history/{code}")
async def api_viop_history(
    code: str,
    days: int = Query(60, ge=1, le=365),
):
    """Per-contract daily snapshot history — used by UOA's z-score baseline.

    Returns rows newest-first. Empty list if we don't have history yet
    (first day of ingestion)."""
    if not code or not code.strip():
        raise HTTPException(status_code=400, detail="empty code")
    from infra import viop_storage
    items = viop_storage.get_history(code.strip(), days=days)
    return success(
        {"code": code, "days": days, "items": items, "count": len(items)},
        extra_meta={"endpoint": "viop.history"},
    )


@router.post("/api/viop/refresh")
async def api_viop_refresh():
    """Manual ingest trigger — same code path as the background loop.
    Useful when the user wants to force a fresh snapshot before
    inspecting today's anomalies."""
    try:
        from engine.viop_feed import run_one_cycle
        res = await asyncio.to_thread(run_one_cycle)
    except Exception as exc:
        log.exception("viop refresh failed: %r", exc)
        return error(f"refresh failed: {exc}", status_code=500)
    return success(
        {"ok": True, "cycle": res.to_dict()},
        extra_meta={"endpoint": "viop.refresh"},
    )

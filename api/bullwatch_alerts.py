# ================================================================
# BISTBULL TERMINAL — BULLWATCH ALARM ENDPOINTS
# api/bullwatch_alerts.py
#
# Read-only views over the immutable high-conviction alarm history.
#
#   GET  /api/bullwatch/alerts/recent      latest N across all tickers
#   GET  /api/bullwatch/alerts/by-ticker/{T}
#   GET  /api/bullwatch/alerts/{alert_id}  single record
#   GET  /api/bullwatch/alerts/stats       feed-health
#   POST /api/bullwatch/alerts/refresh-reactions
#         manual trigger for the Faz 4 reaction backfill (also runs
#         daily in the background loop)
# ================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.response_envelope import success, error

log = logging.getLogger("bistbull.bwa_api")
router = APIRouter()


@router.get("/api/bullwatch/alerts/recent")
async def api_bwa_recent(
    limit: int = Query(50, ge=1, le=500),
    since_days: Optional[int] = Query(None, ge=1, le=365,
                                       description="Only alarms within last N days"),
):
    """Latest alarms across all tickers, newest first."""
    from infra import bullwatch_alerts_storage as st
    items = st.get_recent(limit=limit, since_days=since_days)
    return success(
        {"items": items, "count": len(items)},
        extra_meta={"endpoint": "bullwatch.alerts.recent"},
    )


@router.get("/api/bullwatch/alerts/stats")
async def api_bwa_stats():
    """Feed-health summary — alarm volume, newest, 30-day count."""
    from infra import bullwatch_alerts_storage as st
    return success(
        {"ok": True, "stats": st.get_stats()},
        extra_meta={"endpoint": "bullwatch.alerts.stats"},
    )


@router.get("/api/bullwatch/alerts/by-ticker/{ticker}")
async def api_bwa_by_ticker(
    ticker: str,
    limit: int = Query(20, ge=1, le=200),
):
    """Per-ticker alarm history. Used by the ticker-detail panel
    when surfacing "BullWatch sent N alarms — how did they go?"."""
    if not ticker or not ticker.strip():
        raise HTTPException(status_code=400, detail="empty ticker")
    from infra import bullwatch_alerts_storage as st
    items = st.get_by_ticker(ticker, limit=limit)
    return success(
        {"ticker": ticker.upper(), "items": items, "count": len(items)},
        extra_meta={"endpoint": "bullwatch.alerts.by_ticker"},
    )


@router.get("/api/bullwatch/alerts/{alert_id}")
async def api_bwa_one(alert_id: str):
    """Single alarm by id."""
    from infra import bullwatch_alerts_storage as st
    row = st.get_by_id(alert_id)
    if row is None:
        return error("alert not found", status_code=404)
    return success({"alert": row},
                   extra_meta={"endpoint": "bullwatch.alerts.one"})


@router.post("/api/bullwatch/alerts/refresh-reactions")
async def api_bwa_refresh_reactions():
    """Manual trigger for the Faz 4 reaction backfill (1d / 1w / 1m).
    Same job the daily background loop runs."""
    try:
        from engine.bullwatch_alert_reactions import refresh_alert_reactions
        stats = await asyncio.to_thread(refresh_alert_reactions, 200)
    except ImportError:
        # Faz 4 module not landed yet — graceful degrade
        return error("reaction backfill module not available", status_code=503)
    except Exception as exc:
        log.exception("alert reaction refresh failed: %r", exc)
        return error(f"refresh failed: {exc}", status_code=500)
    return success(
        {"ok": True, "stats": stats},
        extra_meta={"endpoint": "bullwatch.alerts.refresh_reactions"},
    )

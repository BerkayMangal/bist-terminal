# ================================================================
# BISTBULL TERMINAL — UNIFIED ACTIVITY FEED ENDPOINT
# api/activity.py
#
# Combines alarms, membership events, KAP financials, and auto-refresh
# score changes into one chronological feed.
#
#   GET /api/activity/recent?since_hours=24&watchlist=A,B,C&limit=80
# ================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Query

from core.response_envelope import success, error

log = logging.getLogger("bistbull.activity")
router = APIRouter()


@router.get("/api/activity/recent")
async def api_activity_recent(
    since_hours: int = Query(24, ge=1, le=168),
    watchlist: Optional[str] = Query(
        None,
        description="Comma-separated ticker list. When set, feed is filtered.",
    ),
    limit: int = Query(80, ge=1, le=200),
):
    """Last N hours of activity across alarms, membership events, KAP
    financial reports, and auto-refresh score changes."""
    try:
        from engine.activity_feed import get_recent_activity
        wl_list = None
        if watchlist:
            wl_list = [t.strip() for t in watchlist.split(",") if t.strip()]
        data = await asyncio.to_thread(
            get_recent_activity,
            since_hours, wl_list, limit,
        )
    except Exception as exc:
        log.exception("activity feed failed: %r", exc)
        return error(f"activity feed failed: {exc}", status_code=500)
    return success(
        data,
        extra_meta={"endpoint": "activity.recent"},
    )

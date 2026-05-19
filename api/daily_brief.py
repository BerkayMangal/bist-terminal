# ================================================================
# BISTBULL TERMINAL — DAILY BRIEF API
# api/daily_brief.py
#
# Stage 7b endpoints:
#   GET  /api/daily-brief                — today's bulletin (or latest)
#   GET  /api/daily-brief/{YYYY-MM-DD}   — specific date
#   GET  /api/daily-brief/history        — archive list
#   POST /api/daily-brief/regenerate     — manual trigger (auth-gated)
# ================================================================

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.rate_limiter import ops_heavy_rate_limit

from core.response_envelope import success, error

log = logging.getLogger("bistbull.daily_brief")
router = APIRouter()


_DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/api/daily-brief")
async def api_daily_brief_latest():
    """Return today's bulletin if it exists, otherwise the most recent
    one. Frontend uses this for the "open the page → show something
    immediately" path."""
    from infra import bulletin_storage as _bs
    today = _bs.istanbul_today()
    record = _bs.get(today) or _bs.get_latest()
    if record is None:
        return success({
            "bulletin": None,
            "message": "Henüz bülten yok. İlk bülten kapanış sonrası (18:30) yazılır.",
        })
    return success({"bulletin": record})


@router.get("/api/daily-brief/history")
async def api_daily_brief_history(
    limit: int = Query(30, ge=1, le=365,
                       description="Max days to return (latest first)"),
):
    """Archive listing — date + generated_at, no content. The UI uses
    this for a sidebar; clicks fetch the content via the date endpoint."""
    from infra import bulletin_storage as _bs
    return success({"dates": _bs.list_dates(limit=limit)})


@router.get("/api/daily-brief/{bulletin_date}")
async def api_daily_brief_by_date(bulletin_date: str):
    """Return a specific day's bulletin. Date must be YYYY-MM-DD."""
    if not _DATE_RX.match(bulletin_date):
        raise HTTPException(
            status_code=400,
            detail="bulletin_date must be YYYY-MM-DD",
        )
    from infra import bulletin_storage as _bs
    record = _bs.get(bulletin_date)
    if record is None:
        return error(f"Bülten bulunamadı: {bulletin_date}", status_code=404)
    return success({"bulletin": record})


@router.post("/api/daily-brief/regenerate", dependencies=[Depends(ops_heavy_rate_limit)])
async def api_daily_brief_regenerate(
    bulletin_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD override; defaults to today (Istanbul).",
    ),
):
    """Manual regenerate — useful for testing the bulletin pipeline
    without waiting for the 18:30 schedule slot.

    Note: this composes from the CURRENT state (snapshot, KAP feed, etc.),
    so calling it mid-session yields a "snapshot of right now" rather
    than a true end-of-day report.
    """
    if bulletin_date is not None and not _DATE_RX.match(bulletin_date):
        raise HTTPException(
            status_code=400,
            detail="bulletin_date must be YYYY-MM-DD",
        )
    from engine.daily_bulletin import generate_and_persist
    try:
        record = generate_and_persist(bulletin_date)
    except Exception as exc:
        log.exception("daily-brief regenerate failed: %r", exc)
        return error(f"regenerate failed: {exc}", status_code=500)
    return success({"bulletin": record, "regenerated": True})

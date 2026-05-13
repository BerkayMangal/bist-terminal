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


@router.post("/api/diag/fundamentals/{ticker}/refresh")
async def api_diag_fundamentals_refresh(ticker: str):
    """Force-refresh ONE ticker: invalidate every cache layer (raw,
    analysis, tech, bullwatch) and re-fetch via analyze_symbol so the
    next view shows freshly-pulled borsapy data.

    Use case: user sees a "Stale" badge in the Radar, clicks the
    "🔄 Şimdi Yenile" button on the modal, wants instant proof that
    the cache CAN refresh and the score does/doesn't change."""
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        raise HTTPException(status_code=400, detail="empty ticker")

    def _go():
        from engine.kap_dispatcher import _invalidate_caches_for_ticker
        from engine.analysis import analyze_symbol
        from engine.diag_fundamentals import compute_data_freshness

        before = compute_data_freshness(sym)
        _invalidate_caches_for_ticker(sym)
        # Re-fetch — this re-populates raw_cache + analysis_cache via
        # the normal analyze path so the next Radar render sees fresh data.
        analysis = None
        try:
            analysis = analyze_symbol(sym + ".IS")
        except Exception as exc:
            log.warning("force-refresh analyze failed for %s: %r", sym, exc)
        after = compute_data_freshness(sym)
        return {
            "ticker": sym,
            "before": before,
            "after": after,
            "analysis_ok": analysis is not None,
            "new_score": (analysis or {}).get("score"),
            "new_decision": (analysis or {}).get("decision"),
        }

    try:
        data = await asyncio.to_thread(_go)
    except Exception as exc:
        log.exception("refresh %s failed: %r", sym, exc)
        return error(f"refresh failed: {exc}", status_code=500)
    return success(
        data,
        extra_meta={"endpoint": "diag.fundamentals.refresh"},
    )


@router.get("/api/diag/score-history/{ticker}")
async def api_diag_score_history(
    ticker: str,
    days: int = Query(30, ge=1, le=365),
):
    """Per-ticker score history for the Radar sparkline.

    Reads the `score_history` table — each row is one daily snapshot
    written by the background scanner. Empty list means the ticker has
    no snapshots yet (just-installed instance, or a ticker that's
    perpetually rejected by universe filters)."""
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        raise HTTPException(status_code=400, detail="empty ticker")

    def _go():
        try:
            from infra.storage import _get_conn
            c = _get_conn()
            rows = c.execute(
                "SELECT snap_date, score, fa_score, decision "
                "FROM score_history "
                "WHERE symbol = ? AND scoring_version = 'v13_handpicked' "
                "ORDER BY snap_date DESC LIMIT ?",
                (sym, int(days)),
            ).fetchall()
            return [
                {"snap_date": r[0], "score": r[1],
                 "fa_score": r[2], "decision": r[3]}
                for r in rows
            ]
        except Exception as exc:
            log.debug("score_history lookup %s: %r", sym, exc)
            return []

    items = await asyncio.to_thread(_go)
    items.reverse()  # oldest-first for sparkline rendering
    return success(
        {"ticker": sym, "days": days, "items": items, "count": len(items)},
        extra_meta={"endpoint": "diag.score_history"},
    )

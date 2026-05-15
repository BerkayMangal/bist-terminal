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


@router.get("/api/diag/cache-coherence")
async def api_diag_cache_coherence():
    """Cache layer coherence raporu — Stage 3 audit observability.

    BullWatch path'inde 4 ayrı cache katmanı var (raw_cache, analysis_cache,
    bullwatch_cache, snapshot_store). TTL'leri farklı; bir layer fresh
    ama diğeri stale ise score'lar yanlış çıkabilir.

    Bu endpoint her layer'ın boyutunu, en eski entry'sini ve hit rate'ini
    raporlar — kullanıcı veya operator "neden BullWatch eski veri
    gösteriyor" sorusunu cevaplayabilsin.
    """
    out: dict = {
        "ok": True,
        "layers": {},
        "warnings": [],
    }
    # core.cache layers
    try:
        from core.cache import raw_cache, analysis_cache, tech_cache, history_cache
        for name, c in (
            ("raw_cache", raw_cache),
            ("analysis_cache", analysis_cache),
            ("tech_cache", tech_cache),
            ("history_cache", history_cache),
        ):
            try:
                stats = c.stats() if hasattr(c, "stats") else {"size": len(c)}
                out["layers"][name] = stats
            except Exception as exc:
                out["layers"][name] = {"error": str(exc)}
    except Exception as exc:
        out["warnings"].append(f"core.cache import: {exc}")

    # bullwatch_cache (separate from core)
    try:
        from data import bullwatch_cache as bwc
        if hasattr(bwc, "stats"):
            out["layers"]["bullwatch_cache"] = bwc.stats()
        elif hasattr(bwc, "_STATS"):
            out["layers"]["bullwatch_cache"] = dict(bwc._STATS)
    except Exception as exc:
        out["warnings"].append(f"bullwatch_cache: {exc}")

    # snapshot_store (cold copy of last scan)
    try:
        from core.snapshot_store import get_default_store
        import time as _t
        store = get_default_store()
        snap_info: dict = {}
        for module in ("bullwatch", "bullwatch_hot", "bullalfa"):
            try:
                scan_id = store.read_latest_scan_id(module)
                if scan_id:
                    meta = store.read_meta(module, scan_id=scan_id) or {}
                    asof_unix = meta.get("asof_unix")
                    age_sec = (
                        round(_t.time() - asof_unix, 1) if asof_unix else None
                    )
                    snap_info[module] = {
                        "scan_id": scan_id,
                        "asof_unix": asof_unix,
                        "age_sec": age_sec,
                    }
            except Exception as exc:
                snap_info[module] = {"error": str(exc)}
        out["layers"]["snapshot_store"] = snap_info
    except Exception as exc:
        out["warnings"].append(f"snapshot_store: {exc}")

    # api.bullwatch in-mem mirror
    try:
        from api import bullwatch as _bw
        snap = _bw._cache_snapshot() if hasattr(_bw, "_cache_snapshot") else dict(_bw._CACHE)
        items_count = len((snap.get("items") or {}).get("items") or [])
        out["layers"]["bw_inmem_mirror"] = {
            "items_count": items_count,
            "stale_after": snap.get("stale_after"),
            "running": snap.get("running"),
            "as_of": snap.get("as_of"),
        }
    except Exception as exc:
        out["warnings"].append(f"bw inmem: {exc}")

    # Coherence checks — surface large age mismatches that suggest
    # stale-while-revalidate is firing too often.
    try:
        snap = out["layers"].get("snapshot_store") or {}
        bw_snap = snap.get("bullwatch") or {}
        bw_age = bw_snap.get("age_sec")
        if bw_age and bw_age > 3600:
            out["warnings"].append(
                f"bullwatch snapshot is {bw_age:.0f}s old (>1h) — refresh "
                "loop may be hung or borsapy is rejecting"
            )
    except Exception:
        pass

    return success(out, extra_meta={"endpoint": "diag.cache_coherence"})


@router.get("/api/diag/system")
async def api_diag_system():
    """Sistem geneli sağlık özeti — kullanıcı /diag sayfasında self-debug
    yapabilsin diye. Critical state'leri tek endpointte topluyor:
      - BullWatch scan running mi, ne kadar oldu, sıkıştı mı
      - Cache populated mi, snapshot var mı
      - Background loops çalışıyor mu
      - Portfolio + VIOP + KAP storage counts
    """
    import time as _t
    out: dict = {
        "ok": True,
        "timestamp": _t.time(),
        "bullwatch": {},
        "kap": {},
        "portfolio": {},
        "viop": {},
        "auto_refresh": {},
    }
    # BullWatch
    try:
        from api.bullwatch import _CACHE
        running = bool(_CACHE.get("running"))
        started = _CACHE.get("scan_started_at") or 0
        elapsed = (_t.time() - started) if (running and started) else None
        items = ((_CACHE.get("items") or {}).get("items")) or []
        out["bullwatch"] = {
            "cache_populated": bool(items),
            "items_count": len(items),
            "scan_running": running,
            "scan_elapsed_sec": round(elapsed, 1) if elapsed else None,
            "scan_progress": _CACHE.get("progress"),
            "scan_total": _CACHE.get("total"),
            "hung": bool(elapsed and elapsed > 480),  # 8 min watchdog
        }
    except Exception as exc:
        out["bullwatch"] = {"error": str(exc)}
    # KAP storage
    try:
        from infra import kap_storage
        out["kap"] = kap_storage.get_stats()
    except Exception as exc:
        out["kap"] = {"error": str(exc)}
    # Portfolio
    try:
        from infra import portfolio_storage
        out["portfolio"] = portfolio_storage.get_stats()
    except Exception as exc:
        out["portfolio"] = {"error": str(exc)}
    # VIOP
    try:
        from infra import viop_storage
        out["viop"] = viop_storage.get_stats()
    except Exception as exc:
        out["viop"] = {"error": str(exc)}
    # Auto-refresh
    try:
        from engine.auto_refresh_stale import get_last_cycle
        out["auto_refresh"] = {"last_cycle": get_last_cycle()}
    except Exception as exc:
        out["auto_refresh"] = {"error": str(exc)}
    return success(out, extra_meta={"endpoint": "diag.system"})


@router.post("/api/diag/bullwatch/force-reset")
async def api_diag_bw_force_reset():
    """Admin emergency — BullWatch scan hung olduğunda zorla reset et.
    Watchdog 8 dakika bekler ama bazen kullanıcı manuel resetlemek
    isteyebilir."""
    from api.bullwatch import _CACHE, _SCAN_DONE
    out: dict = {"reset": False, "was_running": False}
    if _CACHE.get("running"):
        out["was_running"] = True
        _CACHE["running"] = False
        out["reset"] = True
        try:
            if _SCAN_DONE is not None:
                _SCAN_DONE.set()
        except Exception:
            pass
    return success(out, extra_meta={"endpoint": "diag.bw.force_reset"})


@router.get("/api/diag/auto-refresh/status")
async def api_diag_auto_refresh_status():
    """Last cycle telemetry from engine.auto_refresh_stale.

    The UI banner uses this to show "Auto-refresh çalışıyor mu, en son
    ne zaman koştu, kaç ticker'ı geri getirdi, kaç skor değişti?"
    """
    from engine.auto_refresh_stale import (
        get_last_cycle,
        DEFAULT_INTERVAL_SEC,
        DEFAULT_MAX_PER_CYCLE,
    )
    return success(
        {
            "last_cycle": get_last_cycle(),
            "config": {
                "interval_sec": DEFAULT_INTERVAL_SEC,
                "max_per_cycle": DEFAULT_MAX_PER_CYCLE,
            },
        },
        extra_meta={"endpoint": "diag.auto_refresh.status"},
    )


@router.get("/api/diag/ai-status")
async def api_diag_ai_status():
    """AI provider health + recent call telemetry.

    Added in the AI Quality Overhaul (2026-05). Surfaces:
      - which providers are configured + which is primary
      - aggregate success rate
      - the last AI call (provider, model, latency, ok/fail)
      - last 15 calls as a ring buffer
      - which providers have been flagged quota-exhausted this process

    Lets the operator answer "is the AI healthy / which model served
    the last commentary / why is output empty" without digging logs.
    """
    try:
        from ai.engine import get_ai_telemetry
        telem = get_ai_telemetry()
    except Exception as exc:
        return error(f"AI telemetry unavailable: {exc}", status_code=500)
    return success(telem, extra_meta={"endpoint": "diag.ai_status"})


@router.get("/api/diag/stale")
async def api_diag_stale(
    threshold: str = Query(
        "stale",
        description="'stale' (>72h or missing), 'old' (>26h), or 'any-warning' (includes quarterly-missing & gap flags)",
    ),
    limit: int = Query(40, ge=1, le=200),
):
    """List tickers that need attention — used by the Radar "⚠️ N stale"
    drill-down. Sorted by severity then by age desc."""
    from engine.diag_fundamentals import compute_summary

    # Build universe (scan items first, else config UNIVERSE)
    universe: list[str] = []
    try:
        from app import get_top10_items  # type: ignore
        universe = [
            (i.get("ticker") or i.get("symbol") or "").upper()
            for i in (get_top10_items() or [])
            if (i.get("ticker") or i.get("symbol"))
        ]
    except Exception:
        pass
    if not universe:
        try:
            from config import UNIVERSE
            universe = [u.replace(".IS", "").upper() for u in UNIVERSE]
        except Exception:
            universe = []
    if not universe:
        return success({"items": [], "summary": {"matched": 0}})

    summary = await asyncio.to_thread(compute_summary, universe)
    rows = summary.get("items") or []
    from engine.diag_fundamentals import filter_stale_rows
    matched = filter_stale_rows(rows, threshold=threshold)[:limit]
    return success(
        {
            "items": matched,
            "summary": {
                "matched": len(matched),
                "universe_size": len(universe),
                "threshold": threshold,
            },
            "thresholds": summary.get("thresholds") or {},
        },
        extra_meta={"endpoint": "diag.stale"},
    )


@router.post("/api/diag/fundamentals/batch-refresh")
async def api_diag_batch_refresh(
    tickers: str = Query(..., description="Comma-separated ticker list"),
    max_concurrency: int = Query(4, ge=1, le=8),
):
    """Force-refresh a batch of tickers. Bounded parallelism keeps borsapy
    happy (rate limits surface as retries upstream). Returns per-ticker
    success/failure with the new age — UI can show a result table."""
    raw = [t.strip().upper().replace(".IS", "") for t in tickers.split(",")]
    syms = [t for t in raw if t]
    syms = list(dict.fromkeys(syms))   # dedupe, preserve order
    if not syms:
        raise HTTPException(status_code=400, detail="no tickers")
    # Cap batch size to keep the request reasonably bounded
    syms = syms[:30]

    def _one(sym: str) -> dict:
        try:
            from engine.kap_dispatcher import _invalidate_caches_for_ticker
            from engine.analysis import analyze_symbol
            from engine.diag_fundamentals import compute_data_freshness
            _invalidate_caches_for_ticker(sym)
            try:
                analyze_symbol(sym + ".IS")
                ok = True
                err = None
            except Exception as exc:  # provider failure on this ticker
                ok = False
                err = f"{type(exc).__name__}: {exc}"
            after = compute_data_freshness(sym)
            return {
                "ticker": sym,
                "ok": ok,
                "error": err,
                "new_age_hours": after["borsapy"].get("age_hours"),
                "new_status": after.get("age_status"),
                "new_latest_quarter": after["borsapy"].get("latest_quarter"),
            }
        except Exception as exc:
            return {
                "ticker": sym,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "new_age_hours": None,
                "new_status": "unknown",
                "new_latest_quarter": None,
            }

    def _go():
        from concurrent.futures import ThreadPoolExecutor
        out: list[dict] = []
        with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
            for r in pool.map(_one, syms):
                out.append(r)
        return out

    items = await asyncio.to_thread(_go)
    ok_n = sum(1 for i in items if i["ok"])
    return success(
        {
            "items": items,
            "summary": {
                "requested": len(syms),
                "succeeded": ok_n,
                "failed": len(items) - ok_n,
            },
        },
        extra_meta={"endpoint": "diag.batch_refresh"},
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


@router.get("/api/diag/timeline/{ticker}")
async def api_diag_timeline(
    ticker: str,
    days: int = Query(60, ge=7, le=180),
):
    """Combined ticker timeline: every daily score snapshot AND every
    KAP financial report in the same window. Used by the freshness
    modal to OVERLAY the two — "did the skor change BECAUSE bilanço
    landed, or independently?".

    Score events have `kind=score`, KAP events `kind=kap_financial`.
    All events carry an ISO `date` field so the UI can interleave
    them on the same x-axis.
    """
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        raise HTTPException(status_code=400, detail="empty ticker")

    def _go():
        import datetime as _dt
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
        out: dict = {
            "ticker": sym,
            "days": days,
            "score_events": [],
            "kap_events": [],
        }
        # Score snapshots
        try:
            from infra.storage import _get_conn
            c = _get_conn()
            rows = c.execute(
                "SELECT snap_date, score, fa_score, decision "
                "FROM score_history "
                "WHERE symbol = ? AND scoring_version = 'v13_handpicked' "
                "AND snap_date >= ? "
                "ORDER BY snap_date ASC",
                (sym, cutoff.strftime("%Y-%m-%d")),
            ).fetchall()
            out["score_events"] = [
                {"kind": "score",
                 "date": r[0],
                 "score": r[1],
                 "fa_score": r[2],
                 "decision": r[3]}
                for r in rows
            ]
        except Exception as exc:
            log.debug("timeline score query %s: %r", sym, exc)
        # KAP financial events
        try:
            from infra import kap_storage
            try:
                from data.kap_client import (
                    FINANCIAL_REPORT_SUBJECTS,
                    DISCLOSURE_TYPE_FINANCIAL,
                )
            except Exception:
                FINANCIAL_REPORT_SUBJECTS = {
                    "finansal rapor",
                    "konsolide finansal tablolar",
                    "konsolide olmayan finansal tablolar",
                }
                DISCLOSURE_TYPE_FINANCIAL = "FR"
            kap_rows = kap_storage.get_by_ticker(sym, limit=80)
            for row in kap_rows:
                dtype = (row.get("disclosure_type") or "").upper()
                if dtype != DISCLOSURE_TYPE_FINANCIAL:
                    continue
                subj = (row.get("subject") or "").lower().strip()
                if not any(s in subj for s in FINANCIAL_REPORT_SUBJECTS):
                    continue
                pub = row.get("publish_date")
                if not pub:
                    continue
                try:
                    pubdt = _dt.datetime.fromisoformat(pub)
                    if pubdt.tzinfo is None:
                        pubdt = pubdt.replace(tzinfo=_dt.timezone.utc)
                    if pubdt < cutoff:
                        continue
                except Exception:
                    continue
                out["kap_events"].append({
                    "kind": "kap_financial",
                    "date": pub,
                    "rule_type": row.get("rule_type"),
                    "period": row.get("period"),
                    "year": row.get("year"),
                    "subject": row.get("subject"),
                    "disclosure_index": row.get("disclosure_index"),
                })
        except Exception as exc:
            log.debug("timeline kap query %s: %r", sym, exc)
        return out

    data = await asyncio.to_thread(_go)
    return success(
        data,
        extra_meta={"endpoint": "diag.timeline"},
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

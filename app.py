# ================================================================
# BISTBULL TERMINAL V10.0 — FastAPI APP
# Thin router + lifespan + background scanner + WebSocket.
# Business logic lives in engine/ and ai/ modules.
# ================================================================

import os, asyncio, datetime as dt, time, json, logging
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from core.logging_config import setup_logging, get_logger, set_request_id, generate_id, LogTimer
from core import redis_client
from core.cache import (
    raw_cache, analysis_cache, tech_cache, history_cache,
    macro_cache, takas_cache, social_cache, briefing_cache,
    hero_cache, agent_cache, heatmap_cache, macro_ai_cache,
    get_top10_items, get_top10_asof, set_top10,
    get_scan_status, update_scan_status, increment_scan_progress,
    append_briefing, get_briefing_history,
    restore_all_from_redis, all_cache_stats,
)
from core.scan_coordinator import scan_coordinator
from core.circuit_breaker import all_provider_status
from core.rate_limiter import check_rate_limit, RateLimitExceeded
from core.response_envelope import success, error, not_found, rate_limited, service_unavailable, now_iso
from core.auth import require_jwt_secret, verify_jwt
from api.auth import router as auth_router
from api.phase4_endpoints import router as phase4_router

from config import (
    BOT_VERSION, APP_NAME, CONFIDENCE_MIN, UNIVERSE,
    MACRO_SYMBOLS, FINANCE_QUOTES, FINANCE_BOOKS, STATIC_RATES,
    BACKGROUND_SCAN_STARTUP_DELAY,
    BACKGROUND_SCAN_INTERVAL_OPEN, BACKGROUND_SCAN_INTERVAL_CLOSED,
)
from utils.helpers import normalize_symbol, base_ticker, clean_for_json
from utils.market_status import get_market_status, is_scan_worthwhile
from ai.engine import AI_AVAILABLE, AI_PROVIDERS
from ai.prompts import build_rich_context
from ai.service import (
    generate_trader_summary, generate_hero_story,
    generate_briefing, generate_macro_commentary,
    generate_cross_commentary, generate_agent_answer,
    generate_social_sentiment,
)
from engine.analysis import analyze_symbol
from engine.signal_engine import enrich_signals
from engine.technical import (
    compute_technical, generate_chart_png,
    batch_download_history, cross_hunter, CHART_AVAILABLE,
)
from engine.aggregation import (
    build_scan_item, build_batch_item,
    build_dashboard_data, build_hero_data, build_heatmap_sectors,
    build_briefing_context, build_agent_context,
)
from data.macro import fetch_all_macro, is_yfinance_available
from engine.macro_decision import compute_regime, get_sector_rotation
from engine.macro_signals import build_engine_inputs, build_freshness_report
from engine.action_summary import generate_action_summary
from ai.macro_roles import MACRO_AI_ROLES
from ai.safety import safe_ai_generate, validate_ai_output, FALLBACK_MESSAGES
from infra.storage import init_db
from engine.watchlist import add as wl_add, remove as wl_remove, get_symbols as wl_symbols, get_enriched as wl_enriched
from engine.alerts import generate_watchlist_alerts, get_user_alerts

try:
    from data.providers import BORSAPY_AVAILABLE
except ImportError:
    BORSAPY_AVAILABLE = False

setup_logging()
log = get_logger("bistbull")
SYSTEM_START = dt.datetime.now(dt.timezone.utc)

# ================================================================
# BACKGROUND SCANNER (stays in app.py per scope constraint)
# ================================================================
_daily_changes: dict[str, dict] = {}

async def _background_scanner():
    await asyncio.sleep(BACKGROUND_SCAN_STARTUP_DELAY)
    # Pre-warm macro cache so first user request is instant
    try:
        results = await asyncio.to_thread(fetch_all_macro)
        if results:
            macro_cache.set("macro_all", {"timestamp": now_iso(), "items": clean_for_json(results), "rates": clean_for_json(STATIC_RATES)})
            log.info(f"Macro pre-warmed: {len(results)} items cached")
    except Exception as e:
        log.debug(f"Macro pre-warm skipped: {e}")
    while True:
        try:
            has_data = bool(get_top10_items())
            last_ts = scan_coordinator._started_at or 0
            if is_scan_worthwhile(has_data, last_ts):
                log.info("Background scan başladı...")
                def _history_fn(univ):
                    syms = [normalize_symbol(t) for t in univ]
                    hmap = batch_download_history(syms, "1y", "1d")
                    for sym, hdf in hmap.items():
                        history_cache.set(sym, hdf)
                        if hdf is not None and len(hdf) >= 2:
                            try:
                                last_close = float(hdf["Close"].iloc[-1])
                                prev_close = float(hdf["Close"].iloc[-2])
                                if prev_close > 0:
                                    chg = (last_close - prev_close) / prev_close * 100
                                    ticker = sym.replace(".IS", "")
                                    _daily_changes[ticker] = {"price": round(last_close, 2), "prev_close": round(prev_close, 2), "change_pct": round(chg, 2)}
                            except Exception: pass
                    if _daily_changes: log.info(f"Daily changes: {len(_daily_changes)} symbols cached for heatmap")
                    return hmap
                def _analyze_fn(ticker): return analyze_symbol(normalize_symbol(ticker))
                def _cross_fn(hmap): cross_hunter.scan_all(hmap)
                def _ai_enrich_fn(ranked):
                    if not AI_AVAILABLE: return
                    for r in ranked[:3]:
                        try: tech = tech_cache.get(r.get("symbol", "")); generate_trader_summary(r, tech)
                        except Exception: pass
                await asyncio.to_thread(scan_coordinator.start_scan, UNIVERSE, _analyze_fn, _history_fn, _cross_fn, _ai_enrich_fn)

                # Phase 4.7 A/B dual-write: when calibrated fits are
                # available on disk, run a secondary calibrated pass
                # so score_history has BOTH scoring_versions per
                # (symbol, snap_date). This drives /api/scoring/ab_report
                # and /ab_report with real paired telemetry. If fits
                # aren't loaded, this is a no-op (calibrated path just
                # falls back to v13 internally, which is a duplicate
                # write that upserts harmlessly — but we skip to save
                # the cost). Rule 6: this NEVER affects the primary v13
                # scan, only adds calibrated rows when meaningful.
                try:
                    from engine.scoring_calibrated import _get_fits
                    if _get_fits() is not None:
                        log.info("Phase 4.7 A/B dual-write: starting calibrated pass")
                        def _analyze_cal(ticker):
                            try:
                                return analyze_symbol(
                                    normalize_symbol(ticker),
                                    scoring_version="calibrated_2026Q1",
                                )
                            except Exception:
                                return None
                        # Sequential pass — we already did the parallel
                        # heavy work; this just re-scores using cached raw
                        # data + writes the second snapshot row.
                        for sym in UNIVERSE:
                            await asyncio.to_thread(_analyze_cal, sym)
                        log.info("Phase 4.7 A/B dual-write: calibrated pass done")
                    else:
                        log.debug("Phase 4.7 A/B dual-write: no fits, skipping calibrated pass")
                except Exception as e:
                    log.warning(f"A/B dual-write pass failed: {e}")

                heatmap_cache.clear()
                if AI_AVAILABLE and get_top10_items():
                    try: await _generate_briefing_internal()
                    except Exception: pass
            else:
                ms = get_market_status()
                log.info(f"Scan atlanıyor — {ms['reason']} ({ms['ist_time']} IST)")
        except Exception as e:
            log.error(f"Background scan hatası: {e}")
        ms = get_market_status()
        wait = BACKGROUND_SCAN_INTERVAL_OPEN if ms["status"] == "open" else BACKGROUND_SCAN_INTERVAL_CLOSED
        await asyncio.sleep(wait)

# ================================================================
# LIFESPAN
# ================================================================
@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    # Phase 1: refuse to boot without a real JWT_SECRET. Raises RuntimeError
    # (uvicorn will surface this as a startup failure) if the env var is
    # missing, too short, or still a placeholder string.
    require_jwt_secret()
    redis_client.startup()
    restore_results = restore_all_from_redis()
    log.info(f"{APP_NAME} {BOT_VERSION} | Universe: {len(UNIVERSE)} | AI: {','.join(AI_PROVIDERS) or 'OFF'} | Chart: {'ON' if CHART_AVAILABLE else 'OFF'} | Redis: {'ON' if redis_client.is_available() else 'OFF'} | Restore: {restore_results}")
    task = asyncio.create_task(_background_scanner())
    yield
    task.cancel(); redis_client.shutdown(); log.info(f"{APP_NAME} shutting down")

app = FastAPI(title="BistBull Terminal", version=BOT_VERSION, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

import secrets as _secrets
from fastapi.middleware.cors import CORSMiddleware
_AO = os.environ.get("ALLOWED_ORIGINS","").split(",")
_AO = [o.strip() for o in _AO if o.strip()]
if _AO: app.add_middleware(CORSMiddleware,allow_origins=_AO,allow_methods=["GET","POST","DELETE"],allow_headers=["Content-Type"])
@app.middleware("http")
async def sec_mw(request:Request,call_next):
    r=await call_next(request)
    r.headers["X-Content-Type-Options"]="nosniff"
    r.headers["X-Frame-Options"]="DENY"
    r.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
    # Phase 1 additions -- full complement of defense-in-depth headers.
    # HSTS: force HTTPS for a year; only effective if served over TLS.
    r.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
    # CSP: self + inline (legacy app.js in landing.html is inline) + cdnjs
    # for Chart.js / similar libs the frontend pulls. Google Fonts allowed
    # for style + font. data: images for base64 hero etc. External https:
    # images are allowed -- the analyze layer occasionally embeds KAP logos.
    # 'unsafe-inline' is a known weakness; tightening requires nonce/hash
    # plumbing in the render path, deferred to a later phase.
    r.headers["Content-Security-Policy"]=(
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'"
    )
    r.headers["Permissions-Policy"]="geolocation=(), microphone=(), camera=()"
    return r
@app.middleware("http")
async def ses_mw(request:Request,call_next):
    # Phase 1: JWT-first, bb_session fallback. Authorization: Bearer <jwt>
    # overrides the cookie-derived anonymous id so logged-in users get a
    # persistent identity on every endpoint that reads request.state.user_id,
    # without per-endpoint changes. The cookie is still set so logout
    # (client drops token) reverts cleanly to anonymous.
    auth_header = request.headers.get("authorization", "")
    jwt_uid = None
    if auth_header.lower().startswith("bearer "):
        jwt_uid = verify_jwt(auth_header[7:].strip())
    sid=request.cookies.get("bb_session")
    if not sid:sid=_secrets.token_urlsafe(32)
    request.state.user_id = jwt_uid or sid
    r=await call_next(request)
    r.set_cookie("bb_session",sid,httponly=True,samesite="strict",max_age=86400*90)
    return r

# Phase 1: /api/auth/* endpoints (register/login/logout/me).
app.include_router(auth_router)
app.include_router(phase4_router)

@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return rate_limited(message=str(exc), retry_after=exc.retry_after)

# ================================================================
# CORE ENDPOINTS
# ================================================================

@app.get("/api/bootstrap")
async def api_bootstrap():
    """Single request for initial page load."""
    items = get_top10_items()
    asof = get_top10_asof()
    macro_data = macro_cache.get("macro_all")
    ms = get_market_status()
    cross_data = cross_hunter.last_results or []
    hero_data = hero_cache.get("hero")
    dash_data = build_dashboard_data(items, raw_cache_len=len(raw_cache), tech_cache_len=len(tech_cache), cross_signal_count=len(cross_data)) if items else None
    return success({
        "quote": FINANCE_QUOTES[dt.datetime.now().timetuple().tm_yday % len(FINANCE_QUOTES)],
        "book": FINANCE_BOOKS[dt.datetime.now().timetuple().tm_yday % len(FINANCE_BOOKS)],
        "market_status": ms,
        "macro": macro_data,
        "top10": {"items": [build_scan_item(r) for r in items] if items else [], "total_scanned": len(UNIVERSE)},
        "dashboard": dash_data,
        "hero": hero_data,
        "scan_progress": scan_coordinator.get_progress(),
        "cross_summary": {"total": len(cross_data), "bullish": sum(1 for s in cross_data if s.get("signal_type") == "bullish"), "bearish": sum(1 for s in cross_data if s.get("signal_type") == "bearish")},
        "stats": {"scans_done": len(analysis_cache), "signals_total": len(cross_data), "macro_tracked": len((macro_data or {}).get("items", [])), "cache_raw": len(raw_cache), "cache_tech": len(tech_cache), "uptime_hours": round((dt.datetime.now(dt.timezone.utc) - SYSTEM_START).total_seconds() / 3600, 1), "universe": len(UNIVERSE)},
        "version": BOT_VERSION,
        "ai_available": bool(AI_PROVIDERS),
        "chart_available": CHART_AVAILABLE,
    }, as_of=asof.isoformat() if asof and hasattr(asof, "isoformat") else None)

@app.get("/api/universe")
async def api_universe(): return success({"universe": UNIVERSE, "count": len(UNIVERSE)})

@app.get("/api/analyze/{ticker}")
async def api_analyze(ticker: str, scoring_version: Optional[str] = None):
    # Phase 4.9: scoring_version query param. None => env var default
    # (SCORING_VERSION_DEFAULT -> 'v13_handpicked'). When explicitly set
    # to 'calibrated_2026Q1', analyze_symbol routes value/quality/
    # growth/balance through the calibrated dispatcher. Fallback to V13
    # (when no fits loaded) is surfaced via r["_meta"].
    symbol = normalize_symbol(ticker)
    with LogTimer() as t:
        try:
            r = await asyncio.to_thread(analyze_symbol, symbol, scoring_version)
            m = r["metrics"]
            if m.get("price") is None and m.get("market_cap") is None and m.get("pe") is None: raise ValueError("No data")
            return success(r, latency_ms=t.ms)
        except Exception as e:
            log.warning(f"analyze {ticker}: {e}"); return not_found(f"Veri alınamadı: {base_ticker(ticker)}")

@app.get("/api/technical/{ticker}")
async def api_technical(ticker: str):
    symbol = normalize_symbol(ticker)
    try:
        tech = await asyncio.to_thread(compute_technical, symbol)
        if not tech: raise ValueError("No technical data")
        return success(tech)
    except Exception as e:
        log.warning(f"technical {ticker}: {e}"); return not_found(f"Teknik veri alınamadı: {base_ticker(ticker)}")

@app.get("/api/chart/{ticker}")
async def api_chart(ticker: str):
    symbol = normalize_symbol(ticker)
    try:
        tech = await asyncio.to_thread(compute_technical, symbol)
        chart_bytes = await asyncio.to_thread(generate_chart_png, symbol, tech)
        if chart_bytes: return Response(content=chart_bytes, media_type="image/png")
        raise ValueError("Chart failed")
    except Exception as e:
        log.warning(f"chart {ticker}: {e}"); return error("Grafik oluşturulamadı", status_code=500)

@app.get("/api/ai-summary/{ticker}")
async def api_ai_summary(request: Request, ticker: str):
    check_rate_limit(request, "ai_summary"); symbol = normalize_symbol(ticker)
    try:
        r = await asyncio.to_thread(analyze_symbol, symbol)
        tech = tech_cache.get(symbol)  # analyze_symbol already cached this
        result = await asyncio.to_thread(generate_trader_summary, r, tech)
        return success({
            "ticker": base_ticker(ticker),
            "summary": result.get("summary") or "AI özet oluşturulamadı.",
            "is_fallback": result.get("is_fallback", True),
            "data_grade": result.get("data_grade", "?"),
        })
    except Exception as e:
        log.warning(f"ai-summary {ticker}: {e}"); return error("AI özet alınamadı", status_code=500)

@app.get("/api/top10")
async def api_top10():
    items = get_top10_items(); asof = get_top10_asof()
    if items:
        return success({"items": [build_scan_item(r) for r in items], "total_scanned": len(UNIVERSE)}, as_of=asof.isoformat() if hasattr(asof, "isoformat") else str(asof) if asof else None)
    return success({"items": [], "total_scanned": 0, "message": "Tarama devam ediyor..."})

@app.get("/api/scan")
async def api_scan(request: Request):
    check_rate_limit(request, "scan"); status = get_scan_status(); items = get_top10_items(); asof = get_top10_asof()
    if status["running"]:
        return success({"items": [build_scan_item(r) for r in items] if items else [], "total_scanned": len(UNIVERSE), "scan_running": True}, as_of=asof.isoformat() if hasattr(asof, "isoformat") else str(asof) if asof else None)
    try:
        def _analyze_fn(ticker): return analyze_symbol(normalize_symbol(ticker))
        await asyncio.to_thread(scan_coordinator.start_scan, UNIVERSE, _analyze_fn)
        items = get_top10_items(); asof = get_top10_asof()
        return success({"items": [build_scan_item(r) for r in items], "total_scanned": len(UNIVERSE)}, as_of=asof.isoformat() if hasattr(asof, "isoformat") else str(asof) if asof else None)
    except Exception as e:
        log.error(f"scan: {e}"); return error("Scan başarısız", status_code=500)

@app.get("/api/scan-status")
async def api_scan_status(): return success(scan_coordinator.get_progress())

@app.get("/api/cross")
async def api_cross():
    """
    V3 FIX: Her çağrıda scan_all() tetiklemek yerine son scan sonuçlarını kullan.

    NEDEN: scan_all() → batch_download_history() → borsapy rate limit →
    her seferinde FARKLI alt küme → FARKLI sinyaller.

    ŞİMDİ: Background scanner tarafından üretilen last_results kullanılır.
    Veri yoksa (ilk startup) sadece O ZAMAN scan çalıştırılır.
    """
    try:
        # Önce son scan sonuçlarını kullan (deterministik)
        cached_signals = cross_hunter.last_results

        if not cached_signals and cross_hunter.last_scan == 0:
            # İlk startup — henüz hiç scan yapılmamış, bir kez çalıştır
            log.info("cross: ilk çağrı, scan başlatılıyor")
            cached_signals = await asyncio.to_thread(cross_hunter.scan_all)

        new_signals = enrich_signals(cached_signals or [], analysis_cache)
        bullish = sum(1 for s in new_signals if s.get("signal_type") == "bullish")
        bearish = sum(1 for s in new_signals if s.get("signal_type") == "bearish")
        total_stars = sum(s.get("stars", 1) for s in new_signals)
        vol_confirmed = sum(1 for s in new_signals if s.get("vol_confirmed"))
        kirilim_count = sum(1 for s in new_signals if s.get("category") == "kirilim")
        momentum_count = sum(1 for s in new_signals if s.get("category") == "momentum")
        quality_a = sum(1 for s in new_signals if s.get("signal_quality") == "A")
        quality_b = sum(1 for s in new_signals if s.get("signal_quality") == "B")
        ai_commentary = None
        if AI_AVAILABLE and new_signals:
            try:
                ai_commentary = await asyncio.to_thread(generate_cross_commentary, new_signals, bullish, bearish)
            except Exception as e: log.debug(f"cross AI: {e}")
        return success({"signals": new_signals, "ai_commentary": ai_commentary, "summary": {"total": len(new_signals), "bullish": bullish, "bearish": bearish, "kirilim": kirilim_count, "momentum": momentum_count, "total_stars": total_stars, "vol_confirmed": vol_confirmed, "quality_a": quality_a, "quality_b": quality_b, "scanned": len(UNIVERSE)}}, as_of=now_iso())
    except Exception as e:
        log.error(f"cross: {e}"); return error("Cross Hunter hatası", status_code=500)

# ================================================================
# HEALTH & STATUS
# ================================================================
@app.get("/api/health")
async def api_health():
    ms = get_market_status()
    return success({"version": BOT_VERSION, "app": APP_NAME, "universe": len(UNIVERSE), "ai": AI_PROVIDERS or False, "chart": CHART_AVAILABLE, "scan": scan_coordinator.status(), "market": ms, "redis": redis_client.health_check(), "providers": all_provider_status(), "cache": {"raw": len(raw_cache), "analysis": len(analysis_cache), "tech": len(tech_cache)}})

@app.get("/api/market-status")
async def api_market_status():
    ms = get_market_status(); asof = get_top10_asof()
    ms["last_scan"] = asof.isoformat() if hasattr(asof, "isoformat") else str(asof) if asof else None
    ms["data_age"] = None
    if asof and hasattr(asof, "isoformat"):
        try: ms["data_age"] = f"{(dt.datetime.now(dt.timezone.utc) - asof).total_seconds() / 3600:.1f} saat once"
        except Exception: pass
    return success(ms)

# ================================================================
# ANALYTICS
# ================================================================
TRACK_EVENTS = defaultdict(int); TRACK_LOG = deque(maxlen=500)

@app.api_route("/api/track", methods=["GET", "POST"])
async def api_track(e: str = ""):
    if e: TRACK_EVENTS[e] += 1; TRACK_LOG.append({"event": e, "ts": now_iso()})
    return JSONResponse({"ok": True})

@app.get("/api/analytics")
async def api_analytics(): return success({"events": dict(TRACK_EVENTS), "total": sum(TRACK_EVENTS.values()), "recent": list(TRACK_LOG)[-20:]})

# ================================================================
# MACRO
# ================================================================
@app.get("/api/macro")
async def api_macro():
    cached = macro_cache.get("macro_all")
    if cached is not None: return success(cached, cache_status="hit")
    try:
        results = await asyncio.to_thread(fetch_all_macro)
        result = {"timestamp": now_iso(), "items": clean_for_json(results), "rates": clean_for_json(STATIC_RATES)}
        macro_cache.set("macro_all", result); return success(result, cache_status="miss")
    except Exception as e:
        log.error(f"macro: {e}"); return error("Makro veri alınamadı", status_code=500)


@app.get("/api/rates")
async def api_rates(): return success({"rates": STATIC_RATES})
# ================================================================
# MACRO DECISION ENGINE
# ================================================================
@app.get("/api/macro/decision")
async def api_macro_decision():
    macro_data = macro_cache.get("macro_all")
    if not macro_data or not macro_data.get("items"):
        try:
            results = await asyncio.to_thread(fetch_all_macro)
            macro_data = {"timestamp": now_iso(), "items": clean_for_json(results), "rates": clean_for_json(STATIC_RATES)}
            if macro_data.get("items"):
                macro_cache.set("macro_all", macro_data)
        except Exception:
            pass
        if not macro_data or not macro_data.get("items"):
            return error("Makro veri henüz yüklenmedi.", status_code=503)
    try:
        inputs = build_engine_inputs(macro_data.get("items",[]), macro_data.get("rates",STATIC_RATES), macro_data.get("timestamp"))
        result = compute_regime(inputs)
        from engine.calendar import get_next_important_event, format_event_for_action, get_calendar_summary
        next_ev = get_next_important_event()
        ev_label = format_event_for_action(next_ev) if next_ev else None
        action = generate_action_summary(result, upcoming_event=ev_label)
        sectors = get_sector_rotation(result.regime)
        freshness = build_freshness_report(inputs)
        calendar = get_calendar_summary()
        return success({
            "regime": result.regime, "score": result.score, "confidence": result.confidence,
            "explanation": result.explanation,
            "signals": [{"name":s.name,"value":s.value,"score":s.score,"label":s.label,"source":s.source,"note":s.note} for s in result.signals],
            "contradictions": [{"type":c.type,"message":c.message} for c in result.contradictions],
            "action_summary": action, "sectors": sectors, "freshness": freshness,
            "calendar": calendar, "computed_at": result.computed_at,
        })
    except Exception as e:
        log.error(f"macro decision: {e}"); return error("Karar motoru hesaplanamadı", status_code=500)

@app.get("/api/macro/ai-roles")
async def api_macro_ai_roles(request: Request):
    check_rate_limit(request, "macro_commentary")
    if not AI_AVAILABLE: return success({"roles": {}, "error": "AI pasif"})
    cached = macro_ai_cache.get("macro_roles")
    if cached is not None: return success(cached, cache_status="hit")
    macro_data = macro_cache.get("macro_all")
    if not macro_data or not macro_data.get("items"): return success({"roles": {}, "error": "Makro veri henüz yüklenmedi."})
    try:
        inputs = build_engine_inputs(macro_data.get("items",[]), macro_data.get("rates",STATIC_RATES), macro_data.get("timestamp"))
        regime_result = compute_regime(inputs)
        from ai.engine import ai_call
        from engine.calendar import get_next_important_event, format_event_for_action
        next_ev = get_next_important_event()
        ev_label = format_event_for_action(next_ev) if next_ev else None
        action_text = generate_action_summary(regime_result, upcoming_event=ev_label)

        roles_output = {}
        for role_key, role_def in MACRO_AI_ROLES.items():
            prompt = role_def["prompt_fn"](regime_result)
            text = await asyncio.to_thread(
                safe_ai_generate, prompt, role_key,
                regime_result.regime, action_text,
                ai_call, 300, 2, regime_result.confidence
            )
            roles_output[role_key] = {
                "label": role_def["label"],
                "icon": role_def["icon"],
                "commentary": text,
                "is_fallback": text in FALLBACK_MESSAGES.values(),
            }
        result = {"roles": roles_output, "timestamp": now_iso(), "regime": regime_result.regime}
        macro_ai_cache.set("macro_roles", result); return success(result)
    except Exception as e:
        log.error(f"macro ai roles: {e}"); return success({"roles": {}, "error": str(e)})

@app.get("/api/macro/calendar")
async def api_macro_calendar():
    """Economic calendar — this week + upcoming 14 days."""
    try:
        from engine.calendar import get_calendar_summary
        return success(get_calendar_summary())
    except Exception as e:
        log.error(f"calendar: {e}"); return error("Takvim yüklenemedi", status_code=500)

@app.get("/api/macro/external-brief")
async def api_macro_external_brief():
    """External market context via Perplexity web search. NOT part of decision engine."""
    try:
        from ai.perplexity import fetch_external_brief, PERPLEXITY_AVAILABLE
        if not PERPLEXITY_AVAILABLE:
            return success({
                "brief": None, "available": False,
                "label": "Harici Piyasa Özeti",
                "disclaimer": "PERPLEXITY_API_KEY ayarlanmamış.",
                "feeds_decision": False,
            })
        result = await asyncio.to_thread(fetch_external_brief)
        return success(result)
    except Exception as e:
        log.warning(f"external brief: {e}")
        return success({"brief": None, "available": False, "error": str(e)})


# ================================================================
# DASHBOARD
# ================================================================
@app.get("/api/dashboard")
async def api_dashboard():
    items = get_top10_items()
    data = build_dashboard_data(items, raw_cache_len=len(raw_cache), tech_cache_len=len(tech_cache), cross_signal_count=len(cross_hunter.last_results))
    asof = get_top10_asof()
    return success(data, as_of=asof.isoformat() if asof and hasattr(asof, "isoformat") else None)

# ================================================================
# BRIEFING
# ================================================================
async def _generate_briefing_internal():
    items = get_top10_items()
    if not items: return {"briefing": "Henüz tarama yapılmadı.", "generated": False}
    ctx = build_briefing_context(items, cross_hunter.last_results)
    text = await asyncio.to_thread(generate_briefing, ctx)
    result = {"briefing": text, "generated": True, "timestamp": now_iso()}
    briefing_cache.set("daily_briefing", result)
    hour = dt.datetime.now().hour; period = "sabah" if hour < 12 else "oglen" if hour < 17 else "aksam"
    append_briefing({"text": text, "period": period, "timestamp": result["timestamp"]}); return result

@app.get("/api/briefing")
async def api_briefing(request: Request):
    check_rate_limit(request, "briefing")
    if not AI_AVAILABLE: return success({"briefing": None, "error": "AI pasif"})
    cached = briefing_cache.get("daily_briefing")
    if cached is not None: return success(cached, cache_status="hit")
    try: result = await _generate_briefing_internal(); return success(result)
    except Exception as e: return success({"briefing": None, "error": str(e)})

@app.get("/api/briefings/history")
async def api_briefings_history(): return success({"briefings": get_briefing_history()})

# ================================================================
# TAKAS
# ================================================================
def _fetch_takas_yfinance():
    results = []
    for ticker in UNIVERSE[:20]:
        try:
            foreign_pct = price = None; source = "N/A"
            if BORSAPY_AVAILABLE:
                try:
                    import borsapy as bp_m; _tk = bp_m.Ticker(ticker); fi = _tk.fast_info
                    fr = getattr(fi, "foreign_ratio", None)
                    if fr is not None: foreign_pct = round(fr * 100, 2); source = "borsapy_mkk"
                    lp = getattr(fi, "last_price", None)
                    if lp is not None: price = round(float(lp), 2)
                except Exception: pass
            if foreign_pct is None:
                pass  # yfinance kaldırıldı — borsapy yeterli
            results.append({"ticker": ticker, "foreign_pct": foreign_pct, "price": price, "change_pct": None, "source": source})
        except Exception: continue
    return results or None

@app.get("/api/takas")
async def api_takas():
    cached = takas_cache.get("takas_all")
    if cached is not None: return success(cached, cache_status="hit")
    try:
        data = await asyncio.to_thread(_fetch_takas_yfinance)
        if not data: return success({"items": [], "source": None, "error": "Takas verisi alınamadı."})
        data = sorted([d for d in data if d.get("foreign_pct") is not None], key=lambda x: x["foreign_pct"], reverse=True)
        result = {"timestamp": now_iso(), "items": clean_for_json(data), "source": "yfinance", "count": len(data)}
        takas_cache.set("takas_all", result); return success(result)
    except Exception as e: log.error(f"takas: {e}"); return error("Takas verisi alınamadı", status_code=500)

# ================================================================
# SOCIAL
# ================================================================
@app.get("/api/social")
async def api_social(request: Request):
    check_rate_limit(request, "social")
    cached = social_cache.get("social_sentiment")
    if cached is not None: return success(cached, cache_status="hit")
    result = await asyncio.to_thread(generate_social_sentiment)
    if result:
        social_cache.set("social_sentiment", result); return success(result)
    return success({"timestamp": now_iso(), "source": None, "trending": [], "overall_sentiment": "unavailable", "summary": "XAI_API_KEY gerekli.", "hot_topics": [], "error": "XAI_API_KEY gerekli"})

# ================================================================
# HEATMAP
# ================================================================
# HOTFIX 1 (2026-Q2 production incident): /api/heatmap was taking
# 10 minutes when cache was cold, because cache miss fell through to
# a sequential `for t in UNIVERSE: bp.Ticker(t).fast_info` loop that
# blocks the HTTP request. Result: users saw a blank page for 10min
# on first visit. Fix: cache miss NEVER blocks. We return whatever
# top10 data is already in memory (fast-path, <50ms) and flag the
# response `computing=true` if the full snapshot isn't ready yet.
# The slow 108-symbol fetch is handled EXCLUSIVELY by the background
# loop (engine/background_tasks.py:heatmap_refresh_loop).

_HEATMAP_REFRESH_LOCK = asyncio.Lock()  # prevents duplicate bg refreshes
_HEATMAP_REFRESH_INFLIGHT = False


def _fetch_heatmap_data():
    """Fast-path only: derive heatmap from already-cached top10 scan
    results + _daily_changes. Returns [] if snapshot not ready yet;
    caller flags response as computing=true. NEVER does sequential
    borsapy fetches on the request path (see HOTFIX 1 header)."""
    items = get_top10_items()
    if not items or not _daily_changes:
        return []
    results = []
    for it in items:
        ticker = it.get("ticker", "")
        dc = _daily_changes.get(ticker, {})
        price = dc.get("price") or it.get("price")
        chg = dc.get("change_pct", 0)
        mcap = it.get("market_cap")
        if price and mcap:
            results.append({
                "ticker": ticker,
                "price": round(float(price), 2),
                "change_pct": round(float(chg), 2),
                "market_cap": float(mcap),
                "sector": it.get("sector", "Diger") or "Diger",
                "score": it.get("overall"),
            })
    return results


async def _kick_background_heatmap_refresh():
    """Fire-and-forget: trigger a background heatmap fetch if none is
    already running. Respects the async lock so we don't stampede the
    borsapy API if multiple users hit /api/heatmap while cache is cold."""
    global _HEATMAP_REFRESH_INFLIGHT
    if _HEATMAP_REFRESH_INFLIGHT:
        return
    async with _HEATMAP_REFRESH_LOCK:
        if _HEATMAP_REFRESH_INFLIGHT:
            return
        _HEATMAP_REFRESH_INFLIGHT = True

    async def _bg_task():
        global _HEATMAP_REFRESH_INFLIGHT
        try:
            from engine.background_tasks import _refresh_heatmap_once
            await _refresh_heatmap_once()
        except Exception as e:
            log.warning(f"heatmap background refresh failed: {e}")
        finally:
            _HEATMAP_REFRESH_INFLIGHT = False

    asyncio.create_task(_bg_task())


@app.get("/api/heatmap")
async def api_heatmap():
    # HOTFIX 1: guaranteed <200ms response. Cache hit is unchanged.
    # Cache miss: build partial from top10 (fast), kick off background
    # refresh, flag computing=true so frontend shows its stale/empty
    # state rather than blocking.
    cached = heatmap_cache.get("heatmap")
    if cached is not None:
        return success(cached, cache_status="hit")

    # Fast-path: derive from top10 scan snapshot (already in memory)
    data = _fetch_heatmap_data()  # synchronous, but NO network I/O
    result = build_heatmap_sectors(data)

    if not data:
        # Top10 snapshot not ready either (fresh boot). Tell the
        # frontend it's computing; no blocking.
        await _kick_background_heatmap_refresh()
        result["computing"] = True
        return success(result, cache_status="cold")

    # We built a partial heatmap. Cache it briefly AND kick a proper
    # background refresh so the full version replaces it soon.
    result["computing"] = False
    result["source"] = "partial_from_top10"
    heatmap_cache.set("heatmap", result)
    await _kick_background_heatmap_refresh()
    return success(result, cache_status="partial")

# ================================================================
# QUOTE + BOOK + AGENT + HERO + LIVE + BATCH
# ================================================================
@app.get("/api/quote")
async def api_quote(): return JSONResponse(FINANCE_QUOTES[dt.datetime.now().timetuple().tm_yday % len(FINANCE_QUOTES)])

@app.get("/api/book")
async def api_book(): return JSONResponse(FINANCE_BOOKS[dt.datetime.now().timetuple().tm_yday % len(FINANCE_BOOKS)])

@app.get("/api/agent")
async def api_agent(request: Request, q: str = ""):
    if not q.strip(): return success({"answer": "BistBull Q aktif. Hisse kodu, sektör, sinyal veya makro — ne sorarsan somut veri ile yanıtlarım."})
    check_rate_limit(request, "agent")
    if not AI_AVAILABLE: return success({"answer": "AI motoru aktif değil.", "error": True})
    cached = agent_cache.get(q.strip().lower()[:100])
    if cached is not None: return success(cached, cache_status="hit")
    try:
        items = get_top10_items()
        context = build_agent_context(items, cross_hunter.last_results, q, build_rich_context)
        text = await asyncio.to_thread(generate_agent_answer, context, q)
        result = {"answer": text or "Cevap oluşturulamadı.", "cached": False}
        agent_cache.set(q.strip().lower()[:100], result); return success(result)
    except Exception as e: return success({"answer": f"Hata: {str(e)}", "error": True})

@app.get("/api/hero-summary")
async def api_hero_summary():
    cached = hero_cache.get("hero")
    if cached is not None: return success(cached, cache_status="hit")
    items = get_top10_items(); macro_data = macro_cache.get("macro_all") or {}; cross_data = cross_hunter.last_results or []
    result = build_hero_data(items, macro_data, cross_data)
    result = await asyncio.to_thread(generate_hero_story, result, items, macro_data.get("items", []), len(cross_data))
    if not result["story"]:
        t, b, br = result["stats"]["total"], result["stats"]["bullish"], result["stats"]["bearish"]
        result["story"] = f"{t} hisse tarandı. {b} pozitif, {br} zayıf."
    if not result["bot_says"]: result["bot_says"] = f"Piyasa {result['mode_label'].lower()} modda."
    hero_cache.set("hero", result); return success(result)

@app.get("/api/live/stats")
async def api_live_stats():
    uptime = (dt.datetime.now(dt.timezone.utc) - SYSTEM_START).total_seconds()
    return success({"scans_done": len(analysis_cache), "signals_total": len(cross_hunter.last_results), "macro_tracked": len((macro_cache.get("macro_all") or {}).get("items", [])), "cache_raw": len(raw_cache), "cache_tech": len(tech_cache), "uptime_hours": round(uptime / 3600, 1), "last_scan": get_top10_asof().isoformat() if get_top10_asof() and hasattr(get_top10_asof(), "isoformat") else None, "universe": len(UNIVERSE)})

@app.get("/api/batch/{tickers}")
async def api_batch(tickers: str):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()][:5]; results = []
    for t in ticker_list:
        try:
            r = await asyncio.to_thread(analyze_symbol, normalize_symbol(t)); results.append(build_batch_item(r))
        except Exception as e: results.append({"ticker": t, "error": str(e)})
    return success({"items": results})

# ================================================================
# WATCHLIST + ALERTS (Phase 7)
# ================================================================
def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", None) or request.cookies.get("bb_session", "anonymous")

@app.get("/api/watchlist")
async def api_watchlist_get(request: Request):
    uid = _user_id(request)
    cross_sigs = enrich_signals(cross_hunter.last_results or [], analysis_cache)
    items = await asyncio.to_thread(wl_enriched, uid, analysis_cache, cross_sigs)
    return success({"items": items, "count": len(items)})

@app.post("/api/watchlist")
async def api_watchlist_add(request: Request):
    uid = _user_id(request)
    try:
        body = await request.json()
        symbol = body.get("symbol", "")
    except Exception:
        return error("Geçersiz istek", status_code=400)
    if not symbol:
        return error("symbol alani gerekli", status_code=400)
    result = wl_add(uid, symbol)
    if not result["ok"]:
        return error(result["error"], status_code=400)
    return success(result)

@app.delete("/api/watchlist/{symbol}")
async def api_watchlist_remove(symbol: str, request: Request):
    uid = _user_id(request)
    result = wl_remove(uid, symbol)
    return success(result)

@app.get("/api/watchlist-changes")
async def api_watchlist_changes(request: Request):
    uid = _user_id(request)
    symbols = wl_symbols(uid)
    if not symbols:
        return success({"changes": [], "count": 0})
    try:
        from engine.delta import watchlist_changes
        changes = await asyncio.to_thread(watchlist_changes, symbols)
        return success({"changes": changes, "count": len(changes)})
    except Exception:
        return success({"changes": [], "count": 0})

@app.get("/api/movers")
async def api_movers():
    try:
        from engine.delta import get_movers
        movers = await asyncio.to_thread(get_movers)
        return success(movers)
    except Exception:
        return success({"gainers": [], "losers": []})

@app.get("/api/resolve-ticker")
async def api_resolve_ticker(q: str = ""):
    if not q:
        return success({"tickers": []})
    from engine.ticker_resolver import resolve_multiple
    tickers = resolve_multiple(q)
    return success({"tickers": tickers, "query": q})

@app.get("/api/search-suggest")
async def api_search_suggest(q: str = ""):
    if not q or len(q) < 2:
        return success({"suggestions": []})
    from engine.ticker_resolver import search_suggestions
    sugs = search_suggestions(q)
    return success({"suggestions": sugs})

@app.get("/api/compare")
async def api_compare(request: Request, left: str = "", right: str = ""):
    if not left or not right:
        return error("left ve right parametreleri gerekli", status_code=400)
    try:
        from engine.compare import compare_stocks
        l_sym = normalize_symbol(left)
        r_sym = normalize_symbol(right)
        l_analysis = await asyncio.to_thread(analyze_symbol, l_sym)
        r_analysis = await asyncio.to_thread(analyze_symbol, r_sym)
        comparison = compare_stocks(l_analysis, r_analysis)

        # AI commentary using smart context from compare engine + Perplexity news
        ai_commentary = None
        pplx_news = None
        if AI_AVAILABLE and comparison.get("ai_context"):
            try:
                from ai.engine import ai_call
                from ai.perplexity import fetch_compare_context, PERPLEXITY_AVAILABLE
                # Enrich with recent news if Perplexity available
                extra_ctx = ""
                if PERPLEXITY_AVAILABLE:
                    pplx_news = await asyncio.to_thread(fetch_compare_context, left, right)
                    if pplx_news:
                        extra_ctx = f"\n\nSON GELİŞMELER (web araması — karar motoruna girmez):\n{pplx_news}"
                prompt = _compare_prompt(
                    comparison["ai_context"] + extra_ctx,
                    comparison.get("left_ticker", "?"),
                    comparison.get("right_ticker", "?"),
                    comparison.get("analyst_commentary", ""),
                )
                ai_commentary = await asyncio.to_thread(
                    safe_ai_generate, prompt, "interpreter",
                    confidence="MEDIUM", max_retries=2, ai_call_fn=ai_call, max_tokens=350
                )
            except Exception as e:
                log.warning(f"compare AI: {e}")

        return success({
            "left": l_analysis, "right": r_analysis,
            "comparison": comparison,
            "ai_commentary": ai_commentary,
            "pplx_news": pplx_news,
        })
    except Exception as e:
        log.warning(f"compare {left} vs {right}: {e}")
        return error("Karşılaştırma yapılamadı", status_code=500)


def _compare_prompt(ctx: str, lt: str, rt: str, det_summary: str = "") -> str:
    return f"""Sen kurumsal BIST analisti ve portföy yöneticisisin. 20 yıllık tecrüben var.
İki hisseyi karşılaştıran somut veri var. Buna dayanarak keskin bir yorum yaz.

KURALLAR:
- Max 4 cümle. Her cümle bir rakama referans versin.
- Bir hisseyi öv değil — farkları açıkla, güçlü/zayıf tarafları göster.
- "Hangisini almalıyım?" sorusuna CEVAP VERME.
- Sonunda bir risk/uyarı notu ekle.
- "Sen" dili kullan, "yatırımcılar" değil.

YASAK: kesinlikle, garanti, kaçırma, patlayacak, uçacak, hemen al, büyük fırsat.
YASAK KALIP: "karışık bir görünüm hakim", "dikkatli olunması önerilir".

VERİLER:
{ctx}

{f'DETERMİNİSTİK ÖZET (bununla çelişme): {det_summary}' if det_summary else ''}

Şimdi yaz (4 cümle, rakam kullan):"""

@app.get("/api/stock-news/{ticker}")
async def api_stock_news(ticker: str):
    """Latest news about a BIST stock via Perplexity. NOT part of decision engine."""
    try:
        from ai.perplexity import fetch_stock_news, PERPLEXITY_AVAILABLE
        if not PERPLEXITY_AVAILABLE:
            return success({"available": False, "news": None, "reason": "PERPLEXITY_API_KEY not set"})
        result = await asyncio.to_thread(fetch_stock_news, ticker)
        return success(result)
    except Exception as e:
        log.warning(f"stock news {ticker}: {e}")
        return success({"available": False, "news": None})

@app.get("/api/macro/cds-search")
async def api_cds_search():
    """Search for latest Turkey CDS via Perplexity web search. Still classified as estimated."""
    try:
        from ai.perplexity import search_cds_data, PERPLEXITY_AVAILABLE
        if not PERPLEXITY_AVAILABLE:
            return success({"found": False, "reason": "PERPLEXITY_API_KEY not set"})
        result = await asyncio.to_thread(search_cds_data)
        return success(result)
    except Exception as e:
        log.warning(f"CDS search: {e}")
        return success({"found": False})

@app.get("/api/alerts")
async def api_alerts_get(request: Request):
    uid = _user_id(request)
    alerts = await asyncio.to_thread(get_user_alerts, uid)
    return success({"alerts": alerts, "count": len(alerts)})

@app.post("/api/alerts/refresh")
async def api_alerts_refresh(request: Request):
    uid = _user_id(request)
    symbols = wl_symbols(uid)
    if not symbols:
        return success({"alerts": [], "count": 0, "message": "Watchlist boş"})
    cross_sigs = enrich_signals(cross_hunter.last_results or [], analysis_cache)
    new_alerts = await asyncio.to_thread(generate_watchlist_alerts, uid, symbols, analysis_cache, cross_sigs)
    return success({"new_alerts": new_alerts, "new_count": len(new_alerts)})

# ================================================================
# WEBSOCKET — Scan progress
# ================================================================
_ws_connections: set = set(); _WS_MAX = 50

@app.websocket("/ws/scan")
async def ws_scan(websocket: WebSocket):
    if len(_ws_connections) >= _WS_MAX: await websocket.close(code=1013, reason="Max connections reached"); return
    await websocket.accept(); _ws_connections.add(websocket)
    try:
        while True: progress = scan_coordinator.get_progress(); await websocket.send_json(progress); await asyncio.sleep(2.0)
    except WebSocketDisconnect: pass
    except Exception: pass
    finally: _ws_connections.discard(websocket)

# ================================================================
# SERVE FRONTEND
# ================================================================
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_INDEX_HTML = os.path.join(_BASE_DIR, "index.html"); _LANDING_HTML = os.path.join(_BASE_DIR, "landing.html")

@app.get("/favicon.ico")
@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def _suppress_icon(): return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
async def serve_landing():
    try:
        with open(_LANDING_HTML, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers={"Cache-Control": "public, max-age=600, stale-while-revalidate=1200", "Vary": "Accept-Encoding"})
    except FileNotFoundError: return await serve_terminal()

@app.get("/terminal", response_class=HTMLResponse)
async def serve_terminal():
    try:
        with open(_INDEX_HTML, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=600", "Vary": "Accept-Encoding"})
    except FileNotFoundError: return HTMLResponse(content="<h1>BistBull Terminal</h1><p>index.html bulunamadi</p>", status_code=500)

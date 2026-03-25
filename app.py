# ================================================================
# BISTBULL TERMINAL V9.1 — FastAPI APP (SLIM)
# Sadece endpoint'ler + background scanner + route'lar.
# Tüm iş mantığı modüllerden import edilir.
# ================================================================

import os
import asyncio
import logging
import datetime as dt
import time
import json
import threading
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response

# ================================================================
# INTERNAL MODULES
# ================================================================
from config import (
    BOT_VERSION, APP_NAME, CONFIDENCE_MIN, UNIVERSE,
    MACRO_SYMBOLS, FINANCE_QUOTES, FINANCE_BOOKS, STATIC_RATES,
    SCAN_MAX_WORKERS,
    BACKGROUND_SCAN_STARTUP_DELAY,
    BACKGROUND_SCAN_INTERVAL_OPEN, BACKGROUND_SCAN_INTERVAL_CLOSED,
)
from helpers import (
    normalize_symbol, base_ticker, fmt_num, fmt_pct,
    safe_num, clean_for_json,
)
from cache import (
    raw_cache, analysis_cache, tech_cache, history_cache,
    macro_cache, takas_cache, social_cache, briefing_cache,
    hero_cache, agent_cache, heatmap_cache, macro_ai_cache,
    get_top10_items, get_top10_asof, set_top10,
    get_scan_status, update_scan_status, increment_scan_progress,
    append_briefing, get_briefing_history,
)
from market_status import get_market_status, is_scan_worthwhile
from ai_engine import AI_AVAILABLE, AI_PROVIDERS, ai_call, build_rich_context, ai_trader_summary
from analysis import fetch_raw, analyze_symbol
from technical import (
    compute_technical, generate_chart_png,
    batch_download_history, cross_hunter, CHART_AVAILABLE,
)

# ================================================================
# OPTIONAL IMPORTS
# ================================================================
try:
    import yfinance as yf
    os.makedirs("/tmp/yf-cache", exist_ok=True)
    yf.set_tz_cache_location("/tmp/yf-cache")
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

try:
    from data_layer_v9 import BORSAPY_AVAILABLE
except ImportError:
    BORSAPY_AVAILABLE = False

# ================================================================
# LOGGING
# ================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bistbull")

# ================================================================
# SCAN LOCK
# ================================================================
_SCAN_LOCK = threading.Lock()


def _analyze_safe(ticker: str) -> dict | None:
    try:
        r = analyze_symbol(normalize_symbol(ticker))
        increment_scan_progress()
        return r
    except Exception as e:
        increment_scan_progress()
        log.debug(f"scan skip {ticker}: {e}")
        return None


def scan_universe_blocking() -> list[dict]:
    acquired = _SCAN_LOCK.acquire(blocking=False)
    if not acquired:
        log.info("Scan zaten çalışıyor, skip")
        return get_top10_items()
    try:
        update_scan_status(running=True, phase="analyzing", progress=0, total=len(UNIVERSE), started=time.time())
        ranked = []
        with ThreadPoolExecutor(max_workers=min(SCAN_MAX_WORKERS, len(UNIVERSE))) as pool:
            futures = {pool.submit(_analyze_safe, t): t for t in UNIVERSE}
            for future in as_completed(futures):
                r = future.result()
                if r and r["confidence"] >= CONFIDENCE_MIN:
                    ranked.append(r)
        ranked.sort(key=lambda x: (x["overall"], x["scores"]["quality"]), reverse=True)
        set_top10(dt.datetime.now(dt.timezone.utc), ranked)
        update_scan_status(running=False, phase="done", progress=len(UNIVERSE), total=len(UNIVERSE))
        log.info(f"Scan tamamlandi: {len(ranked)} hisse")
        return ranked
    except Exception:
        update_scan_status(running=False, phase="error")
        raise
    finally:
        _SCAN_LOCK.release()


# ================================================================
# BACKGROUND SCANNER
# ================================================================
async def _background_scanner():
    await asyncio.sleep(BACKGROUND_SCAN_STARTUP_DELAY)
    while True:
        try:
            has_data = bool(get_top10_items())
            if is_scan_worthwhile(has_data):
                log.info("Background scan başladı...")
                t0 = time.time()
                symbols = [normalize_symbol(t) for t in UNIVERSE]
                history_map = await asyncio.to_thread(batch_download_history, symbols, "1y", "1d")
                for sym, hist_df in history_map.items():
                    history_cache.set(sym, hist_df)
                log.info(f"Batch history: {len(history_map)} ({time.time()-t0:.1f}s)")
                await asyncio.to_thread(scan_universe_blocking)
                await asyncio.to_thread(cross_hunter.scan_all, history_map)
                items = get_top10_items()
                log.info(f"Scan done: {len(items)} hisse, {len(cross_hunter.last_results)} sinyal ({time.time()-t0:.1f}s)")
                if AI_AVAILABLE and items:
                    for r in items[:5]:
                        try:
                            tech = tech_cache.get(r["symbol"])
                            await asyncio.to_thread(ai_trader_summary, r, tech)
                        except Exception: pass
                    try: await api_briefing()
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
# FASTAPI APP
# ================================================================
@asynccontextmanager
async def lifespan(application: FastAPI):
    log.info(f"{APP_NAME} {BOT_VERSION} | Universe: {len(UNIVERSE)} | AI: {','.join(AI_PROVIDERS) or 'OFF'} | Chart: {'ON' if CHART_AVAILABLE else 'OFF'}")
    task = asyncio.create_task(_background_scanner())
    yield
    task.cancel()
    log.info("BISTBULL TERMINAL shutting down")

app = FastAPI(title="BistBull Terminal", version="1.0", lifespan=lifespan)


# ================================================================
# HELPERS
# ================================================================
def _build_scan_item(r):
    return {
        "ticker": r["ticker"], "name": r["name"], "overall": r["overall"], "confidence": r["confidence"],
        "fa_score": r.get("fa_score", r.get("deger", r["overall"])), "deger": r.get("deger", r["overall"]),
        "ivme": r.get("ivme", 50), "risk_score": r.get("risk_score", 0),
        "entry_label": r.get("entry_label", ""), "is_hype": r.get("is_hype", False),
        "timing": r.get("timing", ""), "quality_tag": r.get("quality_tag", ""),
        "decision": r.get("decision", ""), "sector_group": r.get("sector_group", ""),
        "style": r["style"], "scores": r["scores"],
        "sector": r.get("sector", ""), "industry": r.get("industry", ""),
        "legendary": r["legendary"], "positives": r["positives"], "negatives": r["negatives"],
        "price": r["metrics"].get("price"), "market_cap": r["metrics"].get("market_cap"),
        "pe": r["metrics"].get("pe"), "pb": r["metrics"].get("pb"),
        "roe": r["metrics"].get("roe"), "revenue_growth": r["metrics"].get("revenue_growth"),
    }

def _items_by_ticker(items):
    """B3/B4 FIX: O(N²) → O(1) dict lookup."""
    return {item["ticker"]: item for item in items}


# ================================================================
# CORE API ENDPOINTS
# ================================================================
@app.get("/api/universe")
async def get_universe():
    return {"universe": UNIVERSE, "count": len(UNIVERSE)}

@app.get("/api/analyze/{ticker}")
async def api_analyze(ticker: str):
    symbol = normalize_symbol(ticker)
    try:
        r = await asyncio.to_thread(analyze_symbol, symbol)
        m = r["metrics"]
        if m.get("price") is None and m.get("market_cap") is None and m.get("pe") is None:
            raise ValueError("No data")
        return JSONResponse(content=clean_for_json(r))
    except Exception as e:
        log.warning(f"analyze {ticker}: {e}")
        raise HTTPException(status_code=404, detail=f"Veri alınamadı: {base_ticker(ticker)}")

@app.get("/api/technical/{ticker}")
async def api_technical(ticker: str):
    symbol = normalize_symbol(ticker)
    try:
        tech = await asyncio.to_thread(compute_technical, symbol)
        if not tech: raise ValueError("No technical data")
        return JSONResponse(content=clean_for_json(tech))
    except Exception as e:
        log.warning(f"technical {ticker}: {e}")
        raise HTTPException(status_code=404, detail=f"Teknik veri alınamadı: {base_ticker(ticker)}")

@app.get("/api/chart/{ticker}")
async def api_chart(ticker: str):
    symbol = normalize_symbol(ticker)
    try:
        tech = await asyncio.to_thread(compute_technical, symbol)
        chart_bytes = await asyncio.to_thread(generate_chart_png, symbol, tech)
        if chart_bytes: return Response(content=chart_bytes, media_type="image/png")
        raise ValueError("Chart failed")
    except Exception as e:
        log.warning(f"chart {ticker}: {e}")
        raise HTTPException(status_code=500, detail="Grafik oluşturulamadı")

@app.get("/api/ai-summary/{ticker}")
async def api_ai_summary(ticker: str):
    symbol = normalize_symbol(ticker)
    try:
        r = await asyncio.to_thread(analyze_symbol, symbol)
        tech = await asyncio.to_thread(compute_technical, symbol)
        text = await asyncio.to_thread(ai_trader_summary, r, tech)
        return {"ticker": base_ticker(ticker), "summary": text or "AI özet oluşturulamadı"}
    except Exception as e:
        log.warning(f"ai-summary {ticker}: {e}")
        raise HTTPException(status_code=500, detail="AI özet alınamadı")

@app.get("/api/top10")
async def api_top10():
    items = get_top10_items()
    if items:
        asof = get_top10_asof()
        return {"asof": asof.isoformat() if asof else None, "items": clean_for_json([_build_scan_item(r) for r in items]), "total_scanned": len(UNIVERSE)}
    return {"asof": None, "items": [], "total_scanned": 0, "message": "Tarama devam ediyor..."}

@app.get("/api/scan")
async def api_scan():
    status = get_scan_status()
    if status["running"]:
        items = get_top10_items()
        if items:
            asof = get_top10_asof()
            return {"asof": asof.isoformat() if asof else None, "items": clean_for_json([_build_scan_item(r) for r in items]), "total_scanned": len(UNIVERSE), "scan_running": True}
        return {"asof": None, "items": [], "total_scanned": 0, "message": "Tarama devam ediyor...", "scan_running": True}
    try:
        ranked = await asyncio.to_thread(scan_universe_blocking)
        asof = get_top10_asof()
        return {"asof": asof.isoformat() if asof else None, "items": clean_for_json([_build_scan_item(r) for r in ranked]), "total_scanned": len(UNIVERSE)}
    except Exception as e:
        log.error(f"scan: {e}")
        raise HTTPException(status_code=500, detail="Scan başarısız")

@app.get("/api/scan-status")
async def api_scan_status():
    status = get_scan_status()
    asof = get_top10_asof()
    elapsed = round(time.time() - status["started"], 1) if status["started"] and status["running"] else None
    return {"running": status["running"], "phase": status["phase"], "progress": status["progress"], "total": status["total"], "has_data": bool(get_top10_items()), "asof": asof.isoformat() if asof else None, "elapsed": elapsed}

@app.get("/api/cross")
async def api_cross():
    try:
        new_signals = await asyncio.to_thread(cross_hunter.scan_all)
        bullish = sum(1 for s in new_signals if s.get("signal_type") == "bullish")
        bearish = sum(1 for s in new_signals if s.get("signal_type") == "bearish")
        total_stars = sum(s.get("stars", 1) for s in new_signals)
        vol_confirmed = sum(1 for s in new_signals if s.get("vol_confirmed"))
        kirilim_count = sum(1 for s in new_signals if s.get("category") == "kirilim")
        momentum_count = sum(1 for s in new_signals if s.get("category") == "momentum")
        ai_commentary = None
        if AI_AVAILABLE and new_signals:
            try:
                ticker_groups = defaultdict(list)
                for s in new_signals[:15]:
                    ticker_groups[s["ticker"]].append(f"{s['signal']}({'*'*s.get('stars',1)})")
                sig_summary = "; ".join(f"{t}: {', '.join(sigs)}" for t, sigs in list(ticker_groups.items())[:8])
                prompt = f"Sen BistBull Cross Hunter sinyal analistisin. Türkçe, somut.\n{len(new_signals)} sinyal ({bullish} yukari, {bearish} asagi).\nSinyaller: {sig_summary}\n\n2-3 cümle: Dikkat çekici sinyal? Hacim teyidi?"
                ai_commentary = await asyncio.to_thread(ai_call, prompt, 250)
            except Exception as e: log.debug(f"cross AI: {e}")
        return {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "signals": clean_for_json(new_signals), "ai_commentary": ai_commentary, "summary": {"total": len(new_signals), "bullish": bullish, "bearish": bearish, "kirilim": kirilim_count, "momentum": momentum_count, "total_stars": total_stars, "vol_confirmed": vol_confirmed, "scanned": len(UNIVERSE)}}
    except Exception as e:
        log.error(f"cross: {e}")
        raise HTTPException(status_code=500, detail="Cross Hunter hatası")

@app.get("/api/health")
async def api_health():
    ms = get_market_status()
    status = get_scan_status()
    return {"status": "ok", "version": BOT_VERSION, "app": APP_NAME, "universe": len(UNIVERSE), "ai": AI_PROVIDERS or False, "chart": CHART_AVAILABLE, "scanning": status["running"], "scan_phase": status["phase"], "market": ms, "cache": {"raw": len(raw_cache), "analysis": len(analysis_cache), "tech": len(tech_cache)}}

@app.get("/api/market-status")
async def api_market_status():
    ms = get_market_status()
    asof = get_top10_asof()
    ms["last_scan"] = asof.isoformat() if asof else None
    ms["data_age"] = None
    if asof:
        age_hours = (dt.datetime.now(dt.timezone.utc) - asof).total_seconds() / 3600
        ms["data_age"] = f"{age_hours:.1f} saat once"
    return ms

# ================================================================
# ANALYTICS
# ================================================================
TRACK_EVENTS = defaultdict(int)
TRACK_LOG = deque(maxlen=500)

@app.api_route("/api/track", methods=["GET", "POST"])
async def api_track(e: str = ""):
    if e: TRACK_EVENTS[e] += 1; TRACK_LOG.append({"event": e, "ts": dt.datetime.now(dt.timezone.utc).isoformat()})
    return {"ok": True}

@app.get("/api/analytics")
async def api_analytics():
    return {"events": dict(TRACK_EVENTS), "total": sum(TRACK_EVENTS.values()), "recent": list(TRACK_LOG)[-20:]}

# ================================================================
# MACRO
# ================================================================
def _fetch_one_macro(key: str, info: dict, ytd_start: str) -> dict | None:
    """Tek bir makro sembolün verisini çek — yf.Ticker ile."""
    try:
        tk = yf.Ticker(info["symbol"])
        h = tk.history(start=ytd_start, interval="1d")
        if h is None or h.empty or len(h) < 2:
            return None
        price = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2])
        change_pct = ((price - prev) / prev * 100) if prev != 0 else 0
        first_close = float(h["Close"].iloc[0])
        ytd_pct = ((price - first_close) / first_close * 100) if first_close != 0 else 0
        m1_pct = ((price - float(h["Close"].iloc[-22])) / float(h["Close"].iloc[-22]) * 100) if len(h) >= 22 else None
        w1_pct = ((price - float(h["Close"].iloc[-5])) / float(h["Close"].iloc[-5]) * 100) if len(h) >= 5 else None
        return {
            "key": key, "name": info["name"], "category": info["category"],
            "flag": info.get("flag", ""), "price": round(price, 4),
            "change": round(price - prev, 4), "change_pct": round(change_pct, 2),
            "ytd_pct": round(ytd_pct, 2),
            "m1_pct": round(m1_pct, 2) if m1_pct is not None else None,
            "w1_pct": round(w1_pct, 2) if w1_pct is not None else None,
        }
    except Exception as e:
        log.debug(f"Macro {key}: {e}")
        return None


def _fetch_all_macro():
    if not YF_AVAILABLE: return []
    now = dt.datetime.now()
    ytd_start = dt.datetime(now.year, 1, 1).strftime("%Y-%m-%d")
    results = []
    # Tek tek çek, 5 paralel — batch download Yahoo'da bloklanıyor
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch_one_macro, k, v, ytd_start): k for k, v in MACRO_SYMBOLS.items()}
        for future in as_completed(futures):
            r = future.result()
            if r:
                results.append(r)
    log.info(f"Macro: {len(results)}/{len(MACRO_SYMBOLS)} başarılı")
    return results

@app.get("/api/macro")
async def api_macro():
    cached = macro_cache.get("macro_all")
    if cached is not None: return cached
    try:
        results = await asyncio.to_thread(_fetch_all_macro)
        result = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "items": clean_for_json(results), "rates": clean_for_json(STATIC_RATES)}
        macro_cache.set("macro_all", result)
        return result
    except Exception as e:
        log.error(f"macro: {e}")
        raise HTTPException(status_code=500, detail="Makro veri alınamadı")

@app.get("/api/macro/commentary")
async def api_macro_commentary():
    if not AI_AVAILABLE: return {"commentary": None, "error": "AI pasif"}
    cached = macro_ai_cache.get("macro_ai")
    if cached is not None: return cached
    try:
        macro_data = macro_cache.get("macro_all")
        if not macro_data or not macro_data.get("items"): return {"commentary": "Makro veri henüz yüklenmedi.", "generated": False}
        lines = [f"{m.get('flag','')} {m['name']}: {m['price']}, gun:{m['change_pct']}%, YTD:{m.get('ytd_pct','?')}%" for m in sorted(macro_data["items"], key=lambda x: x.get("ytd_pct") or 0, reverse=True)]
        prompt = "Sen BistBull makro stratejistisin. Türkçe, somut.\n\n" + "\n".join(lines[:20]) + "\n\nTABLO: 2 cümle\nEM: 1 cümle\nBIST: 1 cümle\nSTRATEJİ: 1 cümle"
        text = await asyncio.to_thread(ai_call, prompt, 300)
        result = {"commentary": text, "generated": True, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}
        macro_ai_cache.set("macro_ai", result)
        return result
    except Exception as e: return {"commentary": None, "error": str(e)}

@app.get("/api/rates")
async def api_rates():
    return {"rates": clean_for_json(STATIC_RATES)}

# ================================================================
# DASHBOARD
# ================================================================
@app.get("/api/dashboard")
async def api_dashboard():
    items = get_top10_items()
    scanned = len(items)
    top3 = [{"ticker": r["ticker"], "name": r["name"], "overall": r["overall"], "style": r["style"], "scores": r["scores"], "price": r["metrics"].get("price"), "positives": r["positives"][:2]} for r in items[:3]]
    opps = sorted([r for r in items if r["scores"].get("value", 0) >= 55], key=lambda x: x["scores"].get("value", 0) + x["scores"].get("growth", 0), reverse=True)
    opportunities = [{"ticker": r["ticker"], "name": r["name"], "overall": r["overall"], "reason": f"Value: {r['scores']['value']:.0f} + Growth: {r['scores']['growth']:.0f}", "price": r["metrics"].get("price")} for r in opps[:3]]
    risky = sorted([r for r in items if r["scores"].get("balance", 100) < 50 or r["overall"] < 40], key=lambda x: x["overall"])
    risks = [{"ticker": r["ticker"], "name": r["name"], "overall": r["overall"], "reason": "; ".join(r["negatives"][:2]), "price": r["metrics"].get("price")} for r in risky[:3]]
    sec_map = defaultdict(lambda: {"count": 0, "avg_score": 0, "tickers": []})
    for r in items:
        sec = r.get("sector") or "Diger"; sec_map[sec]["count"] += 1; sec_map[sec]["avg_score"] += r["overall"]; sec_map[sec]["tickers"].append(r["ticker"])
    sectors = sorted([{"sector": sec, "count": d["count"], "avg_score": round(d["avg_score"]/max(d["count"],1), 1), "tickers": d["tickers"][:5]} for sec, d in sec_map.items()], key=lambda x: x["avg_score"], reverse=True)
    style_map = defaultdict(int)
    for r in items: style_map[r["style"]] += 1
    return {"scanned": scanned, "asof": get_top10_asof().isoformat() if get_top10_asof() else None, "top3": clean_for_json(top3), "opportunities": clean_for_json(opportunities), "risks": clean_for_json(risks), "sectors": clean_for_json(sectors), "styles": dict(style_map), "counters": {"total_analyzed": scanned, "cache_raw": len(raw_cache), "cache_tech": len(tech_cache), "cross_signals": len(cross_hunter.last_results)}}

# ================================================================
# BRIEFING
# ================================================================
@app.get("/api/briefing")
async def api_briefing():
    if not AI_AVAILABLE: return {"briefing": None, "error": "AI pasif"}
    cached = briefing_cache.get("daily_briefing")
    if cached is not None: return cached
    try:
        items = get_top10_items()
        if not items: return {"briefing": "Henüz tarama yapılmadı.", "generated": False}
        top3_d = sorted(items, key=lambda x: x.get("deger", x["overall"]), reverse=True)[:3]
        top3_i = sorted(items, key=lambda x: x.get("ivme", 50), reverse=True)[:3]
        worst = sorted(items, key=lambda x: x.get("deger", x["overall"]))[:2]
        cross_data = cross_hunter.last_results[:5]
        summary_parts = [f"{r['ticker']}: D:{r.get('deger',r['overall']):.0f} I:{r.get('ivme',50):.0f} ({r['style']})" for r in items[:5]]
        deger_str = ", ".join(f"{r['ticker']}(D:{r.get('deger',r['overall']):.0f})" for r in top3_d)
        ivme_str = ", ".join(f"{r['ticker']}(I:{r.get('ivme',50):.0f})" for r in top3_i)
        worst_str = ", ".join(f"{r['ticker']}(D:{r.get('deger',r['overall']):.0f})" for r in worst)
        sig_str = ", ".join(f"{s['ticker']}:{s['signal']}" for s in cross_data[:3])
        prompt = f"Sen BistBull analisti. Türkçe, somut.\nTARAMA: {len(items)} hisse.\nDEGER: {deger_str}\nIVME: {ivme_str}\nZayif: {worst_str}\nTop 5: {'; '.join(summary_parts)}\nSinyal: {len(cross_data)} ({sig_str})\n\nÖZET: 2-3 cümle\nYATIRIMCI: 2 cümle\nTRADER: 2 cümle\nDİKKAT: 1 cümle"
        text = await asyncio.to_thread(ai_call, prompt, 400)
        result = {"briefing": text, "generated": True, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}
        briefing_cache.set("daily_briefing", result)
        hour = dt.datetime.now().hour
        period = "sabah" if hour < 12 else "oglen" if hour < 17 else "aksam"
        append_briefing({"text": text, "period": period, "timestamp": result["timestamp"]})
        return result
    except Exception as e: return {"briefing": None, "error": str(e)}

@app.get("/api/briefings/history")
async def api_briefings_history():
    return {"briefings": get_briefing_history()}

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
                    if fr is not None: foreign_pct = round(fr*100, 2); source = "borsapy_mkk"
                    lp = getattr(fi, "last_price", None)
                    if lp is not None: price = round(float(lp), 2)
                except Exception: pass
            if foreign_pct is None and YF_AVAILABLE:
                try:
                    tk = yf.Ticker(normalize_symbol(ticker)); info = tk.get_info() or {}
                    ip = info.get("heldPercentInstitutions")
                    if ip is not None: foreign_pct = round(ip*100, 2); source = "yfinance_institutional"
                    if price is None:
                        p = info.get("currentPrice") or info.get("regularMarketPrice")
                        if p is not None: price = round(float(p), 2)
                except Exception: pass
            results.append({"ticker": ticker, "foreign_pct": foreign_pct, "price": price, "change_pct": None, "source": source})
        except Exception: continue
    return results or None

@app.get("/api/takas")
async def api_takas():
    cached = takas_cache.get("takas_all")
    if cached is not None: return cached
    try:
        data = await asyncio.to_thread(_fetch_takas_yfinance)
        if not data: return {"items": [], "source": None, "error": "Takas verisi alınamadı."}
        data = sorted([d for d in data if d.get("foreign_pct") is not None], key=lambda x: x["foreign_pct"], reverse=True)
        result = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "items": clean_for_json(data), "source": "yfinance", "count": len(data)}
        takas_cache.set("takas_all", result)
        return result
    except Exception as e:
        log.error(f"takas: {e}")
        raise HTTPException(status_code=500, detail="Takas verisi alınamadı")

# ================================================================
# SOCIAL
# ================================================================
@app.get("/api/social")
async def api_social():
    cached = social_cache.get("social_sentiment")
    if cached is not None: return cached
    if AI_AVAILABLE and "grok" in AI_PROVIDERS:
        try:
            prompt = 'Sen BIST sosyal medya analistisin. X\'teki BIST tartışmalarını analiz et.\nJSON formatında: {"trending": [{"ticker": "THYAO", "sentiment": "bullish", "score": 78, "reason": "..."}], "overall_sentiment": "...", "summary": "...", "hot_topics": ["..."]}\nEn az 5, en fazla 10 hisse.'
            text = await asyncio.to_thread(ai_call, prompt, 500)
            if text:
                clean = text.strip()
                if clean.startswith("```"): clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"): clean = clean[:-3]
                clean = clean.strip()
                if clean.startswith("json"): clean = clean[4:].strip()
                try:
                    data = json.loads(clean)
                    result = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "source": "grok_ai", "trending": data.get("trending", []), "overall_sentiment": data.get("overall_sentiment", "neutral"), "summary": data.get("summary", ""), "hot_topics": data.get("hot_topics", [])}
                    social_cache.set("social_sentiment", result)
                    return result
                except json.JSONDecodeError:
                    result = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "source": "grok_ai", "trending": [], "overall_sentiment": "unknown", "summary": text[:500], "hot_topics": []}
                    social_cache.set("social_sentiment", result)
                    return result
        except Exception as e: log.warning(f"social grok: {e}")
    return {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "source": None, "trending": [], "overall_sentiment": "unavailable", "summary": "XAI_API_KEY gerekli.", "hot_topics": [], "error": "XAI_API_KEY gerekli"}

# ================================================================
# HEATMAP — B3 FIX: dict lookup
# ================================================================
def _fetch_heatmap_data():
    items = get_top10_items()
    item_map = _items_by_ticker(items)
    results = []
    if BORSAPY_AVAILABLE:
        try:
            import borsapy as bp_m
            for t in UNIVERSE:
                try:
                    _tk = bp_m.Ticker(t); fi = _tk.fast_info
                    last = getattr(fi, "last_price", None); prev = getattr(fi, "previous_close", None); mcap = getattr(fi, "market_cap", None)
                    if last is not None and prev is not None and prev > 0:
                        chg = (last - prev) / prev * 100
                        si = item_map.get(t)
                        results.append({"ticker": t, "price": round(float(last), 2), "change_pct": round(chg, 2), "market_cap": float(mcap) if mcap else None, "sector": (si.get("sector", "Diger") if si else "Diger") or "Diger", "score": si["overall"] if si else None})
                except Exception: continue
            if results: return results
        except Exception as e: log.warning(f"heatmap borsapy: {e}")
    if not YF_AVAILABLE: return results
    symbols = [normalize_symbol(t) for t in UNIVERSE]
    try:
        df = yf.download(symbols, period="2d", group_by="ticker", progress=False, threads=True)
        for t in UNIVERSE:
            sym = normalize_symbol(t)
            try:
                ticker_df = df if len(UNIVERSE) == 1 else (df[sym] if sym in df.columns.get_level_values(0) else None)
                if ticker_df is None or ticker_df.empty or len(ticker_df) < 2: continue
                prev_close = float(ticker_df["Close"].iloc[-2]); last_close = float(ticker_df["Close"].iloc[-1])
                if prev_close == 0: continue
                si = item_map.get(t)
                results.append({"ticker": t, "price": round(last_close, 2), "change_pct": round(((last_close-prev_close)/prev_close)*100, 2), "market_cap": si["metrics"].get("market_cap") if si else None, "sector": (si.get("sector", "Diger") if si else "Diger") or "Diger", "score": si["overall"] if si else None})
            except Exception: continue
    except Exception as e: log.warning(f"heatmap yfinance: {e}")
    return results

@app.get("/api/heatmap")
async def api_heatmap():
    cached = heatmap_cache.get("heatmap")
    if cached is not None: return cached
    data = await asyncio.to_thread(_fetch_heatmap_data)
    sectors = defaultdict(list)
    for d in data: sectors[d["sector"]].append(d)
    sector_list = sorted([{"sector": sec, "avg_change": round(sum(i["change_pct"] for i in si)/len(si), 2) if si else 0, "total_mcap": sum(i["market_cap"] or 0 for i in si), "count": len(si), "stocks": sorted(si, key=lambda x: abs(x["change_pct"]), reverse=True)} for sec, si in sectors.items()], key=lambda x: x["avg_change"], reverse=True)
    result = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "sectors": clean_for_json(sector_list), "total": len(data)}
    heatmap_cache.set("heatmap", result)
    return result

# ================================================================
# QUOTE + BOOK
# ================================================================
@app.get("/api/quote")
async def api_quote():
    return FINANCE_QUOTES[dt.datetime.now().timetuple().tm_yday % len(FINANCE_QUOTES)]

@app.get("/api/book")
async def api_book():
    return FINANCE_BOOKS[dt.datetime.now().timetuple().tm_yday % len(FINANCE_BOOKS)]

# ================================================================
# Q AGENT
# ================================================================
@app.get("/api/agent")
async def api_agent(q: str = ""):
    if not q.strip(): return {"answer": "BistBull Q aktif. Hisse kodu, sektör, sinyal veya makro — ne sorarsan somut veri ile yanıtlarım."}
    if not AI_AVAILABLE: return {"answer": "AI motoru aktif değil.", "error": True}
    cached = agent_cache.get(q.strip().lower()[:100])
    if cached is not None: return cached
    try:
        context = ""
        items = get_top10_items()
        if items:
            top3_d = sorted(items, key=lambda x: x.get("deger", x["overall"]), reverse=True)[:3]
            top3_i = sorted(items, key=lambda x: x.get("ivme", 50), reverse=True)[:3]
            d_parts = ", ".join(f"{r['ticker']}(D:{r.get('deger', r['overall']):.0f})" for r in top3_d)
            i_parts = ", ".join(f"{r['ticker']}(I:{r.get('ivme', 50):.0f})" for r in top3_i)
            context = f"Taranan {len(items)} hisseden DEGER: {d_parts}. IVME: {i_parts}.\n"
            for r in items:
                if r["ticker"].lower() in q.lower():
                    context += f"\n{r['ticker']} DETAY:\n{build_rich_context(r)}\n"; break
        cross_signals = cross_hunter.last_results[:5]
        if cross_signals:
            sig_parts = ", ".join(f"{s['ticker']}:{s['signal']}" for s in cross_signals)
            context += f"Sinyaller: {sig_parts}\n"
        prompt = f"Sen Q'sun — BistBull asistanı. Kurumsal, kısa, Türkçe. 3-5 cümle MAX.\n\n{context}Soru: {q}\n\nQ:"
        text = await asyncio.to_thread(ai_call, prompt, 300)
        result = {"answer": text or "Cevap oluşturulamadı.", "cached": False}
        agent_cache.set(q.strip().lower()[:100], result)
        return result
    except Exception as e: return {"answer": f"Hata: {str(e)}", "error": True}

# ================================================================
# HERO SUMMARY — B4 FIX
# ================================================================
@app.get("/api/hero-summary")
async def api_hero_summary():
    cached = hero_cache.get("hero")
    if cached is not None: return cached
    items = get_top10_items()
    macro_data = macro_cache.get("macro_all") or {}
    cross_data = cross_hunter.last_results or []
    bullish_count = sum(1 for r in items if r["overall"] >= 65)
    bearish_count = sum(1 for r in items if r["overall"] < 40)
    total = len(items)
    if bullish_count > total * 0.6: mode = "POZITIF"
    elif bearish_count > total * 0.4: mode = "RISKLI"
    elif bullish_count > bearish_count: mode = "TEMKINLI_POZITIF"
    else: mode = "NOTR"
    mode_color = {"POZITIF": "green", "TEMKINLI_POZITIF": "green", "NOTR": "yellow", "RISKLI": "red"}.get(mode, "yellow")
    mode_label = {"POZITIF": "Pozitif", "TEMKINLI_POZITIF": "Temkinli Pozitif", "NOTR": "Notr", "RISKLI": "Riskli"}.get(mode, "Notr")
    opp = risk_item = None; deger_leaders = []; ivme_leaders = []
    if items:
        by_d = sorted(items, key=lambda x: x.get("deger", x["overall"]), reverse=True)
        deger_leaders = [{"ticker": r["ticker"], "name": r["name"], "deger": r.get("deger", r["overall"]), "ivme": r.get("ivme", 50), "style": r["style"], "reason": r["positives"][0] if r["positives"] else ""} for r in by_d[:3]]
        by_i = sorted(items, key=lambda x: x.get("ivme", 50), reverse=True)
        ivme_leaders = [{"ticker": r["ticker"], "name": r["name"], "deger": r.get("deger", r["overall"]), "ivme": r.get("ivme", 50), "style": r["style"], "reason": r["positives"][0] if r["positives"] else ""} for r in by_i[:3]]
        worst = min(items, key=lambda x: x.get("deger", x["overall"]))
        risk_item = {"ticker": worst["ticker"], "name": worst["name"], "deger": worst.get("deger", worst["overall"]), "reason": worst["negatives"][0] if worst["negatives"] else ""}
        best = max(items, key=lambda x: x["scores"].get("value", 0) + x["scores"].get("growth", 0))
        opp = {"ticker": best["ticker"], "name": best["name"], "overall": best["overall"], "reason": best["positives"][0] if best["positives"] else ""}
    sec_map = defaultdict(lambda: {"total": 0, "count": 0})
    for r in items: s = r.get("sector") or "Diger"; sec_map[s]["total"] += r["overall"]; sec_map[s]["count"] += 1
    strong_sectors = sorted([(k, v["total"]/v["count"]) for k, v in sec_map.items() if v["count"] >= 2], key=lambda x: -x[1])[:3]
    weak_sectors = sorted([(k, v["total"]/v["count"]) for k, v in sec_map.items() if v["count"] >= 2], key=lambda x: x[1])[:2]
    watch = []
    if strong_sectors: watch.append(f"{strong_sectors[0][0]} sektörü güçlü")
    macro_items = macro_data.get("items", [])
    for mi in macro_items:
        if mi.get("key") == "VIX" and mi.get("change_pct", 0) > 3: watch.append("VIX yükseliyor")
        if mi.get("key") == "DXY" and mi.get("change_pct", 0) > 0.5: watch.append("DXY yükselişte")
    if cross_data: watch.append(f"{len(cross_data)} sinyal aktif")
    if not watch: watch = ["Piyasa sakin"]
    story = bot_says = None
    if AI_AVAILABLE and items:
        try:
            d_top = [f"{r['ticker']}(D:{r.get('deger',50):.0f})" for r in (deger_leaders or items[:3])]
            i_top = [f"{r['ticker']}(I:{r.get('ivme',50):.0f})" for r in (ivme_leaders or items[:3])]
            bot3 = [f"{r['ticker']}(D:{r.get('deger',r.get('overall',50)):.0f})" for r in sorted(items, key=lambda x: x.get("deger", x.get("overall",50)))[:3]]
            macro_str = ", ".join(f"{m['name']}:{m.get('change_pct',0):+.1f}%" for m in macro_items[:6])
            prompt = f"BistBull stratejist. Türkçe, somut.\nPiyasa: {mode_label}. {total} hisse, {bullish_count} pozitif.\nDEGER: {', '.join(d_top)}. IVME: {', '.join(i_top)}.\nZayif: {', '.join(bot3)}. Makro: {macro_str}\n{len(cross_data)} sinyal.\n\nHİKÂYE: 2 cümle\nYORUM: 2 cümle\nFIRSAT: 1 cümle"
            text = await asyncio.to_thread(ai_call, prompt, 300)
            if text:
                for line in text.split("\n"):
                    lu = line.strip().upper()
                    if any(lu.startswith(p) for p in ("HİKÂYE:", "HIKAYE:", "HİKAYE:")): story = line.split(":", 1)[1].strip()
                    elif lu.startswith("YORUM:"): bot_says = line[6:].strip()
                    elif lu.startswith("FIRSAT:") and opp: opp["ai_reason"] = line.split(":", 1)[1].strip()
                if not story: story = text[:200]
                if not bot_says: bot_says = text[200:400] if len(text) > 200 else None
        except Exception as e: log.warning(f"hero AI: {e}")
    result = {"mode": mode, "mode_label": mode_label, "mode_color": mode_color, "story": story or f"{total} hisse tarandı. {bullish_count} pozitif, {bearish_count} zayıf.", "opportunity": clean_for_json(opp), "risk": clean_for_json(risk_item), "deger_leaders": clean_for_json(deger_leaders), "ivme_leaders": clean_for_json(ivme_leaders), "bot_says": bot_says or f"Piyasa {mode_label.lower()} modda.", "watch": watch[:4], "strong_sectors": [{"name": s[0], "score": round(s[1], 1)} for s in strong_sectors], "weak_sectors": [{"name": s[0], "score": round(s[1], 1)} for s in weak_sectors], "stats": {"total": total, "bullish": bullish_count, "bearish": bearish_count, "signals": len(cross_data)}, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}
    hero_cache.set("hero", result)
    return result

# ================================================================
# LIVE STATS
# ================================================================
SYSTEM_START = dt.datetime.now(dt.timezone.utc)

@app.get("/api/live/stats")
async def api_live_stats():
    uptime = (dt.datetime.now(dt.timezone.utc) - SYSTEM_START).total_seconds()
    return {"scans_done": len(analysis_cache), "signals_total": len(cross_hunter.last_results), "macro_tracked": len((macro_cache.get("macro_all") or {}).get("items", [])), "cache_raw": len(raw_cache), "cache_tech": len(tech_cache), "uptime_hours": round(uptime/3600, 1), "last_scan": get_top10_asof().isoformat() if get_top10_asof() else None, "universe": len(UNIVERSE)}

# ================================================================
# BATCH
# ================================================================
@app.get("/api/batch/{tickers}")
async def api_batch(tickers: str):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()][:5]
    results = []
    for t in ticker_list:
        try:
            r = await asyncio.to_thread(analyze_symbol, normalize_symbol(t))
            results.append({"ticker": r["ticker"], "name": r["name"], "overall": r["overall"], "confidence": r["confidence"], "style": r["style"], "scores": r["scores"], "legendary": r["legendary"], "positives": r["positives"], "negatives": r["negatives"], "price": r["metrics"].get("price"), "pe": r["metrics"].get("pe"), "roe": r["metrics"].get("roe"), "revenue_growth": r["metrics"].get("revenue_growth"), "market_cap": r["metrics"].get("market_cap")})
        except Exception as e: results.append({"ticker": t, "error": str(e)})
    return {"items": clean_for_json(results)}

# ================================================================
# SERVE FRONTEND
# ================================================================
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_INDEX_HTML = os.path.join(_BASE_DIR, "index.html")
_LANDING_HTML = os.path.join(_BASE_DIR, "landing.html")

@app.get("/favicon.ico")
@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def _suppress_icon():
    return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
async def serve_landing():
    try:
        with open(_LANDING_HTML, "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())
    except FileNotFoundError: return await serve_terminal()

@app.get("/terminal", response_class=HTMLResponse)
async def serve_terminal():
    try:
        with open(_INDEX_HTML, "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())
    except FileNotFoundError: return HTMLResponse(content="<h1>BistBull Terminal</h1><p>index.html bulunamadi</p>", status_code=500)

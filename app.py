# ================================================================
# BIST TERMINAL — FastAPI Backend
# ================================================================
import os, io, logging, asyncio
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from concurrent.futures import ThreadPoolExecutor

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="BIST Terminal", version="1.0")
pool = ThreadPoolExecutor(max_workers=4)

# ================================================================
# API ENDPOINTS
# ================================================================
@app.get("/api/analyze/{ticker}")
async def api_analyze(ticker: str):
    try:
        r = await asyncio.get_event_loop().run_in_executor(pool, engine.analyze, ticker.upper())
        if not r: raise HTTPException(404, "Veri bulunamadi")
        m = r["metrics"]
        return {
            "ticker": r["ticker"], "name": r["name"],
            "sector": r["sector"], "industry": r["industry"],
            "currency": r["currency"],
            "price": r["price"], "market_cap": m.get("market_cap"),
            "overall": r["overall"], "style": r["style"],
            "scores": r["scores"],
            "piotroski": r["piotroski"], "altman": r["altman"],
            "pe": m.get("pe"), "pb": m.get("pb"), "ev_ebitda": m.get("ev_ebitda"),
            "roe": m.get("roe"), "roic": m.get("roic"),
            "gross_margin": m.get("gross_margin"), "operating_margin": m.get("operating_margin"),
            "net_margin": m.get("net_margin"), "debt_equity": m.get("debt_equity"),
            "revenue_growth": m.get("revenue_growth"), "eps_growth": m.get("eps_growth"),
            "fcf_yield": m.get("fcf_yield"), "dividend_yield": m.get("dividend_yield"),
            "current_ratio": m.get("current_ratio"), "interest_coverage": m.get("interest_coverage"),
            "net_debt_ebitda": m.get("net_debt_ebitda"), "graham_fv": m.get("graham_fv"),
            "margin_safety": m.get("margin_safety"),
        }
    except HTTPException: raise
    except Exception as e:
        log.warning(f"analyze {ticker}: {e}")
        raise HTTPException(500, str(e))

@app.get("/api/quantum/{ticker}")
async def api_quantum(ticker: str):
    try:
        r = await asyncio.get_event_loop().run_in_executor(pool, engine.quantum_score, ticker.upper())
        if not r: raise HTTPException(404, "Veri bulunamadi")
        return r
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/top10")
async def api_top10():
    try:
        results = await asyncio.get_event_loop().run_in_executor(pool, engine.scan_top10)
        return {"items": [{
            "ticker": r["ticker"], "name": r["name"], "sector": r["sector"],
            "price": r["price"], "style": r["style"],
            "quantum": r["quantum"], "fundamental": r["fundamental"],
            "momentum": r["momentum"], "flow": r["flow"],
            "signals": r["signals"],
            "scores": r["scores"],
        } for r in results]}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/chart/{ticker}")
async def api_chart(ticker: str):
    try:
        tech = await asyncio.get_event_loop().run_in_executor(pool, engine.compute_technical, ticker.upper())
        if not tech or "df" not in tech: raise HTTPException(404, "Veri yok")
        buf = await asyncio.get_event_loop().run_in_executor(pool, _render_chart, ticker.upper(), tech)
        if not buf: raise HTTPException(500, "Chart olusturulamadi")
        return StreamingResponse(buf, media_type="image/png")
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/ai/{ticker}")
async def api_ai(ticker: str):
    try:
        q = await asyncio.get_event_loop().run_in_executor(pool, engine.quantum_score, ticker.upper())
        if not q: raise HTTPException(404, "Veri yok")
        summary = await asyncio.get_event_loop().run_in_executor(pool, engine.ai_summary, ticker.upper(), q)
        return {"ticker": ticker.upper(), "summary": summary or "AI ozet alinamadi. OPENAI_KEY kontrol edin."}
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/techdetail/{ticker}")
async def api_techdetail(ticker: str):
    try:
        r = await asyncio.get_event_loop().run_in_executor(pool, engine.tech_detail, ticker.upper())
        if not r: raise HTTPException(404, "Veri yok")
        return r
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/health")
async def health():
    return {"status": "ok", "universe": len(engine.UNIVERSE)}

# ================================================================
# CHART RENDERER
# ================================================================
def _render_chart(ticker, tech):
    df = tech["df"].tail(130)
    if len(df) < 20: return None

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), height_ratios=[3, 1],
                                     gridspec_kw={"hspace": 0.05})
    fig.patch.set_facecolor("#0a0e17")
    for ax in [ax1, ax2]:
        ax.set_facecolor("#0a0e17")
        for s in ax.spines.values(): s.set_color("#1e2a3a")
        ax.tick_params(colors="#4a5568", labelsize=8)
        ax.grid(True, alpha=0.08, color="#4a5568")

    dates = df.index
    close = df["Close"]
    ax1.plot(dates, close, color="#3b82f6", linewidth=1.5, label="Fiyat")

    ma50 = close.rolling(50).mean()
    ax1.plot(dates, ma50, color="#f59e0b", linewidth=1, alpha=0.8, label="MA50")

    if tech.get("ma200"):
        full_close = tech["df"]["Close"]
        ma200 = full_close.rolling(200).mean().reindex(df.index)
        valid = ma200.dropna()
        if len(valid) > 5:
            ax1.plot(valid.index, valid, color="#ef4444", linewidth=1, alpha=0.8, label="MA200")

    if tech.get("high_52w"):
        ax1.axhline(y=tech["high_52w"], color="#22c55e", linestyle="--", alpha=0.3, linewidth=0.7)
    if tech.get("low_52w"):
        ax1.axhline(y=tech["low_52w"], color="#ef4444", linestyle="--", alpha=0.3, linewidth=0.7)

    score = tech.get("momentum_score", 50)
    rsi = tech.get("rsi")
    title = f"{engine.base(ticker)}  ₺{tech['price']:.2f}  |  Momentum: {score}/100"
    if rsi: title += f"  |  RSI: {rsi:.0f}"
    ax1.set_title(title, color="#e2e8f0", fontsize=11, fontweight="bold", pad=8)
    ax1.legend(loc="upper left", fontsize=7, facecolor="#0a0e17", edgecolor="#1e2a3a", labelcolor="#94a3b8")
    ax1.set_ylabel("")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

    colors = ["#22c55e" if c >= o else "#ef4444" for c, o in zip(df["Close"], df["Open"])]
    ax2.bar(dates, df["Volume"], color=colors, alpha=0.5, width=0.8)
    ax2.set_ylabel("")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="#0a0e17", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf

# ================================================================
# STATIC FILES + SPA FALLBACK
# ================================================================
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/{path:path}")
async def spa_fallback(path: str):
    if path.startswith("api/"): raise HTTPException(404)
    return FileResponse("static/index.html")

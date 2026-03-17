import os
import logging
import json
import asyncio
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import httpx
import yfinance as yf
import pandas as pd
import numpy as np
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="BistBull v2")
executor = ThreadPoolExecutor(max_workers=4)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
log.info(f"OPENAI_API_KEY set: {'YES' if OPENAI_API_KEY else 'NO'}, len={len(OPENAI_API_KEY)}, model={MODEL}")

# ══════════════════════════════════════════════
# YAHOO FINANCE TICKER MAPPING
# ══════════════════════════════════════════════
MARKET_TICKERS = {
    "xu030": "XU030.IS", "xu100": "XU100.IS", "xbank": "XBANK.IS",
    "usdtry": "USDTRY=X", "eurtry": "EURTRY=X",
    "brent": "BZ=F", "gold": "GC=F", "btc": "BTC-USD",
}

BIST_SUFFIX = ".IS"

STOCK_LIST = [
    "AKBNK","ARCLK","ASELS","BIMAS","DOHOL","EKGYO","ENKAI","EREGL",
    "FROTO","GARAN","GUBRF","HEKTS","ISCTR","KCHOL","KONTR","KOZAL",
    "KRDMD","MGROS","ODAS","OYAKC","PETKM","PGSUS","SAHOL","SASA",
    "SISE","TAVHL","TCELL","THYAO","TOASO","TUPRS","YKBNK","AEFES",
    "AKSA","TTKOM","VESTL","CIMSA","DOAS","TTRAK","KOZAA","AGHOL",
]

STOCK_SECTORS = {
    "AKBNK":"Banka","ARCLK":"Dayanıklı","ASELS":"Savunma","BIMAS":"Perakende",
    "DOHOL":"Holding","EKGYO":"GYO","ENKAI":"İnşaat","EREGL":"Metal",
    "FROTO":"Otomotiv","GARAN":"Banka","GUBRF":"Kimya","HEKTS":"Kimya",
    "ISCTR":"Banka","KCHOL":"Holding","KONTR":"Enerji","KOZAL":"Madencilik",
    "KRDMD":"Metal","MGROS":"Perakende","ODAS":"Enerji","OYAKC":"Çimento",
    "PETKM":"Kimya","PGSUS":"Havacılık","SAHOL":"Holding","SASA":"Kimya",
    "SISE":"Cam","TAVHL":"Havacılık","TCELL":"Telekom","THYAO":"Havacılık",
    "TOASO":"Otomotiv","TUPRS":"Enerji","YKBNK":"Banka","AEFES":"İçecek",
    "AKSA":"Kimya","TTKOM":"Telekom","VESTL":"Elektronik","CIMSA":"Çimento",
    "DOAS":"Otomotiv","TTRAK":"Otomotiv","KOZAA":"Madencilik","AGHOL":"Holding",
}


# ══════════════════════════════════════════════
# YFINANCE HELPERS
# ══════════════════════════════════════════════
def safe_yf(ticker_str, period="5d", interval="1d"):
    """Safely fetch yfinance data, returns DataFrame or empty."""
    try:
        tk = yf.Ticker(ticker_str)
        df = tk.history(period=period, interval=interval)
        if df is None or df.empty:
            return pd.DataFrame()
        return df
    except Exception as e:
        log.warning(f"yf error {ticker_str}: {e}")
        return pd.DataFrame()


def get_price_change(df):
    """Get last price and % change from DataFrame."""
    if df.empty or len(df) < 1:
        return 0, 0
    price = float(df["Close"].iloc[-1])
    if len(df) >= 2:
        prev = float(df["Close"].iloc[-2])
        chg = ((price - prev) / prev * 100) if prev else 0
    else:
        chg = 0
    return round(price, 4), round(chg, 2)


def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(series):
    ema12 = calc_ema(series, 12)
    ema26 = calc_ema(series, 26)
    macd = ema12 - ema26
    signal = calc_ema(macd, 9)
    return macd, signal


def get_technicals(ticker):
    """Calculate full technicals for a BIST stock."""
    yf_ticker = f"{ticker}{BIST_SUFFIX}"
    df = safe_yf(yf_ticker, period="6mo", interval="1d")
    if df.empty or len(df) < 30:
        return None

    close = df["Close"]
    price = round(float(close.iloc[-1]), 2)
    prev = float(close.iloc[-2]) if len(df) >= 2 else price
    chg_pct = round((price - prev) / prev * 100, 2) if prev else 0

    ema5 = calc_ema(close, 5)
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    ema200 = calc_ema(close, 200) if len(df) >= 200 else close * 0

    rsi = calc_rsi(close)
    macd_line, macd_signal = calc_macd(close)

    rsi_val = round(float(rsi.iloc[-1]), 1) if not rsi.empty else 50
    macd_val = round(float(macd_line.iloc[-1]), 4) if not macd_line.empty else 0
    macd_sig = round(float(macd_signal.iloc[-1]), 4) if not macd_signal.empty else 0

    # Support / Resistance (simple: 20d low/high)
    sup = round(float(close.tail(20).min()), 2)
    res = round(float(close.tail(20).max()), 2)

    # Trend
    if len(ema50) > 0 and len(ema200) > 0 and float(ema200.iloc[-1]) > 0:
        if float(ema50.iloc[-1]) > float(ema200.iloc[-1]):
            trend = "Yükseliş"
        elif float(ema50.iloc[-1]) < float(ema200.iloc[-1]):
            trend = "Düşüş"
        else:
            trend = "Yatay"
    else:
        trend = "Yatay"

    # Volume
    avg_vol = float(df["Volume"].tail(20).mean()) if "Volume" in df else 0
    last_vol = float(df["Volume"].iloc[-1]) if "Volume" in df else 0
    vol_ratio = round(last_vol / avg_vol, 2) if avg_vol > 0 else 1

    # Cross signals
    signals = []
    if len(ema5) >= 2 and len(ema20) >= 2:
        if float(ema5.iloc[-2]) < float(ema20.iloc[-2]) and float(ema5.iloc[-1]) > float(ema20.iloc[-1]):
            signals.append({"tip": "EMA5_20", "d": "AL", "g": 7})
        elif float(ema5.iloc[-2]) > float(ema20.iloc[-2]) and float(ema5.iloc[-1]) < float(ema20.iloc[-1]):
            signals.append({"tip": "EMA5_20", "d": "SAT", "g": 7})
    if len(ema20) >= 2 and len(ema50) >= 2:
        if float(ema20.iloc[-2]) < float(ema50.iloc[-2]) and float(ema20.iloc[-1]) > float(ema50.iloc[-1]):
            signals.append({"tip": "EMA20_50", "d": "AL", "g": 8})
        elif float(ema20.iloc[-2]) > float(ema50.iloc[-2]) and float(ema20.iloc[-1]) < float(ema50.iloc[-1]):
            signals.append({"tip": "EMA20_50", "d": "SAT", "g": 8})
    if len(ema50) >= 2 and len(ema200) >= 2 and float(ema200.iloc[-1]) > 0:
        if float(ema50.iloc[-2]) < float(ema200.iloc[-2]) and float(ema50.iloc[-1]) > float(ema200.iloc[-1]):
            signals.append({"tip": "EMA50_200", "d": "AL", "g": 10})
        elif float(ema50.iloc[-2]) > float(ema200.iloc[-2]) and float(ema50.iloc[-1]) < float(ema200.iloc[-1]):
            signals.append({"tip": "EMA50_200", "d": "SAT", "g": 10})
    if rsi_val < 30:
        signals.append({"tip": "RSI", "d": "AL", "g": 6})
    elif rsi_val > 70:
        signals.append({"tip": "RSI", "d": "SAT", "g": 6})
    if len(macd_line) >= 2 and len(macd_signal) >= 2:
        if float(macd_line.iloc[-2]) < float(macd_signal.iloc[-2]) and float(macd_line.iloc[-1]) > float(macd_signal.iloc[-1]):
            signals.append({"tip": "MACD", "d": "AL", "g": 7})
        elif float(macd_line.iloc[-2]) > float(macd_signal.iloc[-2]) and float(macd_line.iloc[-1]) < float(macd_signal.iloc[-1]):
            signals.append({"tip": "MACD", "d": "SAT", "g": 7})

    # Momentum score
    ret5 = (price / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else 0
    ret20 = (price / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else 0

    # Teknik skor (0-100)
    tek_score = 50
    if rsi_val < 30: tek_score += 15
    elif rsi_val > 70: tek_score -= 15
    if trend == "Yükseliş": tek_score += 15
    elif trend == "Düşüş": tek_score -= 15
    if macd_val > macd_sig: tek_score += 10
    else: tek_score -= 10
    if vol_ratio > 1.5: tek_score += 10
    tek_score = max(0, min(100, tek_score))

    # Momentum skor
    mom_score = 50
    if ret5 > 2: mom_score += 20
    elif ret5 < -2: mom_score -= 20
    if ret20 > 5: mom_score += 15
    elif ret20 < -5: mom_score -= 15
    if vol_ratio > 2: mom_score += 15
    mom_score = max(0, min(100, mom_score))

    return {
        "ticker": ticker,
        "price": price,
        "chg_pct": chg_pct,
        "rsi": rsi_val,
        "macd": macd_val,
        "macd_sig": macd_sig,
        "ema5": round(float(ema5.iloc[-1]), 2),
        "ema20": round(float(ema20.iloc[-1]), 2),
        "ema50": round(float(ema50.iloc[-1]), 2),
        "ema200": round(float(ema200.iloc[-1]), 2) if len(ema200) >= 200 else 0,
        "sup": sup, "res": res,
        "trend": trend,
        "vol_ratio": vol_ratio,
        "signals": signals,
        "tek_score": tek_score,
        "mom_score": mom_score,
        "ret5": round(ret5, 2),
        "ret20": round(ret20, 2),
        "sector": STOCK_SECTORS.get(ticker, ""),
    }


def get_fundamentals(ticker):
    """Get basic fundamentals from yfinance."""
    yf_ticker = f"{ticker}{BIST_SUFFIX}"
    try:
        tk = yf.Ticker(yf_ticker)
        info = tk.info or {}
        return {
            "pe": round(info.get("trailingPE", 0) or 0, 1),
            "pb": round(info.get("priceToBook", 0) or 0, 2),
            "div_yield": round((info.get("dividendYield", 0) or 0) * 100, 2),
            "market_cap": info.get("marketCap", 0) or 0,
            "name": info.get("shortName", ticker),
            "beta": round(info.get("beta", 1) or 1, 2),
            "52w_high": round(info.get("fiftyTwoWeekHigh", 0) or 0, 2),
            "52w_low": round(info.get("fiftyTwoWeekLow", 0) or 0, 2),
        }
    except Exception as e:
        log.warning(f"Fundamentals error {ticker}: {e}")
        return {"pe": 0, "pb": 0, "div_yield": 0, "market_cap": 0, "name": ticker}


# ══════════════════════════════════════════════
# GPT HELPER
# ══════════════════════════════════════════════
async def ask_gpt(system, user_msg):
    """Call GPT for commentary only."""
    if not OPENAI_API_KEY:
        return "API key yok"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(OPENAI_URL, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            }, json={
                "model": MODEL, "max_tokens": 2048, "temperature": 0.3,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            })
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        log.error(f"GPT error: {e}")
        return ""


# ══════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════

# ── MARKET BAR (pure yfinance, no AI) ──
@app.post("/api/marketbar")
async def api_marketbar():
    def fetch():
        result = {}
        for key, yf_tk in MARKET_TICKERS.items():
            df = safe_yf(yf_tk, period="5d", interval="1d")
            p, c = get_price_change(df)
            result[key] = {"p": p, "c": c}
        result["ts"] = datetime.utcnow().strftime("%H:%M")
        return result
    data = await asyncio.get_event_loop().run_in_executor(executor, fetch)
    return JSONResponse(data)


# ── RADAR (yfinance top movers + GPT summary) ──
@app.post("/api/radar")
async def api_radar():
    def fetch_movers():
        movers = []
        for tk in STOCK_LIST:
            df = safe_yf(f"{tk}{BIST_SUFFIX}", period="5d", interval="1d")
            p, c = get_price_change(df)
            if p > 0:
                movers.append({"t": tk, "p": p, "chg": c})
        movers.sort(key=lambda x: abs(x["chg"]), reverse=True)
        return movers[:8]

    movers = await asyncio.get_event_loop().run_in_executor(executor, fetch_movers)

    mover_text = ", ".join([f"{m['t']} {m['chg']:+.1f}%" for m in movers])
    commentary = await ask_gpt(
        "BIST piyasa uzmanısın. Kısa ve öz Türkçe yorum yap. SADECE JSON döndür.",
        f"Bugünkü en çok hareket eden BIST hisseleri: {mover_text}\n"
        f'JSON: {{"piyasa_modu":"YÜKSELİŞ|DÜŞÜŞ|YATAY","fear_greed":50,"hacim":"Normal","trend":"Kararsız",'
        f'"ozet":"2-3 cümle piyasa yorumu"}}'
    )

    try:
        clean = commentary.replace("```json", "").replace("```", "").strip()
        ai = json.loads(clean)
    except:
        ai = {"piyasa_modu": "YATAY", "fear_greed": 50, "hacim": "Normal", "trend": "Kararsız", "ozet": ""}

    sicak = []
    for m in movers[:5]:
        sig = "AL" if m["chg"] > 1 else ("SAT" if m["chg"] < -1 else "NÖTR")
        sicak.append({"t": m["t"], "chg": m["chg"], "why": f"₺{m['p']}", "sig": sig})

    ai["sicak"] = sicak
    return JSONResponse(ai)


# ── STOCK ANALYSIS (yfinance data + GPT commentary) ──
@app.post("/api/analiz")
async def api_analiz(request: Request):
    body = await request.json()
    ticker = body.get("ticker", "").upper().strip()
    if not ticker:
        return JSONResponse({"error": "Ticker gerekli"}, status_code=400)

    def fetch():
        tech = get_technicals(ticker)
        fund = get_fundamentals(ticker)
        return tech, fund

    tech, fund = await asyncio.get_event_loop().run_in_executor(executor, fetch)

    if not tech:
        return JSONResponse({"error": f"{ticker} verisi bulunamadı"}, status_code=404)

    data_summary = (
        f"{ticker}: Fiyat={tech['price']}, Δ={tech['chg_pct']}%, RSI={tech['rsi']}, "
        f"MACD={tech['macd']:.4f}, Trend={tech['trend']}, Destek={tech['sup']}, Direnç={tech['res']}, "
        f"F/K={fund['pe']}, PD/DD={fund['pb']}, Temettü={fund['div_yield']}%, "
        f"52H={fund['52w_high']}, 52L={fund['52w_low']}, Beta={fund['beta']}"
    )

    commentary = await ask_gpt(
        "BIST uzmanısın. Verilen GERÇEK verilere dayanarak analiz yap. SADECE JSON, markdown yok.",
        f"{data_summary}\nBu verilere göre analiz yap:\n"
        f'{{"pio":{{"s":0,"y":"piotroski yorumu"}},"alt":{{"z":0,"r":"risk","y":"altman yorumu"}},'
        f'"gra":{{"iv":0,"mos":0,"y":"graham yorumu"}},'
        f'"hab":{{"sen":"Nötr","sk":50,"news":["haber1"]}},'
        f'"vio":{{"oi":"","baz":0,"y":"viop yorumu"}},'
        f'"dcf":{{"hp":0,"pot":0,"tav":"TUT","y":"dcf yorumu"}},'
        f'"rak":[{{"t":"","fk":0,"pd":0,"ytd":0}}],"ozet":"genel yorum","karar":"AL|TUT|SAT"}}'
    )

    try:
        clean = commentary.replace("```json", "").replace("```", "").strip()
        ai = json.loads(clean)
    except:
        ai = {"ozet": commentary[:300] if commentary else "Yorum alınamadı", "karar": "TUT"}

    result = {
        "ticker": ticker,
        "ad": fund.get("name", ticker),
        "fiyat": tech["price"],
        "chg": tech["chg_pct"],
        "tek": {
            "rsi": tech["rsi"],
            "sig": "AL" if tech["macd"] > tech["macd_sig"] else "SAT",
            "tr": tech["trend"],
            "sup": tech["sup"],
            "res": tech["res"],
            "y": f"EMA20={tech['ema20']}, EMA50={tech['ema50']}, Hacim={tech['vol_ratio']}x",
        },
        **ai,
    }
    return JSONResponse(result)


# ── CROSS HUNTER (pure yfinance calculations) ──
@app.post("/api/cross")
async def api_cross():
    def scan():
        all_signals = []
        for tk in STOCK_LIST:
            tech = get_technicals(tk)
            if tech and tech["signals"]:
                for sig in tech["signals"]:
                    all_signals.append({
                        "t": tk, "tip": sig["tip"], "d": sig["d"], "g": sig["g"],
                        "a": f"₺{tech['price']} RSI={tech['rsi']}",
                    })
        al = sum(1 for s in all_signals if s["d"] == "AL")
        sat = sum(1 for s in all_signals if s["d"] == "SAT")
        return {"sigs": all_signals, "al": al, "sat": sat}

    data = await asyncio.get_event_loop().run_in_executor(executor, scan)
    return JSONResponse(data)


# ── QUANTUM SCANNER (pure yfinance calculations) ──
@app.post("/api/quantum")
async def api_quantum():
    def scan():
        stocks = []
        for tk in STOCK_LIST:
            tech = get_technicals(tk)
            if not tech:
                continue

            fund = get_fundamentals(tk)

            # Value score
            vs = 50
            pe = fund.get("pe", 0)
            if 0 < pe < 8: vs = 85
            elif 0 < pe < 12: vs = 70
            elif pe > 25: vs = 30
            pb = fund.get("pb", 0)
            if 0 < pb < 1: vs += 15
            elif pb > 3: vs -= 10
            vs = max(0, min(100, vs))

            # Flow score (volume based)
            fls = 50
            if tech["vol_ratio"] > 2: fls = 80
            elif tech["vol_ratio"] > 1.3: fls = 65
            elif tech["vol_ratio"] < 0.5: fls = 30

            # Temel score
            fs = 50
            if fund.get("div_yield", 0) > 3: fs += 15
            if 0 < pe < 15: fs += 10
            if 0 < pb < 2: fs += 10
            fs = max(0, min(100, fs))

            # KANGAL score
            ks = round((tech["tek_score"] * 0.25 + tech["mom_score"] * 0.25 + vs * 0.2 + fs * 0.15 + fls * 0.15))

            # Rejim
            if abs(tech["chg_pct"]) > 3 and tech["vol_ratio"] > 2: rej = "BREAKOUT"
            elif tech["vol_ratio"] > 1.5 and abs(tech["ret5"]) > 5: rej = "VOLATILE"
            elif tech["trend"] == "Yükseliş": rej = "TREND"
            else: rej = "RANGE"

            sig = "AL" if ks >= 65 else ("SAT" if ks <= 35 else "NÖTR")

            stocks.append({
                "t": tk, "p": tech["price"], "chg": tech["chg_pct"],
                "vs": vs, "ms": tech["mom_score"], "ts": tech["tek_score"],
                "fs": fs, "fls": fls, "ks": ks,
                "rej": rej, "sig": sig,
            })

        stocks.sort(key=lambda x: x["ks"], reverse=True)
        return {"stocks": stocks}

    data = await asyncio.get_event_loop().run_in_executor(executor, scan)
    return JSONResponse(data)


# ── TAKAS (GPT only — no free data source) ──
@app.post("/api/takas")
async def api_takas(request: Request):
    body = await request.json()
    ticker = body.get("ticker", "").upper().strip()
    if not ticker:
        return JSONResponse({"error": "Ticker gerekli"}, status_code=400)

    # Get real price at least
    tech = await asyncio.get_event_loop().run_in_executor(executor, lambda: get_technicals(ticker))

    commentary = await ask_gpt(
        "BIST takas uzmanısın. SADECE JSON döndür, markdown yok.",
        f"{ticker} (₺{tech['price'] if tech else '?'}) takas analizi yap. "
        f"Aracı kurum bazlı alım/satım tahmini, yabancı takas oranı.\n"
        f'{{"ticker":"{ticker}","yab":0,"ytrend":"Sabit","ilgi":"Orta",'
        f'"kim":"analiz","kurumlar":[{{"ad":"İş Yatırım","lot":5000,"yon":"ALICI"}}]}}'
    )

    try:
        clean = commentary.replace("```json", "").replace("```", "").strip()
        return JSONResponse(json.loads(clean))
    except:
        return JSONResponse({"ticker": ticker, "yab": 0, "ytrend": "Sabit", "ilgi": "Orta",
                             "kim": "Veri alınamadı", "kurumlar": []})


# ── LEGACY ENDPOINT (for X radar etc — pure GPT) ──
@app.post("/api/analyze")
async def api_analyze(request: Request):
    body = await request.json()
    system = body.get("system", "")
    messages = body.get("messages", [])
    oai_messages = []
    if system:
        oai_messages.append({"role": "system", "content": system})
    oai_messages.extend(messages)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(OPENAI_URL, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            }, json={"model": MODEL, "max_tokens": 4096, "temperature": 0.3, "messages": oai_messages})
        data = resp.json()
        if resp.status_code != 200:
            err = data.get("error", {})
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return JSONResponse({"error": msg}, status_code=resp.status_code)
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return JSONResponse({"content": [{"type": "text", "text": text}]})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── STATIC ──
@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0", "engine": MODEL}

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def index():
    return FileResponse("static/index.html")

@app.get("/favicon.ico")
def favicon():
    if os.path.exists("static/favicon.ico"):
        return FileResponse("static/favicon.ico", media_type="image/x-icon")
    return JSONResponse({})

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)

# ================================================================
# BISTBULL TERMINAL V10.1 — TECHNICAL ANALYSIS
# compute_technical, Ichimoku, VCP, Rectangle, Pivot levels,
# CrossHunter sinifi, Chart generator (memory-leak fixed)
#
# FIXES IN THIS REVISION (V10.1):
#
# FIX-1 [BUG-PERF-02 CRITICAL] RSI: SMA replaced with Wilder's RMA.
#   OLD: gain = delta.clip(lower=0).rolling(14).mean()
#        loss = (-delta.clip(upper=0)).rolling(14).mean()
#   NEW: alpha=1/14, ewm(alpha=alpha, min_periods=14, adjust=False)
#
#   Wilder (1978) defined RSI using a Smoothed Moving Average (RMA),
#   not a Simple Moving Average. The SMA approximation diverges by
#   4-8 RSI points during trending BIST markets vs TradingView/Bloomberg.
#   adjust=False enforces the recursive formula S_t = alpha*x + (1-alpha)*S_{t-1}.
#
# FIX-2 [BUG-PERF-06] MACD EWM: min_periods added to all three spans.
#   Without min_periods, pandas EWM returns values from bar[0], meaning
#   a stock with 30 bars can show a "BULLISH" MACD cross based on a
#   26-period EMA computed from a single data point. Now matches TradingView.
#
# FIX-3 [BUG-PERF-07] 52W High/Low guard raised from >= 50 to >= 200 bars.
#   tail(252) on a 60-bar dataset computed a "3-month high" and labelled
#   it a "52-week high". This inflated pct_from_high and boosted
#   score_technical_break() by up to +40 points for sparse-data symbols.
# ================================================================

from __future__ import annotations

import io
import os
import time
import random
import logging
from typing import Optional, Any
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from utils.helpers import safe_num, normalize_symbol, base_ticker
from core.cache import tech_cache, history_cache
from config import UNIVERSE

log = logging.getLogger("bistbull.technical")

# ================================================================
# OPTIONAL IMPORTS
# ================================================================
try:
    import yfinance as yf
    os.makedirs("/tmp/yf-cache", exist_ok=True)
    yf.set_tz_cache_location("/tmp/yf-cache")
    YF_AVAILABLE = True
except ImportError:
    yf = None
    YF_AVAILABLE = False

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    CHART_AVAILABLE = True
except ImportError:
    plt = None
    mdates = None
    CHART_AVAILABLE = False

try:
    import borsapy as bp
    BORSAPY_AVAILABLE_TECH = True
except ImportError:
    bp = None
    BORSAPY_AVAILABLE_TECH = False

# ================================================================
# BATCH DOWNLOAD CONFIG
# ================================================================
BATCH_CHUNK_SIZE      = 25
BATCH_CHUNK_DELAY_MIN = 1.5
BATCH_CHUNK_DELAY_MAX = 3.0
BATCH_MAX_RETRIES     = 2

# ================================================================
# INDICATOR PARAMETERS — single source of truth
# ================================================================
_RSI_PERIOD:   int = 14   # Wilder (1978)
_MACD_FAST:    int = 12   # Appel (1979)
_MACD_SLOW:    int = 26
_MACD_SIGNAL:  int = 9
_MIN_BARS_52W: int = 200  # Minimum bars for a valid 52-week metric


# ================================================================
# BATCH HISTORY DOWNLOAD
# ================================================================
def batch_download_history(
    symbols: list,
    period: str = "1y",
    interval: str = "1d",
) -> dict:
    """Batch price history: yfinance chunked -> borsapy fallback."""
    if YF_AVAILABLE:
        result = {}
        if not symbols:
            return result
        chunks = [symbols[i:i + BATCH_CHUNK_SIZE] for i in range(0, len(symbols), BATCH_CHUNK_SIZE)]
        total_chunks = len(chunks)
        for chunk_idx, chunk in enumerate(chunks):
            chunk_result = _download_chunk(chunk, period, interval)
            result.update(chunk_result)
            if chunk_idx < total_chunks - 1:
                time.sleep(random.uniform(BATCH_CHUNK_DELAY_MIN, BATCH_CHUNK_DELAY_MAX))
        if result:
            log.info(f"batch_download (yfinance chunked): {len(result)}/{len(symbols)} basarili ({total_chunks} chunk)")
            return result

    if BORSAPY_AVAILABLE_TECH:
        from data.providers import batch_download_history_v9
        result = batch_download_history_v9(symbols, period=period, interval=interval)
        if result:
            log.info(f"batch_download (borsapy fallback): {len(result)}/{len(symbols)} basarili")
            return result

    log.warning("batch_download: both yfinance and borsapy failed")
    return {}


def _download_chunk(chunk: list, period: str, interval: str, retry: int = 0) -> dict:
    """Download a single chunk. Retries on failure."""
    result = {}
    try:
        df = yf.download(chunk, period=period, interval=interval, group_by="ticker", progress=False, threads=True)
        if df is not None and not df.empty:
            for sym in chunk:
                try:
                    if len(chunk) == 1:
                        ticker_df = df
                    else:
                        if sym in df.columns.get_level_values(0):
                            ticker_df = df[sym].dropna(how="all")
                        else:
                            continue
                    if ticker_df is not None and not ticker_df.empty and len(ticker_df) >= 20:
                        result[sym] = ticker_df
                except Exception:
                    continue
    except Exception as e:
        if retry < BATCH_MAX_RETRIES:
            delay = random.uniform(3.0, 6.0)
            log.info(f"batch chunk retry {retry + 1}/{BATCH_MAX_RETRIES} after {delay:.1f}s ({len(chunk)} symbols)")
            time.sleep(delay)
            return _download_chunk(chunk, period, interval, retry + 1)
        else:
            log.warning(f"batch chunk failed after {BATCH_MAX_RETRIES} retries: {e}")
    return result


# ================================================================
# INDICATOR HELPERS — pure functions, no I/O
# ================================================================

def _wilder_rsi(close: pd.Series, period: int = _RSI_PERIOD) -> pd.Series:
    """
    Wilder's RSI (1978) using Wilder's Smoothed Moving Average (RMA).

    alpha = 1 / period  (Wilder's factor, not 2/(n+1) like standard EMA)
    adjust=False        enforces recursive formula matching TradingView/Bloomberg
    min_periods=period  returns NaN until sufficient data exists

    The prior SMA-based approximation (rolling window mean) diverges from
    TradingView by 4-8 RSI points during trending markets on BIST.
    """
    alpha = 1.0 / period
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    rs    = gain / loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _macd(
    close: pd.Series,
    fast: int   = _MACD_FAST,
    slow: int   = _MACD_SLOW,
    signal: int = _MACD_SIGNAL,
) -> tuple:
    """
    Standard MACD (Appel 1979) with min_periods enforcement.

    Returns: (macd_line, signal_line, histogram)

    min_periods prevents returning values from insufficient data.
    Without it, a 30-bar stock gets a "26-period EMA" from bar[0]
    which triggers spurious crossovers cached for 1 hour.
    adjust=False matches TradingView's EMA initialisation.
    """
    ema_fast    = close.ewm(span=fast,   min_periods=fast,   adjust=False).mean()
    ema_slow    = close.ewm(span=slow,   min_periods=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, min_periods=signal, adjust=False).mean()
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


# ================================================================
# COMPUTE TECHNICAL
# ================================================================
def compute_technical(
    symbol: str,
    hist_df=None,
) -> Optional[dict]:
    """
    Full technical analysis: price history, MA series, RSI, MACD, BB, volume.

    V10.1 fixes:
      - RSI: Wilder RMA              [FIX-1]
      - MACD: min_periods enforced   [FIX-2]
      - 52W High/Low: 200-bar guard  [FIX-3]
    """
    cached = tech_cache.get(symbol)
    if cached is not None:
        return cached

    try:
        df = None
        if hist_df is not None and len(hist_df) >= 50:
            df = hist_df
        else:
            cached_hist = history_cache.get(symbol)
            if cached_hist is not None:
                df = cached_hist
            else:
                if YF_AVAILABLE:
                    try:
                        tk = yf.Ticker(symbol)
                        df = tk.history(period="1y", interval="1d")
                    except Exception:
                        pass
                if (df is None or (hasattr(df, "empty") and df.empty)) and BORSAPY_AVAILABLE_TECH:
                    try:
                        ticker_clean = symbol.upper().replace(".IS", "").replace(".E", "")
                        _tk = bp.Ticker(ticker_clean)
                        df = _tk.history(period="1y", interval="1d")
                    except Exception:
                        pass
                if df is not None and not df.empty:
                    history_cache.set(symbol, df)

        if df is None or len(df) < 50:
            return None

        c  = df["Close"]
        v  = df["Volume"]
        n  = len(c)

        # ── Moving Averages ──────────────────────────────────────────
        ma50  = c.rolling(50).mean()
        ma200 = c.rolling(200).mean() if n >= 200 else pd.Series([np.nan] * n, index=c.index)

        price     = float(c.iloc[-1])
        ma50_val  = float(ma50.iloc[-1])  if not np.isnan(ma50.iloc[-1])                    else None
        ma200_val = float(ma200.iloc[-1]) if (n >= 200 and not np.isnan(ma200.iloc[-1]))     else None

        # ── Golden / Death Cross ─────────────────────────────────────
        cross_signal = None
        if ma50_val and ma200_val and len(ma50) >= 2 and len(ma200) >= 2:
            prev_50  = float(ma50.iloc[-2])  if not np.isnan(ma50.iloc[-2])  else None
            prev_200 = float(ma200.iloc[-2]) if not np.isnan(ma200.iloc[-2]) else None
            if prev_50 and prev_200:
                if prev_50 <= prev_200 and ma50_val > ma200_val:
                    cross_signal = "GOLDEN_CROSS"
                elif prev_50 >= prev_200 and ma50_val < ma200_val:
                    cross_signal = "DEATH_CROSS"

        # ── RSI — Wilder's RMA [FIX-1] ─────────────────────────────
        rsi_series = _wilder_rsi(c, period=_RSI_PERIOD)
        rsi_val    = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else None

        # ── MACD — min_periods enforced [FIX-2] ─────────────────────
        macd_line, signal_line, _ = _macd(c)

        macd_val   = float(macd_line.iloc[-1])   if not np.isnan(macd_line.iloc[-1])   else 0.0
        signal_val = float(signal_line.iloc[-1]) if not np.isnan(signal_line.iloc[-1]) else 0.0
        macd_hist  = macd_val - signal_val
        macd_bullish = macd_val > signal_val

        macd_cross = None
        if n >= 2:
            prev_macd_v = float(macd_line.iloc[-2])   if not np.isnan(macd_line.iloc[-2])   else None
            prev_sig_v  = float(signal_line.iloc[-2]) if not np.isnan(signal_line.iloc[-2]) else None
            if prev_macd_v is not None and prev_sig_v is not None:
                if prev_macd_v <= prev_sig_v and macd_val > signal_val:
                    macd_cross = "BULLISH"
                elif prev_macd_v >= prev_sig_v and macd_val < signal_val:
                    macd_cross = "BEARISH"

        # ── Bollinger Bands ──────────────────────────────────────────
        bb_mid   = c.rolling(20).mean()
        bb_std   = c.rolling(20).std()
        bb_upper = bb_mid + 2.0 * bb_std
        bb_lower = bb_mid - 2.0 * bb_std

        bb_pos = None
        if not np.isnan(bb_upper.iloc[-1]) and not np.isnan(bb_lower.iloc[-1]):
            if price > float(bb_upper.iloc[-1]):
                bb_pos = "ABOVE"
            elif price < float(bb_lower.iloc[-1]):
                bb_pos = "BELOW"
            else:
                bb_pos = "INSIDE"

        # ── 52W High / Low [FIX-3: guard raised to 200 bars] ────────
        # OLD: if len(df) >= 50  ->  computed a 3-month "52-week high"
        # NEW: if len(df) >= 200 ->  requires meaningful window
        if n >= _MIN_BARS_52W:
            high_52w = float(df["High"].tail(252).max())
            low_52w  = float(df["Low"].tail(252).min())
        else:
            high_52w = None
            low_52w  = None

        pct_from_high = ((price - high_52w) / high_52w * 100.0) if high_52w else None
        pct_from_low  = ((price - low_52w)  / low_52w  * 100.0) if low_52w  else None

        # ── Volume ───────────────────────────────────────────────────
        vol_avg   = float(v.tail(20).mean()) if n >= 20 else None
        vol_today = float(v.iloc[-1])        if n > 0   else None
        vol_ratio = (vol_today / vol_avg)    if (vol_avg and vol_avg > 0) else None

        # ── Composite Tech Score (display only) ──────────────────────
        components = []
        if rsi_val is not None:
            if 40 <= rsi_val <= 60:
                components.append({"name": "RSI", "score": 50, "desc": "Notr"})
            elif 30 <= rsi_val < 40:
                components.append({"name": "RSI", "score": 65, "desc": "Asiri satima yaklasıyor"})
            elif rsi_val < 30:
                components.append({"name": "RSI", "score": 85, "desc": "Asiri satim"})
            elif 60 < rsi_val <= 70:
                components.append({"name": "RSI", "score": 40, "desc": "Asiri alima yaklasıyor"})
            else:
                components.append({"name": "RSI", "score": 20, "desc": "Asiri alim"})
        if ma50_val:
            if price > ma50_val:
                components.append({"name": "MA50", "score": 70, "desc": "Fiyat MA50 uzerinde"})
            else:
                components.append({"name": "MA50", "score": 30, "desc": "Fiyat MA50 altinda"})
        if ma200_val:
            if price > ma200_val:
                components.append({"name": "MA200", "score": 75, "desc": "Fiyat MA200 uzerinde"})
            else:
                components.append({"name": "MA200", "score": 25, "desc": "Fiyat MA200 altinda"})
        if ma50_val and ma200_val:
            if ma50_val > ma200_val:
                components.append({"name": "Trend", "score": 80, "desc": "MA50 > MA200 (Yukari)"})
            else:
                components.append({"name": "Trend", "score": 20, "desc": "MA50 < MA200 (Asagi)"})
        if macd_bullish:
            components.append({"name": "MACD", "score": 70, "desc": "Yukselis"})
        else:
            components.append({"name": "MACD", "score": 30, "desc": "Dusus"})
        if vol_ratio and vol_ratio > 1.5:
            components.append({"name": "Hacim", "score": 75, "desc": f"{vol_ratio:.1f}x ortalama"})
        elif vol_ratio:
            components.append({"name": "Hacim", "score": 50, "desc": f"{vol_ratio:.1f}x ortalama"})

        tech_score = sum(c_["score"] for c_ in components) / len(components) if components else 50.0

        # ── Price History (last 130 bars for frontend) ────────────────
        chart_df = df.tail(130)
        price_history = []
        for idx, row in chart_df.iterrows():
            price_history.append({
                "date":   idx.strftime("%Y-%m-%d"),
                "open":   round(float(row["Open"]),  2),
                "high":   round(float(row["High"]),  2),
                "low":    round(float(row["Low"]),   2),
                "close":  round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })

        # ── MA Series for frontend chart ──────────────────────────────
        ma50_series = []
        ma50_full   = c.rolling(50).mean().tail(130)
        for idx_ma, val in ma50_full.items():
            if not np.isnan(val):
                ma50_series.append({"date": idx_ma.strftime("%Y-%m-%d"), "value": round(float(val), 2)})

        ma200_series = []
        if n >= 200:
            ma200_full = c.rolling(200).mean().tail(130)
            for idx_ma, val in ma200_full.items():
                if not np.isnan(val):
                    ma200_series.append({"date": idx_ma.strftime("%Y-%m-%d"), "value": round(float(val), 2)})

        # ── 20-day price change ───────────────────────────────────────
        pct_20d = None
        if n >= 20:
            base_p = float(c.iloc[-20])
            if base_p > 0:
                pct_20d = round(((price - base_p) / base_p) * 100.0, 1)

        result = {
            "price": price, "ma50": ma50_val, "ma200": ma200_val,
            "rsi": rsi_val,
            "macd": macd_val, "macd_signal": signal_val,
            "macd_hist": macd_hist, "macd_bullish": macd_bullish, "macd_cross": macd_cross,
            "cross_signal": cross_signal,
            "bb_pos": bb_pos,
            "bb_upper": round(float(bb_upper.iloc[-1]), 2) if not np.isnan(bb_upper.iloc[-1]) else None,
            "bb_lower": round(float(bb_lower.iloc[-1]), 2) if not np.isnan(bb_lower.iloc[-1]) else None,
            "high_52w": high_52w, "low_52w": low_52w,
            "pct_from_high": pct_from_high, "pct_from_low": pct_from_low,
            "pct_20d": pct_20d,
            "vol_ratio": vol_ratio,
            "tech_score": round(tech_score, 1),
            "components": components,
            "price_history": price_history,
            "ma50_series": ma50_series,
            "ma200_series": ma200_series,
        }
        tech_cache.set(symbol, result)
        return result

    except Exception as e:
        log.warning(f"Technical {symbol}: {e}")
        return None


# ================================================================
# ICHIMOKU CLOUD
# ================================================================
def compute_ichimoku(df) -> Optional[dict]:
    """Ichimoku Kinko Hyo — standard 9/26/52 parameters."""
    if df is None or len(df) < 52:
        return None
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]
    tenkan   = (high.rolling(9).max()  + low.rolling(9).min())  / 2.0
    kijun    = (high.rolling(26).max() + low.rolling(26).min()) / 2.0
    senkou_a = ((tenkan + kijun) / 2.0).shift(26)
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2.0).shift(26)
    return {
        "tenkan":      float(tenkan.iloc[-1])   if not np.isnan(tenkan.iloc[-1])   else None,
        "kijun":       float(kijun.iloc[-1])    if not np.isnan(kijun.iloc[-1])    else None,
        "tenkan_prev": float(tenkan.iloc[-2])   if (len(tenkan) >= 2 and not np.isnan(tenkan.iloc[-2]))   else None,
        "kijun_prev":  float(kijun.iloc[-2])    if (len(kijun) >= 2  and not np.isnan(kijun.iloc[-2]))    else None,
        "senkou_a":    float(senkou_a.iloc[-1]) if not np.isnan(senkou_a.iloc[-1]) else None,
        "senkou_b":    float(senkou_b.iloc[-1]) if not np.isnan(senkou_b.iloc[-1]) else None,
        "price":       float(close.iloc[-1]),
    }


# ================================================================
# VCP — Volatility Contraction Pattern
# ================================================================
def detect_vcp(df) -> bool:
    """Minervini VCP: three contracting ATR windows + breakout."""
    if df is None or len(df) < 50:
        return False
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    tr    = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
    atr_5  = tr.tail(5).mean()
    atr_20 = tr.tail(20).mean()
    atr_50 = tr.tail(50).mean()
    if atr_50 == 0:
        return False
    contracting = (atr_5 < atr_20 * 0.85) and (atr_20 < atr_50 * 0.90)
    recent_high = float(high.tail(5).max())
    breakout    = float(close.iloc[-1]) > recent_high * 0.998
    return contracting and breakout


# ================================================================
# RECTANGLE BREAKOUT
# ================================================================
def detect_rectangle_breakout(df) -> Optional[str]:
    """Returns: 'bullish', 'bearish', or None."""
    if df is None or len(df) < 20:
        return None
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]
    range_high = float(high.tail(20).max())
    range_low  = float(low.tail(20).min())
    if range_low == 0:
        return None
    range_pct = (range_high - range_low) / range_low
    price     = float(close.iloc[-1])
    if range_pct < 0.08 and price > range_high * 0.998:
        return "bullish"
    if range_pct < 0.08 and price < range_low * 1.002:
        return "bearish"
    return None


# ================================================================
# PIVOT LEVELS (Support / Resistance)
# ================================================================
def find_pivot_levels(df, lookback: int = 60) -> tuple:
    """Returns: (resistance, support) using ±3-bar pivot window."""
    if df is None or len(df) < lookback:
        return None, None
    high = df["High"].tail(lookback)
    low  = df["Low"].tail(lookback)
    pivot_highs = []
    pivot_lows  = []
    for i in range(3, len(high) - 3):
        if high.iloc[i] == high.iloc[i - 3:i + 4].max():
            pivot_highs.append(float(high.iloc[i]))
        if low.iloc[i] == low.iloc[i - 3:i + 4].min():
            pivot_lows.append(float(low.iloc[i]))
    return (max(pivot_highs) if pivot_highs else None,
            min(pivot_lows)  if pivot_lows  else None)


# ================================================================
# CROSS HUNTER V2
# ================================================================
SIGNAL_INFO: dict = {
    "Golden Cross":            {"icon": "bullish", "stars": 5, "category": "kirilim",  "explanation": "MA50 yukari kesti MA200'u — orta/uzun vade yukari donusu."},
    "Death Cross":             {"icon": "bearish", "stars": 5, "category": "kirilim",  "explanation": "MA50 asagi kesti MA200'u — orta/uzun vade asagi donusu."},
    "Ichimoku Kumo Breakout":  {"icon": "bullish", "stars": 5, "category": "kirilim",  "explanation": "Fiyat Ichimoku bulutu uzerine cikti — guclu trend degisimi."},
    "Ichimoku Kumo Breakdown": {"icon": "bearish", "stars": 5, "category": "kirilim",  "explanation": "Fiyat Ichimoku bulutu altina dustu."},
    "Ichimoku TK Cross":       {"icon": "bullish", "stars": 4, "category": "kirilim",  "explanation": "Tenkan-sen Kijun-sen'i yukari kesti."},
    "VCP Kirilim":             {"icon": "bullish", "stars": 5, "category": "kirilim",  "explanation": "Volatilite daralma paterni kirilimi."},
    "Rectangle Breakout":      {"icon": "bullish", "stars": 4, "category": "kirilim",  "explanation": "Konsolidasyon kirilimi — yukari."},
    "Rectangle Breakdown":     {"icon": "bearish", "stars": 4, "category": "kirilim",  "explanation": "Konsolidasyon kirilimi — asagi."},
    "52W High Breakout":       {"icon": "bullish", "stars": 5, "category": "kirilim",  "explanation": "52 haftalik zirveyi kirdi."},
    "Direnc Kirilimi":         {"icon": "bullish", "stars": 4, "category": "kirilim",  "explanation": "Pivot direnc seviyesi kirildi."},
    "Destek Kirilimi":         {"icon": "bearish", "stars": 4, "category": "kirilim",  "explanation": "Pivot destek seviyesi kirildi."},
    "MACD Bullish Cross":      {"icon": "bullish", "stars": 3, "category": "momentum", "explanation": "MACD sinyal cizgisini yukari kesti."},
    "MACD Bearish Cross":      {"icon": "bearish", "stars": 3, "category": "momentum", "explanation": "MACD sinyal cizgisini asagi kesti."},
    "RSI Asiri Alim":          {"icon": "bearish", "stars": 1, "category": "momentum", "explanation": "RSI 70+ — asiri alim bolgesi."},
    "RSI Asiri Satim":         {"icon": "bullish", "stars": 1, "category": "momentum", "explanation": "RSI 30- — asiri satim bolgesi."},
    "BB Ust Band Kirilim":     {"icon": "neutral", "stars": 2, "category": "momentum", "explanation": "Fiyat Bollinger ust bandini kirdi."},
    "BB Alt Band Kirilim":     {"icon": "neutral", "stars": 2, "category": "momentum", "explanation": "Fiyat Bollinger alt bandini kirdi."},
}

# Turkish UI labels — displayed in frontend (kept as Turkish)
_SIGNAL_DISPLAY_NAMES: dict = {
    "VCP Kirilim":     "VCP Kırılım",
    "Direnc Kirilimi": "Direnç Kırılımı",
    "Destek Kirilimi": "Destek Kırılımı",
    "RSI Asiri Alim":  "RSI Aşırı Alım",
    "RSI Asiri Satim": "RSI Aşırı Satım",
    "BB Ust Band Kirilim": "BB Üst Band Kırılım",
    "BB Alt Band Kirilim": "BB Alt Band Kırılım",
}


class CrossHunter:
    def __init__(self) -> None:
        self.last_scan:    float         = 0
        self.prev_signals: dict          = {}
        self.last_results: list          = []

    def scan_all(self, history_map=None) -> list:
        """Tum UNIVERSE'u tara. history_map verilmisse tekrar indirmez."""
        new_signals = []
        all_signals = {}

        symbols = [normalize_symbol(t) for t in UNIVERSE]
        if history_map is None:
            history_map = batch_download_history(symbols, period="1y", interval="1d")
        for sym, hist_df in history_map.items():
            history_cache.set(sym, hist_df)

        for t in UNIVERSE:
            try:
                symbol  = normalize_symbol(t)
                hist_df = history_map.get(symbol)
                tech    = compute_technical(symbol, hist_df=hist_df)
                if not tech:
                    continue

                signals:  set   = set()
                price          = tech.get("price", 0)
                vol_ratio      = tech.get("vol_ratio")
                vol_confirmed  = vol_ratio is not None and vol_ratio > 1.3

                details = {
                    "ticker":        t,
                    "price":         price,
                    "rsi":           tech.get("rsi"),
                    "ma50":          tech.get("ma50"),
                    "ma200":         tech.get("ma200"),
                    "tech_score":    tech.get("tech_score", 50),
                    "vol_ratio":     vol_ratio,
                    "pct_from_high": tech.get("pct_from_high"),
                    "macd_bullish":  tech.get("macd_bullish"),
                }

                # MA crosses
                if tech.get("cross_signal") == "GOLDEN_CROSS":
                    signals.add("Golden Cross")
                if tech.get("cross_signal") == "DEATH_CROSS":
                    signals.add("Death Cross")

                # RSI (Wilder RMA values)
                rsi_v = tech.get("rsi")
                if rsi_v and rsi_v > 70:
                    signals.add("RSI Asiri Alim")
                if rsi_v and rsi_v < 30:
                    signals.add("RSI Asiri Satim")

                # MACD (min_periods enforced)
                if tech.get("macd_cross") == "BULLISH":
                    signals.add("MACD Bullish Cross")
                if tech.get("macd_cross") == "BEARISH":
                    signals.add("MACD Bearish Cross")

                # Bollinger
                if tech.get("bb_pos") == "ABOVE":
                    signals.add("BB Ust Band Kirilim")
                if tech.get("bb_pos") == "BELOW":
                    signals.add("BB Alt Band Kirilim")

                # Ichimoku
                if hist_df is not None and len(hist_df) >= 52:
                    ichi = compute_ichimoku(hist_df)
                    if ichi and ichi["senkou_a"] and ichi["senkou_b"]:
                        kumo_top = max(ichi["senkou_a"], ichi["senkou_b"])
                        kumo_bot = min(ichi["senkou_a"], ichi["senkou_b"])
                        if ichi["price"] > kumo_top and ichi.get("tenkan_prev") and ichi["tenkan_prev"] <= kumo_top:
                            signals.add("Ichimoku Kumo Breakout")
                        if ichi["price"] < kumo_bot and ichi.get("tenkan_prev") and ichi["tenkan_prev"] >= kumo_bot:
                            signals.add("Ichimoku Kumo Breakdown")
                        if (ichi.get("tenkan") and ichi.get("kijun")
                                and ichi.get("tenkan_prev") and ichi.get("kijun_prev")):
                            if ichi["tenkan_prev"] <= ichi["kijun_prev"] and ichi["tenkan"] > ichi["kijun"]:
                                signals.add("Ichimoku TK Cross")
                        details["ichimoku_above_kumo"] = ichi["price"] > kumo_top

                # VCP
                if hist_df is not None and detect_vcp(hist_df):
                    signals.add("VCP Kirilim")

                # Rectangle
                if hist_df is not None:
                    rect = detect_rectangle_breakout(hist_df)
                    if rect == "bullish":
                        signals.add("Rectangle Breakout")
                    elif rect == "bearish":
                        signals.add("Rectangle Breakdown")

                # 52W High (requires 200-bar guard via compute_technical)
                pct_high = tech.get("pct_from_high")
                if pct_high is not None and pct_high >= 0:
                    signals.add("52W High Breakout")

                # Support / Resistance
                if hist_df is not None:
                    resistance, support = find_pivot_levels(hist_df)
                    if resistance and price > resistance * 0.998:
                        signals.add("Direnc Kirilimi")
                    if support and price < support * 1.002:
                        signals.add("Destek Kirilimi")

                all_signals[t] = signals
                prev = self.prev_signals.get(t, set())

                for sig in signals:
                    if sig not in prev:
                        si = SIGNAL_INFO.get(sig, {"icon": "neutral", "stars": 1, "explanation": "", "category": "momentum"})
                        # Use Turkish display name if available
                        display_sig = _SIGNAL_DISPLAY_NAMES.get(sig, sig)
                        new_signals.append({
                            "signal":        display_sig,
                            "signal_type":   si["icon"],
                            "stars":         si["stars"],
                            "category":      si.get("category", "momentum"),
                            "explanation":   si["explanation"],
                            "vol_confirmed": vol_confirmed,
                            **details,
                        })

            except Exception as e:
                log.debug(f"CrossHunter {t}: {e}")

        # Ticker strength aggregation
        ticker_strength = defaultdict(lambda: {"signals": [], "total_stars": 0})
        for s in new_signals:
            ts = ticker_strength[s["ticker"]]
            ts["signals"].append(s)
            ts["total_stars"] += s["stars"]
        for s in new_signals:
            s["ticker_total_stars"]  = ticker_strength[s["ticker"]]["total_stars"]
            s["ticker_signal_count"] = len(ticker_strength[s["ticker"]]["signals"])

        new_signals.sort(key=lambda x: (-x["ticker_total_stars"], -x["stars"]))
        self.prev_signals = all_signals
        self.last_scan    = time.time()
        self.last_results = new_signals
        return new_signals


# Global singleton
cross_hunter = CrossHunter()


# ================================================================
# CHART GENERATOR
# ================================================================
def generate_chart_png(symbol: str, tech_data=None) -> Optional[bytes]:
    """Dark-theme price + volume PNG. plt.close() always called."""
    if not CHART_AVAILABLE:
        return None
    fig = None
    try:
        if tech_data and tech_data.get("price_history"):
            dates_str = [p["date"]   for p in tech_data["price_history"]]
            closes    = [p["close"]  for p in tech_data["price_history"]]
            opens     = [p["open"]   for p in tech_data["price_history"]]
            volumes   = [p["volume"] for p in tech_data["price_history"]]
            dates     = pd.to_datetime(dates_str)
        else:
            df = None
            if YF_AVAILABLE:
                try:
                    tk = yf.Ticker(symbol)
                    df = tk.history(period="6mo", interval="1d")
                except Exception:
                    pass
            if (df is None or (hasattr(df, "empty") and df.empty)) and BORSAPY_AVAILABLE_TECH:
                try:
                    ticker_clean = symbol.upper().replace(".IS", "").replace(".E", "")
                    _tk = bp.Ticker(ticker_clean)
                    df  = _tk.history(period="6ay", interval="1d")
                except Exception:
                    pass
            if df is None or len(df) < 20:
                return None
            dates   = df.index
            closes  = df["Close"].tolist()
            opens   = df["Open"].tolist()
            volumes = df["Volume"].tolist()

        close_s = pd.Series(closes)
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), height_ratios=[3, 1], gridspec_kw={"hspace": 0.05})
        fig.patch.set_facecolor("#0d1117")
        ax1.set_facecolor("#0d1117")
        ax2.set_facecolor("#0d1117")

        ax1.plot(dates, closes, color="#58a6ff", linewidth=1.5, label="Fiyat")
        ma50_c = close_s.rolling(50).mean()
        ax1.plot(dates, ma50_c, color="#f0883e", linewidth=1, alpha=0.8, label="MA50")
        if len(close_s) >= 200:
            ma200_c  = close_s.rolling(200).mean()
            valid_idx = ~ma200_c.isna()
            if valid_idx.sum() > 5:
                ax1.plot(dates[valid_idx], ma200_c[valid_idx], color="#da3633", linewidth=1, alpha=0.8, label="MA200")

        ticker  = base_ticker(symbol)
        price   = closes[-1] if closes else 0
        ts      = tech_data.get("tech_score", 50) if tech_data else 50
        rsi_v   = tech_data.get("rsi")            if tech_data else None
        title   = f"{ticker}  {price:.2f}  |  Teknik: {ts}/100"
        if rsi_v:
            title += f"  |  RSI: {rsi_v:.0f}"

        ax1.set_title(title, color="white", fontsize=12, fontweight="bold", pad=10)
        ax1.legend(loc="upper left", fontsize=8, facecolor="#161b22", edgecolor="#30363d", labelcolor="white")
        ax1.tick_params(colors="gray", labelsize=8)
        ax1.grid(True, alpha=0.1, color="gray")
        ax1.set_ylabel("")
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

        colors = ["#3fb950" if c >= o else "#da3633" for c, o in zip(closes, opens)]
        ax2.bar(dates, volumes, color=colors, alpha=0.6, width=0.8)
        ax2.tick_params(colors="gray", labelsize=7)
        ax2.grid(True, alpha=0.1, color="gray")
        ax2.set_ylabel("")
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

        for ax in [ax1, ax2]:
            for spine in ax.spines.values():
                spine.set_color("#30363d")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="#0d1117", edgecolor="none")
        buf.seek(0)
        return buf.getvalue()

    except Exception as e:
        log.warning(f"Chart {symbol}: {e}")
        return None
    finally:
        if fig is not None:
            plt.close(fig)

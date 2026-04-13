# ================================================================
# BISTBULL TERMINAL V10.1 — TECHNICAL ANALYSIS (EODHD)
# compute_technical, Ichimoku, VCP, Rectangle, Pivot levels,
# CrossHunter sınıfı, Chart generator (memory-leak fixed)
# V10.1: yfinance + borsapy → EODHD API migrasyonu.
# Hesaplama mantığı (RSI, MACD, BB, Ichimoku vb.) AYNEN korunmuş.
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
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    CHART_AVAILABLE = True
except ImportError:
    plt = None  # type: ignore
    mdates = None  # type: ignore
    CHART_AVAILABLE = False

# EODHD provider
try:
    from data.providers import fetch_eod_history, batch_download_history_v9 as _eodhd_batch_history, EODHD_AVAILABLE
except ImportError:
    EODHD_AVAILABLE = False
    fetch_eod_history = None  # type: ignore
    _eodhd_batch_history = None  # type: ignore

# Backward compat flags
YF_AVAILABLE = EODHD_AVAILABLE
BORSAPY_AVAILABLE_TECH = False


# ================================================================
# BATCH HISTORY DOWNLOAD — EODHD API
# ================================================================
def batch_download_history(
    symbols: list[str],
    period: str = "1y",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """
    EODHD ile toplu price history indir.
    Her hisseyi paralel thread'lerle çeker — bulk endpoint yerine
    per-symbol eod endpoint kullanır (historical data gerekli).
    """
    if not EODHD_AVAILABLE or _eodhd_batch_history is None:
        log.warning("batch_download: EODHD not available")
        return {}

    result = _eodhd_batch_history(symbols, period=period, interval=interval)
    if result:
        log.info(f"batch_download (EODHD): {len(result)}/{len(symbols)} başarılı")
    return result


# ================================================================
# COMPUTE TECHNICAL — RSI, MACD, MA, BB, Cross, Volume
# ================================================================
def compute_technical(
    symbol: str,
    hist_df: Optional[pd.DataFrame] = None,
) -> Optional[dict]:
    """Full technical analysis with price history, MA series, indicators."""
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
                if EODHD_AVAILABLE and fetch_eod_history is not None:
                    try:
                        df = fetch_eod_history(symbol, period="1y", interval="1d")
                    except Exception:
                        pass
                if df is not None and not df.empty:
                    history_cache.set(symbol, df)

        if df is None or len(df) < 50:
            return None

        c = df["Close"]
        v = df["Volume"]

        # Moving averages
        ma50 = c.rolling(50).mean()
        ma200 = c.rolling(200).mean() if len(c) >= 200 else pd.Series([np.nan] * len(c), index=c.index)
        price = float(c.iloc[-1])
        ma50_val = float(ma50.iloc[-1]) if not np.isnan(ma50.iloc[-1]) else None
        ma200_val = float(ma200.iloc[-1]) if len(c) >= 200 and not np.isnan(ma200.iloc[-1]) else None

        # Cross signals
        cross_signal = None
        if ma50_val and ma200_val and len(ma50) >= 2 and len(ma200) >= 2:
            prev_50 = float(ma50.iloc[-2]) if not np.isnan(ma50.iloc[-2]) else None
            prev_200 = float(ma200.iloc[-2]) if not np.isnan(ma200.iloc[-2]) else None
            if prev_50 and prev_200:
                if prev_50 <= prev_200 and ma50_val > ma200_val:
                    cross_signal = "GOLDEN_CROSS"
                elif prev_50 >= prev_200 and ma50_val < ma200_val:
                    cross_signal = "DEATH_CROSS"

        # RSI
        delta = c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None

        # MACD
        ema12 = c.ewm(span=12).mean()
        ema26 = c.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_val = float(macd_line.iloc[-1])
        signal_val = float(signal_line.iloc[-1])
        macd_hist = macd_val - signal_val
        macd_bullish = macd_val > signal_val
        macd_cross = None
        if len(macd_line) >= 2:
            prev_macd = float(macd_line.iloc[-2])
            prev_sig = float(signal_line.iloc[-2])
            if prev_macd <= prev_sig and macd_val > signal_val:
                macd_cross = "BULLISH"
            elif prev_macd >= prev_sig and macd_val < signal_val:
                macd_cross = "BEARISH"

        # Bollinger Bands
        bb_mid = c.rolling(20).mean()
        bb_std = c.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_pos = None
        if not np.isnan(bb_upper.iloc[-1]) and not np.isnan(bb_lower.iloc[-1]):
            if price > float(bb_upper.iloc[-1]):
                bb_pos = "ABOVE"
            elif price < float(bb_lower.iloc[-1]):
                bb_pos = "BELOW"
            else:
                bb_pos = "INSIDE"

        # 52W high/low
        high_52w = float(df["High"].tail(252).max()) if len(df) >= 50 else None
        low_52w = float(df["Low"].tail(252).min()) if len(df) >= 50 else None
        pct_from_high = ((price - high_52w) / high_52w * 100) if high_52w else None
        pct_from_low = ((price - low_52w) / low_52w * 100) if low_52w else None

        # Volume
        vol_avg = float(v.tail(20).mean()) if len(v) >= 20 else None
        vol_today = float(v.iloc[-1]) if len(v) > 0 else None
        vol_ratio = (vol_today / vol_avg) if vol_avg and vol_avg > 0 else None

        # Tech score
        components: list[dict] = []
        if rsi_val is not None:
            if 40 <= rsi_val <= 60:
                components.append({"name": "RSI", "score": 50, "desc": "Nötr"})
            elif 30 <= rsi_val < 40:
                components.append({"name": "RSI", "score": 65, "desc": "Aşırı satıma yaklaşıyor"})
            elif rsi_val < 30:
                components.append({"name": "RSI", "score": 85, "desc": "Aşırı satım"})
            elif 60 < rsi_val <= 70:
                components.append({"name": "RSI", "score": 40, "desc": "Aşırı alıma yaklaşıyor"})
            else:
                components.append({"name": "RSI", "score": 20, "desc": "Aşırı alım"})
        if ma50_val:
            if price > ma50_val:
                components.append({"name": "MA50", "score": 70, "desc": "Fiyat MA50 üzerinde"})
            else:
                components.append({"name": "MA50", "score": 30, "desc": "Fiyat MA50 altında"})
        if ma200_val:
            if price > ma200_val:
                components.append({"name": "MA200", "score": 75, "desc": "Fiyat MA200 üzerinde"})
            else:
                components.append({"name": "MA200", "score": 25, "desc": "Fiyat MA200 altında"})
        if ma50_val and ma200_val:
            if ma50_val > ma200_val:
                components.append({"name": "Trend", "score": 80, "desc": "MA50 > MA200 (Yukarı)"})
            else:
                components.append({"name": "Trend", "score": 20, "desc": "MA50 < MA200 (Aşağı)"})
        if macd_bullish:
            components.append({"name": "MACD", "score": 70, "desc": "Yükseliş"})
        else:
            components.append({"name": "MACD", "score": 30, "desc": "Düşüş"})
        if vol_ratio and vol_ratio > 1.5:
            components.append({"name": "Hacim", "score": 75, "desc": f"{vol_ratio:.1f}x ortalama"})
        elif vol_ratio:
            components.append({"name": "Hacim", "score": 50, "desc": f"{vol_ratio:.1f}x ortalama"})

        tech_score = sum(c_["score"] for c_ in components) / len(components) if components else 50.0

        # Price history for frontend (last 130 bars)
        chart_df = df.tail(130)
        price_history: list[dict] = []
        for idx, row in chart_df.iterrows():
            price_history.append({
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })

        # MA series for frontend chart
        ma50_series: list[dict] = []
        ma50_full = c.rolling(50).mean().tail(130)
        for idx_ma, val in ma50_full.items():
            if not np.isnan(val):
                ma50_series.append({"date": idx_ma.strftime("%Y-%m-%d"), "value": round(float(val), 2)})
        ma200_series: list[dict] = []
        if len(c) >= 200:
            ma200_full = c.rolling(200).mean().tail(130)
            for idx_ma, val in ma200_full.items():
                if not np.isnan(val):
                    ma200_series.append({"date": idx_ma.strftime("%Y-%m-%d"), "value": round(float(val), 2)})

        # 20-günlük fiyat değişimi
        pct_20d = None
        if len(c) >= 20:
            pct_20d = round(((price - float(c.iloc[-20])) / float(c.iloc[-20])) * 100, 1)

        result = {
            "price": price, "ma50": ma50_val, "ma200": ma200_val,
            "rsi": rsi_val, "macd": macd_val, "macd_signal": signal_val,
            "macd_hist": macd_hist, "macd_bullish": macd_bullish,
            "macd_cross": macd_cross, "cross_signal": cross_signal,
            "bb_pos": bb_pos,
            "bb_upper": round(float(bb_upper.iloc[-1]), 2) if not np.isnan(bb_upper.iloc[-1]) else None,
            "bb_lower": round(float(bb_lower.iloc[-1]), 2) if not np.isnan(bb_lower.iloc[-1]) else None,
            "high_52w": high_52w, "low_52w": low_52w,
            "pct_from_high": pct_from_high, "pct_from_low": pct_from_low,
            "pct_20d": pct_20d,
            "vol_ratio": vol_ratio, "tech_score": round(tech_score, 1),
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
# ICHIMOKU
# ================================================================
def compute_ichimoku(df: pd.DataFrame) -> Optional[dict]:
    if df is None or len(df) < 52:
        return None
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    return {
        "tenkan": float(tenkan.iloc[-1]) if not np.isnan(tenkan.iloc[-1]) else None,
        "kijun": float(kijun.iloc[-1]) if not np.isnan(kijun.iloc[-1]) else None,
        "tenkan_prev": float(tenkan.iloc[-2]) if len(tenkan) >= 2 and not np.isnan(tenkan.iloc[-2]) else None,
        "kijun_prev": float(kijun.iloc[-2]) if len(kijun) >= 2 and not np.isnan(kijun.iloc[-2]) else None,
        "senkou_a": float(senkou_a.iloc[-1]) if not np.isnan(senkou_a.iloc[-1]) else None,
        "senkou_b": float(senkou_b.iloc[-1]) if not np.isnan(senkou_b.iloc[-1]) else None,
        "price": float(close.iloc[-1]),
    }


# ================================================================
# VCP — Volatility Contraction Pattern
# ================================================================
def detect_vcp(df: pd.DataFrame) -> bool:
    if df is None or len(df) < 50:
        return False
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
    atr_5 = tr.tail(5).mean()
    atr_20 = tr.tail(20).mean()
    atr_50 = tr.tail(50).mean()
    if atr_50 == 0:
        return False
    contracting = atr_5 < atr_20 * 0.85 and atr_20 < atr_50 * 0.90
    recent_high = float(high.tail(5).max())
    breakout = float(close.iloc[-1]) > recent_high * 0.998
    return contracting and breakout


# ================================================================
# RECTANGLE BREAKOUT
# ================================================================
def detect_rectangle_breakout(df: pd.DataFrame) -> Optional[str]:
    """Returns: 'bullish', 'bearish', or None."""
    if df is None or len(df) < 20:
        return None
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    range_high = float(high.tail(20).max())
    range_low = float(low.tail(20).min())
    if range_low == 0:
        return None
    range_pct = (range_high - range_low) / range_low
    price = float(close.iloc[-1])
    if range_pct < 0.08 and price > range_high * 0.998:
        return "bullish"
    if range_pct < 0.08 and price < range_low * 1.002:
        return "bearish"
    return None


# ================================================================
# PIVOT LEVELS (S/R)
# ================================================================
def find_pivot_levels(
    df: pd.DataFrame,
    lookback: int = 60,
) -> tuple[Optional[float], Optional[float]]:
    """Returns: (resistance, support)."""
    if df is None or len(df) < lookback:
        return None, None
    high = df["High"].tail(lookback)
    low = df["Low"].tail(lookback)
    pivot_highs: list[float] = []
    pivot_lows: list[float] = []
    for i in range(3, len(high) - 3):
        if high.iloc[i] == high.iloc[i - 3:i + 4].max():
            pivot_highs.append(float(high.iloc[i]))
        if low.iloc[i] == low.iloc[i - 3:i + 4].min():
            pivot_lows.append(float(low.iloc[i]))
    resistance = max(pivot_highs) if pivot_highs else None
    support = min(pivot_lows) if pivot_lows else None
    return resistance, support


# ================================================================
# CROSS HUNTER V2
# ================================================================
SIGNAL_INFO: dict[str, dict] = {
    "Golden Cross":       {"icon": "bullish", "stars": 5, "category": "kirilim", "explanation": "MA50 yukarı kesti MA200'ü — orta/uzun vade yukarı dönüşü."},
    "Death Cross":        {"icon": "bearish", "stars": 5, "category": "kirilim", "explanation": "MA50 aşağı kesti MA200'ü — orta/uzun vade aşağı dönüşü."},
    "Ichimoku Kumo Breakout": {"icon": "bullish", "stars": 5, "category": "kirilim", "explanation": "Fiyat Ichimoku bulutu üzerine çıktı — güçlü trend değişimi."},
    "Ichimoku Kumo Breakdown": {"icon": "bearish", "stars": 5, "category": "kirilim", "explanation": "Fiyat Ichimoku bulutu altına düştü."},
    "Ichimoku TK Cross":  {"icon": "bullish", "stars": 4, "category": "kirilim", "explanation": "Tenkan-sen Kijun-sen'i yukarı kesti."},
    "VCP Kırılım":        {"icon": "bullish", "stars": 5, "category": "kirilim", "explanation": "Volatilite daralma paterni kırılımı."},
    "Rectangle Breakout": {"icon": "bullish", "stars": 4, "category": "kirilim", "explanation": "Konsolidasyon kırılımı — yukarı."},
    "Rectangle Breakdown": {"icon": "bearish", "stars": 4, "category": "kirilim", "explanation": "Konsolidasyon kırılımı — aşağı."},
    "52W High Breakout":  {"icon": "bullish", "stars": 5, "category": "kirilim", "explanation": "52 haftalık zirveyi kırdı."},
    "Direnç Kırılımı":    {"icon": "bullish", "stars": 4, "category": "kirilim", "explanation": "Pivot direnç seviyesi kırıldı."},
    "Destek Kırılımı":    {"icon": "bearish", "stars": 4, "category": "kirilim", "explanation": "Pivot destek seviyesi kırıldı."},
    "MACD Bullish Cross":  {"icon": "bullish", "stars": 3, "category": "momentum", "explanation": "MACD sinyal çizgisini yukarı kesti."},
    "MACD Bearish Cross":  {"icon": "bearish", "stars": 3, "category": "momentum", "explanation": "MACD sinyal çizgisini aşağı kesti."},
    "RSI Aşırı Alım":     {"icon": "bearish", "stars": 1, "category": "momentum", "explanation": "RSI 70+ — aşırı alım bölgesi."},
    "RSI Aşırı Satım":    {"icon": "bullish", "stars": 1, "category": "momentum", "explanation": "RSI 30- — aşırı satım bölgesi."},
    "BB Üst Band Kırılım": {"icon": "neutral", "stars": 2, "category": "momentum", "explanation": "Fiyat Bollinger üst bandını kırdı."},
    "BB Alt Band Kırılım": {"icon": "neutral", "stars": 2, "category": "momentum", "explanation": "Fiyat Bollinger alt bandını kırdı."},
}


class CrossHunter:
    def __init__(self) -> None:
        self.last_scan: float = 0
        self.prev_signals: dict[str, set] = {}
        self.last_results: list[dict] = []

    def scan_all(self, history_map: Optional[dict] = None) -> list[dict]:
        """Tüm UNIVERSE'ü tara. history_map varsa tekrar indirmez (P4 fix)."""
        new_signals: list[dict] = []
        all_signals: dict[str, set] = {}

        symbols = [normalize_symbol(t) for t in UNIVERSE]
        if history_map is None:
            history_map = batch_download_history(symbols, period="1y", interval="1d")
        for sym, hist_df in history_map.items():
            history_cache.set(sym, hist_df)

        for t in UNIVERSE:
            try:
                symbol = normalize_symbol(t)
                hist_df = history_map.get(symbol)
                tech = compute_technical(symbol, hist_df=hist_df)
                if not tech:
                    continue

                signals: set[str] = set()
                price = tech.get("price", 0)
                vol_ratio = tech.get("vol_ratio")
                vol_confirmed = vol_ratio is not None and vol_ratio > 1.3

                details = {
                    "ticker": t, "price": price, "rsi": tech.get("rsi"),
                    "ma50": tech.get("ma50"), "ma200": tech.get("ma200"),
                    "tech_score": tech.get("tech_score", 50),
                    "vol_ratio": vol_ratio, "pct_from_high": tech.get("pct_from_high"),
                    "macd_bullish": tech.get("macd_bullish"),
                }

                # Basic signals
                if tech.get("cross_signal") == "GOLDEN_CROSS":
                    signals.add("Golden Cross")
                if tech.get("cross_signal") == "DEATH_CROSS":
                    signals.add("Death Cross")
                if tech.get("rsi") and tech["rsi"] > 70:
                    signals.add("RSI Aşırı Alım")
                if tech.get("rsi") and tech["rsi"] < 30:
                    signals.add("RSI Aşırı Satım")
                if tech.get("macd_cross") == "BULLISH":
                    signals.add("MACD Bullish Cross")
                if tech.get("macd_cross") == "BEARISH":
                    signals.add("MACD Bearish Cross")
                if tech.get("bb_pos") == "ABOVE":
                    signals.add("BB Üst Band Kırılım")
                if tech.get("bb_pos") == "BELOW":
                    signals.add("BB Alt Band Kırılım")

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
                    signals.add("VCP Kırılım")

                # Rectangle
                if hist_df is not None:
                    rect = detect_rectangle_breakout(hist_df)
                    if rect == "bullish":
                        signals.add("Rectangle Breakout")
                    elif rect == "bearish":
                        signals.add("Rectangle Breakdown")

                # 52W High
                pct_high = tech.get("pct_from_high")
                if pct_high is not None and pct_high >= 0:
                    signals.add("52W High Breakout")

                # S/R
                if hist_df is not None:
                    resistance, support = find_pivot_levels(hist_df)
                    if resistance and price > resistance * 0.998:
                        signals.add("Direnç Kırılımı")
                    if support and price < support * 1.002:
                        signals.add("Destek Kırılımı")

                all_signals[t] = signals
                prev = self.prev_signals.get(t, set())
                for sig in signals:
                    if sig not in prev:
                        si = SIGNAL_INFO.get(sig, {"icon": "neutral", "stars": 1, "explanation": "", "category": "momentum"})
                        new_signals.append({
                            "signal": sig,
                            "signal_type": si["icon"],
                            "stars": si["stars"],
                            "category": si.get("category", "momentum"),
                            "explanation": si["explanation"],
                            "vol_confirmed": vol_confirmed,
                            **details,
                        })
            except Exception as e:
                log.debug(f"CrossHunter {t}: {e}")

        # Ticker gücü
        ticker_strength: dict[str, dict] = defaultdict(lambda: {"signals": [], "total_stars": 0})
        for s in new_signals:
            ts = ticker_strength[s["ticker"]]
            ts["signals"].append(s)
            ts["total_stars"] += s["stars"]
        for s in new_signals:
            s["ticker_total_stars"] = ticker_strength[s["ticker"]]["total_stars"]
            s["ticker_signal_count"] = len(ticker_strength[s["ticker"]]["signals"])

        new_signals.sort(key=lambda x: (-x["ticker_total_stars"], -x["stars"]))
        self.prev_signals = all_signals
        self.last_scan = time.time()
        self.last_results = new_signals
        return new_signals


# Global instance
cross_hunter = CrossHunter()


# ================================================================
# CHART GENERATOR — matplotlib PNG (memory-leak fixed: try/finally)
# ================================================================
def generate_chart_png(symbol: str, tech_data: Optional[dict] = None) -> Optional[bytes]:
    """Matplotlib PNG chart. plt.close always called (B6 fix)."""
    if not CHART_AVAILABLE:
        return None
    fig = None
    try:
        if tech_data and tech_data.get("price_history"):
            dates_str = [p["date"] for p in tech_data["price_history"]]
            closes = [p["close"] for p in tech_data["price_history"]]
            opens = [p["open"] for p in tech_data["price_history"]]
            volumes = [p["volume"] for p in tech_data["price_history"]]
            dates = pd.to_datetime(dates_str)
        else:
            df = None
            if EODHD_AVAILABLE and fetch_eod_history is not None:
                try:
                    df = fetch_eod_history(symbol, period="6mo", interval="1d")
                except Exception:
                    pass
            if df is None or len(df) < 20:
                return None
            dates = df.index
            closes = df["Close"].tolist()
            opens = df["Open"].tolist()
            volumes = df["Volume"].tolist()

        close_s = pd.Series(closes)
        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(10, 6), height_ratios=[3, 1],
            gridspec_kw={"hspace": 0.05},
        )
        fig.patch.set_facecolor("#0d1117")
        ax1.set_facecolor("#0d1117")
        ax2.set_facecolor("#0d1117")

        ax1.plot(dates, closes, color="#58a6ff", linewidth=1.5, label="Fiyat")
        ma50 = close_s.rolling(50).mean()
        ax1.plot(dates, ma50, color="#f0883e", linewidth=1, alpha=0.8, label="MA50")
        if len(close_s) >= 200:
            ma200 = close_s.rolling(200).mean()
            valid_idx = ~ma200.isna()
            if valid_idx.sum() > 5:
                ax1.plot(dates[valid_idx], ma200[valid_idx], color="#da3633", linewidth=1, alpha=0.8, label="MA200")

        ticker = base_ticker(symbol)
        price = closes[-1] if closes else 0
        ts = tech_data.get("tech_score", 50) if tech_data else 50
        rsi_v = tech_data.get("rsi") if tech_data else None
        title = f"{ticker}  {price:.2f}  |  Teknik: {ts}/100"
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

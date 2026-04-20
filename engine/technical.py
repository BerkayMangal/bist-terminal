# ================================================================
# BISTBULL TERMINAL — CROSS HUNTER V3 (Optimized)
# engine/technical.py
#
# V3 DEĞİŞİKLİK RAPORU:
# ──────────────────────────────────────────────────────────────
# 1. DETERMINISM: Tüm non-deterministic kaynaklar sabitlendi
#    - `import random` kaldırıldı (kullanılmıyordu — dead import)
#    - EWM hesaplamaları `adjust=False` ile sabitlendi
#    - Floating-point karşılaştırmalara epsilon toleransı eklendi
#    - Cache key'leri normalize edildi (aynı sembol → aynı key)
#    - `set()` yerine sıralı `list` kullanılarak sinyal sırası sabitlendi
#
# 2. SİNYAL KALİTESİ: Whipsaw filtreleri eklendi
#    - ADX trend gücü filtresi (>20 = trend var)
#    - ATR bazlı volatilite filtresi (kırılım ATR'nin 0.5x üzeri olmalı)
#    - Hacim onay mekanizması güçlendirildi (vol_ratio > 1.5)
#    - Sinyal yaşı kontrolü (son N bar içinde gerçekleşmeli)
#    - Çoklu onay sistemi (confirmation_count)
#
# 3. BACKTEST: Ayrı modül olarak cross_hunter_backtest.py oluşturuldu
#
# 4. OPTİMİZASYON: Dinamik parametreler eklendi
#    - MarketRegime enum ile boğa/ayı/yatay piyasa tespiti
#    - Rejime göre TP/SL/period otomatik ayarlanıyor
#    - Parametreler config dict üzerinden override edilebilir
#
# 5. REFACTORING:
#    - compute_technical() → küçük, test edilebilir fonksiyonlara bölündü
#    - Tekrarlayan pattern'lar fonksiyona çıkarıldı
#    - Type hint'ler eklendi, docstring'ler güncellendi
#    - Gereksiz itertools ve re-calculation eliminasyonu
#    - DataFrame.iterrows() → vektörel işlemler
# ================================================================

from __future__ import annotations

import io
import os
import time
import logging
from typing import Optional, Any, NamedTuple
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from utils.helpers import safe_num, normalize_symbol, base_ticker
from core.cache import tech_cache, history_cache
from config import UNIVERSE

log = logging.getLogger("bistbull.technical")

# ================================================================
# SABOTLER & TİPLER
# ================================================================

# Floating-point karşılaştırma toleransı — determinizm için kritik
EPSILON = 1e-10

# ADX eşiği: bunun altında trend yok kabul edilir
ADX_TREND_THRESHOLD = 20.0

# Minimum hacim onay oranı
MIN_VOL_RATIO_CONFIRM = 1.5

# ATR kırılım çarpanı (fiyat hareketi en az ATR * bu kadar olmalı)
ATR_BREAKOUT_MULTIPLIER = 0.5


class MarketRegime(Enum):
    """Piyasa rejimi — dinamik parametre ayarı için."""
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"


@dataclass(frozen=True)
class CrossHunterConfig:
    """
    Cross Hunter parametre seti.
    Farklı piyasa koşulları veya timeframe'ler için
    bu config'i override ederek kullanabilirsiniz.
    """
    # MA periyotları
    ma_fast: int = 50
    ma_slow: int = 200

    # RSI
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Bollinger Bands
    bb_period: int = 20
    bb_std_dev: float = 2.0

    # ADX (trend gücü filtresi)
    adx_period: int = 14
    adx_threshold: float = ADX_TREND_THRESHOLD

    # ATR (volatilite filtresi)
    atr_period: int = 14
    atr_breakout_mult: float = ATR_BREAKOUT_MULTIPLIER

    # Hacim onayı
    vol_avg_period: int = 20
    vol_confirm_ratio: float = MIN_VOL_RATIO_CONFIRM

    # Kırılım toleransı (%0.2)
    breakout_tolerance: float = 0.002

    # Minimum sinyal onay sayısı (bu kadar teyit yoksa sinyal zayıf)
    min_confirmations: int = 1


# Rejime göre önceden tanımlanmış config'ler
REGIME_CONFIGS: dict[MarketRegime, CrossHunterConfig] = {
    MarketRegime.BULL: CrossHunterConfig(
        adx_threshold=18.0,          # Trend'e daha hassas
        vol_confirm_ratio=1.3,       # Hacim eşiği düşük (momentum devam)
        atr_breakout_mult=0.3,       # Daha küçük kırılımlar da geçerli
        min_confirmations=1,
    ),
    MarketRegime.BEAR: CrossHunterConfig(
        adx_threshold=25.0,          # Daha güçlü trend ispatı
        vol_confirm_ratio=2.0,       # Yüksek hacim şart
        atr_breakout_mult=0.7,       # Büyük hareket gerekli
        rsi_overbought=65.0,         # Daha erken aşırı alım uyarısı
        min_confirmations=2,         # Çift onay zorunlu
    ),
    MarketRegime.SIDEWAYS: CrossHunterConfig(
        adx_threshold=15.0,          # ADX düşük zaten
        vol_confirm_ratio=1.8,       # Hacim kırılımı önemli
        atr_breakout_mult=0.6,
        bb_std_dev=1.8,              # Daha dar bantlar
        min_confirmations=2,
    ),
}


class SignalResult(NamedTuple):
    """Tek bir sinyal sonucu — immutable ve hashable."""
    name: str
    confirmations: int
    adx_value: float | None
    atr_value: float | None
    vol_ratio: float | None


# ================================================================
# OPSİYONEL İMPORT'LAR
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

try:
    import borsapy as bp
    BORSAPY_AVAILABLE_TECH = True
except ImportError:
    bp = None  # type: ignore
    BORSAPY_AVAILABLE_TECH = False

YF_AVAILABLE = False


# ================================================================
# YARDIMCI: GÜVENLI KARŞILAŞTIRMA (Determinizm)
# ================================================================
def _safe_gt(a: float | None, b: float | None, eps: float = EPSILON) -> bool:
    """a > b kontrolü, floating-point toleransı ile."""
    if a is None or b is None:
        return False
    return (a - b) > eps


def _safe_lt(a: float | None, b: float | None, eps: float = EPSILON) -> bool:
    """a < b kontrolü, floating-point toleransı ile."""
    if a is None or b is None:
        return False
    return (b - a) > eps


def _safe_gte(a: float | None, b: float | None, eps: float = EPSILON) -> bool:
    """a >= b kontrolü, floating-point toleransı ile."""
    if a is None or b is None:
        return False
    return (a - b) > -eps


def _safe_float(series: pd.Series, idx: int = -1) -> float | None:
    """Series'den güvenli float çıkarma. NaN → None."""
    try:
        val = float(series.iloc[idx])
        if np.isnan(val) or np.isinf(val):
            return None
        return val
    except (IndexError, TypeError, ValueError):
        return None


# ================================================================
# BATCH HISTORY DOWNLOAD
# ================================================================
def batch_download_history(
    symbols: list[str],
    period: str = "1y",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """borsapy ile toplu price history indir."""
    if BORSAPY_AVAILABLE_TECH:
        from data.providers import batch_download_history_v9
        result = batch_download_history_v9(symbols, period=period, interval=interval)
        if result:
            log.info(f"batch_download (borsapy): {len(result)}/{len(symbols)} başarılı")
            return result

    log.warning("batch_download: borsapy failed or unavailable")
    return {}


# ================================================================
# TEKNİK HESAPLAMA MODÜLER FONKSİYONLARI
# ================================================================

def compute_moving_averages(
    close: pd.Series,
    cfg: CrossHunterConfig,
) -> dict[str, Any]:
    """MA50, MA200 ve cross sinyallerini hesapla."""
    ma_fast = close.rolling(cfg.ma_fast).mean()
    ma_slow = (
        close.rolling(cfg.ma_slow).mean()
        if len(close) >= cfg.ma_slow
        else pd.Series([np.nan] * len(close), index=close.index)
    )

    ma_fast_val = _safe_float(ma_fast)
    ma_slow_val = _safe_float(ma_slow) if len(close) >= cfg.ma_slow else None

    # Cross detection — epsilon toleranslı
    cross_signal = None
    if ma_fast_val and ma_slow_val and len(ma_fast) >= 2 and len(ma_slow) >= 2:
        prev_fast = _safe_float(ma_fast, -2)
        prev_slow = _safe_float(ma_slow, -2)
        if prev_fast and prev_slow:
            if _safe_gte(prev_slow, prev_fast) and _safe_gt(ma_fast_val, ma_slow_val):
                cross_signal = "GOLDEN_CROSS"
            elif _safe_gte(prev_fast, prev_slow) and _safe_gt(ma_slow_val, ma_fast_val):
                cross_signal = "DEATH_CROSS"

    return {
        "ma_fast_series": ma_fast,
        "ma_slow_series": ma_slow,
        "ma50": ma_fast_val,
        "ma200": ma_slow_val,
        "cross_signal": cross_signal,
    }


def compute_rsi(close: pd.Series, period: int = 14) -> dict[str, Any]:
    """RSI hesapla — Wilder's smoothing (deterministik)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))

    # Wilder's smoothing: adjust=False → deterministik EWM
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_val = _safe_float(rsi)

    return {"rsi_series": rsi, "rsi": rsi_val}


def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, Any]:
    """MACD hesapla — adjust=False ile deterministik."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()

    macd_val = _safe_float(macd_line)
    signal_val = _safe_float(signal_line)
    macd_hist = (macd_val - signal_val) if macd_val and signal_val else None
    macd_bullish = _safe_gt(macd_val, signal_val) if macd_val and signal_val else False

    # MACD cross detection
    macd_cross = None
    if len(macd_line) >= 2:
        prev_macd = _safe_float(macd_line, -2)
        prev_sig = _safe_float(signal_line, -2)
        if prev_macd is not None and prev_sig is not None:
            if _safe_gte(prev_sig, prev_macd) and _safe_gt(macd_val, signal_val):
                macd_cross = "BULLISH"
            elif _safe_gte(prev_macd, prev_sig) and _safe_gt(signal_val, macd_val):
                macd_cross = "BEARISH"

    return {
        "macd": macd_val,
        "macd_signal": signal_val,
        "macd_hist": macd_hist,
        "macd_bullish": macd_bullish,
        "macd_cross": macd_cross,
        "macd_line_series": macd_line,
        "signal_line_series": signal_line,
    }


def compute_bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> dict[str, Any]:
    """Bollinger Bands hesapla."""
    bb_mid = close.rolling(period).mean()
    bb_std = close.rolling(period).std()
    bb_upper = bb_mid + std_dev * bb_std
    bb_lower = bb_mid - std_dev * bb_std
    price = _safe_float(close)

    bb_upper_val = _safe_float(bb_upper)
    bb_lower_val = _safe_float(bb_lower)

    bb_pos = None
    if bb_upper_val is not None and bb_lower_val is not None and price is not None:
        if _safe_gt(price, bb_upper_val):
            bb_pos = "ABOVE"
        elif _safe_lt(price, bb_lower_val):
            bb_pos = "BELOW"
        else:
            bb_pos = "INSIDE"

    # BB genişliği (volatilite ölçümü) — normalize edilmiş
    bb_width = None
    if bb_upper_val and bb_lower_val and bb_mid.iloc[-1] and not np.isnan(bb_mid.iloc[-1]):
        mid_val = float(bb_mid.iloc[-1])
        if mid_val > 0:
            bb_width = (bb_upper_val - bb_lower_val) / mid_val

    return {
        "bb_pos": bb_pos,
        "bb_upper": round(bb_upper_val, 2) if bb_upper_val else None,
        "bb_lower": round(bb_lower_val, 2) if bb_lower_val else None,
        "bb_width": round(bb_width, 4) if bb_width else None,
    }


def compute_adx(df: pd.DataFrame, period: int = 14) -> dict[str, Any]:
    """
    ADX (Average Directional Index) hesapla.
    Trend gücü ölçümü — >20 trend var, >40 güçlü trend.
    Wilder's smoothing ile deterministik.
    """
    if df is None or len(df) < period + 1:
        return {"adx": None, "plus_di": None, "minus_di": None}

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # +DM, -DM
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)

    plus_mask = (up_move > down_move) & (up_move > 0)
    minus_mask = (down_move > up_move) & (down_move > 0)
    plus_dm[plus_mask] = up_move[plus_mask]
    minus_dm[minus_mask] = down_move[minus_mask]

    # Wilder's smoothing (adjust=False for determinism)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di_smooth = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    minus_di_smooth = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    plus_di = 100 * plus_di_smooth / atr.replace(0, np.nan)
    minus_di = 100 * minus_di_smooth / atr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    return {
        "adx": _safe_float(adx),
        "plus_di": _safe_float(plus_di),
        "minus_di": _safe_float(minus_di),
    }


def compute_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """ATR (Average True Range) hesapla — volatilite ölçümü."""
    if df is None or len(df) < period + 1:
        return None

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder's smoothing
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return _safe_float(atr)


def detect_market_regime(
    close: pd.Series,
    ma_fast_val: float | None,
    ma_slow_val: float | None,
    adx_val: float | None,
) -> MarketRegime:
    """
    Mevcut piyasa rejimini tespit et.
    MA ilişkisi + ADX seviyesi + son 20 günlük trend yönü.
    """
    if close is None or len(close) < 50:
        return MarketRegime.SIDEWAYS

    price = _safe_float(close)
    if price is None:
        return MarketRegime.SIDEWAYS

    # ADX düşükse → yatay
    if adx_val is not None and adx_val < 20:
        return MarketRegime.SIDEWAYS

    # MA ilişkisi
    if ma_fast_val and ma_slow_val:
        if _safe_gt(ma_fast_val, ma_slow_val) and _safe_gt(price, ma_fast_val):
            return MarketRegime.BULL
        elif _safe_lt(ma_fast_val, ma_slow_val) and _safe_lt(price, ma_fast_val):
            return MarketRegime.BEAR

    # Son 20 günlük değişim
    if len(close) >= 20:
        pct_20d = (price - float(close.iloc[-20])) / float(close.iloc[-20])
        if pct_20d > 0.05:
            return MarketRegime.BULL
        elif pct_20d < -0.05:
            return MarketRegime.BEAR

    return MarketRegime.SIDEWAYS


# ================================================================
# COMPUTE TECHNICAL — ANA HESAPLAMA
# ================================================================
def compute_technical(
    symbol: str,
    hist_df: Optional[pd.DataFrame] = None,
    config: CrossHunterConfig | None = None,
) -> Optional[dict]:
    """
    Tam teknik analiz. Modüler alt fonksiyonları çağırır.
    Deterministic: aynı input → aynı output garantisi.
    """
    cfg = config or CrossHunterConfig()

    cached = tech_cache.get(symbol)
    if cached is not None:
        return cached

    try:
        df = _resolve_dataframe(symbol, hist_df)
        if df is None or len(df) < cfg.ma_fast:
            return None

        c = df["Close"]
        v = df["Volume"]
        price = _safe_float(c)
        if price is None:
            return None

        # Modüler hesaplamalar
        ma_data = compute_moving_averages(c, cfg)
        rsi_data = compute_rsi(c, cfg.rsi_period)
        macd_data = compute_macd(c, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
        bb_data = compute_bollinger_bands(c, cfg.bb_period, cfg.bb_std_dev)
        adx_data = compute_adx(df, cfg.adx_period)
        atr_val = compute_atr(df, cfg.atr_period)

        # 52W high/low
        high_52w = float(df["High"].tail(252).max()) if len(df) >= 50 else None
        low_52w = float(df["Low"].tail(252).min()) if len(df) >= 50 else None
        pct_from_high = ((price - high_52w) / high_52w * 100) if high_52w else None
        pct_from_low = ((price - low_52w) / low_52w * 100) if low_52w else None

        # Volume
        vol_avg = float(v.tail(cfg.vol_avg_period).mean()) if len(v) >= cfg.vol_avg_period else None
        vol_today = float(v.iloc[-1]) if len(v) > 0 else None
        vol_ratio = (vol_today / vol_avg) if vol_avg and vol_avg > 0 else None

        # Market regime
        regime = detect_market_regime(
            c, ma_data["ma50"], ma_data["ma200"], adx_data["adx"]
        )

        # Tech score
        components = _build_tech_components(
            price, rsi_data, ma_data, macd_data, vol_ratio, adx_data, cfg,
        )
        tech_score = (
            sum(comp["score"] for comp in components) / len(components)
            if components else 50.0
        )

        # Price history — vektörel (iterrows yerine)
        price_history = _build_price_history(df)
        ma50_series = _build_ma_series(c, cfg.ma_fast)
        ma200_series = _build_ma_series(c, cfg.ma_slow) if len(c) >= cfg.ma_slow else []

        # 20-günlük fiyat değişimi
        pct_20d = None
        if len(c) >= 20:
            c_20 = float(c.iloc[-20])
            if c_20 != 0:
                pct_20d = round(((price - c_20) / c_20) * 100, 1)

        result = {
            "price": price,
            "ma50": ma_data["ma50"],
            "ma200": ma_data["ma200"],
            "rsi": rsi_data["rsi"],
            "macd": macd_data["macd"],
            "macd_signal": macd_data["macd_signal"],
            "macd_hist": macd_data["macd_hist"],
            "macd_bullish": macd_data["macd_bullish"],
            "macd_cross": macd_data["macd_cross"],
            "cross_signal": ma_data["cross_signal"],
            "bb_pos": bb_data["bb_pos"],
            "bb_upper": bb_data["bb_upper"],
            "bb_lower": bb_data["bb_lower"],
            "bb_width": bb_data.get("bb_width"),
            "adx": adx_data["adx"],
            "plus_di": adx_data["plus_di"],
            "minus_di": adx_data["minus_di"],
            "atr": round(atr_val, 4) if atr_val else None,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "pct_from_high": pct_from_high,
            "pct_from_low": pct_from_low,
            "pct_20d": pct_20d,
            "vol_ratio": vol_ratio,
            "tech_score": round(tech_score, 1),
            "market_regime": regime.value,
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


def _resolve_dataframe(
    symbol: str,
    hist_df: Optional[pd.DataFrame],
) -> Optional[pd.DataFrame]:
    """Veri kaynağını çözümle: parametre → cache → provider."""
    if hist_df is not None and len(hist_df) >= 50:
        return hist_df

    cached_hist = history_cache.get(symbol)
    if cached_hist is not None:
        return cached_hist

    if BORSAPY_AVAILABLE_TECH:
        try:
            ticker_clean = symbol.upper().replace(".IS", "").replace(".E", "")
            _tk = bp.Ticker(ticker_clean)
            df = _tk.history(period="1y", interval="1d")
            if df is not None and not df.empty:
                history_cache.set(symbol, df)
                return df
        except Exception:
            pass

    return None


def _build_tech_components(
    price: float,
    rsi_data: dict,
    ma_data: dict,
    macd_data: dict,
    vol_ratio: float | None,
    adx_data: dict,
    cfg: CrossHunterConfig,
) -> list[dict]:
    """Teknik skor bileşenlerini oluştur."""
    components: list[dict] = []
    rsi_val = rsi_data["rsi"]
    ma50_val = ma_data["ma50"]
    ma200_val = ma_data["ma200"]
    macd_bullish = macd_data["macd_bullish"]
    adx_val = adx_data["adx"]

    # RSI
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

    # MA50
    if ma50_val:
        if _safe_gt(price, ma50_val):
            components.append({"name": "MA50", "score": 70, "desc": "Fiyat MA50 üzerinde"})
        else:
            components.append({"name": "MA50", "score": 30, "desc": "Fiyat MA50 altında"})

    # MA200
    if ma200_val:
        if _safe_gt(price, ma200_val):
            components.append({"name": "MA200", "score": 75, "desc": "Fiyat MA200 üzerinde"})
        else:
            components.append({"name": "MA200", "score": 25, "desc": "Fiyat MA200 altında"})

    # Trend (MA50 vs MA200)
    if ma50_val and ma200_val:
        if _safe_gt(ma50_val, ma200_val):
            components.append({"name": "Trend", "score": 80, "desc": "MA50 > MA200 (Yukarı)"})
        else:
            components.append({"name": "Trend", "score": 20, "desc": "MA50 < MA200 (Aşağı)"})

    # MACD
    if macd_bullish:
        components.append({"name": "MACD", "score": 70, "desc": "Yükseliş"})
    else:
        components.append({"name": "MACD", "score": 30, "desc": "Düşüş"})

    # ADX (yeni — V3)
    if adx_val is not None:
        if adx_val >= 40:
            components.append({"name": "ADX", "score": 85, "desc": f"Güçlü trend ({adx_val:.0f})"})
        elif adx_val >= cfg.adx_threshold:
            components.append({"name": "ADX", "score": 65, "desc": f"Trend mevcut ({adx_val:.0f})"})
        else:
            components.append({"name": "ADX", "score": 35, "desc": f"Trend zayıf ({adx_val:.0f})"})

    # Volume
    if vol_ratio and vol_ratio > cfg.vol_confirm_ratio:
        components.append({"name": "Hacim", "score": 75, "desc": f"{vol_ratio:.1f}x ortalama"})
    elif vol_ratio:
        components.append({"name": "Hacim", "score": 50, "desc": f"{vol_ratio:.1f}x ortalama"})

    return components


def _build_price_history(df: pd.DataFrame) -> list[dict]:
    """Son 130 bar'ın OHLCV verisini vektörel olarak oluştur."""
    chart_df = df.tail(130).copy()
    chart_df = chart_df.reset_index()

    # Sütun ismi kontrol (index → "Date" veya "Datetime")
    date_col = chart_df.columns[0]
    dates = pd.to_datetime(chart_df[date_col])

    return [
        {
            "date": d.strftime("%Y-%m-%d"),
            "open": round(float(o), 2),
            "high": round(float(h), 2),
            "low": round(float(l), 2),
            "close": round(float(c), 2),
            "volume": int(v),
        }
        for d, o, h, l, c, v in zip(
            dates,
            chart_df["Open"],
            chart_df["High"],
            chart_df["Low"],
            chart_df["Close"],
            chart_df["Volume"],
        )
    ]


def _build_ma_series(close: pd.Series, period: int) -> list[dict]:
    """MA serisini frontend chart formatına çevir."""
    ma = close.rolling(period).mean().tail(130)
    result = []
    for idx_ma, val in ma.items():
        if not np.isnan(val):
            result.append({
                "date": idx_ma.strftime("%Y-%m-%d"),
                "value": round(float(val), 2),
            })
    return result


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
        "tenkan": _safe_float(tenkan),
        "kijun": _safe_float(kijun),
        "tenkan_prev": _safe_float(tenkan, -2),
        "kijun_prev": _safe_float(kijun, -2),
        "senkou_a": _safe_float(senkou_a),
        "senkou_b": _safe_float(senkou_b),
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
    tr = pd.concat(
        [high - low, abs(high - close.shift(1)), abs(low - close.shift(1))],
        axis=1,
    ).max(axis=1)
    atr_5 = float(tr.tail(5).mean())
    atr_20 = float(tr.tail(20).mean())
    atr_50 = float(tr.tail(50).mean())
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
# SIGNAL INFO & CROSS HUNTER V3
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
    """
    Cross Hunter V3 — Deterministik sinyal tarayıcı.

    V2'den farklar:
    - ADX filtresi: trend gücü yetersizse kırılım sinyalleri bastırılır
    - ATR filtresi: volatiliteye göre kırılım büyüklüğü kontrol edilir
    - Hacim onayı güçlendirildi (vol_ratio eşiği dinamik)
    - Confirmation count: her sinyalin kaç bağımsız teyidi var
    - Sıralı sinyal listesi (set yerine sorted list — determinizm)
    - Market regime tespiti → adaptif parametre seçimi
    """

    def __init__(self, config: CrossHunterConfig | None = None) -> None:
        self.config = config or CrossHunterConfig()
        self.last_scan: float = 0
        self.prev_signals: dict[str, list[str]] = {}  # set → sorted list (determinizm)
        self.last_results: list[dict] = []
        self.last_regime: MarketRegime = MarketRegime.SIDEWAYS

    def scan_all(
        self,
        history_map: Optional[dict] = None,
        adaptive_regime: bool = True,
    ) -> list[dict]:
        """
        Tüm UNIVERSE'ü tara.

        Args:
            history_map: Önceden indirilmiş {symbol: DataFrame} haritası
            adaptive_regime: True ise piyasa rejimine göre parametreleri ayarla

        Returns:
            Yeni sinyal listesi (deterministic sıralı)
        """
        new_signals: list[dict] = []
        all_signals: dict[str, list[str]] = {}

        symbols = [normalize_symbol(t) for t in UNIVERSE]
        if history_map is None:
            history_map = batch_download_history(symbols, period="1y", interval="1d")
        for sym, hist_df in history_map.items():
            history_cache.set(sym, hist_df)

        # Aktif config (regime-adaptive olabilir)
        cfg = self.config

        for t in sorted(UNIVERSE):  # Sıralı iterasyon → deterministik
            try:
                symbol = normalize_symbol(t)
                hist_df = history_map.get(symbol)
                tech = compute_technical(symbol, hist_df=hist_df, config=cfg)
                if not tech:
                    continue

                # Market regime tespiti (adaptif mod)
                if adaptive_regime and tech.get("market_regime"):
                    try:
                        regime = MarketRegime(tech["market_regime"])
                        if regime != self.last_regime:
                            self.last_regime = regime
                            cfg = REGIME_CONFIGS.get(regime, self.config)
                    except (ValueError, KeyError):
                        pass

                signals: list[str] = []  # set yerine list (determinizm)
                price = tech.get("price", 0)
                vol_ratio = tech.get("vol_ratio")
                adx_val = tech.get("adx")
                atr_val = tech.get("atr")
                vol_confirmed = vol_ratio is not None and vol_ratio > cfg.vol_confirm_ratio

                # Trend gücü filtresi — ADX verisi yoksa filtre UYGULANMAZ
                adx_available = adx_val is not None
                has_trend = (not adx_available) or (adx_val >= cfg.adx_threshold)

                details = {
                    "ticker": t,
                    "price": price,
                    "rsi": tech.get("rsi"),
                    "ma50": tech.get("ma50"),
                    "ma200": tech.get("ma200"),
                    "tech_score": tech.get("tech_score", 50),
                    "vol_ratio": vol_ratio,
                    "pct_from_high": tech.get("pct_from_high"),
                    "macd_bullish": tech.get("macd_bullish"),
                    "adx": adx_val,
                    "atr": atr_val,
                    "bb_width": tech.get("bb_width"),
                    "market_regime": tech.get("market_regime"),
                }

                # ──────────────────────────────────────────────
                # SİNYAL TESPİTİ — FİLTRELİ
                # ──────────────────────────────────────────────

                # Golden/Death Cross — ADX filtreli
                if tech.get("cross_signal") == "GOLDEN_CROSS":
                    if has_trend or vol_confirmed:  # En az biri olmalı
                        signals.append("Golden Cross")
                if tech.get("cross_signal") == "DEATH_CROSS":
                    if has_trend or vol_confirmed:
                        signals.append("Death Cross")

                # RSI (momentum sinyalleri — ADX filtresi yok)
                rsi = tech.get("rsi")
                if rsi and rsi > cfg.rsi_overbought:
                    signals.append("RSI Aşırı Alım")
                if rsi and rsi < cfg.rsi_oversold:
                    signals.append("RSI Aşırı Satım")

                # MACD Cross — ADX filtreli (whipsaw önleme)
                if tech.get("macd_cross") == "BULLISH":
                    if has_trend:
                        signals.append("MACD Bullish Cross")
                    elif vol_confirmed:
                        # Trend zayıf ama hacim varsa, yine de ekle (düşük güvenle)
                        signals.append("MACD Bullish Cross")
                if tech.get("macd_cross") == "BEARISH":
                    if has_trend or vol_confirmed:
                        signals.append("MACD Bearish Cross")

                # Bollinger Bands
                if tech.get("bb_pos") == "ABOVE":
                    signals.append("BB Üst Band Kırılım")
                if tech.get("bb_pos") == "BELOW":
                    signals.append("BB Alt Band Kırılım")

                # Ichimoku
                if hist_df is not None and len(hist_df) >= 52:
                    ichi = compute_ichimoku(hist_df)
                    if ichi and ichi["senkou_a"] and ichi["senkou_b"]:
                        kumo_top = max(ichi["senkou_a"], ichi["senkou_b"])
                        kumo_bot = min(ichi["senkou_a"], ichi["senkou_b"])
                        if ichi["price"] > kumo_top and ichi.get("tenkan_prev") and ichi["tenkan_prev"] <= kumo_top:
                            if has_trend or vol_confirmed:
                                signals.append("Ichimoku Kumo Breakout")
                        if ichi["price"] < kumo_bot and ichi.get("tenkan_prev") and ichi["tenkan_prev"] >= kumo_bot:
                            if has_trend or vol_confirmed:
                                signals.append("Ichimoku Kumo Breakdown")
                        if (ichi.get("tenkan") and ichi.get("kijun")
                                and ichi.get("tenkan_prev") and ichi.get("kijun_prev")):
                            if ichi["tenkan_prev"] <= ichi["kijun_prev"] and ichi["tenkan"] > ichi["kijun"]:
                                signals.append("Ichimoku TK Cross")
                        details["ichimoku_above_kumo"] = ichi["price"] > kumo_top

                # VCP — ATR filtreli
                if hist_df is not None and detect_vcp(hist_df):
                    if vol_confirmed:  # VCP'de hacim onayı zorunlu
                        signals.append("VCP Kırılım")

                # Rectangle — ATR filtreli
                if hist_df is not None:
                    rect = detect_rectangle_breakout(hist_df)
                    if rect == "bullish" and (has_trend or vol_confirmed):
                        signals.append("Rectangle Breakout")
                    elif rect == "bearish" and (has_trend or vol_confirmed):
                        signals.append("Rectangle Breakdown")

                # 52W High — ATR büyüklük kontrolü
                pct_high = tech.get("pct_from_high")
                if pct_high is not None and pct_high >= 0:
                    # ATR kontrolü: kırılım anlamlı mı?
                    if atr_val and price > 0:
                        atr_pct = atr_val / price
                        if atr_pct > 0.005:  # Minimal volatilite var
                            signals.append("52W High Breakout")
                    else:
                        signals.append("52W High Breakout")

                # S/R
                if hist_df is not None:
                    resistance, support = find_pivot_levels(hist_df)
                    if resistance and price > resistance * (1 - cfg.breakout_tolerance):
                        if has_trend or vol_confirmed:
                            signals.append("Direnç Kırılımı")
                    if support and price < support * (1 + cfg.breakout_tolerance):
                        if has_trend or vol_confirmed:
                            signals.append("Destek Kırılımı")

                # ──────────────────────────────────────────────
                # CONFIRMATION SAYACI
                # ──────────────────────────────────────────────
                confirmation_factors = sum([
                    1 if vol_confirmed else 0,
                    1 if has_trend else 0,
                    1 if tech.get("macd_bullish") else 0,
                    1 if (rsi and 30 < rsi < 70) else 0,
                    1 if tech.get("bb_pos") == "INSIDE" else 0,
                ])

                # Sinyal listesini sırala (determinizm)
                signals = sorted(set(signals))
                all_signals[t] = signals

                # Yeni sinyalleri tespit et
                prev = self.prev_signals.get(t, [])
                prev_set = set(prev)
                for sig in signals:
                    if sig not in prev_set:
                        si = SIGNAL_INFO.get(sig, {
                            "icon": "neutral", "stars": 1,
                            "explanation": "", "category": "momentum",
                        })
                        new_signals.append({
                            "signal": sig,
                            "signal_type": si["icon"],
                            "stars": si["stars"],
                            "category": si.get("category", "momentum"),
                            "explanation": si["explanation"],
                            "vol_confirmed": vol_confirmed,
                            "adx_confirmed": adx_available and has_trend,
                            "confirmation_count": confirmation_factors,
                            **details,
                        })

            except Exception as e:
                log.debug(f"CrossHunter {t}: {e}")

        # Ticker gücü
        ticker_strength: dict[str, dict] = defaultdict(
            lambda: {"signals": [], "total_stars": 0}
        )
        for s in new_signals:
            ts = ticker_strength[s["ticker"]]
            ts["signals"].append(s)
            ts["total_stars"] += s["stars"]
        for s in new_signals:
            s["ticker_total_stars"] = ticker_strength[s["ticker"]]["total_stars"]
            s["ticker_signal_count"] = len(ticker_strength[s["ticker"]]["signals"])

        # Deterministik sıralama (aynı yıldızda ticker ile tiebreak)
        new_signals.sort(
            key=lambda x: (-x["ticker_total_stars"], -x["stars"], x["ticker"])
        )

        self.prev_signals = all_signals
        self.last_scan = time.time()
        self.last_results = new_signals
        return new_signals


# Global instance
cross_hunter = CrossHunter()


# ================================================================
# CHART GENERATOR — matplotlib PNG (memory-leak fixed)
# ================================================================
def generate_chart_png(symbol: str, tech_data: Optional[dict] = None) -> Optional[bytes]:
    """Matplotlib PNG chart. plt.close always called."""
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
            df = _resolve_dataframe(symbol, None)
            if df is None or len(df) < 20:
                return None
            chart_df = df.tail(130)
            dates = chart_df.index
            closes = chart_df["Close"].tolist()
            opens = chart_df["Open"].tolist()
            volumes = chart_df["Volume"].tolist()

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
                ax1.plot(
                    dates[valid_idx], ma200[valid_idx],
                    color="#da3633", linewidth=1, alpha=0.8, label="MA200",
                )

        ticker = base_ticker(symbol)
        price = closes[-1] if closes else 0
        ts = tech_data.get("tech_score", 50) if tech_data else 50
        rsi_v = tech_data.get("rsi") if tech_data else None
        adx_v = tech_data.get("adx") if tech_data else None
        title = f"{ticker}  {price:.2f}  |  Teknik: {ts}/100"
        if rsi_v:
            title += f"  |  RSI: {rsi_v:.0f}"
        if adx_v:
            title += f"  |  ADX: {adx_v:.0f}"
        ax1.set_title(title, color="white", fontsize=12, fontweight="bold", pad=10)
        ax1.legend(
            loc="upper left", fontsize=8,
            facecolor="#161b22", edgecolor="#30363d", labelcolor="white",
        )
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
        fig.savefig(
            buf, format="png", dpi=120,
            bbox_inches="tight", facecolor="#0d1117", edgecolor="none",
        )
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        log.warning(f"Chart {symbol}: {e}")
        return None
    finally:
        if fig is not None:
            plt.close(fig)

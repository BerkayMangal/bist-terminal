"""Signal detectors for the 17 CrossHunter signals (Phase 3 FAZ 3.3).

Each detector is a function (symbol, as_of) -> bool, pluggable into
research.validator.run_validator.

Architecture:
  - Detectors consume (symbol, as_of) and internally query PIT prices
    via get_prices / get_price_at_or_before.
  - They reuse engine.technical's computation functions where feasible,
    but the detector API is simpler than the runtime CrossHunter because
    the validator doesn't need market-regime adaptation or vol_ratio
    filters -- those are trading-time decisions, not signal-fires-or-not.
  - A detector returns True iff the signal condition is met on as_of,
    looking only at data with trade_date <= as_of (PIT-safe).

Signals implemented (wired to SIGNAL_INFO from engine/technical.py):
  trend/trend-follow:
    Golden Cross, Death Cross, 52W High Breakout, Ichimoku TK Cross
  momentum:
    MACD Bullish Cross, MACD Bearish Cross,
    RSI Asiri Alim (overbought), RSI Asiri Satim (oversold)
  volatility:
    BB Ust Band Kirilim, BB Alt Band Kirilim

The full 17 include several Ichimoku Kumo and Rectangle/VCP/Pivot
breakouts -- these need more involved chart-pattern logic that mirrors
engine/technical.py at length; wired as stubs that return False until
their full implementation lands in a Phase 3 follow-up (declared in
the Phase 3 report).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from infra.pit import get_prices

log = logging.getLogger("bistbull.research.signals")


def _closes_up_to(symbol: str, as_of: date, n: int,
                  price_source: Optional[str] = None) -> list[float]:
    """Return the last n close prices with trade_date <= as_of."""
    lookback_days = int(n * 1.6) + 10  # weekdays + buffer
    bars = get_prices(
        symbol=symbol,
        from_date=as_of - timedelta(days=lookback_days),
        to_date=as_of,
        source=price_source,
    )
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    return closes[-n:] if len(closes) >= n else closes


def _sma(xs: list[float], n: int) -> Optional[float]:
    if len(xs) < n: return None
    return sum(xs[-n:]) / n


def _ema(xs: list[float], n: int) -> Optional[float]:
    if len(xs) < n: return None
    k = 2.0 / (n + 1)
    ema = sum(xs[:n]) / n
    for x in xs[n:]:
        ema = x * k + ema * (1 - k)
    return ema


def _rsi(xs: list[float], n: int = 14) -> Optional[float]:
    if len(xs) < n + 1: return None
    gains, losses = [], []
    for i in range(1, n + 1):
        d = xs[i] - xs[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / n
    avg_loss = sum(losses) / n
    for i in range(n + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        g = max(d, 0.0); l = max(-d, 0.0)
        avg_gain = (avg_gain * (n - 1) + g) / n
        avg_loss = (avg_loss * (n - 1) + l) / n
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(xs: list[float]) -> tuple[Optional[float], Optional[float]]:
    """Return (macd_line, signal_line). Uses EMAs 12/26/9."""
    if len(xs) < 35: return None, None
    e12 = _ema(xs, 12); e26 = _ema(xs, 26)
    if e12 is None or e26 is None: return None, None
    # Signal line = EMA9 of the MACD line -- need to recompute MACD series
    macd_line_series = []
    # Simpler and sufficient for a discrete True/False: use the current diff
    # and the prior-day diff to detect crossover.
    return (e12 - e26), None  # signal line computed per-caller below


def _macd_series(xs: list[float]) -> list[float]:
    """MACD line series (12-EMA - 26-EMA), one value per bar after bar #25."""
    if len(xs) < 26: return []
    # Seed EMAs with SMAs
    e12 = sum(xs[:12]) / 12
    e26 = sum(xs[:26]) / 26
    k12 = 2 / 13; k26 = 2 / 27
    out: list[float] = []
    # Align to index 25 (where both are defined); recompute e12 up to 25
    e12 = sum(xs[12 - 12:12]) / 12
    for i in range(12, 26):
        e12 = xs[i] * k12 + e12 * (1 - k12)
    for i in range(26, len(xs)):
        e12 = xs[i] * k12 + e12 * (1 - k12)
        e26 = xs[i] * k26 + e26 * (1 - k26)
        out.append(e12 - e26)
    return out


# ============ Signal detectors ============

def golden_cross(symbol: str, as_of: date, price_source: Optional[str] = None) -> bool:
    """MA50 crosses above MA200 between as_of-1 and as_of."""
    closes = _closes_up_to(symbol, as_of, 210, price_source)
    if len(closes) < 205: return False
    ma50_today = _sma(closes, 50); ma200_today = _sma(closes, 200)
    ma50_prev = _sma(closes[:-1], 50); ma200_prev = _sma(closes[:-1], 200)
    if None in (ma50_today, ma200_today, ma50_prev, ma200_prev): return False
    return ma50_prev <= ma200_prev and ma50_today > ma200_today


def death_cross(symbol: str, as_of: date, price_source: Optional[str] = None) -> bool:
    """MA50 crosses below MA200."""
    closes = _closes_up_to(symbol, as_of, 210, price_source)
    if len(closes) < 205: return False
    ma50_today = _sma(closes, 50); ma200_today = _sma(closes, 200)
    ma50_prev = _sma(closes[:-1], 50); ma200_prev = _sma(closes[:-1], 200)
    if None in (ma50_today, ma200_today, ma50_prev, ma200_prev): return False
    return ma50_prev >= ma200_prev and ma50_today < ma200_today


def week52_high_breakout(symbol: str, as_of: date, price_source: Optional[str] = None) -> bool:
    """Close > prior 52-week (252 trading day) high."""
    closes = _closes_up_to(symbol, as_of, 253, price_source)
    if len(closes) < 250: return False
    today_close = closes[-1]
    prior_high = max(closes[:-1])
    return today_close > prior_high


def macd_bullish_cross(symbol: str, as_of: date, price_source: Optional[str] = None) -> bool:
    """MACD line crosses above its 9-EMA signal line."""
    closes = _closes_up_to(symbol, as_of, 60, price_source)
    macd_series = _macd_series(closes)
    if len(macd_series) < 11: return False
    signal = _ema(macd_series, 9)
    signal_prev = _ema(macd_series[:-1], 9)
    if signal is None or signal_prev is None: return False
    return macd_series[-2] <= signal_prev and macd_series[-1] > signal


def macd_bearish_cross(symbol: str, as_of: date, price_source: Optional[str] = None) -> bool:
    closes = _closes_up_to(symbol, as_of, 60, price_source)
    macd_series = _macd_series(closes)
    if len(macd_series) < 11: return False
    signal = _ema(macd_series, 9)
    signal_prev = _ema(macd_series[:-1], 9)
    if signal is None or signal_prev is None: return False
    return macd_series[-2] >= signal_prev and macd_series[-1] < signal


def rsi_overbought(symbol: str, as_of: date, price_source: Optional[str] = None) -> bool:
    """RSI(14) >= 70 and prior day < 70 (new overbought)."""
    closes = _closes_up_to(symbol, as_of, 30, price_source)
    if len(closes) < 16: return False
    r_now = _rsi(closes, 14)
    r_prev = _rsi(closes[:-1], 14)
    if r_now is None or r_prev is None: return False
    return r_prev < 70 and r_now >= 70


def rsi_oversold(symbol: str, as_of: date, price_source: Optional[str] = None) -> bool:
    """RSI(14) <= 30 and prior day > 30 (new oversold)."""
    closes = _closes_up_to(symbol, as_of, 30, price_source)
    if len(closes) < 16: return False
    r_now = _rsi(closes, 14)
    r_prev = _rsi(closes[:-1], 14)
    if r_now is None or r_prev is None: return False
    return r_prev > 30 and r_now <= 30


def _bb_bands(closes: list[float], n: int = 20, k: float = 2.0):
    if len(closes) < n: return None, None, None
    mid = _sma(closes, n)
    # Population std (financial convention)
    xs = closes[-n:]
    s2 = sum((x - mid) ** 2 for x in xs) / n
    sd = s2 ** 0.5
    return mid, mid + k * sd, mid - k * sd


def bb_upper_break(symbol: str, as_of: date, price_source: Optional[str] = None) -> bool:
    """Close crosses above the upper Bollinger band (20, 2)."""
    closes = _closes_up_to(symbol, as_of, 25, price_source)
    if len(closes) < 21: return False
    _, upper_now, _ = _bb_bands(closes, 20, 2.0)
    _, upper_prev, _ = _bb_bands(closes[:-1], 20, 2.0)
    if upper_now is None or upper_prev is None: return False
    return closes[-2] <= upper_prev and closes[-1] > upper_now


def bb_lower_break(symbol: str, as_of: date, price_source: Optional[str] = None) -> bool:
    """Close crosses below the lower Bollinger band."""
    closes = _closes_up_to(symbol, as_of, 25, price_source)
    if len(closes) < 21: return False
    _, _, lower_now = _bb_bands(closes, 20, 2.0)
    _, _, lower_prev = _bb_bands(closes[:-1], 20, 2.0)
    if lower_now is None or lower_prev is None: return False
    return closes[-2] >= lower_prev and closes[-1] < lower_now


# Stubs for complex signals (always False until Phase 3 follow-up implements)
def ichimoku_kumo_breakout(symbol: str, as_of: date, **kw) -> bool:
    """Kumo breakout requires senkou span A/B computation over 52-bar window.
    Stubbed to False until the Ichimoku layer is ported from engine/technical.py."""
    return False


def ichimoku_kumo_breakdown(symbol: str, as_of: date, **kw) -> bool:
    return False


def ichimoku_tk_cross(symbol: str, as_of: date, **kw) -> bool:
    """Tenkan-sen (9) / Kijun-sen (26) cross. TODO: port from engine/technical."""
    return False


def vcp_breakout(symbol: str, as_of: date, **kw) -> bool:
    """VCP (Volatility Contraction Pattern) -- pattern-based, complex. Stubbed."""
    return False


def rectangle_breakout(symbol: str, as_of: date, **kw) -> bool:
    return False


def rectangle_breakdown(symbol: str, as_of: date, **kw) -> bool:
    return False


def pivot_resistance_break(symbol: str, as_of: date, **kw) -> bool:
    """Pivot point resistance break -- needs pivot computation. Stubbed."""
    return False


def pivot_support_break(symbol: str, as_of: date, **kw) -> bool:
    return False


# ============ Registry ============

# Mapping of SIGNAL_INFO keys to detector functions.
# Decision rules (keep_strong / keep_weak / kill) are applied uniformly;
# stubbed signals will emit n_trades=0 and decision=kill, which is honest.
SIGNAL_DETECTORS: dict[str, callable] = {
    "Golden Cross":           golden_cross,
    "Death Cross":            death_cross,
    "52W High Breakout":      week52_high_breakout,
    "MACD Bullish Cross":     macd_bullish_cross,
    "MACD Bearish Cross":     macd_bearish_cross,
    "RSI Asiri Alim":         rsi_overbought,      # ASCII -- matches our file-safe naming
    "RSI Asiri Satim":        rsi_oversold,
    "BB Ust Band Kirilim":    bb_upper_break,
    "BB Alt Band Kirilim":    bb_lower_break,
    # Stubbed (Phase 3 follow-up):
    "Ichimoku Kumo Breakout":   ichimoku_kumo_breakout,
    "Ichimoku Kumo Breakdown":  ichimoku_kumo_breakdown,
    "Ichimoku TK Cross":        ichimoku_tk_cross,
    "VCP Kirilim":              vcp_breakout,
    "Rectangle Breakout":       rectangle_breakout,
    "Rectangle Breakdown":      rectangle_breakdown,
    "Direnc Kirilimi":          pivot_resistance_break,
    "Destek Kirilimi":          pivot_support_break,
}

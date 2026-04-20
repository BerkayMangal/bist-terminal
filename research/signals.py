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
# ==================================================================
# Phase 4 FAZ 4.0.2 — ported from engine/technical.py
# ==================================================================
# The 8 previously-stubbed signals below are ports of the runtime
# CrossHunter's detector logic (compute_ichimoku, detect_vcp,
# detect_rectangle_breakout, find_pivot_levels). Runtime code operates
# on a pandas DataFrame with High/Low/Close columns; detectors here
# operate on a list-of-dicts from infra.pit.get_prices() so the
# validator stays pandas-free.
#
# All detectors are PIT-safe: they only use bars with
# trade_date <= as_of. The "fires on as_of" semantic means the
# condition is newly true today (was false yesterday); matches the
# Phase 3 detectors (golden_cross, macd_bullish_cross, etc.).


def _ohlc_up_to(symbol: str, as_of: date, n: int,
                price_source: Optional[str] = None) -> list[dict]:
    """Return the last n OHLC bars with trade_date <= as_of.

    Returns list of dicts {open, high, low, close, volume, trade_date},
    trimmed to the last n. Used by Ichimoku/VCP/Rectangle/Pivot
    detectors that need more than just close.
    """
    lookback_days = int(n * 1.6) + 10
    bars = get_prices(
        symbol=symbol,
        from_date=as_of - timedelta(days=lookback_days),
        to_date=as_of,
        source=price_source,
    )
    return bars[-n:] if len(bars) >= n else bars


def _highs(bars: list[dict]) -> list[float]:
    return [float(b["high"]) for b in bars if b.get("high") is not None]


def _lows(bars: list[dict]) -> list[float]:
    return [float(b["low"]) for b in bars if b.get("low") is not None]


def _closes(bars: list[dict]) -> list[float]:
    return [float(b["close"]) for b in bars if b.get("close") is not None]


def _ichimoku_levels(highs: list[float], lows: list[float],
                     end: int) -> Optional[dict]:
    """Ichimoku {tenkan, kijun, senkou_a, senkou_b} computed on
    highs[:end] / lows[:end] (so end-exclusive index lets us compute
    for both "today" and "yesterday" with the same helper).

    Returns None when we lack the 52-bar span needed for senkou_b.
    Senkou A and B are shifted 26 bars forward in the standard Ichimoku
    definition; here we return the "current cloud" value, which for
    the senkou_a/b series is the level computed 26 bars ago. The
    upstream compute_ichimoku in engine/technical.py does
    ((tenkan+kijun)/2).shift(26), so the level that's visible TODAY is
    the one built from 26 bars ago's tenkan/kijun. Match that.
    """
    if end < 52:
        return None
    h = highs[:end]
    l = lows[:end]

    def _rolling_max(xs, w, i):
        # max of xs[i-w+1 : i+1]
        if i + 1 < w:
            return None
        return max(xs[i - w + 1: i + 1])

    def _rolling_min(xs, w, i):
        if i + 1 < w:
            return None
        return min(xs[i - w + 1: i + 1])

    last = end - 1
    tenkan = (_rolling_max(h, 9, last) + _rolling_min(l, 9, last)) / 2
    kijun  = (_rolling_max(h, 26, last) + _rolling_min(l, 26, last)) / 2
    # Senkou A/B visible today = the values that were shifted 26 bars
    # forward. I.e., the tenkan/kijun computed at index (last - 26).
    senkou_idx = last - 26
    if senkou_idx < 8:  # need tenkan/kijun (9-bar min) at that index
        return None
    tk_senkou = (_rolling_max(h, 9,  senkou_idx) + _rolling_min(l, 9,  senkou_idx)) / 2
    kj_senkou = (_rolling_max(h, 26, senkou_idx) + _rolling_min(l, 26, senkou_idx)) / 2
    senkou_a = (tk_senkou + kj_senkou) / 2
    # Senkou B = 52-bar midpoint, also shifted; need 52 bars ending at senkou_idx
    if senkou_idx + 1 < 52:
        return None
    senkou_b = (_rolling_max(h, 52, senkou_idx) + _rolling_min(l, 52, senkou_idx)) / 2
    return {"tenkan": tenkan, "kijun": kijun,
            "senkou_a": senkou_a, "senkou_b": senkou_b}


def ichimoku_kumo_breakout(symbol: str, as_of: date,
                           price_source: Optional[str] = None) -> bool:
    """Close crosses above the Ichimoku cloud (max(senkou_a, senkou_b)).

    Ported from engine/technical.py:compute_ichimoku. Requires >=80 bars
    (52 lookback + 26 shift offset + some buffer). Cross means prior
    close <= cloud_top and today's close > cloud_top.
    """
    bars = _ohlc_up_to(symbol, as_of, 100, price_source)
    if len(bars) < 80:
        return False
    h, l, c = _highs(bars), _lows(bars), _closes(bars)
    if min(len(h), len(l), len(c)) < 80:
        return False
    cur = _ichimoku_levels(h, l, len(h))
    prev = _ichimoku_levels(h, l, len(h) - 1)
    if cur is None or prev is None:
        return False
    cloud_top_cur = max(cur["senkou_a"], cur["senkou_b"])
    cloud_top_prev = max(prev["senkou_a"], prev["senkou_b"])
    return c[-2] <= cloud_top_prev and c[-1] > cloud_top_cur


def ichimoku_kumo_breakdown(symbol: str, as_of: date,
                            price_source: Optional[str] = None) -> bool:
    """Close crosses below the Ichimoku cloud (min(senkou_a, senkou_b))."""
    bars = _ohlc_up_to(symbol, as_of, 100, price_source)
    if len(bars) < 80:
        return False
    h, l, c = _highs(bars), _lows(bars), _closes(bars)
    if min(len(h), len(l), len(c)) < 80:
        return False
    cur = _ichimoku_levels(h, l, len(h))
    prev = _ichimoku_levels(h, l, len(h) - 1)
    if cur is None or prev is None:
        return False
    cloud_bot_cur = min(cur["senkou_a"], cur["senkou_b"])
    cloud_bot_prev = min(prev["senkou_a"], prev["senkou_b"])
    return c[-2] >= cloud_bot_prev and c[-1] < cloud_bot_cur


def ichimoku_tk_cross(symbol: str, as_of: date,
                      price_source: Optional[str] = None) -> bool:
    """Tenkan-sen (9) crosses above Kijun-sen (26) -- bullish TK cross.

    Ported from engine/technical.py:SIGNAL_INFO where it's marked
    bullish. A bearish TK cross (tenkan crosses below kijun) is not
    one of the 17 SIGNAL_DETECTORS so we don't implement it here.
    """
    bars = _ohlc_up_to(symbol, as_of, 40, price_source)
    if len(bars) < 30:
        return False
    h, l = _highs(bars), _lows(bars)
    if min(len(h), len(l)) < 30:
        return False
    cur = _ichimoku_levels(h, l, len(h))
    prev = _ichimoku_levels(h, l, len(h) - 1)
    # For TK we don't need senkou (26-bar shift); allow fallback
    # if the helper bailed for senkou reasons but tenkan/kijun would
    # otherwise compute. Re-compute them directly here to decouple.
    def _tk(h, l, end):
        if end < 26: return None, None
        tk = (max(h[end-9:end]) + min(l[end-9:end])) / 2
        kj = (max(h[end-26:end]) + min(l[end-26:end])) / 2
        return tk, kj
    tk_cur, kj_cur = _tk(h, l, len(h))
    tk_prev, kj_prev = _tk(h, l, len(h) - 1)
    if None in (tk_cur, kj_cur, tk_prev, kj_prev):
        return False
    return tk_prev <= kj_prev and tk_cur > kj_cur


def vcp_breakout(symbol: str, as_of: date,
                 price_source: Optional[str] = None) -> bool:
    """Volatility Contraction Pattern breakout.

    Ported from engine/technical.py:detect_vcp:
      - ATR(5) < ATR(20) * 0.85   -- recent volatility contracting
      - ATR(20) < ATR(50) * 0.90
      - Close > max(high[-5:]) * 0.998  -- breakout of recent high
    ATR here is simple mean of true range (matches the engine).
    """
    bars = _ohlc_up_to(symbol, as_of, 60, price_source)
    if len(bars) < 50:
        return False
    h, l, c = _highs(bars), _lows(bars), _closes(bars)
    if min(len(h), len(l), len(c)) < 50:
        return False
    # True range series
    tr: list[float] = []
    for i in range(1, len(c)):
        prev_c = c[i - 1]
        tr.append(max(h[i] - l[i],
                     abs(h[i] - prev_c),
                     abs(l[i] - prev_c)))
    if len(tr) < 50:
        return False
    atr_5 = sum(tr[-5:]) / 5
    atr_20 = sum(tr[-20:]) / 20
    atr_50 = sum(tr[-50:]) / 50
    if atr_50 == 0:
        return False
    contracting = atr_5 < atr_20 * 0.85 and atr_20 < atr_50 * 0.90
    recent_high = max(h[-5:])
    breakout = c[-1] > recent_high * 0.998
    return contracting and breakout


def _rectangle_signal(bars: list[dict], direction: str) -> bool:
    """Shared logic for Rectangle Breakout / Breakdown.

    Ported from engine/technical.py:detect_rectangle_breakout with a
    correction: the runtime code uses the last 20 bars INCLUDING today,
    which means today's breakout high itself pulls range_high up so
    the 'break above range_high * 0.998' check is noisy. For Phase 3's
    backtest semantics (fires-on-as_of-date), we want the rectangle to
    be defined by the 20 bars BEFORE today, and today's close to
    cross it. Uses bars[-21:-1] for the range + bars[-1] for the
    breakout candidate.

    A 'rectangle' = range_pct < 8% over the prior 20 bars.
    Breakout = close > range_high * 0.998 (bullish) or
               close < range_low  * 1.002 (bearish),
    with the prior day still inside the range.
    """
    if len(bars) < 22:  # need 20 range + yesterday + today
        return False
    h, l, c = _highs(bars), _lows(bars), _closes(bars)
    if min(len(h), len(l), len(c)) < 22:
        return False
    range_bars_high = h[-22:-1]   # 20 bars ending yesterday
    range_bars_low = l[-22:-1]
    range_high = max(range_bars_high[-20:])
    range_low = min(range_bars_low[-20:])
    if range_low == 0:
        return False
    range_pct = (range_high - range_low) / range_low
    if range_pct >= 0.08:
        return False
    price = c[-1]
    prev_price = c[-2]
    if direction == "bullish":
        return prev_price <= range_high * 0.998 and price > range_high * 0.998
    else:  # bearish
        return prev_price >= range_low * 1.002 and price < range_low * 1.002


def rectangle_breakout(symbol: str, as_of: date,
                       price_source: Optional[str] = None) -> bool:
    bars = _ohlc_up_to(symbol, as_of, 26, price_source)
    return _rectangle_signal(bars, "bullish")


def rectangle_breakdown(symbol: str, as_of: date,
                        price_source: Optional[str] = None) -> bool:
    bars = _ohlc_up_to(symbol, as_of, 26, price_source)
    return _rectangle_signal(bars, "bearish")


def _pivot_levels(bars: list[dict],
                  lookback: int = 60) -> tuple[Optional[float], Optional[float]]:
    """Fractal-based pivot resistance/support over the last `lookback` bars.

    Ported from engine/technical.py:find_pivot_levels.
      - A fractal pivot high: high[i] == max(high[i-3..i+3])
      - A fractal pivot low:  low[i]  == min(low[i-3..i+3])
      - Resistance = max pivot high, Support = min pivot low.
    """
    if len(bars) < lookback:
        return None, None
    h = _highs(bars[-lookback:])
    l = _lows(bars[-lookback:])
    if min(len(h), len(l)) < lookback:
        return None, None
    ph: list[float] = []
    pl: list[float] = []
    for i in range(3, len(h) - 3):
        if h[i] == max(h[i - 3: i + 4]):
            ph.append(h[i])
        if l[i] == min(l[i - 3: i + 4]):
            pl.append(l[i])
    return (max(ph) if ph else None), (min(pl) if pl else None)


def pivot_resistance_break(symbol: str, as_of: date,
                           price_source: Optional[str] = None) -> bool:
    """'Direnç Kırılımı' -- close crosses above the fractal-pivot
    resistance from the prior 60-bar window.

    Uses the 60 bars BEFORE today to identify the resistance level, so
    today's close crossing it is a fresh breakout (not the same bar
    that defined the level). Fires when prev close was at/below the
    level and today's close is above it.
    """
    bars = _ohlc_up_to(symbol, as_of, 61, price_source)
    if len(bars) < 61:
        return False
    # Use bars[0:60] to define the level; bars[60] is today, bars[59] is yesterday.
    resistance, _ = _pivot_levels(bars[:60], lookback=60)
    if resistance is None:
        return False
    c = _closes(bars)
    if len(c) < 61:
        return False
    return c[-2] <= resistance and c[-1] > resistance


def pivot_support_break(symbol: str, as_of: date,
                        price_source: Optional[str] = None) -> bool:
    """'Destek Kırılımı' -- close crosses below the fractal-pivot support."""
    bars = _ohlc_up_to(symbol, as_of, 61, price_source)
    if len(bars) < 61:
        return False
    _, support = _pivot_levels(bars[:60], lookback=60)
    if support is None:
        return False
    c = _closes(bars)
    if len(c) < 61:
        return False
    return c[-2] >= support and c[-1] < support


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

"""Cross-sectional signal ranking (Phase 4 FAZ 4.4).

Reviewer Q3 decision: NO stock-level bias (OYAKC's MACD +22% at n=8
is not a reliable single-point weight). Instead, a DYNAMIC cross-
sectional rank captures "which stock is exhibiting this signal
strongest TODAY" without fitting to tiny samples.

For each (signal, as_of), compute a signal-strength scalar per
symbol (how strongly the signal is firing) and rank these across
the universe. The top 30% get full calibrated weight, bottom 30%
get zero, middle 40% linear interpolation. Intent: OYAKC today
gets included IF its MACD is currently strong; tomorrow if GARAN
is stronger, GARAN gets the weight.

Signal strengths (0.0 = not firing; 1.0 = very strong):
  52W High Breakout  -- close / 252d_high ratio (closer to 1 = stronger)
  Golden/Death Cross -- |MA50 - MA200| / MA200 (gap magnitude)
  RSI Asiri Alim     -- (rsi_14 - 70) / 30, clipped to [0,1]
  RSI Asiri Satim    -- (30 - rsi_14) / 30, clipped to [0,1]
  MACD Bullish/Bear. -- |macd_histogram| / close (proportional height)
  BB Ust/Alt Band    -- |close - upper_band| / upper_band (distance past)
  Ichimoku Kumo/TK   -- |close - cloud_top| / cloud_top (distance past)
  VCP                -- contraction ratio (ATR5 / ATR50, inverted)
  Rectangle          -- |close - range_bound| / range_bound
  Pivot              -- |close - pivot_level| / pivot_level

All compute from OHLC bars via infra.pit.get_prices. Stocks with
insufficient data return None (excluded from the ranking pool).

Rank output:
  cs_rank_pct(symbol, signal, as_of) -> float in [0, 1] or None
  - 0.0 = weakest strength in universe today
  - 1.0 = strongest strength
  - None = insufficient data or signal not applicable

Weight modulation (applied AFTER calibrated weight lookup):
  rank >= 0.7  -> 1.0 × calibrated_weight  (full weight)
  rank <= 0.3  -> 0.0 × calibrated_weight  (skip)
  0.3 < rank < 0.7 -> linear ramp:
      multiplier = (rank - 0.3) / 0.4
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Optional, Union

from infra.pit import get_prices, get_universe_at

log = logging.getLogger("bistbull.ranking")

DateLike = Union[str, date]

FULL_WEIGHT_CUTOFF = 0.7
ZERO_WEIGHT_CUTOFF = 0.3


# ==========================================================================
# Signal-strength primitives (per signal type)
# ==========================================================================

def _to_date(d: DateLike) -> date:
    if isinstance(d, date):
        return d
    from datetime import datetime
    return datetime.fromisoformat(str(d)[:10]).date()


def _ohlc(symbol: str, as_of: date, n: int,
          source: Optional[str] = None) -> list[dict]:
    """Last n bars ending at or before as_of."""
    lookback = int(n * 1.6) + 10  # weekend/holiday buffer
    bars = get_prices(
        symbol=symbol,
        from_date=as_of - timedelta(days=lookback),
        to_date=as_of, source=source,
    )
    return bars[-n:] if len(bars) >= n else bars


def _sma(xs: list[float], n: int) -> Optional[float]:
    if len(xs) < n: return None
    return sum(xs[-n:]) / n


def _ema(xs: list[float], n: int) -> Optional[float]:
    if len(xs) < n: return None
    alpha = 2.0 / (n + 1)
    v = xs[-n]
    for x in xs[-n + 1:]:
        v = alpha * x + (1 - alpha) * v
    return v


def _rsi(closes: list[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1: return None
    gains, losses = [], []
    for i in range(len(closes) - n, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-delta)
    avg_gain = sum(gains) / n
    avg_loss = sum(losses) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ==========================================================================
# Signal-strength: one function per signal type
# ==========================================================================

def _strength_52w_high_breakout(symbol: str, as_of: date,
                                 source: Optional[str] = None) -> Optional[float]:
    """Close / 252-bar high. Closer to 1 = stronger breakout imminence."""
    bars = _ohlc(symbol, as_of, 252, source)
    if len(bars) < 252:
        return None
    highs = [float(b["high"]) for b in bars if b.get("high") is not None]
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    if not highs or not closes:
        return None
    hi = max(highs)
    if hi <= 0:
        return None
    # Close can occasionally exceed prior-period high after a breakout;
    # clip to [0, 1].
    return _clip01(closes[-1] / hi)


def _strength_trend_cross(symbol: str, as_of: date,
                          source: Optional[str] = None) -> Optional[float]:
    """Gap magnitude |MA50 - MA200| / MA200. Used for Golden/Death Cross."""
    bars = _ohlc(symbol, as_of, 210, source)
    if len(bars) < 210:
        return None
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    ma50 = _sma(closes, 50)
    ma200 = _sma(closes, 200)
    if ma50 is None or ma200 is None or ma200 == 0:
        return None
    # Larger gap = stronger cross. No upper cap; clip to [0, 1] with 0.2
    # (20%) as the "very strong" anchor.
    return _clip01(abs(ma50 - ma200) / ma200 / 0.2)


def _strength_rsi_overbought(symbol: str, as_of: date,
                              source: Optional[str] = None) -> Optional[float]:
    """(RSI - 70) / 30, clipped to [0,1]. RSI=70 -> 0, RSI=100 -> 1."""
    bars = _ohlc(symbol, as_of, 30, source)
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    rsi = _rsi(closes)
    if rsi is None:
        return None
    if rsi < 70:
        return 0.0
    return _clip01((rsi - 70) / 30)


def _strength_rsi_oversold(symbol: str, as_of: date,
                            source: Optional[str] = None) -> Optional[float]:
    """(30 - RSI) / 30, clipped to [0,1]. RSI=30 -> 0, RSI=0 -> 1."""
    bars = _ohlc(symbol, as_of, 30, source)
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    rsi = _rsi(closes)
    if rsi is None:
        return None
    if rsi > 30:
        return 0.0
    return _clip01((30 - rsi) / 30)


def _strength_macd(symbol: str, as_of: date,
                   source: Optional[str] = None,
                   bullish: bool = True) -> Optional[float]:
    """|MACD histogram| / close. 0.02 (2% of price) = full strength."""
    bars = _ohlc(symbol, as_of, 60, source)
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    if len(closes) < 35:
        return None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    if ema12 is None or ema26 is None:
        return None
    macd_line = ema12 - ema26
    # Signal EMA(9) of MACD line -- build the series
    macd_series: list[float] = []
    for i in range(26, len(closes)):
        window = closes[: i + 1]
        e12 = _ema(window, 12)
        e26 = _ema(window, 26)
        if e12 is None or e26 is None:
            continue
        macd_series.append(e12 - e26)
    if len(macd_series) < 9:
        return None
    signal = _ema(macd_series, 9)
    if signal is None:
        return None
    histogram = macd_line - signal
    if (bullish and histogram <= 0) or (not bullish and histogram >= 0):
        return 0.0
    price = closes[-1]
    if price <= 0:
        return None
    return _clip01(abs(histogram) / price / 0.02)


def _strength_bollinger(symbol: str, as_of: date,
                        source: Optional[str] = None,
                        upper: bool = True) -> Optional[float]:
    """Distance from close to band, normalized by band value.
    5% past the band = full strength."""
    bars = _ohlc(symbol, as_of, 25, source)
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    if len(closes) < 20:
        return None
    ma20 = _sma(closes, 20)
    if ma20 is None: return None
    tail = closes[-20:]
    var = sum((x - ma20) ** 2 for x in tail) / (len(tail) - 1)
    std = math.sqrt(var)
    close = closes[-1]
    if upper:
        band = ma20 + 2 * std
        if band <= 0 or close <= band:
            return 0.0
        return _clip01((close - band) / band / 0.05)
    else:
        band = ma20 - 2 * std
        if band <= 0 or close >= band:
            return 0.0
        return _clip01((band - close) / band / 0.05)


# Registry: signal-name -> strength function.
STRENGTH_FUNCTIONS: dict[str, callable] = {
    "52W High Breakout":      _strength_52w_high_breakout,
    "Golden Cross":           _strength_trend_cross,
    "Death Cross":            _strength_trend_cross,
    "RSI Asiri Alim":         _strength_rsi_overbought,
    "RSI Asiri Satim":        _strength_rsi_oversold,
    "MACD Bullish Cross":     lambda s, d, src=None: _strength_macd(s, d, src, bullish=True),
    "MACD Bearish Cross":     lambda s, d, src=None: _strength_macd(s, d, src, bullish=False),
    "BB Ust Band Kirilim":    lambda s, d, src=None: _strength_bollinger(s, d, src, upper=True),
    "BB Alt Band Kirilim":    lambda s, d, src=None: _strength_bollinger(s, d, src, upper=False),
}


def signal_strength(symbol: str, signal: str, as_of: DateLike,
                    price_source: Optional[str] = None) -> Optional[float]:
    """Compute the numerical strength of `signal` for `symbol` at `as_of`.

    Returns a float in [0, 1] or None if insufficient data / unknown
    signal. The strength is NOT calibrated -- it's a raw indicator
    distance / magnitude that the ranker compares across symbols.
    """
    fn = STRENGTH_FUNCTIONS.get(signal)
    if fn is None:
        return None
    try:
        return fn(symbol, _to_date(as_of), price_source)
    except Exception as e:
        log.debug(f"signal_strength({signal}, {symbol}, {as_of}): {e}")
        return None


# ==========================================================================
# Cross-sectional rank
# ==========================================================================

def cs_rank_pct(
    symbol: str,
    signal: str,
    as_of: DateLike,
    universe: str = "BIST30",
    price_source: Optional[str] = None,
) -> Optional[float]:
    """Cross-sectional percentile rank of `symbol`'s `signal` strength
    within `universe` on `as_of`.

    Returns float in [0, 1] or None if:
      - `symbol` itself has no computable strength
      - fewer than 3 other symbols have a strength (statistical floor)

    Percentile = (rank-1) / (n-1) where rank=1 is the weakest. Top
    symbol gets 1.0, bottom symbol gets 0.0.
    """
    as_of_d = _to_date(as_of)
    members = get_universe_at(universe, as_of_d)
    if symbol not in members:
        return None

    strengths: dict[str, float] = {}
    for m in members:
        v = signal_strength(m, signal, as_of_d, price_source=price_source)
        if v is not None:
            strengths[m] = v

    if symbol not in strengths or len(strengths) < 3:
        return None

    # Percentile rank
    sorted_syms = sorted(strengths.items(), key=lambda kv: kv[1])
    # Find symbol's position (weakest = 0, strongest = len-1)
    rank = next(i for i, (s, _) in enumerate(sorted_syms) if s == symbol)
    n = len(sorted_syms)
    if n == 1:
        return 0.5  # only one symbol; neutral rank
    return rank / (n - 1)


def modulation_factor(rank_pct: Optional[float]) -> float:
    """Convert a rank percentile to a weight multiplier in [0, 1].

    rank >= 0.7 -> 1.0   (top 30% -- full weight)
    rank <= 0.3 -> 0.0   (bottom 30% -- skip)
    0.3 < rank < 0.7 -> linear ramp (rank - 0.3) / 0.4

    None -> 1.0 (no modulation possible -> don't penalize; let the
    calibrated weight alone decide).
    """
    if rank_pct is None:
        return 1.0
    if rank_pct >= FULL_WEIGHT_CUTOFF:
        return 1.0
    if rank_pct <= ZERO_WEIGHT_CUTOFF:
        return 0.0
    return (rank_pct - ZERO_WEIGHT_CUTOFF) / (FULL_WEIGHT_CUTOFF - ZERO_WEIGHT_CUTOFF)


def apply_cs_rank_modulation(
    events: list[dict],
    universe: str = "BIST30",
    price_source: Optional[str] = None,
) -> list[dict]:
    """Attach cs_rank_pct + modulation_factor + modulated_weight to
    each event. Events must have 'symbol', 'signal', 'as_of' and an
    existing 'calibrated_weight' key.

    modulated_weight = calibrated_weight * modulation_factor(rank)

    Returns a new list of enriched event dicts (non-destructive)."""
    out: list[dict] = []
    # Cache rank lookups per (symbol, signal, as_of) -- many events
    # share a day; this is why cs_rank_pct is comparatively expensive.
    cache: dict[tuple, Optional[float]] = {}
    for ev in events:
        sym = ev.get("symbol")
        sig = ev.get("signal")
        as_of = ev.get("as_of")
        if not (sym and sig and as_of):
            out.append({**ev, "cs_rank_pct": None, "modulation_factor": 1.0,
                        "modulated_weight": ev.get("calibrated_weight")})
            continue
        key = (sym, sig, str(as_of)[:10])
        if key not in cache:
            cache[key] = cs_rank_pct(
                sym, sig, as_of,
                universe=universe, price_source=price_source,
            )
        rank = cache[key]
        factor = modulation_factor(rank)
        cw = ev.get("calibrated_weight")
        mw = cw * factor if cw is not None else None
        out.append({
            **ev,
            "cs_rank_pct": rank,
            "modulation_factor": factor,
            "modulated_weight": mw,
        })
    return out

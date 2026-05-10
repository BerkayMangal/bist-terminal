# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# features/bullalfa_features.py
#
# Engines 1–7 (spec §8) — thin predicates / scorers operating on a
# pre-computed `EngineInputs` struct. The struct is built by
# `build_engine_inputs(...)`, which sources existing primitives from
# `engine.technical.compute_technical(...)` and supplements them
# with a small number of derived series (EMA20, rolling highs,
# higher-lows count, up/down volume ratio, BB-width percentiles).
#
# Design notes
# ------------
# * Engines are pure: they read from `EngineInputs` and return small
#   dicts/scalars. No I/O, no caching, no globals. This keeps unit
#   tests deterministic and free of fixtures.
# * Primitives that already exist in `engine.technical` (RSI, ATR,
#   ADX, BB-width-today, vol_ratio) are NOT recomputed — we lift
#   them off the `tech` dict. New primitives the existing module
#   doesn't return (EMA20, breakout highs, etc.) are computed once
#   in `build_engine_inputs` and cached on the dataclass.
# * Per-mode behaviour for E1, E3, E4 lives in
#   `engine.bullalfa_params` so v2 can override without code edits.
# ================================================================

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from engine.bullalfa_params import (
    BULLALFA_PARAMS,
    E5_ATR_TIGHTNESS_RATIO,
    E5_BB_WIDTH_PCTILE_COMPRESS,
    E5_EXPANSION_RANGE_MULT,
    E6_EMA20_TOLERANCE_PCT,
    E6_PANIC_BAR_ATR_MULT,
    E7_PENALTY_CAP,
    E7_RSI_HIGH_THRESHOLD,
    E7_RSI_VERY_HIGH_THRESHOLD,
    E7_RUNUP_5D_THRESHOLD,
    E7_VOL_FADE_RATIO,
    PULLBACK_TO_BREAKOUT_LOOKBACK_BARS,
    TOPLANIYOR_LOOKBACK_BARS,
    breakout_bars,
    is_e5_skipped,
    rvol_threshold,
)


# ----------------------------------------------------------------
# EngineInputs — precomputed primitives consumed by all 7 engines
# ----------------------------------------------------------------

@dataclass(frozen=True)
class EngineInputs:
    """Pre-computed scalars used by Engines 1–7.

    All fields are `Optional[...]` so partial inputs (e.g. a stock
    with no benchmark history) don't crash the engines — they simply
    return conservative outputs.
    """

    # ---- price / position ------------------------------------------------
    price:               Optional[float]
    ema20:               Optional[float]
    ema50:               Optional[float]
    ema200:              Optional[float]
    prior_close:         Optional[float]   # yesterday's close (for UZAK DUR check)
    prior_low:           Optional[float]   # yesterday's low (for UZAK DUR check)

    # ---- returns ---------------------------------------------------------
    return_5d:           Optional[float]
    return_20d:          Optional[float]
    return_60d:          Optional[float]

    # ---- volume ----------------------------------------------------------
    rvol_today:          Optional[float]   # vol_today / vol_avg_20d
    rvol_5d_avg:         Optional[float]
    rvol_3d_ago:         Optional[float]
    up_down_vol_ratio_10d: Optional[float]

    # ---- volatility ------------------------------------------------------
    atr14:               Optional[float]
    atr_avg_20d:         Optional[float]
    bb_width_today:      Optional[float]
    bb_width_60d_p25:    Optional[float]   # 25th percentile of bb_width over 60 bars
    bb_width_60d_p35:    Optional[float]
    bb_width_60d_median: Optional[float]
    range_today:         Optional[float]   # high - low for today's bar
    last5_pullback_ok:   Optional[bool]    # E6 down-vol/no-panic check (precomputed)

    # ---- breakout --------------------------------------------------------
    high_20d:            Optional[float]
    high_55d:            Optional[float]
    high_6m:             Optional[float]
    e4_bars_since_20d:   Optional[int]     # bars since last 20-day-high break, None if never
    e4_bars_since_55d:   Optional[int]
    e4_bars_since_6m:    Optional[int]

    # ---- trend / oscillators --------------------------------------------
    rsi:                 Optional[float]
    adx_today:           Optional[float]
    adx_10d_ago:         Optional[float]
    plus_di:             Optional[float]
    minus_di:            Optional[float]
    higher_lows_count_10d: int = 0

    # ---- relative strength ----------------------------------------------
    bench_return_20d:    Optional[float] = None
    bench_return_60d:    Optional[float] = None
    benchmark:           str = "XU100"

    # ---- sector & state -------------------------------------------------
    sector_group:        str = "sanayi"
    short_history:       bool = False
    bars_available:      int = 0
    # ---- BB width 60-bar percentile rank (used by §18 TOPLANIYOR text) ---
    bb_width_60d_pctile: Optional[float] = None  # 0-100 — rank of bb_width_today within 60-bar window


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

def _safe_pct(numerator: float, denominator: float) -> Optional[float]:
    """Return numerator/denominator - 1, or None if denominator is bad."""
    try:
        if denominator is None or numerator is None:
            return None
        d = float(denominator)
        if not math.isfinite(d) or d == 0.0:
            return None
        return float(numerator) / d - 1.0
    except (TypeError, ValueError):
        return None


def _ema(close: pd.Series, span: int) -> Optional[float]:
    """Exponential MA, deterministic (adjust=False), at most-recent bar."""
    if close is None or len(close) < span:
        return None
    val = close.ewm(span=span, adjust=False, min_periods=span).mean().iloc[-1]
    if pd.isna(val):
        return None
    return float(val)


def _rolling_max(s: pd.Series, window: int) -> Optional[float]:
    if s is None or len(s) < window:
        return None
    return float(s.tail(window).max())


def _bars_since_breakout(s: pd.Series, window: int) -> Optional[int]:
    """How many bars ago a `window`-day high was last broken.

    A breakout is a bar whose Close is strictly greater than the max
    Close of the prior `window` bars. Returns 0 if today's bar is
    itself a breakout, 1 if yesterday was, etc. Returns None if no
    breakout in the available history.
    """
    if s is None or len(s) < window + 1:
        return None
    # For each bar i ≥ window, prior_max = max(close[i-window..i-1])
    closes = s.values
    n = len(closes)
    # Walk backwards from the last bar
    for k in range(n - 1, window - 1, -1):
        prior_max = float(np.max(closes[k - window:k]))
        if closes[k] > prior_max:
            return n - 1 - k
    return None


def _percentile(values: pd.Series, q: float) -> Optional[float]:
    """q in [0, 100]."""
    if values is None or len(values) == 0:
        return None
    arr = values.dropna().values
    if len(arr) == 0:
        return None
    return float(np.percentile(arr, q))


def _bb_width_series(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.Series:
    """Bollinger band width (relative). Mirrors compute_bollinger_bands but
    returns the rolling series rather than just the last value."""
    if close is None or len(close) < period:
        return pd.Series([], dtype=float)
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    upper = ma + std_dev * sd
    lower = ma - std_dev * sd
    width = (upper - lower) / ma.replace(0, np.nan)
    return width


def _higher_lows_count(low: pd.Series, lookback: int) -> int:
    """Count of bars within last `lookback` whose low is strictly above
    the previous bar's low. Conservative: lookback compares pairs, so
    the maximum return is lookback-1."""
    if low is None or len(low) < 2:
        return 0
    tail = low.tail(min(len(low), lookback)).values
    count = 0
    for i in range(1, len(tail)):
        if tail[i] > tail[i - 1]:
            count += 1
    return count


def _up_down_vol_ratio(df: pd.DataFrame, lookback: int) -> Optional[float]:
    """Volume on up-days / Volume on down-days, over the last `lookback` bars.
    Returns None if either side is empty."""
    if df is None or len(df) < 2:
        return None
    sub = df.tail(min(len(df), lookback + 1)).copy()
    sub["change"] = sub["Close"].diff()
    up = sub[sub["change"] > 0]["Volume"].sum()
    dn = sub[sub["change"] < 0]["Volume"].sum()
    if dn <= 0:
        return None if up <= 0 else float("inf")
    return float(up / dn)


def _last5_pullback_ok(df: pd.DataFrame, atr_avg_20d: Optional[float]) -> Optional[bool]:
    """E6 components 3+4: down-day volume < up-day volume in last 5 bars,
    AND no bar with intraday range > 2×ATR_avg_20d."""
    if df is None or len(df) < 6 or atr_avg_20d is None or atr_avg_20d <= 0:
        return None
    last5 = df.tail(5).copy()
    # Component 3: down-day vol < up-day vol (sums)
    last5["change"] = last5["Close"].diff()
    up_vol = last5[last5["change"] > 0]["Volume"].sum()
    dn_vol = last5[last5["change"] < 0]["Volume"].sum()
    vol_ok = up_vol > dn_vol
    # Component 4: no panic bar
    ranges = (last5["High"] - last5["Low"]).values
    panic = bool((ranges > E6_PANIC_BAR_ATR_MULT * atr_avg_20d).any())
    return bool(vol_ok and not panic)


# ----------------------------------------------------------------
# build_engine_inputs — orchestrator-side primitive precomputation
# ----------------------------------------------------------------

def build_engine_inputs(
    *,
    hist_df: pd.DataFrame,
    tech: dict[str, Any] | None,
    bench_df: Optional[pd.DataFrame],
    sector_group: str,
    benchmark: str = "XU100",
    short_history: bool = False,
) -> EngineInputs:
    """Construct an EngineInputs from the raw OHLCV history + the
    output of `engine.technical.compute_technical`.

    Parameters
    ----------
    hist_df:
        OHLCV DataFrame for the stock — at minimum columns
        Open / High / Low / Close / Volume, indexed in ascending
        time order. Must contain the most recent bar at the end.
    tech:
        Result dict from `compute_technical(...)`. May be None if
        the tech call failed; in that case we degrade gracefully —
        all derived oscillator/ATR fields are left as None.
    bench_df:
        OHLCV DataFrame for the benchmark index. Used solely for
        Engine 2 returns. Optional.
    sector_group, benchmark, short_history:
        Surfaced on the EngineInputs so engines that branch on
        sector/state don't need a separate argument.
    """
    if hist_df is None or len(hist_df) == 0:
        # Defensive: orchestrator should not call us without data,
        # but if it does, return an inputs struct that fails every
        # engine cleanly.
        return EngineInputs(
            price=None, ema20=None, ema50=None, ema200=None,
            prior_close=None, prior_low=None,
            return_5d=None, return_20d=None, return_60d=None,
            rvol_today=None, rvol_5d_avg=None, rvol_3d_ago=None,
            up_down_vol_ratio_10d=None,
            atr14=None, atr_avg_20d=None,
            bb_width_today=None, bb_width_60d_p25=None,
            bb_width_60d_p35=None, bb_width_60d_median=None,
            range_today=None, last5_pullback_ok=None,
            high_20d=None, high_55d=None, high_6m=None,
            e4_bars_since_20d=None, e4_bars_since_55d=None,
            e4_bars_since_6m=None,
            rsi=None, adx_today=None, adx_10d_ago=None,
            plus_di=None, minus_di=None,
            higher_lows_count_10d=0,
            bench_return_20d=None, bench_return_60d=None,
            benchmark=benchmark,
            sector_group=sector_group,
            short_history=short_history,
            bars_available=0,
        )

    close = hist_df["Close"]
    high  = hist_df["High"]
    low   = hist_df["Low"]
    vol   = hist_df["Volume"]
    n     = len(hist_df)

    price = float(close.iloc[-1]) if n > 0 else None
    prior_close = float(close.iloc[-2]) if n >= 2 else None
    prior_low   = float(low.iloc[-2])   if n >= 2 else None

    # Returns (relative)
    ret_5d = _safe_pct(close.iloc[-1], close.iloc[-6])  if n >= 6  else None
    ret_20d = _safe_pct(close.iloc[-1], close.iloc[-21]) if n >= 21 else None
    ret_60d = _safe_pct(close.iloc[-1], close.iloc[-61]) if n >= 61 else None

    # EMAs
    ema20  = _ema(close, 20)
    ema50  = _ema(close, 50)
    ema200 = _ema(close, 200)

    # Volume relatives
    if n >= 21:
        vol_avg_20d = float(vol.tail(21).iloc[:-1].mean())  # 20 bars before today
    else:
        vol_avg_20d = float(vol.mean()) if n > 0 else 0.0
    vol_today = float(vol.iloc[-1]) if n > 0 else 0.0
    rvol_today = (vol_today / vol_avg_20d) if vol_avg_20d > 0 else None

    rvol_5d_avg = None
    if n >= 25 and vol_avg_20d > 0:
        last5_vol = vol.tail(5)
        rvol_5d_avg = float((last5_vol / vol_avg_20d).mean())

    rvol_3d_ago = None
    if n >= 23 and vol_avg_20d > 0:
        # rvol from 3 bars ago
        rvol_3d_ago = float(vol.iloc[-4] / vol_avg_20d)

    ud_ratio = _up_down_vol_ratio(hist_df, TOPLANIYOR_LOOKBACK_BARS)

    # Volatility — leverage existing tech dict where possible
    atr14   = (tech or {}).get("atr")
    bb_today = (tech or {}).get("bb_width")

    # ATR rolling-20 mean (existing module returns only "today's" ATR)
    if atr14 is not None and n >= 21:
        # Build a quick rolling-true-range series to get atr_avg_20d.
        # True range = max(H-L, |H-prev_C|, |L-prev_C|).
        h = high.values
        l = low.values
        c = close.values
        tr = np.zeros(n)
        tr[0] = h[0] - l[0]
        for i in range(1, n):
            tr[i] = max(
                h[i] - l[i],
                abs(h[i] - c[i - 1]),
                abs(l[i] - c[i - 1]),
            )
        atr_avg_20d = float(np.mean(tr[-21:-1])) if n >= 21 else None
    else:
        atr_avg_20d = None

    # BB-width 60-bar stats
    bb_series = _bb_width_series(close, period=20, std_dev=2.0)
    bb_60 = bb_series.tail(60).dropna() if len(bb_series) > 0 else pd.Series([], dtype=float)
    bb_p25 = _percentile(bb_60, 25.0) if len(bb_60) > 0 else None
    bb_p35 = _percentile(bb_60, 35.0) if len(bb_60) > 0 else None
    bb_med = _percentile(bb_60, 50.0) if len(bb_60) > 0 else None
    if bb_today is None and len(bb_series) > 0:
        bb_today = float(bb_series.iloc[-1]) if not pd.isna(bb_series.iloc[-1]) else None
    # Today's percentile rank within the 60-bar window (0=tightest, 100=widest).
    if bb_today is not None and len(bb_60) >= 5:
        try:
            below = float((bb_60 < bb_today).sum())
            bb_60d_pctile = round(below / float(len(bb_60)) * 100.0, 1)
        except Exception:
            bb_60d_pctile = None
    else:
        bb_60d_pctile = None

    # Today's range
    range_today = float(high.iloc[-1] - low.iloc[-1]) if n > 0 else None

    # Last-5 pullback components 3+4 (E6)
    last5_ok = _last5_pullback_ok(hist_df, atr_avg_20d)

    # Breakout highs & bars-since
    high_20d = _rolling_max(close.iloc[:-1], 20)  # prior 20 bars (not including today)
    high_55d = _rolling_max(close.iloc[:-1], 55) if n > 56 else None
    high_6m  = _rolling_max(close.iloc[:-1], 126) if n > 127 else None

    e4_20 = _bars_since_breakout(close, 20)
    e4_55 = _bars_since_breakout(close, 55) if n > 56 else None
    e4_6m = _bars_since_breakout(close, 126) if n > 127 else None

    # Trend / oscillators (lift from tech dict)
    rsi      = (tech or {}).get("rsi")
    adx_now  = (tech or {}).get("adx")
    plus_di  = (tech or {}).get("plus_di")
    minus_di = (tech or {}).get("minus_di")

    # ADX 10 bars ago — recompute lightweight (Wilder smoothing matches
    # engine.technical). For simplicity we use a 14-period ADX shifted
    # back 10 bars, computed inline.
    adx_10d_ago = None
    if n >= 14 + 10:
        adx_10d_ago = _wilder_adx_n_bars_ago(hist_df, period=14, n_back=10)

    # Higher-lows count over last lookback
    hl_count = _higher_lows_count(low, TOPLANIYOR_LOOKBACK_BARS)

    # Benchmark returns
    bench_ret_20 = None
    bench_ret_60 = None
    if bench_df is not None and len(bench_df) > 0 and "Close" in bench_df.columns:
        bc = bench_df["Close"]
        bn = len(bc)
        if bn >= 21:
            bench_ret_20 = _safe_pct(bc.iloc[-1], bc.iloc[-21])
        if bn >= 61:
            bench_ret_60 = _safe_pct(bc.iloc[-1], bc.iloc[-61])

    return EngineInputs(
        price=price,
        ema20=ema20, ema50=ema50, ema200=ema200,
        prior_close=prior_close, prior_low=prior_low,
        return_5d=ret_5d, return_20d=ret_20d, return_60d=ret_60d,
        rvol_today=rvol_today, rvol_5d_avg=rvol_5d_avg, rvol_3d_ago=rvol_3d_ago,
        up_down_vol_ratio_10d=ud_ratio,
        atr14=atr14, atr_avg_20d=atr_avg_20d,
        bb_width_today=bb_today,
        bb_width_60d_p25=bb_p25, bb_width_60d_p35=bb_p35, bb_width_60d_median=bb_med,
        range_today=range_today, last5_pullback_ok=last5_ok,
        high_20d=high_20d, high_55d=high_55d, high_6m=high_6m,
        e4_bars_since_20d=e4_20, e4_bars_since_55d=e4_55, e4_bars_since_6m=e4_6m,
        rsi=rsi, adx_today=adx_now, adx_10d_ago=adx_10d_ago,
        plus_di=plus_di, minus_di=minus_di,
        higher_lows_count_10d=hl_count,
        bench_return_20d=bench_ret_20, bench_return_60d=bench_ret_60,
        benchmark=benchmark,
        sector_group=sector_group,
        short_history=short_history,
        bars_available=n,
        bb_width_60d_pctile=bb_60d_pctile,
    )


def _wilder_adx_n_bars_ago(df: pd.DataFrame, period: int = 14, n_back: int = 10) -> Optional[float]:
    """Lightweight Wilder-smoothed ADX, returning the value `n_back` bars
    before the latest. Uses adjust=False for determinism, mirroring
    `engine.technical.compute_adx`."""
    n = len(df)
    if n < period + n_back + 1:
        return None
    high = df["High"].astype(float)
    low  = df["Low"].astype(float)
    close = df["Close"].astype(float)
    h_prev_c = (high - close.shift()).abs()
    l_prev_c = (low - close.shift()).abs()
    tr = pd.concat([(high - low), h_prev_c, l_prev_c], axis=1).max(axis=1)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm  = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    dx = ( (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) ) * 100
    adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    if len(adx) < n_back + 1:
        return None
    val = adx.iloc[-1 - n_back]
    return None if pd.isna(val) else float(val)


# ================================================================
# Engines 1–7 — pure predicates / scorers
# ================================================================

def engine_1_trend(inp: EngineInputs, mode: str) -> int:
    """Trend alignment per mode (spec §8). Returns 0 or 1."""
    if mode == "HIZLI":
        if inp.ema20 is None or inp.ema50 is None or inp.price is None:
            return 0
        return int(inp.ema20 > inp.ema50 and inp.price > inp.ema20)
    if mode == "SWING":
        if inp.ema20 is None or inp.ema50 is None or inp.ema200 is None:
            return 0
        return int(inp.ema20 > inp.ema50 > inp.ema200)
    if mode == "POZİSYON":
        if inp.price is None or inp.ema200 is None:
            return 0
        return int(inp.price > inp.ema200)
    return 0


def engine_2_relstr(inp: EngineInputs) -> dict[str, Any]:
    """Relative strength vs benchmark (spec §8).

    score = 1.0  if both rs_short and rs_long > 0
            0.5  if exactly one is > 0
            0.0  otherwise (or if returns missing)
    """
    rs_short = None
    rs_long = None
    if inp.return_20d is not None and inp.bench_return_20d is not None:
        rs_short = inp.return_20d - inp.bench_return_20d
    if inp.return_60d is not None and inp.bench_return_60d is not None:
        rs_long = inp.return_60d - inp.bench_return_60d

    if rs_short is None and rs_long is None:
        score = 0.0
    else:
        # Treat None side as failing (conservative).
        s_pos = (rs_short is not None and rs_short > 0)
        l_pos = (rs_long  is not None and rs_long  > 0)
        if s_pos and l_pos:
            score = 1.0
        elif s_pos or l_pos:
            score = 0.5
        else:
            score = 0.0

    return {
        "score":    score,
        "rs_short": rs_short,
        "rs_long":  rs_long,
        "benchmark": inp.benchmark,
    }


def engine_3_volume(inp: EngineInputs, mode: str) -> dict[str, Any]:
    """Volume confirmation (spec §8): rvol_today > mode threshold."""
    th = rvol_threshold(mode)
    rvol = inp.rvol_today
    if rvol is None or th is None:
        return {"rvol": rvol, "passed": False, "threshold": th}
    return {"rvol": float(rvol), "passed": bool(rvol > th), "threshold": th}


def engine_4_breakout(inp: EngineInputs, mode: str) -> dict[str, Any]:
    """Breakout per mode (spec §8). Returns the breakout type and
    bars_ago, or (None, None) if no breakout in the lookback window
    appropriate to this mode."""
    if mode == "HIZLI":
        bars = inp.e4_bars_since_20d
        if bars is not None:
            return {"type": "20d", "bars_ago": int(bars)}
    elif mode == "SWING":
        bars = inp.e4_bars_since_55d
        if bars is not None:
            return {"type": "55d", "bars_ago": int(bars)}
    elif mode == "POZİSYON":
        bars = inp.e4_bars_since_6m
        if bars is not None:
            return {"type": "6m", "bars_ago": int(bars)}
    return {"type": None, "bars_ago": None}


def engine_5_compression(inp: EngineInputs) -> dict[str, Any]:
    """Compression → expansion check (spec §8). Skipped for some sectors."""
    if is_e5_skipped(inp.sector_group):
        return {
            "compressed": False,
            "expanded":   False,
            "skipped_reason": f"E5 skipped for sector_group={inp.sector_group}",
        }
    if (
        inp.bb_width_today is None
        or inp.bb_width_60d_p25 is None
        or inp.atr14 is None
        or inp.atr_avg_20d is None
    ):
        return {"compressed": False, "expanded": False, "skipped_reason": "insufficient data"}

    compressed = (
        inp.bb_width_today < inp.bb_width_60d_p25
        and inp.atr14 < inp.atr_avg_20d * E5_ATR_TIGHTNESS_RATIO
    )
    # Expansion: today's range > expansion-mult × atr_avg_20d
    expanded = False
    if inp.range_today is not None and inp.atr_avg_20d > 0:
        expanded = inp.range_today > E5_EXPANSION_RANGE_MULT * inp.atr_avg_20d
    return {"compressed": bool(compressed), "expanded": bool(expanded)}


def engine_6_pullback(inp: EngineInputs, mode: str) -> bool:
    """Pullback quality, all four conditions required (spec §8)."""
    # 1. Trend intact
    if engine_1_trend(inp, mode) != 1:
        return False
    # 2. Price within tolerance of EMA20, from above
    if inp.price is None or inp.ema20 is None or inp.ema20 <= 0:
        return False
    if inp.price < inp.ema20:
        return False
    if (inp.price - inp.ema20) / inp.ema20 > E6_EMA20_TOLERANCE_PCT:
        return False
    # 3+4. Down-day vol < up-day vol last 5 bars AND no panic bar
    if inp.last5_pullback_ok is None or not inp.last5_pullback_ok:
        return False
    return True


def engine_7_exhaustion(inp: EngineInputs) -> float:
    """Exhaustion dampener (spec §8). 0..0.7."""
    penalty = 0.0
    if inp.rsi is not None:
        if inp.rsi > E7_RSI_HIGH_THRESHOLD:
            penalty += BULLALFA_PARAMS["engines"]["e7"]["rsi_high_penalty"]
        if inp.rsi > E7_RSI_VERY_HIGH_THRESHOLD:
            penalty += BULLALFA_PARAMS["engines"]["e7"]["rsi_very_high_penalty"]
    if inp.return_5d is not None and inp.return_5d > E7_RUNUP_5D_THRESHOLD:
        penalty += BULLALFA_PARAMS["engines"]["e7"]["runup_penalty"]
    if (
        inp.rvol_today is not None
        and inp.rvol_3d_ago is not None
        and inp.rvol_today < inp.rvol_3d_ago * E7_VOL_FADE_RATIO
    ):
        penalty += BULLALFA_PARAMS["engines"]["e7"]["vol_fade_penalty"]
    if penalty < 0.0:
        penalty = 0.0
    if penalty > E7_PENALTY_CAP:
        penalty = E7_PENALTY_CAP
    return float(penalty)


# ================================================================
# Tie-breaker — E4 wins over E6 same bar; "Pullback to Breakout"
# ================================================================

def detect_pullback_to_breakout(inp: EngineInputs, mode: str) -> bool:
    """E4 fired ≤ N bars ago AND current bar matches E6 → bonus signal.

    Spec §8 tie-breaker: if both E4 and E6 fire on the same bar, E4
    wins (handled by caller). If E4 fired in the recent past AND
    today's bar is an E6 pullback, that's the special "Pullback to
    Breakout" pattern.
    """
    # Pick the bars-since field appropriate to this mode.
    if mode == "HIZLI":
        bars_since = inp.e4_bars_since_20d
    elif mode == "SWING":
        bars_since = inp.e4_bars_since_55d
    elif mode == "POZİSYON":
        bars_since = inp.e4_bars_since_6m
    else:
        return False
    if bars_since is None:
        return False
    if bars_since == 0:
        # Same bar — E4 wins outright; not Pullback-to-Breakout.
        return False
    if bars_since > PULLBACK_TO_BREAKOUT_LOOKBACK_BARS:
        return False
    return engine_6_pullback(inp, mode)


# ================================================================
# Convenience: compute all engines in one pass for a given mode
# ================================================================

def compute_engines(inp: EngineInputs, mode: str) -> dict[str, Any]:
    """Run all 7 engines for `mode` on `inp` and return a dict in
    BullAlfaSignal `engines` shape (spec §19, less accumulation_strength
    which is a TOPLANIYOR concern handled separately)."""
    e1 = engine_1_trend(inp, mode)
    e2 = engine_2_relstr(inp)
    e3 = engine_3_volume(inp, mode)
    e4 = engine_4_breakout(inp, mode)
    e5 = engine_5_compression(inp)
    e6 = engine_6_pullback(inp, mode)
    e7 = engine_7_exhaustion(inp)
    p2b = detect_pullback_to_breakout(inp, mode)
    return {
        "e1_trend":               e1,
        "e2_relstr": {
            "score":     e2["score"],
            "benchmark": e2["benchmark"],
            "rs_short":  e2["rs_short"],
            "rs_long":   e2["rs_long"],
        },
        "e3_volume": {
            "rvol":      e3["rvol"],
            "passed":    e3["passed"],
            "threshold": e3["threshold"],
        },
        "e4_breakout": {
            "type":     e4["type"],
            "bars_ago": e4["bars_ago"],
        },
        "e5_compression": e5,
        "e6_pullback":     bool(e6),
        "e7_exhaustion":   round(float(e7), 4),
        "pullback_to_breakout": bool(p2b),
    }


__all__ = [
    "EngineInputs",
    "build_engine_inputs",
    "engine_1_trend",
    "engine_2_relstr",
    "engine_3_volume",
    "engine_4_breakout",
    "engine_5_compression",
    "engine_6_pullback",
    "engine_7_exhaustion",
    "detect_pullback_to_breakout",
    "compute_engines",
]

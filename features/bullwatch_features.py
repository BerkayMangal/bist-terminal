# ================================================================
# BULLWATCH FEATURES — Pure feature extraction from raw inputs.
#
# Philosophy:
#   - No I/O, no network, no logging.
#   - Takes already-fetched data (metrics dict + OHLCV DataFrame +
#     optional ownership records) and returns a flat feature dict.
#   - Returns None for any feature that cannot be computed from
#     available data (we never fabricate; downstream scorers handle
#     None as "no signal" rather than "zero signal").
#
# Consumed by:
#   engine/bullwatch.py  (the scoring engine)
#
# All math is deterministic — no randomness, no time-of-day branching.
# ================================================================

from __future__ import annotations

from typing import Any, Optional

try:
    import numpy as np  # noqa: F401 (used by callers via pd)
    import pandas as pd
    _PANDAS = True
except Exception:  # pragma: no cover — exercised only if pandas missing
    pd = None  # type: ignore
    _PANDAS = False


# ----------------------------------------------------------------
# Tunable thresholds — kept here as module constants so the engine
# layer stays focused on scoring logic.
# ----------------------------------------------------------------
FLOAT_MARKET_CAP_CAP_TL: float = 3_000_000_000.0     # Core Watch: float < 3B TL (low-float pump candidates)
EXTENDED_WATCH_CAP_TL: float = 15_000_000_000.0      # Extended Watch: 3-15B TL (mid-cap with possible group activity)
# >15B TL → Institutional/Excluded — not BullWatch's hunting ground

LIQUIDITY_FLOOR_TL: float = 5_000_000.0              # 20d avg traded value
PRICE_CALM_PCT: float = 0.08                         # |5d return| < 8%

# Float-pressure bands (daily volume / floating shares)
FLOAT_PRESSURE_STRONG: float = 0.02
FLOAT_PRESSURE_VERY_STRONG: float = 0.04
FLOAT_PRESSURE_EXTREME: float = 0.06

# Relative-volume bands
RVOL_EARLY: float = 1.5
RVOL_STRONG: float = 2.0


# ----------------------------------------------------------------
# Tiny helpers — kept local because the existing utils.helpers
# module is heavy and we want this file to remain importable in
# isolation for unit testing.
# ----------------------------------------------------------------
def _safe_num(x: Any) -> Optional[float]:
    """Convert to float, returning None for None/NaN/non-numeric."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN check (NaN != NaN)
        return None
    return v


def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """a / b, returning None if either side is None or b is ~0."""
    a_, b_ = _safe_num(a), _safe_num(b)
    if a_ is None or b_ is None or abs(b_) < 1e-12:
        return None
    return a_ / b_


def normalize_free_float(ff: Any) -> Optional[float]:
    """
    yfinance returns free_float in inconsistent units:
      - some stocks: fraction in [0, 1]   (e.g., 0.35)
      - some stocks: percentage in [0, 100] (e.g., 35.0)
      - some stocks: raw multiplier > 100 (data quality issue)

    Always return a float in [0, 1] or None for nonsense values.
    """
    v = _safe_num(ff)
    if v is None:
        return None
    if v <= 0:
        return None
    if v <= 1.0:
        return v             # already a fraction
    if v <= 100.0:
        return v / 100.0     # percentage form
    # > 100 is nonsense — could be a data error or a scaled-shares figure;
    # we refuse to guess
    return None


# ================================================================
# UNIVERSE FILTERS — the gate before scoring even begins.
# ================================================================
def float_market_cap(market_cap: Optional[float],
                     free_float: Optional[float]) -> Optional[float]:
    """
    Float market cap = market_cap * free_float.

    free_float is normalized to [0, 1] — see normalize_free_float().
    """
    mc = _safe_num(market_cap)
    ff = normalize_free_float(free_float)
    if mc is None or ff is None:
        return None
    return mc * ff


def passes_float_cap(market_cap: Optional[float],
                     free_float: Optional[float],
                     cap_tl: float = FLOAT_MARKET_CAP_CAP_TL) -> bool:
    """
    Backward-compatible binary filter: True if float_mcap <= cap_tl.

    NOTE (Phase A.6): Prefer `classify_universe_tier()` for new code.
    This binary view is kept for tests/clients that haven't migrated
    to the tiered model.
    """
    fmc = float_market_cap(market_cap, free_float)
    return fmc is not None and fmc <= cap_tl


def classify_universe_tier(market_cap: Optional[float],
                           free_float: Optional[float],
                           core_cap: float = FLOAT_MARKET_CAP_CAP_TL,
                           extended_cap: float = EXTENDED_WATCH_CAP_TL) -> str:
    """
    Tiered universe classifier (Phase A.6 hygiene patch).

    Returns one of:
      - "core"          float_mcap <= 3B TL — primary BullWatch hunting ground
      - "extended"      3B < float_mcap <= 15B TL — mid-cap, may show group activity
      - "institutional" float_mcap > 15B TL — large-cap, generally outside scope
      - "no_data"       cannot compute (missing market_cap or free_float)

    Both `core` and `extended` are eligible for full Phase A scoring; only
    `institutional` and `no_data` are rejected. The tier is surfaced in
    the result so the UI can render Core / Extended badges separately.
    """
    fmc = float_market_cap(market_cap, free_float)
    if fmc is None:
        return "no_data"
    if fmc <= core_cap:
        return "core"
    if fmc <= extended_cap:
        return "extended"
    return "institutional"


def revenue_to_marketcap(revenue: Optional[float],
                         market_cap: Optional[float]) -> Optional[float]:
    """Annual revenue / market cap. Mirrors metrics['ciro_pd'] when both exist."""
    return _safe_div(revenue, market_cap)


def revenue_mispricing_tier(rev_to_mc: Optional[float]) -> int:
    """0 = no tier, 1 = revenue >= 5x mc, 2 = revenue >= 10x mc."""
    if rev_to_mc is None:
        return 0
    if rev_to_mc >= 10.0:
        return 2
    if rev_to_mc >= 5.0:
        return 1
    return 0


# ================================================================
# OHLCV-derived features — all take a DataFrame with at least
# columns Open/High/Low/Close/Volume.
# ================================================================
def avg_traded_value_20d(df) -> Optional[float]:
    """Mean of (Close * Volume) over the trailing 20 sessions."""
    if not _PANDAS or df is None or len(df) < 5:
        return None
    n = min(20, len(df))
    tail = df.tail(n)
    try:
        tv = (tail["Close"] * tail["Volume"]).mean()
    except Exception:
        return None
    return _safe_num(tv)


def passes_liquidity(df, floor_tl: float = LIQUIDITY_FLOOR_TL) -> bool:
    atv = avg_traded_value_20d(df)
    return atv is not None and atv >= floor_tl


def _complete_bar_idx(df) -> int:
    """
    Return the index offset of the last COMPLETED daily bar.

    yfinance returns daily bars including a partial bar for the current
    trading day when the market is open. That partial bar has incomplete
    volume (only N minutes of the session) and a non-final close —
    using it as "today's" snapshot makes RVOL/float_pressure look
    artificially low and quiet, which crashes BullWatch eligibility.

    Strategy: if the last bar's date is today (in Europe/Istanbul,
    BIST market timezone), it's still forming → return -2 (use the
    previous fully-closed day). Otherwise → return -1.

    This keeps BullWatch results stable across the trading day:
    pre-market, intraday, and post-close all see the same canonical
    last-completed-day data.
    """
    if not _PANDAS or df is None or len(df) < 2:
        return -1
    try:
        last_ts = df.index[-1]
        # yfinance returns tz-naive UTC midnights for daily bars on BIST;
        # treat the date directly without tz gymnastics.
        last_date = last_ts.date() if hasattr(last_ts, "date") else None
        if last_date is None:
            return -1
        # "Today" in BIST timezone (UTC+3, no DST since 2016)
        import datetime as _dt
        today_ist = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=3)).date()
        if last_date >= today_ist:
            # Last bar is today (or future timestamp anomaly) → skip it
            return -2
    except Exception:
        pass
    return -1


def relative_volume(df) -> Optional[float]:
    """today_volume / avg_20d_volume (excluding today).

    "Today" = last completed bar. See _complete_bar_idx().
    """
    if not _PANDAS or df is None or len(df) < 7:
        return None
    idx = _complete_bar_idx(df)
    try:
        today = float(df["Volume"].iloc[idx])
        # 20-day window ending the bar BEFORE our reference bar
        end = idx if idx < 0 else idx
        # Slice prior 20 sessions, excluding the reference day itself
        prior_end = end  # exclusive on the right when negative slicing
        prior_start = prior_end - 20 if prior_end < 0 else prior_end - 20
        # Use loc-style negative slicing safely
        prior = df["Volume"].iloc[max(-len(df), prior_start):prior_end]
        if len(prior) < 5:
            return None
        avg = float(prior.mean())
    except Exception:
        return None
    if avg <= 0:
        return None
    return today / avg


def float_pressure(df, shares_outstanding: Optional[float],
                   free_float: Optional[float]) -> Optional[float]:
    """
    daily_volume / floating_shares.

    floating_shares = shares_outstanding * free_float.
    free_float normalized to [0, 1] via normalize_free_float().
    Uses the last COMPLETED daily bar (skips partial intraday bar).
    """
    if not _PANDAS or df is None or len(df) == 0:
        return None
    so = _safe_num(shares_outstanding)
    ff = normalize_free_float(free_float)
    if so is None or ff is None or so <= 0:
        return None
    floating = so * ff
    if floating <= 0:
        return None
    idx = _complete_bar_idx(df)
    try:
        if idx == -2 and len(df) < 2:
            return None
        vol_today = float(df["Volume"].iloc[idx])
    except Exception:
        return None
    if vol_today <= 0:
        return None
    return vol_today / floating


def price_change_5d(df) -> Optional[float]:
    """Fractional return over the last 5 completed sessions (close-to-close)."""
    if not _PANDAS or df is None or len(df) < 7:
        return None
    idx = _complete_bar_idx(df)
    try:
        # Close 5 sessions ago (relative to last completed bar)
        c0 = float(df["Close"].iloc[idx - 5])
        c1 = float(df["Close"].iloc[idx])
    except Exception:
        return None
    if c0 <= 0:
        return None
    return (c1 - c0) / c0


def is_price_calm(df, threshold: float = PRICE_CALM_PCT) -> bool:
    pc = price_change_5d(df)
    return pc is not None and abs(pc) < threshold


# ----------------------------------------------------------------
# Volatility compression — current ATR/BB-width vs 60d baseline.
# A ratio < 1 means the stock is quieter than its own recent
# history (energy build-up). We return the ratio so the engine can
# reward heavier compression with more points.
# ----------------------------------------------------------------
def _atr(df, period: int = 14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def atr_compression_ratio(df, period: int = 14, lookback: int = 60) -> Optional[float]:
    """Current ATR / median ATR over `lookback` sessions. <1 = compressed."""
    if not _PANDAS or df is None or len(df) < period + lookback:
        return None
    try:
        atr = _atr(df, period)
        cur = float(atr.iloc[-1])
        ref = float(atr.tail(lookback).median())
    except Exception:
        return None
    if ref <= 0 or cur != cur:
        return None
    return cur / ref


def bb_width_compression_ratio(df, period: int = 20,
                               lookback: int = 60) -> Optional[float]:
    """Current Bollinger bandwidth / median BB-width over `lookback` sessions."""
    if not _PANDAS or df is None or len(df) < period + lookback:
        return None
    try:
        close = df["Close"]
        mid = close.rolling(period).mean()
        std = close.rolling(period).std()
        width = (4 * std) / mid           # (upper - lower) / mid
        cur = float(width.iloc[-1])
        ref = float(width.tail(lookback).median())
    except Exception:
        return None
    if ref <= 0 or cur != cur:
        return None
    return cur / ref


# ----------------------------------------------------------------
# Price-action accumulation patterns — each detector is a small
# pure function returning bool. The engine sums them into a 0..N
# raw score and the prominent pattern names are surfaced to the UI.
# ----------------------------------------------------------------
def _last_candle(df) -> Optional[dict]:
    if not _PANDAS or df is None or len(df) < 1:
        return None
    try:
        row = df.iloc[-1]
        o, h, l, c, v = (
            float(row["Open"]), float(row["High"]),
            float(row["Low"]), float(row["Close"]), float(row["Volume"]),
        )
    except Exception:
        return None
    return {"o": o, "h": h, "l": l, "c": c, "v": v,
            "rng": max(h - l, 1e-9), "body": abs(c - o)}


def detect_shakeout_recovery(df) -> bool:
    """
    Long lower wick + strong close + elevated volume.
    Lower wick > 50% of range, close in top 30% of range, vol > 1.3x avg.
    """
    cand = _last_candle(df)
    if cand is None or len(df) < 21:
        return False
    lower_wick = min(cand["o"], cand["c"]) - cand["l"]
    upper_part = cand["h"] - max(cand["o"], cand["c"])
    if lower_wick <= 0:
        return False
    if lower_wick / cand["rng"] < 0.5:
        return False
    # Strong close: top 30% of range
    if (cand["c"] - cand["l"]) / cand["rng"] < 0.7:
        return False
    # Volume confirmation
    avg20 = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 21 else 0.0
    if avg20 <= 0 or cand["v"] < 1.3 * avg20:
        return False
    # The wick should also dominate the upper part (no upper rejection)
    return lower_wick > upper_part


def detect_absorption(df) -> bool:
    """High volume + small body + flat close — supply being absorbed."""
    cand = _last_candle(df)
    if cand is None or len(df) < 21:
        return False
    avg20 = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 21 else 0.0
    if avg20 <= 0 or cand["v"] < 1.5 * avg20:
        return False
    if cand["body"] / cand["rng"] > 0.35:    # too directional
        return False
    # Close near where it opened (within 1.5% of open)
    if cand["o"] <= 0:
        return False
    return abs(cand["c"] - cand["o"]) / cand["o"] < 0.015


def detect_tight_closes(df, n: int = 5, max_spread: float = 0.025) -> bool:
    """
    n consecutive closes clustered within `max_spread` of their mean.
    Default: last 5 closes within ±2.5% of their mean = range compression.
    """
    if not _PANDAS or df is None or len(df) < n:
        return False
    try:
        closes = df["Close"].tail(n).astype(float)
        m = float(closes.mean())
        if m <= 0:
            return False
        spread = float((closes.max() - closes.min()) / m)
    except Exception:
        return False
    return spread <= max_spread


def detect_walk_up_accumulation(df, lookback: int = 10) -> bool:
    """
    Higher lows + controlled pullbacks + volume expansion.
    Heuristic: at least 60% of the trailing `lookback` lows are
    higher than the same-position low `lookback` ago, AND the
    average volume of the most recent half is >= 110% of the prior half.
    """
    if not _PANDAS or df is None or len(df) < lookback * 2 + 1:
        return False
    try:
        recent_lows = df["Low"].tail(lookback).astype(float).reset_index(drop=True)
        prior_lows = (df["Low"].iloc[-(2 * lookback):-lookback]
                      .astype(float).reset_index(drop=True))
        higher = (recent_lows.values > prior_lows.values).sum()
        if higher / lookback < 0.6:
            return False
        recent_vol = float(df["Volume"].tail(lookback).mean())
        prior_vol = float(df["Volume"].iloc[-(2 * lookback):-lookback].mean())
    except Exception:
        return False
    if prior_vol <= 0:
        return False
    return recent_vol >= 1.10 * prior_vol


def detect_price_action_patterns(df) -> dict:
    """
    Run all detectors. Returns a dict:
      {
        "shakeout_recovery": bool,
        "absorption": bool,
        "tight_closes": bool,
        "walk_up": bool,
        "count": int,                  # number of patterns present
        "labels": ["Shakeout Recovery", ...],
      }
    """
    flags = {
        "shakeout_recovery": detect_shakeout_recovery(df),
        "absorption": detect_absorption(df),
        "tight_closes": detect_tight_closes(df),
        "walk_up": detect_walk_up_accumulation(df),
    }
    label_map = {
        "shakeout_recovery": "Shakeout Recovery",
        "absorption": "Absorption",
        "tight_closes": "Tight Closes",
        "walk_up": "Walk-Up Accumulation",
    }
    labels = [label_map[k] for k, v in flags.items() if v]
    return {**flags, "count": len(labels), "labels": labels}


# ================================================================
# OWNERSHIP INTELLIGENCE — placeholder feature with stable contract.
#
# Real ownership data (Tera, BoFA, KAP insider filings, fund books)
# is not yet wired into this repo. We expose a typed function so the
# scoring engine can already consume it; if a caller passes None we
# return zero-signal (`None` score) and a `coverage="none"` flag —
# never fabricated bonuses.
#
# When ownership is wired (e.g. via a future data/ownership.py),
# callers will start passing populated `OwnershipSnapshot` dicts
# and the engine will pick up the signal automatically.
# ================================================================
def ownership_signal(snapshot: Optional[dict]) -> dict:
    """
    Expected snapshot schema (when populated):
        {
            "institutional_buys_30d": int,    # # of qualifying broker buys
            "repeated_institutions":  int,    # # of brokers buying >1 day
            "insider_buys_90d":       int,    # KAP insider purchase events
            "fund_increases":         int,    # # of funds raising stake QoQ
            "as_of":                  "ISO date string",
        }

    Returns:
        {
          "score": float in [0,1] | None,
          "coverage": "none" | "partial" | "full",
          "components": {...echo of snapshot subset...},
          "reasons": ["Tera buying 3 days", ...],
        }
    """
    if not snapshot or not isinstance(snapshot, dict):
        return {"score": None, "coverage": "none", "components": {}, "reasons": []}

    inst = int(snapshot.get("institutional_buys_30d") or 0)
    repeat = int(snapshot.get("repeated_institutions") or 0)
    insider = int(snapshot.get("insider_buys_90d") or 0)
    funds = int(snapshot.get("fund_increases") or 0)

    # Each component contributes up to 0.25; raw signal capped at 1.0.
    inst_score = min(inst / 5.0, 1.0) * 0.25
    repeat_score = min(repeat / 3.0, 1.0) * 0.25
    insider_score = min(insider / 2.0, 1.0) * 0.25
    fund_score = min(funds / 3.0, 1.0) * 0.25
    total = inst_score + repeat_score + insider_score + fund_score

    # Coverage: how many of the four channels are populated at all?
    populated = sum(1 for x in (inst, repeat, insider, funds) if x > 0)
    if populated == 0:
        return {"score": None, "coverage": "none",
                "components": snapshot, "reasons": []}
    coverage = "full" if populated >= 3 else "partial"

    reasons: list[str] = []
    if repeat > 0:
        reasons.append(f"{repeat} institution(s) repeatedly accumulating")
    if insider > 0:
        reasons.append(f"{insider} insider purchase(s) in 90d")
    if funds > 0:
        reasons.append(f"{funds} fund(s) raised stake")
    if inst > 0 and not reasons:
        reasons.append(f"{inst} institutional buy(s) in 30d")

    return {
        "score": round(total, 4),
        "coverage": coverage,
        "components": {
            "institutional_buys_30d": inst,
            "repeated_institutions": repeat,
            "insider_buys_90d": insider,
            "fund_increases": funds,
        },
        "reasons": reasons,
    }

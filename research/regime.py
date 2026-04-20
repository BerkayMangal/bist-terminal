"""Market regime classifier for the BIST index (Phase 4 FAZ 4.1 / Q4).

Per reviewer spec Q4: 2022 Türkiye outlier (emtia rallisi + TL
devalüasyonu + hiperenflasyon) cyclical-not-structural. To surface
outliers like it, every validator event gets a regime tag. Regime
is REPORTING-ONLY, NOT a calibration dimension -- adding a third
dimension to the (signal, sector) calibration would split samples
below the n=20 threshold.

Regime = (trend, vol) tuple:
  trend ∈ {'bull', 'neutral', 'bear'}  -- XU100 50d-MA vs 200d-MA
  vol   ∈ {'low', 'mid', 'high'}       -- XU100 30d rolling std,
                                          bucketed against history

Composite label: f'{trend}_{vol}' (e.g. 'bull_high' captures 2022).

The XU100 benchmark bars must be pre-loaded in price_history_pit
for regime computation to succeed. If benchmark data is missing,
get_regime_at returns a neutral 'unknown_unknown' so the pipeline
still runs (fail-open, not fail-closed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Union

from infra.pit import get_prices

log = logging.getLogger("bistbull.regime")

DateLike = Union[str, date]

# Default benchmark symbol. The Phase 3b backfill used XU100; this
# matches. If the user switches to a different benchmark (XUSIN, etc.)
# they pass it explicitly via benchmark_symbol=.
DEFAULT_BENCHMARK = "XU100"

# Rolling windows for trend and volatility
TREND_FAST_N = 50
TREND_SLOW_N = 200
VOL_WINDOW_N = 30
VOL_RANK_WINDOW_N = 252  # ~1 year to rank current vol against history


@dataclass
class RegimeLabel:
    """Result of get_regime_at."""
    trend: str       # 'bull' | 'neutral' | 'bear' | 'unknown'
    vol: str         # 'low' | 'mid' | 'high' | 'unknown'
    label: str       # f'{trend}_{vol}'
    trend_fast_ma: Optional[float] = None
    trend_slow_ma: Optional[float] = None
    vol_30d: Optional[float] = None


def _to_date(d: DateLike) -> date:
    if isinstance(d, date):
        return d
    from datetime import datetime
    return datetime.fromisoformat(str(d)[:10]).date()


def _rolling_std(closes: list[float], n: int) -> Optional[float]:
    """Sample std of LOG returns of the last n+1 closes."""
    if len(closes) < n + 1:
        return None
    import math
    rets = []
    for i in range(len(closes) - n, len(closes)):
        prev = closes[i - 1]
        cur = closes[i]
        if prev <= 0 or cur <= 0:
            return None
        rets.append(math.log(cur / prev))
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def _rolling_mean(xs: list[float], n: int) -> Optional[float]:
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def get_regime_at(
    as_of: DateLike,
    benchmark_symbol: str = DEFAULT_BENCHMARK,
    price_source: Optional[str] = None,
) -> RegimeLabel:
    """Return the market regime on as_of based on benchmark price history.

    Lookback: TREND_SLOW_N (200d) for trend, VOL_RANK_WINDOW_N (252d)
    for volatility-percentile ranking. If fewer than 200 bars are
    available, returns 'unknown' labels.

    Reporting-only: this tag rides along with every validator event
    but does NOT split the calibration sample. See reviewer Q4.
    """
    as_of_d = _to_date(as_of)
    # Need ~1 year of lookback; widen to 400 calendar days for safety
    bars = get_prices(
        symbol=benchmark_symbol,
        from_date=as_of_d - timedelta(days=400),
        to_date=as_of_d,
        source=price_source,
    )
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]

    if len(closes) < TREND_SLOW_N:
        return RegimeLabel(trend="unknown", vol="unknown",
                           label="unknown_unknown")

    # Trend: 50-MA vs 200-MA
    ma_fast = _rolling_mean(closes, TREND_FAST_N)
    ma_slow = _rolling_mean(closes, TREND_SLOW_N)
    if ma_fast is None or ma_slow is None or ma_slow == 0:
        trend = "unknown"
    else:
        # Bull: 50-MA > 200-MA by more than 1% (noise buffer)
        # Bear: 50-MA < 200-MA by more than 1%
        # Neutral: within 1%
        diff_pct = (ma_fast - ma_slow) / ma_slow
        if diff_pct > 0.01:
            trend = "bull"
        elif diff_pct < -0.01:
            trend = "bear"
        else:
            trend = "neutral"

    # Volatility: rank the current 30d std against the last year of 30d stds
    cur_vol = _rolling_std(closes, VOL_WINDOW_N)
    if cur_vol is None:
        vol = "unknown"
    else:
        # Build the rolling std time series: for each day in the lookback,
        # compute the 30d std ending that day.
        hist_vols: list[float] = []
        if len(closes) >= VOL_WINDOW_N + VOL_RANK_WINDOW_N:
            start = len(closes) - VOL_RANK_WINDOW_N
            for i in range(start, len(closes)):
                window = closes[max(0, i - VOL_WINDOW_N): i + 1]
                v = _rolling_std(window, VOL_WINDOW_N)
                if v is not None:
                    hist_vols.append(v)

        if not hist_vols:
            # Not enough history -- classify by absolute threshold.
            # Daily log-return std 1% ~ 16% annualized is 'low',
            # 2% ~ 32% annualized is 'high'. These are rough but stable
            # defaults for markets without a year of prior data.
            if cur_vol < 0.012:
                vol = "low"
            elif cur_vol > 0.022:
                vol = "high"
            else:
                vol = "mid"
        else:
            # Percentile rank: below 33rd = low, above 66th = high
            sorted_vols = sorted(hist_vols)
            n = len(sorted_vols)
            rank = sum(1 for v in sorted_vols if v <= cur_vol) / n
            if rank < 0.33:
                vol = "low"
            elif rank > 0.66:
                vol = "high"
            else:
                vol = "mid"

    return RegimeLabel(
        trend=trend, vol=vol, label=f"{trend}_{vol}",
        trend_fast_ma=ma_fast, trend_slow_ma=ma_slow, vol_30d=cur_vol,
    )


def annotate_events_with_regime(
    events: list[dict],
    benchmark_symbol: str = DEFAULT_BENCHMARK,
    price_source: Optional[str] = None,
) -> list[dict]:
    """Attach a 'regime' field to each event in-place + return the list.

    events: [{'symbol', 'as_of', ...}, ...]. One regime lookup per
    unique date is cached to avoid repeat benchmark queries -- same
    (trend_ma, vol) doesn't change intra-day.
    """
    cache: dict[str, RegimeLabel] = {}
    out = []
    for ev in events:
        date_key = str(ev["as_of"])[:10]
        if date_key not in cache:
            cache[date_key] = get_regime_at(
                date_key, benchmark_symbol=benchmark_symbol,
                price_source=price_source,
            )
        label = cache[date_key]
        row = dict(ev)
        row["regime"] = label.label
        row["regime_trend"] = label.trend
        row["regime_vol"] = label.vol
        out.append(row)
    return out

"""Forward-return labeler (Phase 3 FAZ 3.2).

Given (symbol, as_of_date), compute returns at horizons 5d / 10d / 20d / 60d.
Survivorship-aware: if symbol was not in the requested universe on
as_of_date, the label is None (so the validator drops it from statistics).

Use cases:
- Backtest a technical signal: 'Golden Cross fired on SYM.IS on
  2021-03-15 -- what did the stock do over the next 20 trading days?'
- Phase 4 calibration: 'for each (symbol, week), what was the next
  week's return?'

All dates are PIT: the labeler only reads prices known on/before the
forward horizon; if the horizon hasn't played out yet, the label is None.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional, Union

from infra.pit import get_price_at_or_before, get_universe_at

log = logging.getLogger("bistbull.research.labeler")

DateLike = Union[str, date]

DEFAULT_HORIZONS = (5, 10, 20, 60)


def _to_date(d: DateLike) -> date:
    if isinstance(d, date):
        return d
    from datetime import datetime
    return datetime.fromisoformat(str(d)[:10]).date()


def compute_forward_returns(
    symbol: str,
    as_of_date: DateLike,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    universe: Optional[str] = "BIST30",
    price_source: Optional[str] = None,
    today: Optional[DateLike] = None,
) -> dict[str, Optional[float]]:
    """Compute N-trading-day-ahead returns for (symbol, as_of_date).

    Survivorship contract (Phase 3 non-negotiable):
      If universe is given and symbol NOT in get_universe_at(universe, as_of),
      return a dict of None values -- the caller drops these. Computing
      forward returns for a symbol that wasn't tradable on as_of_date is
      meaningless (you couldn't have bought it).

    PIT contract:
      today (optional) = current date used as a forward-horizon upper
      bound; if as_of_date + horizon > today, that horizon's value is None
      (the future hasn't happened yet). Defaults to date.today().

    Returns {'return_5d': 0.012, 'return_10d': ..., 'return_20d': ...,
             'return_60d': None (if 60d hasn't materialized yet)}
    Key name: 'return_{N}d'.
    """
    as_of = _to_date(as_of_date)
    today_d = _to_date(today) if today else date.today()

    # Survivorship filter
    if universe is not None:
        members = get_universe_at(universe, as_of)
        if symbol.upper() not in members:
            return {f"return_{n}d": None for n in horizons}

    # Baseline price at/before as_of_date
    start_bar = get_price_at_or_before(symbol, as_of, source=price_source)
    if start_bar is None or not start_bar.get("close"):
        return {f"return_{n}d": None for n in horizons}
    start_px = float(start_bar["close"])
    if start_px <= 0:
        return {f"return_{n}d": None for n in horizons}

    out: dict[str, Optional[float]] = {}
    for h in horizons:
        # Trading-day horizon -> approximate by calendar days (h * 1.4 gives
        # some buffer for weekends, and get_price_at_or_before lands on the
        # most recent trading day). Good-enough for weekly/monthly horizons.
        target = as_of + timedelta(days=int(round(h * 1.4)))
        if target > today_d:
            out[f"return_{h}d"] = None
            continue
        end_bar = get_price_at_or_before(symbol, target, source=price_source)
        if end_bar is None or not end_bar.get("close"):
            out[f"return_{h}d"] = None
            continue
        end_px = float(end_bar["close"])
        out[f"return_{h}d"] = round((end_px / start_px) - 1.0, 6)
    return out


def compute_benchmark_returns(
    benchmark_symbol: str,
    as_of_date: DateLike,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    price_source: Optional[str] = None,
    today: Optional[DateLike] = None,
) -> dict[str, Optional[float]]:
    """Same as compute_forward_returns but without the survivorship gate
    (the benchmark IS the market). For computing IR vs XU100 etc.
    """
    return compute_forward_returns(
        symbol=benchmark_symbol, as_of_date=as_of_date,
        horizons=horizons, universe=None,  # skip filter
        price_source=price_source, today=today,
    )


def batch_label_signals(
    signal_events: list[dict],
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    universe: str = "BIST30",
    benchmark_symbol: Optional[str] = None,
    price_source: Optional[str] = None,
    today: Optional[DateLike] = None,
) -> list[dict]:
    """Label a list of signal events.

    signal_events: [{'symbol': ..., 'as_of': ..., 'signal': ..., ...}, ...]
    Each row gets 'return_{N}d' keys; if benchmark_symbol is given, also
    'excess_{N}d' = return_{N}d - benchmark_{N}d.

    Drops rows where ALL horizons are None (survivorship filter fired or
    no price data). Keeps partial-None rows (e.g. 60d not yet materialized)
    so shorter horizons can still be aggregated.
    """
    out: list[dict] = []
    for ev in signal_events:
        labels = compute_forward_returns(
            symbol=ev["symbol"], as_of_date=ev["as_of"],
            horizons=horizons, universe=universe,
            price_source=price_source, today=today,
        )
        # If every horizon is None, drop (not tradable / no price)
        if all(v is None for v in labels.values()):
            continue

        row = dict(ev)
        row.update(labels)

        if benchmark_symbol:
            bench = compute_benchmark_returns(
                benchmark_symbol, ev["as_of"], horizons,
                price_source=price_source, today=today,
            )
            for h in horizons:
                key = f"return_{h}d"
                bkey = f"return_{h}d"
                ekey = f"excess_{h}d"
                if labels.get(key) is not None and bench.get(bkey) is not None:
                    row[ekey] = round(labels[key] - bench[bkey], 6)
                else:
                    row[ekey] = None
        out.append(row)
    return out

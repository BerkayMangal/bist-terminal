"""Signal validator (Phase 3 FAZ 3.3).

Given a signal detector (symbol, as_of) -> bool and a universe / date
range, validator enumerates events, labels them (via labeler.py),
computes statistics, and emits a decision.

Per-signal output (reports/validator/{name}.json + {name}.md) schema
is defined by the reviewer spec:

    {"signal": ..., "universe": ..., "from": ..., "to": ...,
     "n_trades": N, "hit_rate_20d": ..., "avg_return_20d": ...,
     "t_stat_20d": ..., "sharpe_20d_ann": ..., "ir_vs_xu100": ...,
     "decision": "keep_strong" | "keep_weak" | "kill"}

Decision rules:
  keep_strong  -- Sharpe_20d > 1.0  AND t_stat_20d > 2.0
  keep_weak    -- 0.3 <= Sharpe_20d <= 1.0
  kill         -- Sharpe_20d < 0.3  OR  t_stat_20d < 1.5
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from infra.pit import get_universe_at
from research.labeler import batch_label_signals, DEFAULT_HORIZONS

log = logging.getLogger("bistbull.research.validator")


# A SignalDetector is a function (symbol, as_of_date) -> bool.
# Returns True if the signal fired for that symbol at that date.
SignalDetector = Callable[[str, date], bool]


@dataclass
class ValidatorResult:
    """Machine-readable backtest summary per signal.

    Serializable to JSON with asdict(). decision is the enum rule
    described in the module docstring.
    """
    signal: str
    universe: str
    from_date: str
    to_date: str
    n_trades: int
    hit_rate_5d: Optional[float]
    hit_rate_20d: Optional[float]
    avg_return_5d: Optional[float]
    avg_return_20d: Optional[float]
    std_return_20d: Optional[float]
    t_stat_20d: Optional[float]
    sharpe_20d_ann: Optional[float]
    ir_vs_benchmark_20d: Optional[float]
    benchmark_symbol: Optional[str]
    decision: str  # 'keep_strong' | 'keep_weak' | 'kill'
    notes: list[str]

    def as_dict(self) -> dict:
        # asdict but JSON-safe (no NaN)
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


def _mean(xs: list[float]) -> float:
    if not xs: return 0.0
    return sum(xs) / len(xs)


def _std(xs: list[float], ddof: int = 1) -> float:
    n = len(xs)
    if n <= ddof: return 0.0
    m = _mean(xs)
    s2 = sum((x - m) ** 2 for x in xs) / (n - ddof)
    return math.sqrt(s2)


def _decide(sharpe: Optional[float], t_stat: Optional[float]) -> tuple[str, list[str]]:
    notes: list[str] = []
    if sharpe is None or t_stat is None:
        notes.append("insufficient data (null sharpe or t-stat)")
        return "kill", notes
    # Rules from reviewer spec S3
    if sharpe > 1.0 and t_stat > 2.0:
        return "keep_strong", notes
    if sharpe < 0.3 or t_stat < 1.5:
        return "kill", notes
    if 0.3 <= sharpe <= 1.0:
        notes.append("marginal signal -- consider as filter not as trigger")
        return "keep_weak", notes
    # Edge: sharpe>1 but t<2 (many trades, moderate effect)
    notes.append("high Sharpe but low t-stat -- sample size may be small")
    return "keep_weak", notes


def enumerate_events(
    detector: SignalDetector,
    universe: str,
    from_date: date,
    to_date: date,
    sample_every_n_days: int = 5,
) -> list[dict]:
    """Scan (symbol, date) pairs and collect where detector returned True.

    sample_every_n_days=5 matches weekly-ish rebalancing; the caller can
    set to 1 for daily. For 10-year BIST30 backtest:
      27 symbols * ~2600 trading days / 5 = ~14k checks -- tractable.

    Universe membership is checked per-date (uses get_universe_at) so
    delisted symbols don't get scanned after their removal date.
    """
    events: list[dict] = []
    cur = from_date
    one_day = timedelta(days=1)
    step = timedelta(days=sample_every_n_days)

    while cur <= to_date:
        if cur.weekday() < 5:  # weekday only
            members = get_universe_at(universe, cur)
            for sym in members:
                try:
                    if detector(sym, cur):
                        events.append({"symbol": sym, "as_of": cur.isoformat()})
                except Exception as e:
                    log.debug(f"detector({sym}, {cur}): {e}")
        cur += step
    return events


def run_validator(
    signal_name: str,
    detector: SignalDetector,
    universe: str = "BIST30",
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    sample_every_n_days: int = 5,
    benchmark_symbol: Optional[str] = "XU100",
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    today: Optional[date] = None,
) -> ValidatorResult:
    """Run the validator for one signal over [from, to].

    Flow:
      1. enumerate_events -> [{symbol, as_of}, ...]
      2. labeler.batch_label_signals -> adds return_{h}d keys
      3. compute stats: hit_rate, mean, std, t, Sharpe_ann, IR vs benchmark
      4. decide: keep_strong / keep_weak / kill
    """
    from_date = from_date or date(2018, 1, 1)
    to_date = to_date or date.today()

    events = enumerate_events(
        detector, universe=universe,
        from_date=from_date, to_date=to_date,
        sample_every_n_days=sample_every_n_days,
    )

    labeled = batch_label_signals(
        events, horizons=horizons, universe=universe,
        benchmark_symbol=benchmark_symbol, today=today,
    )

    # Pull 5d and 20d arrays (drop None)
    r5 = [row["return_5d"] for row in labeled if row.get("return_5d") is not None]
    r20 = [row["return_20d"] for row in labeled if row.get("return_20d") is not None]
    e20 = [row["excess_20d"] for row in labeled
           if benchmark_symbol and row.get("excess_20d") is not None]

    n = len(labeled)

    def _hit(xs): return sum(1 for x in xs if x > 0) / len(xs) if xs else None

    def _t_stat(xs):
        if len(xs) < 2: return None
        m = _mean(xs); s = _std(xs)
        if s == 0: return None
        return m / (s / math.sqrt(len(xs)))

    def _sharpe_ann(xs, horizon_days):
        if len(xs) < 2: return None
        m = _mean(xs); s = _std(xs)
        if s == 0: return None
        # Annualize: sqrt(252 / horizon)
        return (m / s) * math.sqrt(252.0 / horizon_days)

    def _info_ratio(xs, horizon_days):
        if len(xs) < 2: return None
        m = _mean(xs); s = _std(xs)
        if s == 0: return None
        return (m / s) * math.sqrt(252.0 / horizon_days)

    sharpe_20d_ann = _sharpe_ann(r20, 20)
    t20 = _t_stat(r20)
    decision, notes = _decide(sharpe_20d_ann, t20)

    if n == 0:
        notes.insert(0, "no events fired -- check detector or date range")
    elif n < 30:
        notes.append(f"low trade count ({n}) -- results may be noisy")

    return ValidatorResult(
        signal=signal_name,
        universe=universe,
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        n_trades=n,
        hit_rate_5d=_hit(r5),
        hit_rate_20d=_hit(r20),
        avg_return_5d=_mean(r5) if r5 else None,
        avg_return_20d=_mean(r20) if r20 else None,
        std_return_20d=_std(r20) if len(r20) > 1 else None,
        t_stat_20d=t20,
        sharpe_20d_ann=sharpe_20d_ann,
        ir_vs_benchmark_20d=_info_ratio(e20, 20),
        benchmark_symbol=benchmark_symbol,
        decision=decision,
        notes=notes,
    )


def write_report(result: ValidatorResult, out_dir: Path) -> tuple[Path, Path]:
    """Write {name}.json and {name}.md to out_dir. Returns (json_path, md_path)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = (result.signal
                 .replace("/", "_").replace(" ", "_")
                 .replace("ı", "i").replace("ş", "s").replace("ğ", "g")
                 .replace("ü", "u").replace("ö", "o").replace("ç", "c")
                 .replace("İ", "I").replace("Ş", "S").replace("Ğ", "G")
                 .lower())
    json_path = out_dir / f"{safe_name}.json"
    md_path = out_dir / f"{safe_name}.md"

    data = result.as_dict()
    json_path.write_text(json.dumps(data, indent=2, default=str))

    def _fmt(v, pct=False, n=4):
        if v is None: return "—"
        if pct: return f"{v*100:.2f}%"
        return f"{v:.{n}f}"

    md = f"""# {result.signal}

**Universe:** {result.universe}
**Period:** {result.from_date} → {result.to_date}
**Benchmark:** {result.benchmark_symbol or "—"}

**Decision:** `{result.decision}`

## Statistics

| Metric | Value |
|---|---|
| n_trades | {result.n_trades} |
| hit_rate_5d | {_fmt(result.hit_rate_5d, pct=True)} |
| hit_rate_20d | {_fmt(result.hit_rate_20d, pct=True)} |
| avg_return_5d | {_fmt(result.avg_return_5d, pct=True)} |
| avg_return_20d | {_fmt(result.avg_return_20d, pct=True)} |
| std_return_20d | {_fmt(result.std_return_20d, pct=True)} |
| t_stat_20d | {_fmt(result.t_stat_20d, n=2)} |
| Sharpe_20d_ann | {_fmt(result.sharpe_20d_ann, n=2)} |
| IR vs {result.benchmark_symbol or "—"} (20d) | {_fmt(result.ir_vs_benchmark_20d, n=2)} |

## Notes
""" + "\n".join(f"- {n}" for n in result.notes) + "\n"
    md_path.write_text(md)
    return json_path, md_path

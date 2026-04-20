"""Signal validator (Phase 3 FAZ 3.3 + Phase 4 FAZ 4.1 multi-horizon).

Given a signal detector (symbol, as_of) -> bool and a universe / date
range, validator enumerates events, labels them (via labeler.py),
computes statistics per horizon, and emits a decision.

Phase 4 FAZ 4.1 additions (reviewer Q2, Q4, Q5):
  - Dual-horizon (20d + 60d, extensible to any [5, 10, 20, 60]) stats
    side-by-side. run_validator stays as a backward-compat wrapper
    that returns the 20d slice in the Phase 3 single-horizon shape.
  - Per-event regime tag (bull/neutral/bear × low/mid/high) from
    research.regime. Reporting-only; NOT a calibration dimension.
  - Net-of-cost stats using NET_ASSUMPTION_BPS (reviewer Q5: user said
    'gross primary' -- we keep gross as default but surface net_* as
    a reference column for when portfolio discussions begin).

Per-signal output schema stays reviewer-spec compatible; the Phase 4
JSON adds horizons=[...] and horizon_stats={...} blocks + regime
breakdown, while keeping the top-level 20d fields for consumers that
only know the Phase 3 schema.

Decision rules (unchanged from Phase 3):
  keep_strong  -- Sharpe_20d > 1.0 AND t_stat_20d > 2.0
  keep_weak    -- 0.3 <= Sharpe_20d <= 1.0
  kill         -- Sharpe_20d < 0.3 OR t_stat_20d < 1.5
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, asdict, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from infra.pit import get_universe_at
from research.labeler import batch_label_signals, DEFAULT_HORIZONS

log = logging.getLogger("bistbull.research.validator")


# A SignalDetector is a function (symbol, as_of_date) -> bool.
SignalDetector = Callable[[str, date], bool]

# Reviewer Q5: user said "komisyonu siktir et", so gross is primary.
# But net_* columns travel alongside for the eventual portfolio step.
# 30 bps roundtrip = 15 bp commission + 15 bp slippage, conservative.
# Applied as a single deduction per event (one-way cost in the return
# series); a round-trip would require modeling the exit, which we
# don't here since we're not holding-to-fixed-exit.
NET_ASSUMPTION_BPS = 30


@dataclass
class ValidatorResult:
    """Machine-readable backtest summary per signal (Phase 3 schema).

    Serializable to JSON with asdict(). decision is the enum rule
    described in the module docstring. Phase 4 added the horizon_stats
    and regime_breakdown fields; the Phase 3 fields stay at top level
    for backward compat.
    """
    signal: str
    universe: str
    from_date: str
    to_date: str
    n_trades: int
    # Phase 3 single-horizon fields (20d stays the "canonical" for the
    # top-level decision; Phase 4 horizon_stats holds the full grid)
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
    # Phase 4 FAZ 4.1 additions (kept optional for backward-compat)
    horizons: list[int] = field(default_factory=list)
    horizon_stats: dict = field(default_factory=dict)  # {h: stats}
    regime_breakdown: dict = field(default_factory=dict)  # {label: stats}

    def as_dict(self) -> dict:
        d = asdict(self)
        # JSON-safe: replace NaN/Inf with None
        def _scrub(v):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            if isinstance(v, dict):
                return {k: _scrub(x) for k, x in v.items()}
            if isinstance(v, list):
                return [_scrub(x) for x in v]
            return v
        return {k: _scrub(v) for k, v in d.items()}


def _mean(xs: list[float]) -> float:
    if not xs: return 0.0
    return sum(xs) / len(xs)


def _std(xs: list[float], ddof: int = 1) -> float:
    n = len(xs)
    if n <= ddof: return 0.0
    m = _mean(xs)
    s2 = sum((x - m) ** 2 for x in xs) / (n - ddof)
    return math.sqrt(s2)


def _hit(xs: list[float]) -> Optional[float]:
    if not xs: return None
    return sum(1 for x in xs if x > 0) / len(xs)


def _t_stat(xs: list[float]) -> Optional[float]:
    if len(xs) < 2: return None
    m = _mean(xs); s = _std(xs)
    if s == 0: return None
    return m / (s / math.sqrt(len(xs)))


def _sharpe_ann(xs: list[float], horizon_days: int) -> Optional[float]:
    """Annualized Sharpe-like ratio assuming horizon_days sampling interval."""
    if len(xs) < 2: return None
    m = _mean(xs); s = _std(xs)
    if s == 0: return None
    return (m / s) * math.sqrt(252.0 / horizon_days)


def _info_ratio(xs: list[float], horizon_days: int) -> Optional[float]:
    """Same formula as _sharpe_ann but named IR when xs is excess returns."""
    return _sharpe_ann(xs, horizon_days)


def _horizon_stats(returns: list[float], excess_returns: list[float],
                   horizon_days: int) -> dict:
    """Compute the full stat set for one horizon. Returns None-valued
    entries when sample size is too small."""
    gross = {
        "n": len(returns),
        "hit_rate": _hit(returns),
        "avg_return": _mean(returns) if returns else None,
        "std_return": _std(returns) if len(returns) > 1 else None,
        "t_stat": _t_stat(returns),
        "sharpe_ann": _sharpe_ann(returns, horizon_days),
        "ir_vs_benchmark": _info_ratio(excess_returns, horizon_days)
                          if excess_returns else None,
    }
    # Net-of-cost: subtract NET_ASSUMPTION_BPS / 10000 from every gross
    # return. Symmetric for both directions (bearish signals still pay
    # the cost). Kept as a separate stat block so the caller can decide
    # which to surface.
    cost = NET_ASSUMPTION_BPS / 10000.0
    net_returns = [r - cost for r in returns]
    net = {
        "avg_return_net": _mean(net_returns) if net_returns else None,
        "sharpe_ann_net": _sharpe_ann(net_returns, horizon_days),
        "t_stat_net": _t_stat(net_returns),
    }
    return {**gross, **net}


def _decide(sharpe: Optional[float], t_stat: Optional[float]) -> tuple[str, list[str]]:
    notes: list[str] = []
    if sharpe is None or t_stat is None:
        notes.append("insufficient data (null sharpe or t-stat)")
        return "kill", notes
    if sharpe > 1.0 and t_stat > 2.0:
        return "keep_strong", notes
    if sharpe < 0.3 or t_stat < 1.5:
        return "kill", notes
    if 0.3 <= sharpe <= 1.0:
        notes.append("marginal signal -- consider as filter not as trigger")
        return "keep_weak", notes
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

    Universe membership checked per-date (delisted symbols stop being
    scanned after removal).
    """
    events: list[dict] = []
    cur = from_date
    step = timedelta(days=sample_every_n_days)

    while cur <= to_date:
        if cur.weekday() < 5:
            members = get_universe_at(universe, cur)
            for sym in members:
                try:
                    if detector(sym, cur):
                        events.append({"symbol": sym, "as_of": cur.isoformat()})
                except Exception as e:
                    log.debug(f"detector({sym}, {cur}): {e}")
        cur += step
    return events


def run_validator_multi_horizon(
    signal_name: str,
    detector: SignalDetector,
    universe: str = "BIST30",
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    sample_every_n_days: int = 5,
    benchmark_symbol: Optional[str] = "XU100",
    horizons: tuple[int, ...] = (5, 20, 60),
    today: Optional[date] = None,
    annotate_regime: bool = True,
    regime_benchmark: Optional[str] = None,
) -> ValidatorResult:
    """Phase 4 FAZ 4.1 multi-horizon validator.

    Runs the Phase 3 pipeline (enumerate -> label -> stats -> decide)
    across every horizon in `horizons`. The 20d-horizon remains the
    "canonical" one for the top-level decision; horizon_stats holds
    the per-horizon grid.

    annotate_regime: attach a regime tag to each event from
    research.regime.get_regime_at and produce a regime_breakdown in the
    result. Reporting-only (reviewer Q4); not a calibration dimension.

    horizons: reviewer Q2 said dual (20d + 60d); default is (5, 20, 60)
    to also surface the short-vade reading.
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

    # Regime tagging
    if annotate_regime and labeled:
        try:
            from research.regime import annotate_events_with_regime
            bench = regime_benchmark or benchmark_symbol or "XU100"
            labeled = annotate_events_with_regime(
                labeled, benchmark_symbol=bench,
            )
        except Exception as e:
            log.warning(f"regime annotation skipped: {e}")

    n = len(labeled)

    # Per-horizon stats grid
    horizon_stats: dict[int, dict] = {}
    for h in horizons:
        rets = [row[f"return_{h}d"] for row in labeled
                if row.get(f"return_{h}d") is not None]
        excess = [row[f"excess_{h}d"] for row in labeled
                  if benchmark_symbol and row.get(f"excess_{h}d") is not None]
        horizon_stats[h] = _horizon_stats(rets, excess, h)

    # 20d is canonical for the decision (Phase 3 semantic preserved).
    # If 20d isn't in horizons, use the first horizon in the list.
    canonical_h = 20 if 20 in horizons else horizons[0]
    canonical = horizon_stats.get(canonical_h, {})
    sharpe_c = canonical.get("sharpe_ann")
    t_c = canonical.get("t_stat")
    decision, notes = _decide(sharpe_c, t_c)

    if n == 0:
        notes.insert(0, "no events fired -- check detector or date range")
    elif n < 30:
        notes.append(f"low trade count ({n}) -- results may be noisy")

    # Regime breakdown: per-label {n, mean_20d, sharpe_20d_ann}
    regime_breakdown: dict[str, dict] = {}
    if annotate_regime:
        from collections import defaultdict
        by_regime: dict[str, list[dict]] = defaultdict(list)
        for row in labeled:
            regime = row.get("regime", "unknown_unknown")
            by_regime[regime].append(row)
        for regime, rows in by_regime.items():
            rets_h = [r[f"return_{canonical_h}d"] for r in rows
                      if r.get(f"return_{canonical_h}d") is not None]
            regime_breakdown[regime] = {
                "n": len(rows),
                f"avg_return_{canonical_h}d":
                    _mean(rets_h) if rets_h else None,
                f"sharpe_{canonical_h}d_ann":
                    _sharpe_ann(rets_h, canonical_h),
            }

    # Keep Phase 3 top-level fields
    h20 = horizon_stats.get(20, {})
    h5 = horizon_stats.get(5, {})

    return ValidatorResult(
        signal=signal_name,
        universe=universe,
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        n_trades=n,
        hit_rate_5d=h5.get("hit_rate"),
        hit_rate_20d=h20.get("hit_rate"),
        avg_return_5d=h5.get("avg_return"),
        avg_return_20d=h20.get("avg_return"),
        std_return_20d=h20.get("std_return"),
        t_stat_20d=h20.get("t_stat"),
        sharpe_20d_ann=h20.get("sharpe_ann"),
        ir_vs_benchmark_20d=h20.get("ir_vs_benchmark"),
        benchmark_symbol=benchmark_symbol,
        decision=decision,
        notes=notes,
        horizons=list(horizons),
        horizon_stats={str(h): s for h, s in horizon_stats.items()},
        regime_breakdown=regime_breakdown,
    )


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
    """Phase 3 backward-compatible wrapper.

    Delegates to run_validator_multi_horizon. All Phase 3 callers see
    the same top-level field set (hit_rate_20d, sharpe_20d_ann, etc.)
    The horizon_stats / regime_breakdown are populated too; Phase 3
    code that didn't know about them ignores them safely.

    annotate_regime=False here so Phase 3 tests that don't pre-seed
    an XU100 benchmark don't trip on regime lookup; callers that want
    regime should use run_validator_multi_horizon directly.
    """
    return run_validator_multi_horizon(
        signal_name=signal_name,
        detector=detector,
        universe=universe,
        from_date=from_date,
        to_date=to_date,
        sample_every_n_days=sample_every_n_days,
        benchmark_symbol=benchmark_symbol,
        horizons=horizons,
        today=today,
        annotate_regime=False,  # Phase 3 compat
    )


def write_report(result: ValidatorResult, out_dir: Path) -> tuple[Path, Path]:
    """Write {name}.json and {name}.md to out_dir. Returns (json_path, md_path).

    Phase 4 additions: multi-horizon stats table + regime breakdown
    section (if populated). Phase 3 top-level fields stay at the top
    of both files so single-horizon consumers are unaffected.
    """
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

    # Top block (Phase 3 schema)
    top = f"""# {result.signal}

**Universe:** {result.universe}
**Period:** {result.from_date} → {result.to_date}
**Benchmark:** {result.benchmark_symbol or "—"}

**Decision:** `{result.decision}`

## Statistics (20d canonical)

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
"""

    # Multi-horizon table (Phase 4)
    horizon_md = ""
    if result.horizon_stats:
        horizon_md = "\n## Multi-horizon (Phase 4)\n\n"
        horizon_md += f"Net stats use a {NET_ASSUMPTION_BPS}bp one-way cost deduction per trade.\n\n"
        horizon_md += "| Horizon | n | hit | avg_ret | Sharpe_ann (gross) | Sharpe_ann (net) | t_stat | IR vs bench |\n"
        horizon_md += "|---|---|---|---|---|---|---|---|\n"
        for h_str in sorted(result.horizon_stats.keys(), key=int):
            s = result.horizon_stats[h_str]
            horizon_md += (
                f"| {h_str}d "
                f"| {s.get('n', '—')} "
                f"| {_fmt(s.get('hit_rate'), pct=True)} "
                f"| {_fmt(s.get('avg_return'), pct=True)} "
                f"| {_fmt(s.get('sharpe_ann'), n=2)} "
                f"| {_fmt(s.get('sharpe_ann_net'), n=2)} "
                f"| {_fmt(s.get('t_stat'), n=2)} "
                f"| {_fmt(s.get('ir_vs_benchmark'), n=2)} |\n"
            )

    # Regime breakdown (Phase 4)
    regime_md = ""
    if result.regime_breakdown:
        regime_md = "\n## Regime breakdown (20d, reporting-only)\n\n"
        regime_md += "| Regime | n | avg_20d | Sharpe_20d_ann |\n"
        regime_md += "|---|---|---|---|\n"
        for label in sorted(result.regime_breakdown.keys()):
            s = result.regime_breakdown[label]
            regime_md += (
                f"| `{label}` "
                f"| {s.get('n', '—')} "
                f"| {_fmt(s.get('avg_return_20d'), pct=True)} "
                f"| {_fmt(s.get('sharpe_20d_ann'), n=2)} |\n"
            )

    notes_md = "\n## Notes\n" + "\n".join(f"- {n}" for n in result.notes) + "\n"

    md_path.write_text(top + horizon_md + regime_md + notes_md)
    return json_path, md_path

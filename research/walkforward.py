"""Walk-forward validation (Phase 4 FAZ 4.3).

Reviewer spec Q6: 3-year training window / 1-year test, expanding.
Expanding (not rolling) because rejim bilgisini yakalamak için eski
veri değerli -- a full market cycle's worth of training survives
across every fold.

Fold schedule (inferred from event year distribution, typically
2018-2025 for BIST30):
  Fold 1: train 2018-2020, test 2021
  Fold 2: train 2018-2021, test 2022   <- 2022 outlier stress test
  Fold 3: train 2018-2022, test 2023
  Fold 4: train 2018-2023, test 2024
  Fold 5: train 2018-2024, test 2025

For each fold, for each (signal, horizon):
  1. Training events -> calibrate_signal_weights() -> per-sector weights
  2. Test events -> apply those weights via get_weight (sector-conditional,
     falling back to _default for n<20 sectors)
  3. Compute:
       - raw_sharpe:       unweighted test-period Sharpe (stability metric
                          for the signal itself across folds)
       - sector_weighted_sharpe: Sharpe of (sector_weight × test_return)
                          (does the per-sector training weight add value?)
       - raw_sharpe_net:   raw - 30bp cost per event (FAZ 4.1 / Q5)
       - train_weight:     signal's _default weight from training (for
                          sign-prediction comparison)

CRITICAL: no look-ahead. calibrate_signal_weights is called ONLY on
events from training years. Test events are never reachable from the
weight computation. Tests enforce this invariant.
"""

from __future__ import annotations

import csv
import json
import logging
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Union

from research.calibration import (
    calibrate_signal_weights, get_weight, _extract_return,
)
from research.sectors import get_sector

log = logging.getLogger("bistbull.walkforward")


# Same as validator; here so callers can override per-fold if desired.
NET_ASSUMPTION_BPS = 30


@dataclass
class SignalHorizonStats:
    """Per (fold, signal, horizon) statistics."""
    signal: str
    horizon: int
    n_test: int
    # Raw (unweighted) stats — stability check of the signal itself
    raw_mean: Optional[float]
    raw_std: Optional[float]
    raw_sharpe: Optional[float]          # annualized
    raw_sharpe_net: Optional[float]      # with 30bp cost
    raw_hit_rate: Optional[float]
    # Sector-weighted (prediction application) stats
    n_with_weight: int                   # events that received a non-None weight
    weighted_mean: Optional[float]       # mean(weight × return)
    weighted_sharpe: Optional[float]     # Sharpe of weighted returns
    # Training-side info for prediction-accuracy comparison
    train_weight_default: Optional[float]  # _default weight from training
    train_n: int                         # training-sample size for this signal
    sign_agreement: Optional[bool]       # sign(train_weight) == sign(raw_sharpe)


@dataclass
class FoldResult:
    fold_id: int
    train_from_year: int
    train_to_year: int
    test_year: int
    train_n_total: int
    test_n_total: int
    horizons: list[int]
    # {signal: {horizon: SignalHorizonStats}}
    signal_stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        def _s(stats: SignalHorizonStats) -> dict:
            return asdict(stats)
        d = asdict(self)
        d["signal_stats"] = {
            sig: {str(h): _s(s) for h, s in horizons.items()}
            for sig, horizons in self.signal_stats.items()
        }
        # Scrub NaN/Inf
        def _scrub(v):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            if isinstance(v, dict):
                return {k: _scrub(x) for k, x in v.items()}
            if isinstance(v, list):
                return [_scrub(x) for x in v]
            return v
        return _scrub(d)


# ========== Helpers ==========

def _mean(xs: list[float]) -> Optional[float]:
    if not xs: return None
    return sum(xs) / len(xs)


def _std(xs: list[float]) -> Optional[float]:
    if len(xs) < 2: return None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _sharpe_ann(xs: list[float], h: int) -> Optional[float]:
    if len(xs) < 2: return None
    m = sum(xs) / len(xs)
    s = _std(xs)
    if s is None or s == 0: return None
    return round((m / s) * math.sqrt(252.0 / h), 4)


def _hit_rate(xs: list[float]) -> Optional[float]:
    if not xs: return None
    return sum(1 for x in xs if x > 0) / len(xs)


def _sign(x: Optional[float]) -> Optional[int]:
    if x is None: return None
    if x > 0: return 1
    if x < 0: return -1
    return 0


# ========== Fold construction ==========

def make_expanding_folds(events: list[dict],
                         min_train_years: int = 3) -> list[dict]:
    """Produce fold boundaries from the year coverage of `events`.

    Each fold dict: {fold_id, train_from_year, train_to_year, test_year}.
    Start year is the minimum year in the events; training window is
    expanding (always starts at start_year). First test year is
    start_year + min_train_years.

    If events span fewer than min_train_years + 1 distinct years,
    returns []. The caller should flag this as insufficient data.
    """
    years = sorted({e.get("year") for e in events
                    if e.get("year") is not None})
    if len(years) < min_train_years + 1:
        return []
    start_year = years[0]
    folds = []
    for test_year in years[min_train_years:]:
        folds.append({
            "fold_id": len(folds) + 1,
            "train_from_year": start_year,
            "train_to_year": test_year - 1,
            "test_year": test_year,
        })
    return folds


def _subset_by_years(events: list[dict],
                     from_year: int, to_year: int) -> list[dict]:
    return [e for e in events
            if e.get("year") is not None
            and from_year <= e["year"] <= to_year]


def _attach_sectors_inplace(events: list[dict]) -> None:
    """Populate 'sector' from SECTOR_MAP if missing."""
    for e in events:
        if not e.get("sector"):
            sym = e.get("symbol", "")
            e["sector"] = get_sector(sym) or "Unknown"


# ========== Per-fold evaluation ==========

def _evaluate_fold(
    training_events: list[dict],
    test_events: list[dict],
    horizons: tuple[int, ...],
    min_n: int = 20,
) -> dict[str, dict[int, SignalHorizonStats]]:
    """Train weights on training_events, apply to test_events, return
    per (signal, horizon) stats.

    No look-ahead: weights come from calibrate_signal_weights on
    training_events only. test_events are consumed strictly for
    return stats.
    """
    _attach_sectors_inplace(training_events)
    _attach_sectors_inplace(test_events)

    # Step 1: Train weights on training events only (NO look-ahead)
    weights = calibrate_signal_weights(
        training_events, horizons=horizons, min_n=min_n,
    )

    # Step 2: Group test events by signal for per-signal stats
    from collections import defaultdict
    by_signal: dict[str, list[dict]] = defaultdict(list)
    for ev in test_events:
        sig = ev.get("signal")
        if sig:
            by_signal[sig].append(ev)

    # Step 3: Per (signal, horizon) stats
    out: dict[str, dict[int, SignalHorizonStats]] = {}
    # Also compute training n per signal for the report
    train_n_by_signal: dict[str, int] = defaultdict(int)
    for ev in training_events:
        if ev.get("signal"):
            train_n_by_signal[ev["signal"]] += 1

    for signal, test_evs in by_signal.items():
        out[signal] = {}
        for h in horizons:
            # Raw test returns (unweighted -- signal-level stability)
            raw_rets = [_extract_return(ev, h) for ev in test_evs]
            raw_rets = [r for r in raw_rets if r is not None]

            # Sector-weighted test returns
            weighted_rets: list[float] = []
            for ev in test_evs:
                r = _extract_return(ev, h)
                if r is None:
                    continue
                w = get_weight(weights, signal, ev.get("sector"), h)
                if w is None:
                    continue
                weighted_rets.append(w * r)

            # Training-side prediction: _default weight for this signal
            train_default = (
                weights.get(signal, {}).get("_default", {}).get(f"weight_{h}d")
            )

            raw_sharpe = _sharpe_ann(raw_rets, h)
            raw_sharpe_net = _sharpe_ann(
                [r - NET_ASSUMPTION_BPS / 10000.0 for r in raw_rets], h,
            )
            weighted_sharpe = _sharpe_ann(weighted_rets, h)

            # Sign agreement: does training default-weight sign predict
            # test raw Sharpe sign? (None if either is unknown/zero)
            tsign = _sign(train_default)
            ssign = _sign(raw_sharpe)
            agree = (tsign == ssign) if (tsign in (-1, 1) and ssign in (-1, 1)) else None

            out[signal][h] = SignalHorizonStats(
                signal=signal,
                horizon=h,
                n_test=len(test_evs),
                raw_mean=_mean(raw_rets),
                raw_std=_std(raw_rets),
                raw_sharpe=raw_sharpe,
                raw_sharpe_net=raw_sharpe_net,
                raw_hit_rate=_hit_rate(raw_rets),
                n_with_weight=len(weighted_rets),
                weighted_mean=_mean(weighted_rets),
                weighted_sharpe=weighted_sharpe,
                train_weight_default=train_default,
                train_n=train_n_by_signal.get(signal, 0),
                sign_agreement=agree,
            )
    return out


# ========== Public API ==========

def run_walk_forward(
    events: list[dict],
    horizons: tuple[int, ...] = (20, 60),
    min_train_years: int = 3,
    min_n: int = 20,
) -> list[FoldResult]:
    """Run expanding-window walk-forward validation.

    events: list of event dicts (signal, symbol, year, sector,
            ret_{N}d keys for each horizon). Compatible with the
            deep_events.csv-shaped input from load_events_csv().
    horizons: Q2 default (20, 60). 5d supported for completeness if
              the caller wants short-horizon net-cost sensitivity.
    min_train_years: reviewer Q6 = 3.
    min_n: calibration's per-sector threshold, passed through.

    Returns a list of FoldResult, one per fold. Empty if events don't
    span at least min_train_years + 1 distinct years.
    """
    folds = make_expanding_folds(events, min_train_years=min_train_years)
    results: list[FoldResult] = []
    for fold in folds:
        train_evs = _subset_by_years(
            events, fold["train_from_year"], fold["train_to_year"],
        )
        test_evs = _subset_by_years(
            events, fold["test_year"], fold["test_year"],
        )
        stats = _evaluate_fold(
            train_evs, test_evs, horizons=horizons, min_n=min_n,
        )
        results.append(FoldResult(
            fold_id=fold["fold_id"],
            train_from_year=fold["train_from_year"],
            train_to_year=fold["train_to_year"],
            test_year=fold["test_year"],
            train_n_total=len(train_evs),
            test_n_total=len(test_evs),
            horizons=list(horizons),
            signal_stats=stats,
        ))
    return results


# ========== Report writers ==========

def write_walkforward_csv(results: list[FoldResult],
                           out_path: Union[str, Path]) -> Path:
    """Long-format CSV: one row per (fold, signal, horizon)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "fold_id", "train_from_year", "train_to_year", "test_year",
            "signal", "horizon",
            "n_test", "raw_mean", "raw_std",
            "raw_sharpe", "raw_sharpe_net", "raw_hit_rate",
            "n_with_weight", "weighted_mean", "weighted_sharpe",
            "train_weight_default", "train_n", "sign_agreement",
        ])
        for fr in results:
            for sig, h_map in fr.signal_stats.items():
                for h, s in h_map.items():
                    w.writerow([
                        fr.fold_id, fr.train_from_year, fr.train_to_year,
                        fr.test_year, s.signal, s.horizon,
                        s.n_test,
                        _fmt_num(s.raw_mean), _fmt_num(s.raw_std),
                        _fmt_num(s.raw_sharpe), _fmt_num(s.raw_sharpe_net),
                        _fmt_num(s.raw_hit_rate),
                        s.n_with_weight,
                        _fmt_num(s.weighted_mean),
                        _fmt_num(s.weighted_sharpe),
                        _fmt_num(s.train_weight_default),
                        s.train_n,
                        "" if s.sign_agreement is None else
                        ("1" if s.sign_agreement else "0"),
                    ])
    return out_path


def _fmt_num(v: Optional[float]) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return ""
    return f"{v:.6f}" if isinstance(v, float) else str(v)


def _compute_cross_fold_stability(results: list[FoldResult],
                                   horizon: int) -> dict[str, dict]:
    """For each signal, compute cross-fold stability stats of raw_sharpe.

    Returns {signal: {folds: [...], mean, std, min, max, n_folds}}.
    """
    from collections import defaultdict
    per_signal: dict[str, list[tuple[int, Optional[float]]]] = defaultdict(list)
    for fr in results:
        for sig, h_map in fr.signal_stats.items():
            s = h_map.get(horizon)
            if s is not None:
                per_signal[sig].append((fr.fold_id, s.raw_sharpe))

    out: dict[str, dict] = {}
    for sig, pairs in per_signal.items():
        sharpes = [v for _, v in pairs if v is not None]
        entry: dict = {
            "folds": {fid: v for fid, v in pairs},
            "n_folds": len(sharpes),
        }
        if len(sharpes) >= 2:
            entry["mean"] = sum(sharpes) / len(sharpes)
            m = entry["mean"]
            entry["std"] = math.sqrt(
                sum((x - m) ** 2 for x in sharpes) / (len(sharpes) - 1)
            )
            entry["min"] = min(sharpes)
            entry["max"] = max(sharpes)
        elif len(sharpes) == 1:
            entry["mean"] = sharpes[0]
            entry["std"] = None
            entry["min"] = sharpes[0]
            entry["max"] = sharpes[0]
        else:
            entry["mean"] = None
            entry["std"] = None
            entry["min"] = None
            entry["max"] = None
        out[sig] = entry
    return out


def write_walkforward_markdown(
    results: list[FoldResult],
    out_path: Union[str, Path],
    in_sample_reference: Optional[dict[str, float]] = None,
) -> Path:
    """Stability table + fold breakdown + Fold 2 (2022) stress analysis.

    in_sample_reference: optional {signal: global_sharpe_20d} for the
    "global vs walk-forward" comparison. If provided, shown next to
    the walk-forward mean so readers can see the overfit discount.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not results:
        out_path.write_text("# Walk-Forward\n\n(no folds -- insufficient data)\n")
        return out_path

    horizons = results[0].horizons
    lines: list[str] = ["# Phase 4.3 Walk-Forward Validation\n"]

    # Fold schedule
    lines.append("## Fold schedule (expanding window, 3Y minimum train)\n")
    lines.append("| Fold | Train | Test | Train N | Test N |")
    lines.append("|---|---|---|---|---|")
    for fr in results:
        lines.append(
            f"| {fr.fold_id} "
            f"| {fr.train_from_year}-{fr.train_to_year} "
            f"| {fr.test_year} "
            f"| {fr.train_n_total} | {fr.test_n_total} |"
        )

    # Per-horizon cross-fold stability for each signal
    for h in horizons:
        stability = _compute_cross_fold_stability(results, h)
        lines.append(f"\n## Cross-fold stability — raw Sharpe_{h}d")
        lines.append(
            "Sorted by walk-forward mean descending. "
            "`global` column is the in-sample Phase 3b Sharpe (if provided); "
            "`wf_mean` is the mean across folds; `wf_std` measures stability."
        )
        lines.append(
            f"\n| Signal | global | wf_mean | wf_std | wf_min | wf_max "
            + "".join(f"| F{fr.fold_id} ({fr.test_year}) " for fr in results)
            + "|"
        )
        lines.append(
            "|---|---|---|---|---|---"
            + "|---" * len(results)
            + "|"
        )

        def _sortkey(item):
            sig, st = item
            m = st.get("mean")
            return -m if m is not None else 1e9
        for sig, st in sorted(stability.items(), key=_sortkey):
            global_val = (in_sample_reference or {}).get(sig)
            row = [sig]
            row.append(_fmt_md(global_val))
            row.append(_fmt_md(st["mean"]))
            row.append(_fmt_md(st["std"]))
            row.append(_fmt_md(st["min"]))
            row.append(_fmt_md(st["max"]))
            for fr in results:
                v = st["folds"].get(fr.fold_id)
                row.append(_fmt_md(v))
            lines.append("| " + " | ".join(row) + " |")

    # Fold 2 stress analysis
    fold2 = next((fr for fr in results if fr.fold_id == 2), None)
    if fold2:
        lines.append(
            f"\n## Fold 2 stress analysis — test {fold2.test_year} "
            f"(trained on {fold2.train_from_year}-{fold2.train_to_year})"
        )
        lines.append(
            f"\nReviewer Q4 hypothesis: {fold2.test_year} is a "
            "cyclical outlier (emtia rallisi + TL devalüasyonu + "
            "hiperenflasyon). Training window did not contain this regime."
        )
        lines.append(
            "\nIf Fold 2 Sharpe >> other folds for a signal, the edge is "
            "regime-independent (training never saw 2022 but test worked). "
            "If Fold 2 Sharpe << other folds, the signal leans on 2022 to "
            "produce its global Sharpe, and regime-conditional calibration "
            "should be revisited (Q4 override trigger).\n"
        )
        h_primary = 20 if 20 in horizons else horizons[0]
        stability = _compute_cross_fold_stability(results, h_primary)

        lines.append(f"| Signal | F2 raw_sharpe_{h_primary}d | "
                     f"avg_other_folds | diff | verdict |")
        lines.append("|---|---|---|---|---|")
        for sig, st in sorted(stability.items()):
            f2_val = st["folds"].get(2)
            other_vals = [v for fid, v in st["folds"].items()
                          if fid != 2 and v is not None]
            other_avg = sum(other_vals) / len(other_vals) if other_vals else None
            if f2_val is None or other_avg is None:
                verdict = "—"
                diff = None
            else:
                diff = f2_val - other_avg
                abs_d = abs(diff)
                if abs_d < 0.3:
                    verdict = "stable"
                elif diff > 2.0:
                    # F2 way above other folds -- signal caught 2022's
                    # extreme volatility, but without 2022-like years
                    # in the future its edge may disappear.
                    verdict = "F2 extreme outlier (likely vol-regime-specific)"
                elif diff > 0:
                    # F2 better than average but within normal variation;
                    # the signal generalizes through 2022 without training
                    # on it.
                    verdict = "F2 outperforms (regime-independent)"
                elif diff < -2.0:
                    verdict = "F2 extreme miss (regime-dependent)"
                else:
                    verdict = "F2 underperforms (regime-dependent)"
            lines.append(
                f"| {sig} | {_fmt_md(f2_val)} | {_fmt_md(other_avg)} "
                f"| {_fmt_md(diff)} | {verdict} |"
            )

    # Net-of-cost footnote
    lines.append("")
    lines.append(
        f"\n## Net-of-cost note\n"
        f"\n`raw_sharpe_net` column in the CSV applies a "
        f"{NET_ASSUMPTION_BPS}bp one-way cost per event (FAZ 4.1 / Q5: "
        f"gross primary, net as reference). Short-horizon signals see "
        f"the largest gross→net drop; 20d/60d are less affected."
    )

    # Overfit-discount summary
    lines.append("\n## Summary — in-sample vs walk-forward\n")
    h_primary = 20 if 20 in horizons else horizons[0]
    stab20 = _compute_cross_fold_stability(results, h_primary)
    if in_sample_reference:
        lines.append(f"| Signal | In-sample Sharpe_{h_primary}d | "
                     f"Walk-forward mean | Discount |")
        lines.append("|---|---|---|---|")
        for sig in sorted(stab20.keys()):
            ins = in_sample_reference.get(sig)
            wf = stab20[sig].get("mean")
            if ins is None or wf is None:
                discount = None
            else:
                discount = wf - ins
            lines.append(
                f"| {sig} | {_fmt_md(ins)} | {_fmt_md(wf)} "
                f"| {_fmt_md(discount)} |"
            )
    else:
        lines.append("(in-sample reference not provided)")

    out_path.write_text("\n".join(lines) + "\n")
    return out_path


def _fmt_md(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return "—"
        return f"{v:+.2f}"
    return str(v)

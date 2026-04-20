"""Isotonic regression for monotone threshold calibration (Phase 4 FAZ 4.6).

Pool Adjacent Violators (PAV) algorithm: fits a non-decreasing (or
non-increasing) step function to (x, y) pairs, minimizing squared
error. No ML library -- pure Python.

Use cases:
  1. Signal strength calibration: convert a raw indicator (RSI value,
     MACD histogram, 52W high ratio) into a calibrated signal_strength
     in [0, 1] via fit on (indicator_value, forward_return_20d) pairs.
     Replaces the hand-coded thresholds in engine/scoring.py:score_higher/
     score_lower.

  2. FA scoring calibration (FAZ 4.7 consumer): fit on (metric_value,
     forward_return_60d_TR) pairs per-metric. Output feeds
     engine/scoring_calibrated.py for A/B vs V13 handpicked.

The monotone-increasing direction means "higher metric → higher
expected return". For metrics where LOWER is better (P/E, debt-to-
equity), pass increasing=False or negate x before fitting.

Output: an IsotonicFit object with:
  - predict(x): return the fitted y for a new x (piecewise-constant,
                step-function in the PAV block structure)
  - to_dict()/from_dict(): JSON-serializable via reports/isotonic_fits.json
  - domain_min, domain_max: the x-range the fit was trained on; queries
                            outside this range return the boundary values
                            (no extrapolation)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Union

log = logging.getLogger("bistbull.isotonic")


# Minimum sample threshold for a valid fit (reviewer's MIN_N convention).
MIN_FIT_SAMPLES = 20


@dataclass
class IsotonicFit:
    """Output of fit_isotonic. Piecewise-constant step function over
    sorted x-values; predict does a binary search."""
    x_knots: list[float]    # sorted ascending
    y_values: list[float]   # monotone (increasing or decreasing) per knot
    increasing: bool
    n_samples: int
    domain_min: float
    domain_max: float
    y_min: float
    y_max: float

    def predict(self, x: float) -> float:
        """Return fitted y for a new x. Piecewise constant / step.

        Out-of-domain: clamp to the boundary value. No extrapolation --
        outside the training range we can't make isotonic guarantees.
        """
        if x <= self.domain_min:
            return self.y_values[0]
        if x >= self.domain_max:
            return self.y_values[-1]
        # Binary search for the largest knot ≤ x
        lo, hi = 0, len(self.x_knots) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.x_knots[mid] <= x:
                lo = mid
            else:
                hi = mid - 1
        return self.y_values[lo]

    def predict_normalized(self, x: float) -> float:
        """Return predict(x) mapped to [0, 1] using the fit's y-range.

        When y_min == y_max (degenerate fit), returns 0.5 to avoid
        divide-by-zero. Useful for converting fitted forward returns
        to a [0, 1] signal strength.
        """
        y = self.predict(x)
        span = self.y_max - self.y_min
        if span <= 0:
            return 0.5
        return (y - self.y_min) / span

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "IsotonicFit":
        return cls(**d)


def _pool_adjacent_violators(
    y_weighted_ordered: list[tuple[float, int]],
    increasing: bool,
) -> list[tuple[float, int]]:
    """Core PAV loop.

    Input: list of (weighted_mean_y, block_count) tuples, initially
           one tuple per data point (each block count = 1).
    Output: merged blocks where the weighted means are monotone in
            the required direction.

    Algorithm: scan left-to-right; whenever a block's mean violates
    monotonicity with its predecessor, merge them (weighted average)
    and step back to check the newly-merged block against the
    predecessor. O(n) amortized.
    """
    blocks: list[list] = []  # each: [sum_y, count]
    for y, c in y_weighted_ordered:
        blocks.append([y * c, c])
        # Merge backwards while monotonicity violated
        while len(blocks) >= 2:
            prev_sum, prev_cnt = blocks[-2]
            cur_sum, cur_cnt = blocks[-1]
            prev_mean = prev_sum / prev_cnt
            cur_mean = cur_sum / cur_cnt
            # Violation: for increasing, prev_mean > cur_mean
            #            for decreasing, prev_mean < cur_mean
            if (increasing and prev_mean > cur_mean) or \
               (not increasing and prev_mean < cur_mean):
                blocks.pop()
                blocks[-1] = [prev_sum + cur_sum, prev_cnt + cur_cnt]
            else:
                break
    return [(s / c, c) for s, c in blocks]


def fit_isotonic(
    x_values: list[float],
    y_values: list[float],
    increasing: bool = True,
    min_samples: int = MIN_FIT_SAMPLES,
) -> Optional[IsotonicFit]:
    """Fit a monotone step function to (x, y) pairs via Pool Adjacent
    Violators.

    increasing=True  → higher x correlates with higher y (good for
                       metrics where high = better: ROE, margin).
    increasing=False → higher x correlates with LOWER y (good for
                       P/E, debt-to-equity: high = bad).

    Returns None if fewer than min_samples pairs after filtering
    None/NaN. Returns an IsotonicFit with the fitted step function.

    Tie-breaking when multiple points share the same x: they're grouped
    into a single block with the mean y (PAV handles this automatically
    on the sorted-pairs input).
    """
    # Filter invalid pairs
    pairs: list[tuple[float, float]] = []
    for x, y in zip(x_values, y_values):
        if x is None or y is None:
            continue
        try:
            xv = float(x)
            yv = float(y)
        except (TypeError, ValueError):
            continue
        if math.isnan(xv) or math.isnan(yv) or math.isinf(xv) or math.isinf(yv):
            continue
        pairs.append((xv, yv))

    if len(pairs) < min_samples:
        return None

    # Sort by x ascending (PAV requires monotone x domain)
    pairs.sort(key=lambda t: t[0])

    # Build initial one-per-point blocks
    initial = [(y, 1) for _, y in pairs]
    merged = _pool_adjacent_violators(initial, increasing=increasing)

    # Derive knots: the x-value at the START of each merged block.
    # For predict(x) we want the largest knot ≤ x to map to that block's y.
    x_knots: list[float] = []
    y_out: list[float] = []
    idx = 0
    for block_mean, block_count in merged:
        x_knots.append(pairs[idx][0])
        y_out.append(block_mean)
        idx += block_count

    # Guarantee monotonicity across knots (final safeguard vs numerical ties)
    if increasing:
        for i in range(1, len(y_out)):
            if y_out[i] < y_out[i - 1]:
                y_out[i] = y_out[i - 1]
    else:
        for i in range(1, len(y_out)):
            if y_out[i] > y_out[i - 1]:
                y_out[i] = y_out[i - 1]

    return IsotonicFit(
        x_knots=x_knots,
        y_values=y_out,
        increasing=increasing,
        n_samples=len(pairs),
        domain_min=pairs[0][0],
        domain_max=pairs[-1][0],
        y_min=min(y_out),
        y_max=max(y_out),
    )


def fit_per_metric(
    events: list[dict],
    metric_keys: list[str],
    return_key: str = "ret_20d",
    min_samples: int = MIN_FIT_SAMPLES,
    direction: Optional[dict[str, bool]] = None,
) -> dict[str, IsotonicFit]:
    """Fit an IsotonicFit per metric across a list of event dicts.

    Each event must contain `metric_key` for each key in metric_keys
    and `return_key` for the target. Missing/None values are dropped
    per-metric (one metric's miss doesn't drop the event from others).

    direction: {metric_key: increasing_bool}. If a key is absent,
    defaults to True (higher metric = higher return). For P/E-style
    metrics pass {metric_key: False}.

    Returns {metric_key: IsotonicFit} with insufficient-sample metrics
    omitted.
    """
    direction = direction or {}
    out: dict[str, IsotonicFit] = {}
    for key in metric_keys:
        x_vals = [e.get(key) for e in events]
        y_vals = [e.get(return_key) for e in events]
        fit = fit_isotonic(
            x_vals, y_vals,
            increasing=direction.get(key, True),
            min_samples=min_samples,
        )
        if fit is not None:
            out[key] = fit
    return out


# ==========================================================================
# Serialization
# ==========================================================================

def write_isotonic_fits_json(
    fits: dict[str, IsotonicFit],
    out_path: Union[str, Path],
) -> Path:
    """Dump {metric: IsotonicFit} to JSON. from_dict can rebuild."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v.to_dict() for k, v in fits.items()}
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return out_path


def load_isotonic_fits_json(path: Union[str, Path]) -> dict[str, IsotonicFit]:
    path = Path(path)
    data = json.loads(path.read_text())
    return {k: IsotonicFit.from_dict(v) for k, v in data.items()}


def write_isotonic_fits_markdown(
    fits: dict[str, IsotonicFit],
    out_path: Union[str, Path],
    samples_per_curve: int = 10,
) -> Path:
    """Tabular description of each fit (no matplotlib, per reviewer
    spec: "matplotlib mümkün değilse tablolu")."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _fmt(v, n=4):
        if v is None: return "—"
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v): return "—"
            return f"{v:.{n}f}"
        return str(v)

    lines = [
        "# Phase 4.6 Isotonic Fits\n",
        "Each fit is a piecewise-constant step function producing a "
        "monotone mapping from metric value → expected forward return. "
        "Tabulated below at evenly-spaced sample points for visual "
        "inspection.\n",
    ]

    for metric in sorted(fits.keys()):
        fit = fits[metric]
        dir_label = "↑ increasing" if fit.increasing else "↓ decreasing"
        lines.append(f"\n## {metric}")
        lines.append(
            f"\n- Direction: {dir_label}"
            f"\n- n_samples: {fit.n_samples}"
            f"\n- Domain: [{_fmt(fit.domain_min)}, {_fmt(fit.domain_max)}]"
            f"\n- Range: [{_fmt(fit.y_min)}, {_fmt(fit.y_max)}]"
            f"\n- Knots: {len(fit.x_knots)}"
        )

        if fit.domain_max > fit.domain_min:
            lines.append("\n| x sample | fitted y |")
            lines.append("|---|---|")
            span = fit.domain_max - fit.domain_min
            for i in range(samples_per_curve + 1):
                x = fit.domain_min + i * span / samples_per_curve
                y = fit.predict(x)
                lines.append(f"| {_fmt(x)} | {_fmt(y)} |")

    out_path.write_text("\n".join(lines) + "\n")
    return out_path

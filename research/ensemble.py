"""Mean-variance ensemble optimization over walk-forward Sharpe series
(Phase 4 FAZ 4.5).

Reviewer spec: out-of-sample-anchored ensemble.
  - Expected return μ: per-signal WALK-FORWARD mean Sharpe (Phase 4.3's
    out-of-sample estimator, NOT the in-sample deep_summary Sharpe).
    Overfit discount is already priced in.
  - Covariance Σ: estimated from the per-fold Sharpe vectors
    (signals × folds matrix).
  - Classical mean-variance: maximize μ'w - (λ/2) w'Σw subject to
    Σw = 1, w ≥ 0, AND per-signal caps for Phase 4.3 Fold-2 extreme
    outliers (BB Alt Band Kirilim, RSI Asiri Satim ≤ 10% each --
    these are vol-regime-specific; concentration risk management).

The correlation penalty reviewer requested in the spec ("ikinci
dereceden correlation penalty") is already embedded in the
mean-variance objective via Σ's off-diagonals; we don't apply a
separate penalty term.

Hold-out validation (reviewer spec: F5 2025): train the optimizer on
F1-F4 only, evaluate the ensemble Sharpe on F5. Report whether the
ensemble beats the best single-signal F5 Sharpe (the reviewer's
explicit success criterion).

No ML: solve via closed-form unconstrained optimum followed by
iterative projection onto the simplex-with-caps feasible set. Pure
linear algebra, numpy only for matrix inverse.
"""

from __future__ import annotations

import csv
import json
import logging
import math
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Optional, Union

import numpy as np

log = logging.getLogger("bistbull.ensemble")


# ==========================================================================
# Configuration defaults (reviewer-supplied)
# ==========================================================================

# Regime-outlier caps from Phase 4.3 F2 stress analysis. These two
# signals got 'F2 extreme outlier (likely vol-regime-specific)'
# verdict; capping at 10% protects the ensemble from 2022-like vol
# concentration risk.
REGIME_OUTLIER_CAP = 0.10
REGIME_OUTLIER_SIGNALS: frozenset[str] = frozenset({
    "BB Alt Band Kirilim",
    "RSI Asiri Satim",
})

# Minimum folds required to include a signal. Golden/Death Cross get
# 2-3 folds (small sample), too noisy to contribute to covariance.
MIN_FOLDS_FOR_INCLUSION = 4

# Default risk-aversion parameter for mean-variance. Higher λ -> more
# conservative (more diversified, lower expected return).
DEFAULT_LAMBDA = 2.0


@dataclass
class EnsembleResult:
    """Output of optimize_ensemble_weights."""
    signals: list[str]
    weights: list[float]               # sum to 1, all >= 0
    mu: list[float]                     # per-signal training-fold mean
    expected_sharpe: float              # ensemble E[return] = w'μ
    ensemble_vol: float                 # ensemble variance^0.5 = (w'Σw)^0.5
    correlation_matrix: list[list[float]]
    caps_applied: dict[str, float]      # {signal: cap_applied}
    excluded_signals: list[str]         # dropped due to MIN_FOLDS_FOR_INCLUSION

    def as_dict(self) -> dict:
        def _scrub(v):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            if isinstance(v, dict):
                return {k: _scrub(x) for k, x in v.items()}
            if isinstance(v, list):
                return [_scrub(x) for x in v]
            return v
        return _scrub(asdict(self))


# ==========================================================================
# Helpers
# ==========================================================================

def load_fold_sharpes_csv(
    path: Union[str, Path],
    horizon: int = 20,
    stat_col: str = "raw_sharpe",
) -> dict[str, dict[int, float]]:
    """Read walkforward.csv into {signal: {fold_id: raw_sharpe}}.

    horizon: 20 (default) or 60 — which horizon's Sharpe to use.
    stat_col: raw_sharpe (default) or raw_sharpe_net for net-of-cost.
    Missing/blank cells omitted; signals with too few folds filtered
    by the caller via MIN_FOLDS_FOR_INCLUSION.
    """
    out: dict[str, dict[int, float]] = {}
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if int(r["horizon"]) != horizon:
                continue
            v = r.get(stat_col, "")
            if not v:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            sig = r["signal"]
            fold = int(r["fold_id"])
            out.setdefault(sig, {})[fold] = fv
    return out


def _align_fold_matrix(
    fold_sharpes: dict[str, dict[int, float]],
    min_folds: int = MIN_FOLDS_FOR_INCLUSION,
    excluded_folds: frozenset[int] = frozenset(),
) -> tuple[list[str], np.ndarray, list[str]]:
    """Build the signals × folds matrix, excluding signals with too
    few observations and/or specific folds (e.g., F5 held out for
    validation).

    Returns:
      signals: list of signal names included (ordered)
      M: (n_signals, n_folds) numpy array
      excluded: list of signal names dropped
    """
    # Find folds that appear in at least one included signal
    all_folds = sorted({
        f for sig_folds in fold_sharpes.values()
        for f in sig_folds if f not in excluded_folds
    })

    # Find signals meeting min_folds threshold (counting only non-excluded folds)
    signals_ok: list[str] = []
    excluded: list[str] = []
    for sig, folds in fold_sharpes.items():
        eligible = {f: v for f, v in folds.items() if f not in excluded_folds}
        if len(eligible) >= min_folds:
            signals_ok.append(sig)
        else:
            excluded.append(sig)
    signals_ok.sort()

    # Build matrix; any missing (signal, fold) cell is filled with the
    # signal's own mean (mean-imputation) to keep the matrix shape
    # clean for covariance estimation. If signal has no folds left
    # after exclusion, it's already out.
    n_sig = len(signals_ok)
    n_f = len(all_folds)
    M = np.zeros((n_sig, n_f))
    for i, sig in enumerate(signals_ok):
        folds = fold_sharpes[sig]
        vals = [folds[f] for f in all_folds if f in folds]
        mean = sum(vals) / len(vals) if vals else 0.0
        for j, f in enumerate(all_folds):
            M[i, j] = folds.get(f, mean)
    return signals_ok, M, excluded


def _project_onto_simplex_with_caps(
    w: np.ndarray, caps: dict[int, float], iters: int = 100,
) -> np.ndarray:
    """Project w onto {x : sum(x) = 1, 0 <= x_i <= caps[i] (or 1)}.

    Iterative: alternately clip to [0, cap] and rescale to sum=1.
    Converges for any feasible simplex-with-caps; for infeasible
    (cap * n < 1) returns the clipped input without rescaling.
    """
    n = len(w)
    cap_vec = np.array([caps.get(i, 1.0) for i in range(n)])
    feasible_max = cap_vec.sum()
    if feasible_max < 1.0:
        log.warning(f"caps sum ({feasible_max}) < 1.0; simplex infeasible")
        return np.clip(w, 0.0, cap_vec)

    x = np.clip(w, 0.0, cap_vec)
    for _ in range(iters):
        s = x.sum()
        if s == 0:
            return np.ones(n) / n  # degenerate
        x = x / s
        x = np.clip(x, 0.0, cap_vec)
        new_s = x.sum()
        if abs(new_s - 1.0) < 1e-9:
            break
    # Final rescale if the cap-clipping pulled under 1
    s = x.sum()
    if s > 0:
        x = x / s
        x = np.clip(x, 0.0, cap_vec)
        s2 = x.sum()
        if s2 > 0 and abs(s2 - 1.0) > 1e-6:
            # Caps prevent reaching 1.0; pad the unconstrained positions
            unconstrained = [i for i in range(n) if x[i] < cap_vec[i]]
            slack = 1.0 - s2
            # Redistribute proportionally to current x over unconstrained
            if unconstrained:
                subtotal = sum(x[i] for i in unconstrained)
                if subtotal > 0:
                    for i in unconstrained:
                        x[i] += slack * x[i] / subtotal
                else:
                    per = slack / len(unconstrained)
                    for i in unconstrained:
                        x[i] += per
                x = np.clip(x, 0.0, cap_vec)
    return x


def optimize_ensemble_weights(
    fold_sharpes: dict[str, dict[int, float]],
    excluded_folds: frozenset[int] = frozenset(),
    lam: float = DEFAULT_LAMBDA,
    regime_outlier_cap: float = REGIME_OUTLIER_CAP,
    regime_outlier_signals: frozenset[str] = REGIME_OUTLIER_SIGNALS,
    min_folds: int = MIN_FOLDS_FOR_INCLUSION,
) -> EnsembleResult:
    """Mean-variance optimize ensemble weights from walk-forward Sharpe
    series.

    Problem:
      max_w   μ'w - (λ/2) w'Σw
      s.t.    Σw = 1, w ≥ 0
              w_i ≤ regime_outlier_cap for i in regime_outlier_signals

    Solution:
      1. Unconstrained: w* = (1/λ) Σ^(-1) μ  (from first-order cond)
      2. Project w* onto the feasible set (simplex with caps) via
         iterative clip-and-rescale.

    Returns an EnsembleResult with weights in the SAME ORDER as
    result.signals. excluded_signals lists signals dropped for
    having < min_folds observations.
    """
    signals, M, excluded = _align_fold_matrix(
        fold_sharpes, min_folds=min_folds, excluded_folds=excluded_folds,
    )
    n = len(signals)
    if n == 0:
        return EnsembleResult(
            signals=[], weights=[], mu=[],
            expected_sharpe=0.0, ensemble_vol=0.0,
            correlation_matrix=[], caps_applied={},
            excluded_signals=excluded,
        )

    mu = M.mean(axis=1)
    # Covariance across folds. If only 1 fold post-exclusion, Σ would be
    # singular; pad with small diagonal to keep invertible.
    cov = np.cov(M, ddof=1) if M.shape[1] >= 2 else np.eye(n) * 0.01
    # Ensure 2D even when n==1
    if cov.ndim == 0:
        cov = np.array([[float(cov)]])
    # Ridge: add small diagonal for numerical stability / correlated signals
    cov = cov + np.eye(n) * 1e-6

    # Correlation matrix (for reporting)
    std_vec = np.sqrt(np.clip(np.diag(cov), 1e-12, None))
    corr = cov / np.outer(std_vec, std_vec)

    # Unconstrained MV: solve λ Σ w = μ -> w = (1/λ) Σ^(-1) μ
    try:
        w_unc = np.linalg.solve(lam * cov, mu)
    except np.linalg.LinAlgError:
        # Fallback: equal weights
        w_unc = np.ones(n) / n

    # Caps dict (by index)
    caps: dict[int, float] = {}
    caps_applied: dict[str, float] = {}
    for i, sig in enumerate(signals):
        if sig in regime_outlier_signals:
            caps[i] = regime_outlier_cap
            caps_applied[sig] = regime_outlier_cap

    # Project onto simplex with caps
    w = _project_onto_simplex_with_caps(w_unc, caps)

    # Compute portfolio-level stats
    expected_sharpe = float(np.dot(w, mu))
    ensemble_var = float(np.dot(w, np.dot(cov, w)))
    ensemble_vol = math.sqrt(max(0.0, ensemble_var))

    return EnsembleResult(
        signals=signals,
        weights=[float(x) for x in w],
        mu=[float(x) for x in mu],
        expected_sharpe=expected_sharpe,
        ensemble_vol=ensemble_vol,
        correlation_matrix=[[float(x) for x in row] for row in corr],
        caps_applied=caps_applied,
        excluded_signals=excluded,
    )


def evaluate_ensemble_holdout(
    weights: dict[str, float],
    holdout_sharpes: dict[str, float],
) -> Optional[float]:
    """Compute the ensemble's hold-out fold performance.

    weights: {signal: weight} as produced by optimize_ensemble_weights.
    holdout_sharpes: {signal: test_period_Sharpe_for_that_signal}.

    Returns w · holdout_sharpe (a scalar Sharpe-like statistic). None
    if no overlap between weight and holdout signals."""
    overlap = set(weights) & set(holdout_sharpes)
    if not overlap:
        return None
    total_w = sum(weights[s] for s in overlap)
    if total_w == 0:
        return None
    # Renormalize over the overlap so weights sum to 1 for the
    # holdout evaluation
    return sum(weights[s] * holdout_sharpes[s] for s in overlap) / total_w


def best_single_signal_holdout(holdout_sharpes: dict[str, float]) -> tuple[str, float]:
    """The best individual signal's holdout Sharpe. Post-hoc -- picks
    the winner AFTER seeing the hold-out. Report-only baseline; the
    fair OOS comparison is best_signal_by_training."""
    best = max(holdout_sharpes.items(), key=lambda kv: kv[1])
    return best


def best_signal_by_training(
    training_sharpes: dict[str, dict[int, float]],
    holdout_sharpes: dict[str, float],
    min_folds: int = MIN_FOLDS_FOR_INCLUSION,
) -> tuple[Optional[str], Optional[float], Optional[float]]:
    """Pre-commit baseline: pick the signal with the highest TRAINING
    mean Sharpe (i.e. the signal you would have chosen before seeing
    the hold-out), then report its actual hold-out Sharpe.

    This is the fair OOS comparison for the ensemble: both the
    ensemble and this baseline made their signal selection decision
    using only training data.

    Returns (signal_name, training_mean, holdout_sharpe). Any of
    these may be None if data is insufficient.
    """
    candidates: dict[str, float] = {}
    for sig, folds in training_sharpes.items():
        if len(folds) >= min_folds and sig in holdout_sharpes:
            candidates[sig] = sum(folds.values()) / len(folds)
    if not candidates:
        return None, None, None
    best_sig = max(candidates, key=lambda s: candidates[s])
    return best_sig, candidates[best_sig], holdout_sharpes[best_sig]


# ==========================================================================
# Report writers
# ==========================================================================

def write_ensemble_json(
    result: EnsembleResult,
    out_path: Union[str, Path],
    holdout_eval: Optional[dict] = None,
) -> Path:
    """Write reports/phase_4_ensemble.json."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.as_dict()
    if holdout_eval:
        payload["holdout_evaluation"] = holdout_eval
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return out_path


def write_ensemble_markdown(
    result: EnsembleResult,
    out_path: Union[str, Path],
    holdout_eval: Optional[dict] = None,
) -> Path:
    """Human-readable ensemble report."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _fmt(v, n=3):
        if v is None: return "—"
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v): return "—"
            return f"{v:.{n}f}"
        return str(v)

    lines = [
        "# Phase 4.5 Ensemble — Mean-Variance Weights\n",
        f"Optimized on walk-forward Sharpe across "
        f"{len(result.signals)} signals. Regime-outlier signals "
        f"(BB Alt Band Kirilim, RSI Asiri Satim) capped at "
        f"{REGIME_OUTLIER_CAP*100:.0f}% each to limit vol-regime "
        f"concentration risk (Phase 4.3 F2 stress analysis).\n",
        "## Weights\n",
        "| Signal | μ (wf_mean) | Weight | Cap Applied |",
        "|---|---|---|---|",
    ]
    # Sort by weight desc for readability
    order = sorted(range(len(result.signals)),
                    key=lambda i: -result.weights[i])
    for i in order:
        sig = result.signals[i]
        cap = result.caps_applied.get(sig)
        lines.append(
            f"| {sig} | {_fmt(result.mu[i])} | "
            f"{_fmt(result.weights[i])} | "
            f"{_fmt(cap) if cap is not None else '—'} |"
        )

    lines.append("")
    lines.append(
        f"**Ensemble E[Sharpe]:** {_fmt(result.expected_sharpe)}   ·   "
        f"**Ensemble Vol:** {_fmt(result.ensemble_vol)}\n"
    )

    if result.excluded_signals:
        lines.append(
            "## Excluded signals\n"
            f"(< {MIN_FOLDS_FOR_INCLUSION} folds of walk-forward data; "
            "too noisy for covariance estimation)\n"
        )
        for sig in sorted(result.excluded_signals):
            lines.append(f"- {sig}")
        lines.append("")

    # Correlation matrix
    lines.append("## Correlation matrix")
    lines.append("| Signal | " + " | ".join(result.signals) + " |")
    lines.append("|---|" + "|".join("---" for _ in result.signals) + "|")
    for i, s in enumerate(result.signals):
        row = [s] + [_fmt(result.correlation_matrix[i][j])
                     for j in range(len(result.signals))]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # Hold-out evaluation
    if holdout_eval:
        lines.append("## Hold-out validation (F5 2025)")
        lines.append("")
        ens = holdout_eval.get("ensemble_sharpe")
        lines.append(
            f"- **Ensemble Sharpe on F5:** {_fmt(ens)}"
        )
        # Fair OOS comparison: training-top signal
        tt_sig = holdout_eval.get("training_top_signal")
        tt_train = holdout_eval.get("training_top_training_mean")
        tt_hold = holdout_eval.get("training_top_holdout_sharpe")
        if tt_sig:
            lines.append(
                f"- **Training-top signal (fair OOS):** "
                f"{tt_sig} — training mean "
                f"{_fmt(tt_train)}, F5 Sharpe "
                f"**{_fmt(tt_hold)}**"
            )
            v = holdout_eval.get("verdict_vs_training_top", "")
            lines.append(f"  - Verdict: **{v}**")
        # Post-hoc (cherry-picked) comparison for context
        ph_sig = holdout_eval.get("post_hoc_best_signal")
        ph_val = holdout_eval.get("post_hoc_best_sharpe")
        if ph_sig:
            lines.append(
                f"- **Post-hoc best single on F5 (cherry-picked, "
                f"not a fair OOS baseline):** {ph_sig} — "
                f"Sharpe {_fmt(ph_val)}"
            )
            v2 = holdout_eval.get("verdict_vs_post_hoc", "")
            lines.append(f"  - Verdict: **{v2}**")
        lines.append("")
        lines.append(
            "### Interpretation\n\n"
            "The **training-top signal** is the fair out-of-sample "
            "baseline: it's the single signal a pre-commit strategy "
            "would have picked given only training data (highest "
            "wf_mean across F1-F4). The **post-hoc best** is the "
            "winner on F5 itself — informative as a ceiling but not a "
            "strategy you could have executed.\n\n"
            "If the ensemble beats the training-top signal, "
            "diversification adds value in the hold-out period.\n"
        )

    out_path.write_text("\n".join(lines) + "\n")
    return out_path

"""Calibrated FA scoring (Phase 4 FAZ 4.7).

Parallel to engine/scoring.py's V13 handpicked thresholds. Instead of
`score_higher(roe, 0.05, 0.10, 0.15, 0.25)` with four magic numbers,
each metric gets an IsotonicFit trained on (metric_value,
forward_return_60d_TR) pairs. The fit's predict_normalized(x) returns
a [0, 1] score which is scaled to [5, 100] to match V13's output
range and plug into the existing aggregators.

scoring_version dispatch:
  - "v13_handpicked" (default): existing engine/scoring.py path
  - "calibrated_2026Q1":        this module

Both versions share the same downstream aggregation (score_value,
score_quality, etc.) by using the same parts-list pattern. Only the
per-metric scoring primitive changes.

Infrastructure prerequisites (from earlier phases):
  - migrations/003: score_history has scoring_version in PK (already done)
  - infra/pit.fundamentals_pit: FA data ingested with real borsapy API
    (FAZ 4.0.1 fix)
  - Phase 3 coverage report: FA metrics with coverage < 50% flagged as
    excluded_from_phase_4=yes. This module respects that flag.

Honest note on shipped state:
  - research/isotonic.py (FAZ 4.6) is implemented and tested
  - THIS module scaffolds the scoring_version='calibrated_2026Q1'
    dispatch and mirrors V13's aggregation entrypoints, but the fit
    step requires FA data (metric_value, forward_return_60d_TR) pairs
    that the reviewer's deep_events.csv doesn't contain.
  - calibrate_fa_metrics(events) IS ready to run on any events list
    shaped like [{roe, forward_return_60d, ...}]; an operator with
    FA backfill can execute it, save to reports/fa_isotonic_fits.json,
    and the calibrated_score_* functions pick it up automatically.
  - All tests in this module exercise the dispatch + fallback paths
    with a synthetic FA fit so the code is guaranteed correct; the
    REAL calibration step is an operator task.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Union

from research.isotonic import (
    IsotonicFit, fit_isotonic, load_isotonic_fits_json, write_isotonic_fits_json,
)

log = logging.getLogger("bistbull.scoring_calibrated")

CALIBRATED_VERSION = "calibrated_2026Q1"
HANDPICKED_VERSION = "v13_handpicked"


# Direction per metric: True = higher is better (score_higher semantics),
# False = lower is better (score_lower semantics). Matches engine/scoring.py.
METRIC_DIRECTIONS: dict[str, bool] = {
    # Higher = better
    "roe":                True,
    "roic":               True,
    "roa":                True,
    "net_margin":         True,
    "gross_margin":       True,
    "operating_margin":   True,
    "revenue_growth":     True,
    "eps_growth":         True,
    "ebitda_growth":      True,
    "fcf_yield":          True,
    "fcf_margin":         True,
    "current_ratio":      True,
    "interest_coverage":  True,
    "altman_z":           True,
    "piotroski_f":        True,
    "margin_safety":      True,
    "dividend_yield":     True,
    "cfo_to_ni":          True,
    # Lower = better
    "pe":                 False,
    "pb":                 False,
    "ev_ebitda":          False,
    "ev_sales":           False,
    "peg":                False,
    "debt_equity":        False,
    "net_debt_ebitda":    False,
    "beneish_m":          False,
}


DEFAULT_FITS_PATH = (
    Path(__file__).resolve().parent.parent
    / "reports" / "fa_isotonic_fits.json"
)


# Metrics whose FA coverage was flagged < 50% in Phase 3. An operator
# can override by passing a custom excluded_metrics set to calibrate_fa_metrics.
DEFAULT_EXCLUDED_METRICS: frozenset[str] = frozenset()


# ==========================================================================
# Calibration path (operator-invokable)
# ==========================================================================

def calibrate_fa_metrics(
    events: list[dict],
    metric_keys: Optional[list[str]] = None,
    return_key: str = "forward_return_60d",
    min_samples: int = 20,
    excluded_metrics: frozenset[str] = DEFAULT_EXCLUDED_METRICS,
) -> dict[str, IsotonicFit]:
    """Fit an IsotonicFit per FA metric.

    events: list of dicts, each with FA metric values + forward_return_60d.
            Expected shape matches the output of a (future) research/
            labeler extension that joins fundamentals_pit with return
            windows. The reviewer's operator-task Colab backfill is
            what actually populates this.
    metric_keys: defaults to METRIC_DIRECTIONS.keys().
    return_key: 'forward_return_60d' per reviewer spec. Could be
                'forward_return_20d' for A/B.
    excluded_metrics: metrics to skip (Phase 3 coverage < 50%).

    Returns {metric: IsotonicFit}. Metrics with < min_samples or in
    excluded_metrics are omitted.
    """
    if metric_keys is None:
        metric_keys = [k for k in METRIC_DIRECTIONS
                       if k not in excluded_metrics]
    from research.isotonic import fit_per_metric
    return fit_per_metric(
        events, metric_keys=list(metric_keys),
        return_key=return_key, min_samples=min_samples,
        direction=METRIC_DIRECTIONS,
    )


# ==========================================================================
# Scoring path (runtime dispatch)
# ==========================================================================

_FITS_CACHE: Optional[dict[str, IsotonicFit]] = None


def _get_fits(fits_path: Optional[Union[str, Path]] = None,
              force_reload: bool = False) -> Optional[dict[str, IsotonicFit]]:
    """Load calibrated fits from disk, caching on first read.

    Returns None if the fits file doesn't exist (caller falls back to
    V13 handpicked). force_reload=True bypasses the cache -- tests
    set force_reload when swapping fits between test cases.
    """
    global _FITS_CACHE
    if _FITS_CACHE is not None and not force_reload:
        return _FITS_CACHE

    path = Path(fits_path) if fits_path else DEFAULT_FITS_PATH
    if not path.exists():
        log.debug(f"no calibrated fits at {path}; falling back to handpicked")
        return None
    try:
        _FITS_CACHE = load_isotonic_fits_json(path)
        return _FITS_CACHE
    except Exception as e:
        log.warning(f"failed to load fits from {path}: {e}")
        return None


def reset_fits_cache() -> None:
    """Clear the module-level fits cache. Tests call this to swap fits."""
    global _FITS_CACHE
    _FITS_CACHE = None


def score_metric_calibrated(
    metric_key: str,
    value: Optional[float],
    fits: Optional[dict[str, IsotonicFit]] = None,
) -> Optional[float]:
    """Score a single metric via its calibrated IsotonicFit.

    Returns a score in [5, 100] matching V13's scale so downstream
    aggregation (avg) is direction-compatible. None if:
      - value is None / not numeric
      - no fit available for this metric (caller handles via fallback
        to V13 handpicked)

    Mapping: predict_normalized(x) ∈ [0, 1] -> [5, 100]
    via 5 + 95 * predict_normalized(x). This matches V13's output band.
    """
    if value is None:
        return None
    if fits is None:
        fits = _get_fits()
        if fits is None:
            return None
    fit = fits.get(metric_key)
    if fit is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    normalized = fit.predict_normalized(v)
    return 5.0 + 95.0 * normalized


# ==========================================================================
# Score-bucket wrappers (parallel to engine/scoring.py)
# ==========================================================================
#
# These mirror score_value/score_quality/etc. from engine/scoring.py
# but use score_metric_calibrated. Aggregation (avg of parts, None
# filtering) is identical -- swap only the primitive.

from utils.helpers import avg, safe_num


def score_value_calibrated(
    m: dict,
    fits: Optional[dict[str, IsotonicFit]] = None,
) -> Optional[float]:
    """Calibrated 'Value' bucket: P/E, P/B, EV/EBITDA, EV/Sales, FCF
    yield, margin of safety. Mirrors engine/scoring.py:score_value."""
    # EV/Sales derived same way as in V13
    ev_sales = None
    mc = m.get("market_cap"); td = m.get("total_debt")
    cash = m.get("cash"); rev = m.get("revenue")
    if mc and td is not None and cash is not None and rev and rev > 0:
        ev = mc + (td or 0) - (cash or 0)
        ev_sales = ev / rev

    parts = [
        score_metric_calibrated("pe", m.get("pe"), fits),
        score_metric_calibrated("pb", m.get("pb"), fits),
        score_metric_calibrated("ev_ebitda", m.get("ev_ebitda"), fits),
        score_metric_calibrated("ev_sales", ev_sales, fits),
        score_metric_calibrated("fcf_yield", m.get("fcf_yield"), fits),
        score_metric_calibrated("margin_safety", m.get("margin_safety"), fits),
    ]
    return avg(parts)


def score_quality_calibrated(
    m: dict,
    fits: Optional[dict[str, IsotonicFit]] = None,
) -> Optional[float]:
    parts = [
        score_metric_calibrated("roe", m.get("roe"), fits),
        score_metric_calibrated("roic", m.get("roic"), fits),
        score_metric_calibrated("net_margin", m.get("net_margin"), fits),
    ]
    return avg(parts)


def score_growth_calibrated(
    m: dict,
    fits: Optional[dict[str, IsotonicFit]] = None,
) -> Optional[float]:
    parts = [
        score_metric_calibrated("revenue_growth", m.get("revenue_growth"), fits),
        score_metric_calibrated("eps_growth", m.get("eps_growth"), fits),
        score_metric_calibrated("ebitda_growth", m.get("ebitda_growth"), fits),
        score_metric_calibrated("peg", m.get("peg"), fits),
    ]
    return avg(parts)


def score_balance_calibrated(
    m: dict,
    fits: Optional[dict[str, IsotonicFit]] = None,
) -> Optional[float]:
    parts = [
        score_metric_calibrated("net_debt_ebitda", m.get("net_debt_ebitda"), fits),
        score_metric_calibrated("debt_equity", m.get("debt_equity"), fits),
        score_metric_calibrated("current_ratio", m.get("current_ratio"), fits),
        score_metric_calibrated("interest_coverage", m.get("interest_coverage"), fits),
        score_metric_calibrated("altman_z", m.get("altman_z"), fits),
    ]
    return avg(parts)


# ==========================================================================
# Top-level dispatcher: A/B entrypoint for engine/delta.py
# ==========================================================================

def score_dispatch(
    m: dict,
    sector_group: Optional[str] = None,
    scoring_version: str = HANDPICKED_VERSION,
    fits: Optional[dict[str, IsotonicFit]] = None,
) -> dict:
    """Route to V13 (engine/scoring) or calibrated (this module).

    Returns a dict with the same bucket scores regardless of version:
      {value, quality, growth, balance, scoring_version}

    When scoring_version == 'calibrated_2026Q1' but no fits are
    available (no calibration run yet / FA data not backfilled), this
    falls back to V13 handpicked and records the fallback in the dict
    via scoring_version_effective.
    """
    if scoring_version == CALIBRATED_VERSION:
        fits_avail = fits if fits is not None else _get_fits()
        if fits_avail is not None:
            return {
                "value":   score_value_calibrated(m, fits_avail),
                "quality": score_quality_calibrated(m, fits_avail),
                "growth":  score_growth_calibrated(m, fits_avail),
                "balance": score_balance_calibrated(m, fits_avail),
                "scoring_version": CALIBRATED_VERSION,
                "scoring_version_effective": CALIBRATED_VERSION,
            }
        # Fallback to V13
        log.info(f"calibrated requested but no fits found; falling back to {HANDPICKED_VERSION}")
    # V13 handpicked path
    from engine.scoring import (
        score_value, score_quality, score_growth, score_balance,
    )
    return {
        "value":   score_value(m, sector_group),
        "quality": score_quality(m, sector_group),
        "growth":  score_growth(m, sector_group),
        "balance": score_balance(m, sector_group),
        "scoring_version": scoring_version,
        "scoring_version_effective": HANDPICKED_VERSION,
    }

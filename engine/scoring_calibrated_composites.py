"""Phase 7 — Composite bucket calibration scaffold.

V13 has 3 composite buckets that don't reduce to single-metric
isotonic fits because their internal structure has discrete
branching:

  earnings_quality:
    cfo_to_ni     → continuous, isotonic-friendly
    fcf_margin    → continuous, isotonic-friendly
    beneish_m     → BRANCHED: M < -2.22 → 90, M < -1.78 → 65, else 25
                    Threshold-based, not monotonic in fit space.

  moat:
    gm_stability  → derived: |gross_margin - gm_prev|, isotonic ↓
    roa_stability → derived: |roa - roa_prev|, isotonic ↓
    pricing_power → continuous gross_margin, isotonic ↑
    op_power      → continuous operating_margin, isotonic ↑
    at_trend      → BRANCHED: |Δ| < 0.02 → 55, Δ ≥ 0 → 75, else 35
                    Categorical-trend, not isotonic.

  capital_efficiency:
    dilution      → BRANCHED: share_change ≤ 0 → 100, else cap-at-100
                    Asymmetric, isotonic-incompatible without
                    transformation.
    capex_to_rev  → continuous, isotonic ↓
    roic_quality  → continuous (roic), isotonic ↑

Phase 7 strategy:

  Decompose composite buckets into:
    (a) ISOTONIC sub-components — fit individually with calibration
    (b) DERIVED sub-components — compute from raw metrics first
        (e.g. |Δgm|, then fit isotonic)
    (c) BRANCHED sub-components — kept as-is (V13 logic), wrapped
        in a unified scorer that returns a normalized [5, 100] band

  Combine via weighted average with weights tunable per bucket
  (default = equal weighting for now; calibration can fit weights).

This module defines the decomposition. Calibration of the isotonic
sub-fits + weight optimization is the Phase 7 deploy turn (Colab
+ commit).

NAMING:
  Composite metric_keys for storing in fa_isotonic_fits.json use the
  '_composite_' prefix to distinguish them from raw metrics:
    'cfo_to_ni'                          (raw, fits.json)
    'fcf_margin'                          (raw, fits.json)
    'beneish_m_composite_earnings'        (Phase 7 sub-fit)
    'gm_stability_composite_moat'         (Phase 7 sub-fit derived)
    'at_trend_composite_moat'             (Phase 7 sub-fit branched)
    'dilution_composite_capital'          (Phase 7 sub-fit branched)
    'capex_to_rev_composite_capital'      (Phase 7 sub-fit derived)
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("bistbull.scoring_calibrated_composites")


# ==========================================================================
# Sub-component decomposition specs
# ==========================================================================

# Each composite bucket's sub-components, with their type and how to
# compute them from raw metrics.
#
# type field:
#   'raw'      — direct metric lookup, fed to existing isotonic
#   'derived'  — compute via formula from raw metrics
#   'branched' — discrete logic, fall through to V13 sub-scorer

EARNINGS_QUALITY_COMPONENTS = {
    "cfo_to_ni":        {"type": "raw"},
    "fcf_margin":       {"type": "raw"},
    "beneish_m":        {"type": "branched"},
}

MOAT_COMPONENTS = {
    "pricing_power":    {"type": "raw", "source": "gross_margin"},
    "op_power":         {"type": "raw", "source": "operating_margin"},
    "gm_stability":     {"type": "derived",
                         "formula": "abs_delta",
                         "current": "gross_margin",
                         "prev": "gross_margin_prev"},
    "roa_stability":    {"type": "derived",
                         "formula": "abs_delta",
                         "current": "roa",
                         "prev": "roa_prev"},
    "at_trend":         {"type": "branched",
                         "current": "asset_turnover",
                         "prev": "asset_turnover_prev"},
}

CAPITAL_EFFICIENCY_COMPONENTS = {
    "dilution":         {"type": "branched", "source": "share_change"},
    "capex_to_rev":     {"type": "derived",
                         "formula": "capex_ratio",
                         "operating_cf": "operating_cf",
                         "free_cf": "free_cf",
                         "revenue": "revenue"},
    "roic_quality":     {"type": "raw", "source": "roic"},
}

COMPOSITE_BUCKETS = {
    "earnings_quality": EARNINGS_QUALITY_COMPONENTS,
    "moat": MOAT_COMPONENTS,
    "capital_efficiency": CAPITAL_EFFICIENCY_COMPONENTS,
}


# Direction map for the derived components (what the calibration target is)
COMPOSITE_DERIVED_DIRECTIONS = {
    # earnings_quality
    "beneish_m":        False,  # higher M = more manipulation suspicion
    # moat
    "gm_stability":     False,  # smaller |Δgm| = more stable margin
    "roa_stability":    False,  # smaller |Δroa| = more stable returns
    "at_trend":         True,   # higher Δat = improving operations
    # capital_efficiency
    "dilution":         False,  # higher share count growth = worse for shareholders
    "capex_to_rev":     False,  # higher capex ratio = lower FCF efficiency
}


# ==========================================================================
# Derived component formulae (compute from raw)
# ==========================================================================

def compute_derived(component_name: str, m: dict) -> Optional[float]:
    """Compute a derived sub-component value from raw metrics dict.

    Returns None if any required raw metric is missing.
    """
    spec_bucket = None
    spec = None
    for bucket, components in COMPOSITE_BUCKETS.items():
        if component_name in components:
            spec = components[component_name]
            spec_bucket = bucket
            break

    if spec is None or spec.get("type") != "derived":
        return None

    formula = spec.get("formula")

    if formula == "abs_delta":
        cur_key = spec.get("current")
        prev_key = spec.get("prev")
        cur = m.get(cur_key)
        prev = m.get(prev_key)
        if cur is None or prev is None:
            return None
        return abs(float(cur) - float(prev))

    if formula == "capex_ratio":
        ocf = m.get(spec.get("operating_cf"))
        fcf = m.get(spec.get("free_cf"))
        rev = m.get(spec.get("revenue"))
        if ocf is None or fcf is None or rev is None or rev <= 0:
            return None
        capex = abs(float(ocf) - float(fcf))
        return capex / float(rev)

    return None


# ==========================================================================
# Branched component scorers (V13 logic, returns [5, 100])
# ==========================================================================

def score_branched_beneish_m(value: Optional[float]) -> Optional[float]:
    """Beneish M-score thresholds (V13 logic)."""
    if value is None:
        return None
    if value < -2.22:
        return 90.0
    if value < -1.78:
        return 65.0
    return 25.0


def score_branched_at_trend(
    cur: Optional[float],
    prev: Optional[float],
) -> Optional[float]:
    """Asset turnover trend (V13 logic)."""
    if cur is None or prev is None:
        return None
    delta = cur - prev
    if abs(delta) < 0.02:
        return 55.0
    if delta >= 0:
        return 75.0
    return 35.0


def score_branched_dilution(share_change: Optional[float]) -> Optional[float]:
    """Share dilution penalty (V13 logic)."""
    if share_change is None:
        return None
    if share_change <= 0:
        return 100.0
    # graduated penalty for positive share_change (more issued = worse)
    if share_change <= 0.03:
        return 70.0
    if share_change <= 0.08:
        return 45.0
    if share_change <= 0.20:
        return 25.0
    return 5.0


# ==========================================================================
# Composite bucket scorer (unified)
# ==========================================================================

def score_composite_bucket(
    bucket_name: str,
    m: dict,
    fits: Optional[dict] = None,
) -> Optional[float]:
    """Score a composite bucket by combining its sub-components.

    bucket_name: one of 'earnings_quality', 'moat', 'capital_efficiency'

    Returns a [5, 100] band score (V13-compatible) or None if no
    sub-components produced a value.

    Strategy:
      - Raw components: route to score_metric_calibrated if a fit
        exists, otherwise fall through to V13 scoring (handled by
        caller — this scaffold just computes raw values).
      - Derived components: compute via compute_derived, then
        route to calibrated fit if available.
      - Branched components: V13 logic always (Phase 7 doesn't
        try to calibrate these — they're discrete by nature).

    For Phase 7 scaffold, calibrated fits are not yet available
    for derived sub-components, so this falls back to V13's
    composite scoring entirely. The decomposition is in place
    so that once Phase 7 deploy generates fits for the derived
    sub-components, this dispatcher can use them.
    """
    if bucket_name not in COMPOSITE_BUCKETS:
        return None

    components = COMPOSITE_BUCKETS[bucket_name]
    sub_scores: list[float] = []

    for comp_name, spec in components.items():
        comp_type = spec.get("type")

        if comp_type == "branched":
            # Always V13 logic
            if comp_name == "beneish_m":
                s = score_branched_beneish_m(m.get("beneish_m"))
            elif comp_name == "at_trend":
                s = score_branched_at_trend(
                    m.get(spec.get("current")),
                    m.get(spec.get("prev")),
                )
            elif comp_name == "dilution":
                s = score_branched_dilution(
                    m.get(spec.get("source")),
                )
            else:
                s = None
            if s is not None:
                sub_scores.append(s)
            continue

        # raw / derived: future Phase 7 calibration target
        # For now scaffold returns None for these (caller should
        # use V13 scoring for the bucket as a whole)

    if not sub_scores:
        return None
    # Equal weighting for now; Phase 7 deploy can tune weights
    return sum(sub_scores) / len(sub_scores)


def get_branched_component_count(bucket_name: str) -> int:
    """How many branched components does this bucket have?

    Useful for Phase 7 deploy: branched components can't be
    calibrated, so they remain V13 logic always. Buckets with
    higher branched-fraction are harder to fully calibrate.
    """
    if bucket_name not in COMPOSITE_BUCKETS:
        return 0
    components = COMPOSITE_BUCKETS[bucket_name]
    return sum(1 for _, spec in components.items()
               if spec.get("type") == "branched")

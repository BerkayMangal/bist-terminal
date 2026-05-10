# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# features/bullalfa_toplaniyor.py
#
# §12 — TOPLANIYOR detection + accumulation_strength scoring.
#
# TOPLANIYOR is a SPECIFIC POSITIVE accumulation signal — earned, not
# the default. The orchestrator routes to TOPLANIYOR only when:
#   1. Quality grade ≠ D (D-grade w/o setup → SAKİN)
#   2. Trend not broken (price > ema50 OR ema20 > ema50)
#   3. BB compressed (bb_width_today < bb_width_60d_p35)
#   4. Soft volume rise (rvol_5d_avg ∈ [1.05, 1.50])
#   5. AT LEAST ONE corroborating signal:
#        - ADX rising over last 10 bars
#        - Higher lows over last 10 bars (≥ TOPLANIYOR_HIGHER_LOWS_MIN)
#        - Up-day vol / down-day vol > 1.4 over last 10 bars
#        - No HIZLI/SWING/POZİSYON trigger fired
#
# accumulation_strength ∈ [0, 100] — used by §17 ranking
# (capped at OPPORTUNITY_TOPLANIYOR_CAP). Composed from the four
# weights in BULLALFA_PARAMS["toplaniyor"]["accumulation_strength_weights"]
# (validated at import to sum to 100).
# ================================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine.bullalfa_params import (
    ACC_STRENGTH_ADX_FLOOR,
    ACC_STRENGTH_BUYING_PRESSURE_NORMALISER,
    BULLALFA_PARAMS,
    TOPLANIYOR_HIGHER_LOWS_MIN,
    TOPLANIYOR_LOOKBACK_BARS,
    TOPLANIYOR_RVOL_5D_HIGH,
    TOPLANIYOR_RVOL_5D_LOW,
    TOPLANIYOR_UD_VOL_RATIO_MIN,
)
from features.bullalfa_features import EngineInputs

__all__ = [
    "ToplaniyorAssessment",
    "evaluate_toplaniyor",
    "compute_accumulation_strength",
    "EXCLUDED_QUALITY_GRADES",
    "ACTIONABLE_MODES",
]


# Quality grades that disqualify TOPLANIYOR per §12 ("≠ D").
EXCLUDED_QUALITY_GRADES: frozenset[str] = frozenset({"D"})

# Modes that, if any has fired, mean we should NOT label this TOPLANIYOR.
# Spec §12: "No HIZLI/SWING/POZİSYON trigger fired (otherwise upgrade
# to that mode)."
ACTIONABLE_MODES: frozenset[str] = frozenset({"HIZLI", "SWING", "POZİSYON"})


# ----------------------------------------------------------------
# Result dataclass
# ----------------------------------------------------------------

@dataclass(frozen=True)
class ToplaniyorAssessment:
    """Result of `evaluate_toplaniyor` — used by the orchestrator.

    `eligible` is True only when ALL required conditions are met AND at
    least one corroborating condition fires AND quality_grade is not D
    AND no actionable mode is already firing.

    `required_failures` lists the §12 "Required (all of)" predicates
    that did NOT pass — useful for `why_now` phrasing on the negative
    side and for diagnostics.

    `corroborating_passes` lists the §12 "At least one of" predicates
    that DID pass — used by `why_now` to populate the positive bullets.

    `accumulation_strength` is the §17-input score in [0, 100].
    Computed regardless of `eligible` so callers can show "would-be"
    strength if a single requirement is missing — but the orchestrator
    only uses it when `eligible is True`.
    """

    eligible: bool
    required_failures: tuple[str, ...]
    corroborating_passes: tuple[str, ...]
    accumulation_strength: int
    # Diagnostic: the most relevant blocker reason, if any. Stable code.
    blocker: Optional[str] = None


# ----------------------------------------------------------------
# accumulation_strength
# ----------------------------------------------------------------

def _adx_rise_score(
    adx_today: Optional[float],
    adx_10d_ago: Optional[float],
) -> float:
    """0–1 component for ADX rising over `TOPLANIYOR_LOOKBACK_BARS` bars.

    Normalises the ADX delta by `ACC_STRENGTH_ADX_FLOOR` (the ADX-points
    rise that counts as "fully developing trend" for v1). 5 points over
    10 bars is the v1 ceiling; deltas above that all score 1.0.
    """
    if adx_today is None or adx_10d_ago is None:
        return 0.0
    delta = float(adx_today) - float(adx_10d_ago)
    if delta <= 0.0:
        return 0.0
    return min(1.0, delta / ACC_STRENGTH_ADX_FLOOR)


def _tightness_score(
    bb_today: Optional[float],
    bb_p35: Optional[float],
) -> float:
    """0–1 component for BB-width tightness vs the 60-day 35th-percentile.

    1.0 when bb_today ≤ 0 (degenerate but not an error — perfectly tight),
    sliding to 0.0 at bb_today == bb_p35 (the boundary), 0 above.
    """
    if bb_today is None or bb_p35 is None:
        return 0.0
    bt = float(bb_today)
    bp = float(bb_p35)
    if bp <= 0.0:
        return 0.0
    if bt >= bp:
        return 0.0
    if bt <= 0.0:
        return 1.0
    return max(0.0, min(1.0, (bp - bt) / bp))


def _buying_pressure_score(ud_ratio_10d: Optional[float]) -> float:
    """0–1 component for up-vol/down-vol over `TOPLANIYOR_LOOKBACK_BARS`.

    Anchored at 1.0 (parity). Deltas above parity normalised by
    `ACC_STRENGTH_BUYING_PRESSURE_NORMALISER` (default 0.5 → ratio of
    1.5 saturates the component at 1.0).
    """
    if ud_ratio_10d is None:
        return 0.0
    delta = float(ud_ratio_10d) - 1.0
    if delta <= 0.0:
        return 0.0
    if ACC_STRENGTH_BUYING_PRESSURE_NORMALISER <= 0.0:
        return 1.0  # degenerate config; still fail-safe
    return min(1.0, delta / ACC_STRENGTH_BUYING_PRESSURE_NORMALISER)


def _structure_score(higher_lows_count_10d: Optional[int]) -> float:
    """0–1 component for higher-low structure tightening.

    Saturates at TOPLANIYOR_HIGHER_LOWS_MIN — the spec's "at least N
    higher lows over the last 10 bars" floor for the corroborating
    bullet. A count that just clears the floor gets a full 1.0 score
    on this component; lower counts scale linearly.
    """
    if higher_lows_count_10d is None:
        return 0.0
    n = int(higher_lows_count_10d)
    if n <= 0 or TOPLANIYOR_HIGHER_LOWS_MIN <= 0:
        return 0.0
    return min(1.0, n / float(TOPLANIYOR_HIGHER_LOWS_MIN))


def compute_accumulation_strength(inp: EngineInputs) -> int:
    """Compute the §17 accumulation_strength score in [0, 100].

    Composed from four 0–1 components weighted per
    BULLALFA_PARAMS["toplaniyor"]["accumulation_strength_weights"]
    (sums to 100, enforced at params import).

    Components:
      - adx_rise         (default 25) — how fast trend is forming
      - tightness        (default 30) — BB-width compression depth
      - buying_pressure  (default 25) — up-vol vs down-vol asymmetry
      - structure        (default 20) — higher-lows count

    Returns an int (rounded) for sort stability in §17 ranking.
    """
    weights = BULLALFA_PARAMS["toplaniyor"]["accumulation_strength_weights"]

    raw = (
        weights["adx_rise"]        * _adx_rise_score(inp.adx_today, inp.adx_10d_ago)
        + weights["tightness"]       * _tightness_score(inp.bb_width_today, inp.bb_width_60d_p35)
        + weights["buying_pressure"] * _buying_pressure_score(inp.up_down_vol_ratio_10d)
        + weights["structure"]       * _structure_score(inp.higher_lows_count_10d)
    )
    return int(round(max(0.0, min(100.0, raw))))


# ----------------------------------------------------------------
# Eligibility
# ----------------------------------------------------------------

def _trend_intact(inp: EngineInputs) -> bool:
    """Spec §12: trend not broken — price > ema50 OR ema20 > ema50."""
    p, e20, e50 = inp.price, inp.ema20, inp.ema50
    if p is None or e50 is None:
        return False
    if p > e50:
        return True
    if e20 is not None and e20 > e50:
        return True
    return False


def _bb_compressed(inp: EngineInputs) -> bool:
    """bb_width_today < bb_width_60d_p35 (the §12 threshold)."""
    bt, bp = inp.bb_width_today, inp.bb_width_60d_p35
    if bt is None or bp is None:
        return False
    return float(bt) < float(bp)


def _soft_volume_rise(inp: EngineInputs) -> bool:
    """rvol_5d_avg ∈ (low, high) — sustained mild interest, not breakout.

    Boundary semantics chosen to match spec §12 phrasing
    "rvol_5d_avg ∈ (1.05, 1.50)" — open interval on both sides;
    a 5-day average sitting exactly at 1.50 already implies a
    breakout-grade volume environment.
    """
    rv = inp.rvol_5d_avg
    if rv is None:
        return False
    return TOPLANIYOR_RVOL_5D_LOW < float(rv) < TOPLANIYOR_RVOL_5D_HIGH


def _adx_rising(inp: EngineInputs) -> bool:
    """ADX(14) higher today than `TOPLANIYOR_LOOKBACK_BARS` bars ago."""
    a, b = inp.adx_today, inp.adx_10d_ago
    if a is None or b is None:
        return False
    return float(a) > float(b)


def _higher_lows(inp: EngineInputs) -> bool:
    """At least `TOPLANIYOR_HIGHER_LOWS_MIN` higher lows in the lookback."""
    n = inp.higher_lows_count_10d
    if n is None:
        return False
    return int(n) >= TOPLANIYOR_HIGHER_LOWS_MIN


def _up_down_vol_dominant(inp: EngineInputs) -> bool:
    """up-day vol / down-day vol > `TOPLANIYOR_UD_VOL_RATIO_MIN`."""
    r = inp.up_down_vol_ratio_10d
    if r is None:
        return False
    return float(r) > TOPLANIYOR_UD_VOL_RATIO_MIN


def evaluate_toplaniyor(
    *,
    inp: EngineInputs,
    quality_grade: Optional[str],
    actionable_mode_already_fired: bool = False,
) -> ToplaniyorAssessment:
    """Return whether this stock qualifies for TOPLANIYOR per §12.

    Args:
      inp: engine inputs from `bullalfa_features.build_engine_inputs`.
      quality_grade: "A+" | "A" | "B" | "C" | "D" — D excludes per §12.
      actionable_mode_already_fired: True if the orchestrator has
        already determined that HIZLI/SWING/POZİSYON triggers fire on
        this bar; in that case the priority order in §11 routes the
        signal to that actionable mode rather than TOPLANIYOR. Pass
        False at the assessment stage when you want to know whether
        TOPLANIYOR would fire AS A FALLBACK.

    Returns a `ToplaniyorAssessment`. The orchestrator should only
    label the signal TOPLANIYOR when `eligible is True`.
    """
    # Compute strength regardless — useful for diagnostics + ranking.
    strength = compute_accumulation_strength(inp)

    # Required-set predicates. Use stable string IDs; tests assert on them.
    required_failures: list[str] = []

    if quality_grade in EXCLUDED_QUALITY_GRADES:
        required_failures.append("quality_excluded")

    if not _trend_intact(inp):
        required_failures.append("trend_broken")

    if not _bb_compressed(inp):
        required_failures.append("bb_not_compressed")

    if not _soft_volume_rise(inp):
        required_failures.append("rvol_5d_outside_band")

    # Corroborating-set predicates.
    corroborating: list[str] = []
    if _adx_rising(inp):
        corroborating.append("adx_rising")
    if _higher_lows(inp):
        corroborating.append("higher_lows")
    if _up_down_vol_dominant(inp):
        corroborating.append("up_down_vol_dominant")
    if not actionable_mode_already_fired:
        corroborating.append("no_upgrade")

    # Final eligibility:
    blocker: Optional[str] = None
    if required_failures:
        blocker = required_failures[0]
        eligible = False
    elif not corroborating:
        # All required conditions met but nothing corroborating.
        # Per §12 "At least one of (corroborating)".
        blocker = "no_corroborating_signal"
        eligible = False
    elif actionable_mode_already_fired:
        # Spec §12: "Strong setup but already HIZLI-eligible → upgrades
        # to HIZLI, not TOPLANIYOR." We surface this explicitly so the
        # orchestrator can log the routing choice.
        blocker = "actionable_mode_priority"
        eligible = False
    else:
        eligible = True

    return ToplaniyorAssessment(
        eligible=eligible,
        required_failures=tuple(required_failures),
        corroborating_passes=tuple(corroborating),
        accumulation_strength=strength,
        blocker=blocker,
    )

# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# features/bullalfa_calibration.py
#
# Layer 3 — confidence calibration (spec §9).
#
# v1 launch path (no backtest dependency):
#   raw_combined = w_q · quality + w_t · technical + w_e · edge
#   squashed     = sigmoid_squash(raw_combined)
#   final        = clip(squashed × (1 - exhaustion) × macro_mult × age_mult, 0, 100)
#
# v2 hook (post-launch): when an isotonic fit per mode is available
# in `phase_4_isotonic_fits.json`, swap the squash for
# `isotonic[mode].transform(raw_combined)`. Dampeners chain unchanged.
#
# All weights and constants are sourced from BULLALFA_PARAMS — never
# inlined. v2 calibration writes new values into params; logic untouched.
# ================================================================

from __future__ import annotations

import math
from typing import Optional

from engine.bullalfa_params import BULLALFA_PARAMS, SIGMOID_MIDPOINT, SIGMOID_STEEPNESS

__all__ = [
    "sigmoid_squash",
    "combo_weights_for_mode",
    "combine_raw",
    "apply_dampeners",
    "compute_confidence",
    "calibration_phase",
]


_ACTIONABLE_MODES = frozenset({"HIZLI", "SWING", "POZİSYON"})


# ----------------------------------------------------------------
# Pure squash
# ----------------------------------------------------------------

def sigmoid_squash(
    x: float,
    midpoint: float = SIGMOID_MIDPOINT,
    steepness: float = SIGMOID_STEEPNESS,
) -> float:
    """Squash a raw combined score [0, 100+] to a confidence [0, 100].

    100 / (1 + exp(-steepness × (x - midpoint))).

    Properties (covered by tests):
    - Strictly monotone increasing in x.
    - Bounded in (0, 100), exclusive at the endpoints.
    - sigmoid_squash(midpoint) == 50 by construction.
    """
    # Guard against pathological steepness × Δ that overflows exp.
    z = -float(steepness) * (float(x) - float(midpoint))
    if z > 700.0:        # exp(700) is the max double; clamp to avoid overflow.
        return 0.0
    if z < -700.0:
        return 100.0
    return 100.0 / (1.0 + math.exp(z))


# ----------------------------------------------------------------
# Mode weights
# ----------------------------------------------------------------

def combo_weights_for_mode(mode: str) -> dict[str, float]:
    """Return the {quality, technical, edge} weight triple for an actionable mode.

    Raises KeyError on unknown / non-actionable mode — calibration only
    runs for HIZLI/SWING/POZİSYON. Validate upstream before calling.
    """
    weights = BULLALFA_PARAMS["calibration"]["combo_weights"].get(mode)
    if weights is None:
        raise KeyError(f"combo_weights_for_mode: unsupported mode {mode!r}")
    return dict(weights)  # defensive copy — callers should not mutate params


def combine_raw(
    quality_score: float,
    technical_score: float,
    edge_score: float,
    mode: str,
) -> float:
    """Linear blend per §9 — `q·w_q + t·w_t + e·w_e`.

    Inputs in [0, 100]. Output also in [0, 100] when weights sum to 1
    (enforced at import in `bullalfa_params._validate_weight_tables`).
    """
    w = combo_weights_for_mode(mode)
    return (
        float(quality_score)   * w["quality"]
        + float(technical_score) * w["technical"]
        + float(edge_score)      * w["edge"]
    )


# ----------------------------------------------------------------
# Dampener composition
# ----------------------------------------------------------------

def apply_dampeners(
    squashed: float,
    *,
    exhaustion: float = 0.0,
    macro_mult: float = 1.0,
    age_mult: float = 1.0,
) -> float:
    """Apply the multiplicative dampener chain.

    final = squashed × (1 - exhaustion) × macro_mult × age_mult,
    clipped to [0, 100].

    `exhaustion` is the §8 Engine-7 penalty in [0, 0.7] — already capped
    by `engine_7_exhaustion`. Values outside [0, 1] are clamped here as
    a defensive guard, but the canonical input is the engine's output.

    `macro_mult` and `age_mult` are pass-throughs from BULLALFA_PARAMS
    or the orchestrator — the calibrator does not look up the regime
    itself, to keep this layer pure / unit-testable.
    """
    exh   = max(0.0, min(1.0, float(exhaustion)))
    mm    = max(0.0, float(macro_mult))
    am    = max(0.0, float(age_mult))
    sq    = max(0.0, min(100.0, float(squashed)))
    final = sq * (1.0 - exh) * mm * am
    return max(0.0, min(100.0, final))


# ----------------------------------------------------------------
# End-to-end confidence
# ----------------------------------------------------------------

def compute_confidence(
    *,
    quality_score: float,
    technical_score: float,
    edge_score: float,
    mode: str,
    exhaustion: float = 0.0,
    macro_mult: float = 1.0,
    age_mult: float = 1.0,
) -> dict[str, float | str]:
    """Full Layer-3 calibration for an actionable mode.

    Returns a dict matching the §19 `confidence` block shape:
        {
          "raw_combined":     float [0, 100],
          "squashed":         float (0, 100),
          "exhaustion_factor":float [0, 1],
          "macro_mult":       float ≥ 0,
          "age_mult":         float ≥ 0,
          "final":            float [0, 100],
          "phase":            "v1_heuristic" | "v2_isotonic",
        }

    Non-actionable modes (TOPLANIYOR / SAKİN / UZAK DUR) MUST NOT call
    this function — their opportunity ranking is handled in
    `bullalfa_ranking.opportunity_score`.
    """
    if mode not in _ACTIONABLE_MODES:
        raise ValueError(
            f"compute_confidence: mode {mode!r} is non-actionable; "
            "calibration only applies to HIZLI/SWING/POZİSYON"
        )

    raw      = combine_raw(quality_score, technical_score, edge_score, mode)
    squashed = sigmoid_squash(raw)
    final    = apply_dampeners(
        squashed,
        exhaustion=exhaustion,
        macro_mult=macro_mult,
        age_mult=age_mult,
    )

    return {
        "raw_combined":      round(raw, 4),
        "squashed":          round(squashed, 4),
        "exhaustion_factor": round(max(0.0, min(1.0, float(exhaustion))), 4),
        "macro_mult":        round(max(0.0, float(macro_mult)), 4),
        "age_mult":          round(max(0.0, float(age_mult)), 4),
        "final":             round(final, 2),
        "phase":             calibration_phase(),
    }


# ----------------------------------------------------------------
# Phase label
# ----------------------------------------------------------------

def calibration_phase(isotonic_fits_loaded: Optional[bool] = None) -> str:
    """Return the calibration phase label.

    By default reads `BULLALFA_PARAMS["phase"]` ("v1_heuristic" until
    v2 fits land). Pass `isotonic_fits_loaded=True` to force the v2
    label — used by the orchestrator after it successfully loads
    `phase_4_isotonic_fits.json`.

    Spec §9 mandates a `Kalibrasyon: ön-aşama` badge while in v1.
    """
    if isotonic_fits_loaded is True:
        return "v2_isotonic"
    if isotonic_fits_loaded is False:
        return "v1_heuristic"
    return str(BULLALFA_PARAMS.get("phase", "v1_heuristic"))

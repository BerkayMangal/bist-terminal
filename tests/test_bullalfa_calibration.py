# ================================================================
# tests/test_bullalfa_calibration.py
#
# Spec §22 coverage:
#   - sigmoid_squash monotonicity + bounds
#   - Mode weights sum to 1
#   - Macro × exhaustion × age multiplier composition
#   - Isotonic loaded when present (v2 hook)
#   - Phase label correctness
# ================================================================

from __future__ import annotations

import math

import pytest

from engine.bullalfa_params import (
    BULLALFA_PARAMS,
    SIGMOID_MIDPOINT,
    SIGMOID_STEEPNESS,
)
from features.bullalfa_calibration import (
    apply_dampeners,
    calibration_phase,
    combine_raw,
    combo_weights_for_mode,
    compute_confidence,
    sigmoid_squash,
)


# ================================================================
# sigmoid_squash — bounds + monotonicity + identity at midpoint
# ================================================================

class TestSigmoidSquash:

    def test_midpoint_yields_50(self):
        assert sigmoid_squash(SIGMOID_MIDPOINT) == pytest.approx(50.0, abs=1e-9)

    def test_strictly_monotone_increasing(self):
        xs = [-100, -10, 0, 10, 30, 55, 70, 90, 110, 200]
        ys = [sigmoid_squash(x) for x in xs]
        for a, b in zip(ys, ys[1:]):
            assert a < b, f"sigmoid not monotone: {a} not < {b}"

    @pytest.mark.parametrize("x", [-1e6, -1000, -100, 0, 50, 100, 1000, 1e6])
    def test_bounded_in_zero_one_hundred(self, x):
        y = sigmoid_squash(x)
        assert 0.0 <= y <= 100.0

    def test_extreme_negative_clamps_without_overflow(self):
        # Very large negative input would overflow exp() in naive code.
        # The implementation guards by clamping to 0 / 100 at the
        # exponent boundary (~700).
        assert sigmoid_squash(-1e9) == pytest.approx(0.0, abs=1e-9)

    def test_extreme_positive_clamps_without_overflow(self):
        assert sigmoid_squash(1e9) == pytest.approx(100.0, abs=1e-9)

    def test_steepness_zero_collapses_to_50(self):
        # Defensive — degenerate config should not crash.
        assert sigmoid_squash(0, midpoint=0, steepness=0) == pytest.approx(50.0)

    def test_uses_default_midpoint_and_steepness_from_params(self):
        # Re-deriving the same value with explicit params should match.
        x = 73.0
        a = sigmoid_squash(x)
        b = sigmoid_squash(x, midpoint=SIGMOID_MIDPOINT, steepness=SIGMOID_STEEPNESS)
        assert a == pytest.approx(b, abs=1e-12)


# ================================================================
# Mode weights — sum to 1; required modes present
# ================================================================

class TestComboWeights:

    @pytest.mark.parametrize("mode", ["HIZLI", "SWING", "POZİSYON"])
    def test_weights_sum_to_one(self, mode):
        w = combo_weights_for_mode(mode)
        assert set(w.keys()) == {"quality", "technical", "edge"}
        s = sum(w.values())
        assert s == pytest.approx(1.0, abs=1e-9)

    def test_unknown_mode_raises(self):
        with pytest.raises(KeyError):
            combo_weights_for_mode("TOPLANIYOR")

    def test_returned_dict_is_a_copy(self):
        # Mutating the return must not leak into BULLALFA_PARAMS.
        w = combo_weights_for_mode("HIZLI")
        w["quality"] = 999.0
        fresh = combo_weights_for_mode("HIZLI")
        assert fresh["quality"] != 999.0

    def test_combine_raw_arithmetic(self):
        # SWING: q·0.35 + t·0.40 + e·0.25 = 80·0.35 + 70·0.40 + 60·0.25 = 71
        assert combine_raw(80, 70, 60, "SWING") == pytest.approx(71.0, abs=1e-9)

    def test_combine_raw_with_zeroed_inputs_returns_zero(self):
        assert combine_raw(0, 0, 0, "HIZLI") == 0.0

    def test_combine_raw_at_max_inputs_returns_100(self):
        # When weights sum to 1 and all inputs are 100, result is 100.
        assert combine_raw(100, 100, 100, "POZİSYON") == pytest.approx(100.0, abs=1e-9)


# ================================================================
# Multiplier composition — macro × exhaustion × age
# ================================================================

class TestApplyDampeners:

    def test_identity_with_no_dampeners(self):
        assert apply_dampeners(80.0) == pytest.approx(80.0)

    def test_exhaustion_alone(self):
        # 80 × (1 - 0.2) = 64
        assert apply_dampeners(80.0, exhaustion=0.2) == pytest.approx(64.0)

    def test_macro_alone(self):
        assert apply_dampeners(80.0, macro_mult=0.7) == pytest.approx(56.0)

    def test_age_alone(self):
        assert apply_dampeners(80.0, age_mult=0.9) == pytest.approx(72.0)

    def test_full_chain_composes_multiplicatively(self):
        # 80 × 0.8 × 0.7 × 0.9 = 40.32
        out = apply_dampeners(80.0, exhaustion=0.2, macro_mult=0.7, age_mult=0.9)
        assert out == pytest.approx(40.32, abs=1e-6)

    def test_exhaustion_clamped_to_one(self):
        # exhaustion > 1 clamps to 1 → factor (1-1) = 0 → final = 0
        assert apply_dampeners(80.0, exhaustion=2.0) == 0.0

    def test_negative_exhaustion_clamped_to_zero(self):
        assert apply_dampeners(80.0, exhaustion=-0.5) == pytest.approx(80.0)

    def test_negative_macro_clamped_to_zero(self):
        assert apply_dampeners(80.0, macro_mult=-1.0) == 0.0

    def test_output_clipped_to_max_100(self):
        # 80 × 1.5 = 120, clipped to 100
        assert apply_dampeners(80.0, macro_mult=1.5) == 100.0

    def test_squashed_clipped_to_zero(self):
        assert apply_dampeners(-5.0) == 0.0


# ================================================================
# compute_confidence — full pipeline
# ================================================================

class TestComputeConfidence:

    def test_returns_complete_schema(self):
        c = compute_confidence(
            quality_score=80, technical_score=70, edge_score=60,
            mode="SWING",
        )
        expected_keys = {
            "raw_combined", "squashed", "exhaustion_factor",
            "macro_mult", "age_mult", "final", "phase",
        }
        assert set(c.keys()) == expected_keys

    def test_defaults_yield_squashed_value(self):
        c = compute_confidence(
            quality_score=80, technical_score=70, edge_score=60,
            mode="SWING",
        )
        # raw_combined = 71; squashed = sigmoid(71) ≈ 78.245
        assert c["raw_combined"] == pytest.approx(71.0, abs=1e-3)
        assert c["squashed"] == pytest.approx(sigmoid_squash(71.0), abs=1e-3)
        # No dampeners → final == squashed (within rounding)
        assert c["final"] == pytest.approx(c["squashed"], abs=1e-2)

    def test_full_dampener_chain(self):
        c = compute_confidence(
            quality_score=80, technical_score=70, edge_score=60,
            mode="SWING",
            exhaustion=0.2, macro_mult=0.8, age_mult=1.0,
        )
        # raw=71 → squashed≈78.245 → final ≈ 78.245 × 0.8 × 0.8 ≈ 50.08
        expected = sigmoid_squash(71.0) * 0.8 * 0.8
        assert c["final"] == pytest.approx(round(expected, 2), abs=1e-1)

    @pytest.mark.parametrize("mode", ["TOPLANIYOR", "SAKİN", "UZAK DUR"])
    def test_non_actionable_modes_raise(self, mode):
        with pytest.raises(ValueError):
            compute_confidence(
                quality_score=80, technical_score=70, edge_score=60,
                mode=mode,
            )

    def test_phase_label_pulls_from_params(self):
        c = compute_confidence(
            quality_score=80, technical_score=70, edge_score=60,
            mode="HIZLI",
        )
        assert c["phase"] == BULLALFA_PARAMS.get("phase", "v1_heuristic")

    def test_does_not_mutate_params(self):
        before = dict(BULLALFA_PARAMS["calibration"]["combo_weights"]["SWING"])
        _ = compute_confidence(
            quality_score=80, technical_score=70, edge_score=60,
            mode="SWING",
        )
        after = BULLALFA_PARAMS["calibration"]["combo_weights"]["SWING"]
        assert dict(after) == before


# ================================================================
# Phase label — v2 isotonic hook
# ================================================================

class TestCalibrationPhase:

    def test_default_is_v1_heuristic(self):
        # BULLALFA_PARAMS["phase"] is "v1_heuristic" at import.
        assert calibration_phase() == "v1_heuristic"

    def test_explicit_v2_label(self):
        assert calibration_phase(isotonic_fits_loaded=True) == "v2_isotonic"

    def test_explicit_v1_label_overrides_param(self):
        assert calibration_phase(isotonic_fits_loaded=False) == "v1_heuristic"

    def test_handles_missing_phase_key_gracefully(self, monkeypatch):
        # If params is incomplete (shouldn't happen but be defensive),
        # default to v1_heuristic.
        monkeypatch.delitem(BULLALFA_PARAMS, "phase", raising=False)
        try:
            assert calibration_phase() == "v1_heuristic"
        finally:
            BULLALFA_PARAMS["phase"] = "v1_heuristic"

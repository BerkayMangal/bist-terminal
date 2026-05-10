# ================================================================
# tests/test_bullalfa_risk_frame.py
#
# Spec §22 coverage:
#   - All 7 invariants
#   - ATR stop multipliers per mode (1.2 / 1.8 / 2.5)
#   - Invalid risk_frame downgrades mode to TOPLANIYOR (NEW)
#   - WATCH/SAKİN/UZAK DUR return null risk_frame
# ================================================================

from __future__ import annotations

import pytest

from engine.bullalfa_params import (
    ENTRY_ZONE_HIGH_MULT,
    ENTRY_ZONE_LOW_MULT,
    RISK_FRAME_R_TOLERANCE_PCT,
    max_hold_bars,
    stop_atr_mult,
)
from features.bullalfa_risk import (
    DOWNGRADE_CAVEAT_TR,
    DOWNGRADE_REASON_INVALID,
    build_risk_frame,
    try_build_risk_frame,
    validate_risk_frame,
)


# ================================================================
# build_risk_frame — basic shape per mode
# ================================================================

class TestBuildRiskFrame:

    @pytest.mark.parametrize("mode", ["HIZLI", "SWING", "POZİSYON"])
    def test_actionable_modes_produce_dict(self, mode):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode=mode)
        assert isinstance(rf, dict)
        # Required keys present
        for k in ("entry_zone", "stop", "stop_pct", "target_1r",
                  "target_2r", "target_3r", "invalidation",
                  "max_hold_bars", "trail_rule"):
            assert k in rf

    @pytest.mark.parametrize("mode", ["TOPLANIYOR", "SAKİN", "UZAK DUR"])
    def test_non_actionable_modes_return_none(self, mode):
        # Spec §10: TOPLANIYOR / SAKİN / UZAK DUR carry no risk frame.
        assert build_risk_frame(price=100.0, atr14=2.0, mode=mode) is None

    @pytest.mark.parametrize("price", [None, 0, -5])
    def test_invalid_price_returns_none(self, price):
        assert build_risk_frame(price=price, atr14=2.0, mode="SWING") is None

    @pytest.mark.parametrize("atr", [None, 0, -1])
    def test_invalid_atr_returns_none(self, atr):
        assert build_risk_frame(price=100.0, atr14=atr, mode="SWING") is None

    def test_unknown_mode_returns_none(self):
        assert build_risk_frame(price=100.0, atr14=2.0, mode="GIBBERISH") is None


# ================================================================
# Per-mode ATR multiplier verification (spec §10 — 1.2 / 1.8 / 2.5)
# ================================================================

class TestATRMultipliersPerMode:

    @pytest.mark.parametrize(
        "mode,expected_mult",
        [("HIZLI", 1.2), ("SWING", 1.8), ("POZİSYON", 2.5)],
    )
    def test_stop_distance_matches_atr_multiplier(self, mode, expected_mult):
        # stop = price - atr × mult; round to 2dp matches the impl.
        price, atr = 100.0, 2.0
        rf = build_risk_frame(price=price, atr14=atr, mode=mode)
        expected_stop = round(price - atr * expected_mult, 2)
        assert rf["stop"] == pytest.approx(expected_stop, abs=1e-9)

    def test_params_match_spec_values(self):
        # Defensive — guard against accidental tuning of these critical
        # constants without an explicit decision. The spec hard-codes
        # 1.2 / 1.8 / 2.5 in §10.
        assert stop_atr_mult("HIZLI")    == 1.2
        assert stop_atr_mult("SWING")    == 1.8
        assert stop_atr_mult("POZİSYON") == 2.5

    @pytest.mark.parametrize(
        "mode,expected_hold",
        [("HIZLI", 5), ("SWING", 20), ("POZİSYON", 126)],
    )
    def test_max_hold_bars_per_mode(self, mode, expected_hold):
        # Spec snippet: 5 / 20 / 126 (1 wk / 4 wk / ~6 mo)
        assert max_hold_bars(mode) == expected_hold


# ================================================================
# Invariants — each one must catch its named failure
# ================================================================

class TestInvariants:

    def test_valid_frame_passes_all_invariants(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        ok, fails = validate_risk_frame(rf)
        assert ok is True
        assert fails == []

    def test_none_frame_returns_missing_frame(self):
        ok, fails = validate_risk_frame(None)
        assert ok is False
        assert fails == ["missing_frame"]

    def test_inv1_entry_band(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        # Force entry_low >= entry_high
        rf["entry_zone"] = (101.0, 100.0)
        ok, fails = validate_risk_frame(rf)
        assert ok is False
        assert "inv1_entry_band" in fails

    def test_inv2_stop_below_entry(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        rf["stop"] = rf["entry_zone"][0] + 0.5  # stop now ABOVE entry_low
        ok, fails = validate_risk_frame(rf)
        assert ok is False
        assert "inv2_stop_below_entry" in fails

    def test_inv3_stop_pct_negative(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        rf["stop_pct"] = 1.0  # nonsensical positive
        ok, fails = validate_risk_frame(rf)
        assert ok is False
        assert "inv3_stop_pct_negative" in fails

    def test_inv4_target_above_entry(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        rf["target_1r"] = rf["entry_zone"][1] - 0.5
        ok, fails = validate_risk_frame(rf)
        assert ok is False
        assert "inv4_target_above_entry" in fails

    def test_inv5_target_2r_arithmetic(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        # Knock target_2r off the implied entry+2R by more than tolerance.
        rf["target_2r"] = rf["target_2r"] + 5.0
        ok, fails = validate_risk_frame(rf)
        assert ok is False
        assert "inv5_target_2r_arithmetic" in fails

    def test_inv5_target_2r_within_tolerance(self):
        # Nudge target_2r within RISK_FRAME_R_TOLERANCE_PCT — must still pass.
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        # +0.1% perturbation is well under the 1% tolerance.
        rf["target_2r"] = rf["target_2r"] * (1.0 + 0.5 * RISK_FRAME_R_TOLERANCE_PCT)
        ok, _ = validate_risk_frame(rf)
        assert ok is True

    def test_inv6_target_monotonicity(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        # Swap target_2r and target_3r to break monotonicity.
        rf["target_2r"], rf["target_3r"] = rf["target_3r"], rf["target_2r"]
        ok, fails = validate_risk_frame(rf)
        assert ok is False
        assert "inv6_target_monotonicity" in fails

    def test_inv7_max_hold_positive(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        rf["max_hold_bars"] = 0
        ok, fails = validate_risk_frame(rf)
        assert ok is False
        assert "inv7_max_hold_positive" in fails

    def test_missing_required_key_fails_structurally(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        del rf["target_2r"]
        ok, fails = validate_risk_frame(rf)
        assert ok is False
        assert any(f.startswith("missing_key:") for f in fails)


# ================================================================
# try_build_risk_frame — orchestrator entry point
# ================================================================

class TestTryBuildRiskFrame:

    def test_actionable_valid_returns_frame_no_caveat(self):
        frame, reason, caveats = try_build_risk_frame(
            price=100.0, atr14=2.0, mode="SWING",
        )
        assert frame is not None
        assert reason is None
        assert caveats == []

    def test_invalid_inputs_signals_downgrade_with_caveat(self):
        frame, reason, caveats = try_build_risk_frame(
            price=100.0, atr14=None, mode="HIZLI",
        )
        assert frame is None
        assert reason == DOWNGRADE_REASON_INVALID
        assert caveats and caveats[0] == DOWNGRADE_CAVEAT_TR

    def test_invariant_failure_signals_downgrade(self, monkeypatch):
        # Force the underlying builder to produce an invalid frame.
        from features import bullalfa_risk as risk_mod

        def bad_build(*, price, atr14, mode):
            return {
                "entry_zone":    (101.0, 100.0),  # inv1 fail
                "stop":          90.0,
                "stop_pct":      -10.0,
                "target_1r":     105.0,
                "target_2r":     110.0,
                "target_3r":     115.0,
                "invalidation":  "test",
                "max_hold_bars": 5,
                "trail_rule":    "test",
            }

        monkeypatch.setattr(risk_mod, "build_risk_frame", bad_build)
        frame, reason, caveats = risk_mod.try_build_risk_frame(
            price=100.0, atr14=2.0, mode="SWING",
        )
        assert frame is None
        assert reason == DOWNGRADE_REASON_INVALID
        assert DOWNGRADE_CAVEAT_TR in caveats
        assert "inv1_entry_band" in caveats

    @pytest.mark.parametrize("mode", ["TOPLANIYOR", "SAKİN", "UZAK DUR"])
    def test_non_actionable_returns_clean_none(self, mode):
        # No frame, no downgrade reason, no caveats — risk frame is
        # legitimately absent for these modes.
        frame, reason, caveats = try_build_risk_frame(
            price=100.0, atr14=2.0, mode=mode,
        )
        assert frame is None
        assert reason is None
        assert caveats == []

    def test_unknown_mode_treated_as_non_actionable(self):
        frame, reason, caveats = try_build_risk_frame(
            price=100.0, atr14=2.0, mode="GIBBERISH",
        )
        assert frame is None
        assert reason is None
        assert caveats == []


# ================================================================
# Stop direction / target arithmetic — sanity end-to-end
# ================================================================

class TestStopAndTargetArithmetic:

    def test_target_1r_equals_one_R_above_implied_entry(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="SWING")
        # implied entry == price (used by spec); stop = 100 - 2*1.8 = 96.4
        # target_1r = entry + (entry - stop) = 100 + 3.6 = 103.6
        assert rf["stop"] == pytest.approx(96.4, abs=1e-9)
        assert rf["target_1r"] == pytest.approx(103.6, abs=1e-9)
        assert rf["target_2r"] == pytest.approx(107.2, abs=1e-9)
        assert rf["target_3r"] == pytest.approx(110.8, abs=1e-9)

    def test_entry_zone_band_uses_correct_multipliers(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="HIZLI")
        low, high = rf["entry_zone"]
        assert low  == pytest.approx(round(100.0 * ENTRY_ZONE_LOW_MULT, 2))
        assert high == pytest.approx(round(100.0 * ENTRY_ZONE_HIGH_MULT, 2))

    def test_stop_pct_is_negative(self):
        rf = build_risk_frame(price=100.0, atr14=2.0, mode="POZİSYON")
        assert rf["stop_pct"] < 0

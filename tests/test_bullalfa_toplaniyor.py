# ================================================================
# tests/test_bullalfa_toplaniyor.py
#
# Spec §22 coverage:
#   - Required-set conditions: trend, BB compression, soft volume rise
#   - At-least-one corroborating: ADX rising / higher lows / up-down vol / no upgrade
#   - Quality D + setup conditions → still SAKİN (D excluded)
#   - Quality D + no setup → SAKİN
#   - Strong setup but already HIZLI-eligible → upgrades to HIZLI, not TOPLANIYOR
#   - accumulation_strength scoring (0-100)
# ================================================================

from __future__ import annotations

from dataclasses import replace

import pytest

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
from features.bullalfa_toplaniyor import (
    EXCLUDED_QUALITY_GRADES,
    ToplaniyorAssessment,
    compute_accumulation_strength,
    evaluate_toplaniyor,
)


# ================================================================
# Fixture helper — engineering inputs that pass ALL required predicates
# and ALL corroborating predicates by default. Tests then override the
# individual fields they want to break.
# ================================================================

def _passing_inputs(**overrides) -> EngineInputs:
    base = dict(
        price=100.0, ema20=99.0, ema50=98.0, ema200=95.0,
        prior_close=99.5, prior_low=98.0,
        return_5d=0.01, return_20d=0.03, return_60d=0.05,
        rvol_today=1.2, rvol_5d_avg=1.20, rvol_3d_ago=1.0,
        up_down_vol_ratio_10d=1.5,             # > 1.4 → corroborating ON
        atr14=2.0, atr_avg_20d=2.0,
        bb_width_today=0.020,                  # < 0.030 (p35) → required ON
        bb_width_60d_p25=0.025,
        bb_width_60d_p35=0.030,
        bb_width_60d_median=0.040,
        range_today=2.0,
        last5_pullback_ok=True,
        high_20d=100.0, high_55d=100.0, high_6m=100.0,
        e4_bars_since_20d=5, e4_bars_since_55d=5, e4_bars_since_6m=5,
        rsi=55.0,
        adx_today=22.0, adx_10d_ago=18.0,      # +4 → corroborating ON
        plus_di=20.0, minus_di=15.0,
        higher_lows_count_10d=4,               # ≥ 3 → corroborating ON
        bench_return_20d=0.005, bench_return_60d=0.01,
        benchmark="XU100", sector_group="sanayi",
        short_history=False, bars_available=250,
    )
    base.update(overrides)
    return EngineInputs(**base)


# ================================================================
# Required-set predicates — each one independently blocks
# ================================================================

class TestRequiredConditions:

    def test_strong_setup_qualifies(self):
        r = evaluate_toplaniyor(
            inp=_passing_inputs(),
            quality_grade="B",
            actionable_mode_already_fired=False,
        )
        assert r.eligible is True
        assert r.required_failures == ()
        assert r.blocker is None

    def test_trend_broken_blocks(self):
        # price < ema50 AND ema20 < ema50 → trend not intact
        inp = _passing_inputs(price=90.0, ema20=92.0, ema50=98.0)
        r = evaluate_toplaniyor(inp=inp, quality_grade="B")
        assert r.eligible is False
        assert "trend_broken" in r.required_failures
        assert r.blocker == "trend_broken"

    def test_trend_intact_via_price_above_ema50(self):
        # ema20 < ema50 but price > ema50 → trend still intact
        inp = _passing_inputs(price=100.0, ema20=97.0, ema50=98.0)
        r = evaluate_toplaniyor(inp=inp, quality_grade="B")
        assert r.eligible is True

    def test_trend_intact_via_ema20_above_ema50(self):
        # price < ema50 but ema20 > ema50 → trend still intact
        inp = _passing_inputs(price=97.0, ema20=99.0, ema50=98.0)
        r = evaluate_toplaniyor(inp=inp, quality_grade="B")
        assert r.eligible is True

    def test_bb_not_compressed_blocks(self):
        inp = _passing_inputs(bb_width_today=0.05, bb_width_60d_p35=0.03)
        r = evaluate_toplaniyor(inp=inp, quality_grade="B")
        assert r.eligible is False
        assert "bb_not_compressed" in r.required_failures

    def test_rvol_below_band_blocks(self):
        # rvol_5d_avg below TOPLANIYOR_RVOL_5D_LOW (1.05)
        inp = _passing_inputs(rvol_5d_avg=1.00)
        r = evaluate_toplaniyor(inp=inp, quality_grade="B")
        assert r.eligible is False
        assert "rvol_5d_outside_band" in r.required_failures

    def test_rvol_above_band_blocks(self):
        # 5d avg ≥ 1.50 implies a breakout-grade volume environment;
        # spec excludes TOPLANIYOR in that case.
        inp = _passing_inputs(rvol_5d_avg=1.60)
        r = evaluate_toplaniyor(inp=inp, quality_grade="B")
        assert r.eligible is False
        assert "rvol_5d_outside_band" in r.required_failures

    def test_rvol_at_lower_boundary_excluded(self):
        # Open-interval semantics — exact boundary is not "inside".
        inp = _passing_inputs(rvol_5d_avg=TOPLANIYOR_RVOL_5D_LOW)
        r = evaluate_toplaniyor(inp=inp, quality_grade="B")
        assert "rvol_5d_outside_band" in r.required_failures

    def test_rvol_at_upper_boundary_excluded(self):
        inp = _passing_inputs(rvol_5d_avg=TOPLANIYOR_RVOL_5D_HIGH)
        r = evaluate_toplaniyor(inp=inp, quality_grade="B")
        assert "rvol_5d_outside_band" in r.required_failures

    def test_d_grade_blocks_even_with_perfect_setup(self):
        r = evaluate_toplaniyor(
            inp=_passing_inputs(),
            quality_grade="D",
        )
        assert r.eligible is False
        assert "quality_excluded" in r.required_failures
        assert r.blocker == "quality_excluded"

    def test_d_grade_with_no_setup_also_blocked(self):
        # Spec §12: D-grade names without setup → SAKİN.
        # Here we break BB compression too — both required failures
        # surface; the orchestrator routes to SAKİN.
        inp = _passing_inputs(bb_width_today=0.05)
        r = evaluate_toplaniyor(inp=inp, quality_grade="D")
        assert r.eligible is False
        assert "quality_excluded" in r.required_failures
        assert "bb_not_compressed" in r.required_failures

    @pytest.mark.parametrize("grade", ["A+", "A", "B", "C"])
    def test_non_d_grades_pass_quality_gate(self, grade):
        r = evaluate_toplaniyor(inp=_passing_inputs(), quality_grade=grade)
        assert "quality_excluded" not in r.required_failures
        assert r.eligible is True

    def test_excluded_grades_constant_is_just_d(self):
        # Defensive — guard against accidental tightening.
        assert EXCLUDED_QUALITY_GRADES == frozenset({"D"})


# ================================================================
# Corroborating predicates — at least one must fire
# ================================================================

class TestCorroboratingConditions:

    def test_no_named_corroborator_with_actionable_priority_fails(self):
        # Required all met, every NAMED corroborating predicate broken,
        # AND an actionable mode has fired → blocked. Two blocker codes
        # are simultaneously valid here (no_corroborating_signal AND
        # actionable_mode_priority); the assessment surfaces whichever
        # the implementation finds first. The orchestrator only cares
        # about `eligible`; the blocker code is a diagnostic detail.
        inp = _passing_inputs(
            adx_today=18.0, adx_10d_ago=22.0,    # adx FALLING
            higher_lows_count_10d=0,              # no higher lows
            up_down_vol_ratio_10d=1.0,            # parity, not dominant
        )
        r = evaluate_toplaniyor(
            inp=inp,
            quality_grade="B",
            actionable_mode_already_fired=True,
        )
        assert r.eligible is False
        assert r.blocker in {"no_corroborating_signal", "actionable_mode_priority"}

    def test_no_corroborating_signal_when_all_off_and_no_actionable(self):
        # Required all met, all NAMED corroborators broken, no actionable
        # mode firing → `no_upgrade` fires by definition, so there IS
        # one corroborating signal and TOPLANIYOR is eligible.
        # This confirms `no_upgrade` is a legitimate corroborator on
        # its own (spec §12 fourth bullet).
        inp = _passing_inputs(
            adx_today=18.0, adx_10d_ago=22.0,
            higher_lows_count_10d=0,
            up_down_vol_ratio_10d=1.0,
        )
        r = evaluate_toplaniyor(
            inp=inp,
            quality_grade="B",
            actionable_mode_already_fired=False,
        )
        assert r.eligible is True
        assert r.corroborating_passes == ("no_upgrade",)

    def test_adx_rising_appears_when_others_broken(self):
        # Verify ADX rising contributes to the corroborating set even
        # when higher_lows and ud_vol are broken. With no actionable
        # mode firing, `no_upgrade` also fires — both should be present.
        inp = _passing_inputs(
            adx_today=22.0, adx_10d_ago=18.0,
            higher_lows_count_10d=0,
            up_down_vol_ratio_10d=1.0,
        )
        r = evaluate_toplaniyor(
            inp=inp, quality_grade="B",
            actionable_mode_already_fired=False,
        )
        assert r.eligible is True
        assert "adx_rising" in r.corroborating_passes
        assert "higher_lows" not in r.corroborating_passes
        assert "up_down_vol_dominant" not in r.corroborating_passes

    def test_higher_lows_appears_when_others_broken(self):
        inp = _passing_inputs(
            adx_today=18.0, adx_10d_ago=22.0,
            higher_lows_count_10d=TOPLANIYOR_HIGHER_LOWS_MIN,
            up_down_vol_ratio_10d=1.0,
        )
        r = evaluate_toplaniyor(
            inp=inp, quality_grade="B",
            actionable_mode_already_fired=False,
        )
        assert r.eligible is True
        assert "higher_lows" in r.corroborating_passes
        assert "adx_rising" not in r.corroborating_passes
        assert "up_down_vol_dominant" not in r.corroborating_passes

    def test_up_down_vol_appears_when_others_broken(self):
        inp = _passing_inputs(
            adx_today=18.0, adx_10d_ago=22.0,
            higher_lows_count_10d=0,
            up_down_vol_ratio_10d=TOPLANIYOR_UD_VOL_RATIO_MIN + 0.01,
        )
        r = evaluate_toplaniyor(
            inp=inp, quality_grade="B",
            actionable_mode_already_fired=False,
        )
        assert r.eligible is True
        assert "up_down_vol_dominant" in r.corroborating_passes
        assert "adx_rising" not in r.corroborating_passes
        assert "higher_lows" not in r.corroborating_passes

    def test_no_upgrade_alone_corroborates(self):
        # When all named corroborators are broken but no actionable
        # mode fires, the `no_upgrade` predicate alone qualifies.
        inp = _passing_inputs(
            adx_today=18.0, adx_10d_ago=22.0,
            higher_lows_count_10d=0,
            up_down_vol_ratio_10d=1.0,
        )
        r = evaluate_toplaniyor(
            inp=inp, quality_grade="B",
            actionable_mode_already_fired=False,
        )
        assert r.eligible is True
        assert "no_upgrade" in r.corroborating_passes


# ================================================================
# Priority — actionable mode ALREADY firing means TOPLANIYOR is NOT chosen
# ================================================================

class TestUpgradePriority:

    def test_actionable_mode_priority_over_toplaniyor(self):
        # Strong setup, all corroborating signals firing, but the
        # orchestrator already determined HIZLI/SWING/POZİSYON triggers.
        # Per spec §12: "Strong setup but already HIZLI-eligible →
        # upgrades to HIZLI, not TOPLANIYOR."
        # In our assessment surface the corroborating set drops the
        # `no_upgrade` token, but the strong cases above (adx/higher
        # lows/UD-vol) keep TOPLANIYOR eligibility True. The
        # `actionable_mode_already_fired` flag overrides this and
        # blocks eligibility regardless.
        r = evaluate_toplaniyor(
            inp=_passing_inputs(),
            quality_grade="B",
            actionable_mode_already_fired=True,
        )
        assert r.eligible is False
        assert r.blocker == "actionable_mode_priority"
        # Corroborating passes still computed for diagnostics.
        assert "no_upgrade" not in r.corroborating_passes
        assert any(
            t in r.corroborating_passes
            for t in ("adx_rising", "higher_lows", "up_down_vol_dominant")
        )


# ================================================================
# accumulation_strength — bounds + monotonicity + composition
# ================================================================

class TestAccumulationStrength:

    def test_score_bounded_zero_to_hundred(self):
        v = compute_accumulation_strength(_passing_inputs())
        assert 0 <= v <= 100

    def test_zero_when_all_inputs_missing(self):
        inp = EngineInputs(
            price=100.0, ema20=None, ema50=None, ema200=None,
            prior_close=None, prior_low=None,
            return_5d=None, return_20d=None, return_60d=None,
            rvol_today=None, rvol_5d_avg=None, rvol_3d_ago=None,
            up_down_vol_ratio_10d=None,
            atr14=None, atr_avg_20d=None,
            bb_width_today=None,
            bb_width_60d_p25=None, bb_width_60d_p35=None, bb_width_60d_median=None,
            range_today=None, last5_pullback_ok=None,
            high_20d=None, high_55d=None, high_6m=None,
            e4_bars_since_20d=999, e4_bars_since_55d=999, e4_bars_since_6m=999,
            rsi=None, adx_today=None, adx_10d_ago=None,
            plus_di=None, minus_di=None,
            higher_lows_count_10d=0,
            bench_return_20d=None, bench_return_60d=None,
            benchmark="XU100", sector_group="sanayi",
            short_history=True, bars_available=0,
        )
        assert compute_accumulation_strength(inp) == 0

    def test_score_responds_to_adx_rise(self):
        weak = _passing_inputs(adx_today=18.0, adx_10d_ago=18.0)
        strong = _passing_inputs(
            adx_today=18.0 + ACC_STRENGTH_ADX_FLOOR + 5,
            adx_10d_ago=18.0,
        )
        assert compute_accumulation_strength(strong) > compute_accumulation_strength(weak)

    def test_score_responds_to_compression_depth(self):
        weak = _passing_inputs(bb_width_today=0.029, bb_width_60d_p35=0.030)
        strong = _passing_inputs(bb_width_today=0.005, bb_width_60d_p35=0.030)
        assert compute_accumulation_strength(strong) > compute_accumulation_strength(weak)

    def test_score_responds_to_buying_pressure(self):
        weak = _passing_inputs(up_down_vol_ratio_10d=1.0)
        strong = _passing_inputs(
            up_down_vol_ratio_10d=1.0 + ACC_STRENGTH_BUYING_PRESSURE_NORMALISER,
        )
        assert compute_accumulation_strength(strong) > compute_accumulation_strength(weak)

    def test_score_responds_to_higher_lows(self):
        weak = _passing_inputs(higher_lows_count_10d=0)
        strong = _passing_inputs(
            higher_lows_count_10d=TOPLANIYOR_HIGHER_LOWS_MIN,
        )
        assert compute_accumulation_strength(strong) > compute_accumulation_strength(weak)

    def test_full_stack_yields_full_100_when_all_components_saturate(self):
        # Saturate all four 0–1 components.
        inp = _passing_inputs(
            adx_today=100.0, adx_10d_ago=0.0,                     # huge rise
            bb_width_today=0.0, bb_width_60d_p35=0.030,           # max tightness
            up_down_vol_ratio_10d=999.0,                          # saturates pressure
            higher_lows_count_10d=999,                            # saturates structure
        )
        assert compute_accumulation_strength(inp) == 100

    def test_returns_int(self):
        # §17 ranking sorts on this value; integer return is required for
        # stable tie-breaking under repeated recomputes.
        v = compute_accumulation_strength(_passing_inputs())
        assert isinstance(v, int)


# ================================================================
# Assessment shape — frozen dataclass invariants
# ================================================================

class TestAssessmentShape:

    def test_dataclass_is_frozen(self):
        a = evaluate_toplaniyor(
            inp=_passing_inputs(), quality_grade="B",
        )
        with pytest.raises(Exception):
            # noinspection PyDataclass
            a.eligible = False  # type: ignore[misc]

    def test_required_failures_is_tuple(self):
        a = evaluate_toplaniyor(
            inp=_passing_inputs(), quality_grade="D",
        )
        assert isinstance(a.required_failures, tuple)

    def test_corroborating_passes_is_tuple(self):
        a = evaluate_toplaniyor(
            inp=_passing_inputs(), quality_grade="B",
        )
        assert isinstance(a.corroborating_passes, tuple)

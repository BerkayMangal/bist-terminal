# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# tests/test_bullalfa_engines.py
#
# Engine tests (spec §22).
#
# Coverage targets:
#   - E1–E7 unit tests with synthetic OHLC fixtures
#   - E5 skipped for banka / holding / gyo
#   - Tie-breaker (E4 wins over E6 same bar)
#   - Pullback to Breakout detection (window + cap)
#
# All tests are pure unit — no network, no Redis, no fixtures from
# the broader BISTBull conftest. Synthetic OHLC frames built on the
# fly with a fixed seed for determinism.
# ================================================================

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from features.bullalfa_features import (
    EngineInputs,
    build_engine_inputs,
    compute_engines,
    detect_pullback_to_breakout,
    engine_1_trend,
    engine_2_relstr,
    engine_3_volume,
    engine_4_breakout,
    engine_5_compression,
    engine_6_pullback,
    engine_7_exhaustion,
)
from engine.bullalfa_params import (
    E5_ATR_TIGHTNESS_RATIO,
    E5_BB_WIDTH_PCTILE_COMPRESS,
    E7_PENALTY_CAP,
    PULLBACK_TO_BREAKOUT_LOOKBACK_BARS,
    rvol_threshold,
)


# ----------------------------------------------------------------
# Helpers — minimal EngineInputs builder for unit tests
# ----------------------------------------------------------------

def _mk_inputs(**overrides) -> EngineInputs:
    """Construct an EngineInputs with sane defaults + per-test overrides."""
    defaults = dict(
        price=100.0, ema20=98.0, ema50=95.0, ema200=90.0,
        prior_close=99.0, prior_low=97.0,
        return_5d=0.05, return_20d=0.10, return_60d=0.20,
        rvol_today=2.0, rvol_5d_avg=1.3, rvol_3d_ago=1.5,
        up_down_vol_ratio_10d=1.5,
        atr14=2.0, atr_avg_20d=2.5,
        bb_width_today=0.04, bb_width_60d_p25=0.05,
        bb_width_60d_p35=0.055, bb_width_60d_median=0.07,
        range_today=3.0, last5_pullback_ok=True,
        high_20d=99.0, high_55d=98.0, high_6m=95.0,
        e4_bars_since_20d=0, e4_bars_since_55d=0, e4_bars_since_6m=10,
        rsi=65.0, adx_today=25.0, adx_10d_ago=18.0,
        plus_di=22.0, minus_di=15.0,
        higher_lows_count_10d=4,
        bench_return_20d=0.02, bench_return_60d=0.05, benchmark="XU100",
        sector_group="sanayi", short_history=False, bars_available=200,
    )
    defaults.update(overrides)
    return EngineInputs(**defaults)


# ================================================================
# Engine 1 — Trend Alignment
# ================================================================

class TestEngine1Trend:

    def test_hizli_pass_when_ema20_gt_ema50_and_price_gt_ema20(self):
        inp = _mk_inputs(price=100, ema20=98, ema50=95)
        assert engine_1_trend(inp, "HIZLI") == 1

    def test_hizli_fail_when_price_below_ema20(self):
        inp = _mk_inputs(price=97, ema20=98, ema50=95)
        assert engine_1_trend(inp, "HIZLI") == 0

    def test_hizli_fail_when_ema20_below_ema50(self):
        inp = _mk_inputs(price=100, ema20=94, ema50=95)
        assert engine_1_trend(inp, "HIZLI") == 0

    def test_swing_requires_full_stack(self):
        inp = _mk_inputs(ema20=98, ema50=95, ema200=90)
        assert engine_1_trend(inp, "SWING") == 1

    def test_swing_fails_when_ema50_below_ema200(self):
        inp = _mk_inputs(ema20=98, ema50=85, ema200=90)
        assert engine_1_trend(inp, "SWING") == 0

    def test_pozisyon_only_needs_price_above_ema200(self):
        inp = _mk_inputs(price=92, ema200=90, ema20=85, ema50=88)
        # POZİSYON ignores the short-stack requirement
        assert engine_1_trend(inp, "POZİSYON") == 1

    def test_pozisyon_fails_when_price_below_ema200(self):
        inp = _mk_inputs(price=85, ema200=90)
        assert engine_1_trend(inp, "POZİSYON") == 0

    def test_missing_ema_returns_zero(self):
        inp = _mk_inputs(ema20=None)
        assert engine_1_trend(inp, "HIZLI") == 0

    def test_unknown_mode_returns_zero(self):
        inp = _mk_inputs()
        assert engine_1_trend(inp, "TOPLANIYOR") == 0
        assert engine_1_trend(inp, "GARBAGE") == 0


# ================================================================
# Engine 2 — Relative Strength
# ================================================================

class TestEngine2RelStr:

    def test_full_score_when_both_short_and_long_positive(self):
        inp = _mk_inputs(return_20d=0.10, return_60d=0.20,
                         bench_return_20d=0.02, bench_return_60d=0.05)
        out = engine_2_relstr(inp)
        assert out["score"] == 1.0
        assert out["rs_short"] > 0 and out["rs_long"] > 0

    def test_partial_score_when_only_one_positive(self):
        inp = _mk_inputs(return_20d=0.10, return_60d=-0.10,
                         bench_return_20d=0.02, bench_return_60d=-0.05)
        out = engine_2_relstr(inp)
        assert out["score"] == 0.5

    def test_zero_when_both_negative(self):
        inp = _mk_inputs(return_20d=-0.10, return_60d=-0.20,
                         bench_return_20d=0.02, bench_return_60d=0.05)
        out = engine_2_relstr(inp)
        assert out["score"] == 0.0

    def test_zero_when_no_benchmark_data(self):
        inp = _mk_inputs(bench_return_20d=None, bench_return_60d=None)
        out = engine_2_relstr(inp)
        assert out["score"] == 0.0
        assert out["rs_short"] is None and out["rs_long"] is None

    def test_benchmark_label_propagates(self):
        inp = _mk_inputs(benchmark="XBANK")
        assert engine_2_relstr(inp)["benchmark"] == "XBANK"


# ================================================================
# Engine 3 — Volume Confirmation
# ================================================================

class TestEngine3Volume:

    def test_hizli_threshold_is_1p8(self):
        # Confirms BULLALFA_PARAMS source-of-truth wiring.
        assert rvol_threshold("HIZLI") == 1.8
        assert rvol_threshold("SWING") == 1.3
        assert rvol_threshold("POZİSYON") == 1.0

    @pytest.mark.parametrize("mode,rvol,expected", [
        ("HIZLI",    2.0, True),
        ("HIZLI",    1.5, False),
        ("HIZLI",    1.8, False),   # strictly greater than
        ("SWING",    1.4, True),
        ("SWING",    1.3, False),
        ("POZİSYON", 1.1, True),
        ("POZİSYON", 1.0, False),
    ])
    def test_threshold_per_mode(self, mode, rvol, expected):
        inp = _mk_inputs(rvol_today=rvol)
        assert engine_3_volume(inp, mode)["passed"] is expected

    def test_missing_rvol_fails(self):
        inp = _mk_inputs(rvol_today=None)
        out = engine_3_volume(inp, "HIZLI")
        assert out["passed"] is False
        assert out["rvol"] is None

    def test_unknown_mode_fails(self):
        inp = _mk_inputs(rvol_today=10.0)
        assert engine_3_volume(inp, "TOPLANIYOR")["passed"] is False


# ================================================================
# Engine 4 — Breakout
# ================================================================

class TestEngine4Breakout:

    def test_hizli_uses_20d_window(self):
        inp = _mk_inputs(e4_bars_since_20d=0, e4_bars_since_55d=10, e4_bars_since_6m=20)
        assert engine_4_breakout(inp, "HIZLI") == {"type": "20d", "bars_ago": 0}

    def test_swing_uses_55d_window(self):
        inp = _mk_inputs(e4_bars_since_20d=0, e4_bars_since_55d=2, e4_bars_since_6m=20)
        assert engine_4_breakout(inp, "SWING") == {"type": "55d", "bars_ago": 2}

    def test_pozisyon_uses_6m_window(self):
        inp = _mk_inputs(e4_bars_since_20d=0, e4_bars_since_55d=2, e4_bars_since_6m=5)
        assert engine_4_breakout(inp, "POZİSYON") == {"type": "6m", "bars_ago": 5}

    def test_no_breakout_returns_none(self):
        inp = _mk_inputs(e4_bars_since_20d=None, e4_bars_since_55d=None, e4_bars_since_6m=None)
        assert engine_4_breakout(inp, "HIZLI") == {"type": None, "bars_ago": None}

    def test_unknown_mode_returns_none(self):
        inp = _mk_inputs()
        assert engine_4_breakout(inp, "TOPLANIYOR") == {"type": None, "bars_ago": None}


# ================================================================
# Engine 5 — Compression → Expansion (with sector skip)
# ================================================================

class TestEngine5Compression:

    def test_compressed_when_bb_below_p25_and_atr_below_ratio(self):
        inp = _mk_inputs(
            bb_width_today=0.04,
            bb_width_60d_p25=0.05,
            atr14=2.0,
            atr_avg_20d=2.5,  # 2.0 < 2.5*0.85 = 2.125 → True
            sector_group="sanayi",
        )
        out = engine_5_compression(inp)
        assert out["compressed"] is True

    def test_not_compressed_when_atr_above_ratio(self):
        inp = _mk_inputs(
            bb_width_today=0.04,
            bb_width_60d_p25=0.05,
            atr14=2.5,
            atr_avg_20d=2.5,  # ratio 1.0, fails 0.85 floor
        )
        assert engine_5_compression(inp)["compressed"] is False

    def test_not_compressed_when_bb_above_p25(self):
        inp = _mk_inputs(
            bb_width_today=0.06,
            bb_width_60d_p25=0.05,
        )
        assert engine_5_compression(inp)["compressed"] is False

    def test_expansion_detected(self):
        inp = _mk_inputs(range_today=4.0, atr_avg_20d=2.5)  # 4.0 > 2.5*1.5 = 3.75
        assert engine_5_compression(inp)["expanded"] is True

    @pytest.mark.parametrize("sector", ["banka", "holding", "gyo", "newly_listed", "halted"])
    def test_skipped_for_excluded_sectors(self, sector):
        inp = _mk_inputs(sector_group=sector)
        out = engine_5_compression(inp)
        assert out["compressed"] is False
        assert out["expanded"] is False
        assert "skipped_reason" in out

    def test_insufficient_data_does_not_crash(self):
        inp = _mk_inputs(bb_width_today=None, atr14=None)
        out = engine_5_compression(inp)
        assert out["compressed"] is False
        assert "skipped_reason" in out


# ================================================================
# Engine 6 — Pullback Quality
# ================================================================

class TestEngine6Pullback:

    def test_passes_when_all_four_conditions_met(self):
        inp = _mk_inputs(
            price=99.0, ema20=98.0,                      # 1% above EMA20
            last5_pullback_ok=True,
        )
        # Trend intact via E1 SWING stack
        assert engine_6_pullback(inp, "SWING") is True

    def test_fails_when_trend_broken(self):
        inp = _mk_inputs(ema20=85.0, ema50=95.0)  # E1 SWING fails
        assert engine_6_pullback(inp, "SWING") is False

    def test_fails_when_price_too_far_above_ema20(self):
        inp = _mk_inputs(price=120.0, ema20=98.0, last5_pullback_ok=True)
        # >2% above EMA20
        assert engine_6_pullback(inp, "SWING") is False

    def test_fails_when_price_below_ema20(self):
        inp = _mk_inputs(price=95.0, ema20=98.0, last5_pullback_ok=True)
        assert engine_6_pullback(inp, "SWING") is False

    def test_fails_when_last5_check_unmet(self):
        inp = _mk_inputs(price=99.0, ema20=98.0, last5_pullback_ok=False)
        assert engine_6_pullback(inp, "SWING") is False

    def test_handles_missing_last5_check(self):
        inp = _mk_inputs(price=99.0, ema20=98.0, last5_pullback_ok=None)
        assert engine_6_pullback(inp, "SWING") is False


# ================================================================
# Engine 7 — Exhaustion Dampener
# ================================================================

class TestEngine7Exhaustion:

    def test_no_penalty_for_clean_signal(self):
        inp = _mk_inputs(rsi=60.0, return_5d=0.05, rvol_today=1.5, rvol_3d_ago=1.4)
        assert engine_7_exhaustion(inp) == 0.0

    def test_rsi_above_70_adds_penalty(self):
        inp = _mk_inputs(rsi=72.0, return_5d=0.05, rvol_today=1.5, rvol_3d_ago=1.4)
        assert engine_7_exhaustion(inp) == pytest.approx(0.15, abs=1e-9)

    def test_rsi_above_80_stacks(self):
        # 0.15 (>70) + 0.20 (>80) = 0.35
        inp = _mk_inputs(rsi=82.0, return_5d=0.05, rvol_today=1.5, rvol_3d_ago=1.4)
        assert engine_7_exhaustion(inp) == pytest.approx(0.35, abs=1e-9)

    def test_runup_5d_adds_penalty(self):
        inp = _mk_inputs(rsi=60.0, return_5d=0.25, rvol_today=1.5, rvol_3d_ago=1.4)
        assert engine_7_exhaustion(inp) == pytest.approx(0.15, abs=1e-9)

    def test_volume_fade_adds_penalty(self):
        # rvol_today < rvol_3d_ago * 0.7 → 0.20
        inp = _mk_inputs(rsi=60.0, return_5d=0.05, rvol_today=0.5, rvol_3d_ago=1.0)
        assert engine_7_exhaustion(inp) == pytest.approx(0.20, abs=1e-9)

    def test_full_stack_caps_at_0p7(self):
        # 0.15 + 0.20 + 0.15 + 0.20 = 0.70 (cap)
        inp = _mk_inputs(rsi=85.0, return_5d=0.30, rvol_today=0.4, rvol_3d_ago=1.0)
        assert engine_7_exhaustion(inp) == pytest.approx(E7_PENALTY_CAP, abs=1e-9)

    def test_missing_data_yields_zero_penalty(self):
        inp = _mk_inputs(rsi=None, return_5d=None, rvol_today=None, rvol_3d_ago=None)
        assert engine_7_exhaustion(inp) == 0.0


# ================================================================
# Tie-breaker & Pullback-to-Breakout
# ================================================================

class TestTieBreaker:

    def test_e4_wins_when_both_fire_same_bar(self):
        # Both fire today — pullback_to_breakout must NOT count this
        # as the bonus pattern (spec: "E4 wins"), so the helper returns False.
        inp = _mk_inputs(
            price=99.0, ema20=98.0,
            e4_bars_since_55d=0,        # SWING bucket — bar 0 = today
            last5_pullback_ok=True,
        )
        assert detect_pullback_to_breakout(inp, "SWING") is False

    def test_pullback_to_breakout_fires_within_lookback(self):
        # E4 fired 2 bars ago, today's bar is an E6 pullback → bonus
        inp = _mk_inputs(
            price=99.0, ema20=98.0,
            e4_bars_since_55d=2,
            last5_pullback_ok=True,
        )
        assert detect_pullback_to_breakout(inp, "SWING") is True

    def test_pullback_to_breakout_does_not_fire_outside_lookback(self):
        # E4 fired 5 bars ago — outside the (default 3) lookback window
        inp = _mk_inputs(
            price=99.0, ema20=98.0,
            e4_bars_since_55d=PULLBACK_TO_BREAKOUT_LOOKBACK_BARS + 1,
            last5_pullback_ok=True,
        )
        assert detect_pullback_to_breakout(inp, "SWING") is False

    def test_pullback_to_breakout_requires_e6(self):
        # E4 within lookback but E6 fails (price too far from EMA20)
        inp = _mk_inputs(
            price=110.0, ema20=98.0,
            e4_bars_since_55d=2,
            last5_pullback_ok=True,
        )
        assert detect_pullback_to_breakout(inp, "SWING") is False

    def test_no_breakout_means_no_bonus(self):
        inp = _mk_inputs(
            e4_bars_since_55d=None,
            price=99.0, ema20=98.0, last5_pullback_ok=True,
        )
        assert detect_pullback_to_breakout(inp, "SWING") is False

    def test_unknown_mode_returns_false(self):
        inp = _mk_inputs(price=99.0, ema20=98.0, last5_pullback_ok=True,
                         e4_bars_since_55d=2)
        assert detect_pullback_to_breakout(inp, "TOPLANIYOR") is False


# ================================================================
# compute_engines — orchestrator-shape sanity check
# ================================================================

class TestComputeEngines:

    def test_swing_full_pass_returns_expected_keys(self):
        inp = _mk_inputs()
        out = compute_engines(inp, "SWING")
        # Required schema keys (spec §19)
        for k in (
            "e1_trend", "e2_relstr", "e3_volume", "e4_breakout",
            "e5_compression", "e6_pullback", "e7_exhaustion",
            "pullback_to_breakout",
        ):
            assert k in out
        # Schema spot-checks
        assert isinstance(out["e1_trend"], int)
        assert "score" in out["e2_relstr"]
        assert "rvol" in out["e3_volume"]
        assert "type" in out["e4_breakout"]
        assert "compressed" in out["e5_compression"]
        assert isinstance(out["e6_pullback"], bool)
        assert 0.0 <= out["e7_exhaustion"] <= E7_PENALTY_CAP
        assert isinstance(out["pullback_to_breakout"], bool)

    def test_compute_engines_does_not_mutate_inputs(self):
        inp = _mk_inputs()
        before = inp.__dict__.copy()
        _ = compute_engines(inp, "HIZLI")
        # frozen dataclass — but verify defensively
        assert inp.__dict__ == before


# ================================================================
# build_engine_inputs — full-pipeline integration on synthetic OHLCV
# ================================================================

@pytest.fixture
def uptrend_ohlcv() -> pd.DataFrame:
    """250-bar uptrend, fixed-seed determinism.

    Drift/sigma chosen so the 20-bar tail change has ~3:1 SNR — keeps the
    random-walk realism while making EMA20 > EMA50 > EMA200 hold reliably
    (the prior 0.05/0.50 settings were noise-dominated and could flip
    EMA20 below EMA50 on a tail pullback even though the long-term trend
    was up).
    """
    rng = np.random.default_rng(42)
    closes = np.cumsum(rng.normal(0.20, 0.30, 250)) + 100
    return pd.DataFrame({
        "Open":   closes * 0.99,
        "High":   closes * 1.01,
        "Low":    closes * 0.98,
        "Close":  closes,
        "Volume": rng.integers(100_000, 500_000, 250),
    })


@pytest.fixture
def benchmark_ohlcv() -> pd.DataFrame:
    """Weaker benchmark — used to make stock RS positive."""
    rng = np.random.default_rng(43)
    closes = np.cumsum(rng.normal(0.02, 0.3, 250)) + 100
    return pd.DataFrame({"Close": closes})


class TestBuildEngineInputs:

    def test_emas_present_for_long_history(self, uptrend_ohlcv):
        inputs = build_engine_inputs(
            hist_df=uptrend_ohlcv, tech={}, bench_df=None,
            sector_group="sanayi", benchmark="XU100",
        )
        assert inputs.ema20  is not None
        assert inputs.ema50  is not None
        assert inputs.ema200 is not None
        # 250-bar uptrend should have ema20 > ema50 > ema200
        assert inputs.ema20 > inputs.ema50 > inputs.ema200

    def test_returns_calculated(self, uptrend_ohlcv):
        inputs = build_engine_inputs(
            hist_df=uptrend_ohlcv, tech={}, bench_df=None,
            sector_group="sanayi",
        )
        for ret in (inputs.return_5d, inputs.return_20d, inputs.return_60d):
            assert ret is not None
            assert math.isfinite(ret)

    def test_short_history_skips_60d_return(self):
        rng = np.random.default_rng(1)
        closes = np.cumsum(rng.normal(0.05, 0.5, 30)) + 100
        df = pd.DataFrame({
            "Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
            "Close": closes, "Volume": rng.integers(100, 500, 30),
        })
        inputs = build_engine_inputs(
            hist_df=df, tech={}, bench_df=None, sector_group="sanayi",
        )
        assert inputs.return_5d is not None
        assert inputs.return_20d is not None
        assert inputs.return_60d is None    # not enough history
        assert inputs.ema200 is None

    def test_empty_hist_returns_safe_inputs(self):
        df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        inputs = build_engine_inputs(
            hist_df=df, tech={}, bench_df=None, sector_group="sanayi",
        )
        assert inputs.price is None
        assert inputs.bars_available == 0
        # Engines should not crash on this input
        assert engine_1_trend(inputs, "HIZLI") == 0
        assert engine_3_volume(inputs, "HIZLI")["passed"] is False
        assert engine_5_compression(inputs)["compressed"] is False

    def test_benchmark_returns_attached(self, uptrend_ohlcv, benchmark_ohlcv):
        inputs = build_engine_inputs(
            hist_df=uptrend_ohlcv, tech={}, bench_df=benchmark_ohlcv,
            sector_group="sanayi", benchmark="XU100",
        )
        assert inputs.bench_return_20d is not None
        assert inputs.bench_return_60d is not None
        # E2 should produce a numeric score
        out = engine_2_relstr(inputs)
        assert out["score"] in (0.0, 0.5, 1.0)

    def test_tech_dict_supplies_rsi_and_atr(self, uptrend_ohlcv):
        tech = {"rsi": 55.5, "atr": 1.7, "bb_width": 0.03, "adx": 22.0,
                "plus_di": 18.0, "minus_di": 14.0}
        inputs = build_engine_inputs(
            hist_df=uptrend_ohlcv, tech=tech, bench_df=None,
            sector_group="sanayi",
        )
        assert inputs.rsi == 55.5
        assert inputs.atr14 == 1.7
        assert inputs.bb_width_today == 0.03

    def test_higher_lows_count_bounded_by_lookback(self, uptrend_ohlcv):
        inputs = build_engine_inputs(
            hist_df=uptrend_ohlcv, tech={}, bench_df=None,
            sector_group="sanayi",
        )
        assert 0 <= inputs.higher_lows_count_10d <= 10

    def test_breakout_lookups_dont_crash_short_history(self):
        rng = np.random.default_rng(2)
        closes = np.cumsum(rng.normal(0.0, 0.5, 30)) + 100
        df = pd.DataFrame({
            "Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
            "Close": closes, "Volume": rng.integers(100, 500, 30),
        })
        inputs = build_engine_inputs(
            hist_df=df, tech={}, bench_df=None, sector_group="sanayi",
        )
        # 30 bars: 20-d breakout possible, 55-d / 6m must be None
        assert inputs.high_55d is None
        assert inputs.high_6m  is None
        assert inputs.e4_bars_since_55d is None
        assert inputs.e4_bars_since_6m  is None

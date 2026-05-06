# ================================================================
# BULLWATCH TESTS — Deterministic, no I/O, no network.
#
# These tests exercise:
#   - Universe filters (float cap, liquidity)
#   - Each engine's sub-scoring behaviour (thresholds, monotonicity)
#   - Pattern detection (shakeout, absorption, tight closes, walk-up)
#   - End-to-end score_symbol() with synthetic metrics + OHLCV
#   - scan() orchestration with injected fakes
#   - Score is bounded [0,100] and zone classification is sane
# ================================================================

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path

# Project root on path so `from features.bullwatch_features import ...` works
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from features.bullwatch_features import (
    float_market_cap, passes_float_cap,
    revenue_to_marketcap, revenue_mispricing_tier,
    avg_traded_value_20d, passes_liquidity,
    relative_volume, float_pressure,
    price_change_5d, is_price_calm,
    atr_compression_ratio, bb_width_compression_ratio,
    detect_shakeout_recovery, detect_absorption,
    detect_tight_closes, detect_walk_up_accumulation,
    detect_price_action_patterns, ownership_signal,
    FLOAT_MARKET_CAP_CAP_TL, LIQUIDITY_FLOOR_TL,
)
from engine.bullwatch import (
    score_symbol, scan, BullWatchResult,
    _classify_zone, _engine_float_pressure, _engine_silent_volume,
    _engine_revenue_mispricing, _engine_compression,
    _engine_fundamental_quality, _engine_price_action,
    WEIGHTS_WITH_OWNERSHIP, FLOAT_PRESSURE_STRONG,
    FLOAT_PRESSURE_VERY_STRONG, FLOAT_PRESSURE_EXTREME,
)


# ----------------------------------------------------------------
# Fixtures: synthetic OHLCV builders
# ----------------------------------------------------------------
def _ohlcv(closes, highs=None, lows=None, opens=None, volumes=None):
    """Build a deterministic OHLCV DataFrame from close array."""
    n = len(closes)
    closes = np.asarray(closes, dtype=float)
    if opens is None:
        opens = np.concatenate([[closes[0]], closes[:-1]])
    if highs is None:
        highs = np.maximum(opens, closes) * 1.01
    if lows is None:
        lows = np.minimum(opens, closes) * 0.99
    if volumes is None:
        volumes = np.full(n, 1_000_000.0)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows,
         "Close": closes, "Volume": volumes},
        index=idx,
    )


@pytest.fixture
def quiet_df():
    """A flat-ish 100-day series — perfect for compression testing."""
    rng = np.random.default_rng(42)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.4, 120))
    df = _ohlcv(closes, volumes=np.full(120, 50_000.0))
    return df


@pytest.fixture
def healthy_metrics():
    """A small-cap that passes every filter."""
    return {
        "symbol": "TEST",
        "ticker": "TEST",
        "market_cap": 500_000_000,    # 500M
        "free_float": 0.25,            # → float mcap = 125M (passes 150M cap)
        "shares": 25_000_000,
        "revenue": 3_000_000_000,      # 6× market cap → tier 1
        "pe": 8.5,
        "roe": 0.22,
        "net_debt_ebitda": 0.8,
        "price": 20.0,
    }


# ================================================================
# UNIVERSE FILTERS
# ================================================================
class TestFloatMarketCap:
    def test_basic(self):
        assert float_market_cap(1_000_000_000, 0.30) == 300_000_000

    def test_handles_percentage(self):
        # If someone hands us 30 instead of 0.30, we normalize
        assert float_market_cap(1_000_000_000, 30.0) == 300_000_000

    def test_none_inputs(self):
        assert float_market_cap(None, 0.3) is None
        assert float_market_cap(1e9, None) is None

    def test_invalid_freefloat(self):
        assert float_market_cap(1e9, 0) is None
        assert float_market_cap(1e9, 1.5) is None  # above 100% but below 1.5 threshold? no, 1.5 is rejected
        # 0.5 should work
        assert float_market_cap(1e9, 0.5) == 500_000_000

    def test_passes_cap(self):
        # 100M float mcap → passes 250M cap
        assert passes_float_cap(500_000_000, 0.20) is True
        # 200M float mcap → also passes 250M cap (was failing under 150M)
        assert passes_float_cap(1_000_000_000, 0.20) is True
        # 300M float mcap → fails 250M cap
        assert passes_float_cap(1_500_000_000, 0.20) is False
        # Explicit cap_tl override still works
        assert passes_float_cap(1_000_000_000, 0.20, cap_tl=150_000_000) is False


class TestRevenueMispricing:
    def test_tier_zero(self):
        assert revenue_mispricing_tier(2.5) == 0
        assert revenue_mispricing_tier(None) == 0

    def test_tier_one(self):
        assert revenue_mispricing_tier(5.0) == 1
        assert revenue_mispricing_tier(7.5) == 1

    def test_tier_two(self):
        assert revenue_mispricing_tier(10.0) == 2
        assert revenue_mispricing_tier(25.0) == 2

    def test_revenue_to_marketcap(self):
        assert revenue_to_marketcap(5e9, 1e9) == 5.0
        assert revenue_to_marketcap(5e9, 0) is None
        assert revenue_to_marketcap(None, 1e9) is None


class TestLiquidity:
    def test_above_floor(self):
        df = _ohlcv([10.0] * 30, volumes=np.full(30, 1_000_000.0))
        # 10 * 1M = 10M traded value → passes 5M floor
        assert passes_liquidity(df) is True

    def test_below_floor(self):
        df = _ohlcv([10.0] * 30, volumes=np.full(30, 100_000.0))
        # 10 * 100k = 1M < 5M floor → fails
        assert passes_liquidity(df) is False

    def test_no_data(self):
        assert passes_liquidity(None) is False
        assert passes_liquidity(_ohlcv([10, 11])) is False  # too few rows


# ================================================================
# RELATIVE VOLUME + FLOAT PRESSURE
# ================================================================
class TestRelativeVolume:
    def test_strong(self):
        vols = list(np.full(20, 100_000.0)) + [400_000.0]
        df = _ohlcv([10.0] * 21, volumes=np.array(vols))
        rv = relative_volume(df)
        assert rv is not None
        assert 3.9 <= rv <= 4.1

    def test_normal(self):
        df = _ohlcv([10.0] * 21, volumes=np.full(21, 100_000.0))
        rv = relative_volume(df)
        assert rv is not None
        assert abs(rv - 1.0) < 0.01

    def test_too_short(self):
        df = _ohlcv([10.0] * 4)
        assert relative_volume(df) is None


class TestFloatPressure:
    def test_extreme(self):
        # 10M shares * 0.20 ff = 2M floating; 200k vol → 10% float pressure
        vols = list(np.full(20, 50_000.0)) + [200_000.0]
        df = _ohlcv([10.0] * 21, volumes=np.array(vols))
        fp = float_pressure(df, shares_outstanding=10_000_000, free_float=0.20)
        assert fp is not None
        assert abs(fp - 0.10) < 1e-6

    def test_none_inputs(self):
        df = _ohlcv([10.0] * 5)
        assert float_pressure(df, None, 0.3) is None
        assert float_pressure(df, 1e6, None) is None
        assert float_pressure(None, 1e6, 0.3) is None

    def test_invalid_freefloat(self):
        df = _ohlcv([10.0] * 5)
        assert float_pressure(df, 1e6, 0) is None


# ================================================================
# PRICE-ACTION PATTERN DETECTORS
# ================================================================
class TestShakeoutRecovery:
    def test_classic_shakeout(self):
        # 20 normal sessions + a shakeout candle: long lower wick,
        # close near the high, volume 2x average.
        n = 20
        closes = list(np.full(n, 100.0)) + [100.5]
        opens = list(np.full(n, 100.0)) + [100.0]
        # Last candle: low 95, high 101, open 100, close 100.5 → lower wick = 5
        highs = list(np.full(n, 100.5)) + [101.0]
        lows = list(np.full(n, 99.5)) + [95.0]
        vols = list(np.full(n, 100_000.0)) + [250_000.0]
        df = _ohlcv(closes, highs, lows, opens, vols)
        assert detect_shakeout_recovery(df) is True

    def test_no_wick(self):
        df = _ohlcv([100.0] * 25, volumes=np.full(25, 100_000.0))
        assert detect_shakeout_recovery(df) is False


class TestAbsorption:
    def test_high_vol_small_body(self):
        # 20 normal + 1 absorption candle: tiny body, big volume
        n = 20
        closes = list(np.full(n, 100.0)) + [100.05]
        opens = list(np.full(n, 100.0)) + [100.0]
        highs = list(np.full(n, 100.5)) + [101.0]
        lows = list(np.full(n, 99.5)) + [99.5]   # range 1.5, body 0.05 → 3% body/range
        vols = list(np.full(n, 100_000.0)) + [300_000.0]
        df = _ohlcv(closes, highs, lows, opens, vols)
        assert detect_absorption(df) is True

    def test_directional_candle_rejected(self):
        # Big body → not absorption
        n = 20
        closes = list(np.full(n, 100.0)) + [102.0]
        opens = list(np.full(n, 100.0)) + [100.0]
        highs = list(np.full(n, 100.5)) + [102.5]
        lows = list(np.full(n, 99.5)) + [99.8]
        vols = list(np.full(n, 100_000.0)) + [300_000.0]
        df = _ohlcv(closes, highs, lows, opens, vols)
        assert detect_absorption(df) is False


class TestTightCloses:
    def test_clustered(self):
        df = _ohlcv([100.0, 100.5, 100.2, 100.8, 100.3])
        # spread = 0.8 / 100.36 ≈ 0.8% < 2.5%
        assert detect_tight_closes(df) is True

    def test_spread_too_wide(self):
        df = _ohlcv([100.0, 105.0, 95.0, 110.0, 90.0])
        assert detect_tight_closes(df) is False


class TestWalkUp:
    def test_higher_lows(self):
        # 10 prior lows around 95, then 10 lows climbing 96..98
        prior = np.full(10, 95.0)
        recent = np.linspace(96.0, 98.0, 10)
        lows = np.concatenate([prior, recent, [99.0]])
        closes = lows + 1.0
        opens = closes - 0.5
        highs = closes + 0.5
        # Volume expansion in the second half
        vols = np.concatenate([np.full(10, 100_000.0), np.full(11, 130_000.0)])
        df = _ohlcv(closes, highs, lows, opens, vols)
        assert detect_walk_up_accumulation(df) is True

    def test_no_walkup(self):
        # Random walk with same volumes → fails
        rng = np.random.default_rng(0)
        closes = 100.0 + np.cumsum(rng.normal(0, 0.5, 30))
        df = _ohlcv(closes, volumes=np.full(30, 100_000.0))
        assert detect_walk_up_accumulation(df) is False


def test_detect_price_action_patterns_aggregates():
    df = _ohlcv([100.0, 100.5, 100.2, 100.8, 100.3] * 5)
    out = detect_price_action_patterns(df)
    assert "tight_closes" in out
    assert isinstance(out["count"], int)
    assert isinstance(out["labels"], list)


# ================================================================
# COMPRESSION RATIOS
# ================================================================
class TestCompression:
    def test_atr_below_one_means_compressed(self):
        # 80 random sessions then 10 very calm sessions
        rng = np.random.default_rng(1)
        loud = 100.0 + np.cumsum(rng.normal(0, 1.0, 80))
        calm = np.full(10, loud[-1])
        closes = np.concatenate([loud, calm])
        df = _ohlcv(closes)
        ratio = atr_compression_ratio(df)
        assert ratio is not None
        assert ratio < 1.0   # current ATR squashed vs 60d median

    def test_returns_none_on_short_series(self):
        df = _ohlcv([100.0] * 30)
        assert atr_compression_ratio(df) is None

    def test_bb_width_compresses(self):
        rng = np.random.default_rng(2)
        loud = 100.0 + np.cumsum(rng.normal(0, 1.0, 80))
        calm = np.full(10, loud[-1])
        closes = np.concatenate([loud, calm])
        df = _ohlcv(closes)
        ratio = bb_width_compression_ratio(df)
        assert ratio is not None
        assert ratio < 1.0


# ================================================================
# OWNERSHIP — placeholder contract behaviour
# ================================================================
class TestOwnership:
    def test_no_data_returns_none_score(self):
        sig = ownership_signal(None)
        assert sig["score"] is None
        assert sig["coverage"] == "none"

    def test_empty_dict_returns_none(self):
        sig = ownership_signal({})
        assert sig["score"] is None
        assert sig["coverage"] == "none"

    def test_partial_signal(self):
        sig = ownership_signal({"insider_buys_90d": 1})
        assert sig["score"] is not None
        assert sig["score"] > 0
        assert sig["coverage"] == "partial"
        assert any("insider" in r.lower() for r in sig["reasons"])

    def test_full_signal(self):
        sig = ownership_signal({
            "institutional_buys_30d": 5,
            "repeated_institutions": 3,
            "insider_buys_90d": 2,
            "fund_increases": 3,
        })
        assert sig["score"] == 1.0  # all four channels at max
        assert sig["coverage"] == "full"


# ================================================================
# ENGINE SUB-SCORES — monotonicity
# ================================================================
class TestEngineSubScores:
    def test_float_pressure_monotonic(self):
        s_strong, _ = _engine_float_pressure(FLOAT_PRESSURE_STRONG)
        s_very, _ = _engine_float_pressure(FLOAT_PRESSURE_VERY_STRONG)
        s_extreme, _ = _engine_float_pressure(FLOAT_PRESSURE_EXTREME)
        assert s_strong < s_very < s_extreme
        assert s_extreme == 1.0

    def test_float_pressure_none(self):
        s, r = _engine_float_pressure(None)
        assert s is None
        assert r == []

    def test_silent_volume_thresholds(self):
        s_low, _ = _engine_silent_volume(1.0)
        s_early, _ = _engine_silent_volume(1.5)
        s_strong, _ = _engine_silent_volume(2.0)
        s_huge, _ = _engine_silent_volume(5.0)
        assert s_low <= s_early <= s_strong == s_huge == 1.0

    def test_revenue_tier_scoring(self):
        s_none, _ = _engine_revenue_mispricing(None)
        assert s_none is None
        s_low, _ = _engine_revenue_mispricing(1.0)
        s_t1, _ = _engine_revenue_mispricing(5.0)
        s_t2, _ = _engine_revenue_mispricing(10.0)
        assert s_low < s_t1 < s_t2 == 1.0

    def test_compression_only_when_below_one(self):
        s, _ = _engine_compression(0.5, 0.5)
        assert s == 1.0
        s_neutral, _ = _engine_compression(1.0, 1.0)
        assert s_neutral == 0.0
        s_none, _ = _engine_compression(None, None)
        assert s_none is None

    def test_fundamental_quality_passes(self):
        s, _ = _engine_fundamental_quality(
            {"pe": 10, "roe": 0.20, "net_debt_ebitda": 1.0})
        assert s == 1.0

    def test_fundamental_quality_fails(self):
        s, _ = _engine_fundamental_quality(
            {"pe": 50, "roe": 0.05, "net_debt_ebitda": 5.0})
        assert s == 0.0

    def test_fundamental_quality_no_data(self):
        s, _ = _engine_fundamental_quality({})
        assert s is None

    def test_price_action_zero_is_zero_not_none(self):
        s, _ = _engine_price_action({"count": 0, "labels": []})
        assert s == 0.0


# ================================================================
# WEIGHTS — sanity
# ================================================================
def test_weights_sum_to_100():
    assert math.isclose(sum(WEIGHTS_WITH_OWNERSHIP.values()), 100.0)


# ================================================================
# ZONE CLASSIFICATION
# ================================================================
class TestZoneClassification:
    def test_conviction_needs_high_score_and_tape(self):
        # high score + high RVOL → conviction
        z = _classify_zone(80.0, fp=0.05, rvol=2.5,
                           ownership_score=0.5, pattern_count=2,
                           compression_score=0.3)
        assert z == "CONVICTION"

    def test_high_score_no_tape_is_confirmed(self):
        # high-ish score but no tape → confirmed
        z = _classify_zone(70.0, fp=0.001, rvol=1.0,
                           ownership_score=0.5, pattern_count=2,
                           compression_score=0.5)
        assert z == "CONFIRMED"

    def test_low_score_is_early(self):
        z = _classify_zone(40.0, fp=None, rvol=1.0,
                           ownership_score=None, pattern_count=0,
                           compression_score=0.5)
        assert z == "EARLY"


# ================================================================
# END-TO-END score_symbol()
# ================================================================
class TestScoreSymbolE2E:
    def test_oversize_market_cap_rejected(self, healthy_metrics):
        m = dict(healthy_metrics, market_cap=10_000_000_000, free_float=0.5)
        # 10B * 0.5 = 5B float mcap → way above 150M cap
        df = _ohlcv([10.0] * 100, volumes=np.full(100, 1_000_000.0))
        r = score_symbol(m, df)
        assert r.eligible is False
        assert "float mcap" in r.reject_reason

    def test_dead_board_rejected(self, healthy_metrics):
        df = _ohlcv([10.0] * 100, volumes=np.full(100, 50_000.0))
        # 10 * 50k = 500k traded value → fails 5M liquidity floor
        r = score_symbol(healthy_metrics, df)
        assert r.eligible is False
        assert "traded value" in r.reject_reason

    def test_score_in_bounds(self, healthy_metrics, quiet_df):
        # Patch volume to pass liquidity floor (5M)
        df = quiet_df.copy()
        df["Volume"] = 1_000_000.0    # 100 * 1M = 100M traded value — easy pass
        r = score_symbol(healthy_metrics, df)
        assert r.eligible is True
        assert 0.0 <= r.score <= 100.0
        assert r.zone in ("EARLY", "CONFIRMED", "CONVICTION")

    def test_components_only_for_engines_with_data(self, healthy_metrics, quiet_df):
        df = quiet_df.copy()
        df["Volume"] = 1_000_000.0
        # Strip fundamental data — that engine should drop out cleanly
        m = dict(healthy_metrics)
        m.pop("pe"); m.pop("roe"); m.pop("net_debt_ebitda")
        r = score_symbol(m, df)
        assert r.eligible is True
        assert "fundamental_quality" not in r.components
        # Other engines still score
        assert "revenue_mispricing" in r.components

    def test_high_rvol_reaches_conviction_zone(self, healthy_metrics):
        # Build a series with strong float pressure on the last day
        n = 100
        closes = np.full(n, 20.0)
        vols = np.concatenate([np.full(n - 1, 50_000.0), [400_000.0]])
        # Need traded value to pass: 20 * 50k = 1M ... need higher price or vol
        # Bump base volume so 20d avg traded value >= 5M
        vols = np.concatenate([np.full(n - 1, 500_000.0), [4_000_000.0]])
        df = _ohlcv(closes, volumes=vols)
        # With 25M shares * 0.25 ff = 6.25M float; vol_today 4M → fp=0.64 (extreme)
        r = score_symbol(healthy_metrics, df)
        assert r.eligible is True
        assert r.score >= 50  # at least decent
        # rvol = 4M / 500k = 8x — well above strong threshold
        assert r.metrics["rvol"] > 2.0
        assert r.zone in ("CONFIRMED", "CONVICTION")

    def test_price_calm_boost_is_capped(self, healthy_metrics, quiet_df):
        df = quiet_df.copy()
        df["Volume"] = 1_000_000.0
        r = score_symbol(healthy_metrics, df)
        # Score should never exceed 100 even with calm boost
        assert r.score <= 100.0

    def test_runtime_cap_tl_override(self, healthy_metrics, quiet_df):
        # healthy_metrics has 500M mcap × 0.25 = 125M float (passes default 250M)
        df = quiet_df.copy()
        df["Volume"] = 1_000_000.0
        # Default cap (250M): eligible
        r1 = score_symbol(healthy_metrics, df)
        assert r1.eligible is True
        # Tight cap (50M): rejected, with reason mentioning the override
        r2 = score_symbol(healthy_metrics, df, cap_tl=50_000_000)
        assert r2.eligible is False
        assert "50M cap" in (r2.reject_reason or "")
        # Loose cap (1B): definitely eligible
        r3 = score_symbol(healthy_metrics, df, cap_tl=1_000_000_000)
        assert r3.eligible is True


# ================================================================
# scan() ORCHESTRATION — fully injected fakes, no I/O
# ================================================================
class TestScanOrchestration:
    def test_scan_filters_ineligible(self, healthy_metrics):
        # Two symbols: one passes, one fails (huge market cap)
        df_ok = _ohlcv([20.0] * 100, volumes=np.full(100, 1_000_000.0))
        df_bad = _ohlcv([20.0] * 100, volumes=np.full(100, 1_000_000.0))

        def metrics_fn(sym):
            if sym == "GOOD":
                return dict(healthy_metrics, symbol="GOOD", ticker="GOOD")
            return {"symbol": "BAD", "ticker": "BAD",
                    "market_cap": 1e11, "free_float": 0.5,
                    "shares": 5e9, "revenue": 1e9, "price": 20.0}

        def history_fn(symbols):
            return {"GOOD": df_ok, "BAD": df_bad}

        results = scan(["GOOD", "BAD"],
                       metrics_fn=metrics_fn,
                       history_fn=history_fn,
                       ownership_fn=lambda s: None,
                       max_workers=2)
        # Only GOOD should be included by default
        assert len(results) == 1
        assert results[0].symbol == "GOOD"
        assert results[0].eligible is True

    def test_scan_handles_metrics_failure(self, healthy_metrics):
        df = _ohlcv([20.0] * 100, volumes=np.full(100, 1_000_000.0))

        def metrics_fn(sym):
            if sym == "BROKEN":
                raise RuntimeError("upstream is on fire")
            return dict(healthy_metrics, symbol=sym, ticker=sym)

        def history_fn(symbols):
            return {s: df for s in symbols}

        results = scan(["GOOD", "BROKEN"],
                       metrics_fn=metrics_fn,
                       history_fn=history_fn,
                       ownership_fn=lambda s: None)
        # GOOD survives, BROKEN gets dropped silently
        symbols = {r.symbol for r in results}
        assert "GOOD" in symbols
        assert "BROKEN" not in symbols

    def test_scan_includes_ineligible_when_requested(self, healthy_metrics):
        df = _ohlcv([20.0] * 100, volumes=np.full(100, 1_000_000.0))

        def metrics_fn(sym):
            return {"symbol": sym, "ticker": sym,
                    "market_cap": 1e11, "free_float": 0.5,  # too big
                    "shares": 5e9, "revenue": 1e9, "price": 20.0}

        def history_fn(symbols):
            return {s: df for s in symbols}

        results = scan(["X"], metrics_fn=metrics_fn, history_fn=history_fn,
                       ownership_fn=lambda s: None,
                       include_ineligible=True)
        assert len(results) == 1
        assert results[0].eligible is False
        assert results[0].reject_reason is not None


# ================================================================
# RESULT SERIALIZATION
# ================================================================
class TestResultSerialization:
    def test_to_dict_has_required_fields(self, healthy_metrics, quiet_df):
        df = quiet_df.copy()
        df["Volume"] = 1_000_000.0
        r = score_symbol(healthy_metrics, df)
        d = r.to_dict()
        for key in ("symbol", "score", "zone", "pattern", "data_quality",
                    "components", "metrics", "reasons", "eligible"):
            assert key in d
        assert isinstance(d["score"], float)
        assert d["zone"] in ("EARLY", "CONFIRMED", "CONVICTION")

    def test_pattern_never_says_buy_or_sell(self, healthy_metrics, quiet_df):
        # Strict guarantee: the engine never speaks in trading directives.
        df = quiet_df.copy()
        df["Volume"] = 1_000_000.0
        r = score_symbol(healthy_metrics, df)
        forbidden = {"buy", "sell", "long", "short", "target", "stop"}
        pattern_l = r.pattern.lower()
        for word in forbidden:
            assert word not in pattern_l, f"forbidden word '{word}' in pattern"

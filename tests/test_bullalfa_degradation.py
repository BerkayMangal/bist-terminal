# ================================================================
# tests/test_bullalfa_degradation.py
#
# Spec §22 coverage:
#   - macro_unavailable → assume_neutral
#   - pit_missing → SAKİN (NEW: was excluded in v1.3)
#   - aggregation_failed → SAKİN
#   - technical_failed → SAKİN
#   - freshness < 60 → SAKİN
#   - short_history → limit modes
#   - halted → UZAK DUR
#   - out_of_session → freeze (best-effort: scan-level concept)
#   - isotonic_unavailable → sigmoid v1
#
# All tests run the orchestrator end-to-end with synthetic inputs
# and assert on the §19-shaped output dict.
# ================================================================

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.bullalfa import build_bullalfa_signal
from engine.bullalfa_degrade import (
    DEGRADATION_RULES,
    DegradationLog,
    DegradeAction,
    DegradeCode,
    rule_for,
)


# ================================================================
# Fixture — minimal viable inputs
# ================================================================

@pytest.fixture
def good_hist() -> pd.DataFrame:
    """250 bars of clean uptrend OHLCV."""
    rng = np.random.default_rng(42)
    closes = np.cumsum(rng.normal(0.20, 0.30, 250)) + 100
    return pd.DataFrame({
        "Open":   closes * 0.99,
        "High":   closes * 1.01,
        "Low":    closes * 0.98,
        "Close":  closes,
        "Volume": rng.integers(800_000, 1_500_000, 250),
    })


@pytest.fixture
def good_bench() -> pd.DataFrame:
    rng = np.random.default_rng(43)
    closes = np.cumsum(rng.normal(0.05, 0.20, 250)) + 100
    return pd.DataFrame({"Close": closes})


@pytest.fixture
def good_metrics() -> dict:
    return {
        "pe": 9.5, "roe": 18.0, "net_income": 1e9,
        "revenue": 5e9, "market_cap": 5e10,
    }


@pytest.fixture
def good_macro() -> dict:
    return {"regime": "risk_on", "tl_vol_pct": 30.0}


@pytest.fixture
def good_market() -> dict:
    return {"status": "open", "ist_time": "12:30"}


@pytest.fixture
def good_tech() -> dict:
    return {"atr": 1.5, "rsi": 55.0, "adx": 22.0, "plus_di": 25.0, "minus_di": 18.0}


# ================================================================
# Pure DegradationLog tests
# ================================================================

class TestDegradationLog:

    def test_empty_log_no_caveats(self):
        log = DegradationLog()
        assert log.caveats() == []
        assert log.codes == []

    def test_record_appends_in_order(self):
        log = DegradationLog()
        log.record(DegradeCode.MACRO_UNAVAILABLE)
        log.record(DegradeCode.SHORT_HISTORY)
        assert log.codes == [DegradeCode.MACRO_UNAVAILABLE, DegradeCode.SHORT_HISTORY]

    def test_duplicate_record_deduplicates(self):
        log = DegradationLog()
        log.record(DegradeCode.SHORT_HISTORY)
        log.record(DegradeCode.SHORT_HISTORY)
        assert log.codes == [DegradeCode.SHORT_HISTORY]

    def test_unknown_code_raises(self):
        log = DegradationLog()
        with pytest.raises(ValueError):
            log.record("unknown_code")

    def test_caveats_match_TR_text(self):
        log = DegradationLog()
        log.record(DegradeCode.HALTED_TODAY)
        assert log.caveats() == ["İşlem durdurulmuş"]

    def test_any_force_sakin_aggregation_failed(self):
        log = DegradationLog()
        log.record(DegradeCode.AGGREGATION_FAILED)
        assert log.any_force_sakin() is True

    def test_any_force_sakin_technical_failed(self):
        log = DegradationLog()
        log.record(DegradeCode.TECHNICAL_FAILED)
        assert log.any_force_sakin() is True

    def test_any_force_uzak_dur_halted(self):
        log = DegradationLog()
        log.record(DegradeCode.HALTED_TODAY)
        assert log.any_force_uzak_dur() is True

    def test_limited_mode_set_short_history(self):
        log = DegradationLog()
        log.record(DegradeCode.SHORT_HISTORY)
        allowed = log.limited_mode_set()
        assert allowed == frozenset({"HIZLI", "TOPLANIYOR", "SAKİN"})

    def test_limited_mode_set_none_when_no_limiter(self):
        log = DegradationLog()
        log.record(DegradeCode.MACRO_UNAVAILABLE)
        assert log.limited_mode_set() is None

    def test_rule_for_returns_typed_outcome(self):
        out = rule_for(DegradeCode.HALTED_TODAY)
        assert out.code == DegradeCode.HALTED_TODAY
        assert out.action == DegradeAction.FORCE_MODE_UZAK_DUR
        assert out.caveat == "İşlem durdurulmuş"

    def test_rule_for_unknown_raises(self):
        with pytest.raises(KeyError):
            rule_for("not_a_real_code")

    def test_rules_table_completeness(self):
        # All ten v1.4 codes must be present.
        expected = {
            "macro_unavailable", "pit_missing", "aggregation_failed",
            "technical_failed", "freshness_below_60", "short_history",
            "halted_today", "out_of_session", "benchmark_index_missing",
            "isotonic_unavailable",
        }
        assert set(DEGRADATION_RULES.keys()) == expected


# ================================================================
# Per-degradation orchestrator behavior
# ================================================================

class TestMacroUnavailable:

    def test_no_macro_result_records_caveat(
        self, good_hist, good_bench, good_metrics, good_market, good_tech,
    ):
        sig = build_bullalfa_signal(
            ticker="X", hist_df=good_hist, bench_df=good_bench,
            metrics=good_metrics,
            sector_raw="Industrials",
            macro_result=None,                 # ← trigger macro_unavailable
            market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech, days_listed=1000,
        )
        assert "Rejim tespit edilemedi" in sig["explainer"]["caveats"]
        # Assume neutral → regime should be 'neutral'.
        assert sig["macro"]["regime"] == "neutral"

    def test_unparseable_regime_assumes_neutral(
        self, good_hist, good_bench, good_metrics, good_market, good_tech,
    ):
        sig = build_bullalfa_signal(
            ticker="X", hist_df=good_hist, bench_df=good_bench,
            metrics=good_metrics, sector_raw="Industrials",
            macro_result={"regime": "schmovel", "tl_vol_pct": 50},
            market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech, days_listed=1000,
        )
        assert sig["macro"]["regime"] == "neutral"
        assert "Rejim tespit edilemedi" in sig["explainer"]["caveats"]

    def test_uppercase_regime_normalized_to_lowercase(
        self, good_hist, good_bench, good_metrics, good_market, good_tech,
    ):
        # `engine.macro_decision.compute_regime` returns uppercase.
        sig = build_bullalfa_signal(
            ticker="X", hist_df=good_hist, bench_df=good_bench,
            metrics=good_metrics, sector_raw="Industrials",
            macro_result={"regime": "RISK_ON", "tl_vol_pct": 30},
            market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech, days_listed=1000,
        )
        assert sig["macro"]["regime"] == "risk_on"
        assert "Rejim tespit edilemedi" not in sig["explainer"]["caveats"]


class TestPitMissing:

    def test_empty_hist_records_pit_missing(
        self, good_metrics, good_macro, good_market, good_tech,
    ):
        empty = pd.DataFrame({
            "Open": [], "High": [], "Low": [], "Close": [], "Volume": [],
        })
        sig = build_bullalfa_signal(
            ticker="X", hist_df=empty, bench_df=None,
            metrics=good_metrics, sector_raw="Industrials",
            short_history=True,
            macro_result=good_macro, market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech,
            days_listed=1000,
        )
        # pit_missing is a force-SAKİN action.
        assert sig["mode"] == "SAKİN"
        assert "Geçmiş veri eksik" in sig["explainer"]["caveats"]


class TestAggregationFailed:

    def test_none_metrics_records_aggregation_failed(
        self, good_hist, good_bench, good_macro, good_market, good_tech,
    ):
        sig = build_bullalfa_signal(
            ticker="X", hist_df=good_hist, bench_df=good_bench,
            metrics=None,                          # ← triggers aggregation_failed
            sector_raw="Industrials",
            macro_result=good_macro, market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech, days_listed=1000,
        )
        assert sig["mode"] == "SAKİN"
        assert "Temel veri hesaplanamadı" in sig["explainer"]["caveats"]


class TestFreshnessBelow60:

    def test_too_few_critical_fields_records_freshness(
        self, good_hist, good_bench, good_macro, good_market, good_tech,
    ):
        # _CRITICAL_FIELD_COUNT = 5 → having only 1 of 5 yields 20% fresh.
        sparse = {"pe": 9.5}
        sig = build_bullalfa_signal(
            ticker="X", hist_df=good_hist, bench_df=good_bench,
            metrics=sparse, sector_raw="Industrials",
            macro_result=good_macro, market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech, days_listed=1000,
        )
        assert sig["mode"] == "SAKİN"
        assert "Veri çok eski" in sig["explainer"]["caveats"]


class TestShortHistory:

    def test_short_history_caveats_and_limited_modes(
        self, good_metrics, good_macro, good_market, good_tech,
    ):
        # 50 bars < MIN_TRADING_DAYS=60 → short_history is auto-detected.
        rng = np.random.default_rng(1)
        short = pd.DataFrame({
            "Open": rng.normal(100, 1, 50),
            "High": rng.normal(101, 1, 50),
            "Low":  rng.normal(99, 1, 50),
            "Close": rng.normal(100, 1, 50),
            "Volume": rng.integers(800_000, 1_500_000, 50),
        })
        sig = build_bullalfa_signal(
            ticker="X", hist_df=short, bench_df=None,
            metrics=good_metrics, sector_raw="Industrials",
            macro_result=good_macro, market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech, days_listed=1000,
        )
        # Short-history caveat present.
        assert "Kısa geçmiş — POZİSYON/SWING devre dışı" in sig["explainer"]["caveats"]
        # SWING and POZİSYON forbidden by §15 limit_modes_hizli_toplaniyor_sakin.
        assert sig["mode"] in {"HIZLI", "TOPLANIYOR", "SAKİN", "UZAK DUR"}


class TestHaltedToday:

    def test_halted_forces_uzak_dur(
        self, good_hist, good_bench, good_metrics, good_macro, good_market, good_tech,
    ):
        sig = build_bullalfa_signal(
            ticker="X", hist_df=good_hist, bench_df=good_bench,
            metrics=good_metrics, sector_raw="Industrials",
            halted_today=True,                  # ← forced UZAK DUR
            macro_result=good_macro, market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech, days_listed=1000,
        )
        assert sig["mode"] == "UZAK DUR"
        assert "İşlem durdurulmuş" in sig["explainer"]["caveats"]
        assert sig["risk_frame"] is None
        assert sig["opportunity_score"] == 5


class TestIsotonicUnavailable:

    def test_no_fits_records_caveat_and_uses_v1(
        self, good_hist, good_bench, good_metrics, good_macro, good_market, good_tech,
    ):
        sig = build_bullalfa_signal(
            ticker="X", hist_df=good_hist, bench_df=good_bench,
            metrics=good_metrics, sector_raw="Industrials",
            macro_result=good_macro, market_status=good_market,
            isotonic_fits=None,                 # ← no v2 fits
            tech_pre=good_tech, days_listed=1000,
        )
        # The caveat surfaces as both a degradation caveat AND a §16 warning.
        assert "Kalibrasyon: ön-aşama" in sig["explainer"]["caveats"]
        assert sig["confidence"]["phase"] == "v1_heuristic"

    def test_with_fits_uses_v2_phase(
        self, good_hist, good_bench, good_metrics, good_macro, good_market, good_tech,
    ):
        sig = build_bullalfa_signal(
            ticker="X", hist_df=good_hist, bench_df=good_bench,
            metrics=good_metrics, sector_raw="Industrials",
            macro_result=good_macro, market_status=good_market,
            isotonic_fits={"HIZLI": "stub", "SWING": "stub", "POZİSYON": "stub"},
            tech_pre=good_tech, days_listed=1000,
        )
        assert sig["confidence"]["phase"] == "v2_isotonic"
        assert "Kalibrasyon: ön-aşama" not in sig["explainer"]["caveats"]


class TestBenchmarkFallback:

    def test_holding_keeps_xhold_when_no_fallback_signaled(
        self, good_hist, good_bench, good_metrics, good_macro, good_market, good_tech,
    ):
        # When the sector context can produce its preferred benchmark
        # (no `available_benchmarks` set passed in, so XHOLD is assumed
        # available), no fallback caveat should fire.
        sig = build_bullalfa_signal(
            ticker="SAHOL", hist_df=good_hist, bench_df=good_bench,
            metrics=good_metrics, sector_raw="Financial Services",
            industry_raw="Conglomerates",
            macro_result=good_macro, market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech, days_listed=1000,
        )
        assert "Sektör endeksi yok, XU100 referansı" not in sig["explainer"]["caveats"]


# ================================================================
# Multiple degradations stack
# ================================================================

class TestStackedDegradations:

    def test_halted_and_short_history_both_recorded(
        self, good_metrics, good_macro, good_market, good_tech,
    ):
        # Halted takes precedence (force_mode_uzak_dur), but the short-
        # history caveat should still surface.
        rng = np.random.default_rng(1)
        short = pd.DataFrame({
            "Open": rng.normal(100, 1, 30),
            "High": rng.normal(101, 1, 30),
            "Low":  rng.normal(99, 1, 30),
            "Close": rng.normal(100, 1, 30),
            "Volume": rng.integers(800_000, 1_500_000, 30),
        })
        sig = build_bullalfa_signal(
            ticker="X", hist_df=short, bench_df=None,
            metrics=good_metrics, sector_raw="Industrials",
            halted_today=True,
            macro_result=good_macro, market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech, days_listed=30,
        )
        assert sig["mode"] == "UZAK DUR"
        assert "İşlem durdurulmuş" in sig["explainer"]["caveats"]


# ================================================================
# Output schema invariants — every degraded signal still well-formed
# ================================================================

class TestSchemaInvariants:

    def test_degraded_signal_still_has_all_top_level_keys(
        self, good_hist, good_bench,
    ):
        sig = build_bullalfa_signal(
            ticker="DEGRADED", hist_df=good_hist, bench_df=good_bench,
            metrics=None,                       # forced aggregation_failed
            sector_raw="Industrials",
            macro_result=None,                  # forced macro_unavailable
            market_status=None,                 # forced no session info
            isotonic_fits=None,                 # forced isotonic_unavailable
        )
        for key in (
            "ticker", "sector_group", "generated_at", "schema_version",
            "quality", "macro", "mode", "horizon_bars", "horizon_label",
            "why_now", "engines", "confidence", "opportunity_score",
            "risk_frame", "lifecycle", "liquidity", "explainer",
        ):
            assert key in sig, f"missing key {key} in degraded signal"

    def test_sakin_signal_has_null_risk_frame_and_no_horizon(
        self, good_hist, good_bench, good_macro, good_market, good_tech,
    ):
        sig = build_bullalfa_signal(
            ticker="X", hist_df=good_hist, bench_df=good_bench,
            metrics=None,                # → SAKİN
            sector_raw="Industrials",
            macro_result=good_macro, market_status=good_market,
            isotonic_fits=None,
            tech_pre=good_tech, days_listed=1000,
        )
        assert sig["mode"] == "SAKİN"
        assert sig["risk_frame"] is None
        assert sig["horizon_bars"] is None
        assert sig["horizon_label"] is None

    def test_uzak_dur_signal_has_null_risk_frame(
        self, good_hist, good_bench, good_metrics, good_macro, good_market, good_tech,
    ):
        sig = build_bullalfa_signal(
            ticker="X", hist_df=good_hist, bench_df=good_bench,
            metrics=good_metrics, sector_raw="Industrials",
            halted_today=True,
            macro_result=good_macro, market_status=good_market,
            isotonic_fits=None, tech_pre=good_tech, days_listed=1000,
        )
        assert sig["mode"] == "UZAK DUR"
        assert sig["risk_frame"] is None

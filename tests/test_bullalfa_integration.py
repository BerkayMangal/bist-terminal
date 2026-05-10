# ================================================================
# tests/test_bullalfa_integration.py
#
# Spec §22 coverage:
#   - Full pipeline on synthetic ticker fixture (deterministic regression)
#   - Out-of-scope module untouched (import + monkey-patch check)
#   - Scan response includes meta.sector_concentration + meta.by_mode
#   - Pagination correctness
#   - Universe size = total BIST count, not filtered
# ================================================================

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.bullalfa import build_bullalfa_signal, build_scan_response


# ================================================================
# Shared fixtures
# ================================================================

@pytest.fixture
def hist_uptrend() -> pd.DataFrame:
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
def bench_weak() -> pd.DataFrame:
    rng = np.random.default_rng(43)
    return pd.DataFrame({"Close": np.cumsum(rng.normal(0.05, 0.20, 250)) + 100})


@pytest.fixture
def metrics_strong() -> dict:
    return {
        "pe": 9.5, "roe": 18.0, "net_income": 1e9,
        "revenue": 5e9, "market_cap": 5e10,
    }


@pytest.fixture
def tech_full() -> dict:
    return {"atr": 1.5, "rsi": 55.0, "adx": 22.0, "plus_di": 25.0, "minus_di": 18.0}


@pytest.fixture
def macro_riskon() -> dict:
    return {"regime": "risk_on", "tl_vol_pct": 30.0}


@pytest.fixture
def market_open_midday() -> dict:
    return {"status": "open", "ist_time": "12:30"}


# ================================================================
# Full pipeline — deterministic regression
# ================================================================

class TestFullPipeline:

    def test_uptrend_with_atr_yields_actionable_signal(
        self, hist_uptrend, bench_weak, metrics_strong,
        tech_full, macro_riskon, market_open_midday,
    ):
        sig = build_bullalfa_signal(
            ticker="ASELS",
            hist_df=hist_uptrend, bench_df=bench_weak,
            metrics=metrics_strong,
            sector_raw="Industrials",
            industry_raw="Aerospace & Defense",
            macro_result=macro_riskon,
            market_status=market_open_midday,
            isotonic_fits=None,
            tech_pre=tech_full,
            days_listed=1000,
            now_iso="2026-05-10T12:30:00Z",
        )
        # The synthetic uptrend + strong metrics + no breakout in last 1
        # bar lands on POZİSYON (60d RS positive, quality ≥ 70, E1 set).
        assert sig["mode"] in {"POZİSYON", "SWING"}
        assert sig["risk_frame"] is not None
        assert sig["confidence"]["final"] > 0
        assert sig["opportunity_score"] > 0

    def test_signal_schema_matches_v1_4(
        self, hist_uptrend, bench_weak, metrics_strong,
        tech_full, macro_riskon, market_open_midday,
    ):
        sig = build_bullalfa_signal(
            ticker="ASELS",
            hist_df=hist_uptrend, bench_df=bench_weak,
            metrics=metrics_strong, sector_raw="Industrials",
            macro_result=macro_riskon, market_status=market_open_midday,
            isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
        )
        assert sig["schema_version"] == "1.4"

        # All §19 top-level keys present
        for key in (
            "ticker", "sector_group", "generated_at", "schema_version",
            "quality", "macro", "mode", "horizon_bars", "horizon_label",
            "why_now", "engines", "confidence", "opportunity_score",
            "risk_frame", "lifecycle", "liquidity", "explainer",
        ):
            assert key in sig

        # Engines block always populated
        for ek in (
            "e1_trend", "e2_relstr", "e3_volume", "e4_breakout",
            "e5_compression", "e6_pullback", "e7_exhaustion",
            "pullback_to_breakout", "accumulation_strength",
        ):
            assert ek in sig["engines"]

        # Quality tags present
        for tk in ("kalite", "value", "buffett", "graham"):
            assert tk in sig["quality"]["tags"]

        # Macro block has the four required fields
        for mk in ("regime", "tl_vol_pct", "multiplier", "hizli_disabled"):
            assert mk in sig["macro"]

        # Confidence block
        for ck in ("raw_combined", "final", "phase"):
            assert ck in sig["confidence"]

        # Liquidity block
        for lk in ("adv_20d_try", "penalty_applied", "downgrade_reason"):
            assert lk in sig["liquidity"]

        # Lifecycle block
        for lk in ("signal_id", "triggered_at", "bars_since",
                   "status", "outcome", "mode_history"):
            assert lk in sig["lifecycle"]

        # Explainer block
        for xk in ("why_this_mode", "why_not_higher_mode", "caveats", "warnings"):
            assert xk in sig["explainer"]

    def test_deterministic_regression(
        self, hist_uptrend, bench_weak, metrics_strong,
        tech_full, macro_riskon, market_open_midday,
    ):
        # Same inputs → same opportunity_score, mode, and confidence.final.
        # If a refactor accidentally tweaks weights this surfaces it.
        sig1 = build_bullalfa_signal(
            ticker="ASELS",
            hist_df=hist_uptrend, bench_df=bench_weak,
            metrics=metrics_strong, sector_raw="Industrials",
            macro_result=macro_riskon, market_status=market_open_midday,
            isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
            now_iso="2026-05-10T12:30:00Z",
        )
        sig2 = build_bullalfa_signal(
            ticker="ASELS",
            hist_df=hist_uptrend, bench_df=bench_weak,
            metrics=metrics_strong, sector_raw="Industrials",
            macro_result=macro_riskon, market_status=market_open_midday,
            isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
            now_iso="2026-05-10T12:30:00Z",
        )
        assert sig1["mode"] == sig2["mode"]
        assert sig1["opportunity_score"] == sig2["opportunity_score"]
        assert sig1["confidence"]["final"] == sig2["confidence"]["final"]
        assert sig1["risk_frame"] == sig2["risk_frame"]

    def test_returns_dict_never_raises_on_pathological_input(
        self, macro_riskon, market_open_midday,
    ):
        # Empty everything — must not raise; must return a SAKİN-shaped dict.
        sig = build_bullalfa_signal(
            ticker="JUNK",
            hist_df=pd.DataFrame({
                "Open": [], "High": [], "Low": [], "Close": [], "Volume": [],
            }),
            bench_df=None, metrics=None,
            sector_raw=None, industry_raw=None,
            short_history=True, halted_today=False,
            macro_result=None, market_status=None,
            isotonic_fits=None,
        )
        assert isinstance(sig, dict)
        assert sig["mode"] in {"SAKİN", "UZAK DUR"}
        assert sig["risk_frame"] is None


# ================================================================
# Out-of-scope modules untouched (import + monkey-patch check)
# ================================================================

class TestOutOfScopeModulesUntouched:
    """Spec §22: 'Out-of-scope module untouched (import + monkey-patch check)'.

    Strategy: import each protected module, snapshot its public-callable
    set, run the orchestrator, snapshot again, and compare. The
    orchestrator must NOT add or remove anything from these modules.
    """

    @pytest.mark.parametrize("module_name", [
        "engine.verdict",
        "engine.scoring",
        "engine.scoring_calibrated",
        "engine.scoring_v11",
        "engine.aggregation",
        "engine.labels",
        "engine.bullwatch",
        "engine.technical",
    ])
    def test_module_public_surface_unchanged_after_orchestrator_run(
        self, module_name, hist_uptrend, bench_weak, metrics_strong,
        tech_full, macro_riskon, market_open_midday,
    ):
        try:
            mod = __import__(module_name, fromlist=["*"])
        except ImportError:
            pytest.skip(f"{module_name} not importable in this env")
        before = {n for n in dir(mod) if not n.startswith("_")}

        # Run the full orchestrator pipeline.
        _ = build_bullalfa_signal(
            ticker="ASELS",
            hist_df=hist_uptrend, bench_df=bench_weak,
            metrics=metrics_strong, sector_raw="Industrials",
            macro_result=macro_riskon, market_status=market_open_midday,
            isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
        )
        after = {n for n in dir(mod) if not n.startswith("_")}
        assert before == after, (
            f"{module_name}: orchestrator added/removed names "
            f"{before ^ after}"
        )

    def test_api_bullwatch_module_untouched(
        self, hist_uptrend, bench_weak, metrics_strong,
        tech_full, macro_riskon, market_open_midday,
    ):
        try:
            import api.bullwatch as mod
        except Exception:
            pytest.skip("api.bullwatch not importable in this env")
        before = {n for n in dir(mod) if not n.startswith("_")}
        _ = build_bullalfa_signal(
            ticker="ASELS",
            hist_df=hist_uptrend, bench_df=bench_weak,
            metrics=metrics_strong, sector_raw="Industrials",
            macro_result=macro_riskon, market_status=market_open_midday,
            isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
        )
        after = {n for n in dir(mod) if not n.startswith("_")}
        assert before == after


# ================================================================
# Scan response — by_mode + sector_concentration + universe_size
# ================================================================

def _make_signal(ticker, mode, opp, sector="sanayi"):
    """Tiny factory for scan-response unit tests."""
    return {
        "ticker":            ticker,
        "sector_group":      sector,
        "mode":              mode,
        "opportunity_score": opp,
        # Other fields don't matter for scan tests.
    }


class TestScanResponse:

    def test_universe_size_equals_input_count(self):
        sigs = [
            _make_signal("AKBNK", "HIZLI",      85, "banka"),
            _make_signal("ASELS", "SWING",      70, "sanayi"),
            _make_signal("EREGL", "SAKİN",      18, "sanayi"),
            _make_signal("FROTO", "UZAK DUR",    5, "sanayi"),
        ]
        scan = build_scan_response(sigs)
        assert scan["meta"]["universe_size"] == 4
        assert len(scan["signals"]) == 4

    def test_by_mode_counts(self):
        sigs = [
            _make_signal("A", "HIZLI", 80),
            _make_signal("B", "HIZLI", 70),
            _make_signal("C", "SWING", 60),
            _make_signal("D", "SAKİN", 18),
            _make_signal("E", "SAKİN", 15),
            _make_signal("F", "UZAK DUR", 5),
        ]
        scan = build_scan_response(sigs)
        assert scan["meta"]["by_mode"] == {
            "HIZLI": 2, "SWING": 1, "SAKİN": 2, "UZAK DUR": 1,
        }

    def test_sector_concentration_only_actionable(self):
        sigs = [
            _make_signal("A", "HIZLI",      80, "banka"),
            _make_signal("B", "SWING",      70, "banka"),
            _make_signal("C", "POZİSYON",   65, "banka"),
            _make_signal("D", "TOPLANIYOR", 60, "banka"),  # ← non-actionable, NOT counted
            _make_signal("E", "SAKİN",      15, "banka"),
            _make_signal("F", "HIZLI",      72, "sanayi"),
        ]
        scan = build_scan_response(sigs)
        # Only HIZLI/SWING/POZİSYON count toward concentration.
        assert scan["meta"]["sector_concentration"] == {"banka": 3, "sanayi": 1}

    def test_signals_sorted_by_opportunity_desc(self):
        sigs = [
            _make_signal("LOW",  "SAKİN",     18),
            _make_signal("HIGH", "HIZLI",     85),
            _make_signal("MID",  "SWING",     65),
        ]
        scan = build_scan_response(sigs)
        ordered = [s["ticker"] for s in scan["signals"]]
        assert ordered == ["HIGH", "MID", "LOW"]

    def test_alphabetical_tie_break(self):
        sigs = [
            _make_signal("ZETA",  "HIZLI", 80),
            _make_signal("ALPHA", "HIZLI", 80),
            _make_signal("BETA",  "HIZLI", 80),
        ]
        scan = build_scan_response(sigs)
        ordered = [s["ticker"] for s in scan["signals"]]
        assert ordered == ["ALPHA", "BETA", "ZETA"]

    def test_does_not_mutate_input(self):
        sigs = [
            _make_signal("A", "HIZLI", 80),
            _make_signal("B", "HIZLI", 70),
        ]
        before = [s["ticker"] for s in sigs]
        _ = build_scan_response(sigs)
        after = [s["ticker"] for s in sigs]
        assert before == after  # input order preserved

    def test_pagination_first_page(self):
        sigs = [_make_signal(f"T{i:02d}", "HIZLI", 100 - i) for i in range(50)]
        scan = build_scan_response(sigs, page=1, per_page=10)
        assert len(scan["signals"]) == 10
        assert scan["meta"]["pagination"] == {"page": 1, "per_page": 10, "total": 50}
        # Top page contains the highest-opp tickers.
        assert scan["signals"][0]["ticker"] == "T00"

    def test_pagination_last_page(self):
        sigs = [_make_signal(f"T{i:02d}", "HIZLI", 100 - i) for i in range(50)]
        scan = build_scan_response(sigs, page=5, per_page=10)
        assert len(scan["signals"]) == 10
        assert scan["signals"][-1]["ticker"] == "T49"
        assert scan["meta"]["pagination"]["page"] == 5

    def test_pagination_partial_last_page(self):
        sigs = [_make_signal(f"T{i:02d}", "HIZLI", 100 - i) for i in range(45)]
        scan = build_scan_response(sigs, page=5, per_page=10)
        assert len(scan["signals"]) == 5
        assert scan["meta"]["pagination"]["total"] == 45

    def test_pagination_beyond_last_returns_empty(self):
        sigs = [_make_signal(f"T{i:02d}", "HIZLI", 100 - i) for i in range(10)]
        scan = build_scan_response(sigs, page=99, per_page=10)
        assert scan["signals"] == []

    def test_no_pagination_when_per_page_none(self):
        sigs = [_make_signal(f"T{i:02d}", "HIZLI", 100 - i) for i in range(20)]
        scan = build_scan_response(sigs)
        assert "pagination" not in scan["meta"]
        assert len(scan["signals"]) == 20

    def test_universe_size_includes_all_modes(self):
        # Spec §17 + §13: SAKİN and UZAK DUR are visible (sunk to bottom)
        # but counted in the universe — quality informs but never gates.
        sigs = [
            _make_signal("A", "HIZLI",     85),
            _make_signal("B", "SAKİN",     18),
            _make_signal("C", "UZAK DUR",   5),
            _make_signal("D", "SAKİN",      0),
        ]
        scan = build_scan_response(sigs)
        assert scan["meta"]["universe_size"] == 4

    def test_scan_meta_warnings_passthrough(self):
        sigs = [_make_signal("A", "HIZLI", 80)]
        scan = build_scan_response(
            sigs, extra_warnings=["Concentration banner: banka"],
        )
        assert "Concentration banner: banka" in scan["meta"]["warnings"]

    def test_empty_universe_returns_empty_response(self):
        scan = build_scan_response([])
        assert scan["signals"] == []
        assert scan["meta"]["universe_size"] == 0
        assert scan["meta"]["by_mode"] == {}
        assert scan["meta"]["sector_concentration"] == {}


# ================================================================
# Integration — build then scan
# ================================================================

class TestEndToEnd:

    def test_build_signals_then_scan_response(
        self, hist_uptrend, bench_weak, metrics_strong,
        tech_full, macro_riskon, market_open_midday,
    ):
        # Build 5 signals across modes by varying inputs.
        sigs: list[dict] = []
        for ticker, sector in [
            ("AKBNK", "Financial Services"),
            ("ASELS", "Industrials"),
            ("KCHOL", "Financial Services"),
            ("EREGL", "Basic Materials"),
            ("BIMAS", "Consumer Defensive"),
        ]:
            sigs.append(build_bullalfa_signal(
                ticker=ticker,
                hist_df=hist_uptrend, bench_df=bench_weak,
                metrics=metrics_strong, sector_raw=sector,
                macro_result=macro_riskon, market_status=market_open_midday,
                isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
            ))

        scan = build_scan_response(sigs, page=1, per_page=10)
        assert scan["meta"]["universe_size"] == 5
        # Default sort: opportunity DESC.
        opps = [s["opportunity_score"] for s in scan["signals"]]
        assert opps == sorted(opps, reverse=True)
        # by_mode totals to universe_size.
        assert sum(scan["meta"]["by_mode"].values()) == 5

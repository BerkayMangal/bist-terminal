# ================================================================
# tests/test_bullalfa_schemas.py
#
# Pydantic v2 contract tests: every output path of
# `build_bullalfa_signal` must validate against the BullAlfaSignal
# model. This is the runtime equivalent of the spec §19 schema —
# if the orchestrator emits a shape Pydantic rejects, that's a
# contract violation.
#
# The tests cover all 6 modes plus the major degradation paths.
# ================================================================

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from api.bullalfa_schemas import (
    BullAlfaSignal,
    ScanResponse,
    TickerResponse,
    export_json_schema,
)
from engine.bullalfa import build_bullalfa_signal, build_scan_response


# ================================================================
# Fixtures — reuse the integration test rig
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
def bench() -> pd.DataFrame:
    rng = np.random.default_rng(43)
    return pd.DataFrame({"Close": np.cumsum(rng.normal(0.05, 0.20, 250)) + 100})


@pytest.fixture
def metrics_strong() -> dict:
    return {"pe": 9.5, "roe": 18.0, "net_income": 1e9,
            "revenue": 5e9, "market_cap": 5e10}


@pytest.fixture
def metrics_weak() -> dict:
    return {"pe": 35.0, "roe": 4.0, "net_income": 5e7,
            "revenue": 5e8, "market_cap": 1e9}


@pytest.fixture
def tech_full() -> dict:
    return {"atr": 1.5, "rsi": 55.0, "adx": 22.0,
            "plus_di": 25.0, "minus_di": 18.0}


@pytest.fixture
def macro_riskon() -> dict:
    return {"regime": "risk_on", "tl_vol_pct": 30.0}


@pytest.fixture
def market_open() -> dict:
    return {"status": "open", "ist_time": "12:30"}


# ================================================================
# Per-mode validation
# ================================================================

class TestSignalSchemaPerMode:

    def test_pozisyon_signal_validates(
        self, hist_uptrend, bench, metrics_strong,
        tech_full, macro_riskon, market_open,
    ):
        sig = build_bullalfa_signal(
            ticker="ASELS",
            hist_df=hist_uptrend, bench_df=bench,
            metrics=metrics_strong,
            sector_raw="Industrials",
            macro_result=macro_riskon, market_status=market_open,
            isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
        )
        assert sig["mode"] in {"POZİSYON", "SWING"}
        # The actual schema check — must not raise.
        BullAlfaSignal.model_validate(sig)

    def test_sakin_signal_validates(
        self, hist_uptrend, bench, metrics_weak,
        macro_riskon, market_open,
    ):
        # D-grade metrics → SAKİN by quality min.
        sig = build_bullalfa_signal(
            ticker="WEAK",
            hist_df=hist_uptrend, bench_df=bench,
            metrics=metrics_weak,
            sector_raw="Industrials",
            macro_result=macro_riskon, market_status=market_open,
            isotonic_fits=None, days_listed=1000,
        )
        BullAlfaSignal.model_validate(sig)

    def test_uzak_dur_signal_validates(
        self, hist_uptrend, bench, metrics_strong,
        tech_full, macro_riskon, market_open,
    ):
        sig = build_bullalfa_signal(
            ticker="HALTED",
            hist_df=hist_uptrend, bench_df=bench,
            metrics=metrics_strong, sector_raw="Industrials",
            halted_today=True,
            macro_result=macro_riskon, market_status=market_open,
            isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
        )
        assert sig["mode"] == "UZAK DUR"
        BullAlfaSignal.model_validate(sig)

    def test_newly_listed_signal_validates(
        self, hist_uptrend, bench, metrics_strong,
        tech_full, macro_riskon, market_open,
    ):
        sig = build_bullalfa_signal(
            ticker="IPO99",
            hist_df=hist_uptrend, bench_df=bench,
            metrics=metrics_strong, sector_raw="Industrials",
            macro_result=macro_riskon, market_status=market_open,
            isotonic_fits=None, tech_pre=tech_full, days_listed=60,
        )
        # newly_listed sector_group is allowed by the SectorGroupLiteral.
        BullAlfaSignal.model_validate(sig)


# ================================================================
# Per-degradation validation
# ================================================================

class TestSignalSchemaUnderDegradation:

    def test_macro_unavailable_signal_validates(
        self, hist_uptrend, bench, metrics_strong, tech_full, market_open,
    ):
        sig = build_bullalfa_signal(
            ticker="X",
            hist_df=hist_uptrend, bench_df=bench,
            metrics=metrics_strong, sector_raw="Industrials",
            macro_result=None,           # ← macro_unavailable
            market_status=market_open,
            isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
        )
        BullAlfaSignal.model_validate(sig)

    def test_aggregation_failed_signal_validates(
        self, hist_uptrend, bench, tech_full, macro_riskon, market_open,
    ):
        sig = build_bullalfa_signal(
            ticker="X",
            hist_df=hist_uptrend, bench_df=bench,
            metrics=None,                # ← aggregation_failed
            sector_raw="Industrials",
            macro_result=macro_riskon, market_status=market_open,
            isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
        )
        BullAlfaSignal.model_validate(sig)

    def test_pit_missing_signal_validates(
        self, metrics_strong, tech_full, macro_riskon, market_open,
    ):
        empty = pd.DataFrame({"Open": [], "High": [], "Low": [],
                              "Close": [], "Volume": []})
        sig = build_bullalfa_signal(
            ticker="X",
            hist_df=empty, bench_df=None,
            metrics=metrics_strong, sector_raw="Industrials",
            short_history=True,
            macro_result=macro_riskon, market_status=market_open,
            isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
        )
        BullAlfaSignal.model_validate(sig)

    def test_short_history_signal_validates(
        self, metrics_strong, tech_full, macro_riskon, market_open,
    ):
        rng = np.random.default_rng(1)
        short = pd.DataFrame({
            "Open": rng.normal(100, 1, 30), "High": rng.normal(101, 1, 30),
            "Low":  rng.normal(99, 1, 30),  "Close": rng.normal(100, 1, 30),
            "Volume": rng.integers(800_000, 1_500_000, 30),
        })
        sig = build_bullalfa_signal(
            ticker="X",
            hist_df=short, bench_df=None,
            metrics=metrics_strong, sector_raw="Industrials",
            macro_result=macro_riskon, market_status=market_open,
            isotonic_fits=None, tech_pre=tech_full, days_listed=30,
        )
        BullAlfaSignal.model_validate(sig)


# ================================================================
# Scan response validation
# ================================================================

class TestScanResponseSchema:

    def test_full_scan_validates(
        self, hist_uptrend, bench, metrics_strong, tech_full,
        macro_riskon, market_open,
    ):
        sigs = []
        for tk, sec in [
            ("AKBNK", "Financial Services"),
            ("ASELS", "Industrials"),
            ("KCHOL", "Financial Services"),
            ("EREGL", "Basic Materials"),
        ]:
            sigs.append(build_bullalfa_signal(
                ticker=tk,
                hist_df=hist_uptrend, bench_df=bench,
                metrics=metrics_strong, sector_raw=sec,
                industry_raw="Conglomerates" if tk == "KCHOL" else None,
                macro_result=macro_riskon, market_status=market_open,
                isotonic_fits=None, tech_pre=tech_full, days_listed=1000,
            ))
        scan = build_scan_response(sigs, page=1, per_page=10,
                                    extra_warnings=["test warning"])
        ScanResponse.model_validate(scan)

    def test_empty_scan_validates(self):
        scan = build_scan_response([], extra_warnings=[])
        ScanResponse.model_validate(scan)


# ================================================================
# Schema artifact
# ================================================================

class TestSchemaArtifact:

    def test_export_json_schema_returns_valid_dict(self):
        schema = export_json_schema()
        assert "$schema" in schema
        assert "$defs" in schema
        assert set(schema["$defs"].keys()) >= {
            "BullAlfaSignal", "ScanResponse", "TickerResponse",
        }

    def test_schema_is_json_serializable(self):
        schema = export_json_schema()
        # Round-trip: must serialize and deserialize cleanly.
        s = json.dumps(schema, ensure_ascii=False)
        round_trip = json.loads(s)
        assert round_trip == schema

    def test_schema_size_within_reasonable_bounds(self):
        # Spec sanity — schema shouldn't balloon to >1MB. v1.4
        # produces ~30KB; if a future change pushes past 200KB
        # something has gone wrong (e.g. circular refs flattened).
        schema = export_json_schema()
        size = len(json.dumps(schema))
        assert 5_000 < size < 200_000, f"schema size {size} outside bounds"

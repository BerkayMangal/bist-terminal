"""Phase 6 — Bank scoring scaffold tests.

Three scopes:

  1. Bank metric registry: directions, isotonic-suitable subset.
  2. Bank fits loader: routes to bank fits artifact, returns None
     when not yet committed.
  3. Dispatcher with symbol kwarg: bank symbols route to bank fits
     (if present) or fall through to general path (if not).

This is scaffold only — actual bank fits are produced by Phase 6
Colab notebook running ingest on bank-specific KAP schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ==========================================================================
# Bank metric registry
# ==========================================================================

class TestBankMetricRegistry:
    def test_directions_dict_present(self):
        from engine.scoring_calibrated_banks import BANK_METRIC_DIRECTIONS
        assert isinstance(BANK_METRIC_DIRECTIONS, dict)
        assert len(BANK_METRIC_DIRECTIONS) >= 10  # at least 10 metrics

    def test_required_bank_metrics_present(self):
        from engine.scoring_calibrated_banks import BANK_METRIC_DIRECTIONS
        required = {
            "nim", "roa_bank", "roe_bank", "cost_to_income",
            "npl_ratio", "car",
        }
        missing = required - set(BANK_METRIC_DIRECTIONS.keys())
        assert not missing, f"missing bank metrics: {missing}"

    def test_directions_are_bool_or_none(self):
        from engine.scoring_calibrated_banks import BANK_METRIC_DIRECTIONS
        for k, v in BANK_METRIC_DIRECTIONS.items():
            assert v in (True, False, None), \
                f"{k}: direction must be True/False/None, got {v!r}"

    def test_higher_is_better_metrics(self):
        """Profitability + capital adequacy should be ↑."""
        from engine.scoring_calibrated_banks import BANK_METRIC_DIRECTIONS
        for k in ["nim", "roa_bank", "roe_bank", "car", "tier1_ratio"]:
            assert BANK_METRIC_DIRECTIONS[k] is True, \
                f"{k} should be increasing"

    def test_lower_is_better_metrics(self):
        """NPL, cost ratios, valuation should be ↓."""
        from engine.scoring_calibrated_banks import BANK_METRIC_DIRECTIONS
        for k in ["npl_ratio", "cost_to_income", "pb_bank", "pe_bank"]:
            assert BANK_METRIC_DIRECTIONS[k] is False, \
                f"{k} should be decreasing"

    def test_loan_to_deposit_is_bell_shaped(self):
        """LTD ~95-105% is healthy; both extremes bad → not monotonic."""
        from engine.scoring_calibrated_banks import BANK_METRIC_DIRECTIONS
        assert BANK_METRIC_DIRECTIONS["loan_to_deposit"] is None

    def test_isotonic_subset_excludes_bell_shaped(self):
        from engine.scoring_calibrated_banks import (
            BANK_METRIC_KEYS_ISOTONIC, BANK_METRIC_DIRECTIONS,
        )
        for k in BANK_METRIC_KEYS_ISOTONIC:
            assert BANK_METRIC_DIRECTIONS[k] is not None


# ==========================================================================
# Bank fits loader
# ==========================================================================

class TestBankFitsLoader:
    def test_default_path_is_separate(self):
        """Bank fits should be in their own file, not commingled."""
        from engine.scoring_calibrated_banks import DEFAULT_BANK_FITS_PATH
        from engine.scoring_calibrated import (
            DEFAULT_FITS_PATH, DEFAULT_FITS_V2_PATH,
        )
        assert DEFAULT_BANK_FITS_PATH != DEFAULT_FITS_PATH
        assert DEFAULT_BANK_FITS_PATH != DEFAULT_FITS_V2_PATH
        assert "banks" in str(DEFAULT_BANK_FITS_PATH)

    def test_get_bank_fits_returns_none_when_missing(self, tmp_path, monkeypatch):
        from engine import scoring_calibrated_banks
        from engine import scoring_calibrated
        monkeypatch.setattr(
            scoring_calibrated_banks, "DEFAULT_BANK_FITS_PATH",
            tmp_path / "missing_banks.json",
        )
        scoring_calibrated.reset_fits_cache()
        result = scoring_calibrated_banks.get_bank_fits(force_reload=True)
        assert result is None

    def test_get_bank_fits_loads_when_present(self, tmp_path, monkeypatch):
        from engine import scoring_calibrated_banks
        from engine import scoring_calibrated

        bank_fits = {
            "nim": {
                "x_knots": [0.02, 0.03, 0.04, 0.05],
                "y_values": [-0.05, 0.0, 0.05, 0.10],
                "increasing": True, "n_samples": 270,
                "domain_min": 0.02, "domain_max": 0.05,
                "y_min": -0.05, "y_max": 0.10,
            },
            "npl_ratio": {
                "x_knots": [0.02, 0.04, 0.06, 0.08],
                "y_values": [0.05, 0.0, -0.05, -0.15],
                "increasing": False, "n_samples": 270,
                "domain_min": 0.02, "domain_max": 0.08,
                "y_min": -0.15, "y_max": 0.05,
            },
        }
        path = tmp_path / "banks.json"
        path.write_text(json.dumps(bank_fits))
        monkeypatch.setattr(
            scoring_calibrated_banks, "DEFAULT_BANK_FITS_PATH", path,
        )
        scoring_calibrated.reset_fits_cache()

        result = scoring_calibrated_banks.get_bank_fits(force_reload=True)
        assert result is not None
        assert "nim" in result
        assert "npl_ratio" in result


# ==========================================================================
# Dispatcher symbol-aware bank routing
# ==========================================================================

class TestDispatcherBankRouting:
    """When symbol is a bank and bank fits exist, route to bank fits.
    Otherwise fall through to the existing path."""

    def test_bank_symbol_uses_bank_fits_when_available(self, tmp_path, monkeypatch):
        from engine import scoring_calibrated, scoring_calibrated_banks
        from engine.scoring_calibrated import (
            score_dispatch, reset_fits_cache, CALIBRATED_VERSION,
        )

        # Set up bank fits with metrics that score_value/quality look at
        # (the calibrated_2026Q1_banks dispatch reuses
        # score_value_calibrated etc. which look at general-metric keys
        # like roe, pe). For scaffold testing we just need ANY fit
        # present so the dispatcher takes the bank path.
        bank_fits = {
            "roe": {
                "x_knots": [0.10, 0.20], "y_values": [-0.05, 0.10],
                "increasing": True, "n_samples": 100,
                "domain_min": 0.10, "domain_max": 0.20,
                "y_min": -0.05, "y_max": 0.10,
            }
        }
        bank_path = tmp_path / "banks.json"
        bank_path.write_text(json.dumps(bank_fits))
        monkeypatch.setattr(
            scoring_calibrated_banks, "DEFAULT_BANK_FITS_PATH", bank_path,
        )
        reset_fits_cache()

        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch(m, scoring_version=CALIBRATED_VERSION,
                           symbol="AKBNK")
        assert r["scoring_version_effective"] == "calibrated_2026Q1_banks"

    def test_bank_symbol_falls_through_when_no_bank_fits(self, tmp_path, monkeypatch):
        """No bank fits → fall through to general calibrated path
        (which itself falls back to V13 if its fits also missing)."""
        from engine import scoring_calibrated, scoring_calibrated_banks
        from engine.scoring_calibrated import (
            score_dispatch, reset_fits_cache,
            CALIBRATED_VERSION, HANDPICKED_VERSION,
        )

        # No bank fits, no Q1 fits → V13
        monkeypatch.setattr(
            scoring_calibrated_banks, "DEFAULT_BANK_FITS_PATH",
            tmp_path / "no_banks.json",
        )
        monkeypatch.setattr(
            scoring_calibrated, "DEFAULT_FITS_PATH",
            tmp_path / "no_q1.json",
        )
        reset_fits_cache()

        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch(m, scoring_version=CALIBRATED_VERSION,
                           symbol="AKBNK")
        assert r["scoring_version"] == CALIBRATED_VERSION
        assert r["scoring_version_effective"] == HANDPICKED_VERSION

    def test_non_bank_symbol_unaffected(self, tmp_path, monkeypatch):
        """For non-bank symbols, dispatcher behaves exactly as before
        (Phase 6 must not regress general path)."""
        from engine import scoring_calibrated, scoring_calibrated_banks
        from engine.scoring_calibrated import (
            score_dispatch, reset_fits_cache,
            CALIBRATED_VERSION, HANDPICKED_VERSION,
        )

        # Even if bank fits exist, non-bank symbol uses general path
        bank_fits = {
            "roe": {"x_knots": [0.1, 0.2], "y_values": [-0.05, 0.10],
                    "increasing": True, "n_samples": 100,
                    "domain_min": 0.1, "domain_max": 0.2,
                    "y_min": -0.05, "y_max": 0.10}
        }
        bank_path = tmp_path / "banks.json"
        bank_path.write_text(json.dumps(bank_fits))
        monkeypatch.setattr(
            scoring_calibrated_banks, "DEFAULT_BANK_FITS_PATH", bank_path,
        )
        # No Q1 fits → falls back to V13 for THYAO
        monkeypatch.setattr(
            scoring_calibrated, "DEFAULT_FITS_PATH",
            tmp_path / "no_q1.json",
        )
        reset_fits_cache()

        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch(m, scoring_version=CALIBRATED_VERSION,
                           symbol="THYAO")  # not a bank
        assert r["scoring_version_effective"] == HANDPICKED_VERSION
        # NOT 'calibrated_2026Q1_banks'
        assert r["scoring_version_effective"] != "calibrated_2026Q1_banks"

    def test_no_symbol_kwarg_legacy_behaviour(self, tmp_path, monkeypatch):
        """When symbol is not passed (legacy callers), bank routing
        is bypassed entirely."""
        from engine import scoring_calibrated, scoring_calibrated_banks
        from engine.scoring_calibrated import (
            score_dispatch, reset_fits_cache,
            CALIBRATED_VERSION, HANDPICKED_VERSION,
        )

        monkeypatch.setattr(
            scoring_calibrated, "DEFAULT_FITS_PATH",
            tmp_path / "no_q1.json",
        )
        reset_fits_cache()

        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        # No symbol kwarg
        r = score_dispatch(m, scoring_version=CALIBRATED_VERSION)
        assert r["scoring_version_effective"] == HANDPICKED_VERSION


# ==========================================================================
# Helper: is_bank_metrics_dict
# ==========================================================================

class TestIsBankMetricsDict:
    def test_recognizes_bank_metrics(self):
        from engine.scoring_calibrated_banks import is_bank_metrics_dict
        m = {"nim": 0.04, "npl_ratio": 0.03, "car": 0.18}
        assert is_bank_metrics_dict(m) is True

    def test_rejects_general_metrics(self):
        from engine.scoring_calibrated_banks import is_bank_metrics_dict
        m = {"roe": 0.15, "pe": 10.0, "pb": 1.5}
        assert is_bank_metrics_dict(m) is False

    def test_partial_match(self):
        """Even one bank metric is enough."""
        from engine.scoring_calibrated_banks import is_bank_metrics_dict
        m = {"roe": 0.15, "nim": 0.04}  # mixed
        assert is_bank_metrics_dict(m) is True

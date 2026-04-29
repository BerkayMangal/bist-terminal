"""Phase 8 — Multi-horizon scaffold tests.

Verify horizon constants, path resolution, normalization, and
the score_dispatch_with_horizon wrapper.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ==========================================================================
# Horizon constants
# ==========================================================================

class TestHorizonConstants:
    def test_three_horizons(self):
        from engine.scoring_calibrated_horizons import SUPPORTED_HORIZONS
        assert SUPPORTED_HORIZONS == frozenset({"20d", "60d", "250d"})

    def test_default_is_60d(self):
        from engine.scoring_calibrated_horizons import HORIZON_DEFAULT
        assert HORIZON_DEFAULT == "60d"

    def test_short_is_20d(self):
        from engine.scoring_calibrated_horizons import HORIZON_SHORT
        assert HORIZON_SHORT == "20d"

    def test_long_is_250d(self):
        from engine.scoring_calibrated_horizons import HORIZON_LONG
        assert HORIZON_LONG == "250d"


# ==========================================================================
# Path resolution
# ==========================================================================

class TestHorizonFitsPaths:
    def test_20d_path(self):
        from engine.scoring_calibrated_horizons import resolve_horizon_fits_path
        path = resolve_horizon_fits_path("20d")
        assert path is not None
        assert "20d" in str(path)
        assert path.name == "fa_isotonic_fits_20d.json"

    def test_60d_path_is_default(self):
        from engine.scoring_calibrated_horizons import resolve_horizon_fits_path
        from engine.scoring_calibrated import DEFAULT_FITS_PATH
        # 60d should resolve to the same path as DEFAULT_FITS_PATH
        path = resolve_horizon_fits_path("60d")
        assert path == DEFAULT_FITS_PATH

    def test_250d_path(self):
        from engine.scoring_calibrated_horizons import resolve_horizon_fits_path
        path = resolve_horizon_fits_path("250d")
        assert path is not None
        assert "250d" in str(path)

    def test_unknown_horizon_returns_none(self):
        from engine.scoring_calibrated_horizons import resolve_horizon_fits_path
        assert resolve_horizon_fits_path("nonsense") is None
        assert resolve_horizon_fits_path("100d") is None


# ==========================================================================
# Normalization
# ==========================================================================

class TestNormalizeHorizon:
    def test_none_returns_default(self):
        from engine.scoring_calibrated_horizons import (
            normalize_horizon, HORIZON_DEFAULT,
        )
        assert normalize_horizon(None) == HORIZON_DEFAULT

    def test_empty_returns_default(self):
        from engine.scoring_calibrated_horizons import (
            normalize_horizon, HORIZON_DEFAULT,
        )
        assert normalize_horizon("") == HORIZON_DEFAULT

    def test_supported_passthrough(self):
        from engine.scoring_calibrated_horizons import normalize_horizon
        assert normalize_horizon("20d") == "20d"
        assert normalize_horizon("60d") == "60d"
        assert normalize_horizon("250d") == "250d"

    def test_case_normalized(self):
        from engine.scoring_calibrated_horizons import normalize_horizon
        assert normalize_horizon("20D") == "20d"
        assert normalize_horizon("60D") == "60d"

    def test_whitespace_stripped(self):
        from engine.scoring_calibrated_horizons import normalize_horizon
        assert normalize_horizon("  20d  ") == "20d"

    def test_unknown_falls_back_to_default(self):
        from engine.scoring_calibrated_horizons import (
            normalize_horizon, HORIZON_DEFAULT,
        )
        assert normalize_horizon("100d") == HORIZON_DEFAULT
        assert normalize_horizon("xyz") == HORIZON_DEFAULT


# ==========================================================================
# Target key generator
# ==========================================================================

class TestHorizonTargetKey:
    def test_20d_key(self):
        from engine.scoring_calibrated_horizons import horizon_target_key
        assert horizon_target_key("20d") == "forward_return_20d"

    def test_60d_key(self):
        from engine.scoring_calibrated_horizons import horizon_target_key
        assert horizon_target_key("60d") == "forward_return_60d"

    def test_250d_key(self):
        from engine.scoring_calibrated_horizons import horizon_target_key
        assert horizon_target_key("250d") == "forward_return_250d"

    def test_none_uses_default(self):
        from engine.scoring_calibrated_horizons import horizon_target_key
        assert horizon_target_key(None) == "forward_return_60d"

    def test_unknown_uses_default(self):
        from engine.scoring_calibrated_horizons import horizon_target_key
        assert horizon_target_key("100d") == "forward_return_60d"


# ==========================================================================
# Fits loader
# ==========================================================================

class TestGetHorizonFits:
    def test_60d_loads_default_fits(self):
        """60d should load whatever's in reports/fa_isotonic_fits.json
        (the existing Phase 4.7 fits)."""
        from engine.scoring_calibrated_horizons import get_horizon_fits
        from engine.scoring_calibrated import reset_fits_cache
        reset_fits_cache()
        fits = get_horizon_fits("60d")
        # Real fits in repo → not None
        assert fits is not None
        assert len(fits) >= 5

    def test_20d_returns_none_when_artifact_missing(self):
        """20d fits not yet generated → returns None."""
        from engine.scoring_calibrated_horizons import get_horizon_fits
        from engine.scoring_calibrated import reset_fits_cache
        reset_fits_cache()
        fits = get_horizon_fits("20d")
        # Phase 8 deploy not done → no artifact → None
        assert fits is None

    def test_250d_returns_none_when_artifact_missing(self):
        from engine.scoring_calibrated_horizons import get_horizon_fits
        from engine.scoring_calibrated import reset_fits_cache
        reset_fits_cache()
        fits = get_horizon_fits("250d")
        assert fits is None


# ==========================================================================
# Multi-horizon dispatcher wrapper
# ==========================================================================

class TestScoreDispatchWithHorizon:
    def test_default_horizon_uses_60d_fits(self):
        from engine.scoring_calibrated_horizons import score_dispatch_with_horizon
        from engine.scoring_calibrated import (
            CALIBRATED_VERSION, reset_fits_cache,
        )
        reset_fits_cache()
        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch_with_horizon(
            m, scoring_version=CALIBRATED_VERSION, horizon="60d",
        )
        assert r["horizon"] == "60d"
        assert r["scoring_version"] == CALIBRATED_VERSION
        # 60d fits exist in repo → effective should be CALIBRATED
        assert r["scoring_version_effective"] == CALIBRATED_VERSION

    def test_20d_falls_back_to_60d_when_artifact_missing(self):
        """20d fits not yet generated → falls back to 60d (which
        has real fits in the repo)."""
        from engine.scoring_calibrated_horizons import score_dispatch_with_horizon
        from engine.scoring_calibrated import (
            CALIBRATED_VERSION, reset_fits_cache,
        )
        reset_fits_cache()
        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch_with_horizon(
            m, scoring_version=CALIBRATED_VERSION, horizon="20d",
        )
        # Reported horizon should be 60d (the fallback)
        assert r["horizon"] == "60d"

    def test_horizon_field_in_output(self):
        """All routes should annotate horizon for telemetry."""
        from engine.scoring_calibrated_horizons import score_dispatch_with_horizon
        from engine.scoring_calibrated import HANDPICKED_VERSION
        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch_with_horizon(
            m, scoring_version=HANDPICKED_VERSION, horizon="60d",
        )
        assert "horizon" in r

    def test_no_horizon_kwarg_uses_default(self):
        from engine.scoring_calibrated_horizons import score_dispatch_with_horizon
        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch_with_horizon(m)
        assert r["horizon"] == "60d"

    def test_synthetic_20d_fits_loaded(self, tmp_path, monkeypatch):
        """If we synthesize 20d fits, the dispatcher uses them."""
        from engine import scoring_calibrated_horizons
        from engine.scoring_calibrated_horizons import score_dispatch_with_horizon
        from engine.scoring_calibrated import (
            CALIBRATED_VERSION, reset_fits_cache,
        )

        synthetic = {
            "roe": {
                "x_knots": [0.10, 0.20, 0.30],
                "y_values": [-0.02, 0.05, 0.10],
                "increasing": True, "n_samples": 200,
                "domain_min": 0.10, "domain_max": 0.30,
                "y_min": -0.02, "y_max": 0.10,
            },
            "pe": {
                "x_knots": [5.0, 10.0, 20.0],
                "y_values": [0.10, 0.0, -0.10],
                "increasing": False, "n_samples": 200,
                "domain_min": 5.0, "domain_max": 20.0,
                "y_min": -0.10, "y_max": 0.10,
            },
        }
        path_20d = tmp_path / "fa_isotonic_fits_20d.json"
        path_20d.write_text(json.dumps(synthetic))

        # Patch the path lookup
        new_paths = dict(scoring_calibrated_horizons.HORIZON_FITS_PATHS)
        new_paths["20d"] = path_20d
        monkeypatch.setattr(
            scoring_calibrated_horizons, "HORIZON_FITS_PATHS", new_paths,
        )
        reset_fits_cache()

        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch_with_horizon(
            m, scoring_version=CALIBRATED_VERSION, horizon="20d",
        )
        assert r["horizon"] == "20d"
        assert r["scoring_version_effective"] == CALIBRATED_VERSION

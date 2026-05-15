"""Phase 9 — Sector-conditional fits scaffold tests."""

from __future__ import annotations

import json

import pytest


# ==========================================================================
# Sector constants
# ==========================================================================

class TestSupportedSectors:
    def test_includes_8_known_sectors(self):
        from engine.scoring_calibrated_sectors import SUPPORTED_SECTORS
        for s in ["gayrimenkul", "enerji", "ulasim", "sanayi",
                  "savunma", "holding", "perakende", "banka"]:
            assert s in SUPPORTED_SECTORS

    def test_size(self):
        from engine.scoring_calibrated_sectors import SUPPORTED_SECTORS
        # Currently 8; future Phase 9 deploy may add 'teknoloji'
        assert 8 <= len(SUPPORTED_SECTORS) <= 10


# ==========================================================================
# Path resolution
# ==========================================================================

class TestSectorFitsPath:
    def test_known_sector_path(self):
        from engine.scoring_calibrated_sectors import resolve_sector_fits_path
        path = resolve_sector_fits_path("holding")
        assert path is not None
        assert "holding" in str(path)
        assert path.name.endswith(".json")

    def test_path_pattern(self):
        from engine.scoring_calibrated_sectors import resolve_sector_fits_path
        path = resolve_sector_fits_path("perakende")
        assert path.name == "fa_isotonic_fits_sector_perakende.json"

    def test_unknown_sector_returns_none(self):
        from engine.scoring_calibrated_sectors import resolve_sector_fits_path
        assert resolve_sector_fits_path("nonsense") is None
        assert resolve_sector_fits_path("teknoloji") is None  # not yet supported

    def test_none_returns_none(self):
        from engine.scoring_calibrated_sectors import resolve_sector_fits_path
        assert resolve_sector_fits_path(None) is None
        assert resolve_sector_fits_path("") is None

    def test_case_insensitive(self):
        from engine.scoring_calibrated_sectors import resolve_sector_fits_path
        path1 = resolve_sector_fits_path("HOLDING")
        path2 = resolve_sector_fits_path("holding")
        assert path1 == path2


# ==========================================================================
# Normalize sector
# ==========================================================================

class TestNormalizeSector:
    def test_known_returns_canonical(self):
        from engine.scoring_calibrated_sectors import normalize_sector
        assert normalize_sector("holding") == "holding"
        assert normalize_sector("PERAKENDE") == "perakende"
        assert normalize_sector("  enerji  ") == "enerji"

    def test_unknown_returns_none(self):
        from engine.scoring_calibrated_sectors import normalize_sector
        assert normalize_sector("nonsense") is None
        assert normalize_sector("teknoloji") is None

    def test_none_returns_none(self):
        from engine.scoring_calibrated_sectors import normalize_sector
        assert normalize_sector(None) is None
        assert normalize_sector("") is None


# ==========================================================================
# Get sector fits loader
# ==========================================================================

class TestGetSectorFits:
    def test_returns_none_when_artifact_missing(self):
        """No sector fits committed yet → None."""
        from engine.scoring_calibrated_sectors import get_sector_fits
        from engine.scoring_calibrated import reset_fits_cache
        reset_fits_cache()
        assert get_sector_fits("holding") is None
        assert get_sector_fits("perakende") is None

    def test_unknown_sector_returns_none(self):
        from engine.scoring_calibrated_sectors import get_sector_fits
        assert get_sector_fits("nonexistent") is None

    def test_loads_synthetic_artifact(self, tmp_path, monkeypatch):
        """If we plant a fits file in the expected location, it loads."""
        from engine import scoring_calibrated_sectors
        from engine.scoring_calibrated import reset_fits_cache

        synth = {
            "roe": {"x_knots": [0.10, 0.20], "y_values": [-0.05, 0.10],
                    "increasing": True, "n_samples": 100,
                    "domain_min": 0.10, "domain_max": 0.20,
                    "y_min": -0.05, "y_max": 0.10}
        }
        # Patch _REPORTS_DIR so the loader looks in tmp_path
        monkeypatch.setattr(
            scoring_calibrated_sectors, "_REPORTS_DIR", tmp_path,
        )
        path = tmp_path / "fa_isotonic_fits_sector_holding.json"
        path.write_text(json.dumps(synth))

        reset_fits_cache()
        result = scoring_calibrated_sectors.get_sector_fits("holding")
        assert result is not None
        assert "roe" in result


# ==========================================================================
# Sector-aware dispatcher
# ==========================================================================

class TestSectorAwareDispatcher:
    def test_no_sector_falls_back_to_general(self, tmp_path, monkeypatch):
        """No sector_group → falls through to general dispatcher."""
        from engine import scoring_calibrated, scoring_calibrated_sectors
        from engine.scoring_calibrated_sectors import score_dispatch_sector_aware
        from engine.scoring_calibrated import (
            CALIBRATED_VERSION, reset_fits_cache,
        )

        # Make general path also missing → V13 fallback
        monkeypatch.setattr(
            scoring_calibrated, "DEFAULT_FITS_PATH",
            tmp_path / "no_general.json",
        )
        reset_fits_cache()

        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch_sector_aware(
            m, scoring_version=CALIBRATED_VERSION,
        )
        assert r["sector_fits"] is False  # no sector route taken

    def test_unknown_sector_falls_back_to_general(self, tmp_path, monkeypatch):
        from engine.scoring_calibrated_sectors import score_dispatch_sector_aware
        from engine.scoring_calibrated import (
            CALIBRATED_VERSION, reset_fits_cache,
        )
        reset_fits_cache()

        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch_sector_aware(
            m, sector_group="weirdmadeup",
            scoring_version=CALIBRATED_VERSION,
        )
        assert r["sector_fits"] is False

    def test_holding_uses_holding_fits(self, tmp_path, monkeypatch):
        """When holding fits exist, holding sector_group routes to them."""
        from engine import scoring_calibrated, scoring_calibrated_sectors
        from engine.scoring_calibrated_sectors import score_dispatch_sector_aware
        from engine.scoring_calibrated import (
            CALIBRATED_VERSION, reset_fits_cache,
        )

        synth = {
            "roe": {"x_knots": [0.10, 0.20, 0.30],
                    "y_values": [-0.02, 0.05, 0.15],
                    "increasing": True, "n_samples": 200,
                    "domain_min": 0.10, "domain_max": 0.30,
                    "y_min": -0.02, "y_max": 0.15},
        }
        monkeypatch.setattr(
            scoring_calibrated_sectors, "_REPORTS_DIR", tmp_path,
        )
        path = tmp_path / "fa_isotonic_fits_sector_holding.json"
        path.write_text(json.dumps(synth))
        reset_fits_cache()

        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch_sector_aware(
            m, sector_group="holding",
            scoring_version=CALIBRATED_VERSION,
        )
        assert r["sector_fits"] is True
        assert "holding" in r["scoring_version_effective"]

    def test_explicit_fits_bypasses_sector_routing(self, tmp_path, monkeypatch):
        """When caller passes fits=..., respect that; don't autoload sector."""
        from engine import scoring_calibrated, scoring_calibrated_sectors
        from engine.scoring_calibrated_sectors import score_dispatch_sector_aware
        from engine.scoring_calibrated import (
            CALIBRATED_VERSION, reset_fits_cache,
        )

        # Plant holding fits — they should NOT be used because caller
        # passes explicit fits
        synth_h = {
            "roe": {"x_knots": [0.0, 0.5], "y_values": [0.0, 1.0],
                    "increasing": True, "n_samples": 100,
                    "domain_min": 0.0, "domain_max": 0.5,
                    "y_min": 0.0, "y_max": 1.0},
        }
        monkeypatch.setattr(
            scoring_calibrated_sectors, "_REPORTS_DIR", tmp_path,
        )
        (tmp_path / "fa_isotonic_fits_sector_holding.json").write_text(
            json.dumps(synth_h)
        )
        reset_fits_cache()

        explicit = {
            "roe": {"x_knots": [0.0, 0.4], "y_values": [-0.5, 0.5],
                    "increasing": True, "n_samples": 50,
                    "domain_min": 0.0, "domain_max": 0.4,
                    "y_min": -0.5, "y_max": 0.5},
        }
        # Need to load the IsotonicFit objects
        from research.isotonic import load_isotonic_fits_json
        explicit_path = tmp_path / "explicit.json"
        explicit_path.write_text(json.dumps(explicit))
        explicit_loaded = load_isotonic_fits_json(explicit_path)

        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch_sector_aware(
            m, sector_group="holding",
            scoring_version=CALIBRATED_VERSION,
            fits=explicit_loaded,
        )
        assert r["sector_fits"] is False  # explicit fits used, not sector

    def test_v13_path_marks_sector_fits_false(self):
        from engine.scoring_calibrated_sectors import score_dispatch_sector_aware
        from engine.scoring_calibrated import HANDPICKED_VERSION
        m = {"roe": 0.18, "pe": 8.0, "pb": 1.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.10, "net_margin": 0.20}
        r = score_dispatch_sector_aware(
            m, sector_group="holding",
            scoring_version=HANDPICKED_VERSION,
        )
        assert r["sector_fits"] is False


# ==========================================================================
# Diagnostic
# ==========================================================================

class TestGetCalibratedSectors:
    def test_empty_when_no_artifacts(self):
        from engine.scoring_calibrated_sectors import get_calibrated_sectors
        # Real repo has no per-sector fits committed yet
        result = get_calibrated_sectors()
        # Will be empty in a fresh repo; might be non-empty if Phase 9
        # deploy has run. Either way, should be a list.
        assert isinstance(result, list)

    def test_picks_up_synthetic_artifact(self, tmp_path, monkeypatch):
        from engine import scoring_calibrated_sectors

        monkeypatch.setattr(
            scoring_calibrated_sectors, "_REPORTS_DIR", tmp_path,
        )
        # Plant 2 sector fits files
        (tmp_path / "fa_isotonic_fits_sector_holding.json").write_text("{}")
        (tmp_path / "fa_isotonic_fits_sector_perakende.json").write_text("{}")

        result = scoring_calibrated_sectors.get_calibrated_sectors()
        assert "holding" in result
        assert "perakende" in result
        assert "enerji" not in result

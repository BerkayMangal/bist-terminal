"""Phase 5 — BIST30 non-bank recalibration scaffold tests.

Two scopes:

  1. Universe: UNIVERSE_BIST30_NON_BANK is computed correctly
     (30 - 9 = 21), is_bank() works, banks excluded properly.

  2. Versioned dispatcher: calibrated_2026Q2 routes to v2 fits
     file, calibrated_2026Q1 still uses original fits, both
     coexist independently.

This is scaffold only — actual Phase 5 fits are produced later
by re-running the Colab backfill on the broader symbol set. This
turn just lays the routing infrastructure.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest


# ==========================================================================
# Universe definitions
# ==========================================================================

class TestPhase5Universe:
    def test_bist30_non_bank_count(self):
        from config import UNIVERSE_BIST30_NON_BANK
        # 30 - 4 (banks in BIST30) = 26
        assert len(UNIVERSE_BIST30_NON_BANK) == 26

    def test_bist30_non_bank_excludes_banks(self):
        from config import UNIVERSE_BIST30_NON_BANK, BIST_BANKS
        for sym in BIST_BANKS:
            assert sym not in UNIVERSE_BIST30_NON_BANK

    def test_bist30_non_bank_subset_of_bist30(self):
        from config import UNIVERSE_BIST30, UNIVERSE_BIST30_NON_BANK
        assert set(UNIVERSE_BIST30_NON_BANK).issubset(set(UNIVERSE_BIST30))

    def test_bist_banks_set_size(self):
        # 9 BIST banks: 4 BIST30 + 5 in extra
        # AKBNK GARAN ISCTR YKBNK (BIST30) + HALKB VAKBN TSKB SKBNK ALBRK (extra)
        from config import BIST_BANKS
        assert len(BIST_BANKS) == 9
        for required in {"AKBNK", "GARAN", "ISCTR", "YKBNK", "HALKB",
                          "VAKBN", "TSKB", "SKBNK", "ALBRK"}:
            assert required in BIST_BANKS

    def test_is_bank_helper(self):
        from config import is_bank
        # Banks
        assert is_bank("AKBNK") is True
        assert is_bank("akbnk") is True  # case insensitive
        assert is_bank("GARAN") is True
        # Non-banks
        assert is_bank("THYAO") is False
        assert is_bank("ASELS") is False
        assert is_bank("KCHOL") is False  # holding, not bank
        # Edge cases
        assert is_bank("") is False
        assert is_bank(None) is False  # type: ignore
        assert is_bank("UNKNOWN") is False

    def test_specific_bist30_non_bank_examples(self):
        """Sanity check on which symbols ended up in the Phase 5 set."""
        from config import UNIVERSE_BIST30_NON_BANK
        expected_nonbanks = {
            "ASELS", "THYAO", "BIMAS", "KCHOL", "SISE", "EREGL",
            "TUPRS", "SAHOL", "MGROS", "FROTO", "TOASO", "TCELL",
            "KRDMD", "PETKM", "ENKAI", "TAVHL", "PGSUS", "EKGYO",
            "ARCLK", "TTKOM", "SOKM", "TKFEN", "KONTR", "AKSEN",
            "HEKTS", "SASA",
        }
        # Phase 5 set ⊆ expected nonbanks
        for sym in UNIVERSE_BIST30_NON_BANK:
            assert sym in expected_nonbanks, f"{sym} unexpected in non-bank set"


# ==========================================================================
# Versioned fits dispatcher
# ==========================================================================

class TestVersionedFitsDispatcher:
    """Phase 5 adds calibrated_2026Q2 as a second version. Both must
    coexist and route to their own fits artifact."""

    def test_supported_versions_constant(self):
        from engine.scoring_calibrated import (
            SUPPORTED_CALIBRATED_VERSIONS,
            CALIBRATED_VERSION, CALIBRATED_V2_VERSION,
        )
        assert CALIBRATED_VERSION in SUPPORTED_CALIBRATED_VERSIONS
        assert CALIBRATED_V2_VERSION in SUPPORTED_CALIBRATED_VERSIONS
        assert CALIBRATED_V2_VERSION == "calibrated_2026Q2"

    def test_resolve_fits_path_q1(self):
        from engine.scoring_calibrated import (
            _resolve_fits_path, CALIBRATED_VERSION, DEFAULT_FITS_PATH,
        )
        assert _resolve_fits_path(CALIBRATED_VERSION) == DEFAULT_FITS_PATH

    def test_resolve_fits_path_q2(self):
        from engine.scoring_calibrated import (
            _resolve_fits_path, CALIBRATED_V2_VERSION, DEFAULT_FITS_V2_PATH,
        )
        assert _resolve_fits_path(CALIBRATED_V2_VERSION) == DEFAULT_FITS_V2_PATH

    def test_resolve_fits_path_unknown_falls_back_to_q1(self):
        """Defensive: unknown versions fall back to Q1 path (which
        the dispatcher handles further by checking SUPPORTED_*)."""
        from engine.scoring_calibrated import (
            _resolve_fits_path, DEFAULT_FITS_PATH,
        )
        assert _resolve_fits_path("unknown_version") == DEFAULT_FITS_PATH

    def test_q2_version_routes_to_v2_path(self, tmp_path, monkeypatch):
        """When calibrated_2026Q2 is requested, _get_fits should look
        at DEFAULT_FITS_V2_PATH, not the Q1 path."""
        from engine import scoring_calibrated
        from engine.scoring_calibrated import (
            _get_fits, reset_fits_cache,
            CALIBRATED_V2_VERSION, DEFAULT_FITS_PATH,
        )

        # Q2 path doesn't exist → returns None
        monkeypatch.setattr(scoring_calibrated, "DEFAULT_FITS_V2_PATH",
                            tmp_path / "no_v2.json")
        # But Q1 path *does* exist (real fits in repo)
        # We're testing that Q2 doesn't accidentally load Q1 fits

        reset_fits_cache()
        result = _get_fits(scoring_version=CALIBRATED_V2_VERSION)
        assert result is None  # v2 file doesn't exist

    def test_q1_q2_caches_independent(self, tmp_path, monkeypatch):
        """Loading Q2 should not pollute the Q1 cache and vice versa."""
        from engine import scoring_calibrated
        from engine.scoring_calibrated import (
            _get_fits, reset_fits_cache,
            CALIBRATED_VERSION, CALIBRATED_V2_VERSION,
        )

        # Make Q1 path nonexistent, Q2 path provide synthetic fits
        v2_fits = {
            "roe": {
                "x_knots": [0.1, 0.2, 0.3],
                "y_values": [-0.05, 0.0, 0.10],
                "increasing": True,
                "n_samples": 200, "domain_min": 0.1, "domain_max": 0.3,
                "y_min": -0.05, "y_max": 0.10,
            }
        }
        v2_path = tmp_path / "v2.json"
        v2_path.write_text(json.dumps(v2_fits))
        monkeypatch.setattr(scoring_calibrated, "DEFAULT_FITS_V2_PATH", v2_path)
        monkeypatch.setattr(scoring_calibrated, "DEFAULT_FITS_PATH",
                            tmp_path / "no_q1.json")

        reset_fits_cache()

        q1 = _get_fits(scoring_version=CALIBRATED_VERSION)
        q2 = _get_fits(scoring_version=CALIBRATED_V2_VERSION)

        assert q1 is None
        assert q2 is not None
        assert "roe" in q2

    def test_dispatch_with_q2_uses_q2_fits(self, tmp_path, monkeypatch):
        """score_dispatch with calibrated_2026Q2 should load Q2 fits."""
        from engine import scoring_calibrated
        from engine.scoring_calibrated import (
            score_dispatch, reset_fits_cache,
            CALIBRATED_V2_VERSION,
        )

        v2_fits = {
            "roe": {
                "x_knots": [0.05, 0.15, 0.25],
                "y_values": [-0.02, 0.05, 0.20],
                "increasing": True,
                "n_samples": 600, "domain_min": 0.05, "domain_max": 0.25,
                "y_min": -0.02, "y_max": 0.20,
            },
            "pe": {
                "x_knots": [5.0, 10.0, 20.0, 30.0],
                "y_values": [0.15, 0.05, -0.05, -0.15],
                "increasing": False,
                "n_samples": 600, "domain_min": 5.0, "domain_max": 30.0,
                "y_min": -0.15, "y_max": 0.15,
            },
        }
        v2_path = tmp_path / "v2.json"
        v2_path.write_text(json.dumps(v2_fits))
        monkeypatch.setattr(scoring_calibrated, "DEFAULT_FITS_V2_PATH", v2_path)
        reset_fits_cache()

        m = {"roe": 0.20, "pe": 8.0, "pb": 1.5, "market_cap": 1000,
             "total_debt": 100, "cash": 50, "revenue": 500,
             "fcf_yield": 0.05, "margin_safety": 0.25, "ev_ebitda": 5.0,
             "net_margin": 0.10, "roic": 0.15}
        r = score_dispatch(m, scoring_version=CALIBRATED_V2_VERSION)
        assert r["scoring_version"] == CALIBRATED_V2_VERSION
        assert r["scoring_version_effective"] == CALIBRATED_V2_VERSION

    def test_dispatch_q2_falls_back_to_v13_when_no_fits(self, tmp_path, monkeypatch):
        """No Q2 fits on disk → fall back to V13 handpicked."""
        from engine import scoring_calibrated
        from engine.scoring_calibrated import (
            score_dispatch, reset_fits_cache,
            CALIBRATED_V2_VERSION, HANDPICKED_VERSION,
        )

        monkeypatch.setattr(scoring_calibrated, "DEFAULT_FITS_V2_PATH",
                            tmp_path / "missing_v2.json")
        reset_fits_cache()

        m = {"pe": 10.0, "roe": 0.15, "market_cap": 5000, "total_debt": 100,
             "cash": 50, "revenue": 500, "pb": 1.5, "ev_ebitda": 5.0,
             "fcf_yield": 0.05, "margin_safety": 0.25, "roic": 0.18,
             "net_margin": 0.10}
        r = score_dispatch(m, "teknoloji", CALIBRATED_V2_VERSION)
        assert r["scoring_version"] == CALIBRATED_V2_VERSION
        assert r["scoring_version_effective"] == HANDPICKED_VERSION

    def test_existing_q1_path_unchanged(self, tmp_path, monkeypatch):
        """Sanity: Phase 5 changes don't break Phase 4.7 default path."""
        from engine import scoring_calibrated
        from engine.scoring_calibrated import (
            score_dispatch, reset_fits_cache,
            CALIBRATED_VERSION, HANDPICKED_VERSION,
        )

        # Force Q1 path to nonexistent (real fits exist in repo, but we
        # want fallback path test)
        monkeypatch.setattr(scoring_calibrated, "DEFAULT_FITS_PATH",
                            tmp_path / "missing_q1.json")
        reset_fits_cache()

        m = {"pe": 10.0, "roe": 0.15, "market_cap": 5000, "total_debt": 100,
             "cash": 50, "revenue": 500, "pb": 1.5, "ev_ebitda": 5.0,
             "fcf_yield": 0.05, "margin_safety": 0.25, "roic": 0.18,
             "net_margin": 0.10}
        r = score_dispatch(m, "teknoloji", CALIBRATED_VERSION)
        assert r["scoring_version"] == CALIBRATED_VERSION
        assert r["scoring_version_effective"] == HANDPICKED_VERSION


# ==========================================================================
# Reset behaviour
# ==========================================================================

class TestResetFitsCache:
    def test_reset_clears_all_versions(self, tmp_path, monkeypatch):
        from engine import scoring_calibrated
        from engine.scoring_calibrated import (
            _get_fits, reset_fits_cache,
            CALIBRATED_VERSION, CALIBRATED_V2_VERSION,
        )

        # Set up both paths to real-ish fits
        good = {"roe": {"x_knots": [0.1, 0.2], "y_values": [-0.05, 0.10],
                        "increasing": True, "n_samples": 100,
                        "domain_min": 0.1, "domain_max": 0.2,
                        "y_min": -0.05, "y_max": 0.10}}
        q1_path = tmp_path / "q1.json"
        q2_path = tmp_path / "q2.json"
        q1_path.write_text(json.dumps(good))
        q2_path.write_text(json.dumps(good))
        monkeypatch.setattr(scoring_calibrated, "DEFAULT_FITS_PATH", q1_path)
        monkeypatch.setattr(scoring_calibrated, "DEFAULT_FITS_V2_PATH", q2_path)

        reset_fits_cache()
        # Load both
        q1 = _get_fits(scoring_version=CALIBRATED_VERSION)
        q2 = _get_fits(scoring_version=CALIBRATED_V2_VERSION)
        assert q1 is not None and q2 is not None

        # Clear cache
        reset_fits_cache()
        # Cache should be empty
        assert len(scoring_calibrated._FITS_CACHE) == 0

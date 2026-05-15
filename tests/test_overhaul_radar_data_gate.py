# ================================================================
# tests/test_overhaul_radar_data_gate.py
#
# Radar Overhaul follow-up (2026-05): data-sufficiency gate.
#
# After the universe expanded to the full BIST board (~622), ~130
# thin-data small caps collapsed to overall=1 and piled up at the
# bottom of the radar as misleading "UZAK DUR" rows. The real message
# for those stocks is "veri yetersiz", not "bad company".
#
# Fix: a stock only enters the radar ranking if at least
# RADAR_MIN_DIMENSIONS (4) of the 7 FA dimensions have real data.
# Thin-data stocks are dropped from the list (still searchable via
# /api/analyze). Genuinely-bad stocks WITH full data stay — a low
# score on real data is correct, not noise.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


class TestConfig:
    def test_radar_min_dimensions_exists(self):
        from config import RADAR_MIN_DIMENSIONS
        # Sane band — must require more than half, less than all 7.
        assert 3 <= RADAR_MIN_DIMENSIONS <= 6


class TestDataSufficiencyGate:
    def _cov(self, dims):
        return {"score_coverage": {"summary": {"dimensions_with_data": dims}}}

    def test_full_coverage_passes(self):
        from core.scan_coordinator import _radar_data_sufficient
        assert _radar_data_sufficient(self._cov(7)) is True

    def test_at_threshold_passes(self):
        from core.scan_coordinator import _radar_data_sufficient
        from config import RADAR_MIN_DIMENSIONS
        assert _radar_data_sufficient(self._cov(RADAR_MIN_DIMENSIONS)) is True

    def test_below_threshold_dropped(self):
        from core.scan_coordinator import _radar_data_sufficient
        from config import RADAR_MIN_DIMENSIONS
        assert _radar_data_sufficient(
            self._cov(RADAR_MIN_DIMENSIONS - 1)
        ) is False

    def test_thin_data_two_dims_dropped(self):
        from core.scan_coordinator import _radar_data_sufficient
        assert _radar_data_sufficient(self._cov(2)) is False

    def test_missing_coverage_field_defaults_pass(self):
        """A stock with no score_coverage must NOT be silently dropped —
        default to 'sufficient' so a field-shape regression can't empty
        the radar."""
        from core.scan_coordinator import _radar_data_sufficient
        assert _radar_data_sufficient({}) is True
        assert _radar_data_sufficient({"score_coverage": {}}) is True

    def test_genuinely_bad_full_data_stock_still_passes(self):
        """A stock with all 7 dimensions but terrible fundamentals
        (e.g. ROE -116%) must STILL enter the radar — a low score on
        real data is accurate, not noise. The gate only filters
        thin-data stocks, not bad ones."""
        from core.scan_coordinator import _radar_data_sufficient
        bad_but_complete = {
            "overall": 1,
            "score_coverage": {"summary": {"dimensions_with_data": 7}},
        }
        assert _radar_data_sufficient(bad_but_complete) is True

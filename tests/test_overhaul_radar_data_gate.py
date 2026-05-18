# ================================================================
# tests/test_overhaul_radar_data_gate.py
#
# Radar Overhaul follow-up (2026-05): data-sufficiency gate.
#
# After the universe expanded to the full BIST board (~622), ~130
# thin-data small caps collapsed to overall=1 and piled up at the
# bottom of the radar as misleading rows. The real message for those
# stocks is "veri yetersiz", not "bad company".
#
# Fix: a stock only enters the radar ranking if at least
# RADAR_MIN_DIMENSIONS (4) of the 7 FA dimensions have REAL (non-
# imputed) data. The gate reads `scores_imputed` — analysis.py'nin
# otoriter imputed-boyut listesi. (Eski sürüm ayrı bir score_coverage
# sayımı kullanıyordu; o fazla sayıyordu — bankalar borsapy'den
# finansal veri alamadığı halde radarda kalıyordu.)
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
    _ALL_DIMS = ["value", "quality", "growth", "balance",
                 "earnings", "moat", "capital"]

    def _result(self, real_dims):
        """real_dims kadar GERÇEK boyutu olan bir analiz sonucu üret —
        kalan 7-real_dims boyut imputed (scores_imputed listesinde)."""
        n_imputed = 7 - real_dims
        return {"scores_imputed": self._ALL_DIMS[:n_imputed]}

    def test_full_coverage_passes(self):
        from core.scan_coordinator import _radar_data_sufficient
        assert _radar_data_sufficient(self._result(7)) is True

    def test_at_threshold_passes(self):
        from core.scan_coordinator import _radar_data_sufficient
        from config import RADAR_MIN_DIMENSIONS
        assert _radar_data_sufficient(
            self._result(RADAR_MIN_DIMENSIONS)
        ) is True

    def test_below_threshold_dropped(self):
        from core.scan_coordinator import _radar_data_sufficient
        from config import RADAR_MIN_DIMENSIONS
        assert _radar_data_sufficient(
            self._result(RADAR_MIN_DIMENSIONS - 1)
        ) is False

    def test_thin_data_two_dims_dropped(self):
        from core.scan_coordinator import _radar_data_sufficient
        assert _radar_data_sufficient(self._result(2)) is False

    def test_bank_no_financials_dropped(self):
        """Banka senaryosu: borsapy banka bilançosu vermez →
        quality/growth/balance/earnings/moat imputed → 2 gerçek boyut
        → radardan düşmeli (sahte imputed-50 skoru gösterilmemeli)."""
        from core.scan_coordinator import _radar_data_sufficient
        bank = {"scores_imputed": ["quality", "growth", "balance",
                                   "earnings", "moat"]}
        assert _radar_data_sufficient(bank) is False

    def test_missing_field_defaults_pass(self):
        """scores_imputed alanı hiç yoksa (beklenmedik) stock sessizce
        düşürülmemeli — güvenli tarafta 'yeterli' say."""
        from core.scan_coordinator import _radar_data_sufficient
        assert _radar_data_sufficient({}) is True

    def test_genuinely_bad_full_data_stock_still_passes(self):
        """Tüm 7 boyutu olan ama berbat temelli (ör. ROE -%116) bir
        hisse radara GİRMELİ — gerçek veride düşük skor doğrudur,
        gürültü değil. Kapı yalnız ince-veri hisseleri eler."""
        from core.scan_coordinator import _radar_data_sufficient
        bad_but_complete = {"overall": 1, "scores_imputed": []}
        assert _radar_data_sufficient(bad_but_complete) is True

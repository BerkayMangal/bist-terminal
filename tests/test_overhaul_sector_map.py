# ================================================================
# tests/test_overhaul_sector_map.py
#
# Radar Overhaul follow-up (2026-05): frozen BIST sector map.
#
# Verification-pass finding: borsapy Ticker.info is unreliable from
# production — the `sector` field came back empty for 100% of stocks
# (a far higher failure rate than get_income_stmt's ~10%, proving it
# is not load-throttle but a systematic Ticker.info failure). Every
# stock collapsed to the "sanayi" default → broken sector-conditional
# scoring + a dashboard sector breakdown that was all "Diger".
#
# Fix: sector classification is static, so it's frozen in
# data/bist_sectors.py — built from borsapy's bulk sector indices
# (XBANK/XUTEK/XUSIN/...), a reliable ~21-call bulk source. When
# borsapy's per-stock sector is empty, analyze_symbol falls back to
# the frozen map.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Frozen map shape
# ────────────────────────────────────────────────────────────────


class TestFrozenMap:
    def test_map_exists_and_substantial(self):
        from data.bist_sectors import BIST_SECTOR_MAP
        # Built from sector indices — should cover most of BIST.
        assert len(BIST_SECTOR_MAP) >= 400

    def test_known_tickers_mapped(self):
        from data.bist_sectors import sector_label_for, sector_group_for
        # Banks
        assert sector_group_for("AKBNK") == "banka"
        assert sector_group_for("GARAN") == "banka"
        # Transport
        assert sector_group_for("THYAO") == "ulasim"
        # Holding
        assert sector_group_for("KCHOL") == "holding"
        # Labels are non-empty for covered tickers
        assert sector_label_for("AKBNK") != ""
        assert sector_label_for("FORTE") != ""

    def test_group_values_are_valid(self):
        """Every group the frozen map can emit must be a real scoring
        group that SECTOR_THRESHOLDS knows."""
        from data.bist_sectors import _LABEL_TO_GROUP
        from config import SECTOR_THRESHOLDS
        valid = set(SECTOR_THRESHOLDS.keys())
        for grp in _LABEL_TO_GROUP.values():
            assert grp in valid, f"label maps to unknown group {grp!r}"

    def test_unknown_ticker_safe(self):
        from data.bist_sectors import sector_label_for, sector_group_for
        # Not a real ticker — must not raise, must default sanely.
        assert sector_label_for("ZZZZZ") == ""
        assert sector_group_for("ZZZZZ") == "sanayi"

    def test_suffix_stripped(self):
        from data.bist_sectors import sector_group_for
        # .IS suffix must be handled
        assert sector_group_for("AKBNK.IS") == sector_group_for("AKBNK")


# ────────────────────────────────────────────────────────────────
# analyze_symbol wiring — fallback to frozen map
# ────────────────────────────────────────────────────────────────


class TestAnalysisWiring:
    def test_analysis_imports_frozen_map(self):
        import inspect
        from engine import analysis
        src = inspect.getsource(analysis.analyze_symbol)
        assert "sector_group_for" in src, (
            "analyze_symbol not wired to the frozen sector map"
        )
        assert "sector_label_for" in src

    def test_frozen_map_is_primary_source(self):
        """Frozen sector map (data/bist_sectors.py — borsapy'nin
        GÜVENİLİR toplu sektör endekslerinden üretildi) BİRİNCİL kaynak.
        borsapy'nin per-stock Ticker.info sector'ı güvenilmez (boş ya da
        Türkçe döner) — yalnız frozen map'te olmayan hisseler için
        fallback. Kaynak sırası ile pinlenir."""
        import inspect
        from engine import analysis
        src = inspect.getsource(analysis.analyze_symbol)
        assert "_frozen_label" in src
        assert "sector_group_for(symbol)" in src
        assert "map_sector(_borsapy_sector)" in src
        # Frozen-map dalı, borsapy fallback'inden ÖNCE gelmeli.
        i_frozen = src.find("if _frozen_label")
        i_borsapy = src.find("elif _borsapy_sector")
        assert i_frozen != -1 and i_borsapy != -1
        assert i_frozen < i_borsapy, "frozen map borsapy'den önce gelmeli"

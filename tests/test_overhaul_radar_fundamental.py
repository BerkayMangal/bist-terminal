# ================================================================
# tests/test_overhaul_radar_fundamental.py
#
# Radar Overhaul (2026-05).
#
# User decision:
#   1. Radar universe 108 -> full BIST (~622). It runs once a day and
#      balance sheets only change quarterly, so full coverage is cheap.
#   2. Drop technical analysis from Radar entirely — Radar answers
#      "is this company fundamentally good + fairly priced?". Entry
#      timing ("stay away / enter now") is Cross Hunter / BullWatch.
#
# This stage:
#   - RADAR_UNIVERSE = FULL_BIST
#   - fundamental_quality_label() replaces the timing-flavored
#     entry_quality_label (TEYİTLİ/ERKEN/GEÇ) with quality labels
#     (Kaliteli Değer / Pahalı Kalite / Ucuz ama Riskli / ...)
#   - fundamental_decision() — AL/İZLE/BEKLE/KAÇIN purely from
#     fa_pure + risk, no momentum, no entry-label dependency
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# RADAR_UNIVERSE — full BIST
# ────────────────────────────────────────────────────────────────


class TestRadarUniverse:
    def test_radar_universe_is_full_bist(self):
        from config import RADAR_UNIVERSE, FULL_BIST
        assert RADAR_UNIVERSE == FULL_BIST

    def test_radar_universe_much_larger_than_legacy_108(self):
        from config import RADAR_UNIVERSE
        # Legacy radar scanned 108. Full BIST is ~600+.
        assert len(RADAR_UNIVERSE) > 400, (
            f"RADAR_UNIVERSE only {len(RADAR_UNIVERSE)} — expected full board"
        )

    def test_scan_endpoints_use_radar_universe(self):
        """app.py must scan RADAR_UNIVERSE, not the 108-symbol UNIVERSE."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "app.py"),
            "r", encoding="utf-8",
        ) as fh:
            src = fh.read()
        assert "start_scan, RADAR_UNIVERSE" in src, (
            "Background scanner / scan endpoint still on the 108 UNIVERSE"
        )


# ────────────────────────────────────────────────────────────────
# fundamental_quality_label
# ────────────────────────────────────────────────────────────────


class TestFundamentalQualityLabel:
    def test_kaliteli_deger(self):
        """Strong fundamentals + cheap → Kaliteli Değer."""
        from engine.scoring import fundamental_quality_label
        assert fundamental_quality_label(85, 70, 0) == "Kaliteli Değer"
        assert fundamental_quality_label(62, 56, -5) == "Kaliteli Değer"

    def test_pahali_kalite(self):
        """Strong fundamentals but expensive valuation → Pahalı Kalite."""
        from engine.scoring import fundamental_quality_label
        assert fundamental_quality_label(78, 40, 0) == "Pahalı Kalite"
        assert fundamental_quality_label(60, 30, -5) == "Pahalı Kalite"

    def test_ucuz_ama_riskli(self):
        """Cheap but weak fundamentals → Ucuz ama Riskli."""
        from engine.scoring import fundamental_quality_label
        assert fundamental_quality_label(42, 70, 0) == "Ucuz ama Riskli"

    def test_zayif_temel_low_fa(self):
        from engine.scoring import fundamental_quality_label
        assert fundamental_quality_label(28, 50, 0) == "Zayıf Temel"

    def test_zayif_temel_high_risk(self):
        """Even a decent FA score → Zayıf Temel if risk is severe."""
        from engine.scoring import fundamental_quality_label
        assert fundamental_quality_label(70, 60, -30) == "Zayıf Temel"

    def test_dengeli_middle(self):
        from engine.scoring import fundamental_quality_label
        assert fundamental_quality_label(52, 50, 0) == "Dengeli"

    def test_no_timing_labels_emitted(self):
        """The old timing labels must NEVER come out of the new fn."""
        from engine.scoring import fundamental_quality_label
        timing = {"TEYİTLİ", "ERKEN", "GEÇ", "FIRSAT", "SPEKÜLATİF",
                  "KAÇIN", "BEKLE"}
        for fa in range(10, 100, 7):
            for val in range(10, 100, 11):
                for rp in (0, -10, -25, -40):
                    out = fundamental_quality_label(fa, val, rp)
                    assert out not in timing, (
                        f"Timing label {out!r} leaked at "
                        f"fa={fa} val={val} rp={rp}"
                    )

    def test_none_inputs_safe(self):
        from engine.scoring import fundamental_quality_label
        # Must not raise on None
        out = fundamental_quality_label(None, None, None)
        assert isinstance(out, str)


# ────────────────────────────────────────────────────────────────
# fundamental_decision
# ────────────────────────────────────────────────────────────────


class TestFundamentalDecision:
    def test_al_on_strong_fa(self):
        from engine.scoring import fundamental_decision
        assert fundamental_decision(72, 0) == "AL"
        assert fundamental_decision(60, -10) == "AL"

    def test_izle_on_mid_fa(self):
        from engine.scoring import fundamental_decision
        assert fundamental_decision(50, -5) == "İZLE"

    def test_bekle_on_low_mid(self):
        from engine.scoring import fundamental_decision
        assert fundamental_decision(40, -5) == "BEKLE"

    def test_kacin_on_weak_fa(self):
        from engine.scoring import fundamental_decision
        assert fundamental_decision(25, 0) == "KAÇIN"

    def test_kacin_on_severe_risk(self):
        from engine.scoring import fundamental_decision
        assert fundamental_decision(70, -30) == "KAÇIN"

    def test_decision_monotonic_in_fa(self):
        """Higher FA should never produce a *worse* decision (at fixed
        risk). Pin the ordering AL > İZLE > BEKLE > KAÇIN."""
        from engine.scoring import fundamental_decision
        rank = {"KAÇIN": 0, "BEKLE": 1, "İZLE": 2, "AL": 3}
        prev = -1
        for fa in range(20, 90, 5):
            d = fundamental_decision(fa, 0)
            assert rank[d] >= prev, f"decision regressed at fa={fa}: {d}"
            prev = rank[d]


# ────────────────────────────────────────────────────────────────
# analyze_symbol wiring — entry_label is now a quality label
# ────────────────────────────────────────────────────────────────


class TestAnalysisWiring:
    def test_analysis_imports_new_functions(self):
        """analyze_symbol must use the new fundamental functions."""
        import inspect
        from engine import analysis
        src = inspect.getsource(analysis)
        assert "fundamental_quality_label" in src
        assert "fundamental_decision" in src

    def test_analysis_no_longer_calls_entry_quality_label(self):
        """The old timing classifier must not be wired into the radar
        analyze path anymore."""
        import inspect
        from engine import analysis
        # Get just the analyze_symbol function source
        for name in dir(analysis):
            obj = getattr(analysis, name)
            if callable(obj) and name == "analyze_symbol":
                src = inspect.getsource(obj)
                assert "entry_quality_label(" not in src, (
                    "analyze_symbol still calls the old timing classifier"
                )
                break


# ────────────────────────────────────────────────────────────────
# UI — labels + technical-tab disclaimer
# ────────────────────────────────────────────────────────────────


class TestUI:
    @pytest.fixture(scope="class")
    def terminal_src(self):
        with open(
            os.path.join(os.path.dirname(__file__), "..", "static",
                         "terminal.js"),
            "r", encoding="utf-8",
        ) as fh:
            return fh.read()

    def test_new_labels_in_render(self, terminal_src):
        for lbl in ("Kaliteli Değer", "Pahalı Kalite", "Ucuz ama Riskli",
                    "Zayıf Temel", "Dengeli"):
            assert lbl in terminal_src, f"label {lbl!r} missing from UI"

    def test_technical_tab_disclaimer(self, terminal_src):
        assert "Radar sıralamasına etki etmez" in terminal_src, (
            "Technical tab missing the 'reference only' disclaimer"
        )

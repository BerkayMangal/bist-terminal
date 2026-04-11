# ================================================================
# BISTBULL TERMINAL — MACRO DECISION ENGINE TESTS
# tests/test_macro_decision.py
# ================================================================

import pytest
from engine.macro_decision import (
    compute_regime, _safe_float, _safe_pct,
    get_sector_rotation, RegimeResult,
    THRESHOLDS, REGIME_THRESHOLDS,
)
from engine.action_summary import generate_action_summary
from engine.macro_signals import build_engine_inputs, build_freshness_report


# ================================================================
# SAFETY GUARDS
# ================================================================
class TestSafeFloat:
    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_nan(self):
        assert _safe_float(float("nan")) == 0.0

    def test_string(self):
        assert _safe_float("abc") == 0.0

    def test_clamp_high(self):
        assert _safe_float(99999, hi=500) == 500

    def test_clamp_low(self):
        assert _safe_float(-99999, lo=-100) == -100

    def test_normal(self):
        assert _safe_float(3.14) == 3.14

    def test_string_number(self):
        assert _safe_float("42.5") == 42.5


class TestSafePct:
    def test_clamp(self):
        assert _safe_pct(600) == 500.0
        assert _safe_pct(-200) == -100.0


# ================================================================
# REGIME DETECTION
# ================================================================
class TestRegimeDetection:
    """Core regime detection logic."""

    def _base_inputs(self, **overrides):
        """All-neutral baseline."""
        defaults = {
            "cds": 300,           # neutral
            "usdtry_5d_pct": 2.0, # neutral
            "vix": 20,            # neutral
            "dxy_20d_pct": 0.0,   # neutral
            "yield_spread": -0.5,  # neutral
            "foreign_flow": 0,     # neutral (threshold is 0/0)
            "global_idx_5d_pct": 0.0,  # neutral
            "bist_5d_pct": 0.0,
        }
        defaults.update(overrides)
        return defaults

    def test_all_neutral(self):
        r = compute_regime(self._base_inputs())
        assert r.regime == "NEUTRAL"
        assert r.score == 0

    def test_strong_risk_on(self):
        r = compute_regime(self._base_inputs(
            cds=200, usdtry_5d_pct=0.5, vix=15,
            dxy_20d_pct=-2.0, yield_spread=1.0,
            foreign_flow=100, global_idx_5d_pct=2.0,
        ))
        assert r.regime == "RISK_ON"
        assert r.score >= 3
        assert r.confidence in ("HIGH", "MEDIUM")

    def test_strong_risk_off(self):
        r = compute_regime(self._base_inputs(
            cds=400, usdtry_5d_pct=5.0, vix=30,
            dxy_20d_pct=3.0, yield_spread=-2.0,
            foreign_flow=-100, global_idx_5d_pct=-3.0,
        ))
        assert r.regime == "RISK_OFF"
        assert r.score <= -3
        assert r.confidence in ("HIGH", "MEDIUM")

    def test_borderline_neutral(self):
        """Score of +2 should still be NEUTRAL."""
        r = compute_regime(self._base_inputs(
            cds=200, usdtry_5d_pct=0.5,  # two bullish
        ))
        assert r.regime == "NEUTRAL"
        assert r.score == 2

    def test_borderline_risk_on(self):
        """Score of exactly +3 → RISK_ON."""
        r = compute_regime(self._base_inputs(
            cds=200, usdtry_5d_pct=0.5, vix=15,  # three bullish
        ))
        assert r.regime == "RISK_ON"
        assert r.score == 3

    def test_explanation_not_empty(self):
        r = compute_regime(self._base_inputs())
        assert len(r.explanation) > 10

    def test_signals_count(self):
        r = compute_regime(self._base_inputs())
        assert len(r.signals) == 6


# ================================================================
# CONTRADICTION DETECTION
# ================================================================
class TestContradictions:

    def test_bist_up_in_risk_off(self):
        r = compute_regime({
            "cds": 400, "usdtry_5d_pct": 5.0, "vix": 30,
            "dxy_20d_pct": 3.0, "yield_spread": -2.0,
            "foreign_flow": -100, "global_idx_5d_pct": -3.0,
            "bist_5d_pct": 5.0,  # BIST rallying despite risk off
        })
        assert r.regime == "RISK_OFF"
        types = [c.type for c in r.contradictions]
        assert "bist_vs_macro" in types

    def test_bist_down_in_risk_on(self):
        r = compute_regime({
            "cds": 200, "usdtry_5d_pct": 0.5, "vix": 15,
            "dxy_20d_pct": -2.0, "yield_spread": 1.0,
            "foreign_flow": 100, "global_idx_5d_pct": 2.0,
            "bist_5d_pct": -5.0,  # BIST dropping despite risk on
        })
        assert r.regime == "RISK_ON"
        types = [c.type for c in r.contradictions]
        assert "bist_vs_macro" in types

    def test_cds_vs_fx(self):
        r = compute_regime({
            "cds": 350, "usdtry_5d_pct": -2.0,  # CDS high but TL strengthening
            "vix": 20, "dxy_20d_pct": 0, "yield_spread": 0,
            "foreign_flow": 0, "global_idx_5d_pct": 0,
            "bist_5d_pct": 0,
        })
        types = [c.type for c in r.contradictions]
        assert "cds_vs_fx" in types

    def test_global_vs_local(self):
        r = compute_regime({
            "cds": 300, "usdtry_5d_pct": 2.0, "vix": 20,
            "dxy_20d_pct": 0, "yield_spread": 0,
            "foreign_flow": 0, "global_idx_5d_pct": 2.0,
            "bist_5d_pct": -2.0,  # global up, BIST down
        })
        types = [c.type for c in r.contradictions]
        assert "global_vs_local" in types

    def test_no_contradiction_when_aligned(self):
        r = compute_regime({
            "cds": 200, "usdtry_5d_pct": 0.5, "vix": 15,
            "dxy_20d_pct": -2.0, "yield_spread": 1.0,
            "foreign_flow": 100, "global_idx_5d_pct": 2.0,
            "bist_5d_pct": 3.0,  # aligned with risk on
        })
        assert len(r.contradictions) == 0


# ================================================================
# EDGE CASES
# ================================================================
class TestEdgeCases:

    def test_all_none(self):
        """Missing data should not crash — defaults to neutral."""
        r = compute_regime({})
        assert r.regime == "NEUTRAL"
        assert r.score == 0

    def test_partial_data(self):
        """Only CDS provided."""
        r = compute_regime({"cds": 400})
        assert r.regime in ("RISK_OFF", "NEUTRAL", "RISK_ON")
        assert len(r.signals) == 6

    def test_extreme_values(self):
        """Extreme values should be clamped, not crash."""
        r = compute_regime({
            "cds": 99999, "usdtry_5d_pct": 999, "vix": 999,
            "dxy_20d_pct": 999, "yield_spread": -999,
            "foreign_flow": -999999, "global_idx_5d_pct": -999,
            "bist_5d_pct": -999,
        })
        assert r.regime == "RISK_OFF"
        # Should not crash or produce NaN
        assert r.score == r.score  # not NaN

    def test_string_inputs(self):
        """String values should be handled gracefully."""
        r = compute_regime({"cds": "abc", "vix": "xyz"})
        assert r.regime == "NEUTRAL"


# ================================================================
# ACTION SUMMARY
# ================================================================
class TestActionSummary:

    def test_risk_off_summary(self):
        r = compute_regime({
            "cds": 400, "usdtry_5d_pct": 5.0, "vix": 30,
            "dxy_20d_pct": 3.0, "yield_spread": -2.0,
            "foreign_flow": -100, "global_idx_5d_pct": -3.0,
            "bist_5d_pct": 0,
        })
        text = generate_action_summary(r)
        assert "temkinli" in text.lower()
        assert "pozisyon" in text.lower() or "sektör" in text.lower()
        assert len(text) < 500

    def test_risk_on_summary(self):
        r = compute_regime({
            "cds": 200, "usdtry_5d_pct": 0.5, "vix": 15,
            "dxy_20d_pct": -2.0, "yield_spread": 1.0,
            "foreign_flow": 100, "global_idx_5d_pct": 2.0,
            "bist_5d_pct": 0,
        })
        text = generate_action_summary(r)
        assert "destekleyici" in text.lower() or "alım" in text.lower()

    def test_neutral_summary(self):
        r = compute_regime({
            "cds": 300, "usdtry_5d_pct": 2.0, "vix": 20,
            "dxy_20d_pct": 0, "yield_spread": 0,
            "foreign_flow": 0, "global_idx_5d_pct": 0,
            "bist_5d_pct": 0,
        })
        text = generate_action_summary(r)
        assert "kararsız" in text.lower() or "bekle" in text.lower()

    def test_with_event(self):
        r = compute_regime({"cds": 300})
        text = generate_action_summary(r, upcoming_event="Perşembe enflasyon verisi")
        assert "perşembe" in text.lower() or "enflasyon" in text.lower()

    def test_with_contradiction(self):
        r = compute_regime({
            "cds": 400, "usdtry_5d_pct": 5.0, "vix": 30,
            "dxy_20d_pct": 3.0, "yield_spread": -2.0,
            "foreign_flow": -100, "global_idx_5d_pct": -3.0,
            "bist_5d_pct": 5.0,
        })
        text = generate_action_summary(r)
        assert "dikkat" in text.lower()

    def test_no_hype_words(self):
        """Summary must never contain hype language."""
        for cds in [200, 300, 400]:
            r = compute_regime({"cds": cds, "vix": 15})
            text = generate_action_summary(r).lower()
            for bad in ["uçuş", "patlama", "fırsat kaçırma", "hemen al"]:
                assert bad not in text, f"Hype word '{bad}' found in: {text}"


# ================================================================
# SIGNAL BUILDER
# ================================================================
class TestSignalBuilder:

    def test_build_from_real_data(self):
        """Simulate real macro_items structure."""
        items = [
            {"key": "USDTRY", "price": 38.5, "change_pct": 0.3, "w1_pct": 1.5, "m1_pct": 3.0},
            {"key": "VIX", "price": 22.0, "change_pct": -1.0, "w1_pct": -5.0},
            {"key": "DXY", "price": 103.5, "change_pct": 0.1, "w1_pct": 0.5, "m1_pct": 1.2},
            {"key": "SP500", "price": 5800, "change_pct": 0.5, "w1_pct": 1.8},
            {"key": "XU100", "price": 9500, "change_pct": 0.8, "w1_pct": 2.1},
        ]
        rates = [
            {"key": "CDS_TR", "rate": 320, "updated": "2026-04-10"},
            {"key": "TR10Y", "rate": 30.5, "updated": "2026-04-10"},
            {"key": "TR2Y", "rate": 34.0, "updated": "2026-04-10"},
        ]
        inputs = build_engine_inputs(items, rates, "2026-04-11T10:00:00Z")
        assert inputs["cds"] == 320
        assert inputs["vix"] == 22.0
        assert inputs["usdtry_5d_pct"] == 1.5
        assert inputs["bist_5d_pct"] == 2.1

    def test_empty_data(self):
        inputs = build_engine_inputs([], [], None)
        # Should not crash
        result = compute_regime(inputs)
        assert result.regime == "NEUTRAL"

    def test_freshness_report(self):
        inputs = {
            "cds_source": "tahmini",
            "cds_fetched_at": "2026-04-10",
            "vix_source": "canlı",
            "vix_fetched_at": "2026-04-11T10:00:00Z",
            "foreign_flow_source": "yok",
        }
        report = build_freshness_report(inputs)
        assert len(report) == 3
        stale = [r for r in report if r["stale"]]
        assert len(stale) == 1  # "yok" is stale


# ================================================================
# SECTOR ROTATION
# ================================================================
class TestSectorRotation:

    def test_risk_on_sectors(self):
        s = get_sector_rotation("RISK_ON")
        assert "Bankacılık" in s["strong"]
        assert "Gıda" in s["weak"]

    def test_risk_off_sectors(self):
        s = get_sector_rotation("RISK_OFF")
        assert "Gıda" in s["strong"]
        assert "Bankacılık" in s["weak"]

    def test_unknown_regime_falls_back(self):
        s = get_sector_rotation("UNKNOWN")
        assert "strong" in s

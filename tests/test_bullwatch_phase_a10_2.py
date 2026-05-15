"""Tests for BullWatch v2 Phase A.10 Step 2-A.2 — UI Trust & Cycle Polish:
   - _compute_cycle_state mapping
   - _build_narrative uses diagnostic dicts (conflict, maturity, playbook, pinning)
   - cycle_state field on BullWatchResult
   - data-driven Şüphe section
   - trigger-style Ne bekle (legal-safe, no advice words)
   - no v1 regression
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
import pytest

from engine.bullwatch import (
    score_symbol, BullWatchResult,
    _build_narrative,
    _compute_cycle_state,
)


def _ohlcv(closes, vol=600_000):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.bdate_range(end="2026-05-08", periods=n)
    return pd.DataFrame({
        "Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
        "Close": closes, "Volume": np.full(n, vol),
    }, index=idx)


# ============================================================
# 1. _compute_cycle_state — display mapping
# ============================================================
class TestCycleStateMapping:
    def test_unclear_when_no_dicts(self):
        assert _compute_cycle_state({}) == "BELİRSİZ"

    def test_belirsiz_when_dom_unclear(self):
        cm = {"dominant_read": "UNCLEAR"}
        assert _compute_cycle_state({}, conflict_dict=cm) == "BELİRSİZ"

    def test_toplaniyor_accumulation_early(self):
        cm = {"dominant_read": "ACCUMULATION"}
        mat = {"maturity": "EARLY"}
        assert _compute_cycle_state({}, conflict_dict=cm, maturity_dict=mat) == "TOPLANIYOR"

    def test_toplaniyor_accumulation_mid(self):
        cm = {"dominant_read": "ACCUMULATION"}
        mat = {"maturity": "MID"}
        assert _compute_cycle_state({}, conflict_dict=cm, maturity_dict=mat) == "TOPLANIYOR"

    def test_aleseniyor_with_walkup(self):
        cm = {"dominant_read": "ACCUMULATION"}
        mat = {"maturity": "MID"}
        m = {"patterns": ["Walk-Up Accumulation"]}
        assert _compute_cycle_state(m, conflict_dict=cm, maturity_dict=mat) == "ATEŞLENİYOR"

    def test_aleseniyor_with_markup_playbook(self):
        cm = {"dominant_read": "ACCUMULATION"}
        pb = {"playbook": "MARKUP_SEQUENCE"}
        assert _compute_cycle_state({}, conflict_dict=cm, playbook_dict=pb) == "ATEŞLENİYOR"

    def test_dagitim_riski_distribution(self):
        cm = {"dominant_read": "DISTRIBUTION"}
        mat = {"maturity": "MID"}
        assert _compute_cycle_state({}, conflict_dict=cm, maturity_dict=mat) == "DAĞITIM RİSKİ"

    def test_bosaltiyor_distribution_late(self):
        cm = {"dominant_read": "DISTRIBUTION"}
        mat = {"maturity": "LATE"}
        assert _compute_cycle_state({}, conflict_dict=cm, maturity_dict=mat) == "BOŞALTIYOR"

    def test_bosaltiyor_markdown_playbook(self):
        cm = {"dominant_read": "ACCUMULATION"}  # even with ACC dominant
        pb = {"playbook": "MARKDOWN_SEQUENCE"}
        # markdown playbook overrides
        assert _compute_cycle_state({}, conflict_dict=cm, playbook_dict=pb) == "BOŞALTIYOR"

    def test_belirsiz_fallback(self):
        # No dicts → should return BELİRSİZ
        assert _compute_cycle_state({"patterns": []}) == "BELİRSİZ"


# ============================================================
# 2. _build_narrative — diagnostic-aware variations
# ============================================================
class TestBuildNarrativeDiagnostics:
    def _base_metrics(self, **overrides):
        m = {
            "patterns": [],
            "float_pressure": None, "rvol": None,
            "atr_compression": None, "bb_compression": None,
            "price_change_5d": None,
        }
        m.update(overrides)
        return m

    def test_dist_conflict_drives_watch_text(self):
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics=self._base_metrics(),
            data_quality="high",
            conflict_dict={"dominant_read": "DISTRIBUTION", "confidence": 65},
            maturity_dict={"maturity": "MID"},
        )
        wtw = narrative["what_to_watch"]
        assert "Yüksek hacme rağmen" in wtw or "dağıtım" in wtw.lower()

    def test_acc_early_uses_trigger_language(self):
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics=self._base_metrics(),
            data_quality="high",
            conflict_dict={"dominant_read": "ACCUMULATION"},
            maturity_dict={"maturity": "EARLY"},
        )
        wtw = narrative["what_to_watch"]
        assert "Trigger" in wtw

    def test_position_in_range_surfaces(self):
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics=self._base_metrics(),
            data_quality="high",
            maturity_dict={"indicators": {"position_in_range": 0.92}},
        )
        wh = narrative["whats_happening"]
        assert "üst" in wh.lower() or "tepe" in wh.lower()

    def test_pinning_score_surfaces(self):
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics=self._base_metrics(),
            data_quality="high",
            pinning_dict={"price_pinning_score": 70},
        )
        wh = narrative["whats_happening"]
        assert "pinning" in wh.lower() or "dar bant" in wh.lower()

    def test_data_status_partial_caveat(self):
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics=self._base_metrics(_data_status="partial",
                                      _missing_fields=["income_statement"]),
            data_quality="medium",
        )
        cav = narrative["caveats"]
        assert "partial" in cav.lower()
        assert "income_statement" in cav

    def test_override_applied_caveat(self):
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics=self._base_metrics(override_applied=True,
                                      override_fields=["free_float"]),
            data_quality="high",
        )
        cav = narrative["caveats"]
        assert "manual override" in cav.lower() or "free_float" in cav.lower()

    def test_low_confidence_tier_caveat(self):
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics=self._base_metrics(),
            data_quality="high",
            conflict_dict={"confidence_tier": "LOW", "evidence_depth_count": 1},
        )
        cav = narrative["caveats"]
        assert "tek rule" in cav.lower() or "ek teyit" in cav.lower()

    def test_high_position_dist_human_review_caveat(self):
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics=self._base_metrics(),
            data_quality="high",
            conflict_dict={"dominant_read": "DISTRIBUTION"},
            maturity_dict={"indicators": {"position_in_range": 0.91}},
        )
        cav = narrative["caveats"]
        assert "insan gözüyle" in cav.lower() or "yüksek konum" in cav.lower()


# ============================================================
# 3. Legal-safe language — never advisory words
# ============================================================
class TestLegalSafeLanguage:
    """Spec section 9: never use advisory words like 'al', 'sat', 'hedef',
    'stop', 'kesin'. Use observation-style language."""

    FORBIDDEN_WORDS = [
        "kesin al", "kesin sat",
        "manipülasyon var",
        "hedef ", "hedef.",
        "stop ", "stop.",
        "garanti",
    ]

    def test_no_forbidden_words_in_distribution_narrative(self):
        narrative = _build_narrative(
            score=70, zone="CONVICTION", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics={"patterns": ["Walk-Up Accumulation"],
                     "rvol": 3.0, "float_pressure": 0.05,
                     "atr_compression": 0.85, "bb_compression": 0.85,
                     "price_change_5d": 0.02},
            data_quality="high",
            conflict_dict={"dominant_read": "DISTRIBUTION", "confidence_tier": "MEDIUM",
                           "evidence_depth_count": 2, "confidence": 70},
            maturity_dict={"maturity": "LATE",
                           "indicators": {"position_in_range": 0.88}},
        )
        full = " ".join(narrative.values()).lower()
        for word in self.FORBIDDEN_WORDS:
            assert word not in full, f"Forbidden word '{word}' found in narrative"

    def test_no_forbidden_words_in_accumulation_narrative(self):
        narrative = _build_narrative(
            score=80, zone="CONVICTION", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics={"patterns": ["Walk-Up Accumulation"]},
            data_quality="high",
            conflict_dict={"dominant_read": "ACCUMULATION"},
            maturity_dict={"maturity": "EARLY"},
        )
        full = " ".join(narrative.values()).lower()
        for word in self.FORBIDDEN_WORDS:
            assert word not in full

    def test_uses_observation_language(self):
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics={"patterns": ["Absorption"]},
            data_quality="high",
            conflict_dict={"dominant_read": "ACCUMULATION"},
            maturity_dict={"maturity": "MID"},
        )
        wtw = narrative["what_to_watch"].lower()
        # Should use observation-style words
        observation_words = ["trigger", "beklenen teyit", "takip", "gözlem"]
        assert any(w in wtw for w in observation_words), \
            f"Expected observation words in: {narrative['what_to_watch']}"


# ============================================================
# 4. End-to-end via score_symbol — cycle_state surfaces
# ============================================================
class TestScoreSymbolCycleState:
    def test_cycle_state_attached_to_result(self):
        df = _ohlcv(np.linspace(10, 11, 80), 600_000)
        m = {"symbol": "T", "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8}
        r = score_symbol(m, df=df)
        # cycle_state must be one of the allowed values
        assert r.cycle_state in (
            "TOPLANIYOR", "ATEŞLENİYOR", "DAĞITIM RİSKİ",
            "BOŞALTIYOR", "BELİRSİZ"
        )

    def test_cycle_state_in_to_dict(self):
        df = _ohlcv(np.linspace(10, 11, 80), 600_000)
        m = {"symbol": "T", "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8}
        r = score_symbol(m, df=df)
        d = r.to_dict()
        assert "cycle_state" in d


# ============================================================
# 5. v1 regression — narrative still produces the 3 paragraphs
# ============================================================
class TestNarrativeBackwardsCompat:
    def test_legacy_call_without_dicts_still_works(self):
        """Old callers that don't pass conflict_dict etc. should still get
        a valid 3-key narrative dict."""
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics={"patterns": ["Absorption"], "rvol": 1.5,
                     "float_pressure": 0.03,
                     "atr_compression": None, "bb_compression": None,
                     "price_change_5d": None},
            data_quality="medium",
        )
        assert "whats_happening" in narrative
        assert "what_to_watch" in narrative
        assert "caveats" in narrative
        assert all(isinstance(v, str) for v in narrative.values())

    def test_pattern_recognition_still_surfaces(self):
        """Hotfix18 case-fix: Absorption pattern label must produce
        absorption text. Step 2-A.2 must preserve this."""
        narrative = _build_narrative(
            score=50, zone="EARLY", pattern="Test",
            sector_tr="Endüstri", components={},
            metrics={"patterns": ["Absorption"]},
            data_quality="high",
        )
        wtw = narrative["what_to_watch"].lower()
        assert "absorption" in wtw

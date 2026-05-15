"""Tests for BullWatch v2 Phase A.6 hygiene patch:
   1. Tiered universe (core / extended / institutional / no_data)
   2. Manual free_float overrides for KAPLM, GLRMK, ASELS
   3. Turnover-based conflict matrix rules (high_turnover_*)
   4. Confidence tier system (rule_agreement_pct, evidence_depth_count, confidence_tier)
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
import pytest

from features.bullwatch_features import (
    classify_universe_tier,
    FLOAT_MARKET_CAP_CAP_TL,
    EXTENDED_WATCH_CAP_TL,
)
from data.bullwatch_cache import _KNOWN_OVERRIDES
from engine.bullwatch_conflict import (
    resolve_conflict_matrix, CONFLICT_RULES, _classify_confidence_tier,
)
from engine.bullwatch import score_symbol


def _ohlcv(closes, volumes=None):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    if volumes is None:
        volumes = np.full(n, 200_000.0)
    idx = pd.bdate_range(end="2026-05-08", periods=n)
    return pd.DataFrame({
        "Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
        "Close": closes, "Volume": volumes,
    }, index=idx)


# ============================================================
# 1. Tiered universe classification
# ============================================================
class TestUniverseTier:
    def test_core_tier_below_3B(self):
        # 5B mcap × 0.5 = 2.5B float → core
        assert classify_universe_tier(5_000_000_000, 0.5) == "core"
        # 1B × 0.4 = 400M → core
        assert classify_universe_tier(1_000_000_000, 0.4) == "core"

    def test_extended_tier_3B_to_15B(self):
        # 10B × 0.5 = 5B → extended
        assert classify_universe_tier(10_000_000_000, 0.5) == "extended"
        # 30B × 0.5 = 15B → extended (boundary inclusive)
        assert classify_universe_tier(30_000_000_000, 0.5) == "extended"
        # 6B × 1.0 = 6B → extended
        assert classify_universe_tier(6_000_000_000, 1.0) == "extended"

    def test_institutional_tier_above_15B(self):
        # 100B × 0.5 = 50B → institutional
        assert classify_universe_tier(100_000_000_000, 0.5) == "institutional"
        # 32B × 0.5 = 16B → institutional
        assert classify_universe_tier(32_000_000_000, 0.5) == "institutional"

    def test_no_data_when_missing_inputs(self):
        assert classify_universe_tier(None, 0.5) == "no_data"
        assert classify_universe_tier(1_000_000_000, None) == "no_data"
        assert classify_universe_tier(None, None) == "no_data"

    def test_boundary_3B_exactly(self):
        # 3B exactly → core (boundary inclusive)
        assert classify_universe_tier(6_000_000_000, 0.5) == "core"

    def test_boundary_15B_exactly(self):
        # 15B exactly → extended (boundary inclusive)
        assert classify_universe_tier(30_000_000_000, 0.5) == "extended"


# ============================================================
# 2. Manual overrides exist
# ============================================================
class TestManualOverrides:
    def test_kaplm_override_present(self):
        assert "KAPLM" in _KNOWN_OVERRIDES
        assert "free_float" in _KNOWN_OVERRIDES["KAPLM"]
        ff = _KNOWN_OVERRIDES["KAPLM"]["free_float"]
        assert 0 < ff < 1.0, f"KAPLM free_float must be a fraction, got {ff}"

    def test_glrmk_override_present(self):
        assert "GLRMK" in _KNOWN_OVERRIDES
        assert 0 < _KNOWN_OVERRIDES["GLRMK"]["free_float"] < 1.0

    def test_asels_override_present(self):
        assert "ASELS" in _KNOWN_OVERRIDES
        ff = _KNOWN_OVERRIDES["ASELS"]["free_float"]
        # ASELS public free float ~25.93%
        assert 0.20 < ff < 0.35, f"ASELS free_float should be ~26%, got {ff}"

    def test_existing_overrides_preserved(self):
        # Phase A.6 must NOT remove existing overrides
        assert "ICBCT" in _KNOWN_OVERRIDES
        assert "GLCVY" in _KNOWN_OVERRIDES


# ============================================================
# 3. Turnover-based conflict rules
# ============================================================
class TestTurnoverConflictRules:
    def test_high_turnover_low_position_calm_fires_accumulation(self):
        """turnover>1.0, |ret_20d|<10%, position<0.5 → ACCUMULATION (w=25)"""
        state = {
            "float_turnover_20d": 2.5,
            "ret_20d": 0.03,
            "position_in_range": 0.30,
        }
        result = resolve_conflict_matrix(state)
        assert result.dominant_read == "ACCUMULATION", \
            f"got {result.dominant_read}, fired={[r['rule'] for r in result.resolved_by]}"
        # Verify the specific rule fired
        rules_fired = [r["rule"] for r in result.resolved_by]
        assert "high_turnover_low_position_calm" in rules_fired

    def test_high_turnover_high_position_strong_move_fires_distribution(self):
        """turnover>1.0, position>0.7, ret_20d>30% → DISTRIBUTION (w=25)"""
        state = {
            "float_turnover_20d": 2.0,
            "ret_20d": 0.45,
            "position_in_range": 0.85,
        }
        result = resolve_conflict_matrix(state)
        assert result.dominant_read == "DISTRIBUTION"
        rules_fired = [r["rule"] for r in result.resolved_by]
        assert "high_turnover_high_position_strong_move" in rules_fired

    def test_turnover_below_1_does_not_fire(self):
        """turnover < 1.0 → no turnover rule fires"""
        state = {
            "float_turnover_20d": 0.5,
            "ret_20d": 0.03,
            "position_in_range": 0.30,
        }
        result = resolve_conflict_matrix(state)
        rules_fired = [r["rule"] for r in result.resolved_by]
        assert "high_turnover_low_position_calm" not in rules_fired
        assert "high_turnover_high_position_strong_move" not in rules_fired

    def test_turnover_none_does_not_fire(self):
        """float_turnover_20d=None (Phase A default when no float data) →
        no false positive"""
        state = {
            "float_turnover_20d": None,
            "ret_20d": 0.03,
            "position_in_range": 0.30,
        }
        result = resolve_conflict_matrix(state)
        rules_fired = [r["rule"] for r in result.resolved_by]
        assert "high_turnover_low_position_calm" not in rules_fired

    def test_turnover_low_pos_with_big_move_does_not_fire_low_pos_rule(self):
        """ret_20d=20% violates abs(ret_20d)<10% guard → low_pos_calm rule does NOT fire"""
        state = {
            "float_turnover_20d": 2.0,
            "ret_20d": 0.20,  # too big — ineligible
            "position_in_range": 0.30,
        }
        result = resolve_conflict_matrix(state)
        rules_fired = [r["rule"] for r in result.resolved_by]
        assert "high_turnover_low_position_calm" not in rules_fired


# ============================================================
# 4. Confidence tier system
# ============================================================
class TestConfidenceTier:
    def test_classify_confidence_tier_high_requires_3_rules_and_70pct(self):
        assert _classify_confidence_tier(3, 70) == "HIGH"
        assert _classify_confidence_tier(5, 80) == "HIGH"
        assert _classify_confidence_tier(3, 100) == "HIGH"

    def test_classify_confidence_tier_medium(self):
        assert _classify_confidence_tier(2, 60) == "MEDIUM"
        assert _classify_confidence_tier(2, 100) == "MEDIUM"
        # 3 rules but agreement only 60% → MEDIUM (not HIGH)
        assert _classify_confidence_tier(3, 60) == "MEDIUM"
        # 3 rules with agreement 69% → still MEDIUM
        assert _classify_confidence_tier(3, 69) == "MEDIUM"

    def test_classify_confidence_tier_low_for_single_rule(self):
        """Single-rule output should NEVER be HIGH (the core hygiene fix)."""
        assert _classify_confidence_tier(1, 100) == "LOW"
        assert _classify_confidence_tier(1, 50) == "LOW"
        assert _classify_confidence_tier(1, 0) == "LOW"

    def test_classify_confidence_tier_low_for_low_agreement(self):
        # 2 rules, 50% agreement → LOW
        assert _classify_confidence_tier(2, 50) == "LOW"
        # 0 rules → LOW
        assert _classify_confidence_tier(0, 0) == "LOW"

    def test_resolve_conflict_emits_new_fields(self):
        state = {"move_maturity": "EXHAUSTED"}
        result = resolve_conflict_matrix(state)
        d = result.to_dict()
        assert "rule_agreement_pct" in d
        assert "evidence_depth_count" in d
        assert "confidence_tier" in d
        # Single rule → LOW
        assert d["confidence_tier"] == "LOW"
        assert d["evidence_depth_count"] == 1
        assert d["rule_agreement_pct"] == 100  # 1/1 agree

    def test_no_rules_fire_returns_low(self):
        state = {}  # nothing
        result = resolve_conflict_matrix(state)
        assert result.dominant_read == "UNCLEAR"
        assert result.confidence_tier == "LOW"
        assert result.evidence_depth_count == 0
        assert result.rule_agreement_pct == 0

    def test_multi_rule_high_agreement_yields_medium_or_high(self):
        """Multiple agreeing rules → MEDIUM or HIGH, never LOW"""
        state = {
            "float_turnover_20d": 2.0,
            "ret_20d": 0.05,
            "position_in_range": 0.25,
            "move_maturity": "EARLY",
            "price_pinning_score": 75,  # pinning_with_low_position
        }
        result = resolve_conflict_matrix(state)
        # All three rules → ACCUMULATION
        assert result.dominant_read == "ACCUMULATION"
        assert result.evidence_depth_count >= 2
        assert result.confidence_tier in ("MEDIUM", "HIGH")
        assert result.rule_agreement_pct == 100

    def test_legacy_confidence_field_still_present(self):
        """Backward-compat: numeric `confidence` field still in output."""
        state = {"move_maturity": "EXHAUSTED"}
        result = resolve_conflict_matrix(state)
        d = result.to_dict()
        assert "confidence" in d
        assert isinstance(d["confidence"], int)


# ============================================================
# 5. End-to-end integration with score_symbol
# ============================================================
class TestPhaseA6Integration:
    def test_score_symbol_emits_universe_tier(self):
        df = _ohlcv([10.0 + 0.05*i for i in range(80)], np.full(80, 500_000.0))
        m = {"symbol": "TEST",
             "market_cap": 1_500_000_000, "free_float": 0.4,
             "revenue": 600_000_000, "shares": 60_000_000}
        result = score_symbol(m, df=df)
        assert hasattr(result, "universe_tier")
        assert result.universe_tier in ("core", "extended", "institutional", "no_data")

    def test_extended_tier_reaches_full_scoring(self):
        """3-15B float mcap is now eligible (was rejected pre-A.6)."""
        df = _ohlcv([10.0 + 0.05*i for i in range(80)], np.full(80, 1_000_000.0))
        # 12B × 0.5 = 6B float → extended tier
        m = {"symbol": "EXT_TEST",
             "market_cap": 12_000_000_000, "free_float": 0.5,
             "revenue": 5_000_000_000, "shares": 1_000_000_000}
        result = score_symbol(m, df=df)
        assert result.eligible is True
        assert result.universe_tier == "extended"
        # Phase A modules should still populate
        assert result.move_maturity is not None
        assert result.evidence_layer is not None

    def test_institutional_tier_rejected_with_clear_reason(self):
        df = _ohlcv([10.0] * 80, np.full(80, 1_000_000.0))
        m = {"symbol": "BIG", "market_cap": 100_000_000_000, "free_float": 0.5,
             "revenue": 5e10, "shares": 1e10}
        result = score_symbol(m, df=df)
        assert result.eligible is False
        assert result.universe_tier == "institutional"
        assert "institutional" in (result.reject_reason or "").lower()

    def test_score_symbol_conflict_has_phase_a6_fields(self):
        df = _ohlcv([10.0 + 0.05*i for i in range(80)], np.full(80, 500_000.0))
        m = {"symbol": "TEST",
             "market_cap": 1_500_000_000, "free_float": 0.4,
             "revenue": 600_000_000, "shares": 60_000_000}
        result = score_symbol(m, df=df)
        if result.eligible and result.engine_conflict_matrix:
            cm = result.engine_conflict_matrix
            assert "rule_agreement_pct" in cm
            assert "evidence_depth_count" in cm
            assert "confidence_tier" in cm

    def test_score_symbol_exposes_float_turnover_in_metrics(self):
        df = _ohlcv([10.0] * 80, np.full(80, 5_000_000.0))
        m = {"symbol": "TEST",
             "market_cap": 1_500_000_000, "free_float": 0.4,
             "revenue": 600_000_000, "shares": 60_000_000}
        result = score_symbol(m, df=df)
        if result.eligible:
            # float_turnover_20d should be exposed in metrics
            assert "float_turnover_20d" in result.metrics
            t = result.metrics["float_turnover_20d"]
            # 5M daily × 20d = 100M cumvol; floating = 60M × 0.4 = 24M
            # turnover = 100/24 ≈ 4.17
            assert t is not None
            assert 3.0 < t < 5.0

    def test_single_rule_output_is_LOW_confidence_tier(self):
        """The core hygiene fix: a single fired rule must NOT show HIGH."""
        # A late-stage move with high position fires only late_maturity_distribution_lean
        df_pumped = _ohlcv(np.linspace(10, 16, 80), np.full(80, 1_000_000.0))
        m = {"symbol": "PUMP",
             "market_cap": 1_500_000_000, "free_float": 0.3,
             "revenue": 600_000_000, "shares": 60_000_000}
        result = score_symbol(m, df=df_pumped)
        if result.eligible and result.engine_conflict_matrix:
            cm = result.engine_conflict_matrix
            depth = cm.get("evidence_depth_count", 0)
            tier = cm.get("confidence_tier", "")
            if depth == 1:
                assert tier == "LOW", \
                    f"Single-rule depth={depth} must be LOW, got {tier}"

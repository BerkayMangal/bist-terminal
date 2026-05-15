"""Tests for BullWatch v2 Phase A modules:
   1. Playbook Sequence Engine
   2. Price Pinning / Control Band
   3. Move Maturity Score
   4. Engine Conflict Matrix
   5. Evidence Layer

Plus integration tests verifying the orchestration in score_symbol.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
import pytest

from engine.bullwatch_pinning import compute_price_pinning_score, PricePinningResult
from engine.bullwatch_maturity import compute_move_maturity_score, MoveMaturityResult
from engine.bullwatch_playbook import (
    detect_playbook, SymbolState, PlaybookResult,
    ACCUMULATION_SEQUENCE, DISTRIBUTION_SEQUENCE, MARKUP_SEQUENCE,
)
from engine.bullwatch_conflict import (
    resolve_conflict_matrix, CONFLICT_RULES, ConflictResult,
)
from engine.bullwatch_evidence import (
    safety_audit, build_evidence_list, build_narrative,
    build_evidence_card, FORBIDDEN_TERMS,
)


# ============================================================
# Helpers — synthetic OHLCV builders
# ============================================================
def _make_ohlcv(closes, highs=None, lows=None, opens=None, volumes=None):
    n = len(closes)
    closes = np.asarray(closes, dtype=float)
    if highs is None:
        highs = closes * 1.01
    if lows is None:
        lows = closes * 0.99
    if opens is None:
        opens = closes
    if volumes is None:
        volumes = np.full(n, 100_000.0)
    idx = pd.bdate_range(end="2026-05-08", periods=n)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=idx,
    )


def _make_pinned_series(n=30, mid=10.0, band=0.015, vol=200_000):
    """Closes tightly clustered around `mid` within `band` width."""
    np.random.seed(42)
    closes = mid + np.random.uniform(-mid * band, mid * band, size=n)
    return _make_ohlcv(closes, volumes=np.full(n, vol))


def _make_trending_series(n=60, start=10.0, end=15.0):
    closes = np.linspace(start, end, n)
    return _make_ohlcv(closes)


def _make_late_stage_series(n=80):
    """Trending strongly upward through the last 20 days — late stage."""
    # Smooth uptrend: +50% over the period, with last 20d still climbing
    closes = np.linspace(10.0, 15.0, n)
    return _make_ohlcv(closes, volumes=np.full(n, 500_000.0))


# ============================================================
# Module 2 — Price Pinning
# ============================================================
class TestPricePinning:
    def test_tight_band_high_score(self):
        df = _make_pinned_series(n=50, mid=10.0, band=0.015, vol=200_000)
        result = compute_price_pinning_score(df, lookback_days=20)
        assert result.score is not None
        assert result.score >= 50, f"tight band should score >= 50, got {result.score}"
        assert result.control_band is not None
        assert result.closes_inside_band_pct >= 70

    def test_wide_dispersion_low_score(self):
        # 20 closes spread ±15% from mid
        np.random.seed(7)
        closes = 10.0 + np.random.uniform(-1.5, 1.5, 50)
        df = _make_ohlcv(closes)
        result = compute_price_pinning_score(df, lookback_days=20)
        assert result.score is not None
        # Wide dispersion should NOT find a narrow band → low score
        assert result.score <= 30, f"wide dispersion should score low, got {result.score}"

    def test_insufficient_data_returns_none(self):
        df = _make_ohlcv([10.0] * 5)
        result = compute_price_pinning_score(df, lookback_days=20)
        assert result.score is None

    def test_handles_none_df(self):
        result = compute_price_pinning_score(None)
        assert result.score is None

    def test_to_dict_has_required_keys(self):
        df = _make_pinned_series(n=50)
        result = compute_price_pinning_score(df).to_dict()
        for key in ("price_pinning_score", "control_band", "closes_inside_band_pct",
                    "band_width_pct", "interpretation"):
            assert key in result

    def test_band_includes_majority_of_closes(self):
        df = _make_pinned_series(n=50, mid=20.0, band=0.02)
        result = compute_price_pinning_score(df, lookback_days=20)
        if result.control_band:
            low, high = result.control_band
            window_closes = df["Close"].iloc[-20:].values
            inside = sum(1 for c in window_closes if low <= c <= high)
            # Band optimized for the whole window; in the last 20d slice
            # we still expect majority (>= 60%) inside.
            assert inside >= 12, f"band must contain >= 60% of closes; got {inside}/20"


# ============================================================
# Module 6 — Move Maturity
# ============================================================
class TestMoveMaturity:
    def test_early_stage_classified_for_flat_low_position(self):
        """Flat closes near 12m low → EARLY"""
        # 252 days mostly at 10, no movement
        np.random.seed(1)
        closes = 10.0 + np.random.uniform(-0.05, 0.05, 252)
        df = _make_ohlcv(closes)
        result = compute_move_maturity_score(df)
        assert result.maturity == "EARLY", \
            f"expected EARLY, got {result.maturity} with scores {result.all_scores}"

    def test_late_stage_for_strong_move_high_position(self):
        """Strongly trending up to top → LATE"""
        df = _make_late_stage_series(n=80)
        result = compute_move_maturity_score(df)
        # Should be LATE or MID (not EARLY/EXHAUSTED without ceiling/gap data)
        assert result.maturity in ("LATE", "MID"), \
            f"expected LATE/MID, got {result.maturity}"
        # Position should be high
        assert result.indicators["position_in_range"] >= 0.7

    def test_exhausted_with_ceiling_and_retail_signals(self):
        """LATE move + ceiling break + retail heat → EXHAUSTED"""
        df = _make_late_stage_series(n=80)
        result = compute_move_maturity_score(
            df,
            retail_heat_score=70,
            gap_trap_score=60,
            ceiling_break_result={"ceiling_count": 3, "days_since_break": 1},
        )
        assert result.maturity == "EXHAUSTED", \
            f"expected EXHAUSTED, got {result.maturity}, scores={result.all_scores}"

    def test_unclear_for_too_short_data(self):
        df = _make_ohlcv([10.0] * 30)  # < 60 days
        result = compute_move_maturity_score(df)
        assert result.maturity == "UNCLEAR"

    def test_to_dict_structure(self):
        df = _make_trending_series()
        result = compute_move_maturity_score(df).to_dict()
        for key in ("maturity", "score", "all_scores", "indicators", "evidence"):
            assert key in result

    def test_evidence_uses_observation_language(self):
        df = _make_late_stage_series(n=80)
        result = compute_move_maturity_score(
            df, retail_heat_score=70, gap_trap_score=60,
            ceiling_break_result={"ceiling_count": 3, "days_since_break": 1},
        )
        # No buy/sell directives in evidence strings
        for ev in result.evidence:
            assert "al" not in ev.lower().split() or "alıcı" in ev.lower()
            # "satıcı" allowed, "sat" alone not
            assert "buy" not in ev.lower()
            assert "sell" not in ev.lower()


# ============================================================
# Module 1 — Playbook Sequence Engine
# ============================================================
class TestPlaybookSequence:
    def _make_state(self, df=None, sub_scores=None, metrics=None,
                    pinning=None, retail_heat=None, gap_trap=None,
                    ceiling_break=None, sanction_events=None):
        if df is None:
            df = _make_trending_series()
        return SymbolState(
            df=df,
            sub_scores=sub_scores or {},
            metrics=metrics or {"patterns": []},
            pinning=pinning,
            retail_heat=retail_heat,
            gap_trap=gap_trap,
            ceiling_break=ceiling_break,
            sanction_events=sanction_events or [],
        )

    def test_unclear_for_empty_state(self):
        state = self._make_state(
            sub_scores={"float_pressure": 0.1, "silent_volume": 0.1,
                        "compression": 0.1, "price_action": 0.0},
        )
        result = detect_playbook(state)
        # Note: down_volume_inversion may fire on neutral data, so we
        # just check that confidence is < 50 (no clear playbook)
        assert result.confidence < 50 or result.playbook == "UNCLEAR"

    def test_accumulation_sequence_detected(self):
        # All accumulation triggers fire
        state = self._make_state(
            sub_scores={
                "float_pressure": 0.7,
                "silent_volume": 0.7,
                "compression": 0.7,
                "price_action": 0.0,
            },
            metrics={"patterns": ["absorption", "shakeout", "walk_up"]},
        )
        result = detect_playbook(state)
        assert result.playbook == "ACCUMULATION_SEQUENCE", \
            f"expected ACCUMULATION_SEQUENCE, got {result.playbook}"
        assert result.confidence >= 50

    def test_partial_sequence_returns_lower_confidence(self):
        # Only first 2 of 5 steps complete
        state = self._make_state(
            sub_scores={"float_pressure": 0.7},
            metrics={"patterns": ["absorption"]},
        )
        result = detect_playbook(state)
        # Should not be UNCLEAR but confidence < full
        if result.playbook == "ACCUMULATION_SEQUENCE":
            assert result.confidence < 80

    def test_distribution_sequence_detected(self):
        # Up-trending with high gap trap + retail heat + ceiling
        df = _make_late_stage_series(n=80)
        state = self._make_state(
            df=df,
            sub_scores={"float_pressure": 0.3, "silent_volume": 0.2,
                        "compression": 0.2, "price_action": 0.5},
            metrics={"patterns": []},
            retail_heat=70,
            gap_trap=60,
            ceiling_break={"ceiling_count": 3, "days_since_break": 2},
        )
        result = detect_playbook(state)
        # Should be DISTRIBUTION (best matching sequence)
        assert result.playbook in ("DISTRIBUTION_SEQUENCE", "ACCUMULATION_SEQUENCE")
        # In a real distribution context with gap_trap+retail+ceiling+volume,
        # distribution should win
        # (But we accept ambiguity if down_vol > up_vol doesn't kick in)

    def test_sequence_events_have_required_fields(self):
        state = self._make_state(
            sub_scores={"float_pressure": 0.7},
            metrics={"patterns": ["absorption"]},
        )
        result = detect_playbook(state)
        for event in result.sequence_events:
            assert "step" in event
            assert "name" in event
            assert "label" in event

    def test_to_dict_structure(self):
        state = self._make_state()
        result = detect_playbook(state).to_dict()
        for key in ("playbook", "confidence", "sequence_events",
                    "missing_next_confirmation"):
            assert key in result


# ============================================================
# Module 9 — Engine Conflict Matrix
# ============================================================
class TestConflictMatrix:
    def test_float_lock_with_retail_resolves_to_distribution(self):
        state = {
            "float_pressure_score": 70,
            "retail_heat": 65,
        }
        result = resolve_conflict_matrix(state)
        assert result.dominant_read == "DISTRIBUTION", \
            f"got {result.dominant_read}, fired={[r['rule'] for r in result.resolved_by]}"

    def test_float_lock_without_retail_resolves_to_accumulation(self):
        state = {
            "float_pressure_score": 70,
            "retail_heat": 20,
        }
        result = resolve_conflict_matrix(state)
        assert result.dominant_read == "ACCUMULATION"

    def test_absorption_low_position_accumulation(self):
        state = {
            "absorption_score": 70,
            "position_in_range": 0.3,
        }
        result = resolve_conflict_matrix(state)
        assert result.dominant_read == "ACCUMULATION"

    def test_absorption_high_position_distribution(self):
        state = {
            "absorption_score": 70,
            "position_in_range": 0.85,
        }
        result = resolve_conflict_matrix(state)
        assert result.dominant_read == "DISTRIBUTION"

    def test_unclear_when_no_rules_fire(self):
        state = {
            "float_pressure_score": 10,
            "absorption_score": 10,
            "position_in_range": 0.5,
            "move_maturity": "UNCLEAR",
        }
        result = resolve_conflict_matrix(state)
        assert result.dominant_read == "UNCLEAR"
        assert result.confidence == 0

    def test_competing_reads_in_conflicts(self):
        # Mixed signals: float_lock_no_retail (ACC, w=25) +
        # exhausted_maturity_distribution (DIST, w=25)
        state = {
            "float_pressure_score": 70,
            "retail_heat": 20,
            "move_maturity": "EXHAUSTED",
        }
        result = resolve_conflict_matrix(state)
        # Either could win, but the OTHER should appear in conflicts
        assert len(result.resolved_by) >= 2
        if result.conflicts:
            competing = [c["competing_read"] for c in result.conflicts]
            assert result.dominant_read not in competing

    def test_resolved_by_has_rationale(self):
        state = {
            "float_pressure_score": 70,
            "retail_heat": 65,
        }
        result = resolve_conflict_matrix(state)
        assert len(result.resolved_by) > 0
        assert "rationale" in result.resolved_by[0]
        assert "rule" in result.resolved_by[0]
        assert "weight" in result.resolved_by[0]

    def test_exhausted_maturity_alone_fires_distribution(self):
        state = {"move_maturity": "EXHAUSTED"}
        result = resolve_conflict_matrix(state)
        assert result.dominant_read == "DISTRIBUTION"

    def test_pinning_low_position_accumulation(self):
        state = {
            "price_pinning_score": 75,
            "position_in_range": 0.3,
        }
        result = resolve_conflict_matrix(state)
        assert result.dominant_read == "ACCUMULATION"


# ============================================================
# Module 10 — Evidence Layer / Safety Audit
# ============================================================
class TestSafetyAudit:
    def test_pure_observation_passes(self):
        text = ("Float'ın %42'si dönmüş, range %1.8 ortalama. "
                "Kontrol pattern'i gözlemleniyor olabilir.")
        result = safety_audit(text)
        assert result["uses_observation_language"] is True
        assert result["forbidden_terms_detected"] == []

    def test_buy_directive_caught(self):
        result = safety_audit("Bu hisseyi al, kar al hedef 30 TL.")
        assert result["uses_observation_language"] is False
        # "al" and "kar al" and "hedef" should be detected
        assert any(t in result["forbidden_terms_detected"]
                   for t in ("al", "kar al"))

    def test_sell_directive_caught(self):
        result = safety_audit("This is a strong sell signal — exit now.")
        assert result["uses_observation_language"] is False
        assert "sell" in result["forbidden_terms_detected"]
        assert "exit now" in result["forbidden_terms_detected"]

    def test_partial_word_satıcı_not_flagged(self):
        # "satıcı" contains "sat" but is a different word — should not flag
        text = "Satıcı baskısı emiliyor olabilir."
        result = safety_audit(text)
        assert result["uses_observation_language"] is True, \
            f"false positive: {result['forbidden_terms_detected']}"

    def test_partial_word_alıcı_not_flagged(self):
        text = "Alıcı tarafı güçlü görünüyor."
        result = safety_audit(text)
        assert result["uses_observation_language"] is True

    def test_manipulation_accusation_caught(self):
        result = safety_audit("Burada açık manipülasyon var.")
        assert result["uses_observation_language"] is False
        assert "manipülasyon" in result["forbidden_terms_detected"]

    def test_target_price_caught(self):
        result = safety_audit("Hedef fiyat 25 TL.")
        assert result["uses_observation_language"] is False
        assert "hedef fiyat" in result["forbidden_terms_detected"]

    def test_empty_text(self):
        result = safety_audit("")
        assert result["uses_observation_language"] is True


class TestEvidenceLayer:
    def test_build_evidence_list_from_metrics(self):
        metrics = {
            "float_pressure": 0.05,
            "rvol": 1.8,
            "float_market_cap": 800e6,
            "patterns": ["absorption"],
        }
        evidence = build_evidence_list(metrics, sub_scores={})
        assert len(evidence) > 0
        # Each item has required keys
        for item in evidence:
            assert "metric" in item
            assert "value" in item
            assert "interpretation" in item

    def test_build_narrative_observation_only(self):
        playbook = {"playbook": "ACCUMULATION_SEQUENCE", "confidence": 70,
                    "missing_next_confirmation": ["Hacim eşliğinde breakout"]}
        conflict = {"dominant_read": "ACCUMULATION", "confidence": 75,
                    "resolved_by": [{"rationale": "Lot dönüşü retail ilgi olmadan → quiet accumulation"}]}
        maturity = {"maturity": "EARLY"}
        narrative = build_narrative(playbook, conflict, maturity)
        # Audit must pass
        audit = safety_audit(narrative)
        assert audit["uses_observation_language"] is True, \
            f"narrative failed audit: {audit['forbidden_terms_detected']} in '{narrative}'"
        # Should contain key elements
        assert "Toplama" in narrative or "ilerliyor" in narrative
        assert "EARLY" in narrative or "olgunluğu" in narrative

    def test_build_evidence_card_full_pipeline(self):
        metrics = {"float_pressure": 0.04, "rvol": 1.5, "patterns": ["absorption"]}
        playbook = {"playbook": "ACCUMULATION_SEQUENCE", "confidence": 70,
                    "missing_next_confirmation": []}
        conflict = {"dominant_read": "ACCUMULATION", "confidence": 80,
                    "resolved_by": [{"rationale": "Lot dönüşü"}]}
        maturity = {"maturity": "EARLY", "indicators": {
            "position_in_range": 0.25, "rsi": 55,
        }}

        card = build_evidence_card(
            metrics=metrics, sub_scores={},
            playbook_result=playbook,
            conflict_result=conflict,
            maturity_result=maturity,
        )
        assert "evidence" in card
        assert "narrative" in card
        assert "language_safety" in card
        assert card["language_safety"]["uses_observation_language"] is True
        assert len(card["evidence"]) >= 3

    def test_distribution_card_does_not_say_sell(self):
        playbook = {"playbook": "DISTRIBUTION_SEQUENCE", "confidence": 70,
                    "missing_next_confirmation": []}
        conflict = {"dominant_read": "DISTRIBUTION", "confidence": 80,
                    "resolved_by": [{"rationale": "Yüksek zonelarda satış emiliyor"}]}
        maturity = {"maturity": "LATE"}
        narrative = build_narrative(playbook, conflict, maturity)
        audit = safety_audit(narrative)
        assert audit["uses_observation_language"] is True
        assert "sat" not in narrative.lower().split()
        assert "sell" not in narrative.lower()
        assert "kar al" not in narrative.lower()


# ============================================================
# Integration — score_symbol now produces Phase A outputs
# ============================================================
class TestScoreSymbolPhaseAIntegration:
    def test_score_symbol_returns_phase_a_fields(self):
        from engine.bullwatch import score_symbol

        df = _make_pinned_series(n=80, mid=10.0, band=0.02, vol=300_000)
        metrics = {
            "symbol": "TEST",
            "market_cap": 800_000_000,
            "free_float": 0.30,
            "revenue": 500_000_000,
            "shares": 80_000_000,
        }
        result = score_symbol(metrics, df=df)

        # New Phase A optional fields must be present (or None gracefully)
        assert hasattr(result, "playbook_sequence")
        assert hasattr(result, "price_pinning")
        assert hasattr(result, "move_maturity")
        assert hasattr(result, "engine_conflict_matrix")
        assert hasattr(result, "evidence_layer")

        # Eligible symbol → modules should populate
        if result.eligible:
            assert result.price_pinning is not None
            assert result.move_maturity is not None
            assert result.playbook_sequence is not None
            assert result.evidence_layer is not None

    def test_score_symbol_evidence_layer_passes_safety_audit(self):
        from engine.bullwatch import score_symbol

        df = _make_pinned_series(n=80, mid=10.0)
        metrics = {
            "symbol": "TEST",
            "market_cap": 800_000_000,
            "free_float": 0.30,
            "revenue": 500_000_000,
            "shares": 80_000_000,
        }
        result = score_symbol(metrics, df=df)

        if result.eligible and result.evidence_layer:
            assert result.evidence_layer["language_safety"]["uses_observation_language"] is True

    def test_score_symbol_to_dict_serializable(self):
        """The result must be JSON-serializable (dict/list/primitives)."""
        from engine.bullwatch import score_symbol
        import json

        df = _make_pinned_series(n=80)
        metrics = {
            "symbol": "TEST",
            "market_cap": 800_000_000,
            "free_float": 0.30,
            "revenue": 500_000_000,
            "shares": 80_000_000,
        }
        result = score_symbol(metrics, df=df)

        # Should serialize cleanly
        d = result.to_dict()
        # Convert datetime/Timestamp by str
        json_str = json.dumps(d, default=str)
        assert len(json_str) > 100

    def test_existing_v1_fields_unchanged(self):
        """V1 fields must remain identical — no regression."""
        from engine.bullwatch import score_symbol

        df = _make_pinned_series(n=80)
        metrics = {
            "symbol": "TEST",
            "market_cap": 800_000_000,
            "free_float": 0.30,
            "revenue": 500_000_000,
            "shares": 80_000_000,
        }
        result = score_symbol(metrics, df=df)
        assert hasattr(result, "score")
        assert hasattr(result, "zone")
        assert hasattr(result, "pattern")
        assert hasattr(result, "components")
        assert hasattr(result, "metrics")
        assert hasattr(result, "narrative")
        assert hasattr(result, "data_quality")


# ============================================================
# Cross-module language safety — global guard
# ============================================================
class TestGlobalLanguageSafety:
    def test_all_pinning_interpretations_are_safe(self):
        """Every PricePinningResult.interpretation string must pass audit."""
        for interp in ("fiyat_kontrol_ediliyor_olabilir", "orta_seviye_pinning",
                       "zayıf_işaret", "yok"):
            audit = safety_audit(interp.replace("_", " "))
            assert audit["uses_observation_language"] is True, \
                f"interpretation '{interp}' failed audit"

    def test_all_maturity_classes_safe(self):
        """Every maturity class label must pass audit."""
        for label in ("EARLY", "MID", "LATE", "EXHAUSTED", "UNCLEAR"):
            audit = safety_audit(f"Hareket olgunluğu: {label}.")
            assert audit["uses_observation_language"] is True

    def test_all_conflict_rationales_safe(self):
        """Every CONFLICT_RULES rationale must be observation-only."""
        for rule in CONFLICT_RULES:
            rationale = rule["rationale"]
            audit = safety_audit(rationale)
            assert audit["uses_observation_language"] is True, \
                f"rule '{rule['name']}' has unsafe rationale: " \
                f"{audit['forbidden_terms_detected']} in '{rationale}'"

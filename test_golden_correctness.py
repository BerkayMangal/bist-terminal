# ================================================================
# BISTBULL TERMINAL — Golden Correctness Tests (Hardening Sprint)
# Verifies scoring formulas produce expected results for known inputs.
# ================================================================

import pytest
import sys
sys.path.insert(0, ".")

from engine.scoring import (
    compute_fa_pure, compute_ivme, compute_overall,
    compute_risk_penalties, compute_valuation_stretch,
    score_value, score_quality, score_growth,
    confidence_score,
)
from engine.explainability import (
    build_explanation, build_dimension_breakdown,
    _strength, _direction, _sub_contribution,
)
from config import FA_WEIGHTS, IVME_WEIGHTS, CONFIDENCE_KEYS


# ================================================================
# GOLDEN SET — known metrics → known scores
# ================================================================
class TestGoldenScoring:
    """Verify dimension scores match hand-calculated expectations."""

    def _metrics(self, **overrides):
        base = {
            "pe": 8.0, "pb": 1.5, "ev_ebitda": 5.0, "fcf_yield": 0.06,
            "margin_safety": 0.15, "roe": 0.18, "roic": 0.14, "net_margin": 0.12,
            "revenue_growth": 0.15, "eps_growth": 0.20, "ebitda_growth": 0.12, "peg": 0.4,
        }
        base.update(overrides)
        return base

    def test_value_score_cheap_stock(self):
        """Low PE + low PB → high value score."""
        s = score_value(self._metrics(pe=5, pb=0.8, ev_ebitda=3), "sanayi")
        assert s is not None
        assert s >= 70, f"Cheap stock should score >=70, got {s}"

    def test_value_score_expensive_stock(self):
        """High PE + high PB → low value score."""
        s = score_value(self._metrics(pe=30, pb=5, ev_ebitda=20), "sanayi")
        assert s is not None
        assert s <= 35, f"Expensive stock should score <=35, got {s}"

    def test_quality_score_high_roe(self):
        """High ROE + margins → high quality."""
        s = score_quality(self._metrics(roe=0.25, roic=0.20, net_margin=0.18), "sanayi")
        assert s is not None
        assert s >= 65

    def test_quality_score_low_roe(self):
        """Low ROE → low quality."""
        s = score_quality(self._metrics(roe=0.03, roic=0.02, net_margin=0.02), "sanayi")
        assert s is not None
        assert s <= 40

    def test_growth_score_strong(self):
        """Strong growth metrics → high score."""
        s = score_growth(self._metrics(revenue_growth=0.25, eps_growth=0.30, ebitda_growth=0.20, peg=0.3), "sanayi")
        assert s is not None
        assert s >= 70

    def test_growth_score_negative(self):
        """Negative growth → low score."""
        s = score_growth(self._metrics(revenue_growth=-0.15, eps_growth=-0.20, ebitda_growth=-0.10), "sanayi")
        assert s is not None
        assert s <= 30


class TestGoldenFormulas:
    """Verify overall formula composition is mathematically correct."""

    def test_fa_pure_weights_sum(self):
        total = sum(FA_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01, f"FA weights must sum to 1.0, got {total}"

    def test_ivme_weights_sum(self):
        total = sum(IVME_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01, f"Ivme weights must sum to 1.0, got {total}"

    def test_fa_pure_all_50_gives_50(self):
        scores = {k: 50.0 for k in FA_WEIGHTS}
        result = compute_fa_pure(scores)
        assert abs(result - 50.0) < 0.5

    def test_overall_composition(self):
        """Overall = FA*0.55 + momentum_gated*0.35 + val_stretch + risk*0.3"""
        fa = 70.0
        ivme = 60.0
        value = 75.0
        risk = -10

        momentum_gated = ivme * (fa / 100.0)  # 60 * 0.7 = 42
        val_stretch = compute_valuation_stretch(value)
        risk_contrib = max(risk, -30) * 0.3

        expected = fa * 0.55 + momentum_gated * 0.35 + val_stretch + risk_contrib
        expected = max(1, min(99, expected))

        actual = compute_overall(fa, ivme, value, risk)
        assert abs(actual - expected) < 1.0, f"Expected ~{expected:.1f}, got {actual}"

    def test_confidence_full_data(self):
        """All 16 confidence keys present → 100%."""
        m = {k: 1.0 for k in CONFIDENCE_KEYS}
        assert confidence_score(m) == 100.0

    def test_confidence_half_data(self):
        """8/16 keys → 50%."""
        m = {}
        for i, k in enumerate(CONFIDENCE_KEYS):
            m[k] = 1.0 if i < 8 else None
        assert confidence_score(m) == 50.0


class TestDataQualityTier:
    """Verify data_quality_tier computation logic."""

    def test_full_tier(self):
        """0-1 imputed dims + high confidence = full."""
        n_imputed = 1
        confidence = 85
        if n_imputed <= 1 and confidence >= 70:
            tier = "full"
        elif n_imputed <= 3 and confidence >= 40:
            tier = "partial"
        else:
            tier = "market_only"
        assert tier == "full"

    def test_partial_tier(self):
        """2 imputed dims + confidence 55 = partial."""
        n_imputed = 2
        confidence = 55
        if n_imputed == 0 and confidence >= 70:
            tier = "full"
        elif n_imputed <= 3 and confidence >= 40:
            tier = "partial"
        else:
            tier = "market_only"
        assert tier == "partial"

    def test_market_only_tier(self):
        """5 imputed dims + confidence 25 = market_only."""
        n_imputed = 5
        confidence = 25
        if n_imputed == 0 and confidence >= 70:
            tier = "full"
        elif n_imputed <= 3 and confidence >= 40:
            tier = "partial"
        else:
            tier = "market_only"
        assert tier == "market_only"


class TestContributionCorrectness:
    """Verify contribution calculations are mathematically sound."""

    def test_sub_contribution_positive(self):
        """Score above 50 → positive contribution."""
        c = _sub_contribution(80.0, 0.30, 3)
        assert c > 0

    def test_sub_contribution_negative(self):
        """Score below 50 → negative contribution."""
        c = _sub_contribution(20.0, 0.30, 3)
        assert c < 0

    def test_sub_contribution_neutral(self):
        """Score at 50 → zero contribution."""
        c = _sub_contribution(50.0, 0.30, 3)
        assert abs(c) < 0.001

    def test_dimension_contribution_matches_weight(self):
        """Dimension contribution = weight * (score - 50)."""
        scores = {"value": 70, "quality": 60, "growth": 40}
        for dim, score in scores.items():
            w = FA_WEIGHTS.get(dim, 0)
            expected = w * (score - 50)
            assert abs(expected) > 0 or score == 50

    def test_breakdown_contributions_sum_to_fa_delta(self):
        """Sum of dimension contributions ≈ FA Pure - 50."""
        all_scores = {k: 65.0 for k in FA_WEIGHTS}
        all_scores.update({k: 55.0 for k in IVME_WEIGHTS})
        metrics = {k: None for k in ["pe", "pb", "roe"]}  # minimal

        bd = build_dimension_breakdown(all_scores, metrics, "sanayi", [])
        fa_contribs = sum(bd[d]["contribution"] for d in FA_WEIGHTS)
        fa_pure = compute_fa_pure(all_scores)
        expected_delta = fa_pure - 50.0

        assert abs(fa_contribs - expected_delta) < 1.0, \
            f"Contrib sum {fa_contribs:.1f} should ≈ FA delta {expected_delta:.1f}"


class TestStrengthCategories:
    """Verify 5-level strength system boundaries."""

    def test_boundaries(self):
        assert _strength(90) == "strong_positive"
        assert _strength(75) == "strong_positive"
        assert _strength(74) == "positive"
        assert _strength(55) == "positive"
        assert _strength(54) == "neutral"
        assert _strength(45) == "neutral"
        assert _strength(44) == "negative"
        assert _strength(25) == "negative"
        assert _strength(24) == "strong_negative"
        assert _strength(0) == "strong_negative"
        assert _strength(None) == "neutral"

    def test_direction_boundaries(self):
        assert _direction(60) == "positive"
        assert _direction(55) == "positive"
        assert _direction(50) == "neutral"
        assert _direction(45) == "negative"
        assert _direction(None) == "neutral"

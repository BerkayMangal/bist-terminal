"""Phase 7 — Composite bucket calibration scaffold tests.

Verify the decomposition framework is correctly structured:
  - All 3 composite buckets have specs
  - Derived formulae compute correctly
  - Branched scorers match V13 thresholds (regression guard)
  - Composite dispatcher returns values in [5, 100] band
"""

from __future__ import annotations

import pytest


# ==========================================================================
# Composite bucket specs
# ==========================================================================

class TestCompositeBucketSpecs:
    def test_three_composite_buckets(self):
        from engine.scoring_calibrated_composites import COMPOSITE_BUCKETS
        assert set(COMPOSITE_BUCKETS.keys()) == {
            "earnings_quality", "moat", "capital_efficiency",
        }

    def test_each_bucket_has_components(self):
        from engine.scoring_calibrated_composites import COMPOSITE_BUCKETS
        for bucket, components in COMPOSITE_BUCKETS.items():
            assert len(components) >= 2, f"{bucket} has fewer than 2 components"

    def test_each_component_has_type(self):
        from engine.scoring_calibrated_composites import COMPOSITE_BUCKETS
        valid_types = {"raw", "derived", "branched"}
        for bucket, components in COMPOSITE_BUCKETS.items():
            for comp, spec in components.items():
                assert spec.get("type") in valid_types, \
                    f"{bucket}/{comp}: invalid type {spec.get('type')!r}"

    def test_earnings_quality_has_beneish_branched(self):
        from engine.scoring_calibrated_composites import EARNINGS_QUALITY_COMPONENTS
        assert EARNINGS_QUALITY_COMPONENTS["beneish_m"]["type"] == "branched"

    def test_moat_has_at_trend_branched(self):
        from engine.scoring_calibrated_composites import MOAT_COMPONENTS
        assert MOAT_COMPONENTS["at_trend"]["type"] == "branched"

    def test_capital_has_dilution_branched(self):
        from engine.scoring_calibrated_composites import CAPITAL_EFFICIENCY_COMPONENTS
        assert CAPITAL_EFFICIENCY_COMPONENTS["dilution"]["type"] == "branched"


# ==========================================================================
# Derived formula computation
# ==========================================================================

class TestDerivedFormulae:
    def test_abs_delta_gm_stability(self):
        from engine.scoring_calibrated_composites import compute_derived
        m = {"gross_margin": 0.30, "gross_margin_prev": 0.28}
        # |0.30 - 0.28| = 0.02
        result = compute_derived("gm_stability", m)
        assert result == pytest.approx(0.02, abs=0.0001)

    def test_abs_delta_roa_stability(self):
        from engine.scoring_calibrated_composites import compute_derived
        m = {"roa": 0.05, "roa_prev": 0.10}
        # |0.05 - 0.10| = 0.05
        result = compute_derived("roa_stability", m)
        assert result == pytest.approx(0.05, abs=0.0001)

    def test_abs_delta_missing_returns_none(self):
        from engine.scoring_calibrated_composites import compute_derived
        m = {"gross_margin": 0.30}  # prev missing
        assert compute_derived("gm_stability", m) is None

    def test_capex_ratio(self):
        from engine.scoring_calibrated_composites import compute_derived
        m = {"operating_cf": 200, "free_cf": 100, "revenue": 1000}
        # capex = |200 - 100| = 100, ratio = 100/1000 = 0.10
        result = compute_derived("capex_to_rev", m)
        assert result == pytest.approx(0.10, abs=0.0001)

    def test_capex_ratio_zero_revenue_returns_none(self):
        from engine.scoring_calibrated_composites import compute_derived
        m = {"operating_cf": 200, "free_cf": 100, "revenue": 0}
        assert compute_derived("capex_to_rev", m) is None

    def test_unknown_component_returns_none(self):
        from engine.scoring_calibrated_composites import compute_derived
        m = {"some_metric": 1.0}
        assert compute_derived("nonexistent", m) is None

    def test_raw_component_not_derived(self):
        """Raw components (e.g. cfo_to_ni) are not 'derived' — direct lookup."""
        from engine.scoring_calibrated_composites import compute_derived
        m = {"cfo_to_ni": 1.5}
        # cfo_to_ni is type='raw', not 'derived' — function returns None
        assert compute_derived("cfo_to_ni", m) is None


# ==========================================================================
# Branched scorers (V13 logic regression guards)
# ==========================================================================

class TestBranchedBeneishM:
    def test_low_m_high_score(self):
        """M < -2.22 → 90 (clean earnings)."""
        from engine.scoring_calibrated_composites import score_branched_beneish_m
        assert score_branched_beneish_m(-3.0) == 90.0
        assert score_branched_beneish_m(-2.5) == 90.0

    def test_mid_m_mid_score(self):
        """-2.22 <= M < -1.78 → 65 (borderline)."""
        from engine.scoring_calibrated_composites import score_branched_beneish_m
        assert score_branched_beneish_m(-2.0) == 65.0
        assert score_branched_beneish_m(-1.79) == 65.0

    def test_high_m_low_score(self):
        """M >= -1.78 → 25 (manipulation risk)."""
        from engine.scoring_calibrated_composites import score_branched_beneish_m
        assert score_branched_beneish_m(-1.0) == 25.0
        assert score_branched_beneish_m(0.0) == 25.0
        assert score_branched_beneish_m(2.0) == 25.0

    def test_none_returns_none(self):
        from engine.scoring_calibrated_composites import score_branched_beneish_m
        assert score_branched_beneish_m(None) is None


class TestBranchedAtTrend:
    def test_stable_trend(self):
        """|Δ| < 0.02 → 55 (stable)."""
        from engine.scoring_calibrated_composites import score_branched_at_trend
        assert score_branched_at_trend(1.0, 1.01) == 55.0  # Δ = -0.01
        assert score_branched_at_trend(1.0, 1.0) == 55.0   # Δ = 0

    def test_improving_trend(self):
        """Δ >= 0.02 → 75 (improving)."""
        from engine.scoring_calibrated_composites import score_branched_at_trend
        assert score_branched_at_trend(1.10, 1.00) == 75.0

    def test_declining_trend(self):
        """Δ <= -0.02 → 35 (declining)."""
        from engine.scoring_calibrated_composites import score_branched_at_trend
        assert score_branched_at_trend(1.00, 1.10) == 35.0

    def test_missing_returns_none(self):
        from engine.scoring_calibrated_composites import score_branched_at_trend
        assert score_branched_at_trend(None, 1.0) is None
        assert score_branched_at_trend(1.0, None) is None


class TestBranchedDilution:
    def test_no_dilution_max_score(self):
        """share_change <= 0 → 100 (buyback or stable)."""
        from engine.scoring_calibrated_composites import score_branched_dilution
        assert score_branched_dilution(0) == 100.0
        assert score_branched_dilution(-0.05) == 100.0

    def test_mild_dilution(self):
        """0 < sc <= 0.03 → 70."""
        from engine.scoring_calibrated_composites import score_branched_dilution
        assert score_branched_dilution(0.02) == 70.0

    def test_moderate_dilution(self):
        """0.03 < sc <= 0.08 → 45."""
        from engine.scoring_calibrated_composites import score_branched_dilution
        assert score_branched_dilution(0.05) == 45.0

    def test_heavy_dilution(self):
        """sc > 0.20 → 5."""
        from engine.scoring_calibrated_composites import score_branched_dilution
        assert score_branched_dilution(0.50) == 5.0

    def test_none_returns_none(self):
        from engine.scoring_calibrated_composites import score_branched_dilution
        assert score_branched_dilution(None) is None


# ==========================================================================
# Composite bucket dispatcher
# ==========================================================================

class TestCompositeBucketDispatcher:
    def test_score_earnings_with_branched_only(self):
        from engine.scoring_calibrated_composites import score_composite_bucket
        # Only branched component present (beneish_m)
        m = {"beneish_m": -3.0}
        s = score_composite_bucket("earnings_quality", m)
        assert s == 90.0  # only beneish_m=90 contributes

    def test_score_moat_branched_components(self):
        from engine.scoring_calibrated_composites import score_composite_bucket
        # Only at_trend branched component present
        m = {"asset_turnover": 1.10, "asset_turnover_prev": 1.00}
        s = score_composite_bucket("moat", m)
        assert s == 75.0  # improving trend

    def test_score_capital_branched_components(self):
        from engine.scoring_calibrated_composites import score_composite_bucket
        m = {"share_change": 0}  # no dilution
        s = score_composite_bucket("capital_efficiency", m)
        assert s == 100.0

    def test_unknown_bucket_returns_none(self):
        from engine.scoring_calibrated_composites import score_composite_bucket
        s = score_composite_bucket("nonexistent_bucket", {})
        assert s is None

    def test_no_components_returns_none(self):
        from engine.scoring_calibrated_composites import score_composite_bucket
        s = score_composite_bucket("earnings_quality", {})
        assert s is None  # no metrics available

    def test_multiple_branched_averaged(self):
        from engine.scoring_calibrated_composites import score_composite_bucket
        # moat has 1 branched (at_trend)
        # capital has 1 branched (dilution)
        m_capital = {"share_change": 0.50}  # heavy dilution = 5
        s = score_composite_bucket("capital_efficiency", m_capital)
        # Only dilution contributed = 5
        assert s == 5.0


# ==========================================================================
# Helper: branched component count
# ==========================================================================

class TestBranchedCount:
    def test_earnings_quality_one_branched(self):
        from engine.scoring_calibrated_composites import get_branched_component_count
        # beneish_m is branched
        assert get_branched_component_count("earnings_quality") == 1

    def test_moat_one_branched(self):
        from engine.scoring_calibrated_composites import get_branched_component_count
        # at_trend is branched
        assert get_branched_component_count("moat") == 1

    def test_capital_one_branched(self):
        from engine.scoring_calibrated_composites import get_branched_component_count
        # dilution is branched
        assert get_branched_component_count("capital_efficiency") == 1

    def test_unknown_bucket_zero(self):
        from engine.scoring_calibrated_composites import get_branched_component_count
        assert get_branched_component_count("nonexistent") == 0

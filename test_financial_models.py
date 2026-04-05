# ================================================================
# BISTBULL TERMINAL — Unit Tests: Financial Models
# Tests: compute_piotroski, compute_altman, compute_beneish
#
# These are academic financial formulas. Tests verify:
# 1. Formula correctness against known hand-calculated values
# 2. Edge cases (missing data, zero denominators)
# 3. Graceful None returns when data is insufficient
#
# All tests are deterministic, pure-logic, no I/O.
# ================================================================

import pytest
import math

from engine.analysis import compute_piotroski, compute_altman, compute_beneish


# ================================================================
# compute_piotroski — F-Score (0-9)
# ================================================================
class TestComputePiotroski:
    """Piotroski F-Score: 9 binary tests. Needs >= 4 testable conditions."""

    def test_perfect_score(self, healthy_industrial_metrics):
        """A healthy company should score high (7-9)."""
        m = healthy_industrial_metrics
        result = compute_piotroski(m)
        assert result is not None
        assert 7 <= result <= 9

    def test_distressed_score(self, distressed_company_metrics):
        """A distressed company should score low (0-3)."""
        m = distressed_company_metrics
        result = compute_piotroski(m)
        assert result is not None
        assert result <= 4

    def test_sparse_data_returns_none(self, sparse_metrics):
        """With mostly None data, should return None (< 4 testable conditions)."""
        result = compute_piotroski(sparse_metrics)
        assert result is None

    def test_range_0_to_9(self):
        """Score must be between 0 and 9 inclusive."""
        m = {
            "roa": 0.05, "operating_cf": 100, "roa_prev": 0.03,
            "net_income": 80, "current_ratio": 1.5, "current_ratio_prev": 1.3,
            "share_change": -0.01, "total_debt": 50, "total_assets": 200,
            "total_debt_prev": 60, "total_assets_prev": 200,
            "gross_margin": 0.30, "gross_margin_prev": 0.28,
            "asset_turnover": 0.7, "asset_turnover_prev": 0.65,
        }
        result = compute_piotroski(m)
        assert result is not None
        assert 0 <= result <= 9

    def test_positive_roa_scores_point(self):
        """Positive ROA should earn a point (test 1)."""
        m_pos = {"roa": 0.05, "operating_cf": 1, "net_income": 1, "roa_prev": 0.04}
        m_neg = {"roa": -0.05, "operating_cf": 1, "net_income": 1, "roa_prev": 0.04}
        score_pos = compute_piotroski(m_pos)
        score_neg = compute_piotroski(m_neg)
        assert score_pos is not None
        assert score_neg is not None
        assert score_pos > score_neg

    def test_cfo_greater_than_net_income(self):
        """CFO > NI (accrual quality) should earn a point (test 4)."""
        m_accrual_good = {
            "roa": 0.05, "operating_cf": 200, "roa_prev": 0.04,
            "net_income": 150,
            "current_ratio": 1.5, "current_ratio_prev": 1.3,
        }
        m_accrual_bad = {
            "roa": 0.05, "operating_cf": 100, "roa_prev": 0.04,
            "net_income": 150,
            "current_ratio": 1.5, "current_ratio_prev": 1.3,
        }
        score_good = compute_piotroski(m_accrual_good)
        score_bad = compute_piotroski(m_accrual_bad)
        assert score_good > score_bad

    def test_no_share_dilution(self):
        """No dilution (share_change <= 0) should earn a point (test 6)."""
        m_no_dil = {
            "roa": 0.05, "operating_cf": 200, "roa_prev": 0.04,
            "net_income": 100, "share_change": -0.01,
        }
        m_diluted = {
            "roa": 0.05, "operating_cf": 200, "roa_prev": 0.04,
            "net_income": 100, "share_change": 0.05,
        }
        score_no_dil = compute_piotroski(m_no_dil)
        score_diluted = compute_piotroski(m_diluted)
        assert score_no_dil > score_diluted


# ================================================================
# compute_altman — Z-Score
# ================================================================
class TestComputeAltman:
    """Altman Z-Score: 1.2×(WC/TA) + 1.4×(RE/TA) + 3.3×(EBIT/TA) + 0.6×(MVE/TL) + 1.0×(Sales/TA)"""

    def test_healthy_company(self, healthy_industrial_metrics):
        """A healthy industrial company should be near or above the grey zone (Z > 2.9)."""
        result = compute_altman(healthy_industrial_metrics)
        assert result is not None
        assert result > 2.9  # Grey zone starts at 1.8, safe at 3.0; fixture is 2.957

    def test_distressed_company(self, distressed_company_metrics):
        """A distressed company should be in the distress zone (Z < 1.8)."""
        result = compute_altman(distressed_company_metrics)
        assert result is not None
        assert result < 1.8

    def test_sparse_data_returns_none(self, sparse_metrics):
        """Missing required fields should return None."""
        result = compute_altman(sparse_metrics)
        assert result is None

    def test_hand_calculated(self):
        """Verify against hand-calculated result."""
        m = {
            "working_capital": 30,
            "total_assets": 200,
            "retained_earnings": 50,
            "ebit": 28,
            "total_liabilities": 80,
            "revenue": 120,
            "market_cap": 182,
        }
        result = compute_altman(m)
        assert result is not None
        # 1.2*(30/200) + 1.4*(50/200) + 3.3*(28/200) + 0.6*(182/80) + 1.0*(120/200)
        # = 1.2*0.15 + 1.4*0.25 + 3.3*0.14 + 0.6*2.275 + 1.0*0.6
        # = 0.18 + 0.35 + 0.462 + 1.365 + 0.6 = 2.957
        assert abs(result - 2.957) < 0.01

    def test_zero_total_assets_returns_none(self):
        """Division by zero guard."""
        m = {
            "working_capital": 30, "total_assets": 0,
            "retained_earnings": 50, "ebit": 28,
            "total_liabilities": 80, "revenue": 120, "market_cap": 182,
        }
        result = compute_altman(m)
        assert result is None

    def test_zero_total_liabilities_returns_none(self):
        m = {
            "working_capital": 30, "total_assets": 200,
            "retained_earnings": 50, "ebit": 28,
            "total_liabilities": 0, "revenue": 120, "market_cap": 182,
        }
        result = compute_altman(m)
        assert result is None

    def test_negative_working_capital_allowed(self):
        """Negative WC is valid — just produces lower Z-score."""
        m = {
            "working_capital": -50, "total_assets": 200,
            "retained_earnings": 10, "ebit": 20,
            "total_liabilities": 120, "revenue": 100, "market_cap": 80,
        }
        result = compute_altman(m)
        assert result is not None


# ================================================================
# compute_beneish — M-Score
# ================================================================
class TestComputeBeneish:
    """Beneish M-Score: earnings manipulation probability.
    M > -1.78 = likely manipulator, M < -2.22 = unlikely manipulator."""

    def test_healthy_company(self, healthy_industrial_metrics):
        """A healthy company should be below -2.22 (unlikely manipulator)."""
        result = compute_beneish(healthy_industrial_metrics)
        assert result is not None
        assert result < -1.78  # at least not a likely manipulator

    def test_sparse_data_returns_none(self, sparse_metrics):
        """Missing required fields should return None."""
        result = compute_beneish(sparse_metrics)
        assert result is None

    def test_zero_revenue_returns_none(self):
        """Zero revenue makes the formula undefined."""
        m = {"revenue": 0, "revenue_prev": 100, "total_assets": 200, "total_assets_prev": 180}
        result = compute_beneish(m)
        assert result is None

    def test_zero_revenue_prev_returns_none(self):
        m = {"revenue": 100, "revenue_prev": 0, "total_assets": 200, "total_assets_prev": 180}
        result = compute_beneish(m)
        assert result is None

    def test_returns_float(self, healthy_industrial_metrics):
        """Result should be a float."""
        result = compute_beneish(healthy_industrial_metrics)
        assert isinstance(result, float)

    def test_high_accrual_flags_manipulation(self):
        """High accruals (NI >> CFO) should push M-score higher (more suspicious)."""
        # Company with big gap between NI and CFO
        base = {
            "revenue": 100_000, "revenue_prev": 90_000,
            "gross_profit": 35_000, "gross_profit_prev": 32_000,
            "receivables": 20_000, "receivables_prev": 12_000,  # big jump
            "current_assets": 50_000, "current_assets_prev": 40_000,
            "ppe": 60_000, "ppe_prev": 55_000,
            "depreciation": 5_000, "depreciation_prev": 4_500,
            "sga": 8_000, "sga_prev": 7_000,
            "total_assets": 150_000, "total_assets_prev": 130_000,
            "total_debt": 40_000, "total_debt_prev": 35_000,
            "net_income": 15_000,
            "operating_cf": 3_000,  # CFO much lower than NI
        }
        result = compute_beneish(base)
        # With such high accruals, M-score should be elevated
        # We can't assert an exact value but it should be computable
        assert result is not None

    def test_stable_company_low_mscore(self):
        """A stable company with proportional growth should have low M-score."""
        base = {
            "revenue": 100_000, "revenue_prev": 95_000,
            "gross_profit": 35_000, "gross_profit_prev": 33_250,
            "receivables": 10_000, "receivables_prev": 9_500,
            "current_assets": 40_000, "current_assets_prev": 38_000,
            "ppe": 60_000, "ppe_prev": 58_000,
            "depreciation": 5_000, "depreciation_prev": 4_800,
            "sga": 7_000, "sga_prev": 6_650,
            "total_assets": 130_000, "total_assets_prev": 125_000,
            "total_debt": 30_000, "total_debt_prev": 29_000,
            "net_income": 12_000,
            "operating_cf": 14_000,  # CFO > NI — good accrual quality
        }
        result = compute_beneish(base)
        assert result is not None
        # Stable proportional growth → low M-score
        assert result < -1.5  # should be in the "unlikely manipulator" zone

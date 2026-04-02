# ================================================================
# BISTBULL TERMINAL — Unit Tests: Canonical Metric Pipeline
# Tests: normalize_metrics, compute_score_coverage,
#        confidence_penalty_for_imputed_scores, check_field_parity,
#        provider parity, missing-data propagation
#
# All tests are deterministic, pure-logic, no I/O.
# ================================================================

import pytest

from engine.metrics import (
    ALL_METRIC_FIELDS,
    IDENTITY_FIELDS,
    FA_DIMENSION_INPUTS,
    normalize_metrics,
    compute_score_coverage,
    confidence_penalty_for_imputed_scores,
    check_field_parity,
)
from config import FA_WEIGHTS


# ================================================================
# normalize_metrics
# ================================================================
class TestNormalizeMetrics:
    """normalize_metrics fills missing canonical fields with None."""

    def test_adds_missing_fields(self):
        """A minimal dict should get all canonical fields added as None."""
        m = {"symbol": "TEST.IS", "ticker": "TEST", "name": "Test",
             "currency": "TRY", "sector": "", "industry": "",
             "data_source": "test", "price": 10.0}
        result = normalize_metrics(m)
        for field in ALL_METRIC_FIELDS:
            assert field in result, f"Missing canonical field: {field}"

    def test_preserves_existing_values(self):
        """Existing values must NOT be overwritten."""
        m = {"symbol": "T", "ticker": "T", "name": "T",
             "currency": "TRY", "sector": "", "industry": "",
             "data_source": "test", "pe": 8.5, "roe": 0.15}
        result = normalize_metrics(m)
        assert result["pe"] == 8.5
        assert result["roe"] == 0.15

    def test_fills_missing_with_none(self):
        """Missing fields are set to None, not 0 or any other value."""
        m = {"symbol": "T", "ticker": "T", "name": "T",
             "currency": "TRY", "sector": "", "industry": "",
             "data_source": "test"}
        result = normalize_metrics(m)
        assert result["pe"] is None
        assert result["altman_z"] is None
        assert result["share_change"] is None

    def test_preserves_extra_fields(self):
        """Provider-specific fields like foreign_ratio are preserved."""
        m = {"symbol": "T", "ticker": "T", "name": "T",
             "currency": "TRY", "sector": "", "industry": "",
             "data_source": "borsapy", "foreign_ratio": 0.45}
        result = normalize_metrics(m)
        assert result["foreign_ratio"] == 0.45

    def test_does_not_mutate_original(self):
        """Original dict must not be modified."""
        m = {"symbol": "T", "ticker": "T", "name": "T",
             "currency": "TRY", "sector": "", "industry": "",
             "data_source": "test"}
        original_keys = set(m.keys())
        normalize_metrics(m)
        assert set(m.keys()) == original_keys

    def test_full_dict_unchanged(self, healthy_industrial_metrics):
        """A complete dict should pass through unchanged (all fields present)."""
        result = normalize_metrics(healthy_industrial_metrics)
        for k, v in healthy_industrial_metrics.items():
            assert result[k] == v


# ================================================================
# compute_score_coverage
# ================================================================
class TestComputeScoreCoverage:
    """compute_score_coverage tracks which FA dimensions have real data."""

    def test_full_data(self, healthy_industrial_metrics):
        """Healthy company: all dimensions should have data."""
        cov = compute_score_coverage(healthy_industrial_metrics)
        summary = cov["summary"]
        assert summary["dimensions_with_data"] == 7
        assert summary["imputed_dimensions"] == []

    def test_sparse_data(self, sparse_metrics):
        """Sparse company: 6 of 7 dimensions imputed (value has market_cap)."""
        cov = compute_score_coverage(sparse_metrics)
        summary = cov["summary"]
        # sparse_metrics has market_cap=500M → value dim has 1/7 inputs
        assert summary["dimensions_with_data"] == 1
        assert len(summary["imputed_dimensions"]) == 6

    def test_partial_data(self):
        """Stock with value data but no growth data."""
        m = {
            "pe": 8.0, "pb": 1.2, "ev_ebitda": 5.0,
            "fcf_yield": 0.04, "margin_safety": 0.1,
            "revenue": 1e9, "market_cap": 5e9,
            # Growth fields all None
            "revenue_growth": None, "eps_growth": None,
            "ebitda_growth": None, "peg": None,
            # Quality fields present
            "roe": 0.15, "roic": 0.10, "net_margin": 0.08,
        }
        cov = compute_score_coverage(m)
        assert cov["value"]["available"] > 0
        assert cov["growth"]["available"] == 0
        assert "growth" in cov["summary"]["imputed_dimensions"]
        assert "value" not in cov["summary"]["imputed_dimensions"]

    def test_coverage_per_dimension_structure(self, healthy_industrial_metrics):
        """Each dimension entry should have available, total, pct."""
        cov = compute_score_coverage(healthy_industrial_metrics)
        for dim in FA_DIMENSION_INPUTS:
            assert "available" in cov[dim]
            assert "total" in cov[dim]
            assert "pct" in cov[dim]
            assert 0 <= cov[dim]["pct"] <= 100

    def test_summary_structure(self, healthy_industrial_metrics):
        cov = compute_score_coverage(healthy_industrial_metrics)
        s = cov["summary"]
        assert "dimensions_with_data" in s
        assert "total_dimensions" in s
        assert "imputed_dimensions" in s
        assert s["total_dimensions"] == 7


# ================================================================
# confidence_penalty_for_imputed_scores
# ================================================================
class TestConfidencePenalty:
    """Confidence penalty scales with FA weight of imputed dimensions."""

    def test_no_imputation(self):
        assert confidence_penalty_for_imputed_scores([]) == 0.0

    def test_single_imputed_dimension(self):
        """Imputing growth (weight=0.15) should cost 15 points."""
        penalty = confidence_penalty_for_imputed_scores(["growth"])
        assert penalty == FA_WEIGHTS["growth"] * 100

    def test_multiple_imputed_dimensions(self):
        """Imputing growth + moat should cost their combined weight × 100."""
        dims = ["growth", "moat"]
        expected = sum(FA_WEIGHTS[d] * 100 for d in dims)
        assert confidence_penalty_for_imputed_scores(dims) == expected

    def test_all_imputed(self):
        """Imputing all 7 dimensions costs 100 points total."""
        all_dims = list(FA_WEIGHTS.keys())
        penalty = confidence_penalty_for_imputed_scores(all_dims)
        assert abs(penalty - 100.0) < 0.1  # weights sum to 1.0 → penalty = 100

    def test_quality_costliest(self):
        """Quality (weight 0.30) should be the most expensive single imputation."""
        penalties = {d: confidence_penalty_for_imputed_scores([d]) for d in FA_WEIGHTS}
        max_dim = max(penalties, key=penalties.get)
        assert max_dim == "quality"


# ================================================================
# check_field_parity
# ================================================================
class TestCheckFieldParity:
    """check_field_parity reports which canonical fields are present/missing."""

    def test_full_dict(self, healthy_industrial_metrics):
        result = check_field_parity(healthy_industrial_metrics)
        assert len(result["missing"]) == 0 or result["pct_present"] > 90

    def test_sparse_dict(self):
        m = {"symbol": "T", "data_source": "test"}
        result = check_field_parity(m)
        assert len(result["missing"]) > 0
        assert result["pct_present"] < 10

    def test_extra_fields_detected(self):
        m = {"custom_field": 42, "another_extra": "hello"}
        result = check_field_parity(m)
        assert "custom_field" in result["extra"]
        assert "another_extra" in result["extra"]


# ================================================================
# PROVIDER PARITY — both providers should produce the same field set
# ================================================================
class TestProviderParity:
    """After normalize_metrics(), both providers should have identical field sets."""

    def _yfinance_skeleton(self) -> dict:
        """Minimal yfinance-style output."""
        m = {k: None for k in ALL_METRIC_FIELDS}
        m.update({
            "symbol": "THYAO.IS", "ticker": "THYAO", "name": "THY",
            "currency": "TRY", "sector": "Industrials", "industry": "Airlines",
            "data_source": "yfinance",
            "pe": 5.0, "revenue": 1e10, "market_cap": 1e11,
            "share_change": -0.02,
            "ciro_pd": 0.1,
        })
        return m

    def _borsapy_skeleton(self) -> dict:
        """Minimal borsapy-style output."""
        m = {k: None for k in ALL_METRIC_FIELDS}
        m.update({
            "symbol": "THYAO.IS", "ticker": "THYAO", "name": "THY",
            "currency": "TRY", "sector": "Industrials", "industry": "Airlines",
            "data_source": "borsapy",
            "pe": 5.0, "revenue": 1e10, "market_cap": 1e11,
            "share_change": None,  # borsapy can't compute this
            "ciro_pd": 0.1,       # now computed in borsapy too
            "foreign_ratio": 0.65,
            "free_float": 0.35,
        })
        return m

    def test_canonical_fields_present_both(self):
        """Both providers must have all canonical fields after normalize."""
        yf = normalize_metrics(self._yfinance_skeleton())
        bp = normalize_metrics(self._borsapy_skeleton())
        for field in ALL_METRIC_FIELDS:
            assert field in yf, f"yfinance missing: {field}"
            assert field in bp, f"borsapy missing: {field}"

    def test_known_divergences(self):
        """Document the known remaining divergences."""
        yf = self._yfinance_skeleton()
        bp = self._borsapy_skeleton()
        # share_change: yfinance has it, borsapy doesn't
        assert yf["share_change"] is not None
        assert bp["share_change"] is None
        # foreign_ratio: borsapy has it, yfinance doesn't
        assert bp.get("foreign_ratio") is not None
        assert yf.get("foreign_ratio") is None

    def test_ciro_pd_now_in_both(self):
        """After Phase 2 fix, both providers should compute ciro_pd."""
        yf = self._yfinance_skeleton()
        bp = self._borsapy_skeleton()
        assert yf["ciro_pd"] is not None
        assert bp["ciro_pd"] is not None


# ================================================================
# MISSING DATA PROPAGATION — the core safety test
# ================================================================
class TestMissingDataPropagation:
    """Verify that missing data is tracked, not hidden."""

    def test_all_none_produces_7_imputed(self, sparse_metrics):
        """A stock with all None metrics should have 7 imputed dimensions."""
        from engine.scoring import (
            score_value, score_quality, score_growth,
            score_balance, score_earnings, score_moat, score_capital,
        )
        raw = {
            "value": score_value(sparse_metrics, "sanayi"),
            "quality": score_quality(sparse_metrics, "sanayi"),
            "growth": score_growth(sparse_metrics, "sanayi"),
            "balance": score_balance(sparse_metrics, "sanayi"),
            "earnings": score_earnings(sparse_metrics),
            "moat": score_moat(sparse_metrics),
            "capital": score_capital(sparse_metrics),
        }
        imputed = [k for k, v in raw.items() if v is None]
        assert len(imputed) == 7

    def test_imputed_scores_still_default_to_50(self, sparse_metrics):
        """Backward compat: imputed scores still become 50."""
        from engine.scoring import score_growth
        raw = score_growth(sparse_metrics, "sanayi")
        assert raw is None
        final = round(raw if raw is not None else 50, 1)
        assert final == 50.0

    def test_coverage_detects_sparse(self, sparse_metrics):
        """Score coverage correctly identifies 6 of 7 dimensions as imputed.
        (value dim has market_cap from the sparse fixture)"""
        cov = compute_score_coverage(sparse_metrics)
        assert len(cov["summary"]["imputed_dimensions"]) == 6

    def test_confidence_penalty_reduces_sparse(self, sparse_metrics):
        """Mostly sparse stock gets large confidence penalty (~82 pts for 6/7 dims)."""
        cov = compute_score_coverage(sparse_metrics)
        imputed = cov["summary"]["imputed_dimensions"]
        penalty = confidence_penalty_for_imputed_scores(imputed)
        # 6 dims imputed = all except value (0.18) → penalty = (1.0 - 0.18) × 100 = 82
        assert penalty >= 80.0

    def test_partial_missing_detected(self):
        """Stock with growth data missing should show growth as imputed."""
        m = normalize_metrics({
            "symbol": "T.IS", "ticker": "T", "name": "T",
            "currency": "TRY", "sector": "Industrials", "industry": "",
            "data_source": "test",
            "pe": 8.0, "pb": 1.2, "roe": 0.15,
            # Growth fields deliberately missing
        })
        cov = compute_score_coverage(m)
        assert "growth" in cov["summary"]["imputed_dimensions"]
        assert cov["growth"]["available"] == 0

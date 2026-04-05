# ================================================================
# BISTBULL TERMINAL — Unit Tests: Explainability Engine (v2)
# Tests: summary, contribution-based ranking, 5-level strength,
#        human-readable labels, explanation fields, edge cases
# ================================================================

import pytest

from engine.explainability import (
    build_explanation, build_dimension_breakdown,
    extract_top_drivers, explain_confidence,
    explain_missing_data, explain_overall_formula,
    build_summary, _strength, _direction,
)
from config import FA_WEIGHTS, IVME_WEIGHTS


@pytest.fixture
def full_analysis():
    return {
        "ticker": "EREGL", "name": "Eregli", "symbol": "EREGL.IS",
        "overall": 68.5, "confidence": 87.5,
        "fa_score": 62.0, "deger": 58.0, "ivme": 55.0,
        "risk_score": -5, "risk_penalty": -5,
        "risk_reasons": ["Dusuk faiz karsilama 2.5x (-4)"],
        "entry_label": "TEYITLI", "is_hype": False,
        "sector_group": "sanayi",
        "scores": {
            "value": 72.0, "quality": 68.0, "growth": 45.0, "balance": 65.0,
            "earnings": 70.0, "moat": 55.0, "capital": 60.0,
            "momentum": 58.0, "tech_break": 50.0, "inst_flow": 45.0,
        },
        "scores_imputed": [],
        "score_coverage": {
            dim: {"available": 5, "total": 5, "pct": 100} for dim in FA_WEIGHTS
        },
        "metrics": {
            "pe": 6.5, "pb": 1.1, "ev_ebitda": 4.2, "fcf_yield": 0.099, "margin_safety": 0.254,
            "roe": 0.183, "roic": 0.176, "net_margin": 0.183,
            "revenue_growth": 0.143, "eps_growth": 0.222, "ebitda_growth": 0.185, "peg": 0.29,
            "net_debt_ebitda": 0.16, "debt_equity": 25.0, "current_ratio": 1.75,
            "interest_coverage": 14.0, "altman_z": 4.2,
            "cfo_to_ni": 1.18, "fcf_margin": 0.15, "beneish_m": -2.50,
            "gross_margin": 0.30, "gross_margin_prev": 0.286,
            "roa": 0.11, "roa_prev": 0.10,
            "operating_margin": 0.233, "asset_turnover": 0.60, "asset_turnover_prev": 0.583,
            "dividend_yield": 0.08, "share_change": -0.01,
            "operating_cf": 26e9, "free_cf": 18e9, "net_income": 22e9, "revenue": 120e9,
            "roic": 0.176, "market_cap": 182e9, "total_debt": 30e9, "cash": 25e9,
            "piotroski_f": 8, "inst_holders_pct": 0.65,
        },
        "style": "Kaliteli Bilesik",
        "legendary": {}, "positives": [], "negatives": [],
        "applicability": {},
    }


@pytest.fixture
def sparse_analysis():
    return {
        "ticker": "SPARSE", "name": "Sparse A.S.", "symbol": "SPARSE.IS",
        "overall": 42.0, "confidence": 55.0,
        "fa_score": 48.0, "deger": 45.0, "ivme": 40.0,
        "risk_score": -8, "risk_penalty": -8,
        "risk_reasons": ["Negatif nakit akisi (-8)"],
        "entry_label": "BEKLE", "is_hype": False,
        "sector_group": "sanayi",
        "scores": {
            "value": 55.0, "quality": 42.0, "growth": 50.0, "balance": 38.0,
            "earnings": 50.0, "moat": 50.0, "capital": 50.0,
            "momentum": 40.0, "tech_break": 35.0, "inst_flow": 30.0,
        },
        "scores_imputed": ["growth", "earnings", "moat", "capital"],
        "score_coverage": {
            "growth": {"available": 0, "total": 4, "pct": 0},
            "earnings": {"available": 0, "total": 3, "pct": 0},
            "moat": {"available": 0, "total": 7, "pct": 0},
            "capital": {"available": 0, "total": 7, "pct": 0},
            "value": {"available": 3, "total": 7, "pct": 42.9},
            "quality": {"available": 2, "total": 3, "pct": 66.7},
            "balance": {"available": 2, "total": 5, "pct": 40.0},
        },
        "metrics": {
            "pe": 12.0, "pb": 1.8, "ev_ebitda": None, "fcf_yield": None, "margin_safety": None,
            "roe": 0.08, "roic": None, "net_margin": 0.05,
            "revenue_growth": None, "eps_growth": None, "ebitda_growth": None, "peg": None,
            "net_debt_ebitda": 3.5, "debt_equity": None, "current_ratio": 0.9,
            "interest_coverage": 2.0, "altman_z": None,
            "cfo_to_ni": None, "fcf_margin": None, "beneish_m": None,
            "gross_margin": None, "gross_margin_prev": None,
            "roa": None, "roa_prev": None,
            "operating_margin": None, "asset_turnover": None, "asset_turnover_prev": None,
            "dividend_yield": None, "share_change": None,
            "operating_cf": None, "free_cf": None, "net_income": None, "revenue": None,
            "roic": None, "market_cap": 1e9, "total_debt": 5e8, "cash": 1e8,
            "piotroski_f": None, "inst_holders_pct": None,
        },
        "style": "Dengeli", "legendary": {}, "positives": [], "negatives": [],
        "applicability": {},
    }


# ================================================================
# SUMMARY
# ================================================================
class TestSummary:
    def test_summary_present(self, full_analysis):
        exp = build_explanation(full_analysis)
        assert "summary" in exp
        assert isinstance(exp["summary"], str)
        assert len(exp["summary"]) > 10

    def test_summary_deterministic(self, full_analysis):
        s1 = build_explanation(full_analysis)["summary"]
        s2 = build_explanation(full_analysis)["summary"]
        assert s1 == s2

    def test_summary_mentions_positive_and_negative(self, full_analysis):
        exp = build_explanation(full_analysis)
        s = exp["summary"].lower()
        assert "ancak" in s or "dikkat" in s or "dengeli" in s

    def test_summary_sparse_stock(self, sparse_analysis):
        exp = build_explanation(sparse_analysis)
        assert isinstance(exp["summary"], str)
        assert len(exp["summary"]) > 5

    def test_build_summary_all_positive(self):
        pos = [{"name": "Ucuz F/K orani", "contribution": 5.0}]
        neg = []
        s = build_summary(pos, neg, [], 80)
        assert "güçlü" in s.lower() or "öne" in s.lower() or "profil" in s.lower()

    def test_build_summary_all_negative(self):
        pos = []
        neg = [{"name": "Buyume verisi eksik", "contribution": -3.0}]
        s = build_summary(pos, neg, [], 30)
        assert "dikkat" in s.lower()

    def test_build_summary_balanced(self):
        s = build_summary([], [], [], 50)
        assert "dengeli" in s.lower()


# ================================================================
# STRENGTH (5-level system)
# ================================================================
class TestStrengthSystem:
    def test_strong_positive(self):
        assert _strength(90) == "strong_positive"
        assert _strength(75) == "strong_positive"

    def test_positive(self):
        assert _strength(60) == "positive"
        assert _strength(55) == "positive"

    def test_neutral(self):
        assert _strength(50) == "neutral"
        assert _strength(45) == "neutral"

    def test_negative(self):
        assert _strength(35) == "negative"
        assert _strength(25) == "negative"

    def test_strong_negative(self):
        assert _strength(10) == "strong_negative"
        assert _strength(0) == "strong_negative"

    def test_none_is_neutral(self):
        assert _strength(None) == "neutral"

    def test_no_old_labels(self, full_analysis):
        exp = build_explanation(full_analysis)
        for d in exp["top_positive_drivers"] + exp["top_negative_drivers"]:
            assert d["strength"] not in ("low", "medium", "high", "weak", "missing")


# ================================================================
# CONTRIBUTION-BASED RANKING
# ================================================================
class TestContributionRanking:
    def test_drivers_have_contribution(self, full_analysis):
        exp = build_explanation(full_analysis)
        for d in exp["top_positive_drivers"]:
            assert "contribution" in d

    def test_positive_sorted_by_contribution(self, full_analysis):
        exp = build_explanation(full_analysis)
        contribs = [d["contribution"] for d in exp["top_positive_drivers"]]
        assert contribs == sorted(contribs, reverse=True)

    def test_negative_sorted_by_contribution(self, sparse_analysis):
        exp = build_explanation(sparse_analysis)
        contribs = [d["contribution"] for d in exp["top_negative_drivers"]]
        assert contribs == sorted(contribs)

    def test_dimension_contribution_is_weight_times_delta(self, full_analysis):
        exp = build_explanation(full_analysis)
        bd = exp["driver_breakdown"]
        for dim in FA_WEIGHTS:
            expected = round(FA_WEIGHTS[dim] * (full_analysis["scores"][dim] - 50.0), 2)
            assert bd[dim]["contribution"] == expected


# ================================================================
# HUMAN-READABLE LABELS
# ================================================================
class TestHumanReadableLabels:
    def test_drivers_have_explanation_field(self, full_analysis):
        exp = build_explanation(full_analysis)
        for d in exp["top_positive_drivers"]:
            assert "explanation" in d
            assert isinstance(d["explanation"], str)

    def test_driver_names_not_raw_keys(self, full_analysis):
        exp = build_explanation(full_analysis)
        raw_keys = {"pe", "pb", "roe", "roic", "peg", "cfo_to_ni"}
        for d in exp["top_positive_drivers"] + exp["top_negative_drivers"]:
            assert d["name"] not in raw_keys, f"Raw key used as name: {d['name']}"

    def test_drivers_have_key_field(self, full_analysis):
        exp = build_explanation(full_analysis)
        for d in exp["top_positive_drivers"]:
            assert "key" in d


# ================================================================
# DIVERSITY
# ================================================================
class TestDiversity:
    def test_max_5_positive(self, full_analysis):
        exp = build_explanation(full_analysis)
        assert len(exp["top_positive_drivers"]) <= 5

    def test_max_5_negative(self, sparse_analysis):
        exp = build_explanation(sparse_analysis)
        assert len(exp["top_negative_drivers"]) <= 5

    def test_no_dimension_flooding(self, full_analysis):
        exp = build_explanation(full_analysis)
        for drivers in [exp["top_positive_drivers"], exp["top_negative_drivers"]]:
            dim_counts = {}
            for d in drivers:
                dim = d.get("dimension", d.get("key", ""))
                dim_counts[dim] = dim_counts.get(dim, 0) + 1
            for dim, count in dim_counts.items():
                assert count <= 2, f"Dimension '{dim}' appears {count} times (max 2)"


# ================================================================
# MISSING DATA
# ================================================================
class TestMissingData:
    def test_no_impact_when_complete(self):
        r = explain_missing_data([], {})
        assert r["has_impact"] is False

    def test_simple_wording(self, sparse_analysis):
        r = explain_missing_data(sparse_analysis["scores_imputed"], sparse_analysis["score_coverage"])
        assert "tahmine" in r["summary"].lower() or "tahmin" in r["summary"].lower()
        assert r["has_impact"] is True

    def test_dimension_name_present(self, sparse_analysis):
        r = explain_missing_data(sparse_analysis["scores_imputed"], sparse_analysis["score_coverage"])
        for dim in r["imputed_dimensions"]:
            assert "dimension_name" in dim

    def test_imputed_in_negatives(self, sparse_analysis):
        exp = build_explanation(sparse_analysis)
        neg_keys = [d.get("key", "") for d in exp["top_negative_drivers"]]
        imputed_negs = [k for k in neg_keys if "_imputed" in k]
        assert len(imputed_negs) > 0


# ================================================================
# CONFIDENCE
# ================================================================
class TestConfidence:
    def test_high_confidence(self, full_analysis):
        r = explain_confidence(87.5, full_analysis["metrics"], [])
        assert "iyi" in r["summary"].lower() or "guven" in r["summary"].lower()

    def test_low_confidence_mentions_imputed(self, sparse_analysis):
        r = explain_confidence(55.0, sparse_analysis["metrics"], sparse_analysis["scores_imputed"])
        assert "eksik" in r["summary"].lower()


# ================================================================
# OVERALL FORMULA
# ================================================================
class TestFormula:
    def test_components_present(self, full_analysis):
        exp = build_explanation(full_analysis)
        c = exp["overall_formula"]["components"]
        assert "fa_pure" in c
        assert "momentum_effect" in c
        assert "valuation_stretch" in c
        assert "risk_penalty" in c


# ================================================================
# EDGE CASES
# ================================================================
class TestEdgeCases:
    def test_all_data_missing(self):
        r = {
            "ticker": "X", "overall": 40, "confidence": 10,
            "fa_score": 50, "ivme": 50, "risk_score": 0, "risk_penalty": 0,
            "risk_reasons": [], "sector_group": "sanayi",
            "scores": {k: 50.0 for k in {**FA_WEIGHTS, **IVME_WEIGHTS}},
            "scores_imputed": list(FA_WEIGHTS.keys()),
            "score_coverage": {dim: {"available": 0, "total": 5, "pct": 0} for dim in FA_WEIGHTS},
            "metrics": {k: None for k in [
                "pe", "pb", "ev_ebitda", "fcf_yield", "margin_safety",
                "roe", "roic", "net_margin", "revenue_growth", "eps_growth",
                "ebitda_growth", "peg", "net_debt_ebitda", "debt_equity",
                "current_ratio", "interest_coverage", "altman_z", "cfo_to_ni",
                "fcf_margin", "beneish_m", "gross_margin", "gross_margin_prev",
                "roa", "roa_prev", "operating_margin", "asset_turnover",
                "asset_turnover_prev", "dividend_yield", "share_change",
                "operating_cf", "free_cf", "net_income", "revenue", "market_cap",
                "total_debt", "cash", "piotroski_f", "inst_holders_pct",
            ]},
        }
        exp = build_explanation(r)
        assert exp["missing_data_impact"]["has_impact"] is True
        assert "summary" in exp
        assert isinstance(exp["summary"], str)

    def test_extreme_positive_stock(self):
        r = {
            "ticker": "BEST", "overall": 95, "confidence": 100,
            "fa_score": 90, "ivme": 85, "risk_score": 4, "risk_penalty": 4,
            "risk_reasons": [], "sector_group": "sanayi",
            "scores": {k: 90.0 for k in {**FA_WEIGHTS, **IVME_WEIGHTS}},
            "scores_imputed": [],
            "score_coverage": {dim: {"available": 5, "total": 5, "pct": 100} for dim in FA_WEIGHTS},
            "metrics": {
                "pe": 4, "pb": 0.6, "ev_ebitda": 3, "fcf_yield": 0.10, "margin_safety": 0.35,
                "roe": 0.25, "roic": 0.20, "net_margin": 0.18,
                "revenue_growth": 0.25, "eps_growth": 0.30, "ebitda_growth": 0.20, "peg": 0.15,
                "net_debt_ebitda": -0.5, "debt_equity": 15, "current_ratio": 2.5,
                "interest_coverage": 20, "altman_z": 5.0,
                "cfo_to_ni": 1.3, "fcf_margin": 0.12, "beneish_m": -3.0,
                "gross_margin": 0.40, "gross_margin_prev": 0.39,
                "roa": 0.15, "roa_prev": 0.14,
                "operating_margin": 0.25, "asset_turnover": 0.7, "asset_turnover_prev": 0.68,
                "dividend_yield": 0.05, "share_change": -0.02,
                "operating_cf": 1e9, "free_cf": 8e8, "net_income": 7e8, "revenue": 5e9,
                "roic": 0.20, "market_cap": 10e9, "total_debt": 5e8, "cash": 1e9,
                "piotroski_f": 9, "inst_holders_pct": 0.75,
            },
        }
        exp = build_explanation(r)
        assert len(exp["top_positive_drivers"]) >= 3
        assert exp["missing_data_impact"]["has_impact"] is False
        assert "güçlü" in exp["summary"].lower() or "öne" in exp["summary"].lower() or "profil" in exp["summary"].lower()

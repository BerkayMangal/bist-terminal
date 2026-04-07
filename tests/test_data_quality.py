import pytest
from engine.data_quality import assess_data_quality, build_decision_context

class TestAssessDataQuality:
    def test_clean_data_grade_a(self):
        r = assess_data_quality({"pe": 15, "roe": 20, "net_income": 1e6, "revenue": 1e7, "market_cap": 1e8}, [])
        assert r["grade"] == "A" and r["anomaly_count"] == 0

    def test_extreme_pe(self):
        r = assess_data_quality({"pe": 800, "roe": 10, "net_income": 1, "revenue": 1, "market_cap": 1})
        assert any(a["field"] == "pe" for a in r["anomalies"])

    def test_missing_critical(self):
        assert assess_data_quality({})["missing_count"] == 5

    def test_growth_jump(self):
        r = assess_data_quality({"pe": 10, "roe": 10, "net_income": 1, "revenue": 1, "market_cap": 1, "revenue_growth": 7.0})
        assert any(a["type"] == "growth_jump" for a in r["anomalies"])

    def test_imputed_affect_grade(self):
        r = assess_data_quality({"pe": 10, "roe": 10, "net_income": 1, "revenue": 1, "market_cap": 1}, ["value", "quality", "growth"])
        assert r["grade"] in ("B", "C")

    def test_never_crashes(self):
        assert "grade" in assess_data_quality({"pe": "bad"}, None)

    def test_extreme_move(self):
        r = assess_data_quality({"pe": 10, "roe": 10, "net_income": 1, "revenue": 1, "market_cap": 1, "day_return": 0.35})
        assert any(a["type"] == "extreme_move" for a in r["anomalies"])

class TestDecisionContext:
    def test_high(self):
        assert build_decision_context({"grade": "A", "anomalies": []}, 80, False, [])["reliability"] == "high"

    def test_low(self):
        ctx = build_decision_context({"grade": "D", "anomalies": [{"label": "x"}]}, 30, True, ["a", "b", "c"])
        assert ctx["reliability"] == "low" and len(ctx["caveats"]) > 0

    def test_never_crashes(self):
        assert "reliability" in build_decision_context({}, 0, False, None)

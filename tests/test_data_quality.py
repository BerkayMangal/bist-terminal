"""Tests for engine/data_quality.py — data trust & anomaly layer."""
import pytest
from engine.data_quality import assess_data_quality, build_decision_context


class TestAssessDataQuality:
    def test_clean_data_grade_a(self):
        m = {"pe": 15, "roe": 20, "net_income": 1e6, "revenue": 1e7, "market_cap": 1e8}
        r = assess_data_quality(m, [])
        assert r["grade"] == "A"
        assert r["anomaly_count"] == 0
        assert r["missing_count"] == 0

    def test_extreme_pe_flagged(self):
        m = {"pe": 800, "roe": 10, "net_income": 1, "revenue": 1, "market_cap": 1}
        r = assess_data_quality(m)
        assert any(a["field"] == "pe" for a in r["anomalies"])
        assert r["grade"] != "A"

    def test_negative_extreme_pe(self):
        m = {"pe": -600, "roe": 10, "net_income": 1, "revenue": 1, "market_cap": 1}
        r = assess_data_quality(m)
        assert any(a["type"] == "extreme_value" and a["field"] == "pe" for a in r["anomalies"])

    def test_missing_critical_fields(self):
        m = {}  # all missing
        r = assess_data_quality(m)
        assert r["missing_count"] == 5
        assert "pe" in r["missing_fields"]

    def test_growth_jump_flagged(self):
        m = {"pe": 10, "roe": 10, "net_income": 1, "revenue": 1, "market_cap": 1,
             "revenue_growth": 7.0}
        r = assess_data_quality(m)
        assert any(a["type"] == "growth_jump" for a in r["anomalies"])

    def test_imputed_dimensions_affect_grade(self):
        m = {"pe": 10, "roe": 10, "net_income": 1, "revenue": 1, "market_cap": 1}
        r = assess_data_quality(m, ["value", "quality", "growth"])
        assert r["grade"] in ("B", "C")

    def test_never_crashes_on_garbage(self):
        r = assess_data_quality({"pe": "not_a_number"}, None)
        assert "grade" in r  # fallback or partial result

    def test_extreme_day_move(self):
        m = {"pe": 10, "roe": 10, "net_income": 1, "revenue": 1, "market_cap": 1,
             "day_return": 0.35}
        r = assess_data_quality(m)
        assert any(a["type"] == "extreme_move" for a in r["anomalies"])


class TestDecisionContext:
    def test_high_reliability(self):
        health = {"grade": "A", "anomalies": [], "missing_fields": []}
        ctx = build_decision_context(health, 80.0, False, [])
        assert ctx["reliability"] == "high"
        assert ctx["caveats"] == []

    def test_low_reliability_bad_grade(self):
        health = {"grade": "D", "anomalies": [{"label": "test"}], "missing_fields": []}
        ctx = build_decision_context(health, 30.0, True, ["a", "b", "c"])
        assert ctx["reliability"] == "low"
        assert len(ctx["caveats"]) > 0

    def test_never_crashes(self):
        ctx = build_decision_context({}, 0, False, None)
        assert "reliability" in ctx

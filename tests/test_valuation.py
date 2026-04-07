"""Tests for engine/valuation.py — valuation trust layer."""
import pytest
from engine.valuation import build_valuation_layer


def _base_metrics(**overrides):
    m = {
        "price": 50.0, "market_cap": 5e9, "revenue": 2e9, "ebitda": 400e6,
        "net_income": 250e6, "free_cf": 200e6, "total_debt": 1e9,
        "cash": 300e6, "equity": 2e9, "pe": 12.0, "pb": 2.5,
        "ev_ebitda": 8.0, "revenue_growth": 0.22, "net_margin": 0.125,
        "fcf_yield": 0.04, "graham_fv": 65.0, "debt_equity": 0.5,
        "net_debt_ebitda": 1.75, "currency": "TRY", "sector": "Finans",
        "dividend_yield": 0.03,
    }
    m.update(overrides)
    return m


def _base_analysis(**overrides):
    a = {"sector_group": "Finans", "symbol": "TEST.IS"}
    a.update(overrides)
    return a


class TestBuildValuationLayer:
    def test_full_data_returns_range(self):
        r = build_valuation_layer(_base_metrics(), _base_analysis())
        assert r["valuation"]["method"] != "unavailable"
        assert r["valuation"]["bear_case"] < r["valuation"]["base_case"] < r["valuation"]["bull_case"]
        assert "range" in r["valuation"]

    def test_high_confidence_with_fcf(self):
        r = build_valuation_layer(_base_metrics(), _base_analysis())
        assert r["valuation_confidence"]["level"] in ("high", "medium")

    def test_missing_all_financials_falls_back_graham(self):
        m = _base_metrics(revenue=None, net_income=None, free_cf=None, ebitda=None)
        r = build_valuation_layer(m, _base_analysis())
        assert r["valuation"]["method"] in ("graham", "unavailable")

    def test_completely_empty_metrics(self):
        r = build_valuation_layer({}, {})
        assert r["valuation"]["method"] == "unavailable"
        assert r["valuation_confidence"]["level"] == "low"

    def test_never_crashes_on_garbage(self):
        r = build_valuation_layer({"price": "bad", "revenue": None}, {})
        assert "valuation" in r

    def test_assumptions_present(self):
        r = build_valuation_layer(_base_metrics(), _base_analysis())
        a = r["valuation_assumptions"]
        assert "growth_rate" in a
        assert "discount_rate" in a
        assert a["discount_rate"] > 0

    def test_inputs_present(self):
        r = build_valuation_layer(_base_metrics(), _base_analysis())
        vi = r["valuation_inputs"]
        assert vi["revenue"] == 2e9
        assert vi["last_price"] == 50.0

    def test_data_health_flags_missing(self):
        m = _base_metrics(free_cf=None, ebitda=None)
        r = build_valuation_layer(m, _base_analysis())
        assert r["valuation_data_health"]["free_cf"] == "missing"
        assert r["valuation_data_health"]["ebitda"] == "missing"
        assert r["valuation_data_health"]["revenue"] == "ok"

    def test_risks_generated(self):
        m = _base_metrics(revenue_growth=0.50, net_margin=0.02, debt_equity=3.0)
        r = build_valuation_layer(m, _base_analysis())
        assert len(r["valuation_risks"]) > 0
        assert len(r["valuation_risks"]) <= 3

    def test_scenarios_present(self):
        r = build_valuation_layer(_base_metrics(), _base_analysis())
        vs = r["valuation_scenarios"]
        assert "base_case" in vs
        assert "risk_case" in vs

    def test_data_context(self):
        r = build_valuation_layer(_base_metrics(), _base_analysis())
        dc = r["valuation_data_context"]
        assert "market_data_date" in dc
        assert "freshness" in dc

    def test_vs_price_computed(self):
        r = build_valuation_layer(_base_metrics(), _base_analysis())
        if r["valuation"].get("base_case"):
            assert "vs_price" in r["valuation"]

    def test_negative_fcf_falls_to_earnings(self):
        m = _base_metrics(free_cf=-100e6)
        r = build_valuation_layer(m, _base_analysis())
        assert r["valuation"]["method"] in ("dcf_earnings", "dcf_revenue", "graham")

    def test_pb_note_below_book(self):
        m = _base_metrics(pb=0.7)
        r = build_valuation_layer(m, _base_analysis())
        assert "pb_note" in r["valuation_context"]

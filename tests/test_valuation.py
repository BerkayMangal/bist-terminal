import pytest
from engine.valuation import build_valuation_layer

def _m(**o):
    m = {"price": 50.0, "market_cap": 5e9, "revenue": 2e9, "ebitda": 400e6, "net_income": 250e6, "free_cf": 200e6,
         "total_debt": 1e9, "cash": 300e6, "equity": 2e9, "pe": 12.0, "pb": 2.5, "ev_ebitda": 8.0,
         "revenue_growth": 0.22, "net_margin": 0.125, "fcf_yield": 0.04, "graham_fv": 65.0, "debt_equity": 0.5,
         "net_debt_ebitda": 1.75, "currency": "TRY", "dividend_yield": 0.03}
    m.update(o); return m

def _a(**o):
    a = {"sector_group": "Finans"}; a.update(o); return a

class TestValuation:
    def test_range(self):
        r = build_valuation_layer(_m(), _a())
        assert r["valuation"]["bear_case"] < r["valuation"]["base_case"] < r["valuation"]["bull_case"]

    def test_confidence(self):
        assert build_valuation_layer(_m(), _a())["valuation_confidence"]["level"] in ("high", "medium")

    def test_graham_fallback(self):
        r = build_valuation_layer(_m(revenue=None, net_income=None, free_cf=None, ebitda=None), _a())
        assert r["valuation"]["method"] in ("graham", "unavailable")

    def test_empty(self):
        r = build_valuation_layer({}, {})
        assert r["valuation"]["method"] == "unavailable"

    def test_never_crashes(self):
        assert "valuation" in build_valuation_layer({"price": "bad"}, {})

    def test_assumptions(self):
        assert build_valuation_layer(_m(), _a())["valuation_assumptions"]["discount_rate"] > 0

    def test_inputs(self):
        assert build_valuation_layer(_m(), _a())["valuation_inputs"]["revenue"] == 2e9

    def test_data_health_missing(self):
        r = build_valuation_layer(_m(free_cf=None, ebitda=None), _a())
        assert r["valuation_data_health"]["free_cf"] == "missing"

    def test_risks(self):
        r = build_valuation_layer(_m(revenue_growth=0.50, net_margin=0.02, debt_equity=3.0), _a())
        assert 0 < len(r["valuation_risks"]) <= 3

    def test_scenarios(self):
        assert "base_case" in build_valuation_layer(_m(), _a())["valuation_scenarios"]

    def test_vs_price(self):
        r = build_valuation_layer(_m(), _a())
        if r["valuation"].get("base_case"): assert "vs_price" in r["valuation"]

    def test_negative_fcf(self):
        assert build_valuation_layer(_m(free_cf=-100e6), _a())["valuation"]["method"] in ("dcf_earnings", "dcf_revenue", "graham")

    def test_pb_note(self):
        assert "pb_note" in build_valuation_layer(_m(pb=0.7), _a())["valuation_context"]

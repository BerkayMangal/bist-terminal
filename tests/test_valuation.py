from engine.valuation import build_valuation_layer
def _m(**o):
 m={"price":50,"market_cap":5e9,"revenue":2e9,"ebitda":4e8,"net_income":2.5e8,"free_cf":2e8,"total_debt":1e9,"cash":3e8,"equity":2e9,"pe":12,"pb":2.5,"revenue_growth":0.22,"net_margin":0.125,"fcf_yield":0.04,"graham_fv":65,"debt_equity":0.5,"currency":"TRY","dividend_yield":0.03};m.update(o);return m
class TestVal:
 def test_range(self):r=build_valuation_layer(_m(),{});assert r["valuation"]["bear_case"]<r["valuation"]["base_case"]<r["valuation"]["bull_case"]
 def test_conf(self):assert build_valuation_layer(_m(),{})["valuation_confidence"]["level"] in ("high","medium")
 def test_empty(self):assert build_valuation_layer({},{})["valuation"]["method"]=="unavailable"
 def test_crash(self):assert "valuation" in build_valuation_layer({"price":"bad"},{})
 def test_assumptions(self):assert build_valuation_layer(_m(),{})["valuation_assumptions"]["discount_rate"]>0
 def test_health(self):assert build_valuation_layer(_m(free_cf=None),{})["valuation_data_health"]["free_cf"]=="missing"
 def test_risks(self):assert len(build_valuation_layer(_m(revenue_growth=0.5,net_margin=0.02,debt_equity=3),{})["valuation_risks"])>0

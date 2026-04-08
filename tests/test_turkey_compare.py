from engine.turkey_context import build_turkey_context
from engine.compare import compare_stocks

def _m(**o):
    m={"pe":12,"roe":0.15,"net_income":2.5e8,"revenue":2e9,"market_cap":5e9,"operating_cf":2e8,"free_cf":1.5e8,
       "cfo_to_ni":0.8,"net_margin":0.125,"revenue_growth":0.22,"beneish_m":-2.5,"debt_equity":0.5,
       "net_debt_ebitda":1.5,"receivables":1e8,"receivables_prev":8e7,"revenue_prev":1.6e9,
       "fcf_margin":0.07,"sector":"Finans"}
    m.update(o);return m
def _a(**o):
    a={"sector_group":"Sanayi","ticker":"TEST"};a.update(o);return a
def _analysis(ticker="TOASO",overall=65,ivme=55,**o):
    a={"ticker":ticker,"overall":overall,"deger":overall,"ivme":ivme,"fa_score":60,"risk_score":-5,
       "scores":{"value":60,"quality":65,"growth":55,"balance":60,"earnings":50,"momentum":55},
       "decision":"AL","timing_intel":{"state":"uygun"},"valuation":{"vs_price":10},
       "turkey_context":{"profit_quality_interpretation":{"level":"iyi"}}}
    a.update(o);return a

class TestTurkeyContext:
    def test_basic(self):
        r=build_turkey_context(_m(),_a())
        assert "inflation_accounting" in r
        assert "profit_quality_interpretation" in r
        assert "accounting_risk" in r
        assert "turkey_notes" in r
    def test_inflation_watch(self):
        r=build_turkey_context(_m(revenue_growth=0.6,operating_cf=5e7,net_income=2.5e8),_a())
        assert r["inflation_accounting"]["status"] in ("watch","material")
    def test_profit_quality_good(self):
        r=build_turkey_context(_m(cfo_to_ni=1.2,operating_cf=3e8),_a())
        assert r["profit_quality_interpretation"]["level"]=="iyi"
    def test_profit_quality_weak(self):
        r=build_turkey_context(_m(operating_cf=-1e7),_a())
        assert r["profit_quality_interpretation"]["level"]=="zayıf"
    def test_accounting_risk_low(self):
        r=build_turkey_context(_m(beneish_m=-3.0),_a())
        assert r["accounting_risk"]["level"]=="düşük"
    def test_accounting_risk_high(self):
        r=build_turkey_context(_m(beneish_m=-1.5),_a())
        assert r["accounting_risk"]["level"]=="yüksek"
    def test_bank_notes(self):
        r=build_turkey_context(_m(),_a(sector_group="Banka"))
        assert any("banka" in n.lower() or "Banka" in n for n in r["turkey_notes"])
    def test_holding_notes(self):
        r=build_turkey_context(_m(),_a(sector_group="Holding"))
        assert any("holding" in n.lower() or "Holding" in n for n in r["turkey_notes"])
    def test_crash(self):
        assert isinstance(build_turkey_context({},{}),dict)

class TestCompare:
    def test_basic(self):
        c=compare_stocks(_analysis("TOASO",70,60),_analysis("FROTO",65,55))
        assert c["left_ticker"]=="TOASO"
        assert c["right_ticker"]=="FROTO"
        assert "summary" in c
        assert "dimensions" in c
    def test_key_differences(self):
        L=_analysis("A",80,70,scores={"value":80,"quality":70,"growth":60,"balance":65,"earnings":55,"momentum":70})
        R=_analysis("B",50,40,scores={"value":40,"quality":50,"growth":45,"balance":50,"earnings":45,"momentum":40})
        c=compare_stocks(L,R)
        assert len(c["key_differences"])>0
    def test_equal(self):
        a=_analysis("X",60,55)
        c=compare_stocks(a,a)
        assert all(v=="eşit" for v in c["dimensions"].values())
    def test_crash(self):
        c=compare_stocks({},{})
        assert "left_ticker" in c or "error" in c

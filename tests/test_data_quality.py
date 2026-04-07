from engine.data_quality import assess_data_quality, build_decision_context
class TestDQ:
 def test_clean(self): assert assess_data_quality({"pe":15,"roe":20,"net_income":1,"revenue":1,"market_cap":1},[])["grade"]=="A"
 def test_extreme(self): assert any(a["field"]=="pe" for a in assess_data_quality({"pe":800,"roe":10,"net_income":1,"revenue":1,"market_cap":1})["anomalies"])
 def test_missing(self): assert assess_data_quality({})["missing_count"]==5
 def test_crash(self): assert "grade" in assess_data_quality({"pe":"bad"},None)
 def test_ctx_high(self): assert build_decision_context({"grade":"A","anomalies":[]},80,False,[])["reliability"]=="high"
 def test_ctx_low(self): assert build_decision_context({"grade":"D","anomalies":[{"label":"x"}]},30,True,["a","b","c"])["reliability"]=="low"

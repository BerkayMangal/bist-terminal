from engine.timing_intel import build_timing_intel
def _s(**o):s={"momentum":60,"tech_break":55,"inst_flow":50};s.update(o);return s
def _t(**o):t={"rsi":52,"vol_ratio":1.2,"macd_bullish":True,"pct_20d":5,"ma50":100,"ma200":95,"price":105,"bb_pos":0.6,"high_52w":120,"low_52w":70,"macd_cross":None};t.update(o);return t
class TestTI:
 def test_uygun(self):assert build_timing_intel(_s(momentum=70),_t(rsi=50,vol_ratio=1.5),{})["timing_intel"]["state"]=="uygun"
 def test_bekle(self):assert build_timing_intel(_s(momentum=25),_t(rsi=80,vol_ratio=0.5,macd_bullish=False,pct_20d=35),{})["timing_intel"]["state"]=="bekle"
 def test_none_tech(self):assert build_timing_intel(_s(),None,{})["timing_intel"]["state"]=="belirsiz"
 def test_string_scores(self):r=build_timing_intel({"momentum":"60","tech_break":"55","inst_flow":"50"},_t(),{});assert r["timing_intel"]["state"] in ("uygun","erken","bekle")
 def test_recent_up(self):assert any("yükseldi" in a for a in build_timing_intel(_s(),_t(pct_20d=12),{})["recent_activity"])
 def test_watch_ma50(self):assert any("MA50" in w for w in build_timing_intel(_s(),_t(price=90,ma50=100),{})["watch_points"])
 def test_signals(self):assert any("güçlü" in s for s in build_timing_intel(_s(momentum=75),_t(),{})["signal_summary"])
 def test_timeline(self):assert "kısa_vade" in build_timing_intel(_s(),_t(),{})["trend_timeline"]
 def test_crash(self):assert "timing_intel" in build_timing_intel({},{"rsi":"bad"},{})

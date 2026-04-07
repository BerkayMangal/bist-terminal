import pytest
from engine.timing_intel import build_timing_intel

def _scores(**o):
    s = {"momentum": 60, "tech_break": 55, "inst_flow": 50}; s.update(o); return s

def _tech(**o):
    t = {"rsi": 52, "vol_ratio": 1.2, "macd_bullish": True, "pct_20d": 5.0, "ma50": 100, "ma200": 95,
         "price": 105, "bb_pos": 0.6, "high_52w": 120, "low_52w": 70, "macd_cross": None,
         "bb_upper": 115, "bb_lower": 90}
    t.update(o); return t

class TestTimingIntel:
    def test_uygun_state(self):
        r = build_timing_intel(_scores(momentum=70), _tech(rsi=50, vol_ratio=1.5, macd_bullish=True), {})
        assert r["timing_intel"]["state"] == "uygun"

    def test_bekle_state(self):
        r = build_timing_intel(_scores(momentum=25), _tech(rsi=80, vol_ratio=0.5, macd_bullish=False, pct_20d=35), {})
        assert r["timing_intel"]["state"] == "bekle"

    def test_no_tech_belirsiz(self):
        r = build_timing_intel(_scores(), None, {})
        assert r["timing_intel"]["state"] == "belirsiz"

    def test_recent_activity_price_up(self):
        r = build_timing_intel(_scores(), _tech(pct_20d=12), {})
        assert any("yükseldi" in a for a in r["recent_activity"])

    def test_recent_activity_price_down(self):
        r = build_timing_intel(_scores(), _tech(pct_20d=-10), {})
        assert any("düştü" in a for a in r["recent_activity"])

    def test_watch_points_below_ma50(self):
        r = build_timing_intel(_scores(), _tech(price=90, ma50=100), {})
        assert any("MA50" in w for w in r["watch_points"])

    def test_signal_summary_strong_momentum(self):
        r = build_timing_intel(_scores(momentum=75), _tech(), {})
        assert any("güçlü" in s for s in r["signal_summary"])

    def test_trend_timeline(self):
        r = build_timing_intel(_scores(), _tech(), {})
        assert "kısa_vade" in r["trend_timeline"]

    def test_max_3_items(self):
        r = build_timing_intel(_scores(), _tech(pct_20d=15, vol_ratio=2.0, rsi=25, macd_cross="bullish"), {})
        assert len(r["recent_activity"]) <= 3
        assert len(r["watch_points"]) <= 3
        assert len(r["signal_summary"]) <= 3

    def test_never_crashes(self):
        assert "timing_intel" in build_timing_intel({}, {"rsi": "bad"}, {})

    def test_never_crashes_empty(self):
        assert "timing_intel" in build_timing_intel({}, {}, {})

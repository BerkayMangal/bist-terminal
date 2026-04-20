# ================================================================
# BISTBULL TERMINAL — Unit Tests: Aggregation + Prompts
# Tests the pure functions extracted from app.py in Phase 3.
# No I/O, no network, no Redis, no AI.
# ================================================================

import pytest

from engine.aggregation import (
    build_scan_item, build_batch_item,
    build_dashboard_data, build_hero_data, build_heatmap_sectors,
    build_briefing_context, build_agent_context,
)
from ai.prompts import (
    hero_prompt, parse_hero_response,
    briefing_prompt, macro_commentary_prompt, cross_commentary_prompt,
    agent_prompt, SOCIAL_PROMPT, clean_json_response,
)


# ================================================================
# Shared fixture: minimal analysis result
# ================================================================
@pytest.fixture
def sample_analysis():
    """Minimal analysis result dict as produced by analyze_symbol."""
    return {
        "ticker": "THYAO", "name": "THY", "symbol": "THYAO.IS",
        "overall": 72.5, "confidence": 85.0,
        "fa_score": 65.0, "deger": 62.0, "ivme": 58.0,
        "risk_score": -5, "entry_label": "TEYİTLİ", "is_hype": False,
        "timing": "TEYİTLİ", "quality_tag": "GÜÇLÜ", "decision": "AL",
        "sector_group": "ulasim", "sector": "Industrials", "industry": "Airlines",
        "style": "Kaliteli Bileşik",
        "scores": {"value": 68, "quality": 72, "growth": 55, "balance": 70,
                   "earnings": 65, "moat": 52, "capital": 60,
                   "momentum": 62, "tech_break": 55, "inst_flow": 48},
        "legendary": {"piotroski": "7/9 (Güçlü)", "altman": "3.20 (Güvenli)", "beneish": "-2.50 (Düşük risk)"},
        "positives": ["Yüksek iş kalitesi — ROE %18", "Sağlam bilanço"],
        "negatives": ["Bazı veriler eksik"],
        "metrics": {"price": 280.0, "market_cap": 180e9, "pe": 6.5, "pb": 1.2, "roe": 0.18, "revenue_growth": 0.12},
        "v11": {"ciro_pd": 0.5, "ciro_pd_label": "NORMAL", "is_fatal": False, "fatal_risks": []},
        "v11_labels": {
            "conviction": {"score": 72, "level": "YÜKSEK"},
            "earnings_quality": {"label": "İYİ"},
            "capital_allocation": {"label": "GÜÇLÜ"},
            "regime": "NORMAL",
            "legendary": {"buffett_graham": {"passed": True}, "anti_bubble": {"passed": False}, "value_trap": {"passed": True}},
        },
    }


@pytest.fixture
def sample_items(sample_analysis):
    """List of 3 analysis results for aggregation tests."""
    items = [sample_analysis.copy()]
    item2 = sample_analysis.copy()
    item2.update({"ticker": "EREGL", "name": "Ereğli", "overall": 68.0, "deger": 70.0, "ivme": 45.0,
                  "style": "Derin Değer", "sector": "Basic Materials",
                  "scores": {**sample_analysis["scores"], "value": 80, "growth": 40}})
    item3 = sample_analysis.copy()
    item3.update({"ticker": "SASA", "name": "Sasa Polyester", "overall": 35.0, "deger": 30.0, "ivme": 25.0,
                  "style": "Yüksek Riskli Dönüş", "sector": "Basic Materials",
                  "negatives": ["Borç/likidite riski", "Düşük kârlılık"],
                  "scores": {**sample_analysis["scores"], "value": 30, "balance": 25}})
    items.extend([item2, item3])
    return items


# ================================================================
# build_scan_item
# ================================================================
class TestBuildScanItem:
    def test_has_required_keys(self, sample_analysis):
        item = build_scan_item(sample_analysis)
        for key in ["ticker", "name", "overall", "confidence", "deger", "ivme",
                     "style", "scores", "positives", "negatives", "price", "pe"]:
            assert key in item, f"Missing key: {key}"

    def test_extracts_v11_fields(self, sample_analysis):
        item = build_scan_item(sample_analysis)
        assert item["ciro_pd"] == 0.5
        assert item["conviction"] == 72
        assert item["legendary_v11"]["buffett_graham"] is True

    def test_handles_missing_v11(self):
        minimal = {"ticker": "X", "name": "X", "overall": 50, "confidence": 50,
                    "style": "Dengeli", "scores": {}, "legendary": {},
                    "positives": [], "negatives": [], "metrics": {}}
        item = build_scan_item(minimal)
        assert item["ciro_pd"] is None
        assert item["conviction"] is None


# ================================================================
# build_dashboard_data
# ================================================================
class TestBuildDashboardData:
    def test_structure(self, sample_items):
        data = build_dashboard_data(sample_items)
        assert "scanned" in data
        assert "top3" in data
        assert "opportunities" in data
        assert "risks" in data
        assert "sectors" in data

    def test_top3_limited(self, sample_items):
        data = build_dashboard_data(sample_items)
        assert len(data["top3"]) <= 3

    def test_empty_items(self):
        data = build_dashboard_data([])
        assert data["scanned"] == 0
        assert data["top3"] == []


# ================================================================
# build_hero_data
# ================================================================
class TestBuildHeroData:
    def test_mode_detection(self, sample_items):
        result = build_hero_data(sample_items, {}, [])
        assert result["mode"] in ("POZITIF", "TEMKINLI_POZITIF", "NOTR", "RISKLI")
        assert result["mode_label"] in ("Pozitif", "Temkinli Pozitif", "Notr", "Riskli")

    def test_has_leaders(self, sample_items):
        result = build_hero_data(sample_items, {}, [])
        assert len(result["deger_leaders"]) > 0
        assert len(result["ivme_leaders"]) > 0

    def test_empty_items(self):
        result = build_hero_data([], {}, [])
        assert result["stats"]["total"] == 0
        assert result["deger_leaders"] == []

    def test_ai_fields_initially_none(self, sample_items):
        result = build_hero_data(sample_items, {}, [])
        assert result["story"] is None
        assert result["bot_says"] is None

    def test_watch_with_cross_data(self, sample_items):
        cross = [{"ticker": "THYAO", "signal": "GOLDEN_CROSS"}]
        result = build_hero_data(sample_items, {}, cross)
        assert any("sinyal" in w for w in result["watch"])


# ================================================================
# build_heatmap_sectors
# ================================================================
class TestBuildHeatmapSectors:
    def test_groups_by_sector(self):
        data = [
            {"ticker": "A", "change_pct": 2.0, "market_cap": 1e9, "sector": "Tech"},
            {"ticker": "B", "change_pct": -1.0, "market_cap": 2e9, "sector": "Tech"},
            {"ticker": "C", "change_pct": 3.0, "market_cap": 5e8, "sector": "Energy"},
        ]
        result = build_heatmap_sectors(data)
        assert result["total"] == 3
        assert len(result["sectors"]) == 2

    def test_empty_data(self):
        result = build_heatmap_sectors([])
        assert result["total"] == 0
        assert result["sectors"] == []


# ================================================================
# build_briefing_context
# ================================================================
class TestBuildBriefingContext:
    def test_structure(self, sample_items):
        ctx = build_briefing_context(sample_items, [])
        assert "count" in ctx
        assert "deger_str" in ctx
        assert "ivme_str" in ctx
        assert ctx["count"] == 3


# ================================================================
# build_agent_context
# ================================================================
class TestBuildAgentContext:
    def test_includes_scan_data(self, sample_items):
        ctx = build_agent_context(sample_items, [], "THYAO")
        assert "THYAO" in ctx
        assert "DEGER" in ctx

    def test_empty_items(self):
        ctx = build_agent_context([], [], "test")
        assert ctx == ""


# ================================================================
# PROMPT TESTS
# ================================================================
class TestHeroPrompt:
    def test_returns_string(self):
        prompt = hero_prompt("Notr", 100, 40, [], [], [], [], 5)
        assert isinstance(prompt, str)
        assert "BistBull" in prompt

class TestParseHeroResponse:
    def test_parses_standard(self):
        text = "HİKÂYE: Piyasa güçlü.\nYORUM: Bankalar öncü.\nFIRSAT: THYAO ucuz."
        r = parse_hero_response(text)
        assert r["story"] == "Piyasa güçlü."
        assert r["bot_says"] == "Bankalar öncü."
        assert r["ai_reason"] == "THYAO ucuz."

    def test_fallback_on_unparseable(self):
        r = parse_hero_response("Just some random AI text without labels")
        assert r["story"] is not None  # falls back to text[:200]

class TestCleanJsonResponse:
    def test_clean_json(self):
        assert clean_json_response('{"key": "val"}') == {"key": "val"}

    def test_strips_code_fences(self):
        assert clean_json_response('```json\n{"a": 1}\n```') == {"a": 1}

    def test_invalid_returns_none(self):
        assert clean_json_response("not json at all") is None

    def test_empty_returns_none(self):
        assert clean_json_response("") is None

class TestBriefingPrompt:
    def test_includes_context(self):
        ctx = {"count": 50, "deger_str": "THYAO(D:72)", "ivme_str": "ASELS(I:65)",
               "worst_str": "SASA(D:30)", "summary_parts": ["THYAO: D:72 I:58 (Kaliteli)"],
               "sig_str": "THYAO:GOLDEN_CROSS", "signal_count": 1}
        prompt = briefing_prompt(ctx)
        assert "THYAO" in prompt
        assert "50 hisse" in prompt

class TestMacroCommentaryPrompt:
    def test_returns_string(self):
        items = [{"flag": "🇹🇷", "name": "BIST 30", "price": 10000, "change_pct": 1.5, "ytd_pct": 12.0}]
        prompt = macro_commentary_prompt(items)
        assert "makro" in prompt.lower()

class TestCrossCommentaryPrompt:
    def test_returns_string(self):
        signals = [{"ticker": "THYAO", "signal": "GOLDEN_CROSS", "stars": 3}]
        prompt = cross_commentary_prompt(signals, 1, 0)
        assert "THYAO" in prompt

class TestAgentPrompt:
    def test_includes_query(self):
        prompt = agent_prompt("some context", "THYAO nasıl?")
        assert "THYAO nasıl?" in prompt

class TestSocialPrompt:
    def test_is_nonempty(self):
        assert len(SOCIAL_PROMPT) > 50
        assert "JSON" in SOCIAL_PROMPT

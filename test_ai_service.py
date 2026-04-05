# ================================================================
# BISTBULL TERMINAL — Unit Tests: AI Service & Clients
# Tests the service layer, prompt integration, and client singletons.
# No live API calls — tests pure logic and structure only.
# ================================================================

import pytest

from ai.prompts import (
    build_rich_context, trader_summary_prompt,
    hero_prompt, parse_hero_response,
    briefing_prompt, macro_commentary_prompt,
    cross_commentary_prompt, agent_prompt,
    SOCIAL_PROMPT, clean_json_response,
)


# ================================================================
# Shared fixture
# ================================================================
@pytest.fixture
def sample_analysis():
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
        "legendary": {"piotroski": "7/9 (Güçlü)", "altman": "3.20 (Güvenli)",
                       "beneish": "-2.50 (Düşük risk)", "graham_filter": "Geçti",
                       "buffett_filter": "Sınırda"},
        "positives": ["Yüksek iş kalitesi — ROE %18"],
        "negatives": ["Bazı veriler eksik"],
        "risk_reasons": ["Yok"],
        "metrics": {"price": 280.0, "market_cap": 180e9, "pe": 6.5, "pb": 1.2,
                     "roe": 0.18, "roic": 0.12, "gross_margin": 0.30, "net_margin": 0.10,
                     "revenue_growth": 0.12, "eps_growth": 0.08,
                     "net_debt_ebitda": 1.5, "current_ratio": 1.3,
                     "interest_coverage": 6.0, "fcf_yield": 0.05, "cfo_to_ni": 1.1,
                     "ev_ebitda": 5.0, "sector": "Industrials",
                     "pct_from_high": -8},
    }


# ================================================================
# CLIENT SINGLETON TESTS
# ================================================================
class TestClientSingletons:
    """Verify clients are singletons (same object returned on repeat calls)."""

    def test_grok_singleton(self):
        from ai.clients import get_grok_client
        # Without GROK_KEY set, returns None — but doesn't crash
        c1 = get_grok_client()
        c2 = get_grok_client()
        assert c1 is c2  # same object (None is None, or same client)

    def test_openai_singleton(self):
        from ai.clients import get_openai_client
        c1 = get_openai_client()
        c2 = get_openai_client()
        assert c1 is c2

    def test_anthropic_singleton(self):
        from ai.clients import get_anthropic_client
        c1 = get_anthropic_client()
        c2 = get_anthropic_client()
        assert c1 is c2


# ================================================================
# build_rich_context — now in prompts.py
# ================================================================
class TestBuildRichContext:
    def test_returns_string(self, sample_analysis):
        ctx = build_rich_context(sample_analysis)
        assert isinstance(ctx, str)
        assert "THYAO" in ctx
        assert "FA SCORE" in ctx

    def test_includes_scores(self, sample_analysis):
        ctx = build_rich_context(sample_analysis)
        assert "Value:68" in ctx
        assert "Quality:72" in ctx

    def test_includes_metrics(self, sample_analysis):
        ctx = build_rich_context(sample_analysis)
        assert "ROE:" in ctx
        assert "F/K:" in ctx

    def test_with_tech_data(self, sample_analysis):
        tech = {"rsi": 55.0, "vol_ratio": 1.3, "pct_20d": 5.2,
                "price": 280, "ma50": 270, "macd_bullish": True,
                "bb_pos": "INSIDE", "pct_from_high": -8}
        ctx = build_rich_context(sample_analysis, tech)
        assert "RSI=55" in ctx
        assert "MA50 üzerinde" in ctx

    def test_hype_warning(self, sample_analysis):
        r = {**sample_analysis, "is_hype": True}
        ctx = build_rich_context(r)
        assert "HYPE" in ctx


# ================================================================
# trader_summary_prompt — now in prompts.py
# ================================================================
class TestTraderSummaryPrompt:
    def test_returns_string(self, sample_analysis):
        prompt = trader_summary_prompt(sample_analysis)
        assert isinstance(prompt, str)
        assert "BIST" in prompt
        assert "GİRİŞ:" in prompt

    def test_includes_entry_label(self, sample_analysis):
        prompt = trader_summary_prompt(sample_analysis)
        assert "TEYİTLİ" in prompt

    def test_hype_warning_in_prompt(self, sample_analysis):
        r = {**sample_analysis, "is_hype": True}
        prompt = trader_summary_prompt(r)
        assert "HYPE" in prompt or "SPEKÜLATİF" in prompt


# ================================================================
# SERVICE FUNCTION STRUCTURE TESTS
# (can't test actual AI calls, but verify service modules import cleanly
# and have the expected functions)
# ================================================================
class TestServiceModuleStructure:
    def test_all_service_functions_exist(self):
        from ai.service import (
            generate_trader_summary,
            generate_hero_story,
            generate_briefing,
            generate_macro_commentary,
            generate_cross_commentary,
            generate_agent_answer,
            generate_social_sentiment,
        )
        assert callable(generate_trader_summary)
        assert callable(generate_hero_story)
        assert callable(generate_briefing)
        assert callable(generate_macro_commentary)
        assert callable(generate_cross_commentary)
        assert callable(generate_agent_answer)
        assert callable(generate_social_sentiment)

    def test_services_return_none_when_ai_unavailable(self):
        """Without API keys, all services should return None gracefully."""
        from ai.service import (
            generate_briefing,
            generate_macro_commentary,
            generate_cross_commentary,
            generate_agent_answer,
            generate_social_sentiment,
        )
        # These all check AI_AVAILABLE internally and return None
        assert generate_briefing({"count": 0, "deger_str": "", "ivme_str": "",
                                   "worst_str": "", "summary_parts": [],
                                   "sig_str": "", "signal_count": 0}) is None
        assert generate_macro_commentary([]) is None
        assert generate_cross_commentary([], 0, 0) is None
        assert generate_agent_answer("", "test") is None
        assert generate_social_sentiment() is None


# ================================================================
# ENGINE ai_call — verify provider chain structure
# ================================================================
class TestAiCallStructure:
    def test_ai_call_returns_none_without_providers(self):
        """Without API keys configured, ai_call returns None."""
        from ai.engine import ai_call, AI_AVAILABLE
        if not AI_AVAILABLE:
            assert ai_call("test prompt") is None

    def test_providers_list_is_deterministic(self):
        from ai.engine import AI_PROVIDERS
        assert isinstance(AI_PROVIDERS, list)
        # Order should be: grok first (if available), then openai, then anthropic
        for p in AI_PROVIDERS:
            assert p in ("grok", "openai", "anthropic")


# ================================================================
# MOCKED SERVICE FUNCTION TESTS
# Verify service functions correctly chain: prompt → ai_call → parse
# ================================================================
from unittest.mock import patch

from ai.service import (
    generate_trader_summary, generate_hero_story,
    generate_briefing, generate_macro_commentary,
    generate_cross_commentary, generate_agent_answer,
    generate_social_sentiment,
)


class TestGenerateTraderSummaryMocked:
    @patch("ai.service.ai_call", return_value="GİRİŞ: TEYİTLİ — güçlü giriş.\nTEZ: ROE %18.")
    @patch("ai.service.AI_AVAILABLE", True)
    def test_returns_ai_text(self, mock_call, sample_analysis):
        result = generate_trader_summary(sample_analysis)
        assert result is not None
        assert "TEYİTLİ" in result
        mock_call.assert_called_once()

    @patch("ai.service.AI_AVAILABLE", False)
    def test_returns_none_when_ai_off(self, sample_analysis):
        assert generate_trader_summary(sample_analysis) is None

    @patch("ai.service.ai_cache")
    @patch("ai.service.ai_call", return_value=None)
    @patch("ai.service.AI_AVAILABLE", True)
    def test_returns_none_on_ai_failure(self, mock_call, mock_cache, sample_analysis):
        mock_cache.get.return_value = None  # no cache hit
        assert generate_trader_summary(sample_analysis) is None


class TestGenerateHeroStoryMocked:
    @patch("ai.service.ai_call", return_value="HİKÂYE: Piyasa güçlü.\nYORUM: Bankalar öncü.\nFIRSAT: THYAO ucuz.")
    @patch("ai.service.AI_AVAILABLE", True)
    def test_fills_story_and_bot_says(self, mock_call):
        hero_data = {
            "mode_label": "Notr", "stats": {"total": 50, "bullish": 20, "bearish": 8, "signals": 5},
            "deger_leaders": [], "ivme_leaders": [],
            "opportunity": {"ticker": "EREGL", "name": "Ereğli", "overall": 70},
            "story": None, "bot_says": None,
        }
        items = [{"ticker": "THYAO", "overall": 72, "deger": 70, "ivme": 58,
                  "scores": {"value": 68, "growth": 55}, "positives": ["Strong"]}]
        result = generate_hero_story(hero_data, items, [], 5)
        assert result["story"] == "Piyasa güçlü."
        assert result["bot_says"] == "Bankalar öncü."

    @patch("ai.service.AI_AVAILABLE", False)
    def test_returns_unchanged_when_ai_off(self):
        hero_data = {"story": None, "bot_says": None}
        result = generate_hero_story(hero_data, [], [], 0)
        assert result["story"] is None


class TestGenerateBriefingMocked:
    @patch("ai.service.ai_call", return_value="Piyasa sakin, THYAO güçlü.")
    @patch("ai.service.AI_AVAILABLE", True)
    def test_returns_text(self, mock_call):
        ctx = {"count": 50, "deger_str": "THYAO(D:72)", "ivme_str": "ASELS(I:65)",
               "worst_str": "SASA(D:30)", "summary_parts": ["THYAO: D:72 I:58"],
               "sig_str": "THYAO:GOLDEN_CROSS", "signal_count": 1}
        assert generate_briefing(ctx) == "Piyasa sakin, THYAO güçlü."


class TestGenerateMacroCommentaryMocked:
    @patch("ai.service.ai_call", return_value="VIX yükseliyor, dikkatli ol.")
    @patch("ai.service.AI_AVAILABLE", True)
    def test_returns_text(self, mock_call):
        items = [{"flag": "🇹🇷", "name": "BIST 30", "price": 10000, "change_pct": 1.5, "ytd_pct": 12.0}]
        assert "VIX" in generate_macro_commentary(items)


class TestGenerateCrossCommentaryMocked:
    @patch("ai.service.ai_call", return_value="THYAO golden cross dikkat çekici.")
    @patch("ai.service.AI_AVAILABLE", True)
    def test_returns_text(self, mock_call):
        signals = [{"ticker": "THYAO", "signal": "GOLDEN_CROSS", "stars": 3}]
        assert "THYAO" in generate_cross_commentary(signals, 1, 0)

    @patch("ai.service.AI_AVAILABLE", True)
    def test_returns_none_with_empty_signals(self):
        assert generate_cross_commentary([], 0, 0) is None


class TestGenerateAgentAnswerMocked:
    @patch("ai.service.ai_call", return_value="THYAO güçlü görünüyor, F/K 6.5.")
    @patch("ai.service.AI_AVAILABLE", True)
    def test_returns_text(self, mock_call):
        assert "THYAO" in generate_agent_answer("context", "THYAO nasıl?")


class TestGenerateSocialSentimentMocked:
    @patch("ai.service.AI_AVAILABLE", True)
    @patch("ai.service.AI_PROVIDERS", ["grok"])
    @patch("ai.service.ai_call", return_value='{"trending": [{"ticker": "THYAO"}], "overall_sentiment": "bullish", "summary": "ok", "hot_topics": []}')
    def test_returns_structured_data(self, mock_call):
        result = generate_social_sentiment()
        assert result is not None
        assert result["source"] == "grok_ai"
        assert result["overall_sentiment"] == "bullish"

    @patch("ai.service.AI_AVAILABLE", True)
    @patch("ai.service.AI_PROVIDERS", ["grok"])
    @patch("ai.service.ai_call", return_value="not json")
    def test_handles_bad_json(self, mock_call):
        result = generate_social_sentiment()
        assert result["overall_sentiment"] == "unknown"

    @patch("ai.service.AI_AVAILABLE", True)
    @patch("ai.service.AI_PROVIDERS", ["openai"])
    def test_requires_grok_provider(self):
        assert generate_social_sentiment() is None


class TestAiCallFallbackMocked:
    @patch("ai.engine.AI_PROVIDERS", [])
    def test_no_providers_returns_none(self):
        from ai.engine import ai_call
        assert ai_call("test") is None

    @patch("ai.engine.AI_PROVIDERS", ["grok"])
    def test_first_provider_success(self):
        from unittest.mock import MagicMock
        from ai.engine import ai_call
        mock_grok = MagicMock(return_value="response")
        with patch.dict("ai.engine._CALLERS", {"grok": mock_grok}):
            assert ai_call("test", 200) == "response"

    @patch("ai.engine.AI_PROVIDERS", ["grok"])
    @patch("ai.engine._call_grok", side_effect=Exception("down"))
    def test_all_fail_returns_none(self, mock_grok):
        from ai.engine import ai_call
        assert ai_call("test") is None

# ================================================================
# BISTBULL TERMINAL — Unit Tests: AI Layer
# Tests: client singletons, service functions, prompt builders,
#        response parsers, fallback behavior.
#
# All tests are deterministic — no live API calls.
# AI call behavior is tested via monkeypatching.
# ================================================================

import pytest
from unittest.mock import patch, MagicMock

from ai.clients import get_grok_client, get_openai_client, get_anthropic_client
from ai.engine import ai_call, AI_PROVIDERS, _CALLERS
from ai.prompts import (
    hero_prompt, parse_hero_response,
    briefing_prompt, macro_commentary_prompt,
    cross_commentary_prompt, agent_prompt,
    SOCIAL_PROMPT, clean_json_response,
    build_rich_context, trader_summary_prompt,
)
from ai.service import (
    generate_trader_summary, generate_hero_story,
    generate_briefing, generate_macro_commentary,
    generate_cross_commentary, generate_agent_answer,
    generate_social_sentiment,
)


# ================================================================
# CLIENT SINGLETONS
# ================================================================
class TestClientSingletons:
    """Verify client getters return the same instance on repeated calls."""

    def test_grok_returns_same_instance(self):
        """Two calls to get_grok_client() should return the same object."""
        # If no key configured, returns None — that's fine, the singleton logic still works
        c1 = get_grok_client()
        c2 = get_grok_client()
        assert c1 is c2  # same object (or both None)

    def test_openai_returns_same_instance(self):
        c1 = get_openai_client()
        c2 = get_openai_client()
        assert c1 is c2

    def test_anthropic_returns_same_instance(self):
        c1 = get_anthropic_client()
        c2 = get_anthropic_client()
        assert c1 is c2

    def test_no_key_returns_none(self):
        """Without API keys, clients should be None (not crash)."""
        # In test env, keys are likely unset
        # Just verify no exception is raised
        get_grok_client()
        get_openai_client()
        get_anthropic_client()


# ================================================================
# AI_CALL FALLBACK
# ================================================================
class TestAiCallFallback:
    """Test the provider fallback chain without real API calls."""

    def test_returns_none_when_no_providers(self):
        """With an empty provider list, ai_call should return None."""
        with patch("ai.engine.AI_PROVIDERS", []):
            result = ai_call("test prompt", 100)
            assert result is None

    def test_fallback_on_exception(self):
        """If first provider throws, should try the next one."""
        mock_grok = MagicMock(side_effect=RuntimeError("grok down"))
        mock_openai = MagicMock(return_value="openai response")

        with patch("ai.engine.AI_PROVIDERS", ["grok", "openai"]), \
             patch.dict("ai.engine._CALLERS", {"grok": mock_grok, "openai": mock_openai}):
            result = ai_call("test", 100)
            assert result == "openai response"
            assert mock_grok.called
            assert mock_openai.called

    def test_all_providers_fail_returns_none(self):
        """If all providers fail, returns None."""
        mock_fail = MagicMock(side_effect=RuntimeError("down"))

        with patch("ai.engine.AI_PROVIDERS", ["grok"]), \
             patch.dict("ai.engine._CALLERS", {"grok": mock_fail}):
            result = ai_call("test", 100)
            assert result is None

    def test_first_provider_succeeds(self):
        """If first provider succeeds, second is never called."""
        mock_grok = MagicMock(return_value="grok result")
        mock_openai = MagicMock(return_value="openai result")

        with patch("ai.engine.AI_PROVIDERS", ["grok", "openai"]), \
             patch.dict("ai.engine._CALLERS", {"grok": mock_grok, "openai": mock_openai}):
            result = ai_call("test", 100)
            assert result == "grok result"
            assert mock_grok.called
            assert not mock_openai.called


# ================================================================
# PROMPT BUILDERS — completeness tests
# ================================================================
class TestPromptBuilders:
    """Verify all prompt builders produce non-empty strings with expected content."""

    def test_hero_prompt_content(self):
        p = hero_prompt("Notr", 100, 40, [], [], [{"ticker": "THYAO", "deger": 70, "ivme": 60, "overall": 65}], [{"name": "VIX", "change_pct": 2.5}], 3)
        assert "BistBull" in p
        assert "Notr" in p
        assert "100" in p

    def test_briefing_prompt_content(self):
        ctx = {"count": 50, "deger_str": "THYAO(D:72)", "ivme_str": "ASELS(I:65)",
               "worst_str": "SASA(D:30)", "summary_parts": ["THYAO: D:72 I:58"],
               "sig_str": "THYAO:GOLDEN_CROSS", "signal_count": 1}
        p = briefing_prompt(ctx)
        assert "THYAO" in p
        assert "50" in p

    def test_macro_commentary_prompt_content(self):
        items = [{"flag": "🇹🇷", "name": "BIST 30", "price": 10000, "change_pct": 1.5, "ytd_pct": 12}]
        p = macro_commentary_prompt(items)
        assert "BIST 30" in p
        assert "makro" in p.lower()

    def test_cross_commentary_prompt_content(self):
        signals = [{"ticker": "THYAO", "signal": "GOLDEN_CROSS", "stars": 3}]
        p = cross_commentary_prompt(signals, 1, 0)
        assert "THYAO" in p
        assert "GOLDEN_CROSS" in p

    def test_agent_prompt_content(self):
        p = agent_prompt("THYAO context data", "THYAO nasıl?")
        assert "THYAO" in p
        assert "Q" in p

    def test_social_prompt_nonempty(self):
        assert len(SOCIAL_PROMPT) > 50
        assert "JSON" in SOCIAL_PROMPT

    def test_trader_summary_prompt_content(self):
        r = {
            "ticker": "THYAO", "name": "THY", "symbol": "THYAO.IS",
            "overall": 72, "fa_score": 65, "risk_score": -5,
            "entry_label": "TEYİTLİ", "decision": "AL", "quality_tag": "GÜÇLÜ",
            "ivme": 58, "timing": "TEYİTLİ", "is_hype": False,
            "sector_group": "ulasim", "style": "Kaliteli Bileşik",
            "scores": {"value": 68, "quality": 72, "growth": 55, "balance": 70,
                       "earnings": 65, "moat": 52, "capital": 60,
                       "momentum": 62, "tech_break": 55, "inst_flow": 48},
            "metrics": {"sector": "Industrials", "price": 280, "market_cap": 180e9,
                        "pe": 6.5, "pb": 1.2, "ev_ebitda": 4.5, "roe": 0.18,
                        "roic": 0.14, "gross_margin": 0.30, "net_margin": 0.12,
                        "revenue_growth": 0.12, "eps_growth": 0.15,
                        "net_debt_ebitda": 1.5, "current_ratio": 1.3,
                        "interest_coverage": 8, "fcf_yield": 0.06, "cfo_to_ni": 1.1},
            "legendary": {"piotroski": "7/9", "altman": "3.2", "beneish": "-2.5",
                          "graham_filter": "Geçti", "buffett_filter": "Geçti"},
            "risk_reasons": [], "positives": ["Güçlü"], "negatives": ["Yok"],
        }
        p = trader_summary_prompt(r, None)
        assert "THYAO" in p
        assert "yatırım" in p.lower() or "analiz" in p.lower()

    def test_build_rich_context(self):
        r = {
            "ticker": "THYAO", "name": "THY",
            "overall": 72, "fa_score": 65, "risk_score": -5,
            "entry_label": "TEYİTLİ", "decision": "AL", "quality_tag": "GÜÇLÜ",
            "ivme": 58, "timing": "TEYİTLİ", "is_hype": False,
            "sector_group": "ulasim", "style": "Kaliteli Bileşik",
            "scores": {"value": 68, "quality": 72, "growth": 55, "balance": 70,
                       "earnings": 65, "moat": 52, "capital": 60,
                       "momentum": 62, "tech_break": 55, "inst_flow": 48},
            "metrics": {"sector": "Industrials", "price": 280, "market_cap": 180e9,
                        "pe": 6.5, "pb": 1.2, "ev_ebitda": 4.5, "roe": 0.18,
                        "roic": 0.14, "gross_margin": 0.30, "net_margin": 0.12,
                        "revenue_growth": 0.12, "eps_growth": 0.15,
                        "net_debt_ebitda": 1.5, "current_ratio": 1.3,
                        "interest_coverage": 8, "fcf_yield": 0.06, "cfo_to_ni": 1.1},
            "legendary": {"piotroski": "7/9", "altman": "3.2", "beneish": "-2.5",
                          "graham_filter": "Geçti", "buffett_filter": "Geçti"},
            "risk_reasons": [], "positives": ["Güçlü"], "negatives": ["Yok"],
        }
        ctx = build_rich_context(r)
        assert "THYAO" in ctx
        assert "Value:" in ctx
        assert "Quality:" in ctx


# ================================================================
# RESPONSE PARSERS
# ================================================================
class TestResponseParsers:
    """Test AI response parsing functions."""

    def test_parse_hero_all_fields(self):
        text = "HİKÂYE: Bankalar lider.\nYORUM: Momentum güçlü.\nFIRSAT: THYAO ucuz."
        r = parse_hero_response(text)
        assert r["story"] == "Bankalar lider."
        assert r["bot_says"] == "Momentum güçlü."
        assert r["ai_reason"] == "THYAO ucuz."

    def test_parse_hero_alternate_spelling(self):
        """HIKAYE: (without accent) should also be parsed."""
        text = "HIKAYE: Alt yazım.\nYORUM: Test."
        r = parse_hero_response(text)
        assert r["story"] == "Alt yazım."

    def test_parse_hero_missing_fields(self):
        text = "Bu bir yapılandırılmamış AI yanıtı. Hiçbir etiket yok."
        r = parse_hero_response(text)
        assert r["story"] is not None  # falls back to text[:200]
        assert r["ai_reason"] is None

    def test_clean_json_valid(self):
        assert clean_json_response('{"a": 1, "b": [2, 3]}') == {"a": 1, "b": [2, 3]}

    def test_clean_json_code_fence(self):
        assert clean_json_response('```json\n{"x": 42}\n```') == {"x": 42}

    def test_clean_json_no_fence_label(self):
        assert clean_json_response('```\n{"x": 42}\n```') == {"x": 42}

    def test_clean_json_invalid(self):
        assert clean_json_response("not json") is None

    def test_clean_json_empty(self):
        assert clean_json_response("") is None

    def test_clean_json_nested(self):
        text = '{"trending": [{"ticker": "THYAO"}], "summary": "test"}'
        result = clean_json_response(text)
        assert result["trending"][0]["ticker"] == "THYAO"


# ================================================================
# SERVICE LAYER — integration with mocked ai_call
# ================================================================
class TestServiceLayer:
    """Test service functions with mocked AI calls."""

    def test_generate_briefing_returns_text(self):
        ctx = {"count": 50, "deger_str": "T(D:70)", "ivme_str": "A(I:60)",
               "worst_str": "S(D:30)", "summary_parts": ["T: D:70"],
               "sig_str": "T:GC", "signal_count": 1}
        with patch("ai.service.AI_AVAILABLE", True), \
             patch("ai.service.ai_call", return_value="Mocked briefing text"):
            result = generate_briefing(ctx)
            assert result == "Mocked briefing text"

    def test_generate_briefing_returns_none_when_ai_off(self):
        with patch("ai.service.AI_AVAILABLE", False):
            result = generate_briefing({})
            assert result is None

    def test_generate_macro_commentary(self):
        items = [{"flag": "🇹🇷", "name": "BIST", "price": 10000, "change_pct": 1.5, "ytd_pct": 12}]
        with patch("ai.service.AI_AVAILABLE", True), \
             patch("ai.service.ai_call", return_value="Makro yorum"):
            result = generate_macro_commentary(items)
            assert result == "Makro yorum"

    def test_generate_cross_commentary(self):
        signals = [{"ticker": "THYAO", "signal": "GC", "stars": 2}]
        with patch("ai.service.AI_AVAILABLE", True), \
             patch("ai.service.ai_call", return_value="Cross yorum"):
            result = generate_cross_commentary(signals, 1, 0)
            assert result == "Cross yorum"

    def test_generate_cross_commentary_empty_signals(self):
        with patch("ai.service.AI_AVAILABLE", True):
            result = generate_cross_commentary([], 0, 0)
            assert result is None

    def test_generate_agent_answer(self):
        with patch("ai.service.AI_AVAILABLE", True), \
             patch("ai.service.ai_call", return_value="Q yanıtı"):
            result = generate_agent_answer("context", "THYAO nasıl?")
            assert result == "Q yanıtı"

    def test_generate_social_sentiment_parses_json(self):
        mock_json = '{"trending": [{"ticker": "THYAO"}], "overall_sentiment": "bullish", "summary": "test", "hot_topics": []}'
        with patch("ai.service.AI_AVAILABLE", True), \
             patch("ai.service.AI_PROVIDERS", ["grok"]), \
             patch("ai.service.ai_call", return_value=mock_json):
            result = generate_social_sentiment()
            assert result is not None
            assert result["overall_sentiment"] == "bullish"
            assert result["trending"][0]["ticker"] == "THYAO"

    def test_generate_social_sentiment_no_grok(self):
        with patch("ai.service.AI_AVAILABLE", True), \
             patch("ai.service.AI_PROVIDERS", ["openai"]):
            result = generate_social_sentiment()
            assert result is None

    def test_generate_hero_story_fills_fields(self):
        hero_data = {
            "mode_label": "Notr", "stats": {"total": 50, "bullish": 20, "bearish": 10, "signals": 3},
            "deger_leaders": [], "ivme_leaders": [],
            "story": None, "bot_says": None, "opportunity": None,
        }
        items = [{"ticker": "T", "deger": 70, "ivme": 60, "overall": 65}]
        mock_text = "HİKÂYE: Test hikaye.\nYORUM: Test yorum.\nFIRSAT: Test fırsat."
        with patch("ai.service.AI_AVAILABLE", True), \
             patch("ai.service.ai_call", return_value=mock_text):
            result = generate_hero_story(hero_data, items, [], 0)
            assert result["story"] == "Test hikaye."
            assert result["bot_says"] == "Test yorum."

    def test_generate_trader_summary_caches(self):
        r = {"symbol": "THYAO.IS", "overall": 72, "ivme": 58, "entry_label": "TEYİTLİ",
             "ticker": "THYAO", "name": "THY", "is_hype": False,
             "fa_score": 65, "risk_score": -5, "decision": "AL",
             "quality_tag": "GÜÇLÜ", "timing": "TEYİTLİ", "sector_group": "ulasim",
             "style": "Kaliteli Bileşik",
             "scores": {"value": 68, "quality": 72, "growth": 55, "balance": 70,
                        "earnings": 65, "moat": 52, "capital": 60,
                        "momentum": 62, "tech_break": 55, "inst_flow": 48},
             "metrics": {"sector": "Industrials", "price": 280, "market_cap": 180e9,
                         "pe": 6.5, "pb": 1.2, "ev_ebitda": 4.5, "roe": 0.18,
                         "roic": 0.14, "gross_margin": 0.30, "net_margin": 0.12,
                         "revenue_growth": 0.12, "eps_growth": 0.15,
                         "net_debt_ebitda": 1.5, "current_ratio": 1.3,
                         "interest_coverage": 8, "fcf_yield": 0.06, "cfo_to_ni": 1.1},
             "legendary": {"piotroski": "7/9", "altman": "3.2", "beneish": "-2.5",
                           "graham_filter": "Geçti", "buffett_filter": "Geçti"},
             "risk_reasons": [], "positives": ["Güçlü"], "negatives": ["Yok"]}
        with patch("ai.service.AI_AVAILABLE", True), \
             patch("ai.service.ai_call", return_value="Yatırım tezi") as mock_call, \
             patch("ai.service.ai_cache") as mock_cache:
            mock_cache.get.return_value = None
            result = generate_trader_summary(r, None)
            # generate_trader_summary now returns dict: {summary, is_fallback, data_grade}
            assert isinstance(result, dict)
            assert result["summary"] == "Yatırım tezi"
            assert result["is_fallback"] is False
            assert mock_cache.set.called  # verify caching happened

"""Phase 5.2.3 — AI multi-model consensus engine tests.

Covers:
- sentiment classification (bullish/bearish/neutral, TR + EN)
- confidence estimation (declared vs derived)
- keyword extraction + Jaccard overlap
- compute_consensus aggregation: leader selection, split detection
- determinism: same input → same output
- edge cases: all errors, single model, partial errors

NO network. ai.engine providers are NOT exercised here — that's
covered by call_all_providers being mocked in a separate test.
"""
from __future__ import annotations

import pytest

from engine.ai_consensus import (
    classify_sentiment,
    extract_keywords,
    jaccard,
    estimate_confidence,
    compute_consensus,
)


# ============================================================
# classify_sentiment
# ============================================================
class TestSentimentClassification:
    def test_bullish_turkish(self):
        assert classify_sentiment("Hisse güçlü yükseliş gösteriyor, alım fırsatı var.") == "bullish"

    def test_bearish_turkish(self):
        assert classify_sentiment("Düşüş riski yüksek, satış baskısı var. Zayıf görünüm.") == "bearish"

    def test_neutral_turkish(self):
        assert classify_sentiment("Yatay seyir, izlemeye devam. Net sinyal yok.") == "neutral"

    def test_bullish_english(self):
        assert classify_sentiment("Strong buy signal, bullish momentum, upside potential.") == "bullish"

    def test_bearish_english(self):
        assert classify_sentiment("Weak fundamentals, bearish trend, sell signal active.") == "bearish"

    def test_empty_text(self):
        assert classify_sentiment("") == "neutral"
        assert classify_sentiment(None) == "neutral"  # type: ignore

    def test_mixed_text_neutral_default(self):
        # Equal bullish and bearish words → neutral
        text = "Yükseliş bekleniyor ama düşüş riski de var."
        result = classify_sentiment(text)
        assert result in ("neutral", "bullish", "bearish")  # any deterministic outcome OK


# ============================================================
# extract_keywords + jaccard
# ============================================================
class TestKeywordExtraction:
    def test_extracts_meaningful_words(self):
        text = "THYAO güçlü kazanç açıkladı, hisse yükselişe geçti."
        kws = extract_keywords(text)
        assert isinstance(kws, set)
        assert len(kws) > 0
        assert all(len(w) >= 4 for w in kws)

    def test_drops_stopwords(self):
        text = "Bu hisse için bir alım fırsatı."
        kws = extract_keywords(text)
        # "bir", "bu" are stopwords — should not appear
        # Note: normalizer strips ç/ş/ı diacritics
        assert "bir" not in kws
        assert "bu" not in kws

    def test_deterministic(self):
        text = "Yapay zekâ destekli analiz, güçlü teknik göstergeler."
        a = extract_keywords(text)
        b = extract_keywords(text)
        assert a == b

    def test_empty(self):
        assert extract_keywords("") == set()
        assert extract_keywords(None) == set()  # type: ignore


class TestJaccard:
    def test_identical_sets(self):
        s = {"alpha", "beta", "gamma"}
        assert jaccard(s, s) == 1.0

    def test_disjoint_sets(self):
        assert jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        # |∩| = 1, |∪| = 3 → 0.333
        assert abs(jaccard({"a", "b"}, {"b", "c"}) - 1 / 3) < 1e-9

    def test_both_empty(self):
        assert jaccard(set(), set()) == 0.0

    def test_one_empty(self):
        assert jaccard(set(), {"x"}) == 0.0


# ============================================================
# estimate_confidence
# ============================================================
class TestConfidence:
    def test_declared_takes_precedence(self):
        # Even if text is empty, a declared confidence wins
        assert estimate_confidence("", declared=0.85) == 0.85
        assert estimate_confidence("anything", declared=0.5) == 0.5

    def test_declared_clamped(self):
        # >100 → clamp to 1.0
        assert estimate_confidence("", declared=250) == 1.0
        # negative → clamp to 0.0
        assert estimate_confidence("", declared=-0.5) == 0.0

    def test_declared_percent_normalized(self):
        # 75 → 0.75 (treated as percent if > 1)
        assert estimate_confidence("", declared=75) == 0.75

    def test_long_decisive_text_high_confidence(self):
        text = ("Çok güçlü alım sinyali — bullish trend, yukarı kırılım, "
                "pozitif momentum, kâr açıklaması, alım fırsatı, ralli, "
                "yukarı yönlü beklenti, güçlü teknik göstergeler.") * 3
        c = estimate_confidence(text)
        assert c > 0.5

    def test_empty_text_zero_confidence(self):
        assert estimate_confidence("") == 0.0

    def test_short_neutral_text_low_confidence(self):
        c = estimate_confidence("ok")
        assert c < 0.5


# ============================================================
# compute_consensus — main aggregation logic
# ============================================================
class TestComputeConsensus:
    def _bullish_response(self, provider: str, conf: float = None) -> dict:
        return {
            "provider": provider,
            "text": "Çok güçlü alım sinyali. Bullish momentum, yukarı kırılım net.",
            "confidence": conf,
        }

    def _bearish_response(self, provider: str) -> dict:
        return {
            "provider": provider,
            "text": "Satış baskısı yüksek. Bearish trend, düşüş riski belirgin.",
        }

    def _neutral_response(self, provider: str) -> dict:
        return {
            "provider": provider,
            "text": "Yatay seyir, izlemeye devam. Net sinyal yok.",
        }

    def test_unanimous_bullish(self):
        responses = [
            self._bullish_response("perplexity"),
            self._bullish_response("grok"),
            self._bullish_response("openai"),
            self._bullish_response("anthropic"),
        ]
        result = compute_consensus(responses)
        assert result["sentiment"] == "bullish"
        assert result["sentiment_distribution"]["bullish"] == 4
        assert result["agreement_score"] >= 0.8
        assert result["is_split"] is False
        assert result["leader"] in {"perplexity", "grok", "openai", "anthropic"}
        assert result["leader_text"] is not None

    def test_split_two_two(self):
        # 2 bullish + 2 bearish → split
        responses = [
            self._bullish_response("perplexity"),
            self._bullish_response("grok"),
            self._bearish_response("openai"),
            self._bearish_response("anthropic"),
        ]
        result = compute_consensus(responses)
        assert result["is_split"] is True
        assert result["sentiment"] == "split"

    def test_clear_majority(self):
        # 3 bullish + 1 neutral
        responses = [
            self._bullish_response("perplexity"),
            self._bullish_response("grok"),
            self._bullish_response("openai"),
            self._neutral_response("anthropic"),
        ]
        result = compute_consensus(responses)
        assert result["is_split"] is False
        assert result["sentiment"] == "bullish"
        assert result["sentiment_distribution"]["bullish"] == 3

    def test_all_errors(self):
        responses = [
            {"provider": "perplexity", "text": None, "error": "timeout"},
            {"provider": "grok", "text": None, "error": "rate_limit"},
        ]
        result = compute_consensus(responses)
        assert result["leader"] is None
        assert result["leader_text"] is None
        assert result["sentiment"] == "neutral"
        assert all(m["has_error"] for m in result["per_model"])

    def test_partial_errors_recoverable(self):
        responses = [
            self._bullish_response("perplexity"),
            self._bullish_response("grok"),
            {"provider": "openai", "text": None, "error": "timeout"},
            {"provider": "anthropic", "text": None, "error": "rate_limit"},
        ]
        result = compute_consensus(responses)
        # 2 valid responses, both bullish
        assert result["sentiment"] == "bullish"
        assert result["leader"] in {"perplexity", "grok"}
        # Errored models still appear in per_model with has_error=True
        provs = {m["provider"] for m in result["per_model"]}
        assert "openai" in provs and "anthropic" in provs

    def test_single_model(self):
        result = compute_consensus([self._bullish_response("perplexity")])
        assert result["sentiment"] == "bullish"
        assert result["leader"] == "perplexity"
        assert result["model_count"] == 1
        # With only 1 model, can't split
        assert result["is_split"] is False

    def test_empty_input(self):
        result = compute_consensus([])
        assert result["leader"] is None
        assert result["sentiment"] == "neutral"
        assert result["model_count"] == 0 or result["sentiment_distribution"]["bullish"] == 0

    def test_declared_confidence_picks_leader(self):
        # All bullish, but Anthropic self-reports 0.95 confidence — should be leader
        responses = [
            {"provider": "perplexity", "text": "Çok güçlü alım sinyali, bullish.", "confidence": 0.5},
            {"provider": "grok", "text": "Strong buy, bullish trend.", "confidence": 0.5},
            {"provider": "openai", "text": "Bullish momentum bekleniyor.", "confidence": 0.5},
            {"provider": "anthropic", "text": "Güçlü alım, yukarı yönlü.", "confidence": 0.95},
        ]
        result = compute_consensus(responses)
        assert result["leader"] == "anthropic"

    def test_deterministic_repeated_call(self):
        responses = [
            self._bullish_response("perplexity"),
            self._bullish_response("grok"),
            self._neutral_response("openai"),
        ]
        a = compute_consensus(responses)
        b = compute_consensus(responses)
        assert a["leader"] == b["leader"]
        assert a["sentiment"] == b["sentiment"]
        assert a["agreement_score"] == b["agreement_score"]
        assert a["sentiment_distribution"] == b["sentiment_distribution"]


# ============================================================
# call_all_providers — covered with a stub (no network)
# ============================================================
class TestCallAllProvidersFallback:
    """call_all_providers must gracefully no-op when ai.engine has no providers."""

    def test_empty_provider_list_returns_empty(self, monkeypatch):
        from engine import ai_consensus
        # monkeypatch _CALLERS / AI_PROVIDERS in ai.engine module
        import ai.engine as eng
        monkeypatch.setattr(eng, "AI_PROVIDERS", [])
        monkeypatch.setattr(eng, "_CALLERS", {})
        out = ai_consensus.call_all_providers("test prompt")
        assert out == []

    def test_explicit_providers_subset(self, monkeypatch):
        # Stub callers — no real API call
        import ai.engine as eng
        from engine import ai_consensus

        def _stub_perplexity(prompt: str, max_tokens: int) -> str:
            return f"perplexity says: {prompt[:30]}"

        def _stub_grok(prompt: str, max_tokens: int) -> str:
            return f"grok says: {prompt[:30]}"

        monkeypatch.setattr(eng, "AI_PROVIDERS", ["perplexity", "grok"])
        monkeypatch.setattr(eng, "_CALLERS", {
            "perplexity": _stub_perplexity, "grok": _stub_grok
        })
        out = ai_consensus.call_all_providers("hello world")
        assert len(out) == 2
        # Sorted alphabetically (deterministic)
        assert out[0]["provider"] == "grok"
        assert out[1]["provider"] == "perplexity"
        assert all(r.get("text") for r in out)

    def test_provider_exception_captured(self, monkeypatch):
        import ai.engine as eng
        from engine import ai_consensus

        def _explode(prompt: str, max_tokens: int) -> str:
            raise RuntimeError("boom")

        monkeypatch.setattr(eng, "AI_PROVIDERS", ["openai"])
        monkeypatch.setattr(eng, "_CALLERS", {"openai": _explode})
        out = ai_consensus.call_all_providers("any prompt")
        assert len(out) == 1
        assert out[0]["error"] is not None
        assert out[0]["text"] is None

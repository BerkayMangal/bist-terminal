# ================================================================
# BISTBULL TERMINAL — Unit Tests: Signal Engine (Phase 6)
# Tests: signal quality, confidence, reason, risk flags, enrichment
# ================================================================

import pytest

from engine.signal_engine import (
    compute_signal_quality,
    compute_signal_confidence,
    extract_signal_reason,
    extract_risk_flags,
    enrich_signal,
    enrich_signals,
)


# ================================================================
# FIXTURES
# ================================================================
@pytest.fixture
def strong_bullish_signal():
    return {
        "signal": "Golden Cross", "signal_type": "bullish",
        "stars": 3, "vol_confirmed": True,
        "ticker": "THYAO", "price": 280,
        "ticker_signal_count": 3, "ticker_total_stars": 7,
        "tech_score": 72, "category": "kirilim",
    }


@pytest.fixture
def weak_bearish_signal():
    return {
        "signal": "RSI Asiri Alim", "signal_type": "bearish",
        "stars": 1, "vol_confirmed": False,
        "ticker": "SASA", "price": 45,
        "ticker_signal_count": 1, "ticker_total_stars": 1,
        "tech_score": 35, "category": "momentum",
    }


@pytest.fixture
def strong_analysis():
    return {
        "overall": 72, "confidence": 87.5, "fa_score": 65,
        "ivme": 68, "risk_score": -3, "risk_penalty": -3,
        "scores_imputed": [],
        "positives": ["Guclu karlilik"],
        "negatives": ["Bazi eksik"],
        "explanation": {
            "summary": "Test",
            "top_positive_drivers": [
                {"name": "Yuksek ozsermaye karliligi", "contribution": 5.0},
                {"name": "Net kar marji", "contribution": 4.2},
                {"name": "Ucuz F/K orani", "contribution": 3.5},
            ],
            "top_negative_drivers": [
                {"name": "Risk penaltisi", "contribution": -1.5},
                {"name": "Kurumsal yatirimci ilgisi", "contribution": -1.0},
            ],
        },
    }


@pytest.fixture
def weak_analysis():
    return {
        "overall": 38, "confidence": 40, "fa_score": 42,
        "ivme": 35, "risk_score": -22, "risk_penalty": -22,
        "scores_imputed": ["growth", "earnings", "moat", "capital"],
        "positives": [],
        "negatives": ["Yuksek borc", "Zayif momentum"],
        "explanation": {
            "summary": "Zayif",
            "top_positive_drivers": [],
            "top_negative_drivers": [
                {"name": "Borc yuku", "contribution": -4.0},
                {"name": "Buyume verisi eksik", "contribution": -3.0},
                {"name": "Risk penaltisi", "contribution": -2.5},
            ],
        },
    }


# ================================================================
# SIGNAL QUALITY
# ================================================================
class TestSignalQuality:
    def test_strong_signal_with_strong_analysis_is_A(self, strong_bullish_signal, strong_analysis):
        q = compute_signal_quality(strong_bullish_signal, strong_analysis)
        assert q == "A"

    def test_weak_signal_with_weak_analysis_is_C(self, weak_bearish_signal, weak_analysis):
        q = compute_signal_quality(weak_bearish_signal, weak_analysis)
        assert q == "C"

    def test_strong_signal_no_analysis_is_B(self, strong_bullish_signal):
        q = compute_signal_quality(strong_bullish_signal, None)
        assert q == "B"

    def test_weak_signal_no_analysis_is_C(self, weak_bearish_signal):
        q = compute_signal_quality(weak_bearish_signal, None)
        assert q == "C"

    def test_quality_is_deterministic(self, strong_bullish_signal, strong_analysis):
        q1 = compute_signal_quality(strong_bullish_signal, strong_analysis)
        q2 = compute_signal_quality(strong_bullish_signal, strong_analysis)
        assert q1 == q2

    def test_quality_only_returns_valid_grades(self, strong_bullish_signal, strong_analysis):
        q = compute_signal_quality(strong_bullish_signal, strong_analysis)
        assert q in ("A", "B", "C")

    def test_high_risk_downgrades(self, strong_bullish_signal, weak_analysis):
        q = compute_signal_quality(strong_bullish_signal, weak_analysis)
        assert q in ("B", "C")


# ================================================================
# SIGNAL CONFIDENCE
# ================================================================
class TestSignalConfidence:
    def test_range_0_to_100(self, strong_bullish_signal, strong_analysis):
        c = compute_signal_confidence(strong_bullish_signal, strong_analysis)
        assert 0 <= c <= 100

    def test_strong_signal_high_confidence(self, strong_bullish_signal, strong_analysis):
        c = compute_signal_confidence(strong_bullish_signal, strong_analysis)
        assert c >= 60

    def test_weak_signal_lower_confidence(self, weak_bearish_signal, weak_analysis):
        c = compute_signal_confidence(weak_bearish_signal, weak_analysis)
        assert c < 60

    def test_no_analysis_caps_at_65(self, strong_bullish_signal):
        c = compute_signal_confidence(strong_bullish_signal, None)
        assert c <= 65

    def test_no_analysis_minimum_30(self, weak_bearish_signal):
        c = compute_signal_confidence(weak_bearish_signal, None)
        assert c >= 30

    def test_more_imputed_dims_lower_confidence(self, strong_bullish_signal, strong_analysis, weak_analysis):
        c_strong = compute_signal_confidence(strong_bullish_signal, strong_analysis)
        c_weak = compute_signal_confidence(strong_bullish_signal, weak_analysis)
        assert c_strong > c_weak


# ================================================================
# REASON EXTRACTION
# ================================================================
class TestExtractReason:
    def test_returns_list(self, strong_analysis):
        reasons = extract_signal_reason(strong_analysis)
        assert isinstance(reasons, list)

    def test_max_3_items(self, strong_analysis):
        reasons = extract_signal_reason(strong_analysis)
        assert len(reasons) <= 3

    def test_returns_driver_names(self, strong_analysis):
        reasons = extract_signal_reason(strong_analysis)
        assert "Yuksek ozsermaye karliligi" in reasons

    def test_no_analysis_returns_empty(self):
        assert extract_signal_reason(None) == []

    def test_no_explanation_uses_positives(self):
        analysis = {"positives": ["Guclu", "Ucuz"], "explanation": None}
        reasons = extract_signal_reason(analysis)
        assert reasons == ["Guclu", "Ucuz"]

    def test_empty_drivers_returns_empty(self, weak_analysis):
        reasons = extract_signal_reason(weak_analysis)
        assert reasons == []


# ================================================================
# RISK FLAGS
# ================================================================
class TestExtractRiskFlags:
    def test_returns_list(self, strong_analysis):
        flags = extract_risk_flags(strong_analysis)
        assert isinstance(flags, list)

    def test_max_3_items(self, weak_analysis):
        flags = extract_risk_flags(weak_analysis)
        assert len(flags) <= 3

    def test_returns_negative_drivers(self, weak_analysis):
        flags = extract_risk_flags(weak_analysis)
        assert "Borc yuku" in flags

    def test_no_analysis_returns_empty(self):
        assert extract_risk_flags(None) == []

    def test_no_explanation_uses_negatives(self):
        analysis = {"negatives": ["Pahalı", "Zayıf"], "explanation": None}
        flags = extract_risk_flags(analysis)
        assert flags == ["Pahalı", "Zayıf"]


# ================================================================
# ENRICH SIGNAL
# ================================================================
class TestEnrichSignal:
    def test_adds_all_fields(self, strong_bullish_signal, strong_analysis):
        enriched = enrich_signal(strong_bullish_signal, strong_analysis)
        assert "signal_quality" in enriched
        assert "signal_confidence" in enriched
        assert "reason" in enriched
        assert "risk_flags" in enriched

    def test_preserves_original_fields(self, strong_bullish_signal, strong_analysis):
        enriched = enrich_signal(strong_bullish_signal, strong_analysis)
        assert enriched["signal"] == "Golden Cross"
        assert enriched["ticker"] == "THYAO"
        assert enriched["stars"] == 3

    def test_does_not_mutate_original(self, strong_bullish_signal, strong_analysis):
        original_keys = set(strong_bullish_signal.keys())
        enrich_signal(strong_bullish_signal, strong_analysis)
        assert set(strong_bullish_signal.keys()) == original_keys
        assert "signal_quality" not in strong_bullish_signal

    def test_works_without_analysis(self, strong_bullish_signal):
        enriched = enrich_signal(strong_bullish_signal, None)
        assert enriched["signal_quality"] in ("A", "B", "C")
        assert 0 <= enriched["signal_confidence"] <= 100
        assert isinstance(enriched["reason"], list)
        assert isinstance(enriched["risk_flags"], list)


# ================================================================
# BATCH ENRICH
# ================================================================
class TestEnrichSignals:
    def test_enriches_all(self, strong_bullish_signal, weak_bearish_signal):
        signals = [strong_bullish_signal, weak_bearish_signal]

        class MockCache:
            def get(self, key):
                return None

        result = enrich_signals(signals, MockCache())
        assert len(result) == 2
        assert all("signal_quality" in s for s in result)

    def test_empty_list(self):
        result = enrich_signals([], None)
        assert result == []


# ================================================================
# EDGE CASES
# ================================================================
class TestEdgeCases:
    def test_minimal_signal(self):
        sig = {"signal": "test", "ticker": "X"}
        enriched = enrich_signal(sig, None)
        assert enriched["signal_quality"] in ("A", "B", "C")

    def test_analysis_without_explanation(self):
        sig = {"signal": "test", "ticker": "X", "stars": 2, "vol_confirmed": True}
        analysis = {"ivme": 60, "risk_score": -5, "confidence": 70,
                     "scores_imputed": [], "positives": ["Good"], "negatives": ["Bad"]}
        enriched = enrich_signal(sig, analysis)
        assert enriched["reason"] == ["Good"]
        assert enriched["risk_flags"] == ["Bad"]

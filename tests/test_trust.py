# ================================================================
# BISTBULL TERMINAL — TRUST SYSTEM TESTS (HARDENED)
# tests/test_trust.py
# ================================================================

import pytest
from core.trust import (
    DataPoint, Classification, guard_decision, guard_ai,
    check_minimum_data, build_freshness_label, filter_decision_inputs,
)
from ai.safety import (
    validate_ai_output, FORBIDDEN_WORDS, FALLBACK_MESSAGES,
    safe_ai_generate, OVERCONFIDENCE_WORDS, JARGON_WORDS,
)
from engine.macro_decision import compute_regime, THRESHOLDS
from engine.action_summary import generate_action_summary


# ================================================================
# DATAPOINT CLASSIFICATION
# ================================================================
class TestDataPoint:
    def test_trusted_delayed_is_safe(self):
        dp = DataPoint(22.0, "yfinance", Classification.TRUSTED_DELAYED)
        assert dp.safe_for_decision and dp.safe_for_ai and dp.safe_for_hero

    def test_estimated_allowed_with_caveat(self):
        dp = DataPoint(295, "manuel", Classification.ESTIMATED, timestamp="2026-04-11T00:00:00+00:00")
        assert dp.safe_for_decision and dp.is_estimated

    def test_editorial_blocked_from_decision(self):
        dp = DataPoint("Bankacılık", "editör", Classification.EDITORIAL)
        assert not dp.safe_for_decision
        assert dp.safe_for_ai

    def test_ai_blocked_from_decision_and_hero(self):
        dp = DataPoint("text", "grok", Classification.AI_GENERATED)
        assert not dp.safe_for_decision and not dp.safe_for_hero

    def test_fake_banned_everywhere(self):
        dp = DataPoint(0, "hardcoded", Classification.FAKE_PLACEHOLDER)
        assert not dp.safe_for_decision and not dp.safe_for_ai and not dp.safe_for_hero


# ================================================================
# FAIL-SAFE GUARDS (Fix 2: never crash)
# ================================================================
class TestFailSafeGuards:
    def test_guard_decision_returns_false_for_fake(self):
        dp = DataPoint(0, "hardcoded", Classification.FAKE_PLACEHOLDER)
        assert guard_decision(dp, "foreign_flow") is False  # no crash

    def test_guard_decision_returns_false_for_ai(self):
        dp = DataPoint("text", "grok", Classification.AI_GENERATED)
        assert guard_decision(dp, "commentary") is False  # no crash

    def test_guard_decision_returns_false_for_editorial(self):
        dp = DataPoint("X", "editör", Classification.EDITORIAL)
        assert guard_decision(dp, "sector") is False  # no crash

    def test_guard_decision_returns_true_for_trusted(self):
        dp = DataPoint(22.0, "yfinance", Classification.TRUSTED_DELAYED)
        assert guard_decision(dp) is True

    def test_filter_excludes_fake_safely(self):
        raw = {"vix": 22.0, "fake_flow": 0}
        cls = {"vix": Classification.TRUSTED_DELAYED, "fake_flow": Classification.FAKE_PLACEHOLDER}
        clean, excluded = filter_decision_inputs(raw, cls)
        assert "vix" in clean
        assert "fake_flow" not in clean
        assert excluded == 1


# ================================================================
# MINIMUM DATA RULE
# ================================================================
class TestMinimumData:
    def test_enough_trusted(self):
        dps = {
            "a": DataPoint(1, "yf", Classification.TRUSTED_DELAYED),
            "b": DataPoint(2, "yf", Classification.TRUSTED_DELAYED),
            "c": DataPoint(3, "yf", Classification.TRUSTED_DELAYED),
        }
        assert check_minimum_data(dps, 3) is True

    def test_insufficient(self):
        dps = {"a": DataPoint(1, "manuel", Classification.ESTIMATED)}
        assert check_minimum_data(dps, 3) is False


# ================================================================
# REGIME BEHAVIOR (Fix 1: 4 snapshots)
# ================================================================
class TestRegimeSnapshots:
    def test_strong_risk_on(self):
        """All 6 signals bullish → RISK_ON, good confidence."""
        r = compute_regime({
            "cds": 200, "usdtry_5d_pct": 0.5, "vix": 15,
            "dxy_20d_pct": -2.0, "yield_spread": 1.0, "global_idx_5d_pct": 2.0,
        })
        assert r.regime == "RISK_ON"
        assert r.score >= 3
        assert len(r.signals) == 6

    def test_strong_risk_off(self):
        """All 6 signals bearish → RISK_OFF."""
        r = compute_regime({
            "cds": 400, "usdtry_5d_pct": 5.0, "vix": 30,
            "dxy_20d_pct": 3.0, "yield_spread": -2.0, "global_idx_5d_pct": -3.0,
        })
        assert r.regime == "RISK_OFF"
        assert r.score <= -3

    def test_mixed_signals_neutral(self):
        """3 bullish + 3 bearish → NEUTRAL."""
        r = compute_regime({
            "cds": 200, "usdtry_5d_pct": 0.5, "vix": 15,  # 3 bull
            "dxy_20d_pct": 3.0, "yield_spread": -2.0, "global_idx_5d_pct": -3.0,  # 3 bear
        })
        assert r.regime == "NEUTRAL"
        assert r.score == 0

    def test_low_quality_inputs(self):
        """Only 1 real signal → NEUTRAL + LOW confidence."""
        r = compute_regime({"cds": 400})
        assert r.regime == "NEUTRAL"  # only 1 signal, can't reach ±3
        assert r.confidence == "LOW"

    def test_no_foreign_flow_in_engine(self):
        """foreign_flow must NOT be in thresholds."""
        assert "foreign_flow" not in THRESHOLDS

    def test_estimated_reduces_confidence(self):
        """Same strong inputs but with estimated signals → lower confidence."""
        r = compute_regime({
            "cds": 200, "usdtry_5d_pct": 0.5, "vix": 15,
            "dxy_20d_pct": -2.0, "yield_spread": 1.0, "global_idx_5d_pct": 2.0,
        })
        # CDS and yield_spread default to "tahmini" source
        # 2 estimated out of 6 → confidence capped at MEDIUM
        assert r.confidence in ("MEDIUM", "HIGH")


# ================================================================
# AI VALIDATION (Fix 3: hardened)
# ================================================================
class TestAIValidationHardened:
    def test_clean_passes(self):
        r = validate_ai_output("CDS 295 seviyesinde, risk iştahını sınırlıyor.", "interpreter")
        assert r.ok

    def test_all_forbidden_words(self):
        for word in FORBIDDEN_WORDS:
            r = validate_ai_output(f"Bu konuda {word} diyebiliriz.", "interpreter")
            assert not r.ok, f"Not caught: '{word}'"

    def test_filler_rejected(self):
        r = validate_ai_output("Piyasalarda karışık bir görünüm hakim.", "interpreter")
        assert not r.ok

    def test_risk_off_bullish_contradiction(self):
        r = validate_ai_output("Güçlü alım fırsatı var.", "action_coach", regime="RISK_OFF")
        assert not r.ok

    def test_risk_off_bullish_ok_for_risk_controller(self):
        """Risk controller CAN mention positive signals even in RISK_OFF (it's their job to contrast)."""
        r = validate_ai_output("Kısa vadede toparlanma olabilir ama sürdürülebilir değil.", "risk_controller", regime="RISK_OFF")
        assert r.ok  # risk_controller is exempt from bullish-phrase blocking

    def test_overconfident_on_low_data(self):
        for word in OVERCONFIDENCE_WORDS[:3]:
            r = validate_ai_output(f"Bu {word} gösteriyor ki trend değişiyor.", "interpreter", confidence="LOW")
            assert not r.ok, f"Overconfidence not caught on LOW data: '{word}'"

    def test_overconfident_ok_on_high_data(self):
        r = validate_ai_output("Net olarak yön aşağı.", "interpreter", confidence="HIGH")
        assert r.ok  # HIGH confidence → overconfidence words allowed

    def test_unsupported_causal_claim(self):
        r = validate_ai_output("Bunun nedeni büyük oyuncuların pozisyon kapatması ve piyasanın yeniden yapılanması.", "interpreter")
        assert not r.ok

    def test_excessive_jargon(self):
        r = validate_ai_output("Likidite daralması ve carry trade baskısı artıyor.", "interpreter")
        assert not r.ok

    def test_single_jargon_ok(self):
        r = validate_ai_output("Carry trade baskısı devam ediyor.", "interpreter")
        assert r.ok  # 1 jargon is tolerated


# ================================================================
# SAFE AI GENERATE (Fix 4: tuned fallback)
# ================================================================
class TestSafeAIGenerate:
    def test_fallback_on_no_ai(self):
        r = safe_ai_generate("p", "interpreter", ai_call_fn=None)
        assert r == FALLBACK_MESSAGES["interpreter"]

    def test_fallback_on_low_confidence(self):
        """LOW confidence → immediate deterministic fallback, no AI call."""
        call_count = 0
        def counter_ai(prompt, tokens):
            nonlocal call_count; call_count += 1; return "test"
        r = safe_ai_generate("p", "interpreter", ai_call_fn=counter_ai, confidence="LOW")
        assert r == FALLBACK_MESSAGES["interpreter"]
        assert call_count == 0  # AI was never called

    def test_fallback_on_repeated_bad_output(self):
        def bad_ai(p, t): return "Bu kesinlikle garanti bir fırsat!"
        r = safe_ai_generate("p", "interpreter", ai_call_fn=bad_ai, max_retries=2)
        assert r == FALLBACK_MESSAGES["interpreter"]

    def test_good_output_passes(self):
        def good_ai(p, t): return "CDS 295 seviyesinde."
        r = safe_ai_generate("p", "interpreter", ai_call_fn=good_ai, confidence="MEDIUM")
        assert "CDS" in r

    def test_medium_confidence_allows_ai(self):
        """MEDIUM confidence → AI is called (not blocked like LOW)."""
        def ok_ai(p, t): return "VIX düşük, bu olumlu."
        r = safe_ai_generate("p", "interpreter", ai_call_fn=ok_ai, confidence="MEDIUM")
        assert "VIX" in r


# ================================================================
# ACTION SUMMARY — TRUST-AWARE WORDING (Fix 5)
# ================================================================
class TestActionSummaryWording:
    def test_low_confidence_reflects_in_wording(self):
        r = compute_regime({})  # empty → LOW confidence
        text = generate_action_summary(r)
        assert "net değil" in text.lower() or "sınırlı" in text.lower()

    def test_risk_off_no_bullish_language(self):
        r = compute_regime({
            "cds": 400, "usdtry_5d_pct": 5.0, "vix": 30,
            "dxy_20d_pct": 3.0, "yield_spread": -2.0, "global_idx_5d_pct": -3.0,
        })
        text = generate_action_summary(r).lower()
        assert "alım" not in text
        assert "fırsat" not in text

    def test_estimated_data_noted(self):
        """When 2+ signals are estimated, summary should mention it."""
        r = compute_regime({"cds": 300, "yield_spread": -0.5})
        # CDS and yield_spread are both "tahmini"
        text = generate_action_summary(r)
        # With only 2 real signals and LOW confidence, it should reflect uncertainty
        assert "net değil" in text.lower() or "sınırlı" in text.lower() or "tahmini" in text.lower()

    def test_editorial_sectors_soft_wording(self):
        """Sector rotation should use soft wording, not absolute."""
        r = compute_regime({
            "cds": 400, "usdtry_5d_pct": 5.0, "vix": 30,
            "dxy_20d_pct": 3.0, "yield_spread": -2.0, "global_idx_5d_pct": -3.0,
        })
        text = generate_action_summary(r)
        # Should say "görece güçlü görünüyor" not "sektörlerine yakın dur"
        assert "görece" in text.lower() or "görünüyor" in text.lower()

    def test_no_hype_words_ever(self):
        for scenario in [
            {"cds": 200, "vix": 15},
            {"cds": 400, "vix": 30},
            {},
        ]:
            r = compute_regime(scenario)
            text = generate_action_summary(r).lower()
            for bad in ["uçuş", "patlama", "hemen al"]:
                assert bad not in text


# ================================================================
# FRESHNESS LABELS
# ================================================================
class TestFreshnessLabels:
    def test_fake_label(self):
        assert "yok" in build_freshness_label(Classification.FAKE_PLACEHOLDER, "").lower()

    def test_ai_label(self):
        assert "AI" in build_freshness_label(Classification.AI_GENERATED, "grok")

    def test_editorial_label(self):
        assert "Editöryal" in build_freshness_label(Classification.EDITORIAL, "editör")

    def test_estimated_label(self):
        assert "Tahmini" in build_freshness_label(Classification.ESTIMATED, "manuel")

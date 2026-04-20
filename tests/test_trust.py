# ================================================================
# BISTBULL TERMINAL — TRUST SYSTEM TESTS (v3 — weighted scoring)
# tests/test_trust.py
# ================================================================

import pytest
from core.trust import (
    DataPoint, Classification, guard_decision, guard_ai,
    check_minimum_data, build_freshness_label, filter_decision_inputs,
)
from ai.safety import (
    validate_ai_output, FORBIDDEN_WORDS, FALLBACK_MESSAGES,
    safe_ai_generate, OVERCONFIDENCE_WORDS,
)
from engine.macro_decision import compute_regime, THRESHOLDS
from engine.action_summary import generate_action_summary


# ================================================================
# FAIL-SAFE GUARDS
# ================================================================
class TestGuards:
    def test_fake_returns_false(self):
        dp = DataPoint(0, "hc", Classification.FAKE_PLACEHOLDER)
        assert guard_decision(dp, "x") is False  # no crash

    def test_ai_returns_false(self):
        dp = DataPoint("t", "grok", Classification.AI_GENERATED)
        assert guard_decision(dp, "x") is False

    def test_editorial_returns_false(self):
        dp = DataPoint("X", "ed", Classification.EDITORIAL)
        assert guard_decision(dp, "x") is False

    def test_trusted_returns_true(self):
        dp = DataPoint(22.0, "yf", Classification.TRUSTED_DELAYED)
        assert guard_decision(dp) is True

    def test_filter_excludes_fake(self):
        raw = {"vix": 22.0, "fake": 0}
        cls = {"vix": Classification.TRUSTED_DELAYED, "fake": Classification.FAKE_PLACEHOLDER}
        clean, excl = filter_decision_inputs(raw, cls)
        assert "vix" in clean and "fake" not in clean and excl == 1


# ================================================================
# WEIGHTED REGIME SCORING
# ================================================================
class TestWeightedRegime:
    def test_all_trusted_bullish(self):
        """All 6 bullish, 4 trusted + 2 estimated → score = 4*1.0 + 2*0.5 = 5.0"""
        r = compute_regime({
            "cds": 200, "usdtry_5d_pct": 0.5, "vix": 15,
            "dxy_20d_pct": -2.0, "yield_spread": 1.0, "global_idx_5d_pct": 2.0,
        })
        assert r.regime == "RISK_ON"
        assert r.score == 5.0  # 4×1.0 + 2×0.5
        assert len(r.signals) == 6

    def test_all_bearish(self):
        r = compute_regime({
            "cds": 400, "usdtry_5d_pct": 5.0, "vix": 30,
            "dxy_20d_pct": 3.0, "yield_spread": -2.0, "global_idx_5d_pct": -3.0,
        })
        assert r.regime == "RISK_OFF"
        assert r.score == -5.0

    def test_mixed_neutral(self):
        """3 bull + 3 bear → ~0 weighted → NEUTRAL"""
        r = compute_regime({
            "cds": 200, "usdtry_5d_pct": 0.5, "vix": 15,
            "dxy_20d_pct": 3.0, "yield_spread": -2.0, "global_idx_5d_pct": -3.0,
        })
        assert r.regime == "NEUTRAL"

    def test_estimated_only_cannot_reach_risk_on(self):
        """Only CDS and yield_spread (both estimated 0.5 weight) bullish → max 1.0 → NEUTRAL"""
        r = compute_regime({"cds": 200, "yield_spread": 1.0})
        assert r.regime == "NEUTRAL"
        assert r.score <= 1.0

    def test_low_quality_always_low_confidence(self):
        r = compute_regime({"cds": 400})
        assert r.confidence == "LOW"

    def test_empty_neutral_low(self):
        r = compute_regime({})
        assert r.regime == "NEUTRAL"
        assert r.confidence == "LOW"
        assert r.score == 0

    def test_no_foreign_flow(self):
        assert "foreign_flow" not in THRESHOLDS


# ================================================================
# LOW-CONFIDENCE ACTION SUMMARY
# ================================================================
class TestLowConfidenceSummary:
    def test_low_says_incomplete(self):
        r = compute_regime({})
        t = generate_action_summary(r)
        assert "net" in t.lower() or "eksik" in t.lower() or "sınırlı" in t.lower()

    def test_low_with_one_neg_still_mentions_it(self):
        r = compute_regime({"cds": 400})
        t = generate_action_summary(r)
        assert "CDS" in t or "teyit" in t.lower()

    def test_low_with_one_pos_still_mentions_it(self):
        r = compute_regime({"vix": 15})
        t = generate_action_summary(r)
        assert "VIX" in t or "teyit" in t.lower()

    def test_low_recommends_wait(self):
        r = compute_regime({})
        t = generate_action_summary(r).lower()
        assert "bekle" in t

    def test_risk_off_soft_sector_wording(self):
        r = compute_regime({
            "cds": 400, "usdtry_5d_pct": 5.0, "vix": 30,
            "dxy_20d_pct": 3.0, "yield_spread": -2.0, "global_idx_5d_pct": -3.0,
        })
        t = generate_action_summary(r)
        assert "görece" in t.lower() or "görünüyor" in t.lower()

    def test_no_hype_any_scenario(self):
        for s in [{"cds": 200, "vix": 15}, {"cds": 400, "vix": 30}, {}]:
            t = generate_action_summary(compute_regime(s)).lower()
            for bad in ["uçuş", "patlama", "hemen al", "garanti"]:
                assert bad not in t


# ================================================================
# AI VALIDATION
# ================================================================
class TestAIValidation:
    def test_clean_passes(self):
        assert validate_ai_output("CDS 295, risk iştahı sınırlı.", "interpreter").ok

    def test_forbidden_words(self):
        for w in FORBIDDEN_WORDS:
            assert not validate_ai_output(f"Bu {w} demek.", "interpreter").ok

    def test_filler_rejected(self):
        assert not validate_ai_output("Piyasalarda karışık bir görünüm hakim.", "interpreter").ok

    def test_risk_off_bullish_blocked(self):
        assert not validate_ai_output("Güçlü alım fırsatı.", "action_coach", regime="RISK_OFF").ok

    def test_overconfident_on_low(self):
        assert not validate_ai_output("Açıkça yön yukarı.", "interpreter", confidence="LOW").ok

    def test_overconfident_ok_on_high(self):
        assert validate_ai_output("Net olarak yön aşağı.", "interpreter", confidence="HIGH").ok

    def test_causal_claim_rejected(self):
        assert not validate_ai_output("Bunun nedeni büyük oyuncuların pozisyon kapatması ve likidite azalması.", "interpreter").ok

    def test_jargon_2plus_rejected(self):
        assert not validate_ai_output("Likidite daralması ve carry trade baskısı.", "interpreter").ok

    def test_single_jargon_ok(self):
        assert validate_ai_output("Carry trade baskısı sürüyor.", "interpreter").ok


# ================================================================
# SAFE AI GENERATE
# ================================================================
class TestSafeGenerate:
    def test_no_ai_fallback(self):
        assert safe_ai_generate("p", "interpreter") == FALLBACK_MESSAGES["interpreter"]

    def test_low_confidence_immediate_fallback(self):
        calls = []
        def spy(p, t): calls.append(1); return "test"
        r = safe_ai_generate("p", "interpreter", ai_call_fn=spy, confidence="LOW")
        assert r == FALLBACK_MESSAGES["interpreter"]
        assert len(calls) == 0

    def test_medium_allows_ai(self):
        r = safe_ai_generate("p", "interpreter", ai_call_fn=lambda p,t: "VIX düşük.", confidence="MEDIUM")
        assert "VIX" in r

    def test_bad_output_falls_back(self):
        r = safe_ai_generate("p", "interpreter", ai_call_fn=lambda p,t: "Bu kesinlikle garanti!", max_retries=2)
        assert r == FALLBACK_MESSAGES["interpreter"]

    def test_reality_checker_fallback(self):
        assert safe_ai_generate("p", "reality_checker") == FALLBACK_MESSAGES["reality_checker"]


# ================================================================
# FRESHNESS LABELS
# ================================================================
class TestLabels:
    def test_fake(self): assert "yok" in build_freshness_label(Classification.FAKE_PLACEHOLDER, "").lower()
    def test_ai(self): assert "AI" in build_freshness_label(Classification.AI_GENERATED, "grok")
    def test_editorial(self): assert "Editöryal" in build_freshness_label(Classification.EDITORIAL, "ed")
    def test_estimated(self): assert "Tahmini" in build_freshness_label(Classification.ESTIMATED, "m")


# ================================================================
# 4 AI ROLES EXIST
# ================================================================
class TestRolesExist:
    def test_four_roles(self):
        from ai.macro_roles import MACRO_AI_ROLES
        assert "interpreter" in MACRO_AI_ROLES
        assert "risk_controller" in MACRO_AI_ROLES
        assert "action_coach" in MACRO_AI_ROLES
        assert "reality_checker" in MACRO_AI_ROLES
        assert len(MACRO_AI_ROLES) == 4

    def test_reality_checker_prompt_works(self):
        from ai.macro_roles import MACRO_AI_ROLES
        r = compute_regime({"cds": 300, "vix": 20})
        prompt = MACRO_AI_ROLES["reality_checker"]["prompt_fn"](r)
        assert "yanılt" in prompt.lower() or "kontrol" in prompt.lower()

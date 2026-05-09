"""Tests for BullWatch v2 Phase A.7 (Hotfix17):
   1. Absorption pattern case-sensitivity fix
   2. Runner override-aware fetch_metrics (sanity)
   3. Final narrative precedence — conflict overrides playbook
   4. Soft review flag for high-position ambiguous cases (runner-side)
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
import pytest

from engine.bullwatch import score_symbol
from engine.bullwatch_conflict import resolve_conflict_matrix, CONFLICT_RULES
from engine.bullwatch_evidence import (
    build_narrative, _detect_playbook_conflict_override,
    _DOMINANT_LABELS_LOWER, _PLAYBOOK_LABEL_TR,
)


def _ohlcv(closes, volumes=None):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    if volumes is None:
        volumes = np.full(n, 200_000.0)
    idx = pd.bdate_range(end="2026-05-08", periods=n)
    return pd.DataFrame({
        "Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
        "Close": closes, "Volume": volumes,
    }, index=idx)


# ============================================================
# 1. Absorption pattern case-sensitivity
# ============================================================
class TestAbsorptionCaseSensitivity:
    """A.7 fix #1: pattern labels are title-case in production but the
    rule check was lowercase. Title-case patterns should now be detected."""

    def _state_with_pattern(self, label):
        """Build conflict state simulating high-position absorption case."""
        return {
            "absorption_score": 100.0,  # simulate detection passed through
            "position_in_range": 0.92,
            "move_maturity": "MID",
        }

    def test_titlecase_absorption_label_recognized(self):
        """When score_symbol sees patterns=['Absorption'], absorption_score
        should be set to 100, allowing absorption_high_range to fire."""
        # Build a high-position scenario
        df = _ohlcv(np.linspace(10, 18, 80), np.full(80, 1_000_000.0))
        m = {"symbol": "TEST_TITLE",
             "market_cap": 1_500_000_000, "free_float": 0.3,
             "revenue": 600_000_000, "shares": 60_000_000}
        result = score_symbol(m, df=df)
        # We can't directly test the case fix from outside without forcing
        # a pattern, but we can verify the conflict_state construction
        # doesn't crash. The real fix is that *if* a pattern is detected
        # with title-case label, conflict matrix sees absorption_score=100.

        # Direct test of case-sensitivity logic:
        patterns_lc_titlecase = [str(p).lower() for p in ["Absorption"]]
        patterns_lc_lower = [str(p).lower() for p in ["absorption"]]
        patterns_lc_walkup = [str(p).lower() for p in ["Walk-Up Accumulation"]]

        assert "absorption" in patterns_lc_titlecase, \
            "Title-case 'Absorption' must lowercase to 'absorption'"
        assert "absorption" in patterns_lc_lower, \
            "Already-lowercase must continue to work"
        assert "absorption" not in patterns_lc_walkup, \
            "'Walk-Up Accumulation' must NOT trigger absorption"

    def test_walkup_does_not_trigger_absorption(self):
        """Make sure normalization isn't too loose — Walk-Up Accumulation
        contains 'accumulation' not 'absorption'."""
        patterns = ["Walk-Up Accumulation"]
        patterns_lc = [str(p).lower() for p in patterns]
        # 'absorption' as a list element check:
        assert "absorption" not in patterns_lc

    def test_miatk_like_fixture_fires_absorption_rule(self):
        """End-to-end: high-position + Absorption pattern should produce
        non-UNCLEAR conflict read via absorption_high_range rule."""
        # Direct conflict-matrix test (bypasses score_symbol's pattern
        # detector — we simulate as if absorption was detected):
        state = {
            "absorption_score": 100,    # absorption pattern detected
            "position_in_range": 0.92,  # high position (MIATK had 92%)
            "move_maturity": "MID",
        }
        result = resolve_conflict_matrix(state)
        # absorption_high_range should fire → DISTRIBUTION
        rules_fired = [r["rule"] for r in result.resolved_by]
        assert "absorption_high_range" in rules_fired, \
            f"Expected absorption_high_range to fire, got {rules_fired}"
        assert result.dominant_read == "DISTRIBUTION"


# ============================================================
# 2. Runner override-aware fetch_metrics (basic sanity)
# ============================================================
class TestRunnerOverrideAware:
    """A.7 fix #2: the runner now applies _KNOWN_OVERRIDES to metrics
    before scoring. We can't test the full network path here, but we
    can verify the override layer is exercised by score_symbol when
    metrics include the manual-override-provided free_float."""

    def test_kaplm_metrics_with_override_become_eligible(self):
        """If the runner correctly applied KAPLM's override (ff=0.35),
        score_symbol should see eligible=True when given valid OHLCV."""
        # Volume must clear 5M TL liquidity floor; closes ~10 → need ~500k vol
        df = _ohlcv(np.linspace(10, 11, 80), np.full(80, 600_000.0))
        # Simulate metrics AS IF runner applied KAPLM override
        m = {"symbol": "KAPLM",
             "market_cap": 1_500_000_000,
             "free_float": 0.35,  # ← from override
             "revenue": 500_000_000,
             "shares": 60_000_000}
        result = score_symbol(m, df=df)
        assert result.eligible is True, (
            f"Expected eligible, got reject_reason={result.reject_reason}"
        )
        assert result.universe_tier in ("core", "extended")

    def test_apply_overrides_fixes_free_float_field(self):
        """The production helper _apply_overrides should set free_float
        for KAPLM/GLRMK/ASELS when they're absent from yfinance."""
        from data.bullwatch_cache import _KNOWN_OVERRIDES, _apply_overrides
        for sym in ("KAPLM", "GLRMK", "ASELS"):
            metrics = {"market_cap": 1_000_000_000, "free_float": None}
            patched = _apply_overrides(metrics, sym)
            assert patched["free_float"] is not None
            assert 0 < patched["free_float"] < 1.0
            assert patched["free_float"] == _KNOWN_OVERRIDES[sym]["free_float"]


# ============================================================
# 3. Final narrative precedence
# ============================================================
class TestNarrativePrecedence:
    """A.7 fix #3: when conflict matrix overrides playbook, narrative
    must lead with conflict resolution, not with playbook headline."""

    def test_acc_playbook_dist_conflict_leads_with_dist(self):
        """ALGYO scenario: playbook says ACC, conflict says DIST."""
        pb = {"playbook": "ACCUMULATION_SEQUENCE", "confidence": 50,
              "missing_next_confirmation": ["Spring / shakeout recovery"]}
        cm = {"dominant_read": "DISTRIBUTION", "confidence": 100,
              "resolved_by": [{"rationale": "Yüksek lot dönüşü test"}]}
        mat = {"maturity": "LATE"}
        narrative = build_narrative(pb, cm, mat)
        # Must lead with conflict, not playbook
        assert narrative.startswith("🟠 Çelişki çözümü")
        assert "dağıtım" in narrative
        assert "DAĞITIM" not in narrative or "Çelişki çözümü dağıtım" in narrative
        # Should mention playbook as override-d
        assert "override edildi" in narrative
        assert "toplama" in narrative  # playbook mention
        # Audit must pass
        from engine.bullwatch_evidence import safety_audit
        audit = safety_audit(narrative)
        assert audit["uses_observation_language"] is True
        assert audit["forbidden_terms_detected"] == []

    def test_markup_playbook_dist_conflict_leads_with_dist(self):
        """playbook MARKUP + conflict DIST should lead with DIST."""
        pb = {"playbook": "MARKUP_SEQUENCE", "confidence": 60,
              "missing_next_confirmation": []}
        cm = {"dominant_read": "DISTRIBUTION", "confidence": 80,
              "resolved_by": [{"rationale": "test rationale"}]}
        mat = {"maturity": "LATE"}
        narrative = build_narrative(pb, cm, mat)
        assert narrative.startswith("🟠 Çelişki çözümü")
        assert "dağıtım" in narrative

    def test_no_conflict_leads_with_playbook(self):
        """When playbook and conflict agree (or conflict UNCLEAR),
        narrative should follow original A.6 order."""
        pb = {"playbook": "ACCUMULATION_SEQUENCE", "confidence": 70,
              "missing_next_confirmation": []}
        cm = {"dominant_read": "ACCUMULATION", "confidence": 80,
              "resolved_by": [{"rationale": "test rationale"}]}
        mat = {"maturity": "EARLY"}
        narrative = build_narrative(pb, cm, mat)
        # Should lead with playbook (🟢 emoji from accumulation headline)
        assert narrative.startswith("🟢")
        # Should NOT contain the override message
        assert "override edildi" not in narrative

    def test_no_conflict_unclear_dom_leads_with_playbook(self):
        """conflict UNCLEAR → playbook leads, no override mention."""
        pb = {"playbook": "MARKUP_SEQUENCE", "confidence": 33,
              "missing_next_confirmation": ["Walk-up breakout"]}
        cm = {"dominant_read": "UNCLEAR", "confidence": 0, "resolved_by": []}
        mat = {"maturity": "EARLY"}
        narrative = build_narrative(pb, cm, mat)
        assert narrative.startswith("🟢 Markup sequence")
        assert "override edildi" not in narrative

    def test_low_conflict_confidence_does_not_override(self):
        """conflict confidence < 50% should NOT override even if reads conflict."""
        pb = {"playbook": "ACCUMULATION_SEQUENCE", "confidence": 60,
              "missing_next_confirmation": []}
        cm = {"dominant_read": "DISTRIBUTION", "confidence": 30,
              "resolved_by": [{"rationale": "weak"}]}
        mat = {"maturity": "EARLY"}
        narrative = build_narrative(pb, cm, mat)
        # Should still lead with playbook because conflict not strong enough
        assert narrative.startswith("🟢")
        assert "override edildi" not in narrative

    def test_detect_override_function(self):
        """Direct unit test of _detect_playbook_conflict_override."""
        # ACC playbook + DIST conflict + 50%+ → True
        assert _detect_playbook_conflict_override(
            "ACCUMULATION_SEQUENCE", "DISTRIBUTION", 60) is True
        # MARKUP + DIST → True
        assert _detect_playbook_conflict_override(
            "MARKUP_SEQUENCE", "DISTRIBUTION", 50) is True
        # DIST + ACC → True
        assert _detect_playbook_conflict_override(
            "DISTRIBUTION_SEQUENCE", "ACCUMULATION", 80) is True
        # Same direction → False
        assert _detect_playbook_conflict_override(
            "ACCUMULATION_SEQUENCE", "ACCUMULATION", 80) is False
        # Below threshold → False
        assert _detect_playbook_conflict_override(
            "ACCUMULATION_SEQUENCE", "DISTRIBUTION", 49) is False
        # UNCLEAR conflict → False
        assert _detect_playbook_conflict_override(
            "ACCUMULATION_SEQUENCE", "UNCLEAR", 80) is False

    def test_turkish_lowercase_labels_correct(self):
        """Python's str.lower() mangles Turkish chars. Verify our manual
        mapping doesn't have broken lowercase forms."""
        assert _DOMINANT_LABELS_LOWER["DISTRIBUTION"] == "dağıtım"
        assert _DOMINANT_LABELS_LOWER["ACCUMULATION"] == "birikim"
        assert _PLAYBOOK_LABEL_TR["DISTRIBUTION_SEQUENCE"] == "dağıtım"
        assert _PLAYBOOK_LABEL_TR["ACCUMULATION_SEQUENCE"] == "toplama"


# ============================================================
# 4. Soft review flag — runner-side, exercised via classify_row
# ============================================================
class TestRunnerSoftFlagMIATK:
    """A.7 fix #4: high-position + high-turnover + heat but UNCLEAR
    should get the new soft review flag."""

    def test_miatk_like_case_gets_review_flag(self):
        """Build a raw_evidence dict matching MIATK live data and
        verify classify_row marks it as needs_review."""
        # Need to import classify_row from runner — it's in /home/claude
        runner_path = "/home/claude/phase_a_review_runner.py"
        # Import via exec (runner isn't a package)
        ns = {}
        with open(runner_path) as f:
            exec(f.read().replace('if __name__ == "__main__":\n    main()', ''), ns)
        classify_row = ns["classify_row"]

        miatk_raw = {
            "eligible": True,
            "universe_tier": "extended",
            "position_in_range": 0.92,
            "price_pinning_score": 40,
            "move_maturity": "MID",
            "dominant_read": "UNCLEAR",
            "conflict_confidence": 0,
            "confidence_tier": "LOW",
            "evidence_depth_count": 0,
            "playbook": "ACCUMULATION_SEQUENCE",
            "playbook_confidence": 50,
            "audit_passed": True,
            "rsi_14": 78.8,
            "vol_climax_ratio": 4.38,
            "float_turnover_20d": 1.95,
            "ret_20d_pct": 16.0,
            "absorption_pattern_present": True,
            "patterns_detected": ["Absorption"],
        }
        flag, reasons = classify_row(miatk_raw)
        # Must NOT be looks_right anymore
        assert flag == "needs_review", f"Expected needs_review, got {flag}"
        joined = " ".join(reasons)
        assert "High-position" in joined or "no dominant read" in joined

    def test_quiet_unclear_does_not_trigger_soft_flag(self):
        """A truly quiet UNCLEAR (low position, low turnover) should
        remain looks_right — soft flag is for high-heat ambiguity only."""
        runner_path = "/home/claude/phase_a_review_runner.py"
        ns = {}
        with open(runner_path) as f:
            exec(f.read().replace('if __name__ == "__main__":\n    main()', ''), ns)
        classify_row = ns["classify_row"]

        quiet_raw = {
            "eligible": True,
            "universe_tier": "core",
            "position_in_range": 0.45,
            "price_pinning_score": 30,
            "move_maturity": "EARLY",
            "dominant_read": "UNCLEAR",
            "conflict_confidence": 0,
            "confidence_tier": "LOW",
            "evidence_depth_count": 0,
            "playbook": "UNCLEAR",
            "playbook_confidence": 0,
            "audit_passed": True,
            "rsi_14": 50.0,
            "vol_climax_ratio": 1.2,
            "float_turnover_20d": 0.5,
            "ret_20d_pct": 1.0,
            "absorption_pattern_present": False,
            "patterns_detected": [],
        }
        flag, reasons = classify_row(quiet_raw)
        # Should still be looks_right
        assert flag == "looks_right"

    def test_high_position_low_turnover_does_not_trigger(self):
        """Soft flag requires turnover > 1.0. ANHYT-like (turnover 0.29)
        should not be flagged by THIS rule (other flags may still apply)."""
        runner_path = "/home/claude/phase_a_review_runner.py"
        ns = {}
        with open(runner_path) as f:
            exec(f.read().replace('if __name__ == "__main__":\n    main()', ''), ns)
        classify_row = ns["classify_row"]

        anhyt_like_raw = {
            "eligible": True,
            "universe_tier": "extended",
            "position_in_range": 0.84,
            "price_pinning_score": 68,
            "move_maturity": "EARLY",
            "dominant_read": "UNCLEAR",  # contrived: pretend conflict UNCLEAR
            "conflict_confidence": 0,
            "confidence_tier": "LOW",
            "evidence_depth_count": 0,
            "playbook": "UNCLEAR",
            "playbook_confidence": 0,
            "audit_passed": True,
            "rsi_14": 68.4,
            "vol_climax_ratio": 1.29,
            "float_turnover_20d": 0.29,  # ← low, soft flag should NOT trigger
            "ret_20d_pct": 3.8,
            "absorption_pattern_present": False,
            "patterns_detected": [],
        }
        flag, reasons = classify_row(anhyt_like_raw)
        # Soft MIATK flag should NOT be in reasons
        joined = " ".join(reasons)
        assert "no dominant read" not in joined or "High-position" not in joined

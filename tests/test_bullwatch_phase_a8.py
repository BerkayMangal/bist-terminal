"""Tests for BullWatch v2 Phase A.8 (Hotfix18):
   1. Global pattern label normalization across narrative + playbook + runner
   2. Runner absorption_pattern_present uses substring match
   3. Runner exports override_applied + override_source on every row
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
import pytest

from engine.bullwatch import score_symbol, _build_narrative
from engine.bullwatch_playbook import (
    _absorption_pattern, _shakeout_pattern, _walk_up_pattern,
    _tight_closes_pattern, _patterns_normalized,
    SymbolState,
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


def _state(patterns_labels: list[str], **kwargs) -> SymbolState:
    """Build a SymbolState fixture for playbook detector unit tests."""
    metrics = kwargs.pop("metrics", {})
    metrics["patterns"] = patterns_labels
    return SymbolState(
        df=kwargs.pop("df", None),
        sub_scores=kwargs.pop("sub_scores", {}),
        metrics=metrics,
        pinning=kwargs.pop("pinning", None),
    )


# ============================================================
# 1. Playbook detectors — title-case labels recognized
# ============================================================
class TestPlaybookPatternNormalization:
    def test_absorption_titlecase_recognized(self):
        s = _state(["Absorption"], sub_scores={"silent_volume": 0.0})
        assert _absorption_pattern(s) is True

    def test_absorption_lowercase_still_works(self):
        s = _state(["absorption"], sub_scores={"silent_volume": 0.0})
        assert _absorption_pattern(s) is True

    def test_walkup_label_recognized(self):
        s = _state(["Walk-Up Accumulation"])
        assert _walk_up_pattern(s) is True

    def test_walkup_underscore_label_recognized(self):
        # In case some path emits the snake_case name
        s = _state(["walk_up"])
        assert _walk_up_pattern(s) is True

    def test_shakeout_label_recognized(self):
        s = _state(["Shakeout Recovery"])
        assert _shakeout_pattern(s) is True

    def test_shakeout_underscore_label_recognized(self):
        s = _state(["shakeout_recovery"])
        assert _shakeout_pattern(s) is True

    def test_tight_closes_titlecase_recognized(self):
        s = _state(["Tight Closes"])
        assert _tight_closes_pattern(s) is True

    def test_tight_closes_underscore_recognized(self):
        s = _state(["tight_closes"])
        assert _tight_closes_pattern(s) is True

    def test_no_false_positive_for_walkup_in_absorption(self):
        s = _state(["Walk-Up Accumulation"], sub_scores={"silent_volume": 0.0})
        # Walk-Up doesn't contain 'absorption' substring
        assert _absorption_pattern(s) is False

    def test_no_false_positive_for_absorption_in_shakeout(self):
        s = _state(["Absorption"])
        assert _shakeout_pattern(s) is False
        assert _walk_up_pattern(s) is False
        assert _tight_closes_pattern(s) is False

    def test_empty_patterns(self):
        s = _state([])
        assert _absorption_pattern(s) is False
        assert _shakeout_pattern(s) is False
        assert _walk_up_pattern(s) is False
        assert _tight_closes_pattern(s) is False

    def test_patterns_normalized_helper(self):
        s = _state(["Absorption", "Walk-Up Accumulation"])
        lc = _patterns_normalized(s)
        assert "absorption" in lc
        assert "walk-up accumulation" in lc


# ============================================================
# 2. v1 narrative pattern checks
# ============================================================
class TestV1NarrativePatternChecks:
    """The legacy _build_narrative had silent case bugs:
    'absorption' in ['Absorption'] → False, so the watch text
    never appeared on real data. Verify the lines now appear."""

    def test_titlecase_absorption_appears_in_watch_text(self):
        narrative = _build_narrative(
            score=50.0, zone="EARLY", pattern="Test",
            sector_tr="Test", components={},
            metrics={
                "patterns": ["Absorption"],
                "float_pressure": None, "rvol": None,
                "atr_compression": None, "bb_compression": None,
                "price_change_5d": None,
            },
            data_quality="ok",
        )
        what_to_watch = narrative.get("what_to_watch", "")
        assert "Absorption pattern var" in what_to_watch, \
            f"Expected Absorption watch text, got: {what_to_watch}"

    def test_titlecase_walkup_appears(self):
        narrative = _build_narrative(
            score=50.0, zone="EARLY", pattern="Test",
            sector_tr="Test", components={},
            metrics={
                "patterns": ["Walk-Up Accumulation"],
                "float_pressure": None, "rvol": None,
                "atr_compression": None, "bb_compression": None,
                "price_change_5d": None,
            },
            data_quality="ok",
        )
        assert "Walk-up devam" in narrative.get("what_to_watch", "")

    def test_titlecase_shakeout_appears(self):
        narrative = _build_narrative(
            score=50.0, zone="EARLY", pattern="Test",
            sector_tr="Test", components={},
            metrics={
                "patterns": ["Shakeout Recovery"],
                "float_pressure": None, "rvol": None,
                "atr_compression": None, "bb_compression": None,
                "price_change_5d": None,
            },
            data_quality="ok",
        )
        assert "Shakeout candle yapıldı" in narrative.get("what_to_watch", "")


# ============================================================
# 3. Runner absorption_pattern_present + override fields
# ============================================================
class TestRunnerHotfix18Fields:
    """Verify runner extract_raw_evidence:
       - absorption_pattern_present uses substring after lowercasing
       - override_applied / override_source surfaced on every row
    """

    def _load_runner(self):
        ns = {}
        with open("/home/claude/phase_a_review_runner.py") as f:
            exec(f.read().replace('if __name__ == "__main__":\n    main()', ''), ns)
        return ns

    def test_absorption_pattern_present_titlecase(self):
        ns = self._load_runner()
        extract_raw_evidence = ns["extract_raw_evidence"]

        df = _ohlcv(np.linspace(10, 11, 80), np.full(80, 600_000.0))
        m = {"symbol": "T", "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8,
             "override_applied": False, "override_source": "yfinance"}
        result = score_symbol(m, df=df)
        # Force a title-case pattern in metrics for the test
        if result.eligible:
            result.metrics["patterns"] = ["Absorption"]
            raw = extract_raw_evidence(result, df, m)
            assert raw["absorption_pattern_present"] is True

    def test_absorption_pattern_present_walkup_does_not_match(self):
        ns = self._load_runner()
        extract_raw_evidence = ns["extract_raw_evidence"]

        df = _ohlcv(np.linspace(10, 11, 80), np.full(80, 600_000.0))
        m = {"symbol": "T", "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8,
             "override_applied": False, "override_source": "yfinance"}
        result = score_symbol(m, df=df)
        if result.eligible:
            result.metrics["patterns"] = ["Walk-Up Accumulation"]
            raw = extract_raw_evidence(result, df, m)
            assert raw["absorption_pattern_present"] is False

    def test_override_fields_surfaced_eligible(self):
        ns = self._load_runner()
        extract_raw_evidence = ns["extract_raw_evidence"]

        df = _ohlcv(np.linspace(10, 11, 80), np.full(80, 600_000.0))
        m = {"symbol": "KAPLM", "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8,
             "override_applied": True, "override_source": "known_override"}
        result = score_symbol(m, df=df)
        raw = extract_raw_evidence(result, df, m)
        assert raw.get("override_applied") is True
        assert raw.get("override_source") == "known_override"

    def test_override_fields_surfaced_ineligible(self):
        ns = self._load_runner()
        extract_raw_evidence = ns["extract_raw_evidence"]

        df = _ohlcv(np.linspace(10, 11, 80), np.full(80, 600_000.0))
        # Big mcap → institutional reject
        m = {"symbol": "BIG", "market_cap": 50e9, "free_float": 0.5,
             "shares": 5e9, "revenue": 1e10,
             "override_applied": False, "override_source": "yfinance"}
        result = score_symbol(m, df=df)
        assert result.eligible is False
        raw = extract_raw_evidence(result, df, m)
        assert "override_applied" in raw
        assert "override_source" in raw
        assert raw.get("override_source") == "yfinance"

    def test_override_fields_default_none(self):
        """If metrics dict doesn't have override fields (caller bypassed
        runner's fetch_metrics), runner should still emit them as None."""
        ns = self._load_runner()
        extract_raw_evidence = ns["extract_raw_evidence"]

        df = _ohlcv(np.linspace(10, 11, 80), np.full(80, 600_000.0))
        m = {"symbol": "T", "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8}  # NO override fields
        result = score_symbol(m, df=df)
        raw = extract_raw_evidence(result, df, m)
        # Should not crash; should be None
        assert raw.get("override_applied") is None
        assert raw.get("override_source") is None


# ============================================================
# 4. End-to-end: MIATK absorption + high pos → conflict fires
# ============================================================
class TestEndToEndMIATK:
    """Direct conflict-matrix test mirrors the live MIATK fixture:
    high position + Absorption pattern → DISTRIBUTION via absorption_high_range.
    """

    def test_miatk_absorption_high_position_fires_distribution(self):
        from engine.bullwatch_conflict import resolve_conflict_matrix
        # Full MIATK-like state including the title-case Absorption pattern.
        # Note: conflict_state is built by score_symbol which now does the
        # case-normalization. We pass absorption_score=100 directly to
        # mirror what score_symbol would compute.
        state = {
            "absorption_score": 100,
            "position_in_range": 0.92,
            "move_maturity": "MID",
            "rsi_14": 78.8,
            "vol_climax_ratio": 4.38,
            "float_turnover_20d": 1.95,
            "ret_20d": 0.16,
            "price_pinning_score": 40,
        }
        result = resolve_conflict_matrix(state)
        rules = [r["rule"] for r in result.resolved_by]
        assert "absorption_high_range" in rules
        assert result.dominant_read == "DISTRIBUTION"

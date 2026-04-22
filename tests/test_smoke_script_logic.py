"""Offline tests for scripts/smoke_test_calibrated.py's parsing logic.

Can't actually exercise the HTTP path in CI (no production URL), but
we CAN verify the three check_* functions against representative
response payloads so any regression in response shape is caught.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "smoke_test_calibrated.py"
)
_spec = importlib.util.spec_from_file_location("smoke", _SCRIPT_PATH)
smoke = importlib.util.module_from_spec(_spec)
sys.modules["smoke"] = smoke
_spec.loader.exec_module(smoke)


def _good_payload(effective="calibrated_2026Q1", score=65.0):
    """Representative healthy response from /api/analyze/X."""
    return {
        "ok": True,
        "data": {
            "symbol": "THYAO",
            "overall": score,
            "ivme": 72,
            "risk_score": -5,
            "fa_score": 62.0,
            "decision": "AL",
            "v13": {"deger_score": score, "verdict": "Test"},
            "turkey": {
                "composite_multiplier": 1.05,
                "composite_grade": "B",
                "filters": {},
                "summary": "Test",
            },
            "academic": {
                "adjusted_fa": 62.0,
                "academic_penalty": -1.5,
                "composite_penalty": -1.5,
                "composite_score": 62,
            },
            "_meta": {
                "scoring_version": "calibrated_2026Q1",
                "scoring_version_effective": effective,
            },
        },
    }


class TestCheckScoringVersionEffective:
    def test_calibrated_effective_passes(self, capsys):
        r = smoke.check_scoring_version_effective(
            _good_payload(effective="calibrated_2026Q1")
        )
        assert r is True

    def test_v13_fallback_fails(self, capsys):
        r = smoke.check_scoring_version_effective(
            _good_payload(effective="v13_handpicked")
        )
        assert r is False
        out = capsys.readouterr().out
        assert "fallback" in out.lower()

    def test_missing_meta_fails(self, capsys):
        payload = {"ok": True, "data": {"overall": 60}}
        r = smoke.check_scoring_version_effective(payload)
        assert r is False

    def test_payload_without_data_envelope(self, capsys):
        """When endpoint returns flat JSON (no envelope), still works."""
        flat = {
            "overall": 65.0,
            "_meta": {
                "scoring_version": "calibrated_2026Q1",
                "scoring_version_effective": "calibrated_2026Q1",
            },
        }
        assert smoke.check_scoring_version_effective(flat) is True


class TestCheckDegerScoreRange:
    def test_overall_in_range_passes(self):
        assert smoke.check_deger_score_range(_good_payload(score=55.0)) is True

    def test_overall_below_range_fails(self):
        assert smoke.check_deger_score_range(_good_payload(score=0.5)) is False

    def test_overall_above_range_fails(self):
        assert smoke.check_deger_score_range(_good_payload(score=150.0)) is False

    def test_missing_score_fails(self, capsys):
        payload = {"ok": True, "data": {"_meta": {
            "scoring_version_effective": "calibrated_2026Q1",
        }}}
        assert smoke.check_deger_score_range(payload) is False

    def test_non_numeric_score_fails(self):
        payload = _good_payload()
        payload["data"]["overall"] = "not a number"
        # No 'deger' fallback either in _good_payload
        payload["data"].pop("deger", None)
        assert smoke.check_deger_score_range(payload) is False


class TestCheckK3K4Present:
    def test_both_layers_present_passes(self):
        assert smoke.check_k3_k4_present(_good_payload()) is True

    def test_missing_turkey_fails(self):
        p = _good_payload()
        del p["data"]["turkey"]
        assert smoke.check_k3_k4_present(p) is False

    def test_missing_academic_fails(self):
        p = _good_payload()
        del p["data"]["academic"]
        assert smoke.check_k3_k4_present(p) is False

    def test_turkey_multiplier_out_of_range_fails(self):
        p = _good_payload()
        p["data"]["turkey"]["composite_multiplier"] = 5.0  # obviously wrong
        assert smoke.check_k3_k4_present(p) is False

    def test_academic_without_penalty_fails(self):
        p = _good_payload()
        # Remove all penalty keys
        for k in ("academic_penalty", "total_adjustment_pct", "composite_penalty"):
            p["data"]["academic"].pop(k, None)
        assert smoke.check_k3_k4_present(p) is False


class TestGetNestedHelper:
    def test_basic_access(self):
        d = {"a": {"b": {"c": 42}}}
        assert smoke._get_nested(d, "a", "b", "c") == 42

    def test_missing_key_returns_default(self):
        d = {"a": 1}
        assert smoke._get_nested(d, "a", "b", "c", default="fallback") == "fallback"

    def test_non_dict_intermediate_returns_default(self):
        d = {"a": "not a dict"}
        assert smoke._get_nested(d, "a", "b") is None

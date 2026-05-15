# ================================================================
# tests/test_viop_tahtaci_overlay.py
#
# Tahtacı × VIOP overlay — UOA z-score + KAP operator signal combo.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine import viop_tahtaci_overlay as ov


def _iso_days_ago(d: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=d)).isoformat()


# ────────────────────────────────────────────────────────────────
# _kap_decay
# ────────────────────────────────────────────────────────────────


class TestKapDecay:
    def test_fresh_full_weight(self):
        assert ov._kap_decay(0.0, 14) == 1.0
        assert ov._kap_decay(0.5, 14) > 0.95

    def test_midpoint_half(self):
        assert abs(ov._kap_decay(7.0, 14) - 0.5) < 0.01

    def test_outside_window_zero(self):
        assert ov._kap_decay(15.0, 14) == 0.0
        assert ov._kap_decay(100.0, 14) == 0.0

    def test_none_zero(self):
        assert ov._kap_decay(None, 14) == 0.0


# ────────────────────────────────────────────────────────────────
# compute_overlay_score
# ────────────────────────────────────────────────────────────────


class TestComputeOverlayScore:
    def test_no_signals_uoa_unchanged(self):
        out = ov.compute_overlay_score(uoa_score=4.0, operator_signals=[])
        assert out["kap_strength"] == 0.0
        assert out["overlay_score"] == 4.0
        assert out["signals"] == []

    def test_fresh_insider_doubles(self):
        # Fresh INSIDER (weight 1.0, decay ~1.0) → overlay = uoa * 2
        sigs = [{"tag": "INSIDER", "age_days": 0.5}]
        out = ov.compute_overlay_score(uoa_score=4.0, operator_signals=sigs)
        assert out["kap_strength"] > 0.9
        assert 7.5 < out["overlay_score"] <= 8.0

    def test_stale_signal_minimal_boost(self):
        # 13/14 day old → tiny decay → small boost
        sigs = [{"tag": "INSIDER", "age_days": 13.0}]
        out = ov.compute_overlay_score(uoa_score=4.0, operator_signals=sigs)
        assert out["kap_strength"] < 0.15
        # Overlay barely above raw UOA
        assert out["overlay_score"] < 4.8

    def test_multiple_signals_stack(self):
        sigs = [
            {"tag": "INSIDER",      "age_days": 1.0},
            {"tag": "BUYBACK",      "age_days": 2.0},
            {"tag": "MGMT_CHANGE",  "age_days": 3.0},
        ]
        out = ov.compute_overlay_score(uoa_score=2.0, operator_signals=sigs)
        # 3 signals → strength sums up; overlay strongly amplified
        assert len(out["signals"]) == 3
        assert out["overlay_score"] > 3.5

    def test_signal_metadata_preserved(self):
        sigs = [{
            "tag": "INSIDER", "age_days": 1.0,
            "disclosure_index": 12345, "subject": "Pay Alım Satım"
        }]
        out = ov.compute_overlay_score(uoa_score=4.0, operator_signals=sigs)
        s = out["signals"][0]
        assert s["disclosure_index"] == 12345
        assert s["subject"] == "Pay Alım Satım"

    def test_unknown_tag_no_contribution(self):
        sigs = [{"tag": "BOGUS_TAG", "age_days": 1.0}]
        out = ov.compute_overlay_score(uoa_score=4.0, operator_signals=sigs)
        assert out["kap_strength"] == 0.0


# ────────────────────────────────────────────────────────────────
# gather_recent_operator_signals
# ────────────────────────────────────────────────────────────────


class TestGatherSignals:
    def test_only_within_window(self, monkeypatch):
        from infra import kap_storage
        # 5 days ago = inside 14-day window; 30 days ago = outside
        rows = [
            {"ticker": "AAA", "subject": "Pay Alım Satım Bildirimi",
             "publish_date": _iso_days_ago(5)},
            {"ticker": "BBB", "subject": "Pay Alım Satım Bildirimi",
             "publish_date": _iso_days_ago(30)},
        ]
        monkeypatch.setattr(kap_storage, "get_recent", lambda limit=1000: rows)
        out = ov.gather_recent_operator_signals(window_days=14)
        assert "AAA" in out
        assert "BBB" not in out

    def test_non_operator_subjects_skipped(self, monkeypatch):
        from infra import kap_storage
        rows = [
            {"ticker": "AAA", "subject": "Finansal Rapor",   # not operator
             "publish_date": _iso_days_ago(2)},
            {"ticker": "BBB", "subject": "Pay Alım Satım Bildirimi",
             "publish_date": _iso_days_ago(2)},
        ]
        monkeypatch.setattr(kap_storage, "get_recent", lambda limit=1000: rows)
        out = ov.gather_recent_operator_signals()
        assert "AAA" not in out
        assert "BBB" in out
        assert out["BBB"][0]["tag"] == "INSIDER"

    def test_storage_failure_returns_empty(self, monkeypatch):
        from infra import kap_storage

        def _boom(*a, **kw):
            raise RuntimeError("redis down")

        monkeypatch.setattr(kap_storage, "get_recent", _boom)
        out = ov.gather_recent_operator_signals()
        assert out == {}


# ────────────────────────────────────────────────────────────────
# get_overlay_anomalies — end-to-end with mocked sources
# ────────────────────────────────────────────────────────────────


class TestGetOverlayAnomalies:
    def _setup(self, monkeypatch, uoa_items, kap_signals):
        import engine.viop_uoa as uoa_mod
        monkeypatch.setattr(
            uoa_mod, "get_today_anomalies",
            lambda **kw: uoa_items,
        )
        monkeypatch.setattr(
            ov, "gather_recent_operator_signals",
            lambda window_days=14: kap_signals,
        )

    def _uoa_row(self, code, underlying, score):
        return {
            "code": code, "underlying": underlying,
            "kind": "option", "uoa": {"score": score},
        }

    def test_require_kap_filters_pure_uoa(self, monkeypatch):
        uoa = [self._uoa_row("O_A", "AAA", 4.0)]
        self._setup(monkeypatch, uoa, kap_signals={})    # no KAP for AAA
        # require_kap=True → drops
        out = ov.get_overlay_anomalies(require_kap=True)
        assert out == []
        # require_kap=False → keeps with unchanged UOA score
        out = ov.get_overlay_anomalies(require_kap=False)
        assert len(out) == 1
        assert out[0]["overlay"]["overlay_score"] == 4.0

    def test_overlap_boosts_score(self, monkeypatch):
        uoa = [self._uoa_row("O_A", "AAA", 3.0)]
        kap = {"AAA": [{"tag": "INSIDER", "age_days": 1.0}]}
        self._setup(monkeypatch, uoa, kap)
        out = ov.get_overlay_anomalies(require_kap=True)
        assert len(out) == 1
        assert out[0]["overlay"]["overlay_score"] > 5.5   # 3 × (~1.93) ≈ 5.8

    def test_sort_by_overlay_score_desc(self, monkeypatch):
        uoa = [
            self._uoa_row("O_A", "AAA", 5.0),  # no KAP → 5.0
            self._uoa_row("O_B", "BBB", 3.0),  # +INSIDER → ~5.8
            self._uoa_row("O_C", "CCC", 4.0),  # +BUYBACK fresh → ~6.2
        ]
        kap = {
            "BBB": [{"tag": "INSIDER", "age_days": 1.0}],
            "CCC": [{"tag": "BUYBACK", "age_days": 0.5}],
        }
        self._setup(monkeypatch, uoa, kap)
        out = ov.get_overlay_anomalies(require_kap=False)
        codes = [r["code"] for r in out]
        # CCC (overlay highest), then BBB, then AAA
        assert codes[0] == "O_C"
        assert codes[-1] == "O_A"

    def test_limit_applied(self, monkeypatch):
        uoa = [self._uoa_row(f"O_{i}", f"T{i}", 3.0) for i in range(20)]
        kap = {f"T{i}": [{"tag": "INSIDER", "age_days": 1.0}]
               for i in range(20)}
        self._setup(monkeypatch, uoa, kap)
        out = ov.get_overlay_anomalies(require_kap=True, limit=5)
        assert len(out) == 5

    def test_empty_uoa(self, monkeypatch):
        self._setup(monkeypatch, [], {})
        out = ov.get_overlay_anomalies()
        assert out == []


# ────────────────────────────────────────────────────────────────
# get_overlay_summary
# ────────────────────────────────────────────────────────────────


class TestGetOverlaySummary:
    def test_aggregates_by_tag(self, monkeypatch):
        import engine.viop_uoa as uoa_mod
        uoa_items = [
            {"code": "O_A", "underlying": "AAA", "kind": "option",
             "uoa": {"score": 3.0}},
            {"code": "O_B", "underlying": "BBB", "kind": "option",
             "uoa": {"score": 4.0}},
        ]
        monkeypatch.setattr(uoa_mod, "get_today_anomalies",
                            lambda **kw: uoa_items)
        kap = {
            "AAA": [{"tag": "INSIDER", "age_days": 1.0}],
            "BBB": [{"tag": "INSIDER", "age_days": 2.0},
                    {"tag": "BUYBACK", "age_days": 1.0}],
        }
        monkeypatch.setattr(ov, "gather_recent_operator_signals",
                            lambda window_days=14: kap)
        out = ov.get_overlay_summary()
        assert out["n_overlays"] == 2
        assert out["unique_underlyings"] == 2
        assert out["by_tag"].get("INSIDER") == 2
        assert out["by_tag"].get("BUYBACK") == 1
        assert out["top_score"] > 5

    def test_empty_summary(self, monkeypatch):
        import engine.viop_uoa as uoa_mod
        monkeypatch.setattr(uoa_mod, "get_today_anomalies", lambda **kw: [])
        monkeypatch.setattr(ov, "gather_recent_operator_signals",
                            lambda window_days=14: {})
        out = ov.get_overlay_summary()
        assert out["n_overlays"] == 0
        assert out["top_score"] == 0

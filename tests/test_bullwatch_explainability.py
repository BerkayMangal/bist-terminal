# ================================================================
# tests/test_bullwatch_explainability.py
#
# BullWatch score explainability — "Niye bu skor / niye tahtacı sinyali?"
# Tahtacı-centric: kap_activity + ownership + group_boost + walkup
# kombinasyonu derived "Tahtacı Signal Strength" olarak surfacelanır.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine import bullwatch_explainability as exp


# ────────────────────────────────────────────────────────────────
# Reason → engine classification
# ────────────────────────────────────────────────────────────────


class TestClassifyReason:
    def test_float_reasons(self):
        assert exp._classify_reason("Strong float pressure (4.2%)") == "float_pressure"
        assert exp._classify_reason("Float Squeeze detected") == "float_pressure"

    def test_revenue_reasons(self):
        assert exp._classify_reason("Revenue ≥ 10× market cap") == "revenue_mispricing"
        assert exp._classify_reason("ciro/pd extreme") == "revenue_mispricing"

    def test_volume_reasons(self):
        assert exp._classify_reason("Early relative volume (1.6×)") == "silent_volume"
        assert exp._classify_reason("Strong RVOL spike") == "silent_volume"

    def test_walkup_reasons(self):
        assert exp._classify_reason("Sustained walk-up: 5 days") == "price_action"
        assert exp._classify_reason("Walk Up pattern detected") == "price_action"

    def test_compression_reasons(self):
        assert exp._classify_reason("ATR compressed to 0.62×") == "compression"
        assert exp._classify_reason("BB width compress") == "compression"

    def test_kap_reasons(self):
        assert exp._classify_reason("KAP activity: 2 insider buys") == "kap_activity"
        assert exp._classify_reason("Pay Alım Satım Bildirimi") == "kap_activity"
        assert exp._classify_reason("Holding-group activity (yıldız)") == "kap_activity"
        assert exp._classify_reason("Buyback program") == "kap_activity"

    def test_ownership_reasons(self):
        assert exp._classify_reason("Ownership footprint") == "ownership"
        assert exp._classify_reason("Foreign holding shift") == "ownership"

    def test_fundamental_reasons(self):
        assert exp._classify_reason("ROE 18%") == "fundamental_quality"
        assert exp._classify_reason("PE outside healthy range") == "fundamental_quality"
        assert exp._classify_reason("Net debt / EBITDA = 0.8") == "fundamental_quality"

    def test_unknown_returns_none(self):
        assert exp._classify_reason("XYZ something arbitrary") is None
        assert exp._classify_reason("") is None
        assert exp._classify_reason(None) is None


# ────────────────────────────────────────────────────────────────
# Engine breakdown — weights, contributions, grouped reasons
# ────────────────────────────────────────────────────────────────


class TestBuildEngineBreakdown:
    def test_full_components(self):
        components = {
            "float_pressure":      0.8,
            "revenue_mispricing":  0.5,
            "silent_volume":       0.6,
            "price_action":        0.7,
            "compression":         0.4,
            "ownership":           0.5,
            "fundamental_quality": 0.8,
            "kap_activity":        0.7,
        }
        reasons = [
            "Strong float pressure (4.5%)",
            "ROE 16%",
            "KAP activity: 2 insider buys",
        ]
        engines, unmatched = exp.build_engine_breakdown(components, reasons)
        # Should have 8 engines, all marked available
        assert len(engines) == 8
        assert all(e["available"] for e in engines)
        # Contributions should sum to the FINAL SCORE.
        # With all-1.0 sub_scores the sum would be 100; with partial
        # sub_scores it's in [0, 100] and equals the engine output.
        # Manual calc with default weights (20, 12, 12, 18, 8, 10, 5, 15):
        #   0.8*20 + 0.5*12 + 0.6*12 + 0.7*18 + 0.4*8 + 0.5*10 + 0.8*5
        #   + 0.7*15 = 16+6+7.2+12.6+3.2+5+4+10.5 = 64.5
        total = sum(e["contribution_pct"] for e in engines)
        assert 64.0 < total < 65.0
        # Reasons grouped properly
        by_key = {e["key"]: e for e in engines}
        assert "Strong float pressure (4.5%)" in by_key["float_pressure"]["reasons"]
        assert "ROE 16%" in by_key["fundamental_quality"]["reasons"]
        assert any("insider" in r for r in by_key["kap_activity"]["reasons"])
        assert unmatched == []

    def test_missing_engines_excluded_from_norm(self):
        # Only 3 engines have data — weight normalization redistributes
        # so the available 3 sum to 100, not the full 8.
        components = {
            "float_pressure":      0.8,
            "revenue_mispricing":  None,
            "silent_volume":       None,
            "price_action":        0.7,
            "compression":         None,
            "ownership":           None,
            "fundamental_quality": None,
            "kap_activity":        0.6,
        }
        engines, _ = exp.build_engine_breakdown(components, [])
        available = [e for e in engines if e["available"]]
        unavailable = [e for e in engines if not e["available"]]
        assert len(available) == 3
        assert len(unavailable) == 5
        # Unavailable engines get 0 contribution
        for e in unavailable:
            assert e["contribution_pct"] == 0.0
        # With 3 of 8 engines, weights are renormalized to 100 / sum.
        # Available weights: float_pressure(20) + price_action(18) + kap_activity(15) = 53
        # norm = 100/53 ≈ 1.887. Total = (0.8*20 + 0.7*18 + 0.6*15) * 1.887
        #     = (16 + 12.6 + 9) * 1.887 = 37.6 * 1.887 ≈ 70.94
        total = sum(e["contribution_pct"] for e in available)
        assert 70.0 < total < 72.0

    def test_unmatched_reasons_surfaced(self):
        components = {"float_pressure": 0.5}
        reasons = ["Arbitrary string no keyword"]
        _, unmatched = exp.build_engine_breakdown(components, reasons)
        assert "Arbitrary string no keyword" in unmatched

    def test_canonical_engine_order(self):
        # UI relies on stable engine ordering for the bar chart
        components = {k: 0.5 for k in (
            "kap_activity", "float_pressure", "compression",
            "ownership", "revenue_mispricing", "silent_volume",
            "price_action", "fundamental_quality",
        )}
        engines, _ = exp.build_engine_breakdown(components, [])
        keys_order = [e["key"] for e in engines]
        # Order from WEIGHTS_WITH_OWNERSHIP dict insertion
        assert keys_order[0] == "float_pressure"
        assert "kap_activity" in keys_order


# ────────────────────────────────────────────────────────────────
# Tahtacı Signal Strength — the headline derived score
# ────────────────────────────────────────────────────────────────


class TestTahtaciSignalStrength:
    def test_no_signal(self):
        out = exp.compute_tahtaci_signal_strength({}, {})
        assert out["score"] == 0.0
        assert out["label"] == "Tahtacı yok"

    def test_strong_kap_only(self):
        # KAP fired strongly, no ownership/group/walkup — moderate score
        out = exp.compute_tahtaci_signal_strength(
            {"kap_activity": 0.9}, {},
        )
        # 0.45 * 0.9 = 0.405
        assert 0.35 < out["score"] < 0.45
        assert out["label"] in ("Güçlü ısınma", "Erken belirtiler")

    def test_full_tahtaci_setup(self):
        # All four signals strong — Net imza
        components = {"kap_activity": 1.0, "ownership": 1.0}
        metrics = {"group_activity_boost": 6.0, "walkup_days": 10}
        out = exp.compute_tahtaci_signal_strength(components, metrics)
        # All four → 1.0
        assert out["score"] > 0.9
        assert out["label"] == "Net tahtacı imzası"

    def test_walkup_threshold(self):
        # walkup_days < 5 contributes 0
        out_short = exp.compute_tahtaci_signal_strength(
            {}, {"walkup_days": 4},
        )
        out_long = exp.compute_tahtaci_signal_strength(
            {}, {"walkup_days": 10},
        )
        assert out_short["score"] == 0.0
        assert out_long["score"] > 0.05

    def test_group_boost_normalized(self):
        # group_activity_boost is 0..6 on the engine; normalized to 0..1
        out_max = exp.compute_tahtaci_signal_strength(
            {}, {"group_activity_boost": 6.0},
        )
        # 0.20 * 1.0 = 0.20
        assert abs(out_max["score"] - 0.20) < 0.001

    def test_components_echoed(self):
        out = exp.compute_tahtaci_signal_strength(
            {"kap_activity": 0.5, "ownership": 0.3},
            {"group_activity_boost": 3.0, "walkup_days": 7},
        )
        c = out["components"]
        assert c["kap_activity"] == 0.5
        assert c["ownership"] == 0.3
        assert c["group_boost"] == 0.5    # 3/6
        assert c["walkup_days"] == 7


# ────────────────────────────────────────────────────────────────
# Engine grouping — UI's 3-section layout
# ────────────────────────────────────────────────────────────────


class TestGroupEnginesByCategory:
    def test_tahtaci_category(self):
        engines = [
            {"key": "kap_activity"}, {"key": "ownership"},
            {"key": "float_pressure"}, {"key": "fundamental_quality"},
        ]
        out = exp.group_engines_by_category(engines)
        # kap_activity + ownership go to tahtaci bucket
        tahtaci_keys = {e["key"] for e in out["tahtaci"]}
        assert tahtaci_keys == {"kap_activity", "ownership"}
        # float_pressure → teyit
        assert out["teyit"][0]["key"] == "float_pressure"
        # fundamental_quality → baglam
        assert out["baglam"][0]["key"] == "fundamental_quality"

    def test_all_buckets_present(self):
        engines = [{"key": k} for k in (
            "kap_activity", "ownership", "float_pressure", "silent_volume",
            "price_action", "compression", "revenue_mispricing",
            "fundamental_quality",
        )]
        out = exp.group_engines_by_category(engines)
        # No engine lost
        all_keys = sum((len(v) for v in out.values()), 0)
        assert all_keys == 8


# ────────────────────────────────────────────────────────────────
# Full build_explanation contract — keys the frontend depends on
# ────────────────────────────────────────────────────────────────


class TestBuildExplanationContract:
    def test_full_item_returns_expected_keys(self, monkeypatch):
        # Avoid touching snapshot store
        monkeypatch.setattr(exp, "_get_previous_components", lambda s: None)
        item = {
            "symbol": "BIMAS",
            "score": 82.5,
            "zone": "CONVICTION",
            "pattern": "Tahtacı KAP Aktivitesi + Float Squeeze",
            "data_quality": "high",
            "missing_fields": [],
            "components": {
                "float_pressure": 0.8, "revenue_mispricing": 0.5,
                "silent_volume": 0.6, "price_action": 0.7,
                "compression": 0.5, "ownership": 0.6,
                "fundamental_quality": 0.7, "kap_activity": 0.9,
            },
            "reasons": [
                "Strong float pressure (4.5%)",
                "KAP activity: 2 insider buys",
                "ROE 17%",
            ],
            "metrics": {
                "is_bank": False,
                "group_activity_boost": 5.0,
                "walkup_days": 7,
            },
            "narrative": {"whats_happening": "test"},
        }
        out = exp.build_explanation(item)
        # All top-level fields the frontend reads
        for key in ("symbol", "score", "zone", "pattern", "data_quality",
                    "tahtaci_strength", "engines", "engines_grouped",
                    "unmatched_reasons", "previous", "delta", "narrative"):
            assert key in out, f"missing top-level key: {key}"
        # tahtaci_strength must have score + label + components
        ts = out["tahtaci_strength"]
        for key in ("score", "label", "components"):
            assert key in ts
        # engines_grouped has 3 buckets
        for cat in ("tahtaci", "teyit", "baglam"):
            assert cat in out["engines_grouped"]
            bucket = out["engines_grouped"][cat]
            assert "label" in bucket and "engines" in bucket

    def test_empty_item_returns_empty_dict(self):
        assert exp.build_explanation({}) == {}
        assert exp.build_explanation(None) == {}

    def test_delta_when_previous_exists(self, monkeypatch):
        monkeypatch.setattr(exp, "_get_previous_components", lambda s: {
            "score": 78.0,
            "components": {"kap_activity": 0.5, "float_pressure": 0.6},
            "zone": "CONFIRMED",
            "scan_id": "prev-123",
        })
        item = {
            "symbol": "BIMAS",
            "score": 84.0,
            "zone": "CONVICTION",
            "components": {"kap_activity": 0.8, "float_pressure": 0.7},
            "reasons": [],
            "metrics": {},
        }
        out = exp.build_explanation(item)
        d = out["delta"]
        assert d is not None
        assert d["score"] == 6.0
        # Per-engine deltas
        assert d["by_engine"]["kap_activity"] == 0.3
        assert d["by_engine"]["float_pressure"] == 0.1

    def test_no_delta_when_no_previous(self, monkeypatch):
        monkeypatch.setattr(exp, "_get_previous_components", lambda s: None)
        item = {
            "symbol": "X", "score": 80, "zone": "CONVICTION",
            "components": {"kap_activity": 0.5}, "reasons": [], "metrics": {},
        }
        out = exp.build_explanation(item)
        assert out["delta"] is None


# ────────────────────────────────────────────────────────────────
# Data quality breakdown
# ────────────────────────────────────────────────────────────────


class TestDataQualityBreakdown:
    def test_high_quality_no_missing(self):
        item = {"data_quality": "high", "missing_fields": [],
                "sector_tr": "Endüstri"}
        dq = exp._data_quality_breakdown(item)
        assert dq["tier"] == "high"
        assert dq["is_bank"] is False
        assert dq["missing_fields"] == []
        assert dq["tier_explanation"]

    def test_bank_flagged(self):
        item = {"data_quality": "medium", "sector_tr": "Finansal",
                "missing_fields": ["operating_cf"]}
        dq = exp._data_quality_breakdown(item)
        assert dq["is_bank"] is True
        assert "operating_cf" in dq["missing_fields"]

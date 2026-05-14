# ================================================================
# tests/test_bullwatch_prealarm.py
#
# BullWatch Pre-Alarm detector — "Tahtacı yaklaşıyor".
# Mevcut CONVICTION mantığını BOZMUYORUZ; bu sadece eşiğe yaklaşan
# adayları ek katmanda surface eden read-only helper.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine import bullwatch_prealarm as pre


def _item(
    symbol: str,
    score: float,
    zone: str = "CONFIRMED",
    *,
    kap=0.0, ownership=0.0,
    walkup_days=0, group_boost=0.0,
    components_override=None,
    data_quality="high",
    pattern="X",
):
    components = {
        "float_pressure":      0.5,
        "revenue_mispricing":  0.5,
        "silent_volume":       0.5,
        "price_action":        0.5,
        "compression":         0.5,
        "ownership":           ownership,
        "fundamental_quality": 0.5,
        "kap_activity":        kap,
    }
    if components_override:
        components.update(components_override)
    return {
        "symbol": symbol,
        "score": score,
        "zone": zone,
        "pattern": pattern,
        "sector_tr": "Endüstri",
        "data_quality": data_quality,
        "components": components,
        "metrics": {
            "walkup_days": walkup_days,
            "group_activity_boost": group_boost,
        },
    }


# ────────────────────────────────────────────────────────────────
# Score window guards — CRITICAL: must NOT touch CONVICTION territory
# ────────────────────────────────────────────────────────────────


class TestScoreWindow:
    def test_score_below_70_excluded(self):
        items = [_item("AAA", score=69.9, kap=0.8, ownership=0.8)]
        out = pre.find_pre_alarm_candidates(items)
        assert out == []

    def test_score_75_excluded_conviction_territory(self):
        # 75.0 is the CONVICTION threshold. Pre-alarm must STAY below 75
        # or it would be a duplicate signal — kullanıcının "mantığı
        # bozmadan iyileştir" direktifinin tam karşılığı.
        items = [_item("AAA", score=75.0, kap=0.8, ownership=0.8)]
        out = pre.find_pre_alarm_candidates(items)
        assert out == [], "75.0+ score is CONVICTION territory, not pre-alarm"

    def test_score_74_99_included(self):
        items = [_item("AAA", score=74.99, kap=0.8, ownership=0.8)]
        out = pre.find_pre_alarm_candidates(items)
        assert len(out) == 1

    def test_score_70_boundary_included(self):
        items = [_item("AAA", score=70.0, kap=0.8, ownership=0.8)]
        out = pre.find_pre_alarm_candidates(items)
        assert len(out) == 1


# ────────────────────────────────────────────────────────────────
# Tahtacı strength gate
# ────────────────────────────────────────────────────────────────


class TestTahtaciStrengthGate:
    def test_weak_tahtaci_excluded(self):
        # Score 72 but ZERO tahtacı sinyali — should be excluded.
        # We don't want "rastgele yüksek skor"; pre-alarm requires
        # tahtacı imzasının ısınması.
        items = [_item("AAA", score=72.0,
                       kap=0.0, ownership=0.0,
                       walkup_days=0, group_boost=0.0)]
        out = pre.find_pre_alarm_candidates(items)
        assert out == []

    def test_strong_tahtaci_only_kap(self):
        # kap=0.8 alone: 0.45*0.8 = 0.36 → above 0.30 threshold
        items = [_item("AAA", score=72.0, kap=0.8)]
        out = pre.find_pre_alarm_candidates(items)
        assert len(out) == 1
        assert out[0]["tahtaci_strength"] > 0.3

    def test_walkup_alone_below_threshold(self):
        # walkup_days=7 → walkup_contribution = (7-4)/6 * 0.10 = 0.05
        # Not enough alone.
        items = [_item("AAA", score=72.0, walkup_days=7)]
        out = pre.find_pre_alarm_candidates(items)
        assert out == []

    def test_combined_signals_pass(self):
        # kap=0.5 (0.225) + walkup=10 (0.10) + group=4 (0.133) = ~0.46
        items = [_item("AAA", score=72.0,
                       kap=0.5, walkup_days=10, group_boost=4.0)]
        out = pre.find_pre_alarm_candidates(items)
        assert len(out) == 1


# ────────────────────────────────────────────────────────────────
# Zone gate
# ────────────────────────────────────────────────────────────────


class TestZoneGate:
    def test_early_excluded(self):
        items = [_item("AAA", score=72.0, kap=0.8, zone="EARLY")]
        out = pre.find_pre_alarm_candidates(items)
        assert out == []

    def test_conviction_excluded_too(self):
        # CONVICTION already fired — we don't want to surface it as
        # "pre-alarm" too. (Score window would also exclude it but
        # defense-in-depth.)
        items = [_item("AAA", score=73.0, kap=0.8, zone="CONVICTION")]
        out = pre.find_pre_alarm_candidates(items)
        # CONVICTION items shouldn't ever have score<75 in practice, but
        # if they do, our zone filter still excludes them.
        assert out == []


# ────────────────────────────────────────────────────────────────
# Sort by pre_alarm_score desc
# ────────────────────────────────────────────────────────────────


class TestSorting:
    def test_higher_tahtaci_ranks_first(self):
        items = [
            _item("WEAK",   score=72.0, kap=0.5),   # ts ~0.225
            _item("STRONG", score=72.0, kap=1.0, ownership=1.0),  # ts ~0.7
            _item("MID",    score=72.0, kap=0.7),                 # ts ~0.315
        ]
        out = pre.find_pre_alarm_candidates(items)
        order = [c["symbol"] for c in out if c["symbol"] in ("WEAK", "STRONG", "MID")]
        assert order[0] == "STRONG"

    def test_proximity_to_75_boosts_rank(self):
        # Same tahtacı strength, but score 74 should outrank 70
        items = [
            _item("FAR",  score=70.5, kap=0.8),
            _item("NEAR", score=74.5, kap=0.8),
        ]
        out = pre.find_pre_alarm_candidates(items)
        assert out[0]["symbol"] == "NEAR"


# ────────────────────────────────────────────────────────────────
# Output schema — pinning the contract for the UI
# ────────────────────────────────────────────────────────────────


class TestOutputSchema:
    def test_required_keys_present(self):
        items = [_item("BIMAS", score=72.0, kap=0.8)]
        out = pre.find_pre_alarm_candidates(items)
        row = out[0]
        for key in ("symbol", "score", "zone", "pattern", "sector_tr",
                    "tahtaci_strength", "tahtaci_label",
                    "tahtaci_components", "missing_engines",
                    "data_quality_blocker", "pre_alarm_score"):
            assert key in row, f"missing key: {key}"

    def test_data_quality_blocker_reflects_medium(self):
        items = [_item("BIMAS", score=72.0, kap=0.8,
                       data_quality="medium")]
        out = pre.find_pre_alarm_candidates(items)
        assert out[0]["data_quality_blocker"] is not None
        assert "orta" in out[0]["data_quality_blocker"]

    def test_missing_engines_listed(self):
        # All engines exist, but most are mid-range (0.3-0.5 → borderline)
        components = {
            "float_pressure": 0.40,  # borderline
            "silent_volume":  0.45,  # borderline
            "price_action":   0.35,  # borderline
            "compression":    0.55,  # active
            "ownership":      0.60,  # active
            "fundamental_quality": 0.5,
            "revenue_mispricing":  0.4,
            "kap_activity":   0.80,  # strong tahtacı
        }
        items = [_item("BIMAS", score=72.0, kap=0.8,
                       components_override=components)]
        out = pre.find_pre_alarm_candidates(items)
        missing = out[0]["missing_engines"]
        # Should surface the borderline engines (sub 0.30-0.50)
        assert isinstance(missing, list)
        assert len(missing) <= 3


# ────────────────────────────────────────────────────────────────
# Summary aggregator
# ────────────────────────────────────────────────────────────────


class TestPreAlarmSummary:
    def test_empty_items(self):
        out = pre.get_pre_alarm_summary([])
        assert out["count"] == 0
        assert out["top_tahtaci_strength"] == 0.0
        assert out["buckets"] == {"net": 0, "guclu": 0, "erken": 0}

    def test_buckets_by_strength(self):
        items = [
            _item("NET",   score=72, kap=1.0, ownership=1.0,
                  walkup_days=10, group_boost=6.0),    # ts ~1.0 → "net"
            _item("GUCLU", score=72, kap=0.9, ownership=0.5),   # ts ~0.53 → "guclu"
            _item("ERKEN", score=72, kap=0.7),                  # ts ~0.31 → "erken"
        ]
        out = pre.get_pre_alarm_summary(items)
        assert out["count"] == 3
        b = out["buckets"]
        assert b["net"] >= 1
        assert b["guclu"] >= 1
        assert b["erken"] >= 1


# ────────────────────────────────────────────────────────────────
# DEFENSE: CONVICTION mantığı bozulmadı kontrolü
# ────────────────────────────────────────────────────────────────


class TestConvictionUntouched:
    def test_does_not_emit_alarm_storage(self, monkeypatch):
        # Pre-alarm detector should NEVER call save_alert or similar.
        # We monkeypatch to make sure no accidental write happens.
        from infra import bullwatch_alerts_storage as storage
        save_called = []
        monkeypatch.setattr(storage, "save_alert",
                            lambda a: save_called.append(a) or True)
        items = [_item("STRONG", score=74.5, kap=1.0, ownership=1.0)]
        # Run detector
        out = pre.find_pre_alarm_candidates(items)
        assert len(out) == 1
        # Critically: no write to alarm storage
        assert save_called == [], (
            "Pre-alarm must be read-only — no alarm storage writes allowed. "
            "CONVICTION alarms come from engine.bullwatch_alerts, not here."
        )

    def test_score_threshold_75_is_hard_wall(self):
        # No matter how strong tahtacı strength, 75+ NEVER appears in
        # pre-alarms. Defense-in-depth — even if filtering elsewhere
        # is broken, this gate stands.
        items = [
            _item("A", score=75.0, kap=1.0, ownership=1.0,
                  walkup_days=10, group_boost=6.0),
            _item("B", score=99.0, kap=1.0, ownership=1.0,
                  walkup_days=10, group_boost=6.0),
        ]
        out = pre.find_pre_alarm_candidates(items)
        assert out == []

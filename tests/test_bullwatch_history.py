"""Tests for BullWatch history (snapshot store + delta computation)."""
from __future__ import annotations

import sys
import os
import datetime as dt
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.bullwatch_history import (
    _slim_item, _zone_direction, compute_delta_for_item,
    annotate_with_deltas,
    DELTA_TYPE_NEW, DELTA_TYPE_ZONE_UP, DELTA_TYPE_ZONE_DOWN,
    DELTA_TYPE_SCORE_UP, DELTA_TYPE_SCORE_DOWN, DELTA_TYPE_STABLE,
    SCORE_DELTA_THRESHOLD,
)


# ============================================================
# Slim item — only what we need for trend comparison
# ============================================================
class TestSlimItem:
    def test_extracts_essential_fields(self):
        full = {
            "symbol": "KAPLM",
            "score": 52.0,
            "zone": "EARLY",
            "pattern": "Float Squeeze",
            "eligible": True,
            "narrative": {"whats_happening": "long text"},  # discarded
            "components": {"a": 1, "b": 2},                  # discarded
            "metrics": {
                "float_market_cap": 800_000_000,
                "rvol": 0.8,
                "free_float": 0.3,                            # not slimmed
            },
        }
        slim = _slim_item(full)
        assert slim == {
            "score": 52.0,
            "zone": "EARLY",
            "pattern": "Float Squeeze",
            "eligible": True,
            "float_market_cap": 800_000_000,
            "rvol": 0.8,
        }

    def test_handles_missing_metrics(self):
        full = {"symbol": "X", "score": 10, "zone": "EARLY",
                "pattern": "—", "eligible": True}
        slim = _slim_item(full)
        assert slim["float_market_cap"] is None
        assert slim["rvol"] is None


# ============================================================
# Zone direction
# ============================================================
class TestZoneDirection:
    def test_promotion(self):
        assert _zone_direction("EARLY", "CONFIRMED") == "up"
        assert _zone_direction("CONFIRMED", "CONVICTION") == "up"
        assert _zone_direction("EARLY", "CONVICTION") == "up"

    def test_demotion(self):
        assert _zone_direction("CONVICTION", "CONFIRMED") == "down"
        assert _zone_direction("CONFIRMED", "EARLY") == "down"

    def test_same_or_missing(self):
        assert _zone_direction("EARLY", "EARLY") is None
        assert _zone_direction(None, "EARLY") is None
        assert _zone_direction("EARLY", None) is None
        assert _zone_direction("UNKNOWN", "EARLY") is None


# ============================================================
# Delta classification — the heart of trend tracking
# ============================================================
class TestComputeDelta:
    def test_no_prior_snapshot_means_stable(self):
        # When there's NO history at all (first day ever), don't flag
        # everything as "new" — that's noise. Stay quiet.
        d = compute_delta_for_item(
            {"symbol": "A", "score": 50, "zone": "EARLY"},
            prior_snapshot=None,
        )
        assert d["type"] == DELTA_TYPE_STABLE
        assert d["score_change"] is None

    def test_new_symbol_in_existing_snapshot(self):
        # Snapshot exists but doesn't include this symbol → genuinely new
        d = compute_delta_for_item(
            {"symbol": "NEW", "score": 60, "zone": "EARLY"},
            prior_snapshot={"OLD": {"score": 30, "zone": "EARLY"}},
        )
        assert d["type"] == DELTA_TYPE_NEW
        assert "yeni" in d["label_short"]

    def test_zone_promotion_beats_score_jitter(self):
        d = compute_delta_for_item(
            {"symbol": "K", "score": 62, "zone": "CONFIRMED"},
            prior_snapshot={"K": {"score": 60, "zone": "EARLY"}},
        )
        # Zone change matters more than +2 score change
        assert d["type"] == DELTA_TYPE_ZONE_UP
        assert "EARLY → CONFIRMED" in d["label_short"]
        assert d["prev_zone"] == "EARLY"

    def test_zone_demotion(self):
        d = compute_delta_for_item(
            {"symbol": "K", "score": 50, "zone": "EARLY"},
            prior_snapshot={"K": {"score": 70, "zone": "CONFIRMED"}},
        )
        assert d["type"] == DELTA_TYPE_ZONE_DOWN
        assert d["prev_zone"] == "CONFIRMED"

    def test_score_up_above_threshold(self):
        d = compute_delta_for_item(
            {"symbol": "K", "score": 60, "zone": "EARLY"},
            prior_snapshot={"K": {"score": 40, "zone": "EARLY"}},
        )
        assert d["type"] == DELTA_TYPE_SCORE_UP
        assert d["score_change"] == 20.0
        assert d["label_short"] == "+20"

    def test_score_down_above_threshold(self):
        d = compute_delta_for_item(
            {"symbol": "K", "score": 30, "zone": "EARLY"},
            prior_snapshot={"K": {"score": 50, "zone": "EARLY"}},
        )
        assert d["type"] == DELTA_TYPE_SCORE_DOWN
        assert d["score_change"] == -20.0
        # label includes the minus already
        assert d["label_short"] == "-20"

    def test_micro_jitter_is_stable(self):
        # +3 points is noise, don't flag it
        d = compute_delta_for_item(
            {"symbol": "K", "score": 53, "zone": "EARLY"},
            prior_snapshot={"K": {"score": 50, "zone": "EARLY"}},
        )
        assert d["type"] == DELTA_TYPE_STABLE

    def test_threshold_boundary(self):
        # Exactly at threshold (5.0) should flag as up
        d = compute_delta_for_item(
            {"symbol": "K", "score": 55, "zone": "EARLY"},
            prior_snapshot={"K": {"score": 50, "zone": "EARLY"}},
        )
        assert d["type"] == DELTA_TYPE_SCORE_UP
        # Just below threshold should NOT flag
        d2 = compute_delta_for_item(
            {"symbol": "K", "score": 54.9, "zone": "EARLY"},
            prior_snapshot={"K": {"score": 50, "zone": "EARLY"}},
        )
        assert d2["type"] == DELTA_TYPE_STABLE


# ============================================================
# Annotation pipeline (mutates list of items)
# ============================================================
class TestAnnotateWithDeltas:
    def test_attaches_delta_field_to_each_item(self):
        items = [
            {"symbol": "A", "score": 60, "zone": "EARLY"},
            {"symbol": "B", "score": 33, "zone": "EARLY"},  # diff -2, below threshold
        ]
        prior = {"A": {"score": 40, "zone": "EARLY"},
                 "B": {"score": 35, "zone": "EARLY"}}
        annotate_with_deltas(items, prior_snapshot=prior)
        assert items[0]["delta"]["type"] == DELTA_TYPE_SCORE_UP
        assert items[1]["delta"]["type"] == DELTA_TYPE_STABLE

    def test_handles_missing_prior(self):
        items = [{"symbol": "X", "score": 10, "zone": "EARLY"}]
        # No prior snapshot at all → stable, not "new"
        annotate_with_deltas(items, prior_snapshot=None)
        assert items[0]["delta"]["type"] == DELTA_TYPE_STABLE

    def test_returns_same_list(self):
        items = [{"symbol": "A", "score": 50, "zone": "EARLY"}]
        result = annotate_with_deltas(items, prior_snapshot={})
        assert result is items  # same object, mutated


# ============================================================
# Save/load through Redis (mocked) — graceful on Redis down
# ============================================================
class TestSnapshotPersistenceMocked:
    def test_save_noop_when_redis_down(self):
        from data.bullwatch_history import save_snapshot
        with patch("core.redis_client.is_available", return_value=False):
            ok = save_snapshot([{"symbol": "X", "eligible": True, "score": 50}])
            assert ok is False

    def test_save_calls_set_json_with_ttl(self):
        from data.bullwatch_history import save_snapshot, SNAPSHOT_TTL_SEC
        items = [
            {"symbol": "A", "score": 60, "zone": "EARLY", "pattern": "FS",
             "eligible": True, "metrics": {"float_market_cap": 1e9, "rvol": 1.0}},
            {"symbol": "B", "score": 20, "zone": "EARLY", "pattern": "QW",
             "eligible": False, "metrics": {}},  # ineligible — should NOT persist
        ]
        with patch("core.redis_client.is_available", return_value=True), \
             patch("core.redis_client.set_json", return_value=True) as mock_set:
            save_snapshot(items, date=dt.date(2026, 5, 8))
            mock_set.assert_called_once()
            args, kwargs = mock_set.call_args
            # Key format
            assert args[0] == "bullwatch:snapshot:2026-05-08"
            # Only eligible items stored
            stored = args[1]
            assert "A" in stored
            assert "B" not in stored
            # TTL applied
            assert kwargs.get("ttl") == SNAPSHOT_TTL_SEC

    def test_save_swallows_redis_exception(self):
        from data.bullwatch_history import save_snapshot
        with patch("core.redis_client.is_available", return_value=True), \
             patch("core.redis_client.set_json", side_effect=RuntimeError("redis fail")):
            # Must not raise — BullWatch keeps working even if history broken
            ok = save_snapshot([{"symbol": "X", "eligible": True, "score": 50}])
            assert ok is False


# ============================================================
# Score history (for sparklines) — length always == days
# ============================================================
class TestScoreHistory:
    def test_returns_correct_length(self):
        from data.bullwatch_history import get_score_history
        with patch("core.redis_client.is_available", return_value=True), \
             patch("data.bullwatch_history.get_snapshot", return_value=None):
            h = get_score_history("KAPLM", days=7)
            assert len(h) == 7
            assert all(v is None for v in h)

    def test_returns_actual_scores_when_present(self):
        from data.bullwatch_history import get_score_history
        # Simulate: KAPLM had scores 30, 35, 40 over the last 3 days,
        # missing on the others
        def fake_get_snapshot(d):
            today = dt.date.today()
            offset = (today - d).days
            scores_by_offset = {0: 40, 1: 35, 2: 30}
            if offset in scores_by_offset:
                return {"KAPLM": {"score": scores_by_offset[offset]}}
            return None
        with patch("core.redis_client.is_available", return_value=True), \
             patch("data.bullwatch_history.get_snapshot", side_effect=fake_get_snapshot):
            h = get_score_history("KAPLM", days=7)
            # oldest first; days 6,5,4,3 = None; days 2,1,0 = 30, 35, 40
            assert h == [None, None, None, None, 30, 35, 40]


# ============================================================
# Language guarantee: never produce trade-recommending strings
# ============================================================
class TestLanguageNeutral:
    def test_label_short_has_no_trade_directives(self):
        # Test all the delta types we generate. None should say "buy/sell"
        cases = [
            (compute_delta_for_item(
                {"symbol": "X", "score": 60, "zone": "CONFIRMED"},
                {"X": {"score": 40, "zone": "EARLY"}})),
            (compute_delta_for_item(
                {"symbol": "X", "score": 30, "zone": "EARLY"},
                {"X": {"score": 60, "zone": "CONFIRMED"}})),
            (compute_delta_for_item(
                {"symbol": "Y", "score": 50, "zone": "EARLY"},
                {"OTHER": {"score": 50, "zone": "EARLY"}})),
        ]
        forbidden = {"al", "sat", "alın", "satın", "buy", "sell",
                     "kâr", "kar al", "stop", "target", "fırsat"}
        for d in cases:
            label = (d.get("label_short") or "").lower()
            for word in forbidden:
                assert word not in label, (
                    f"forbidden word '{word}' in label '{label}'"
                )

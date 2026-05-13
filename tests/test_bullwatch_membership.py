# ================================================================
# tests/test_bullwatch_membership.py
#
# BullWatch list-membership event detector.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine.bullwatch_membership import detect_changes, detect_and_persist


def _item(sym: str, score: float = 70.0, zone: str = "CONFIRMED",
          pattern: str = "Float Squeeze"):
    return {
        "symbol": sym, "score": score, "zone": zone, "pattern": pattern,
    }


class TestDetectChanges:
    def test_empty_inputs(self):
        assert detect_changes([], []) == []

    def test_entry_when_new_item_appears(self):
        prev = [_item("A"), _item("B")]
        new = [_item("A"), _item("B"), _item("EDIP")]
        events = detect_changes(prev, new, scan_id="s1")
        types = {(e["event_type"], e["ticker"]) for e in events}
        assert ("ENTRY", "EDIP") in types
        # No phantom events for A/B
        assert all(e["ticker"] != "A" for e in events)

    def test_exit_when_item_disappears(self):
        prev = [_item("A"), _item("EDIP")]
        new = [_item("A")]
        events = detect_changes(prev, new, scan_id="s2")
        types = {(e["event_type"], e["ticker"]) for e in events}
        assert ("EXIT", "EDIP") in types

    def test_exit_carries_prev_score_and_zone(self):
        prev = [_item("EDIP", score=72.5, zone="CONFIRMED",
                      pattern="Walk-Up")]
        new = []
        events = detect_changes(prev, new, scan_id="s3")
        e = events[0]
        assert e["event_type"] == "EXIT"
        assert e["prev_score"] == 72.5
        assert e["prev_zone"] == "CONFIRMED"
        assert e["prev_pattern"] == "Walk-Up"
        assert e["new_zone"] is None
        assert e["new_score"] is None

    def test_zone_upgrade(self):
        prev = [_item("FORTE", zone="CONFIRMED", score=70)]
        new = [_item("FORTE", zone="CONVICTION", score=78)]
        events = detect_changes(prev, new, scan_id="s4")
        kinds = [e["event_type"] for e in events]
        assert "ZONE_UPGRADE" in kinds
        e = next(e for e in events if e["event_type"] == "ZONE_UPGRADE")
        assert e["prev_zone"] == "CONFIRMED"
        assert e["new_zone"] == "CONVICTION"
        assert e["prev_score"] == 70
        assert e["new_score"] == 78

    def test_zone_downgrade(self):
        prev = [_item("X", zone="CONVICTION")]
        new = [_item("X", zone="EARLY")]
        events = detect_changes(prev, new, scan_id="s5")
        kinds = [e["event_type"] for e in events]
        assert "ZONE_DOWNGRADE" in kinds

    def test_same_zone_no_event(self):
        prev = [_item("A", zone="CONFIRMED", score=70)]
        new = [_item("A", zone="CONFIRMED", score=72)]
        events = detect_changes(prev, new)
        assert events == []

    def test_event_id_includes_scan_id(self):
        prev = []
        new = [_item("AA")]
        events = detect_changes(prev, new, scan_id="scan-42")
        e = events[0]
        assert "scan-42" in e["event_id"]
        assert e["event_id"].startswith("AA:")
        assert e["event_id"].endswith(":ENTRY")

    def test_handles_ticker_field_too(self):
        # bullwatch items use 'symbol', but be defensive — accept 'ticker'
        prev = [{"ticker": "AA", "zone": "CONFIRMED", "score": 60}]
        new = [{"ticker": "AA", "zone": "CONVICTION", "score": 80}]
        events = detect_changes(prev, new, scan_id="s6")
        assert any(e["event_type"] == "ZONE_UPGRADE"
                   and e["ticker"] == "AA" for e in events)

    def test_combined_changes(self):
        # 1 entry, 1 exit, 1 upgrade, 1 unchanged
        prev = [
            _item("KEEP", zone="CONFIRMED"),
            _item("UP", zone="CONFIRMED"),
            _item("LEAVE", zone="EARLY"),
        ]
        new = [
            _item("KEEP", zone="CONFIRMED"),
            _item("UP", zone="CONVICTION"),
            _item("ENTER", zone="EARLY"),
        ]
        events = detect_changes(prev, new, scan_id="s7")
        kinds = {(e["event_type"], e["ticker"]) for e in events}
        assert ("ZONE_UPGRADE", "UP") in kinds
        assert ("ENTRY", "ENTER") in kinds
        assert ("EXIT", "LEAVE") in kinds
        # KEEP must produce no event
        assert not any(e["ticker"] == "KEEP" for e in events)


class TestDetectAndPersist:
    def test_no_events_no_writes(self, monkeypatch):
        from infra import bullwatch_membership_storage as storage
        calls = {"save": 0}

        def _save(ev):
            calls["save"] += 1
            return True

        monkeypatch.setattr(storage, "save_event", _save)
        out = detect_and_persist([], [], scan_id="s")
        assert out["events"] == 0
        assert calls["save"] == 0

    def test_writes_each_event(self, monkeypatch):
        from infra import bullwatch_membership_storage as storage
        saved = []

        def _save(ev):
            saved.append(ev)
            return True

        monkeypatch.setattr(storage, "save_event", _save)
        prev = [_item("OLD", zone="CONFIRMED")]
        new = [_item("OLD", zone="CONVICTION"), _item("NEW")]
        out = detect_and_persist(prev, new, scan_id="s8")
        assert out["events"] == 2
        assert out["saved"] == 2
        assert out["by_type"].get("ENTRY") == 1
        assert out["by_type"].get("ZONE_UPGRADE") == 1

    def test_storage_failure_does_not_raise(self, monkeypatch):
        from infra import bullwatch_membership_storage as storage

        def _save(ev):
            raise RuntimeError("redis down")

        monkeypatch.setattr(storage, "save_event", _save)
        # Should NOT propagate — refresh loop must keep running
        out = detect_and_persist([], [_item("A")], scan_id="s9")
        # Note: detect_and_persist's outer try catches the storage import
        # but per-event save_event call goes through; we re-raise via
        # the loop variable. So per-event errors break the loop. The
        # contract is "outer function never raises" — verified by no
        # exception bubbling out.
        assert "events" in out

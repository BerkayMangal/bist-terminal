# ================================================================
# tests/test_bullwatch_group_activity.py
#
# Tahtacı PR B — holding-group activity engine + reverse-index helpers.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine.bullwatch_holding_groups import (
    HOLDING_GROUPS,
    get_group,
    get_peers,
)
from engine.bullwatch_group_activity import compute_group_activity_boost


def _alert(ticker: str, days_ago: int = 1) -> dict:
    stamp = (_dt.datetime.now(_dt.timezone.utc)
             - _dt.timedelta(days=days_ago)).isoformat()
    return {"ticker": ticker, "alarmed_at": stamp, "zone": "CONVICTION"}


# ── Holding-group config ─────────────────────────────────────────


class TestHoldingGroupConfig:
    def test_lookup_known_ticker(self):
        assert get_group("BIMAS") == "yildiz"
        assert get_group("ARCLK") == "koc"
        assert get_group("SAHOL") == "sabanci"

    def test_lookup_with_is_suffix(self):
        assert get_group("BIMAS.IS") == "yildiz"
        assert get_group("bimas.is") == "yildiz"

    def test_unknown_ticker_returns_none(self):
        assert get_group("NOPE") is None
        assert get_group("") is None

    def test_peers_excludes_self(self):
        peers = get_peers("BIMAS")
        assert "BIMAS" not in peers
        assert "ULKER" in peers
        assert "TBORG" in peers

    def test_peers_unknown_returns_empty(self):
        assert get_peers("UNKNOWN") == set()

    def test_groups_disjoint_for_tested_tickers(self):
        # Tickers picked for the core test set shouldn't span groups.
        for sample in ("BIMAS", "ARCLK", "SAHOL", "DOHOL"):
            grp = get_group(sample)
            assert grp is not None
            # Only one group claims it
            owners = [g for g, members in HOLDING_GROUPS.items()
                      if sample.upper() in {m.upper() for m in members}]
            assert len(owners) == 1, f"{sample} in multiple groups: {owners}"


# ── Group activity engine ────────────────────────────────────────


class TestGroupActivityBoost:
    def test_unknown_ticker_no_boost(self, monkeypatch):
        out = compute_group_activity_boost("NOPE")
        assert out["boost"] == 0.0
        assert out["group"] is None
        assert out["peer_alerts_14d"] == 0

    def test_no_peer_alerts_returns_zero(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=200, since_days=None: [])
        out = compute_group_activity_boost("BIMAS")
        assert out["boost"] == 0.0
        assert out["group"] == "yildiz"
        assert out["peer_alerts_14d"] == 0

    def test_one_peer_alert(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage
        # ULKER is in Yıldız group with BIMAS
        alerts = [_alert("ULKER", days_ago=3)]
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=200, since_days=None: alerts)
        out = compute_group_activity_boost("BIMAS")
        assert out["boost"] == 1.5
        assert out["peer_alerts_14d"] == 1
        assert "ULKER" in out["peer_tickers_active"]

    def test_two_peer_alerts(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage
        alerts = [_alert("ULKER", days_ago=2), _alert("TBORG", days_ago=5)]
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=200, since_days=None: alerts)
        out = compute_group_activity_boost("BIMAS")
        assert out["boost"] == 3.5
        assert out["peer_alerts_14d"] == 2

    def test_three_peers_caps_curve(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage
        # Koç group has more members — give 3 distinct peers
        alerts = [
            _alert("FROTO", days_ago=1),
            _alert("TUPRS", days_ago=4),
            _alert("TOASO", days_ago=7),
        ]
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=200, since_days=None: alerts)
        out = compute_group_activity_boost("ARCLK")
        assert out["boost"] == 5.0
        assert out["peer_alerts_14d"] == 3

    def test_four_or_more_peers_hits_ceiling(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage
        alerts = [
            _alert("FROTO"), _alert("TUPRS"),
            _alert("TOASO"), _alert("AYGAZ"), _alert("OTKAR"),
        ]
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=200, since_days=None: alerts)
        out = compute_group_activity_boost("ARCLK")
        assert out["boost"] == 6.0

    def test_duplicate_peer_alerts_counted_once(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage
        # ULKER fires twice — should count as one active peer
        alerts = [_alert("ULKER", days_ago=1), _alert("ULKER", days_ago=4)]
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=200, since_days=None: alerts)
        out = compute_group_activity_boost("BIMAS")
        assert out["boost"] == 1.5
        assert out["peer_alerts_14d"] == 1

    def test_self_alert_not_counted_as_peer(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage
        # BIMAS's own alert shouldn't count
        alerts = [_alert("BIMAS", days_ago=2)]
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=200, since_days=None: alerts)
        out = compute_group_activity_boost("BIMAS")
        assert out["boost"] == 0.0
        assert out["peer_alerts_14d"] == 0

    def test_other_group_alerts_not_counted(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage
        # Koç group activity shouldn't help a Yıldız ticker
        alerts = [_alert("ARCLK"), _alert("FROTO")]
        monkeypatch.setattr(storage, "get_recent",
                            lambda limit=200, since_days=None: alerts)
        out = compute_group_activity_boost("BIMAS")
        assert out["boost"] == 0.0

    def test_storage_failure_returns_zero(self, monkeypatch):
        from infra import bullwatch_alerts_storage as storage

        def _boom(*a, **kw):
            raise RuntimeError("redis down")

        monkeypatch.setattr(storage, "get_recent", _boom)
        out = compute_group_activity_boost("BIMAS")
        # Should not raise; should return zero boost with group info intact
        assert out["boost"] == 0.0
        assert out["group"] == "yildiz"

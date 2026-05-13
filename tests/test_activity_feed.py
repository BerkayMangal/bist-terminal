# ================================================================
# tests/test_activity_feed.py
#
# Unified activity feed aggregator.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine import activity_feed as af


def _iso_hours_ago(h: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(hours=h)).isoformat()


def _alarm(ticker="X", hours_ago=1.0, score=82, zone="CONVICTION"):
    return {
        "alert_id": f"a-{ticker}",
        "ticker": ticker,
        "alarmed_at": _iso_hours_ago(hours_ago),
        "score_at_alarm": score,
        "zone_at_alarm": zone,
        "pattern_at_alarm": "Float Squeeze",
    }


def _mem(ticker="X", hours_ago=2.0, etype="ENTRY",
         prev_zone=None, new_zone="CONFIRMED"):
    return {
        "event_id": f"e-{ticker}-{etype}",
        "ticker": ticker,
        "event_type": etype,
        "occurred_at": _iso_hours_ago(hours_ago),
        "prev_zone": prev_zone,
        "new_zone": new_zone,
        "prev_score": None, "new_score": 70,
    }


def _kap(ticker="X", hours_ago=3.0, year=2026, period=1,
         rule="3 Aylık", subject="Konsolide Finansal Tablolar"):
    return {
        "disclosure_index": int(hours_ago * 100),
        "ticker": ticker,
        "disclosure_type": "FR",
        "subject": subject,
        "publish_date": _iso_hours_ago(hours_ago),
        "rule_type": rule, "period": period, "year": year,
    }


def _setup_storage(monkeypatch, alarms=None, mems=None, kap=None, kap_by_t=None):
    """Hook the three storage layers + auto_refresh cycle."""
    import infra.bullwatch_alerts_storage as bas
    import infra.bullwatch_membership_storage as bms
    import infra.kap_storage as ks
    import engine.auto_refresh_stale as ars

    monkeypatch.setattr(bas, "get_recent",
                        lambda limit=200, since_days=None: alarms or [])
    monkeypatch.setattr(bms, "get_recent",
                        lambda limit=300, since_days=None: mems or [])
    monkeypatch.setattr(ks, "get_recent", lambda limit=500: kap or [])
    monkeypatch.setattr(ks, "get_by_ticker",
                        lambda t, limit=20: (kap_by_t or {}).get(t.upper(), []))
    # No score-change events by default
    monkeypatch.setattr(ars, "get_last_cycle", lambda: None)


# ── Sources ──────────────────────────────────────────────────────


class TestAlarmSource:
    def test_picks_recent_alarms(self, monkeypatch):
        _setup_storage(monkeypatch, alarms=[_alarm("AAA", hours_ago=2.0)])
        out = af.get_recent_activity(since_hours=24)
        assert any(i["type"] == af.TYPE_ALARM and i["ticker"] == "AAA"
                   for i in out["items"])

    def test_skips_alarms_outside_window(self, monkeypatch):
        _setup_storage(monkeypatch,
                       alarms=[_alarm("OLD", hours_ago=48.0)])
        out = af.get_recent_activity(since_hours=24)
        assert all(i["ticker"] != "OLD" for i in out["items"])

    def test_alarm_severity_high(self, monkeypatch):
        _setup_storage(monkeypatch, alarms=[_alarm("AAA", hours_ago=1.0)])
        out = af.get_recent_activity(since_hours=24)
        a = next(i for i in out["items"] if i["ticker"] == "AAA")
        assert a["severity"] == "high"


class TestMembershipSource:
    def test_picks_recent_membership_events(self, monkeypatch):
        _setup_storage(monkeypatch, mems=[
            _mem("XYZ", hours_ago=3, etype="ENTRY", new_zone="CONFIRMED"),
        ])
        out = af.get_recent_activity(since_hours=24)
        m = next(i for i in out["items"] if i["ticker"] == "XYZ")
        assert m["type"] == af.TYPE_MEMBERSHIP
        assert "Listeye girdi" in m["summary"]
        assert "CONFIRMED" in m["summary"]

    def test_zone_upgrade_summary(self, monkeypatch):
        _setup_storage(monkeypatch, mems=[
            _mem("AAA", etype="ZONE_UPGRADE",
                 prev_zone="CONFIRMED", new_zone="CONVICTION"),
        ])
        out = af.get_recent_activity(since_hours=24)
        m = next(i for i in out["items"] if i["ticker"] == "AAA")
        assert "Zone yükseldi" in m["summary"]
        assert "CONFIRMED → CONVICTION" in m["summary"]
        assert m["severity"] == "medium"

    def test_zone_downgrade_severity(self, monkeypatch):
        _setup_storage(monkeypatch, mems=[
            _mem("AAA", etype="ZONE_DOWNGRADE",
                 prev_zone="CONVICTION", new_zone="EARLY"),
        ])
        out = af.get_recent_activity(since_hours=24)
        m = next(i for i in out["items"] if i["ticker"] == "AAA")
        assert m["severity"] == "low"

    def test_unknown_event_type_skipped(self, monkeypatch):
        bogus = _mem("AAA", etype="BOGUS_TYPE")
        _setup_storage(monkeypatch, mems=[bogus])
        out = af.get_recent_activity(since_hours=24)
        assert not any(i["ticker"] == "AAA" for i in out["items"])


class TestKapSource:
    def test_only_financial_subjects(self, monkeypatch):
        _setup_storage(monkeypatch, kap=[
            _kap("AAA", subject="Konsolide Finansal Tablolar"),
            _kap("BBB", subject="Sorumluluk Beyanı"),  # not financial
        ])
        out = af.get_recent_activity(since_hours=24)
        tickers = {i["ticker"] for i in out["items"]
                   if i["type"] == af.TYPE_KAP_FINANCIAL}
        assert "AAA" in tickers
        assert "BBB" not in tickers

    def test_annual_severity_high(self, monkeypatch):
        _setup_storage(monkeypatch, kap=[
            _kap("AAA", rule="Yıllık"),
        ])
        out = af.get_recent_activity(since_hours=24)
        e = next(i for i in out["items"] if i["ticker"] == "AAA")
        assert e["severity"] == "high"

    def test_quarterly_severity_medium(self, monkeypatch):
        _setup_storage(monkeypatch, kap=[
            _kap("AAA", rule="3 Aylık"),
        ])
        out = af.get_recent_activity(since_hours=24)
        e = next(i for i in out["items"] if i["ticker"] == "AAA")
        assert e["severity"] == "medium"

    def test_summary_includes_period(self, monkeypatch):
        _setup_storage(monkeypatch, kap=[
            _kap("AAA", rule="3 Aylık", period=1, year=2026),
        ])
        out = af.get_recent_activity(since_hours=24)
        e = next(i for i in out["items"] if i["ticker"] == "AAA")
        assert "Q1 2026" in e["summary"]


# ── Watchlist filter ─────────────────────────────────────────────


class TestWatchlistFilter:
    def test_unset_watchlist_returns_all(self, monkeypatch):
        _setup_storage(monkeypatch, alarms=[
            _alarm("AAA"), _alarm("BBB"),
        ])
        out = af.get_recent_activity(since_hours=24, watchlist=None)
        tickers = {i["ticker"] for i in out["items"]}
        assert tickers == {"AAA", "BBB"}
        assert out["watchlist_filter"] is False

    def test_watchlist_filters_alarms(self, monkeypatch):
        _setup_storage(monkeypatch, alarms=[
            _alarm("AAA"), _alarm("BBB"),
        ])
        out = af.get_recent_activity(
            since_hours=24, watchlist=["AAA"],
        )
        tickers = {i["ticker"] for i in out["items"]}
        assert tickers == {"AAA"}
        assert out["watchlist_filter"] is True

    def test_watchlist_filters_membership(self, monkeypatch):
        _setup_storage(monkeypatch, mems=[
            _mem("AAA"), _mem("BBB"),
        ])
        out = af.get_recent_activity(
            since_hours=24, watchlist=["BBB"],
        )
        tickers = {i["ticker"] for i in out["items"]}
        assert tickers == {"BBB"}

    def test_watchlist_uses_per_ticker_kap_query(self, monkeypatch):
        # When watchlist is set, KAP source should use get_by_ticker
        # instead of get_recent. Validate by populating only the
        # per-ticker map and leaving global get_recent empty.
        _setup_storage(
            monkeypatch,
            kap=[],   # global empty
            kap_by_t={"AAA": [_kap("AAA")]},
        )
        out = af.get_recent_activity(
            since_hours=24, watchlist=["AAA"],
        )
        assert any(i["ticker"] == "AAA"
                   and i["type"] == af.TYPE_KAP_FINANCIAL
                   for i in out["items"])

    def test_empty_watchlist_treated_as_no_filter(self, monkeypatch):
        _setup_storage(monkeypatch, alarms=[_alarm("AAA")])
        out = af.get_recent_activity(
            since_hours=24, watchlist=[],
        )
        # Empty list means no filter
        assert any(i["ticker"] == "AAA" for i in out["items"])
        assert out["watchlist_filter"] is False


# ── Sort + structure ─────────────────────────────────────────────


class TestSortAndStructure:
    def test_sorted_newest_first(self, monkeypatch):
        _setup_storage(monkeypatch, alarms=[
            _alarm("OLD", hours_ago=10),
            _alarm("MID", hours_ago=5),
            _alarm("NEW", hours_ago=1),
        ])
        out = af.get_recent_activity(since_hours=24)
        order = [i["ticker"] for i in out["items"]
                 if i["type"] == af.TYPE_ALARM]
        assert order == ["NEW", "MID", "OLD"]

    def test_counts_reflect_types(self, monkeypatch):
        _setup_storage(monkeypatch,
                       alarms=[_alarm("A1"), _alarm("A2")],
                       mems=[_mem("M1"), _mem("M2"), _mem("M3")],
                       kap=[_kap("K1")])
        out = af.get_recent_activity(since_hours=24)
        c = out["counts"]
        assert c.get("ALARM") == 2
        assert c.get("MEMBERSHIP") == 3
        assert c.get("KAP_FINANCIAL") == 1

    def test_limit_applied(self, monkeypatch):
        alarms = [_alarm(f"T{i}", hours_ago=i) for i in range(1, 20)]
        _setup_storage(monkeypatch, alarms=alarms)
        out = af.get_recent_activity(since_hours=24, limit=5)
        assert len(out["items"]) == 5

    def test_storage_failure_returns_empty(self, monkeypatch):
        import infra.bullwatch_alerts_storage as bas
        import infra.bullwatch_membership_storage as bms
        import infra.kap_storage as ks
        import engine.auto_refresh_stale as ars

        def _boom(*a, **kw):
            raise RuntimeError("redis down")

        monkeypatch.setattr(bas, "get_recent", _boom)
        monkeypatch.setattr(bms, "get_recent", _boom)
        monkeypatch.setattr(ks, "get_recent", _boom)
        monkeypatch.setattr(ks, "get_by_ticker", _boom)
        monkeypatch.setattr(ars, "get_last_cycle", _boom)
        out = af.get_recent_activity(since_hours=24)
        # Should not raise; degrades to empty feed
        assert out["items"] == []

    def test_since_hours_clamped(self, monkeypatch):
        _setup_storage(monkeypatch)
        out = af.get_recent_activity(since_hours=10000)
        # Clamped to <= 168 (7 days)
        assert out["since_hours"] <= 168

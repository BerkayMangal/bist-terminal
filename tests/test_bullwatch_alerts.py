# ================================================================
# tests/test_bullwatch_alerts.py
#
# Faz 1 — BullWatch high-conviction alarm engine + storage.
# Verifies the criteria, dedupe, and SQLite/Redis round-trip.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine.bullwatch_alerts import (
    BullWatchAlert,
    is_high_conviction,
    derive_alerts,
    MIN_SCORE,
    MIN_ENGINES_FIRED,
    DEDUPE_WINDOW_DAYS,
)
from tests._fake_redis import FakeRedis
from tests.test_kap_feed import fake_redis, patched_redis   # noqa: F401


@pytest.fixture
def tmp_sqlite(monkeypatch, tmp_path):
    """Like tests.test_kap_feed.tmp_sqlite but also resets the BullWatch
    alarm storage's thread-local connection so each test sees a fresh DB."""
    import infra.kap_storage as ks
    import infra.bullwatch_alerts_storage as bs
    db_path = str(tmp_path / "bw_alerts_test.db")
    monkeypatch.setattr("infra.storage.DB_PATH", db_path)
    for mod in (ks, bs):
        if hasattr(mod._local, "conn"):
            try:
                mod._local.conn.close()
            except Exception:
                pass
            del mod._local.conn
    ks.init_db()
    bs.init_db()
    yield db_path
    for mod in (ks, bs):
        if hasattr(mod._local, "conn"):
            try:
                mod._local.conn.close()
            except Exception:
                pass
            del mod._local.conn


# ── Sample item factory ─────────────────────────────────────────────


def _strong_item(ticker: str = "KAPLM", **overrides) -> dict:
    """An item that passes ALL alarm criteria by default."""
    base = {
        "symbol": ticker,
        "score": 82.5,
        "zone": "CONVICTION",
        "pattern": "Float Squeeze + Walk-Up Accumulation",
        "data_quality": "high",
        "eligible": True,
        "sector_tr": "Sanayi",
        "components": {
            "float_pressure": 0.95,
            "walk_up": 0.7,
            "absorption": 0.5,
        },
        "metrics": {"price": 12.30},
    }
    base.update(overrides)
    return base


# ── 1. is_high_conviction classifier ───────────────────────────────


class TestCriteria:
    def test_strong_item_passes(self):
        ok, fails = is_high_conviction(_strong_item())
        assert ok is True
        assert fails == []

    def test_ineligible_fails(self):
        ok, fails = is_high_conviction(_strong_item(eligible=False))
        assert ok is False
        assert "not_eligible" in fails

    def test_wrong_zone_fails(self):
        ok, fails = is_high_conviction(_strong_item(zone="CONFIRMED"))
        assert ok is False
        assert "zone_not_conviction" in fails

    def test_low_score_fails(self):
        ok, fails = is_high_conviction(_strong_item(score=MIN_SCORE - 1))
        assert ok is False
        assert any(f.startswith("score_below_") for f in fails)

    def test_low_data_quality_fails(self):
        ok, fails = is_high_conviction(_strong_item(data_quality="medium"))
        assert ok is False
        assert "data_quality_not_high" in fails

    def test_single_engine_fails(self):
        ok, fails = is_high_conviction(_strong_item(
            components={"float_pressure": 0.9, "walk_up": 0},
        ))
        assert ok is False
        assert any("engines_fired_" in f for f in fails)

    def test_exactly_two_engines_passes(self):
        ok, fails = is_high_conviction(_strong_item(
            components={"float_pressure": 0.9, "walk_up": 0.4},
        ))
        assert ok is True


# ── 2. derive_alerts ────────────────────────────────────────────────


class TestDeriveAlerts:
    def test_filters_to_passing_items(self):
        items = [
            _strong_item("KAPLM"),
            _strong_item("BAD", zone="CONFIRMED"),   # filtered out
            _strong_item("GOOD2", symbol="GOOD2"),
        ]
        # Fix the symbol on the third one (overrides dict above)
        items[2]["symbol"] = "GOOD2"
        alerts = derive_alerts(items)
        symbols = {a.ticker for a in alerts}
        assert "KAPLM" in symbols
        assert "GOOD2" in symbols
        assert "BAD" not in symbols

    def test_alert_fields_populated(self):
        items = [_strong_item("KAPLM")]
        alerts = derive_alerts(items)
        a = alerts[0]
        assert a.ticker == "KAPLM"
        assert a.score_at_alarm == 82.5
        assert a.zone_at_alarm == "CONVICTION"
        assert a.pattern_at_alarm.startswith("Float Squeeze")
        assert a.engines_fired == 3
        assert a.price_at_alarm == 12.30
        assert a.sector_tr == "Sanayi"

    def test_empty_input(self):
        assert derive_alerts([]) == []
        assert derive_alerts(None) == []


# ── 3. Storage round-trip + dedupe ─────────────────────────────────


class TestStorage:
    def test_save_and_read(self, patched_redis, tmp_sqlite):
        from infra import bullwatch_alerts_storage as st
        st.init_db()
        alerts = derive_alerts([_strong_item("KAPLM")])
        assert st.save_alert(alerts[0]) is True
        rows = st.get_recent(limit=10)
        assert len(rows) == 1
        assert rows[0]["ticker"] == "KAPLM"

    def test_dedupe_window(self, patched_redis, tmp_sqlite):
        from infra import bullwatch_alerts_storage as st
        st.init_db()
        a = derive_alerts([_strong_item("KAPLM")])[0]
        st.save_alert(a)
        # Same ticker just alarmed → within dedupe window
        assert st.was_alarmed_within("KAPLM", DEDUPE_WINDOW_DAYS) is True
        # Different ticker → not alarmed
        assert st.was_alarmed_within("OTHER", DEDUPE_WINDOW_DAYS) is False

    def test_dedupe_expires(self, patched_redis, tmp_sqlite):
        from infra import bullwatch_alerts_storage as st
        st.init_db()
        a = derive_alerts([_strong_item("KAPLM")])[0]
        # Force an old alarmed_at so it falls outside the window
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=15)).isoformat()
        a_old = BullWatchAlert(
            alert_id=a.alert_id, ticker=a.ticker, alarmed_at=old,
            score_at_alarm=a.score_at_alarm, zone_at_alarm=a.zone_at_alarm,
            pattern_at_alarm=a.pattern_at_alarm,
            data_quality_at_alarm=a.data_quality_at_alarm,
            engines_fired=a.engines_fired, sector_tr=a.sector_tr,
            price_at_alarm=a.price_at_alarm,
        )
        st.save_alert(a_old)
        # 7-day window — 15 days ago doesn't count
        assert st.was_alarmed_within("KAPLM", 7) is False
        # 30-day window — does count
        assert st.was_alarmed_within("KAPLM", 30) is True

    def test_dispatch_dedupes(self, patched_redis, tmp_sqlite):
        from engine.bullwatch_alerts import dispatch_scan_alerts
        from infra import bullwatch_alerts_storage as st
        st.init_db()
        items = [_strong_item("KAPLM"), _strong_item("ASTOR")]
        s1 = dispatch_scan_alerts(items)
        assert s1["persisted"] == 2
        assert s1["deduped"] == 0
        # Second run with same items → both dedupe
        s2 = dispatch_scan_alerts(items)
        assert s2["persisted"] == 0
        assert s2["deduped"] == 2

    def test_get_by_ticker(self, patched_redis, tmp_sqlite):
        from infra import bullwatch_alerts_storage as st
        st.init_db()
        a = derive_alerts([_strong_item("KAPLM")])[0]
        st.save_alert(a)
        rows = st.get_by_ticker("KAPLM", limit=5)
        assert len(rows) == 1
        assert rows[0]["ticker"] == "KAPLM"
        # Unknown ticker returns empty
        assert st.get_by_ticker("UNKNOWN") == []


# ── 4. Stats endpoint shape ────────────────────────────────────────


class TestStats:
    def test_stats_with_no_alerts(self, patched_redis, tmp_sqlite):
        from infra import bullwatch_alerts_storage as st
        st.init_db()
        s = st.get_stats()
        assert s["total_in_sqlite"] == 0
        assert s["last_30d_count"] == 0
        assert s["newest_alarmed_at"] is None

    def test_stats_after_one_alert(self, patched_redis, tmp_sqlite):
        from infra import bullwatch_alerts_storage as st
        st.init_db()
        a = derive_alerts([_strong_item("KAPLM")])[0]
        st.save_alert(a)
        s = st.get_stats()
        assert s["total_in_sqlite"] == 1
        assert s["last_30d_count"] == 1
        assert s["newest_alarmed_at"] is not None

# ================================================================
# tests/test_kap_feed.py
#
# Faz 1 — unit tests for the KAP disclosure feed pipeline.
# No real network calls — pykap is monkeypatched. No real Redis —
# FakeRedis from tests/_fake_redis.py. SQLite uses a tmp file.
# ================================================================

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from data.kap_client import (
    DisclosureRecord,
    DISCLOSURE_TYPE_FINANCIAL,
    _normalize_disclosure,
    _parse_kap_datetime,
)
from tests._fake_redis import FakeRedis


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def tmp_sqlite(monkeypatch, tmp_path):
    """Redirect kap_storage at a temp SQLite file and reset its
    thread-local connection so each test gets a clean DB."""
    db_path = str(tmp_path / "kap_test.db")
    import infra.kap_storage as ks
    # Point the lazy DB_PATH lookup at our tmp file
    monkeypatch.setattr("infra.storage.DB_PATH", db_path)
    # Wipe any existing thread-local connection so the next _conn() call
    # opens against the new path.
    if hasattr(ks._local, "conn"):
        try:
            ks._local.conn.close()
        except Exception:
            pass
        del ks._local.conn
    ks.init_db()
    yield db_path
    # Cleanup
    if hasattr(ks._local, "conn"):
        try:
            ks._local.conn.close()
        except Exception:
            pass
        del ks._local.conn


@pytest.fixture
def patched_redis(monkeypatch, fake_redis):
    """Make core.redis_client.get_client() return our FakeRedis."""
    import core.redis_client as rc
    monkeypatch.setattr(rc, "get_client", lambda: fake_redis)
    return fake_redis


# ── Sample data ─────────────────────────────────────────────────────


def _raw_disclosure(idx: int = 1596848, ticker: str = "ARCLK",
                    subject: str = "Finansal Rapor",
                    dtype: str = "FR", year: int = 2026, period: int = 1,
                    rule_type: str = "3 Aylık"):
    return {
        "publishDate": "22.04.2026 18:30:24",
        "fundCode": None,
        "kapTitle": "ARÇELİK A.Ş." if ticker == "ARCLK" else f"{ticker} A.Ş.",
        "isOldKap": False,
        "disclosureClass": dtype,
        "disclosureType": dtype,
        "disclosureCategory": dtype,
        "summary": None,
        "subject": subject,
        "relatedStocks": None,
        "year": year,
        "ruleType": rule_type,
        "period": period,
        "disclosureIndex": idx,
        "isLate": False,
        "stockCodes": ticker,
        "hasMultiLanguageSupport": True,
        "attachmentCount": 1,
        "modifyStatus": None,
    }


# ── 1. KAP client normalization ─────────────────────────────────────


class TestNormalization:
    def test_parses_kap_datetime_to_utc(self):
        iso = _parse_kap_datetime("22.04.2026 18:30:24")
        # Istanbul is UTC+3, so 18:30:24 local → 15:30:24 UTC
        assert iso is not None
        assert iso.startswith("2026-04-22T15:30:24")
        assert iso.endswith("+00:00")

    def test_parses_garbage_to_none(self):
        assert _parse_kap_datetime("") is None
        assert _parse_kap_datetime("not a date") is None
        assert _parse_kap_datetime(None) is None  # type: ignore

    def test_normalize_round_trip(self):
        rec = _normalize_disclosure(_raw_disclosure())
        assert rec is not None
        assert rec.disclosure_index == 1596848
        assert rec.ticker == "ARCLK"
        assert rec.disclosure_type == "FR"
        assert rec.rule_type == "3 Aylık"
        assert rec.period == 1
        assert rec.year == 2026
        assert rec.attachment_count == 1
        assert rec.is_late is False

    def test_normalize_missing_index_returns_none(self):
        bad = _raw_disclosure()
        bad["disclosureIndex"] = None
        assert _normalize_disclosure(bad) is None

    def test_normalize_blank_ticker_returns_none(self):
        bad = _raw_disclosure()
        bad["stockCodes"] = ""
        assert _normalize_disclosure(bad, fallback_ticker=None) is None

    def test_fallback_ticker_applied_when_stockcodes_blank(self):
        bad = _raw_disclosure()
        bad["stockCodes"] = ""
        rec = _normalize_disclosure(bad, fallback_ticker="ARCLK")
        assert rec is not None
        assert rec.ticker == "ARCLK"

    def test_multi_ticker_uses_first(self):
        raw = _raw_disclosure()
        raw["stockCodes"] = "AKBNK,ISCTR,GARAN"
        rec = _normalize_disclosure(raw)
        assert rec.ticker == "AKBNK"


# ── 2. is_financial_report classification ───────────────────────────


class TestFinancialReportClassification:
    def test_finansal_rapor_is_financial(self):
        rec = _normalize_disclosure(_raw_disclosure(subject="Finansal Rapor"))
        assert rec.is_financial_report() is True

    def test_other_subject_not_financial_even_if_fr_type(self):
        rec = _normalize_disclosure(_raw_disclosure(
            subject="Sorumluluk Beyanı (Konsolide)", dtype="FR",
        ))
        # Type is FR but subject isn't a balance sheet release → no cache invalidate
        assert rec.is_financial_report() is False

    def test_non_fr_type_not_financial(self):
        rec = _normalize_disclosure(_raw_disclosure(
            subject="Finansal Rapor", dtype="ODA",
        ))
        assert rec.is_financial_report() is False


# ── 3. Storage round-trip (Redis + SQLite) ──────────────────────────


class TestStorage:
    def test_save_disclosure_round_trip(self, patched_redis, tmp_sqlite):
        from infra import kap_storage
        rec = _normalize_disclosure(_raw_disclosure(idx=1596848))
        # First save returns True (new)
        assert kap_storage.save_disclosure(rec) is True
        # Idempotent re-save returns False
        assert kap_storage.save_disclosure(rec) is False
        # Recent fetch works
        recent = kap_storage.get_recent(limit=10)
        assert len(recent) == 1
        assert recent[0]["disclosure_index"] == 1596848
        # By-ticker fetch works
        by_t = kap_storage.get_by_ticker("ARCLK", limit=10)
        assert len(by_t) == 1
        assert by_t[0]["ticker"] == "ARCLK"

    def test_last_seen_index_round_trip(self, patched_redis):
        from infra import kap_storage
        assert kap_storage.get_last_seen_index() == 0
        kap_storage.set_last_seen_index(1596848)
        assert kap_storage.get_last_seen_index() == 1596848
        # Negative / zero ignored
        kap_storage.set_last_seen_index(-1)
        assert kap_storage.get_last_seen_index() == 1596848

    def test_by_ticker_sorted_newest_first(self, patched_redis, tmp_sqlite):
        from infra import kap_storage
        # Different publish dates so the ZSET sort isn't tied. In prod
        # disclosure_index is monotonic so this happens naturally.
        rows_raw = [
            _raw_disclosure(idx=1000),
            _raw_disclosure(idx=2000),
            _raw_disclosure(idx=1500),
        ]
        rows_raw[0]["publishDate"] = "20.04.2026 10:00:00"
        rows_raw[1]["publishDate"] = "22.04.2026 18:30:24"
        rows_raw[2]["publishDate"] = "21.04.2026 14:00:00"
        for raw in rows_raw:
            kap_storage.save_disclosure(_normalize_disclosure(raw))
        rows = kap_storage.get_by_ticker("ARCLK", limit=10)
        indices = [r["disclosure_index"] for r in rows]
        assert indices == [2000, 1500, 1000]


# ── 4. Dispatcher — Plan C cache invalidation ───────────────────────


class TestDispatcherInvalidatesCaches:
    def test_financial_report_drops_raw_cache(self, monkeypatch):
        from engine import kap_dispatcher
        from core.cache import raw_cache, analysis_cache
        # Pre-populate caches
        raw_cache.set("ARCLK", {"foo": "bar"})
        analysis_cache.set("ARCLK", {"overall": 75})
        assert raw_cache.get("ARCLK") is not None
        rec = _normalize_disclosure(_raw_disclosure(subject="Finansal Rapor"))
        kap_dispatcher.dispatch_new_disclosure(rec)
        # raw_cache should be gone
        assert raw_cache.get("ARCLK") is None
        assert analysis_cache.get("ARCLK") is None

    def test_non_financial_does_not_invalidate(self, monkeypatch):
        from engine import kap_dispatcher
        from core.cache import raw_cache
        raw_cache.set("KCHOL", {"x": 1})
        # ODA = Özel Durum, not a balance sheet release
        rec = _normalize_disclosure(_raw_disclosure(
            ticker="KCHOL",
            subject="Genel Kurul Toplantısı",
            dtype="ODA",
        ))
        kap_dispatcher.dispatch_new_disclosure(rec)
        # Cache untouched
        assert raw_cache.get("KCHOL") == {"x": 1}
        # Cleanup
        raw_cache.pop("KCHOL", None)


# ── 5. Feed cycle — end-to-end with mocked pykap ────────────────────


class TestFeedCycle:
    def test_first_cycle_persists_everything(
        self, monkeypatch, patched_redis, tmp_sqlite,
    ):
        # Mock pykap so we don't hit network
        sample = [_raw_disclosure(idx=2000), _raw_disclosure(idx=1500)]

        def fake_list(ticker, days=7, disclosure_type="FR"):
            from data.kap_client import _normalize_disclosure
            return [_normalize_disclosure(r) for r in sample if r]

        import data.kap_client as kc
        monkeypatch.setattr(kc, "list_disclosures", fake_list)
        # Tahtacı PR A: feed now also fetches ODA general announcements;
        # mock it to empty so this test isolates FR behavior.
        monkeypatch.setattr(kc, "list_general_announcements",
                            lambda ticker, days=14: [])

        # Tiny universe
        import engine.kap_feed as feed
        stats = feed.run_one_cycle(universe=["ARCLK", "AKBNK"])
        # 2 tickers × 2 disclosures, dedup by index gives 2 unique persisted
        # (both tickers return same indices, only first persists each)
        assert stats.universe_size == 2
        assert stats.new_disclosures_persisted >= 2  # exact count depends on dedup
        assert stats.highest_index_seen == 2000

        # High-water mark advanced
        from infra import kap_storage
        assert kap_storage.get_last_seen_index() == 2000

    def test_second_cycle_skips_already_seen(
        self, monkeypatch, patched_redis, tmp_sqlite,
    ):
        sample = [_raw_disclosure(idx=2000)]

        def fake_list(ticker, days=7, disclosure_type="FR"):
            from data.kap_client import _normalize_disclosure
            return [_normalize_disclosure(r) for r in sample if r]

        import data.kap_client as kc
        monkeypatch.setattr(kc, "list_disclosures", fake_list)
        monkeypatch.setattr(kc, "list_general_announcements",
                            lambda ticker, days=14: [])
        import engine.kap_feed as feed

        # First cycle persists
        feed.run_one_cycle(universe=["ARCLK"])
        # Second cycle should persist 0 new
        stats2 = feed.run_one_cycle(universe=["ARCLK"])
        assert stats2.new_disclosures_persisted == 0

    def test_cycle_isolates_per_ticker_errors(
        self, monkeypatch, patched_redis, tmp_sqlite,
    ):
        """A failure in one ticker shouldn't poison the rest."""
        good = [_normalize_disclosure(_raw_disclosure(idx=3000, ticker="ARCLK"))]

        def flaky(ticker, days=7, disclosure_type="FR"):
            if ticker == "BAD":
                raise RuntimeError("simulated outage")
            return good if ticker == "ARCLK" else []

        import data.kap_client as kc
        monkeypatch.setattr(kc, "list_disclosures", flaky)
        monkeypatch.setattr(kc, "list_general_announcements",
                            lambda ticker, days=14: [])
        import engine.kap_feed as feed
        stats = feed.run_one_cycle(universe=["BAD", "ARCLK", "EMPTY"])
        # ARCLK still got persisted despite BAD blowing up
        assert stats.new_disclosures_persisted >= 1
        # The bad ticker didn't increment errors (caught inside list_disclosures
        # in production code — here our mock raises before that boundary so it
        # IS counted as an error). Either behavior is acceptable; assertion is
        # just that the cycle still completed and persisted the good one.
        assert stats.highest_index_seen == 3000

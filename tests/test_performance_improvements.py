# ================================================================
# tests/test_performance_improvements.py
#
# Verifies the performance work added in this PR:
#   1. KAP calendar cache (12h Redis TTL)
#   2. BullWatch `lite` mode trims heavy fields
#   3. SafeCache.stats() now reports L2 size
# ================================================================

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from tests._fake_redis import FakeRedis


# ── 1. Calendar cache ──────────────────────────────────────────────


class TestCalendarCache:
    def test_cache_hit_skips_pykap(self, monkeypatch):
        """Second call within TTL must not call pykap."""
        from data import kap_client as kc
        from core import redis_client as rc

        fake = FakeRedis()
        monkeypatch.setattr(rc, "get_client", lambda: fake)

        # Pre-populate the cache key with a known payload
        sample = [{"subject": "Finansal Rapor", "startDate": "01.07.2026"}]
        fake.set("bb:kap:calendar:ARCLK",
                 json.dumps(sample, ensure_ascii=False),
                 ex=12 * 3600)

        # Stub pykap so we can detect any unexpected call
        called = {"n": 0}
        class _StubComp:
            def __init__(self, ticker): pass
            def get_expected_disclosure_list(self, count=20):
                called["n"] += 1
                return []
        import pykap.bist
        monkeypatch.setattr(pykap.bist, "BISTCompany", _StubComp)

        result = kc.list_expected_disclosures("ARCLK")
        assert result == sample
        assert called["n"] == 0, "pykap should not be called on cache hit"

    def test_cache_miss_writes_through(self, monkeypatch):
        """First call hits pykap, writes the result to Redis."""
        from data import kap_client as kc
        from core import redis_client as rc

        fake = FakeRedis()
        monkeypatch.setattr(rc, "get_client", lambda: fake)

        sample = [{"subject": "Finansal Rapor", "year": 2026}]
        class _StubComp:
            def __init__(self, ticker): pass
            def get_expected_disclosure_list(self, count=20):
                return sample
        import pykap.bist
        monkeypatch.setattr(pykap.bist, "BISTCompany", _StubComp)

        result = kc.list_expected_disclosures("BIMAS")
        assert result == sample
        # Verify write-through
        cached = fake.get("bb:kap:calendar:BIMAS")
        assert cached is not None
        assert json.loads(cached) == sample

    def test_empty_ticker_returns_empty(self, monkeypatch):
        from data import kap_client as kc
        assert kc.list_expected_disclosures("") == []
        assert kc.list_expected_disclosures(None) == []  # type: ignore

    def test_corrupted_cache_falls_back(self, monkeypatch):
        """Cache contains non-JSON → re-fetch and overwrite."""
        from data import kap_client as kc
        from core import redis_client as rc

        fake = FakeRedis()
        monkeypatch.setattr(rc, "get_client", lambda: fake)
        fake.set("bb:kap:calendar:GARAN", "not valid json {{{", ex=3600)

        sample = [{"subject": "Yıllık Rapor"}]
        class _StubComp:
            def __init__(self, ticker): pass
            def get_expected_disclosure_list(self, count=20):
                return sample
        import pykap.bist
        monkeypatch.setattr(pykap.bist, "BISTCompany", _StubComp)

        result = kc.list_expected_disclosures("GARAN")
        assert result == sample


# ── 2. BullWatch lite mode ─────────────────────────────────────────


class TestLiteMode:
    def test_lite_strips_heavy_fields(self):
        from api.bullwatch import _trim_item_for_lite
        full_item = {
            "symbol": "KAPLM", "score": 85.0, "zone": "CONVICTION",
            "pattern": "Tahtacı KAP Aktivitesi + Float Squeeze",
            "data_quality": "high", "sector_tr": "Sanayi",
            "components": {"float_pressure": 0.9, "kap_activity": 0.6},
            "metrics": {"price": 12.3, "market_cap": 500e6, "free_float": 0.3},
            "reasons": ["Strong float pressure", "Insider buy 2 days ago"],
            "narrative": {"whats_happening": "lorem ipsum " * 50},
        }
        trimmed = _trim_item_for_lite(full_item)
        # Kept
        assert trimmed["symbol"] == "KAPLM"
        assert trimmed["score"] == 85.0
        assert trimmed["zone"] == "CONVICTION"
        assert trimmed["pattern"].startswith("Tahtacı")
        assert trimmed["sector_tr"] == "Sanayi"
        # Stripped
        assert "metrics" not in trimmed
        assert "components" not in trimmed
        assert "reasons" not in trimmed
        assert "narrative" not in trimmed

    def test_apply_filters_with_lite(self):
        from api.bullwatch import _apply_filters_and_slice
        payload = {"items": [
            {"symbol": "A", "score": 90, "zone": "CONVICTION",
             "metrics": {"x": 1}, "narrative": {"y": 2}},
            {"symbol": "B", "score": 70, "zone": "CONFIRMED",
             "metrics": {"x": 3}, "narrative": {"y": 4}},
        ]}
        out = _apply_filters_and_slice(payload, None, 0, 10, lite=True)
        assert len(out) == 2
        for item in out:
            assert "metrics" not in item
            assert "narrative" not in item
        # Without lite, fields preserved
        out_full = _apply_filters_and_slice(payload, None, 0, 10, lite=False)
        for item in out_full:
            assert "metrics" in item


# ── 3. SafeCache L2 size ───────────────────────────────────────────


class TestL2Visibility:
    def test_stats_reports_l2_size(self, monkeypatch):
        """stats() should count Redis L2 entries under the namespace."""
        from core import cache as cache_mod
        from core import redis_client as rc

        fake = FakeRedis()
        # Pre-seed some L2 entries under raw namespace
        for sym in ("ARCLK", "BIMAS", "KAPLM"):
            fake.set(f"bb:cache:raw:{sym}", json.dumps({"x": 1}))
        # Different namespace shouldn't count
        fake.set("bb:cache:analysis:AKBNK", json.dumps({"y": 2}))

        monkeypatch.setattr(rc, "get_client", lambda: fake)
        monkeypatch.setattr(rc, "is_available", lambda: True)

        stats = cache_mod.raw_cache.stats()
        assert stats["namespace"] == "raw"
        assert stats["l2_size"] == 3

    def test_stats_l2_none_when_redis_unavailable(self, monkeypatch):
        from core import cache as cache_mod
        from core import redis_client as rc
        monkeypatch.setattr(rc, "is_available", lambda: False)
        stats = cache_mod.raw_cache.stats()
        assert stats["l2_size"] is None

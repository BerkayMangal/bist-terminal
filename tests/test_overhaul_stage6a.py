# ================================================================
# tests/test_overhaul_stage6a.py
#
# Great Overhaul Stage 6a: Persistent history cache (cache-first).
#
# Problem after Stage 5:
#   BullWatch progress was visible, but EVERY refresh still hit borsapy
#   for all ~437 tickers. With observed rate-limit retries, each refresh
#   stayed in the 2-5 min range. User: "her refresh'te yavaş".
#
# Fix:
#   batch_download_history_v9 now consults history_cache (a SafeCache
#   with 24h TTL) BEFORE issuing any borsapy requests. Warm entries
#   skip the network entirely; only cold misses are fetched. Fresh
#   results are then written back to the cache for the next refresh.
#
# Expected impact:
#   - First cold scan: same as Stage 5 (~3 min for 437 tickers)
#   - Subsequent refreshes within TTL: ~5-10 seconds (cache only)
# ================================================================

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Helper — stub borsapy with a hit counter so we can assert calls
# ────────────────────────────────────────────────────────────────


class _Counter:
    def __init__(self):
        self.calls = 0
        self.symbols_fetched: list[str] = []


def _install_borsapy_stub(monkeypatch, counter: _Counter):
    """Stub providers.bp / providers.cb_borsapy so we don't touch network."""
    from data import providers

    class _StubDF:
        def __len__(self): return 100  # > 20 → accepted

    class _StubTk:
        def __init__(self, tc):
            self._tc = tc

        def history(self, period=None, interval=None):
            counter.calls += 1
            counter.symbols_fetched.append(self._tc)
            return _StubDF()

    class _Stub_bp:
        Ticker = staticmethod(_StubTk)

    class _StubCB:
        def before_call(self): pass
        def on_success(self): pass

    monkeypatch.setattr(providers, "_is_empty_frame", lambda df: False)
    monkeypatch.setattr(providers, "BORSAPY_AVAILABLE", True)
    monkeypatch.setattr(providers, "bp", _Stub_bp)
    monkeypatch.setattr(providers, "cb_borsapy", _StubCB())


def _reset_history_cache():
    """Force the in-memory cache empty before each test."""
    from core.cache import history_cache
    history_cache.clear() if hasattr(history_cache, "clear") else None
    # SafeCache may not expose clear; use the internal cache dict
    try:
        history_cache._cache.clear()
        history_cache._timestamps.clear()
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────
# Cold start: every symbol fetched from borsapy
# ────────────────────────────────────────────────────────────────


class TestColdStart:
    def test_empty_cache_fetches_all(self, monkeypatch):
        from data import providers
        _reset_history_cache()
        counter = _Counter()
        _install_borsapy_stub(monkeypatch, counter)

        result = providers.batch_download_history_v9(["AAA", "BBB", "CCC"])

        # All 3 symbols fetched, none from cache
        assert len(result) == 3
        assert counter.calls == 3
        assert sorted(counter.symbols_fetched) == ["AAA", "BBB", "CCC"]


# ────────────────────────────────────────────────────────────────
# Warm cache: NO borsapy calls
# ────────────────────────────────────────────────────────────────


class TestWarmCache:
    def test_all_warm_skips_borsapy(self, monkeypatch):
        """Full cache hit → zero network calls. The fundamental Stage 6a
        promise."""
        from data import providers
        from core.cache import history_cache
        _reset_history_cache()

        # Pre-warm the cache for 3 tickers
        class _StubDF:
            def __len__(self): return 100
        for sym in ("AAA", "BBB", "CCC"):
            history_cache.set(sym, _StubDF())

        counter = _Counter()
        _install_borsapy_stub(monkeypatch, counter)

        result = providers.batch_download_history_v9(["AAA", "BBB", "CCC"])

        assert len(result) == 3
        # CRITICAL: borsapy was NOT touched
        assert counter.calls == 0


class TestPartialWarm:
    def test_only_cold_symbols_fetched(self, monkeypatch):
        from data import providers
        from core.cache import history_cache
        _reset_history_cache()

        # Warm 2 of 4; the other 2 will be cold
        class _StubDF:
            def __len__(self): return 100
        history_cache.set("WARM1", _StubDF())
        history_cache.set("WARM2", _StubDF())

        counter = _Counter()
        _install_borsapy_stub(monkeypatch, counter)

        result = providers.batch_download_history_v9(
            ["WARM1", "COLD1", "WARM2", "COLD2"],
        )
        assert len(result) == 4
        # Only the 2 cold tickers should hit borsapy
        assert counter.calls == 2
        assert sorted(counter.symbols_fetched) == ["COLD1", "COLD2"]


# ────────────────────────────────────────────────────────────────
# use_cache=False — admin escape hatch refetches everything
# ────────────────────────────────────────────────────────────────


class TestForceRefetch:
    def test_use_cache_false_ignores_warm(self, monkeypatch):
        from data import providers
        from core.cache import history_cache
        _reset_history_cache()

        class _StubDF:
            def __len__(self): return 100
        history_cache.set("AAA", _StubDF())
        history_cache.set("BBB", _StubDF())

        counter = _Counter()
        _install_borsapy_stub(monkeypatch, counter)

        providers.batch_download_history_v9(
            ["AAA", "BBB"], use_cache=False,
        )
        # All re-fetched despite the warm cache
        assert counter.calls == 2


# ────────────────────────────────────────────────────────────────
# Write-back: fresh fetches land in the cache
# ────────────────────────────────────────────────────────────────


class TestCacheWriteBack:
    def test_fresh_fetches_are_cached(self, monkeypatch):
        from data import providers
        from core.cache import history_cache
        _reset_history_cache()

        counter = _Counter()
        _install_borsapy_stub(monkeypatch, counter)

        providers.batch_download_history_v9(["NEW1", "NEW2"])

        # Both new symbols are now in the cache
        assert history_cache.get("NEW1") is not None
        assert history_cache.get("NEW2") is not None

    def test_second_call_hits_cache(self, monkeypatch):
        """After a fresh fetch, the next call within TTL should be a
        100% cache hit — the core Stage 6a value prop."""
        from data import providers
        _reset_history_cache()

        counter = _Counter()
        _install_borsapy_stub(monkeypatch, counter)

        # First call: cold
        providers.batch_download_history_v9(["AAA", "BBB"])
        first_calls = counter.calls
        assert first_calls == 2

        # Second call: should now be fully warm
        providers.batch_download_history_v9(["AAA", "BBB"])
        assert counter.calls == first_calls, (
            "Second refresh hit borsapy — cache write-back broken?"
        )


# ────────────────────────────────────────────────────────────────
# Progress callback fires correctly under cache-first
# ────────────────────────────────────────────────────────────────


class TestProgressUnderCache:
    def test_fully_warm_emits_immediate_full_progress(self, monkeypatch):
        from data import providers
        from core.cache import history_cache
        _reset_history_cache()

        class _StubDF:
            def __len__(self): return 100
        for s in ("A", "B", "C"):
            history_cache.set(s, _StubDF())

        counter = _Counter()
        _install_borsapy_stub(monkeypatch, counter)

        seen: list[tuple[int, int]] = []
        providers.batch_download_history_v9(
            ["A", "B", "C"],
            progress_callback=lambda d, t: seen.append((d, t)),
        )
        # At least one progress emission with done==total
        assert any(d == 3 and t == 3 for d, t in seen)


# ────────────────────────────────────────────────────────────────
# Edge cases
# ────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_symbol_list(self, monkeypatch):
        from data import providers
        _reset_history_cache()
        counter = _Counter()
        _install_borsapy_stub(monkeypatch, counter)
        result = providers.batch_download_history_v9([])
        assert result == {}
        assert counter.calls == 0

    def test_borsapy_unavailable_returns_empty(self, monkeypatch):
        from data import providers
        _reset_history_cache()
        monkeypatch.setattr(providers, "BORSAPY_AVAILABLE", False)
        result = providers.batch_download_history_v9(["AAA"])
        assert result == {}

# ================================================================
# tests/test_overhaul_stage1.py
#
# Great Overhaul Stage 1: Determinism Foundation
#
# Audit-confirmed bugs:
#   1. compute_kap_boost cutoff = datetime.now() per-call →
#      14-day window varies across symbols in a 20min scan
#   2. compute_group_activity_boost — same issue
#   3. _CACHE dict thread-unsafe writes
#   4. Dead code in providers.py:915-1005 (unreachable duplicate)
#
# This module pins the fixes in tests so regression is detected
# immediately if someone reverts.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Fix 1A + 1B: scan_now deterministic timing
# ────────────────────────────────────────────────────────────────


def _disclosure(ticker, hours_ago, subject="Pay Alım Satım Bildirimi", idx=1):
    pub = (_dt.datetime.now(_dt.timezone.utc)
           - _dt.timedelta(hours=hours_ago)).isoformat()
    return {
        "disclosure_index": idx,
        "ticker": ticker,
        "subject": subject,
        "publish_date": pub,
    }


class TestKapBoostScanNow:
    """Window cutoff must use scan_now, not datetime.now() at call time."""

    def test_scan_now_pins_window(self, monkeypatch):
        from engine.bullwatch_kap_boost import compute_kap_boost
        from infra import kap_storage
        # A disclosure 13.5 days old. Standard 14-day window includes it.
        rows = [_disclosure("BIMAS", hours_ago=13.5 * 24)]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        # With scan_now = now: 13.5 < 14 → in window → score > 0
        score_now, _, meta_now = compute_kap_boost(
            "BIMAS", lookback_days=14,
            scan_now=_dt.datetime.now(_dt.timezone.utc),
        )
        assert score_now is not None
        assert (meta_now.get("signals_in_window") or 0) >= 1

        # With scan_now BACK 1 day: 13.5 - 24 = -10.5h ago from cutoff;
        # cutoff was now-1d-14d = 15 days ago; disclosure 13.5 days ago
        # is still in window. Equivalent verification.
        score_past, _, meta_past = compute_kap_boost(
            "BIMAS", lookback_days=14,
            scan_now=_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1),
        )
        # Both should agree the disclosure exists (1 signal)
        assert (meta_past.get("signals_in_window") or 0) >= 1

    def test_window_boundary_consistency(self, monkeypatch):
        """The CRITICAL bug: disclosure right at the 14-day boundary.
        Without scan_now pinning, two calls a minute apart can get
        different results."""
        from engine.bullwatch_kap_boost import compute_kap_boost
        from infra import kap_storage
        # 14 days 1 minute ago — JUST outside default window
        rows = [_disclosure("BIMAS", hours_ago=14 * 24 + 1/60)]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        # Pin scan_now to NOW. Disclosure is just outside cutoff →
        # signals_in_window = 0
        fixed_now = _dt.datetime.now(_dt.timezone.utc)
        _, _, meta_a = compute_kap_boost("BIMAS", scan_now=fixed_now)
        # Pin scan_now to 1 SEC LATER. Same disclosure, same window
        # because cutoff is also 1 sec later → still 0
        _, _, meta_b = compute_kap_boost(
            "BIMAS", scan_now=fixed_now + _dt.timedelta(seconds=1),
        )
        # Both should agree: 0 signals (just outside window)
        assert meta_a.get("signals_in_window", 0) == 0
        assert meta_b.get("signals_in_window", 0) == 0

    def test_naive_datetime_treated_as_utc(self, monkeypatch):
        """If caller passes a naive datetime, function must coerce to
        UTC instead of crashing (defensive)."""
        from engine.bullwatch_kap_boost import compute_kap_boost
        from infra import kap_storage
        rows = [_disclosure("BIMAS", hours_ago=1)]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        naive = _dt.datetime.utcnow()    # naive (no tzinfo)
        score, _, _ = compute_kap_boost("BIMAS", scan_now=naive)
        # No crash, score computed
        assert score is not None or score == 0

    def test_legacy_no_scan_now_still_works(self, monkeypatch):
        """Backwards-compat: callers that don't pass scan_now keep
        the legacy 'now-at-call' behavior."""
        from engine.bullwatch_kap_boost import compute_kap_boost
        from infra import kap_storage
        rows = [_disclosure("BIMAS", hours_ago=2)]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        score, _, meta = compute_kap_boost("BIMAS")    # no scan_now
        assert (meta.get("signals_in_window") or 0) >= 1


class TestGroupActivityScanNow:
    def test_scan_now_pins_window(self, monkeypatch):
        from engine import bullwatch_group_activity as ga
        from infra import bullwatch_alerts_storage as ast
        # Peer alert 13 days ago
        peer_stamp = (_dt.datetime.now(_dt.timezone.utc)
                      - _dt.timedelta(days=13)).isoformat()
        alerts = [{"ticker": "ULKER", "alarmed_at": peer_stamp,
                   "zone": "CONVICTION"}]
        monkeypatch.setattr(ast, "get_recent",
                            lambda limit=200, since_days=None: alerts)
        # With scan_now = today, window [today-14d, today] includes
        # 13-day-ago alert.
        out = ga.compute_group_activity_boost(
            "BIMAS", lookback_days=14,
            scan_now=_dt.datetime.now(_dt.timezone.utc),
        )
        assert "ULKER" in out.get("peer_tickers_active", [])

    def test_legacy_no_scan_now_still_works(self, monkeypatch):
        from engine import bullwatch_group_activity as ga
        from infra import bullwatch_alerts_storage as ast
        peer_stamp = (_dt.datetime.now(_dt.timezone.utc)
                      - _dt.timedelta(days=2)).isoformat()
        alerts = [{"ticker": "ULKER", "alarmed_at": peer_stamp,
                   "zone": "CONVICTION"}]
        monkeypatch.setattr(ast, "get_recent",
                            lambda limit=200, since_days=None: alerts)
        out = ga.compute_group_activity_boost("BIMAS")    # no scan_now
        assert "ULKER" in out.get("peer_tickers_active", [])


# ────────────────────────────────────────────────────────────────
# Fix 1C: scan() threads scan_now through to score_symbol
# ────────────────────────────────────────────────────────────────


class TestScanScanNowPlumbing:
    def test_scan_signature_accepts_scan_now(self):
        from engine.bullwatch import scan
        import inspect
        sig = inspect.signature(scan)
        assert "scan_now" in sig.parameters

    def test_score_symbol_signature_accepts_scan_now(self):
        from engine.bullwatch import score_symbol
        import inspect
        sig = inspect.signature(score_symbol)
        assert "scan_now" in sig.parameters

    def test_scan_captures_now_once_when_omitted(self, monkeypatch):
        """Even if caller doesn't supply scan_now, the scan() function
        must capture it ONCE internally and reuse for all symbols."""
        from engine import bullwatch
        captured_scan_nows = []

        def _spy_score_symbol(metrics, df, ownership, cap_tl=None, scan_now=None):
            captured_scan_nows.append(scan_now)
            from engine.bullwatch import BullWatchResult
            return BullWatchResult(
                symbol=metrics.get("symbol", "X"),
                score=0.0, zone="EARLY", pattern="quiet",
                eligible=False,
            )

        # Monkeypatch within the scan path
        monkeypatch.setattr(bullwatch, "score_symbol", _spy_score_symbol)
        # Provide trivial providers so scan can run without I/O
        def _metrics_fn(sym):
            return {"market_cap": 1e9, "free_float": 0.4,
                    "shares": 1e7, "symbol": sym}

        def _history_fn(syms):
            return {s: None for s in syms}

        bullwatch.scan(
            ["AAA", "BBB", "CCC"],
            metrics_fn=_metrics_fn, history_fn=_history_fn,
            max_workers=2,
        )
        # All 3 symbols must have received the SAME scan_now
        # (within the scan, time advances but the captured ref is pinned)
        assert len(captured_scan_nows) == 3
        # All should be a datetime object (not None)
        assert all(isinstance(t, _dt.datetime) for t in captured_scan_nows)
        # All must be IDENTICAL — the determinism guarantee
        assert all(t == captured_scan_nows[0] for t in captured_scan_nows)


# ────────────────────────────────────────────────────────────────
# Fix 1D: Thread-safe _CACHE
# ────────────────────────────────────────────────────────────────


class TestCacheThreadSafety:
    def test_cache_helpers_exist(self):
        from api import bullwatch as bw_api
        for fn_name in ("_cache_set", "_cache_get",
                        "_cache_update", "_cache_snapshot"):
            assert hasattr(bw_api, fn_name), f"missing helper: {fn_name}"

    def test_cache_lock_is_reentrant(self):
        from api import bullwatch as bw_api
        # RLock — must allow same thread to acquire twice without deadlock
        with bw_api._CACHE_LOCK:
            with bw_api._CACHE_LOCK:
                bw_api._cache_set("test_key", "v1")
        assert bw_api._cache_get("test_key") == "v1"
        # cleanup
        bw_api._cache_set("test_key", None)

    def test_concurrent_writes_no_partial_state(self):
        """Two threads writing different keys must not interfere; readers
        get atomic dict state."""
        from api import bullwatch as bw_api

        def _writer_a():
            for i in range(200):
                bw_api._cache_update(test_a=i, test_b=i * 10)

        def _writer_b():
            for i in range(200):
                bw_api._cache_update(test_c=i, test_d=i * 100)

        t1 = threading.Thread(target=_writer_a)
        t2 = threading.Thread(target=_writer_b)
        t1.start(); t2.start()
        # Snapshot during contention — should never crash or see partials
        for _ in range(50):
            snap = bw_api._cache_snapshot()
            # snapshot is a dict, queryable safely
            assert isinstance(snap, dict)
            time.sleep(0.001)
        t1.join(); t2.join()
        final = bw_api._cache_snapshot()
        assert final.get("test_a") == 199
        assert final.get("test_c") == 199
        # cleanup
        for k in ("test_a", "test_b", "test_c", "test_d"):
            bw_api._cache_set(k, None)

    def test_atomic_update_sees_consistent_pair(self):
        """When progress + total are updated together via _cache_update,
        a concurrent reader must see EITHER old pair OR new pair, never
        old.progress + new.total or vice versa."""
        from api import bullwatch as bw_api
        # Seed
        bw_api._cache_update(progress=0, total=100)
        observed = []

        def _writer():
            for i in range(1, 51):
                bw_api._cache_update(progress=i, total=i * 2)

        def _reader():
            for _ in range(200):
                snap = bw_api._cache_snapshot()
                p, t = snap.get("progress"), snap.get("total")
                if p is not None and t is not None:
                    # Invariant: total == progress * 2 (always, post-init)
                    observed.append(t == p * 2)

        tw = threading.Thread(target=_writer)
        tr = threading.Thread(target=_reader)
        tw.start(); tr.start()
        tw.join(); tr.join()
        # Every observation must hold the invariant
        assert all(observed), (
            "atomic _cache_update broken — saw partial state: "
            f"{sum(observed)}/{len(observed)} observations passed"
        )


# ────────────────────────────────────────────────────────────────
# Fix 1E: Dead code removal
# ────────────────────────────────────────────────────────────────


class TestProvidersDeadCodeRemoved:
    def test_file_length_under_old_size(self):
        """The duplicate dead block at lines 915-1005 (90 lines) must
        be gone. File should be < 920 lines."""
        path = os.path.join(
            os.path.dirname(__file__), "..", "data", "providers.py",
        )
        with open(path, "r", encoding="utf-8") as fh:
            n = sum(1 for _ in fh)
        assert n < 920, (
            f"data/providers.py has {n} lines — dead code may have "
            "regressed (was 1005 with duplicate, now should be ~914)"
        )

    def test_no_duplicate_fetch_one_in_batch(self):
        """The batch_download_history_v9 function should have EXACTLY
        ONE _fetch_one inner definition, not two."""
        path = os.path.join(
            os.path.dirname(__file__), "..", "data", "providers.py",
        )
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        # Slice to within batch_download_history_v9
        start = src.find("def batch_download_history_v9")
        # Find the next top-level def (or end of file)
        rest = src[start:]
        # Look for the start of the next sibling top-level def or end
        # of file. Sibling defs are at column 0.
        # We can grep for "_fetch_one" definitions
        n = rest.count("def _fetch_one(")
        # The function used to have 2 (one dead). After fix: 1.
        assert n == 1, (
            f"Expected 1 _fetch_one in batch_download_history_v9, "
            f"found {n}"
        )

    def test_providers_imports_cleanly(self):
        """Sanity — file still parses + imports."""
        import importlib
        import data.providers as p
        importlib.reload(p)
        assert hasattr(p, "batch_download_history_v9")
        assert hasattr(p, "fetch_raw_v9")

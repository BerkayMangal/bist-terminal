# ================================================================
# tests/test_audit_fixes.py
#
# Strict tests covering the fixes that came out of the parallel audit
# pass (PR following #61). Each test pins ONE specific bug + edge case
# so regressions surface immediately if the fix is reverted.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# 1. Holding groups must be disjoint
# ────────────────────────────────────────────────────────────────


class TestHoldingGroupsDisjoint:
    def test_no_ticker_in_two_groups(self):
        from engine.bullwatch_holding_groups import HOLDING_GROUPS
        seen: dict[str, str] = {}
        for grp, members in HOLDING_GROUPS.items():
            for t in members:
                u = t.upper()
                if u in seen:
                    pytest.fail(
                        f"{u} appears in BOTH {seen[u]!r} and {grp!r}"
                    )
                seen[u] = grp

    def test_ykbnk_is_koc_only(self):
        # The fix removed the duplicate "yapikredi" group. YKBNK was
        # in both "koc" and "yapikredi" — reverse-index last-wins meant
        # peer alerts fired against KCHOL only instead of the full Koç
        # family. Lock this in.
        from engine.bullwatch_holding_groups import get_group, get_peers
        assert get_group("YKBNK") == "koc"
        peers = get_peers("YKBNK")
        assert "ARCLK" in peers           # full Koç peers, not just KCHOL
        assert "FROTO" in peers

    def test_aksa_is_akkok_only(self):
        from engine.bullwatch_holding_groups import get_group
        assert get_group("AKSA") == "akkok"

    def test_brsan_is_borusan_only(self):
        from engine.bullwatch_holding_groups import get_group
        assert get_group("BRSAN") == "borusan"

    def test_disjoint_assertion_runs_at_import(self):
        # The import-time assertion in bullwatch_holding_groups must
        # raise if anyone re-introduces a duplicate. Simulate that.
        from engine import bullwatch_holding_groups as bhg
        original = dict(bhg.HOLDING_GROUPS)
        try:
            bhg.HOLDING_GROUPS["bogus"] = {"YKBNK"}      # collision
            with pytest.raises(RuntimeError, match="overlap"):
                bhg._assert_groups_disjoint()
        finally:
            bhg.HOLDING_GROUPS.clear()
            bhg.HOLDING_GROUPS.update(original)


# ────────────────────────────────────────────────────────────────
# 2. Backtest histogram captures extreme returns
# ────────────────────────────────────────────────────────────────


class TestBacktestHistogramExtremes:
    def _setup(self, monkeypatch, returns_1d):
        import datetime as _dt2
        from infra import bullwatch_alerts_storage as st
        rows = []
        for i, r in enumerate(returns_1d):
            stamp = (_dt2.datetime.now(_dt2.timezone.utc)
                     - _dt2.timedelta(days=i + 1)).isoformat()
            rows.append({
                "alert_id": f"a-{i}",
                "ticker": "T",
                "alarmed_at": stamp,
                "score_at_alarm": 82,
                "zone_at_alarm": "CONVICTION",
                "pattern_at_alarm": "Float Squeeze",
                "sector_tr": "Endüstri",
                "reaction_1d_pct": r,
                "reaction_1w_pct": None,
                "reaction_1m_pct": None,
            })
        monkeypatch.setattr(st, "get_recent",
                            lambda limit=500, since_days=None: rows)

    def test_extreme_pump_captured(self, monkeypatch):
        # 500% return must NOT silently vanish. Previously bins capped
        # at (10, 100), so values >= 100 fell through. After fix bins
        # use ±inf endpoints.
        from engine.bullwatch_backtest import compute_backtest
        self._setup(monkeypatch, [500.0, 250.0, 12.0])
        out = compute_backtest()
        buckets = {b["bucket"]: b["count"] for b in out["histogram_1d"]}
        # All three values are > 10%, so they all land in ">10%"
        assert buckets.get(">10%", 0) == 3

    def test_extreme_crash_captured(self, monkeypatch):
        # -90% must land in <-10%, not silently vanish via < -100 lower bound.
        from engine.bullwatch_backtest import compute_backtest
        self._setup(monkeypatch, [-90.0, -50.0, -15.0])
        out = compute_backtest()
        buckets = {b["bucket"]: b["count"] for b in out["histogram_1d"]}
        assert buckets.get("<-10%", 0) == 3

    def test_all_zero_returns(self, monkeypatch):
        # All exactly 0.0 — must land in "-2..0%" (bin is [-2, 0) so
        # 0 doesn't match) ... actually 0 lands in "0..2%" since [0, 2).
        # Either way, NO value should vanish.
        from engine.bullwatch_backtest import compute_backtest
        self._setup(monkeypatch, [0.0] * 5)
        out = compute_backtest()
        total = sum(b["count"] for b in out["histogram_1d"])
        assert total == 5


# ────────────────────────────────────────────────────────────────
# 3. Freshness: future-dated _fetched_at must not be "fresh"
# ────────────────────────────────────────────────────────────────


class TestFreshnessFutureDate:
    def test_future_fetched_at_is_unknown_not_fresh(self):
        from engine.diag_fundamentals import _age_status
        # Negative hours = future date (clock skew, bad data).
        # Used to silently match "fresh" (hours <= 26). After fix:
        # treated as unknown so the UI doesn't show misleading green ✓.
        assert _age_status(-5.0) == "unknown"
        assert _age_status(-0.1) == "unknown"
        # Boundary: 0 hours = right now, still fresh
        assert _age_status(0.0) == "fresh"

    def test_age_status_bands_unchanged_for_positive(self):
        from engine.diag_fundamentals import _age_status
        assert _age_status(1.0) == "fresh"
        assert _age_status(26.0) == "fresh"
        assert _age_status(40.0) == "old"
        assert _age_status(100.0) == "stale"
        assert _age_status(None) == "unknown"


# ────────────────────────────────────────────────────────────────
# 4. Membership event_id uniqueness without scan_id
# ────────────────────────────────────────────────────────────────


class TestMembershipEventIdNoScanId:
    def test_microsecond_timestamps_distinguished(self):
        # When scan_id is None, the fallback ID derives from the full
        # ISO timestamp including microseconds. Two detections in the
        # SAME wall-clock second (different microseconds) must still
        # produce distinct event_ids — otherwise INSERT OR IGNORE
        # silently drops the second event.
        from engine.bullwatch_membership import detect_changes
        new_items = [{"symbol": "AA", "zone": "CONFIRMED", "score": 70}]
        # Two calls at different microsecond instants
        e1 = detect_changes([], new_items, scan_id=None,
                            occurred_at="2026-05-13T15:00:00.123456+00:00")
        e2 = detect_changes([], new_items, scan_id=None,
                            occurred_at="2026-05-13T15:00:00.987654+00:00")
        assert e1[0]["event_id"] != e2[0]["event_id"]

    def test_explicit_scan_id_used_when_provided(self):
        from engine.bullwatch_membership import detect_changes
        new_items = [{"symbol": "AA", "zone": "CONFIRMED", "score": 70}]
        events = detect_changes([], new_items, scan_id="scan-99")
        assert "scan-99" in events[0]["event_id"]


# ────────────────────────────────────────────────────────────────
# 5. Score velocity: all-None scores must not look frozen
# ────────────────────────────────────────────────────────────────


class TestVelocityAllNoneScores:
    def test_all_none_scores_not_frozen(self, monkeypatch):
        # If score_history has 10 rows but every score column is NULL,
        # we should report n_snapshots=0 and frozen=False — NOT claim
        # the ticker is mysteriously frozen.
        from engine.diag_fundamentals import compute_score_velocity
        import infra.storage as st

        class _C:
            def execute(self, *a, **kw):
                class _R:
                    def fetchall(self): return [(None,)] * 10
                return _R()

        monkeypatch.setattr(st, "_get_conn", lambda: _C())
        out = compute_score_velocity("X")
        assert out["n_snapshots"] == 0
        assert out["frozen"] is False


# ────────────────────────────────────────────────────────────────
# 6. Activity feed: sort stability + window clamping
# ────────────────────────────────────────────────────────────────


class TestActivityFeedEdgeCases:
    def test_since_hours_zero_clamped_to_one(self, monkeypatch):
        from engine import activity_feed as af
        import infra.bullwatch_alerts_storage as bas
        import infra.bullwatch_membership_storage as bms
        import infra.kap_storage as ks
        import engine.auto_refresh_stale as ars
        for mod, fn in [(bas, "get_recent"), (bms, "get_recent"),
                        (ks, "get_recent"), (ks, "get_by_ticker")]:
            monkeypatch.setattr(mod, fn,
                                lambda *a, **kw: [])
        monkeypatch.setattr(ars, "get_last_cycle", lambda: None)
        out = af.get_recent_activity(since_hours=0)
        assert out["since_hours"] >= 1

    def test_since_hours_negative_clamped_to_one(self, monkeypatch):
        from engine import activity_feed as af
        import infra.bullwatch_alerts_storage as bas
        import infra.bullwatch_membership_storage as bms
        import infra.kap_storage as ks
        import engine.auto_refresh_stale as ars
        for mod, fn in [(bas, "get_recent"), (bms, "get_recent"),
                        (ks, "get_recent"), (ks, "get_by_ticker")]:
            monkeypatch.setattr(mod, fn, lambda *a, **kw: [])
        monkeypatch.setattr(ars, "get_last_cycle", lambda: None)
        out = af.get_recent_activity(since_hours=-24)
        assert out["since_hours"] >= 1

    def test_sort_stable_for_tied_timestamps(self, monkeypatch):
        # When two events share occurred_at, sort must not crash and
        # must produce a deterministic output (insertion order is fine).
        from engine import activity_feed as af
        same_ts = (_dt.datetime.now(_dt.timezone.utc)
                   - _dt.timedelta(hours=1)).isoformat()
        alarm_rows = [
            {"alert_id": "a1", "ticker": "AAA", "alarmed_at": same_ts,
             "score_at_alarm": 80, "zone_at_alarm": "CONVICTION",
             "pattern_at_alarm": "P"},
            {"alert_id": "a2", "ticker": "BBB", "alarmed_at": same_ts,
             "score_at_alarm": 80, "zone_at_alarm": "CONVICTION",
             "pattern_at_alarm": "P"},
        ]
        import infra.bullwatch_alerts_storage as bas
        import infra.bullwatch_membership_storage as bms
        import infra.kap_storage as ks
        import engine.auto_refresh_stale as ars
        monkeypatch.setattr(bas, "get_recent",
                            lambda limit=200, since_days=None: alarm_rows)
        monkeypatch.setattr(bms, "get_recent",
                            lambda limit=300, since_days=None: [])
        monkeypatch.setattr(ks, "get_recent", lambda limit=500: [])
        monkeypatch.setattr(ks, "get_by_ticker",
                            lambda t, limit=20: [])
        monkeypatch.setattr(ars, "get_last_cycle", lambda: None)
        # Should not raise
        out = af.get_recent_activity(since_hours=24)
        # Both events present (no dropping)
        tickers = [i["ticker"] for i in out["items"]]
        assert set(tickers) == {"AAA", "BBB"}


# ────────────────────────────────────────────────────────────────
# 7. Backtest fake-pump detector boundary
# ────────────────────────────────────────────────────────────────


class TestFakePumpBoundary:
    def _setup(self, monkeypatch, rows):
        from infra import bullwatch_alerts_storage as st
        full = []
        for i, (r1d, r1w) in enumerate(rows):
            stamp = (_dt.datetime.now(_dt.timezone.utc)
                     - _dt.timedelta(days=i + 1)).isoformat()
            full.append({
                "alert_id": f"a-{i}",
                "ticker": f"T{i}",
                "alarmed_at": stamp,
                "score_at_alarm": 82,
                "zone_at_alarm": "CONVICTION",
                "pattern_at_alarm": "X",
                "sector_tr": "Endüstri",
                "reaction_1d_pct": r1d,
                "reaction_1w_pct": r1w,
                "reaction_1m_pct": None,
            })
        monkeypatch.setattr(st, "get_recent",
                            lambda limit=500, since_days=None: full)

    def test_exactly_3pct_and_minus_2pct_is_fake(self, monkeypatch):
        # 1d >= 3 AND 1w <= -2 → flagged. Exact boundaries included.
        from engine.bullwatch_backtest import compute_backtest
        self._setup(monkeypatch, [(3.0, -2.0)])
        out = compute_backtest()
        assert out["fake_pump"]["count"] == 1

    def test_just_below_threshold_not_fake(self, monkeypatch):
        from engine.bullwatch_backtest import compute_backtest
        self._setup(monkeypatch, [(2.99, -1.99)])
        out = compute_backtest()
        assert out["fake_pump"]["count"] == 0

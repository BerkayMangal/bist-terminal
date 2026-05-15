# ================================================================
# tests/test_kap_reactions.py
#
# Faz 4 — KAP reaction tracker.
# Verifies the pure-logic helpers (series parsing, n-trading-day lookup)
# and the storage helpers (price_at_disclosure + reaction_*_pct updates).
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest

from data.kap_client import _normalize_disclosure
from tests._fake_redis import FakeRedis
from tests.test_kap_feed import (   # type: ignore
    fake_redis, tmp_sqlite, patched_redis, _raw_disclosure,
)


# ── Pure-logic helpers ─────────────────────────────────────────────


class TestSeriesHelpers:
    def test_last_close_before(self):
        from engine.kap_reactions import _last_close_before
        prices = [
            (_dt.date(2026, 4, 20), 100.0),
            (_dt.date(2026, 4, 21), 102.0),
            (_dt.date(2026, 4, 22), 105.0),
            (_dt.date(2026, 4, 23), 103.0),
        ]
        assert _last_close_before(prices, _dt.date(2026, 4, 22)) == 105.0
        assert _last_close_before(prices, _dt.date(2026, 4, 19)) is None
        assert _last_close_before(prices, _dt.date(2026, 4, 25)) == 103.0

    def test_close_n_trading_days_after(self):
        from engine.kap_reactions import _close_n_trading_days_after
        prices = [
            (_dt.date(2026, 4, 21), 100.0),
            (_dt.date(2026, 4, 22), 105.0),   # disclosure day
            (_dt.date(2026, 4, 23), 108.0),   # +1
            (_dt.date(2026, 4, 24), 110.0),   # +2
            (_dt.date(2026, 4, 27), 112.0),   # +3 (weekend gap)
            (_dt.date(2026, 4, 28), 115.0),   # +4
        ]
        cutoff = _dt.date(2026, 4, 22)
        assert _close_n_trading_days_after(prices, cutoff, 1) == 108.0
        assert _close_n_trading_days_after(prices, cutoff, 2) == 110.0
        assert _close_n_trading_days_after(prices, cutoff, 3) == 112.0
        # Beyond available data
        assert _close_n_trading_days_after(prices, cutoff, 10) is None

    def test_disclosure_close_date_handles_tz(self):
        from engine.kap_reactions import _disclosure_close_date
        # 22 April 2026, 18:30:24 Istanbul time → still April 22 local
        iso = "2026-04-22T15:30:24+00:00"  # 18:30 Istanbul
        d = _disclosure_close_date(iso)
        assert d == _dt.date(2026, 4, 22)

    def test_series_from_df_handles_typical_shape(self):
        from engine.kap_reactions import _series_from_df
        # DatetimeIndex + Close column — the borsapy / yfinance shape
        idx = pd.to_datetime(["2026-04-20", "2026-04-21", "2026-04-22"])
        df = pd.DataFrame({"Close": [100.0, 102.0, 105.0]}, index=idx)
        s = _series_from_df(df)
        assert len(s) == 3
        assert s[0] == (_dt.date(2026, 4, 20), 100.0)
        assert s[-1] == (_dt.date(2026, 4, 22), 105.0)

    def test_series_from_df_empty_safe(self):
        from engine.kap_reactions import _series_from_df
        assert _series_from_df(None) == []
        assert _series_from_df(pd.DataFrame()) == []


# ── Storage updates ────────────────────────────────────────────────


class TestReactionStorage:
    def test_save_price_at_disclosure_idempotent(self, patched_redis, tmp_sqlite):
        from infra import kap_storage
        from engine.kap_reactions import _save_price_at_disclosure
        rec = _normalize_disclosure(_raw_disclosure(idx=7777))
        kap_storage.save_disclosure(rec)
        _save_price_at_disclosure(7777, 105.50)
        row = kap_storage.get_by_index(7777)
        assert row["price_at_disclosure"] == 105.50
        # Re-save with different value — only updates if NULL, so should stay 105.50
        _save_price_at_disclosure(7777, 99.99)
        row2 = kap_storage.get_by_index(7777)
        assert row2["price_at_disclosure"] == 105.50

    def test_save_reactions_partial(self, patched_redis, tmp_sqlite):
        from infra import kap_storage
        from engine.kap_reactions import _save_reactions
        rec = _normalize_disclosure(_raw_disclosure(idx=8888))
        kap_storage.save_disclosure(rec)
        _save_reactions(8888, {"reaction_1d_pct": 2.5}, "2026-05-12T00:00:00+00:00")
        row = kap_storage.get_by_index(8888)
        assert row["reaction_1d_pct"] == 2.5
        assert row["reaction_1w_pct"] is None
        # Top up later
        _save_reactions(8888, {"reaction_1w_pct": 5.1, "reaction_1m_pct": 8.4},
                        "2026-06-12T00:00:00+00:00")
        row2 = kap_storage.get_by_index(8888)
        assert row2["reaction_1d_pct"] == 2.5
        assert row2["reaction_1w_pct"] == 5.1
        assert row2["reaction_1m_pct"] == 8.4


# ── End-to-end refresh with mocked history ──────────────────────────


class TestRefreshReactions:
    def test_full_refresh_flow(self, monkeypatch, patched_redis, tmp_sqlite):
        from infra import kap_storage
        import engine.kap_reactions as kr

        # Seed an old disclosure (3 days ago) — 1d horizon should fill
        old_rec = _normalize_disclosure(_raw_disclosure(idx=9001, ticker="ARCLK"))
        # Force the publish_date to 3 days ago for SQL date filter
        c = kap_storage._conn()
        kap_storage.save_disclosure(old_rec)
        old_date = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=3)).date()
        c.execute(
            "UPDATE kap_disclosures SET publish_date = ? WHERE disclosure_index = ?",
            (f"{old_date.isoformat()}T18:30:00+00:00", 9001),
        )
        c.commit()

        # Mock batch_download_history to return a series spanning the
        # disclosure day + several days after.
        all_dates = [old_date - _dt.timedelta(days=1), old_date,
                     old_date + _dt.timedelta(days=1),
                     old_date + _dt.timedelta(days=2)]
        df = pd.DataFrame(
            {"Close": [100.0, 100.0, 105.0, 107.0]},
            index=pd.to_datetime([d.isoformat() for d in all_dates]),
        )

        def fake_history(syms, period="1y", interval="1d"):
            return {s: df for s in syms}

        monkeypatch.setattr(
            "engine.technical.batch_download_history", fake_history,
        )

        stats = kr.refresh_reactions(max_rows=10)
        assert stats["scanned"] >= 1
        assert stats["captured_price_at_disclosure"] >= 0  # may be already set
        row = kap_storage.get_by_index(9001)
        assert row["price_at_disclosure"] == 100.0
        # 1 day later close was 105 → +5% reaction
        assert row["reaction_1d_pct"] == pytest.approx(5.0, abs=0.1)

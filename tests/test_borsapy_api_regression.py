"""Phase 4.8.1 — borsapy API regression test.

The Phase 4.7 deploy debugging session found that
`research/ingest_prices.py:_fetch_real` was calling the old
`borsapy.get_prices()` module-level API which doesn't exist
in borsapy >= 0.8. The fix in commit 2aacdfe switched to
`bp.Ticker(sym).history(period="max", interval="1d")`.

This regression test exercises both:
  1. The default-fetcher path (with stubbed borsapy module)
     to lock in the API contract — if borsapy upstream changes
     the Ticker.history signature again, this test will fail
     loudly.
  2. The empty-DataFrame branch — fetcher must return [] cleanly
     instead of crashing on .iterrows().

Pure offline test: builds a fake `borsapy` module with a
synthetic Ticker that returns a tiny pandas DataFrame.
"""

from __future__ import annotations

import sys
from datetime import date
from types import ModuleType
from unittest.mock import patch

import pytest


def _build_fake_borsapy(history_data=None, raise_import: bool = False):
    """Construct a fake borsapy module mimicking the 0.8.x API."""
    if raise_import:
        return None  # caller will pop module

    import pandas as pd

    fake = ModuleType("borsapy")

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period="max", interval="1d"):
            if history_data is None:
                return pd.DataFrame()
            return pd.DataFrame(history_data)

    fake.Ticker = FakeTicker
    return fake


# ==========================================================================
# Default fetcher path: API contract regression
# ==========================================================================

class TestBorsapyApiContract:
    def test_default_fetcher_uses_ticker_history(self, monkeypatch):
        """If borsapy 0.8.x changes again, this test catches it."""
        import pandas as pd
        # 3 trading days of fake data
        df_data = pd.DataFrame({
            "Open": [100.0, 102.0, 101.0],
            "High": [105.0, 103.0, 102.0],
            "Low": [99.0, 100.0, 99.5],
            "Close": [104.0, 101.5, 100.5],
            "Volume": [1000.0, 1500.0, 1200.0],
        }, index=pd.to_datetime([
            "2024-01-15", "2024-01-16", "2024-01-17",
        ]))

        # Patch the borsapy module
        fake = _build_fake_borsapy(df_data.to_dict("list"))
        # Need to set the index too — recreate inside FakeTicker
        class FakeTicker:
            def __init__(self, symbol):
                self.symbol = symbol
            def history(self, period="max", interval="1d"):
                return df_data
        fake.Ticker = FakeTicker

        monkeypatch.setitem(sys.modules, "borsapy", fake)

        from research.ingest_prices import _fetch_real
        result = _fetch_real(
            "AKSEN",
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            fetcher=None,  # exercise default path
        )
        assert len(result) == 3
        # Result entries are dicts with date + OHLCV
        first = result[0]
        assert first["close"] == 104.0
        assert first["volume"] == 1000.0

    def test_default_fetcher_handles_empty_dataframe(self, monkeypatch):
        """borsapy returns empty DF when symbol unknown / delisted.
        _fetch_real must return [] cleanly, not crash."""
        fake = _build_fake_borsapy(None)
        monkeypatch.setitem(sys.modules, "borsapy", fake)

        from research.ingest_prices import _fetch_real
        result = _fetch_real(
            "NONEXISTENT",
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            fetcher=None,
        )
        assert result == []

    def test_default_fetcher_handles_none_dataframe(self, monkeypatch):
        """Some borsapy versions return None instead of empty DF.
        Same defensive behaviour expected."""
        import pandas as pd
        fake = ModuleType("borsapy")

        class FakeTicker:
            def __init__(self, symbol):
                pass
            def history(self, period="max", interval="1d"):
                return None  # the case

        fake.Ticker = FakeTicker
        monkeypatch.setitem(sys.modules, "borsapy", fake)

        from research.ingest_prices import _fetch_real
        result = _fetch_real(
            "AKSEN",
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            fetcher=None,
        )
        assert result == []

    def test_lowercase_columns(self, monkeypatch):
        """Some yfinance-style sources return lowercase columns.
        Patch should normalize to lowercase, output dict keys
        always lowercase."""
        import pandas as pd
        df_data = pd.DataFrame({
            "open": [100.0],
            "high": [105.0],
            "low": [99.0],
            "close": [104.0],
            "volume": [1000.0],
        }, index=pd.to_datetime(["2024-01-15"]))

        fake = ModuleType("borsapy")
        class FakeTicker:
            def __init__(self, symbol):
                pass
            def history(self, period="max", interval="1d"):
                return df_data
        fake.Ticker = FakeTicker
        monkeypatch.setitem(sys.modules, "borsapy", fake)

        from research.ingest_prices import _fetch_real
        result = _fetch_real(
            "AKSEN",
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            fetcher=None,
        )
        assert len(result) == 1
        assert result[0]["close"] == 104.0

    def test_old_get_prices_api_would_fail(self, monkeypatch):
        """Negative test: if a borsapy were to expose ONLY get_prices
        (the old API), our code should not regress to using it.

        We construct a fake borsapy module that has get_prices but
        NOT Ticker. _fetch_real should fail because Ticker is gone,
        not silently work via get_prices."""
        fake = ModuleType("borsapy")
        # Only the OLD api, no Ticker class
        fake.get_prices = lambda s, from_date, to_date: []
        monkeypatch.setitem(sys.modules, "borsapy", fake)

        from research.ingest_prices import _fetch_real
        with pytest.raises(AttributeError, match="Ticker"):
            _fetch_real(
                "AKSEN",
                from_date=date(2024, 1, 1),
                to_date=date(2024, 12, 31),
                fetcher=None,
            )


# ==========================================================================
# Test injection path: must remain stable
# ==========================================================================

class TestFetcherInjection:
    """The fetcher=... kwarg path is what tests use to bypass network.
    Make sure it still works the same way."""

    def test_injected_fetcher_returns_dicts(self):
        from research.ingest_prices import _fetch_real

        def stub(sym, fd, td):
            return [
                {"trade_date": date(2024, 1, 15), "open": 100, "high": 105,
                 "low": 99, "close": 104, "volume": 1000,
                 "adjusted_close": 104},
            ]

        result = _fetch_real(
            "AKSEN",
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            fetcher=stub,
        )
        assert len(result) == 1
        assert result[0]["close"] == 104

    def test_injected_fetcher_filters_by_date_range(self):
        """Even with fetcher injection, _fetch_real should filter
        to [from_date, to_date]."""
        from research.ingest_prices import _fetch_real

        def stub(sym, fd, td):
            return [
                {"trade_date": date(2023, 12, 31), "open": 100, "close": 100,
                 "high": 100, "low": 100, "volume": 1, "adjusted_close": 100},
                {"trade_date": date(2024, 6, 15), "open": 101, "close": 102,
                 "high": 102, "low": 100, "volume": 1, "adjusted_close": 102},
                {"trade_date": date(2025, 1, 5), "open": 110, "close": 111,
                 "high": 112, "low": 109, "volume": 1, "adjusted_close": 111},
            ]

        result = _fetch_real(
            "AKSEN",
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            fetcher=stub,
        )
        # Only the 2024-06-15 row should survive the date filter
        assert len(result) == 1
        assert result[0]["close"] == 102

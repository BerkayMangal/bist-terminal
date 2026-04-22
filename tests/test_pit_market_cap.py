"""Tests for scripts/ingest_fa_for_calibration.py:_pit_market_cap.

Context (Phase 4.7 v2): the old implementation used
tk.fast_info.market_cap (TODAY's mcap) for every historical quarter,
producing PB=7994 outliers in Colab ROUND A. Fix: close_price at
filed_at × shares_outstanding.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest


_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "ingest_fa_for_calibration.py"
)
_spec = importlib.util.spec_from_file_location("fa_ingest_v2", _SCRIPT_PATH)
fa_ingest = importlib.util.module_from_spec(_spec)
sys.modules["fa_ingest_v2"] = fa_ingest
_spec.loader.exec_module(fa_ingest)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Isolated DB so we can seed prices without collision."""
    db = tmp_path / "pit_mcap.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
    import infra.storage
    infra.storage._local = threading.local()
    infra.storage.DB_PATH = str(db)
    from infra.storage import init_db
    init_db()
    yield db


class TestPitMarketCap:
    def test_shares_current_path(self, fresh_db):
        """shares_outstanding × close_price_at_filed_at = PIT mcap."""
        from infra.pit import save_price
        save_price("THYAO", date(2020, 5, 15), "test", close=150.0)

        mcap = fa_ingest._pit_market_cap(
            symbol="THYAO", filed_at=date(2020, 5, 15),
            shares_current=1_000_000_000,  # 1B shares
            paid_in_capital=None,
        )
        assert mcap == pytest.approx(150.0 * 1_000_000_000)

    def test_pit_prices_differ_across_quarters(self, fresh_db):
        """The bug we're fixing: same shares + DIFFERENT prices across
        quarters must produce DIFFERENT mcaps (not the same current mcap
        applied everywhere)."""
        from infra.pit import save_price
        # 2020 filing, low price
        save_price("THYAO", date(2020, 5, 15), "test", close=50.0)
        # 2023 filing, high price
        save_price("THYAO", date(2023, 5, 15), "test", close=250.0)

        mcap_2020 = fa_ingest._pit_market_cap(
            "THYAO", date(2020, 5, 15), 1_000_000_000, None,
        )
        mcap_2023 = fa_ingest._pit_market_cap(
            "THYAO", date(2023, 5, 15), 1_000_000_000, None,
        )
        assert mcap_2020 == pytest.approx(50.0 * 1e9)
        assert mcap_2023 == pytest.approx(250.0 * 1e9)
        # This is the key assertion — the two quarters produce
        # different mcaps, NOT the same
        assert mcap_2020 != mcap_2023
        assert mcap_2023 / mcap_2020 == pytest.approx(5.0)  # 5x price diff

    def test_paid_in_capital_fallback(self, fresh_db):
        """When shares_outstanding unavailable, fall back to paid-in
        capital / 1 TL nominal (Turkish convention)."""
        from infra.pit import save_price
        save_price("XYZ", date(2020, 1, 15), "test", close=10.0)
        mcap = fa_ingest._pit_market_cap(
            symbol="XYZ", filed_at=date(2020, 1, 15),
            shares_current=None,
            paid_in_capital=5_000_000,  # 5M TL paid-in capital = 5M shares
        )
        assert mcap == pytest.approx(10.0 * 5_000_000)

    def test_missing_price_returns_none(self, fresh_db):
        """No PIT price → None (not a crash)."""
        # No save_price calls — db is empty
        mcap = fa_ingest._pit_market_cap(
            "NOPRICE", date(2020, 1, 15), 1_000_000, None,
        )
        assert mcap is None

    def test_zero_shares_returns_none(self, fresh_db):
        """Zero shares shouldn't produce a zero mcap row (that's misleading)."""
        from infra.pit import save_price
        save_price("ZERO", date(2020, 1, 15), "test", close=10.0)
        mcap = fa_ingest._pit_market_cap(
            "ZERO", date(2020, 1, 15),
            shares_current=0, paid_in_capital=0,
        )
        assert mcap is None

    def test_filed_at_resolves_to_nearest_trading_day(self, fresh_db):
        """If filed_at is a non-trading day, get_price_at_or_before
        should find the most recent trading day before it."""
        from infra.pit import save_price
        # Friday 2020-01-10 has a price
        save_price("XYZ", date(2020, 1, 10), "test", close=100.0)
        # No price on Saturday 2020-01-11 (non-trading)
        # filed_at on Saturday should still resolve
        mcap = fa_ingest._pit_market_cap(
            "XYZ", date(2020, 1, 11),  # Saturday
            shares_current=1_000_000,
            paid_in_capital=None,
        )
        assert mcap == pytest.approx(100.0 * 1_000_000)


class TestBankSkipConstant:
    """BANK_SYMBOLS set must include all known BIST banks."""

    def test_known_banks_in_set(self):
        expected_banks = {
            "AKBNK", "GARAN", "YKBNK", "ISCTR",
            "HALKB", "VAKBN", "TSKB", "SKBNK", "ALBRK",
        }
        assert expected_banks.issubset(fa_ingest.BANK_SYMBOLS)

    def test_non_banks_not_in_set(self):
        """Sanity: THYAO, BIMAS, TUPRS etc. are NOT banks."""
        for sym in ("THYAO", "BIMAS", "ASELS", "EREGL", "TUPRS"):
            assert sym not in fa_ingest.BANK_SYMBOLS

    def test_bank_symbol_passed_to_ingest_driver_is_skipped(
        self, fresh_db, tmp_path,
    ):
        """Running ingest_symbols with a bank in the list: bank row
        should NOT appear in CSV, checkpoint should record SKIP
        reason."""
        fa_ingest._seed_synthetic_prices(
            ["THYAO", "AKBNK"], date(2020, 1, 1), date(2021, 12, 31),
        )
        out = tmp_path / "events.csv"
        cp = tmp_path / "cp.json"
        fetcher = fa_ingest.make_synthetic_fetcher()
        n_events, n_failed = fa_ingest.ingest_symbols(
            ["THYAO", "AKBNK"], date(2020, 1, 1), date(2021, 6, 30),
            fetcher, out, cp, sleep_between_symbols=0,
        )
        assert n_failed == 0
        # Read CSV and confirm AKBNK is nowhere
        import csv
        with out.open() as f:
            rows = list(csv.DictReader(f))
        symbols_in_csv = {r["symbol"] for r in rows}
        assert "AKBNK" not in symbols_in_csv
        assert "THYAO" in symbols_in_csv
        # And checkpoint should note the skip reason
        import json
        cp_data = json.loads(cp.read_text())
        assert "AKBNK" in cp_data.get("completed_symbols", [])
        assert "AKBNK" in cp_data.get("errors", {})
        assert "bank" in cp_data["errors"]["AKBNK"].lower()

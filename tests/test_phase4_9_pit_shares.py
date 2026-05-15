"""Phase 4.9 — PIT shares outstanding tests.

Verify that:
  1. _pit_shares_outstanding prefers paid_in_capital (PIT) over
     shares_current (today's snapshot) when both are available.
  2. _pit_market_cap routes through this preference correctly.
  3. Fallbacks still work when only one source is available.
  4. None / zero / negative inputs handled defensively.

The Phase 4.7 v2 fix used the OLD preference (shares_current first)
to keep the script working at all. Phase 4.9 inverts this so historic
mcap reflects historic share counts, fixing PE/PB noise on symbols
that had capital actions (rights issues, bonus shares, redenom).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest


# Load the script as a module
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "ingest_fa_for_calibration.py"
)
_spec = importlib.util.spec_from_file_location("ingest_fa", _SCRIPT_PATH)
ingest_fa = importlib.util.module_from_spec(_spec)
sys.modules["ingest_fa"] = ingest_fa
_spec.loader.exec_module(ingest_fa)


# ==========================================================================
# _pit_shares_outstanding helper
# ==========================================================================

class TestPitSharesOutstanding:
    def test_pit_paid_in_capital_preferred_over_current(self):
        """When both available, PIT (paid_in_capital) wins."""
        shares, src = ingest_fa._pit_shares_outstanding(
            symbol="AKSEN",
            period_end=date(2020, 6, 30),
            paid_in_capital=1_000_000_000,  # 1B TL nominal = 1B shares
            shares_current=2_000_000_000,    # today's count is double
        )
        assert shares == 1_000_000_000
        assert src == "pit_paid_in_capital"

    def test_falls_back_to_current_when_paid_in_capital_none(self):
        shares, src = ingest_fa._pit_shares_outstanding(
            symbol="AKSEN",
            period_end=date(2020, 6, 30),
            paid_in_capital=None,
            shares_current=500_000_000,
        )
        assert shares == 500_000_000
        assert src == "current_fast_info"

    def test_falls_back_to_current_when_paid_in_capital_zero(self):
        shares, src = ingest_fa._pit_shares_outstanding(
            symbol="AKSEN",
            period_end=date(2020, 6, 30),
            paid_in_capital=0,
            shares_current=500_000_000,
        )
        assert shares == 500_000_000
        assert src == "current_fast_info"

    def test_falls_back_to_current_when_paid_in_capital_negative(self):
        # Defensive: shouldn't happen but guard anyway
        shares, src = ingest_fa._pit_shares_outstanding(
            symbol="AKSEN",
            period_end=date(2020, 6, 30),
            paid_in_capital=-1_000_000,
            shares_current=500_000_000,
        )
        assert shares == 500_000_000
        assert src == "current_fast_info"

    def test_returns_unavailable_when_both_none(self):
        shares, src = ingest_fa._pit_shares_outstanding(
            symbol="AKSEN",
            period_end=date(2020, 6, 30),
            paid_in_capital=None,
            shares_current=None,
        )
        assert shares is None
        assert src == "unavailable"

    def test_returns_unavailable_when_both_zero(self):
        shares, src = ingest_fa._pit_shares_outstanding(
            symbol="AKSEN",
            period_end=date(2020, 6, 30),
            paid_in_capital=0,
            shares_current=0,
        )
        assert shares is None
        assert src == "unavailable"

    def test_returns_float(self):
        """Output should be float regardless of input type."""
        shares, src = ingest_fa._pit_shares_outstanding(
            symbol="AKSEN",
            period_end=date(2020, 6, 30),
            paid_in_capital=1_000_000,  # int input
            shares_current=None,
        )
        assert isinstance(shares, float)


# ==========================================================================
# _pit_market_cap routes through new preference
# ==========================================================================

class TestPitMarketCap:
    @pytest.fixture
    def mock_price(self, monkeypatch):
        """Mock get_price_at_or_before to return a fixed close price."""
        def fake_price(symbol, filed_at):
            return {"close": 5.0, "adjusted_close": 5.0}
        monkeypatch.setattr(
            "infra.pit.get_price_at_or_before",
            fake_price,
        )

    def test_uses_pit_paid_in_capital_when_present(self, mock_price):
        # paid_in_capital=1B, current=2B, price=5.0
        # PIT wins → mcap = 5.0 * 1B = 5B
        mcap = ingest_fa._pit_market_cap(
            symbol="AKSEN",
            filed_at=date(2020, 8, 15),
            shares_current=2_000_000_000,
            paid_in_capital=1_000_000_000,
            period_end=date(2020, 6, 30),
        )
        assert mcap == pytest.approx(5_000_000_000.0)

    def test_falls_back_to_shares_current(self, mock_price):
        # paid_in_capital=None, current=500M, price=5.0
        # → mcap = 5.0 * 500M = 2.5B
        mcap = ingest_fa._pit_market_cap(
            symbol="AKSEN",
            filed_at=date(2020, 8, 15),
            shares_current=500_000_000,
            paid_in_capital=None,
            period_end=date(2020, 6, 30),
        )
        assert mcap == pytest.approx(2_500_000_000.0)

    def test_returns_none_when_no_shares_data(self, mock_price):
        mcap = ingest_fa._pit_market_cap(
            symbol="AKSEN",
            filed_at=date(2020, 8, 15),
            shares_current=None,
            paid_in_capital=None,
            period_end=date(2020, 6, 30),
        )
        assert mcap is None

    def test_returns_none_when_no_price(self, monkeypatch):
        def no_price(symbol, filed_at):
            return None
        monkeypatch.setattr(
            "infra.pit.get_price_at_or_before",
            no_price,
        )
        mcap = ingest_fa._pit_market_cap(
            symbol="AKSEN",
            filed_at=date(2020, 8, 15),
            shares_current=1_000_000_000,
            paid_in_capital=1_000_000_000,
            period_end=date(2020, 6, 30),
        )
        assert mcap is None

    def test_period_end_optional(self, mock_price):
        """period_end defaults to filed_at — function should still work."""
        mcap = ingest_fa._pit_market_cap(
            symbol="AKSEN",
            filed_at=date(2020, 8, 15),
            shares_current=None,
            paid_in_capital=1_000_000_000,
        )
        assert mcap == pytest.approx(5_000_000_000.0)


# ==========================================================================
# Real-world simulation: capital action between then and now
# ==========================================================================

class TestCapitalActionScenarios:
    """Simulate the bug Phase 4.9 fixes."""

    @pytest.fixture
    def mock_price(self, monkeypatch):
        def fake_price(symbol, filed_at):
            return {"close": 10.0}
        monkeypatch.setattr(
            "infra.pit.get_price_at_or_before",
            fake_price,
        )

    def test_2018_quarter_with_2024_doubled_shares(self, mock_price):
        """Symbol had bonus shares between 2018 and now: shares doubled.
        Old behaviour (Phase 4.7 v2): used 2024 share count for 2018 mcap
        → PB looks twice as expensive than it really was.
        New behaviour (Phase 4.9): uses 2018 paid_in_capital → correct mcap.
        """
        # 2018 paid_in_capital = 100M shares
        # Today's shares_outstanding = 200M (doubled via bonus shares)
        # 2018 close = 10.0
        # True 2018 mcap = 10 * 100M = 1B
        # Old (wrong) mcap with current shares = 10 * 200M = 2B (2x overstated)
        mcap_phase49 = ingest_fa._pit_market_cap(
            symbol="AKSEN",
            filed_at=date(2018, 8, 15),
            shares_current=200_000_000,    # today
            paid_in_capital=100_000_000,    # 2018-Q2
            period_end=date(2018, 6, 30),
        )
        assert mcap_phase49 == pytest.approx(1_000_000_000.0)
        # NOT 2_000_000_000 (which is what the old code would return)

    def test_no_capital_action_phase49_matches_phase47(self, mock_price):
        """Symbol has not done any capital action: paid_in_capital ==
        shares_current. Phase 4.9 produces same result as Phase 4.7 v2."""
        mcap = ingest_fa._pit_market_cap(
            symbol="STABLE",
            filed_at=date(2020, 8, 15),
            shares_current=500_000_000,
            paid_in_capital=500_000_000,
            period_end=date(2020, 6, 30),
        )
        assert mcap == pytest.approx(5_000_000_000.0)

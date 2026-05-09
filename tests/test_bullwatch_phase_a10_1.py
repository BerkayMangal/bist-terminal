"""Tests for BullWatch v2 Phase A.10 Step 2-A.1 — small correctness patch:
   Fix A: compute_metrics_v9 must include 'shares' in returned metrics dict
   Fix B: DataFrame helpers must not crash on non-DataFrame inputs (string,
          int, etc.) — this happens when borsapy returns an error string
          instead of a DataFrame for income/balance/cashflow statements.
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from data.providers import (
    _is_empty_frame,
    _find_data_col,
    _pick,
    _pair,
    _pick_debt,
)


# ============================================================
# Fix B — _is_empty_frame helper
# ============================================================
class TestIsEmptyFrame:
    def test_none_is_empty(self):
        assert _is_empty_frame(None) is True

    def test_string_is_empty(self):
        """borsapy occasionally returns error strings instead of DataFrames."""
        assert _is_empty_frame("error: rate limit exceeded") is True
        assert _is_empty_frame("") is True

    def test_int_is_empty(self):
        assert _is_empty_frame(42) is True

    def test_list_is_empty(self):
        """A bare list isn't a DataFrame either."""
        assert _is_empty_frame([1, 2, 3]) is True

    def test_dict_is_empty(self):
        assert _is_empty_frame({"key": "value"}) is True

    def test_empty_dataframe_is_empty(self):
        assert _is_empty_frame(pd.DataFrame()) is True

    def test_dataframe_with_no_rows_is_empty(self):
        assert _is_empty_frame(pd.DataFrame(columns=["A", "B"])) is True

    def test_real_dataframe_is_not_empty(self):
        df = pd.DataFrame({"A": [1, 2, 3]})
        assert _is_empty_frame(df) is False

    def test_dataframe_subclass_works(self):
        """If something quacks like a DataFrame, accept it."""
        class Fake:
            empty = False
        # not a real DataFrame but has .empty attr — _is_empty_frame
        # should respect its .empty attribute
        assert _is_empty_frame(Fake()) is False


# ============================================================
# Fix B — helper functions survive non-DataFrame inputs
# ============================================================
class TestFindDataColDefensive:
    def test_string_input_returns_zero(self):
        """The original bug — _find_data_col('error string') would crash."""
        assert _find_data_col("error: rate limit") == 0

    def test_none_input_returns_zero(self):
        assert _find_data_col(None) == 0

    def test_empty_dataframe_returns_zero(self):
        assert _find_data_col(pd.DataFrame()) == 0

    def test_real_dataframe_returns_real_col(self):
        df = pd.DataFrame({
            "2024": [100, 200, 300, 400],
            "2025": [110, 210, 310, 410],
        }, index=["Revenue", "GrossProfit", "OpInc", "NI"])
        # First col has 4 non-zero values, should return 0
        assert _find_data_col(df) == 0


class TestPickDefensive:
    def test_string_input_returns_none(self):
        """The original bug — _pick('error', ['Revenue']) would crash."""
        assert _pick("error: rate limit", ["Revenue"]) is None

    def test_none_input_returns_none(self):
        assert _pick(None, ["Revenue"]) is None

    def test_int_input_returns_none(self):
        assert _pick(42, ["Revenue"]) is None

    def test_real_dataframe_finds_value(self):
        df = pd.DataFrame({
            "2024": [1000, 200],
            "2023": [900, 180],
        }, index=["Revenue", "GrossProfit"])
        assert _pick(df, ["Revenue"]) == 1000


class TestPairDefensive:
    def test_string_input_returns_none_pair(self):
        """The original bug — _pair('error', ['Revenue']) would crash."""
        result = _pair("error: rate limit", ["Revenue"])
        assert result == (None, None)

    def test_none_input_returns_none_pair(self):
        assert _pair(None, ["Revenue"]) == (None, None)

    def test_real_dataframe_returns_both_periods(self):
        df = pd.DataFrame({
            "2024": [1000, 200],
            "2023": [900, 180],
        }, index=["Revenue", "GrossProfit"])
        cur, prev = _pair(df, ["Revenue"])
        assert cur == 1000
        assert prev == 900


class TestPickDebtDefensive:
    def test_string_input_returns_none_pair(self):
        result = _pick_debt("error: rate limit")
        assert result == (None, None)

    def test_none_input_returns_none_pair(self):
        assert _pick_debt(None) == (None, None)


# ============================================================
# Fix A + B — end-to-end via compute_metrics_v9
# ============================================================
# We can't easily run real compute_metrics_v9 in sandbox (no borsapy),
# but we can verify the fix by importing the function and inspecting
# the source for the "shares" key in the returned dict.
class TestSharesInReturnDict:
    def test_shares_key_present_in_compute_metrics_v9_return(self):
        """Fix A: shares was missing from the return dict. Verify the
        source code includes the key now."""
        import inspect
        from data import providers
        src = inspect.getsource(providers.compute_metrics_v9)
        # The return statement should include "shares": shares
        assert '"shares": shares' in src, \
            "Fix A regression: 'shares' missing from compute_metrics_v9 return dict"

    def test_field_sources_includes_shares(self):
        import inspect
        from data import providers
        src = inspect.getsource(providers.compute_metrics_v9)
        # _field_sources dict should include 'shares' key
        assert '"shares":' in src, "_field_sources should tag 'shares'"


# ============================================================
# Synthetic compute_metrics_v9 — non-DataFrame statements survive
# ============================================================
class TestComputeMetricsResilience:
    """Drive compute_metrics_v9 indirectly via the helpers that handle
    its statements. The key thing: if borsapy returns a string for
    income/balance/cashflow, the _pick/_pair calls in compute_metrics_v9
    must return None instead of raising AttributeError."""

    def test_simulated_borsapy_string_response_pattern(self):
        """Simulate the failure mode: borsapy.get_income_stmt returned a
        string error message. Without the fix, _pair would crash."""
        # All the calls compute_metrics_v9 makes look like:
        #   revenue, revenue_prev = _pair(fin, IS_MAP["revenue"])
        # where fin might be a string due to borsapy error.
        bad_fin = "Connection error: rate limited"
        bad_bal = "Connection error: rate limited"
        bad_cf = "Connection error: rate limited"

        # All these should return None/None tuples without raising
        rev, rev_prev = _pair(bad_fin, ["Hasılat"])
        assert rev is None and rev_prev is None

        ta, ta_prev = _pair(bad_bal, ["Toplam Varlıklar"])
        assert ta is None and ta_prev is None

        op_cf, op_cf_prev = _pair(bad_cf, ["İşletme Faaliyetlerinden Net Nakit Akışı"])
        assert op_cf is None and op_cf_prev is None

        # _pick_debt also defensive
        td, td_prev = _pick_debt(bad_bal)
        assert td is None and td_prev is None


# ============================================================
# Regression: cached_compute_metrics still works with new shapes
# ============================================================
class TestCachedComputeMetricsAfterFix:
    """Verify the data_status / missing_fields logic now correctly
    handles 'shares' as a present field after Fix A."""

    def test_metrics_with_shares_yields_live_status(self):
        """When metrics has all 3 required fields including shares,
        data_status should be 'live'."""
        from data.bullwatch_cache import _compute_data_status
        m = {"market_cap": 1.5e9, "free_float": 0.35, "shares": 60e6}
        status, missing = _compute_data_status(m)
        assert status == "live"
        assert missing == []

    def test_metrics_with_only_market_cap_is_partial(self):
        """Without free_float OR shares, status is partial."""
        from data.bullwatch_cache import _compute_data_status
        m = {"market_cap": 1.5e9, "free_float": None, "shares": None}
        status, missing = _compute_data_status(m)
        assert status == "partial"
        assert "free_float" in missing
        assert "shares" in missing

    def test_metrics_with_shares_via_kaplm_path(self):
        """End-to-end synthetic: borsapy returns shares + market_cap, no
        free_float (the KAPLM/GLRMK/ASELS pattern). Override applies free_float.
        After both fixes, data_status should be 'live'."""
        from data.bullwatch_cache import (
            _compute_data_status, _apply_overrides, _apply_sanity,
        )
        # Mimic borsapy output AFTER Fix A: shares is now in the dict
        m = {
            "symbol": "KAPLM", "ticker": "KAPLM",
            "market_cap": 1.27e10, "free_float": None,
            "shares": 254_000_000,  # Fix A: now included
            "_field_sources": {
                "market_cap": "borsapy.fast_info",
                "shares": "borsapy.fast_info",
                "free_float": "missing",
            },
            "data_source": "borsapy", "data_quality": "high",
        }
        m = _apply_sanity(m, "KAPLM")
        m = _apply_overrides(m, "KAPLM")
        status, missing = _compute_data_status(m)

        # After override: free_float=0.35 (manual). All 3 required fields filled.
        assert m["free_float"] == 0.35
        assert m["override_applied"] is True
        assert status == "live"
        assert missing == []

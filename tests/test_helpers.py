# ================================================================
# BISTBULL TERMINAL — Unit Tests: Utility Functions
# Tests: safe_num, normalize_symbol, avg, score_higher, score_lower
#
# All tests are deterministic, pure-logic, no I/O.
# ================================================================

import math
import pytest

from utils.helpers import safe_num, normalize_symbol, avg, score_higher, score_lower


# ================================================================
# safe_num
# ================================================================
class TestSafeNum:
    """safe_num converts to float, returning None for invalid/NaN/Inf."""

    def test_normal_int(self):
        assert safe_num(42) == 42.0

    def test_normal_float(self):
        assert safe_num(3.14) == 3.14

    def test_zero(self):
        assert safe_num(0) == 0.0

    def test_negative(self):
        assert safe_num(-99.5) == -99.5

    def test_string_number(self):
        assert safe_num("123.45") == 123.45

    def test_none_returns_none(self):
        assert safe_num(None) is None

    def test_nan_returns_none(self):
        assert safe_num(float("nan")) is None

    def test_inf_returns_none(self):
        assert safe_num(float("inf")) is None

    def test_neg_inf_returns_none(self):
        assert safe_num(float("-inf")) is None

    def test_empty_string_returns_none(self):
        assert safe_num("") is None

    def test_non_numeric_string_returns_none(self):
        assert safe_num("abc") is None

    def test_numpy_nan(self):
        """numpy NaN should also return None."""
        import numpy as np
        assert safe_num(np.nan) is None

    def test_numpy_inf(self):
        import numpy as np
        assert safe_num(np.inf) is None


# ================================================================
# normalize_symbol
# ================================================================
class TestNormalizeSymbol:
    """normalize_symbol ensures .IS suffix for yfinance compatibility."""

    def test_bare_ticker(self):
        assert normalize_symbol("THYAO") == "THYAO.IS"

    def test_already_suffixed(self):
        assert normalize_symbol("THYAO.IS") == "THYAO.IS"

    def test_lowercase_input(self):
        result = normalize_symbol("thyao")
        assert result.endswith(".IS")
        assert "THYAO" in result.upper()

    def test_with_spaces(self):
        result = normalize_symbol("  EREGL  ")
        assert result.strip() == result  # no leading/trailing spaces
        assert result.endswith(".IS")

    def test_with_e_suffix_preserved(self):
        """normalize_symbol only adds .IS to bare tickers.
        Already-suffixed tickers (like .E) are kept as-is."""
        result = normalize_symbol("THYAO.E")
        # Current behavior: .E is not stripped — the function only appends .IS
        # to tickers that have no dot suffix at all.
        assert result == "THYAO.E"


# ================================================================
# avg
# ================================================================
class TestAvg:
    """avg computes mean of non-None values, returns None if all None."""

    def test_normal_list(self):
        assert avg([60, 70, 80]) == 70.0

    def test_with_nones_filtered(self):
        result = avg([60, None, 80])
        assert result == 70.0

    def test_all_nones_returns_none(self):
        assert avg([None, None, None]) is None

    def test_empty_list_returns_none(self):
        assert avg([]) is None

    def test_single_value(self):
        assert avg([42.5]) == 42.5

    def test_single_none(self):
        assert avg([None]) is None

    def test_mixed_zeros_and_values(self):
        """Zero is a valid value, not None."""
        assert avg([0, 100]) == 50.0


# ================================================================
# score_higher (higher value = higher score)
# ================================================================
class TestScoreHigher:
    """score_higher maps a metric value to 0-100 via 4 thresholds.
    Below t1 → ~0-25, above t4 → ~90-100."""

    def test_none_returns_none(self):
        assert score_higher(None, 0.05, 0.10, 0.15, 0.20) is None

    def test_well_above_top(self):
        result = score_higher(0.50, 0.05, 0.10, 0.15, 0.20)
        assert result >= 90

    def test_well_below_bottom(self):
        result = score_higher(-0.10, 0.05, 0.10, 0.15, 0.20)
        assert result <= 15

    def test_at_midpoint(self):
        """Value between t2 and t3 should produce a mid-range score."""
        result = score_higher(0.12, 0.05, 0.10, 0.15, 0.20)
        assert 40 <= result <= 70

    def test_monotonic(self):
        """Higher input → higher or equal score."""
        scores = [score_higher(v, 5, 10, 15, 20) for v in [1, 5, 10, 15, 20, 30]]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1]


# ================================================================
# score_lower (lower value = higher score) — e.g. P/E
# ================================================================
class TestScoreLower:
    """score_lower is the inverse: lower input = higher score.
    Used for metrics like P/E, EV/EBITDA where cheaper is better."""

    def test_none_returns_none(self):
        assert score_lower(None, 5, 10, 15, 20) is None

    def test_very_low_value_high_score(self):
        result = score_lower(2, 5, 10, 15, 20)
        assert result >= 85

    def test_very_high_value_low_score(self):
        result = score_lower(50, 5, 10, 15, 20)
        assert result <= 15

    def test_monotonic_inverse(self):
        """Higher input → lower or equal score."""
        scores = [score_lower(v, 5, 10, 15, 20) for v in [2, 5, 10, 15, 20, 40]]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]

    def test_symmetry_with_score_higher(self):
        """At equivalent positions, score_lower and score_higher should
        produce complementary results (not exact, but directionally opposite)."""
        high = score_higher(3, 5, 10, 15, 20)   # below bottom → low score
        low = score_lower(3, 5, 10, 15, 20)     # below bottom → high score
        assert low > high

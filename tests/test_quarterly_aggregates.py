# ================================================================
# tests/test_quarterly_aggregates.py
#
# Plan B v1 — unit tests for data.providers._compute_quarterly_aggregates.
# Verifies YTD/YoY-Q semantics on cumulative-YTD quarterly data
# (borsapy's format).
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest

from data.providers import _compute_quarterly_aggregates


def _make_q_df(revenue=None, net_income=None, op_income=None,
               cols=None):
    """Build a fake quarterly DataFrame mirroring borsapy's shape.

    Columns are newest-first. Each row is one metric. Values must be a
    length-8 list (Q_now, Q_now-1, ..., Q_now-7). Pass None to skip a
    metric.
    """
    cols = cols or [
        "2025Q4", "2025Q3", "2025Q2", "2025Q1",
        "2024Q4", "2024Q3", "2024Q2", "2024Q1",
    ]
    rows = {}
    if revenue is not None:
        rows["Satış Gelirleri"] = revenue
    if net_income is not None:
        rows["DÖNEM KARI (ZARARI)"] = net_income
    if op_income is not None:
        rows["FAALİYET KARI (ZARARI)"] = op_income
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, index=cols).T
    return df


# ── 1. Cumulative-YTD semantics on Q4 (full year reported) ──────────


class TestFullYearYoYQ:
    def test_revenue_growth_matches_annual_when_latest_is_q4(self):
        """When the latest quarter is Q4, the cumulative figure IS the
        annual figure, so growth_yoy_q must equal annual growth."""
        df = _make_q_df(revenue=[
            120, 90, 60, 30,    # 2025 cumulative (Q4 == annual)
            100, 75, 50, 25,    # 2024 cumulative (Q4 == annual)
        ])
        r = _compute_quarterly_aggregates(df)
        assert r["latest_quarter"] == "2025Q4"
        assert r["revenue_ytd"] == 120
        assert r["revenue_ytd_prev"] == 100
        assert r["revenue_growth_yoy_q"] == pytest.approx(0.20)

    def test_all_three_metrics_populated(self):
        df = _make_q_df(
            revenue=[120, 90, 60, 30, 100, 75, 50, 25],
            net_income=[20, 15, 10, 5, 10, 7, 5, 2],
            op_income=[40, 30, 20, 10, 30, 22, 15, 7],
        )
        r = _compute_quarterly_aggregates(df)
        assert r["revenue_growth_yoy_q"] == pytest.approx(0.20)
        assert r["net_income_growth_yoy_q"] == pytest.approx(1.0)  # 20 vs 10
        assert r["operating_income_growth_yoy_q"] == pytest.approx(40/30 - 1)


# ── 2. Mid-year scenario — Q3 cumulative ───────────────────────────


class TestMidYearYoYQ:
    def test_q3_cumulative_yoy_growth(self):
        """When only first 3 quarters of 2025 are reported, growth_yoy_q
        should compare 2025 YTD-through-Q3 vs 2024 YTD-through-Q3 — a
        signal you couldn't get from annual fields (those still reflect
        2024 vs 2023)."""
        # No 2025Q4 yet, so column 0 carries 2025Q3 cumulative
        cols = [
            "2025Q3", "2025Q2", "2025Q1", "_unused",  # padded
            "2024Q3", "2024Q2", "2024Q1", "_unused",
        ]
        # Note: in real borsapy, when latest is mid-year, 2025Q4 column
        # is simply missing entirely (last_n=8 returns 8 valid quarters,
        # newest first). We model that by treating idx 0 as 2025Q3.
        df = _make_q_df(
            revenue=[90, 60, 30, 0, 75, 50, 25, 0],  # 2025Q3 cum=90 vs 2024Q3 cum=75
            cols=cols,
        )
        r = _compute_quarterly_aggregates(df)
        assert r["latest_quarter"] == "2025Q3"
        assert r["revenue_ytd"] == 90
        assert r["revenue_ytd_prev"] == 75
        assert r["revenue_growth_yoy_q"] == pytest.approx(0.20)


# ── 3. Edge cases ──────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_df_returns_unavailable(self):
        r = _compute_quarterly_aggregates(pd.DataFrame())
        assert r["quarterly_data_available"] is False
        assert r["latest_quarter"] is None
        assert "revenue_growth_yoy_q" not in r

    def test_none_input(self):
        r = _compute_quarterly_aggregates(None)
        assert r["quarterly_data_available"] is False

    def test_zero_prev_year_returns_none_growth(self):
        df = _make_q_df(revenue=[100, 80, 60, 40, 0, 0, 0, 0])
        r = _compute_quarterly_aggregates(df)
        assert r["revenue_ytd"] == 100
        assert r["revenue_ytd_prev"] == 0
        assert r["revenue_growth_yoy_q"] is None  # division by zero guard

    def test_negative_prev_year_uses_absolute_denominator(self):
        # Growth meaningful even for sign flips: use |prev| denominator
        df = _make_q_df(net_income=[10, 8, 6, 4, -5, -4, -3, -2])
        r = _compute_quarterly_aggregates(df)
        # (10 - (-5)) / |-5| = 15/5 = 3.0
        assert r["net_income_growth_yoy_q"] == pytest.approx(3.0)

    def test_partial_metric_coverage(self):
        """Some metrics may be missing — function returns what it can."""
        df = _make_q_df(revenue=[100, 80, 60, 40, 90, 70, 50, 30])
        r = _compute_quarterly_aggregates(df)
        assert r["revenue_growth_yoy_q"] is not None
        # net_income row absent — should be None or missing key
        assert r.get("net_income_growth_yoy_q") is None

    def test_no_row_match_demotes_availability(self):
        """If we got columns but couldn't parse any known row (e.g.
        bank-format rows when caller expects non-bank schema), the
        availability flag should be False so callers fall back."""
        # Build a DF with cols but row names that don't match IS_MAP
        cols = ["2025Q4", "2025Q3", "2025Q2", "2025Q1",
                "2024Q4", "2024Q3", "2024Q2", "2024Q1"]
        df = pd.DataFrame(
            {"Foo Bar Baz Unknown Row": [1, 2, 3, 4, 5, 6, 7, 8]},
            index=cols,
        ).T
        r = _compute_quarterly_aggregates(df)
        assert r["quarterly_data_available"] is False

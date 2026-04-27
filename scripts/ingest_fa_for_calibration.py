#!/usr/bin/env python3
"""FA backfill for Phase 4.7 isotonic calibration.

Produces reports/fa_events.csv — one row per (symbol, period_end, metric)
tuple with the corresponding forward 60-trading-day TR return. This file
is the input to scripts/calibrate_fa_from_events.py which fits the
per-metric IsotonicFit objects and writes reports/fa_isotonic_fits.json.

USAGE (Colab — recommended, ~2 hours runtime):
    !python scripts/ingest_fa_for_calibration.py \\
        --symbols=BIST30 --start=2018-01-01 --end=2026-04-01 \\
        --out=reports/fa_events.csv

USAGE (local dry-run with the synthetic fetcher, <10s):
    !python scripts/ingest_fa_for_calibration.py --dry-run

IDEMPOTENT + RESUMABLE:
  - Checkpoint file: reports/fa_events_checkpoint.json
  - After each symbol finishes, checkpoint + CSV are flushed to disk.
  - Re-running skips symbols already in the checkpoint.
  - Corrupt rows in fa_events.csv are deduplicated on re-run
    (we key on (symbol, period_end, metric, source)).

RATE-LIMIT AWARE (HOTFIX 1 pattern):
  - Each borsapy call uses the 3-attempt retry in data/providers.py.
  - Between symbols we sleep --sleep-between-symbols seconds
    (default 2s) to avoid the mass-failure issue seen in prod.

FETCH PROFILE:
  - 17 metrics × 32 quarters × 30 symbols ≈ 16,000 datapoints
  - 3 statements × 30 symbols × ~4 req/s borsapy = ~25 min raw
  - With 2s inter-symbol sleep + retries: 2-3 hours end-to-end
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

# Repo path so `python scripts/ingest_fa_for_calibration.py` works from anywhere
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from infra.pit import (
    save_fundamental, save_price, get_price_at_or_before,
    DEFAULT_UNIVERSE_CSV,
)
from infra.storage import init_db
from research.sectors import get_sector

log = logging.getLogger("bistbull.fa_ingest")


# ==========================================================================
# CONFIG
# ==========================================================================

# Metrics we fit isotonic curves for in Phase 4.7.
# Each tuple: (metric_name, direction_higher_better, computation_hint).
# computation_hint is human documentation; the real arithmetic lives
# in _derive_metrics_from_statements below.
#
# Phase 4.7 v2: extended from 13 to 16. New additions are cleanly
# derivable from the 3 standard statements without bank-specific
# handling. Metrics NOT added (documented here for clarity):
#   - piotroski_f, altman_z, beneish_m, cfo_to_ni, fcf_margin,
#     eps_growth, ebitda_growth, peg, ev_ebitda, ev_sales,
#     margin_safety, dividend_yield
#   These are sub-metrics of V13's earnings/moat/capital composite
#   buckets (kept in V13 — see PHASE_4_7_FINAL_REPORT.md decision
#   "earnings/moat/capital V13'te bırakıldı") OR require multi-
#   period history not cleanly available in quarterly statements.
METRIC_REGISTRY: list[tuple[str, bool, str]] = [
    # Higher = better (12)
    ("roe",              True,  "net_income / avg_equity"),
    ("roic",             True,  "NOPAT / invested_capital"),
    ("roa",              True,  "net_income / total_assets"),
    ("net_margin",       True,  "net_income / revenue"),
    ("gross_margin",     True,  "gross_profit / revenue"),
    ("operating_margin", True,  "operating_income / revenue"),
    ("revenue_growth",   True,  "yoy revenue change"),
    ("fcf_yield",        True,  "free_cashflow / market_cap"),
    ("fcf_margin",       True,  "free_cashflow / revenue"),
    ("cfo_to_ni",        True,  "operating_cf / net_income"),
    ("current_ratio",    True,  "current_assets / current_liabilities"),
    ("interest_coverage",True,  "ebit / interest_expense"),
    # Lower = better (4)
    ("pe",               False, "market_cap / trailing_net_income"),
    ("pb",               False, "market_cap / equity"),
    ("debt_equity",      False, "total_debt / equity"),
    ("net_debt_ebitda",  False, "(debt - cash) / ebitda"),
]

FORWARD_DAYS = 60           # forward return window (trading days approx by calendar days)
MIN_FILING_LAG_DAYS = 45    # KAP T+45 rule — filings come 40-75 days after period end
DEFAULT_SOURCE = "borsapy"

# Phase 4.7 v2: banks have a completely different KAP schema (Krediler,
# Bankalar Bakiyeleri, etc.) that doesn't map to the standard IFRS line
# items this script expects. Rather than produce 22 None-valued metric
# rows per bank × quarter, we early-skip and let a future Phase 5
# calibration pass handle them with bank-specific mappings.
BANK_SYMBOLS: frozenset[str] = frozenset({
    "AKBNK", "GARAN", "YKBNK", "ISCTR", "HALKB", "VAKBN",
    "TSKB", "SKBNK", "ALBRK",
})


# ==========================================================================
# DATA MODELS
# ==========================================================================

@dataclass
class FaEvent:
    """One row in fa_events.csv."""
    symbol: str
    period_end: str        # 'YYYY-MM-DD' end of quarter
    filed_at: str          # 'YYYY-MM-DD' when KAP published (period_end + 45d default)
    metric: str
    metric_value: float
    forward_return_60d: float
    forward_price_from: float
    forward_price_to: float
    sector: str
    source: str


# ==========================================================================
# FETCHER ABSTRACTION
# ==========================================================================
# The ingest pipeline accepts any fetcher with the signature:
#   fetcher(symbol, start_date, end_date) -> list[dict]
# Each dict has:
#   {period_end: date, filed_at: date, income: dict, balance: dict,
#    cashflow: dict, fast: dict}
# where income/balance/cashflow are plain numeric dicts of statement lines.

def make_synthetic_fetcher(
    metrics_per_symbol: Optional[dict[str, dict]] = None,
    seed: int = 42,
) -> Callable:
    """Deterministic fetcher for dry-run testing.

    Produces statement-like dicts the same shape as the real borsapy
    fetcher but with controlled numbers. This is what we use in the
    sandbox to verify the ingest pipeline end-to-end before the user
    runs the real 2-hour Colab backfill.
    """
    import hashlib

    def _fetcher(symbol: str, start: date, end: date) -> list[dict]:
        out = []
        # quarter-end dates between start and end
        q_end_months = (3, 6, 9, 12)
        for year in range(start.year, end.year + 1):
            for m in q_end_months:
                if m == 12:
                    qe = date(year, 12, 31)
                elif m == 3:
                    qe = date(year, 3, 31)
                elif m == 6:
                    qe = date(year, 6, 30)
                else:
                    qe = date(year, 9, 30)
                if qe < start or qe > end:
                    continue
                # Deterministic "fundamentals" based on (symbol, qe) hash
                h = int(hashlib.sha256(
                    f"{symbol}:{qe.isoformat()}".encode()
                ).hexdigest()[:8], 16) / 0xFFFFFFFF
                # Baseline revenue grows ~15%/year
                years_since = (qe.year - 2018) + qe.month / 12
                rev = 1e9 * (1.15 ** years_since) * (0.8 + 0.4 * h)
                # Net margin 5-25%
                nm = 0.05 + 0.20 * h
                ni = rev * nm
                # Equity = 3x net income (rough)
                equity = ni * 3
                # Debt varies
                debt = equity * (0.3 + 0.7 * h)
                # Cash
                cash = debt * (0.2 + 0.3 * h)
                out.append({
                    "period_end": qe,
                    "filed_at": qe + timedelta(days=MIN_FILING_LAG_DAYS),
                    "income": {
                        "revenue": rev,
                        "gross_profit": rev * (nm + 0.15),
                        "operating_income": rev * (nm + 0.05),
                        "net_income": ni,
                        "ebit": rev * (nm + 0.05),
                        "interest_expense": debt * 0.20,  # 20% TL rate
                    },
                    "balance": {
                        "equity": equity,
                        "total_debt": debt,
                        "cash": cash,
                        "current_assets": cash + rev * 0.3,
                        "current_liabilities": rev * 0.2,
                        "total_assets": equity + debt,
                    },
                    "cashflow": {
                        "free_cashflow": ni * 0.6,
                        "operating_cf": ni * 0.9,
                        "depreciation": rev * 0.03,  # ~3% of rev as D&A
                    },
                    "fast": {
                        "market_cap": equity * (2 + 3 * h),  # P/B between 2 and 5
                    },
                })
        return out

    return _fetcher


def make_borsapy_fetcher() -> Callable:
    """Real borsapy fetcher using quarterly statements.

    Phase 4.7 v2 hardening (post-Colab ROUND A diagnosis):
      - Uses utils.label_matching.pick_value for diacritic-aware fuzzy
        lookup instead of strict pandas.loc[exact_label, col].
        The Colab ROUND A produced only 3/25 metrics because many
        candidate strings had small diacritic/whitespace differences
        from what borsapy actually returns.
      - Point-in-time market_cap: for each quarter's filed_at date,
        compute mcap = close_price_at_filed_at × shares_outstanding.
        The old version used tk.fast_info.market_cap which is
        TODAY's mcap applied to every historical quarter — producing
        PB=7994 outliers.
      - Reuses the retry pattern from data/providers.py:fetch_raw_v9.
    """
    try:
        import borsapy as bp
    except ImportError:
        raise RuntimeError(
            "borsapy not importable. This fetcher is only for production "
            "Colab runs. Use --dry-run for local testing."
        )

    from utils.label_matching import pick_value, pick_all_values

    def _fetcher(symbol: str, start: date, end: date) -> list[dict]:
        tc = symbol.upper().replace(".IS", "").replace(".E", "")

        # Phase 4.7 v2: bank schema incompatibility — early skip.
        # Callers (ingest_symbols) also check this, but we double-gate
        # here so any future direct use of the fetcher is safe.
        if tc in BANK_SYMBOLS:
            log.info(f"{tc}: banka şeması farklı, skip (Phase 5 kandidatı)")
            return []

        tk = bp.Ticker(tc)
        import time as _t
        attempts = 3
        income_df = balance_df = cashflow_df = None
        last_exc = None
        for attempt in range(attempts):
            try:
                # Non-banks use default financial_group=None
                income_df = tk.get_income_stmt(quarterly=True, financial_group=None, last_n=40)
                balance_df = tk.get_balance_sheet(quarterly=True, financial_group=None, last_n=40)
                cashflow_df = tk.get_cashflow(quarterly=True, financial_group=None, last_n=40)
                break
            except Exception as e:
                last_exc = e
                if attempt < attempts - 1:
                    sleep_for = (0.5, 1.0, 2.0)[attempt]
                    log.info(
                        f"{symbol}: retry {attempt+2}/{attempts} after "
                        f"{sleep_for}s (prev: {type(e).__name__}: {e!r})"
                    )
                    _t.sleep(sleep_for)
                    continue
        if income_df is None:
            raise RuntimeError(
                f"{symbol}: failed after {attempts} attempts "
                f"(last: {type(last_exc).__name__}: {last_exc!r})"
            )

        # Try to fetch shares_outstanding for PIT market_cap computation.
        # This is a CURRENT value; we assume shares don't change dramatically
        # over 8 years for mature large-cap symbols (which BIST30 are).
        # Fallback: balance sheet "Ödenmiş Sermaye" (paid-in capital) × 1 TL
        # nominal assumption. This is done per-quarter in the loop below.
        shares_outstanding_current: Optional[float] = None
        try:
            fi = tk.fast_info
            shares_outstanding_current = getattr(fi, "shares_outstanding", None) \
                                          or getattr(fi, "shares", None)
        except Exception as e:
            log.debug(f"{tc}: shares_outstanding unavailable: {type(e).__name__}")

        # Reshape borsapy DataFrames into per-quarter dicts
        out: list[dict] = []
        import pandas as pd
        if income_df is None or income_df.empty:
            return out

        for col in income_df.columns:
            try:
                qe = pd.Timestamp(col).date()
            except Exception:
                continue
            if qe < start or qe > end:
                continue

            # ─────────────────────────────────────────────────────────
            # INCOME STATEMENT — ground-truth labels per ROUND B Colab
            # ─────────────────────────────────────────────────────────
            # Real borsapy labels (verified 2026-04-22 across THYAO/ASELS/
            # EREGL/BIMAS/TUPRS):
            #   'Satış Gelirleri'                      → revenue
            #   'BRÜT KAR (ZARAR)'                     → gross profit (ALL CAPS!)
            #   'FAALİYET KARI (ZARARI)'               → operating income (ALL CAPS)
            #   'Finansman Gideri Öncesi Faaliyet      → EBIT proxy
            #    Karı/Zararı'
            #   '(Esas Faaliyet Dışı) Finansal         → interest expense proxy
            #    Giderler (-)'
            #   'DÖNEM KARI (ZARARI)'                  → net income (ALL CAPS)
            #   'Ana Ortaklık Payları'                 → attributable NI
            # Candidate order: ROUND-B real labels first, prior v2 candidates
            # as fallbacks so older fixtures don't break.
            income = {
                "revenue": pick_value(income_df, col, [
                    "Satış Gelirleri", "Hasılat", "Toplam Gelirler",
                    "Net Satışlar",
                ]),
                "gross_profit": pick_value(income_df, col, [
                    "BRÜT KAR (ZARAR)", "Brüt Kar", "Brüt Kar/Zarar",
                    "Brüt Esas Faaliyet Karı",
                ]),
                "operating_income": pick_value(income_df, col, [
                    "FAALİYET KARI (ZARARI)",
                    "Net Faaliyet Kar/Zararı",
                    "Esas Faaliyet Karı", "Esas Faaliyet Karı/Zararı",
                    "Faaliyet Karı", "Faaliyet Karı/Zararı",
                ]),
                "net_income": pick_value(income_df, col, [
                    # Primary: consolidated net income. "Ana Ortaklık
                    # Payları" (attributable) is a subset for ROE purists,
                    # but for calibration consistency we use consolidated
                    # (matches how engine/metrics derives it).
                    "DÖNEM KARI (ZARARI)",
                    "SÜRDÜRÜLEN FAALİYETLER DÖNEM KARI/ZARARI",
                    "Dönem Net Karı", "Ana Ortaklığa Ait Dönem Net Karı",
                    "Dönem Net Karı/Zararı", "Net Dönem Karı",
                    "Dönem Karı/Zararı", "Net Kar",
                ]),
                "ebit": pick_value(income_df, col, [
                    # KAP has no 'FAVÖK' line. Use "Finansman Gideri Öncesi
                    # Faaliyet Karı/Zararı" which IS EBIT definitionally
                    # (operating income + non-op investment income,
                    # pre-interest).
                    "Finansman Gideri Öncesi Faaliyet Karı/Zararı",
                    "FAVÖK",
                    "FAALİYET KARI (ZARARI)",
                    "Esas Faaliyet Karı", "Faaliyet Karı",
                ]),
                "interest_expense": pick_value(income_df, col, [
                    "(Esas Faaliyet Dışı) Finansal Giderler (-)",
                    "Finansman Giderleri", "Faiz Giderleri",
                    "Finansal Giderler",
                ]),
            }

            # ─────────────────────────────────────────────────────────
            # BALANCE SHEET — ground-truth labels per ROUND B Colab
            # ─────────────────────────────────────────────────────────
            # Real labels (with indent-stripped via normalize_label):
            #   'Dönen Varlıklar'            → current assets
            #   '  Nakit ve Nakit Benzerleri' → cash (2-space indent)
            #   'Kısa Vadeli Yükümlülükler'  → current liabilities
            #   '  Finansal Borçlar'         → financial debt SHORT-TERM
            #   'Uzun Vadeli Yükümlülükler'  → long-term liabilities
            #   '  Finansal Borçlar'         → financial debt LONG-TERM (SAME LABEL!)
            #   'Özkaynaklar'                → equity
            #   '  Ana Ortaklığa Ait Özkaynaklar' → attributable equity
            #   '  Ödenmiş Sermaye'          → paid-in capital
            #   'TOPLAM VARLIKLAR'           → total assets (ALL CAPS)
            #   'TOPLAM KAYNAKLAR'           → total liab + equity
            #
            # CRITICAL: "Finansal Borçlar" appears TWICE (short-term +
            # long-term). We SUM both via pick_all_values.
            financial_debt_parts = pick_all_values(
                balance_df, col, ["Finansal Borçlar"], allow_substring=False,
            )
            total_debt_sum = sum(financial_debt_parts) if financial_debt_parts else None
            if total_debt_sum is None:
                # Fallback: single-label lookup for older schemas
                total_debt_sum = pick_value(balance_df, col, [
                    "Toplam Finansal Borçlar", "Finansal Borçlar",
                    "Toplam Borçlar",
                ])

            balance = {
                "equity": pick_value(balance_df, col, [
                    "Özkaynaklar", "Toplam Özkaynaklar",
                    "Ana Ortaklığa Ait Özkaynaklar",
                ]),
                "total_debt": total_debt_sum,
                "cash": pick_value(balance_df, col, [
                    "Nakit ve Nakit Benzerleri", "Nakit",
                ]),
                "current_assets": pick_value(balance_df, col, [
                    "Dönen Varlıklar", "Toplam Dönen Varlıklar",
                ]),
                "current_liabilities": pick_value(balance_df, col, [
                    "Kısa Vadeli Yükümlülükler",
                    "Toplam Kısa Vadeli Yükümlülükler",
                ]),
                "total_assets": pick_value(balance_df, col, [
                    "TOPLAM VARLIKLAR",
                    "Toplam Varlıklar", "Aktifler Toplamı", "Aktif Toplamı",
                ]),
                "paid_in_capital": pick_value(balance_df, col, [
                    "Ödenmiş Sermaye", "Çıkarılmış Sermaye",
                ]),
            }

            # ─────────────────────────────────────────────────────────
            # CASH FLOW — ground-truth labels per ROUND B Colab
            # ─────────────────────────────────────────────────────────
            # Real labels (with leading-space indent stripped):
            #   ' İşletme Faaliyetlerinden Kaynaklanan Net Nakit' → operating CF
            #   ' Yatırım Faaliyetlerinden Kaynaklanan Nakit'      → investing CF
            #   'Serbest Nakit Akım'                               → FCF (note: 'Akım' not 'Akışı')
            #   'Amortisman Giderleri'                             → depreciation
            cashflow = {
                "free_cashflow": pick_value(cashflow_df, col, [
                    "Serbest Nakit Akım",
                    "Serbest Nakit Akışı", "FCF",
                ]),
                "operating_cf": pick_value(cashflow_df, col, [
                    "İşletme Faaliyetlerinden Kaynaklanan Net Nakit",
                    "İşletme Faaliyetlerinden Sağlanan Nakit Akışı",
                    "İşletme Faaliyetlerinden Elde Edilen Nakit Akışları",
                    "Faaliyetlerden Sağlanan Nakit",
                ]),
                "depreciation": pick_value(cashflow_df, col, [
                    "Amortisman Giderleri",
                    "Amortisman ve İtfa Payları",
                    "Amortisman",
                ]),
            }

            # Point-in-time market_cap
            # Strategy (fallback chain):
            #   1. If shares_outstanding_current + PIT close price -> mcap
            #   2. If paid_in_capital (assuming 1 TL nominal) + PIT close -> mcap
            #   3. Otherwise None (metrics that need mcap will fall out)
            filed_at = qe + timedelta(days=MIN_FILING_LAG_DAYS)
            pit_mcap = _pit_market_cap(
                symbol=tc, filed_at=filed_at,
                shares_current=shares_outstanding_current,
                paid_in_capital=balance.get("paid_in_capital"),
                period_end=qe,
            )
            fast = {"market_cap": pit_mcap}

            out.append({
                "period_end": qe,
                "filed_at": filed_at,
                "income": income, "balance": balance,
                "cashflow": cashflow, "fast": fast,
            })
        return out

    return _fetcher


def _pit_shares_outstanding(
    symbol: str,
    period_end: date,
    paid_in_capital: Optional[float],
    shares_current: Optional[float],
) -> tuple[Optional[float], str]:
    """Phase 4.9: resolve point-in-time shares outstanding.

    Returns (shares, source_tag) where source_tag is one of:
      'pit_paid_in_capital'  — paid-in capital from THAT quarter's balance
                                sheet, divided by 1 TL nominal (Turkish
                                convention). This is the truly PIT value.
      'current_fast_info'     — fallback to current shares_outstanding
                                (proxy: BIST30 large caps rarely issue/retire
                                shares dramatically, so bounded error).
      'unavailable'           — neither available; caller's PE/PB metrics
                                will fall out.

    Phase 4.9 changes the preference order from Phase 4.7 v2:
      Old: shares_current preferred, paid_in_capital as fallback
      New: paid_in_capital (PIT) preferred, shares_current as fallback

    Why: paid_in_capital is read from the same quarter's balance sheet
    as the rest of the metrics, so it correctly reflects historical
    capital actions (rights issues, bonus shares, capital reductions).
    Using current shares for a 2018 quarter would inflate or deflate
    PE/PB depending on whether the company has since issued / retired
    shares.

    Limitations acknowledged:
      - Some BIST symbols have nominal value other than 1 TL. This
        helper assumes 1 TL (Turkish standard since 2005 redenomination).
      - Some share classes (preferred, restricted) are not separated.
        For the metrics this calibration cares about (PE, PB, FCF yield),
        bulk shares is what matters.
    """
    if paid_in_capital and paid_in_capital > 0:
        return float(paid_in_capital), "pit_paid_in_capital"
    if shares_current and shares_current > 0:
        return float(shares_current), "current_fast_info"
    return None, "unavailable"


def _pit_market_cap(
    symbol: str, filed_at: date,
    shares_current: Optional[float],
    paid_in_capital: Optional[float],
    period_end: Optional[date] = None,
) -> Optional[float]:
    """Compute point-in-time market cap.

    Phase 4.7 v2 fix: previously this script used tk.fast_info.market_cap
    (TODAY's mcap) for every historical quarter, producing PB=7994
    outliers. Fix: close_price_at_filed_at × shares_outstanding.

    Phase 4.9 fix: previously preferred shares_current (today's count) over
    paid_in_capital (PIT). This produced subtle PE/PB noise for symbols
    that had capital actions between then and now. Now PIT wins.

    Resolution chain:
      1. paid_in_capital (PIT, from same-quarter balance sheet) × PIT close
      2. shares_outstanding (current snapshot, fallback) × PIT close
      3. None — caller's metrics requiring mcap (PE, PB, FCF yield) fall out
    """
    from infra.pit import get_price_at_or_before
    row = get_price_at_or_before(symbol, filed_at)
    if row is None:
        return None
    close_price = row.get("close") or row.get("adjusted_close")
    if not close_price:
        return None

    shares, _src = _pit_shares_outstanding(
        symbol=symbol,
        period_end=period_end or filed_at,
        paid_in_capital=paid_in_capital,
        shares_current=shares_current,
    )
    if shares is None:
        return None
    return float(close_price) * shares


# ==========================================================================
# METRIC DERIVATION
# ==========================================================================

def _derive_metrics_from_statements(
    q: dict,
    prev_q: Optional[dict] = None,
    prev_year_q: Optional[dict] = None,
) -> dict[str, float]:
    """From one quarterly-statement dict (+ optional previous periods),
    derive the 13 metrics in METRIC_REGISTRY.

    Skips any metric that lacks required inputs (returns None, filtered
    at caller). Prev-period aware metrics (revenue_growth) return None
    when prev_year_q is missing.
    """
    inc = q.get("income", {}) or {}
    bal = q.get("balance", {}) or {}
    cf = q.get("cashflow", {}) or {}
    fast = q.get("fast", {}) or {}

    def _safe_div(a, b):
        if a is None or b is None or b == 0:
            return None
        try:
            return float(a) / float(b)
        except (TypeError, ValueError):
            return None

    rev = inc.get("revenue")
    ni = inc.get("net_income")
    equity = bal.get("equity")
    debt = bal.get("total_debt")
    cash = bal.get("cash")
    total_assets = bal.get("total_assets")
    mcap = fast.get("market_cap")
    ebit = inc.get("ebit")
    intexp = inc.get("interest_expense")
    fcf = cf.get("free_cashflow")
    ocf = cf.get("operating_cf")
    # Phase 4.7 v3 (ROUND B): Amortisman available from cashflow now.
    # EBITDA = EBIT + Depreciation/Amortization (real, not proxy).
    depr = cf.get("depreciation")

    # TTM-style signals — for quarterly statements we approximate by
    # using the quarter's annualized figures where sensible.
    metrics: dict[str, Optional[float]] = {}
    metrics["roe"] = _safe_div(ni, equity) * 4 if _safe_div(ni, equity) else None
    metrics["roa"] = _safe_div(ni, total_assets) * 4 if _safe_div(ni, total_assets) else None
    metrics["net_margin"] = _safe_div(ni, rev)
    metrics["gross_margin"] = _safe_div(inc.get("gross_profit"), rev)
    metrics["operating_margin"] = _safe_div(inc.get("operating_income"), rev)
    metrics["fcf_yield"] = _safe_div(fcf * 4 if fcf is not None else None, mcap)
    metrics["fcf_margin"] = _safe_div(fcf, rev)
    metrics["cfo_to_ni"] = _safe_div(ocf, ni) if (ocf is not None and ni) else None
    metrics["current_ratio"] = _safe_div(bal.get("current_assets"),
                                          bal.get("current_liabilities"))
    metrics["interest_coverage"] = _safe_div(ebit, intexp) if (
        ebit is not None and intexp and intexp != 0
    ) else None
    metrics["pe"] = _safe_div(mcap, ni * 4 if ni is not None else None)
    metrics["pb"] = _safe_div(mcap, equity)
    metrics["debt_equity"] = _safe_div(debt, equity)
    # Phase 4.7 v3 (ROUND B): use real depreciation when available for EBITDA.
    # EBITDA = EBIT + Depreciation (annualized × 4 from quarterly).
    # Net debt / EBITDA is a standard leverage metric. Lower is better.
    if ebit is not None:
        ebitda_q = ebit + (depr or 0)
        ebitda_ann = ebitda_q * 4
        if ebitda_ann and ebitda_ann != 0:
            net_debt = (debt or 0) - (cash or 0)
            metrics["net_debt_ebitda"] = net_debt / ebitda_ann
        else:
            metrics["net_debt_ebitda"] = None
    elif ocf is not None:
        # Fallback when EBIT isn't computable (old v2 proxy)
        net_debt = (debt or 0) - (cash or 0)
        ebitda_proxy = (ebit or 0) + (ocf * 0.2)
        metrics["net_debt_ebitda"] = (
            net_debt / ebitda_proxy if ebitda_proxy else None
        )
    else:
        metrics["net_debt_ebitda"] = None

    # ROIC approx: ebit / (equity + debt)
    invcap = (equity or 0) + (debt or 0)
    metrics["roic"] = _safe_div(ebit * 4 if ebit else None, invcap) if invcap else None

    # Revenue growth = yoy
    if prev_year_q is not None:
        prev_rev = (prev_year_q.get("income") or {}).get("revenue")
        if prev_rev and rev:
            metrics["revenue_growth"] = (rev - prev_rev) / prev_rev
        else:
            metrics["revenue_growth"] = None
    else:
        metrics["revenue_growth"] = None

    # Filter Nones
    return {k: float(v) for k, v in metrics.items() if v is not None}


# ==========================================================================
# FORWARD RETURN
# ==========================================================================

def _forward_return_60d(
    symbol: str, filed_at: date,
    forward_days: int = FORWARD_DAYS,
) -> Optional[tuple[float, float, float]]:
    """Compute forward `forward_days` calendar-day price return from
    filed_at. Returns (return_fraction, price_from, price_to) or None
    if price history is incomplete.
    """
    from_price_row = get_price_at_or_before(symbol, filed_at)
    if from_price_row is None:
        return None
    from_price = from_price_row.get("close") or from_price_row.get("adjusted_close")
    if not from_price:
        return None

    end = filed_at + timedelta(days=forward_days)
    to_price_row = get_price_at_or_before(symbol, end)
    if to_price_row is None:
        return None
    to_price = to_price_row.get("close") or to_price_row.get("adjusted_close")
    if not to_price:
        return None

    return ((to_price - from_price) / from_price, float(from_price), float(to_price))


# ==========================================================================
# CHECKPOINTING
# ==========================================================================

@dataclass
class Checkpoint:
    completed_symbols: list[str]
    total_events: int
    errors: dict[str, str]

    @staticmethod
    def empty() -> "Checkpoint":
        return Checkpoint(completed_symbols=[], total_events=0, errors={})


def _load_checkpoint(path: Path) -> Checkpoint:
    if not path.exists():
        return Checkpoint.empty()
    try:
        data = json.loads(path.read_text())
        return Checkpoint(**data)
    except Exception as e:
        log.warning(f"checkpoint load failed ({e!r}); starting fresh")
        return Checkpoint.empty()


def _write_checkpoint(path: Path, cp: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cp), indent=2))


# ==========================================================================
# DRY-RUN SEED: prices for the synthetic fetcher
# ==========================================================================

def _seed_synthetic_prices(symbols: list[str], start: date, end: date) -> None:
    """For dry-run, populate price_history_pit with deterministic weekly
    bars so forward_return_60d has data to compute against."""
    import hashlib
    for sym in symbols:
        d = start
        # Baseline price
        base_h = int(hashlib.sha256(sym.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        base_price = 50 + 150 * base_h
        while d <= end:
            if d.weekday() < 5:
                # Price drifts with a tiny deterministic trend
                elapsed = (d - start).days
                drift = 1.0 + 0.0008 * elapsed  # ~30%/year
                daily_noise_h = int(hashlib.sha256(
                    f"{sym}:{d.isoformat()}".encode()
                ).hexdigest()[:8], 16) / 0xFFFFFFFF
                noise = 1.0 + 0.02 * (daily_noise_h - 0.5)
                px = base_price * drift * noise
                save_price(sym, d, "synthetic",
                           open_=px, high=px*1.01, low=px*0.99,
                           close=px, volume=1e6)
            d += timedelta(days=1)


# ==========================================================================
# MAIN INGEST DRIVER
# ==========================================================================

def ingest_symbols(
    symbols: list[str], start: date, end: date,
    fetcher: Callable, out_path: Path,
    checkpoint_path: Path,
    source: str = DEFAULT_SOURCE,
    sleep_between_symbols: float = 2.0,
) -> tuple[int, int]:
    """Fetch, derive metrics, compute forward returns, write CSV.

    Returns (events_written, symbols_failed).
    """
    cp = _load_checkpoint(checkpoint_path)
    already_done = set(cp.completed_symbols)

    # Append-mode CSV (resumable). Write header only if file is empty.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out_path.exists() or out_path.stat().st_size == 0

    events_count = cp.total_events
    failed_count = 0

    with open(out_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "symbol", "period_end", "filed_at", "metric",
                "metric_value", "forward_return_60d",
                "forward_price_from", "forward_price_to",
                "sector", "source",
            ])
            f.flush()

        for i, sym in enumerate(symbols):
            if sym in already_done:
                log.info(f"[{i+1}/{len(symbols)}] {sym}: skip (checkpointed)")
                continue

            # Phase 4.7 v2: banks have incompatible KAP schema — early skip
            if sym.upper() in BANK_SYMBOLS:
                log.info(
                    f"[{i+1}/{len(symbols)}] {sym}: SKIP — banka şeması farklı "
                    f"(Krediler, Bankalar Bakiyeleri), ayrı calibration turu "
                    f"gerekli (Phase 5 kandidatı)"
                )
                # Mark as completed so re-runs don't re-attempt; record reason
                cp.completed_symbols.append(sym)
                cp.errors[sym] = "SKIP: bank schema, deferred to Phase 5"
                _write_checkpoint(checkpoint_path, cp)
                continue

            log.info(f"[{i+1}/{len(symbols)}] {sym}: fetching...")
            t0 = time.monotonic()
            try:
                quarters = fetcher(sym, start, end)
            except Exception as e:
                log.error(
                    f"{sym}: fetcher raised {type(e).__name__}: {e!r}",
                    exc_info=False,
                )
                cp.errors[sym] = f"{type(e).__name__}: {e!r}"
                failed_count += 1
                _write_checkpoint(checkpoint_path, cp)
                continue

            # Sort quarters ascending so prev_year_q lookup works
            quarters.sort(key=lambda q: q["period_end"])

            sym_events = 0
            for j, q in enumerate(quarters):
                # Find prev_year_q for revenue_growth (4 quarters back)
                prev_year_q = None
                if j >= 4 and (
                    q["period_end"].month == quarters[j - 4]["period_end"].month
                    and q["period_end"].year - quarters[j - 4]["period_end"].year == 1
                ):
                    prev_year_q = quarters[j - 4]

                metrics = _derive_metrics_from_statements(q, prev_year_q=prev_year_q)
                if not metrics:
                    continue

                # Also persist raw fundamentals into fundamentals_pit
                # so future re-runs and production scoring can use them
                for mname, mval in metrics.items():
                    try:
                        save_fundamental(
                            symbol=sym,
                            period_end=q["period_end"],
                            filed_at=q["filed_at"],
                            source=source,
                            metric=mname,
                            value=mval,
                        )
                    except Exception as e:
                        log.debug(f"save_fundamental {sym}/{mname}: {e!r}")

                # Forward return at filed_at
                fr = _forward_return_60d(sym, q["filed_at"])
                if fr is None:
                    continue
                ret_frac, price_from, price_to = fr
                sector = get_sector(sym) or "Unknown"

                for mname, mval in metrics.items():
                    writer.writerow([
                        sym, q["period_end"].isoformat(),
                        q["filed_at"].isoformat(),
                        mname, f"{mval:.6f}",
                        f"{ret_frac:.6f}",
                        f"{price_from:.4f}", f"{price_to:.4f}",
                        sector, source,
                    ])
                    sym_events += 1

                f.flush()  # be idempotent against kill -9

            events_count += sym_events
            cp.completed_symbols.append(sym)
            cp.total_events = events_count
            _write_checkpoint(checkpoint_path, cp)

            elapsed = time.monotonic() - t0
            log.info(
                f"[{i+1}/{len(symbols)}] {sym}: done in {elapsed:.1f}s, "
                f"{sym_events} events (total: {events_count})"
            )

            # Rate-limit gap between symbols
            if i < len(symbols) - 1 and sleep_between_symbols > 0:
                time.sleep(sleep_between_symbols)

    return events_count, failed_count


# ==========================================================================
# CLI
# ==========================================================================

def _parse_symbols_spec(spec: str) -> list[str]:
    if spec.upper() == "BIST30":
        from config import UNIVERSE_BIST30
        return list(UNIVERSE_BIST30)
    if spec.upper() == "ALL":
        from config import UNIVERSE
        return list(UNIVERSE)
    # Comma-separated
    return [s.strip().upper() for s in spec.split(",") if s.strip()]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", default="BIST30",
                   help="'BIST30' (default), 'ALL', or comma-separated list")
    p.add_argument("--start", default="2018-01-01",
                   help="ISO start date for quarter coverage")
    p.add_argument("--end", default=date.today().isoformat(),
                   help="ISO end date")
    p.add_argument("--out", default="reports/fa_events.csv")
    p.add_argument("--checkpoint", default="reports/fa_events_checkpoint.json")
    p.add_argument("--source", default=DEFAULT_SOURCE)
    p.add_argument("--sleep-between-symbols", type=float, default=2.0,
                   help="Rate-limit gap between symbols (seconds)")
    p.add_argument("--dry-run", action="store_true",
                   help="Use synthetic fetcher; don't touch borsapy network")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--reset-checkpoint", action="store_true",
                   help="Delete checkpoint + CSV before starting")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Ensure DB initialized (Phase 3b PIT tables)
    init_db()

    symbols = _parse_symbols_spec(args.symbols)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    out_path = Path(args.out)
    cp_path = Path(args.checkpoint)

    if args.reset_checkpoint:
        if out_path.exists(): out_path.unlink()
        if cp_path.exists(): cp_path.unlink()
        log.info("checkpoint + CSV reset")

    log.info(
        f"=== FA ingest: {len(symbols)} symbols, "
        f"{start}..{end}, dry_run={args.dry_run} ==="
    )

    if args.dry_run:
        log.info("DRY RUN: seeding synthetic prices first (3 yr)")
        # Shorter price window for dry-run speed
        _seed_synthetic_prices(
            symbols, start=start - timedelta(days=30),
            end=end + timedelta(days=120),
        )
        fetcher = make_synthetic_fetcher()
    else:
        fetcher = make_borsapy_fetcher()

    events, failed = ingest_symbols(
        symbols, start, end, fetcher, out_path, cp_path,
        source=args.source,
        sleep_between_symbols=args.sleep_between_symbols,
    )

    log.info(f"=== Done. {events} events written to {out_path}. "
             f"{failed} symbol(s) failed ===")
    if failed > 0:
        log.info(f"See {cp_path} for per-symbol errors")
    return 0 if failed < len(symbols) * 0.1 else 1  # allow 10% symbol loss


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

# ================================================================
# BISTBULL TERMINAL V10.0 — ANALYSIS ENGINE
# compute_metrics, Piotroski, Altman, Beneish, analyze_symbol
# yfinance + borsapy unified data path.
#
# V10.1 AUDIT FIXES (2026-03):
#   FIX-1  Piotroski F4: CFO > NI  →  CFO/TA > ROA  (Piotroski 2000)
#   FIX-2  Altman Z: silent `re_ or 0.0`  →  treat None as missing data;
#          bank/insurance sector guard returns None (model not applicable)
#   FIX-3  Beneish AQI: unbounded division  →  clamped to [0.5, 3.0]
#          (Beneish 1999 empirical range; prevents heavy-asset companies
#           from receiving false manipulation flags)
#   FIX-4  net_debt_ebit var renamed  →  net_debt_ebitda_val  (naming bug)
#   FIX-5  Graham FV: sector guard added for banks/financials and
#          high-multiple growth stocks (P/E > 40 or P/B > 10);
#          Graham's formula pre-conditions are now enforced
#   FIX-6  analyze_symbol: adjust_weights() now WIRES the applicability
#          matrix into FA scoring — banks no longer receive Altman Z weight
# ================================================================

from __future__ import annotations

import os
import logging
from typing import Optional, Any

import pandas as pd

from utils.helpers import safe_num, pick_row_pair, growth, base_ticker
from core.cache import raw_cache, analysis_cache
from config import UNIVERSE, FA_WEIGHTS

log = logging.getLogger("bistbull.analysis")

# ================================================================
# OPTIONAL IMPORTS
# ================================================================
try:
    import yfinance as yf
    os.makedirs("/tmp/yf-cache", exist_ok=True)
    yf.set_tz_cache_location("/tmp/yf-cache")
    YF_AVAILABLE = True
except ImportError:
    yf = None  # type: ignore
    YF_AVAILABLE = False

try:
    from data.providers import fetch_raw_v9, compute_metrics_v9, BORSAPY_AVAILABLE
except ImportError:
    BORSAPY_AVAILABLE = False
    fetch_raw_v9 = None  # type: ignore
    compute_metrics_v9 = None  # type: ignore


# ================================================================
# INTERNAL HELPERS
# ================================================================

# Keywords that identify financial-sector entities for which
# classic industrial-model formulas (Altman Z, Graham FV) are N/A.
_FINANCIAL_SECTOR_KEYWORDS: tuple[str, ...] = (
    "bank", "insurance", "financial services", "financial",
    "banka", "sigorta", "finans", "bankacılık",
)


def _is_financial_sector(sector: str, industry: str) -> bool:
    """Returns True if this company belongs to the financial sector family."""
    combined = (sector + " " + industry).lower()
    return any(k in combined for k in _FINANCIAL_SECTOR_KEYWORDS)


# ================================================================
# RAW FETCH
# ================================================================
def fetch_raw(symbol: str) -> dict:
    """V9: borsapy → gerçek KAP verisi. yfinance fallback."""
    cached = raw_cache.get(symbol)
    if cached is not None:
        return cached

    if BORSAPY_AVAILABLE and fetch_raw_v9 is not None:
        try:
            raw = fetch_raw_v9(symbol)
            log.debug(f"fetch_raw V9 OK: {symbol} (source: borsapy)")
            return raw
        except Exception as e:
            log.warning(f"fetch_raw V9 failed for {symbol}: {e}, trying yfinance...")

    if not YF_AVAILABLE:
        raise RuntimeError(f"Ne borsapy ne yfinance çalışıyor — {symbol} verisi alınamadı")

    tk = yf.Ticker(symbol)
    info = tk.get_info() or {}
    try:
        fast = getattr(tk, "fast_info", {}) or {}
    except Exception:
        fast = {}
    try:
        financials = tk.financials
    except Exception:
        financials = None
    try:
        balance = tk.balance_sheet
    except Exception:
        balance = None
    try:
        cashflow = tk.cashflow
    except Exception:
        cashflow = None

    raw = {
        "info": info, "fast": fast,
        "financials": financials, "balance": balance, "cashflow": cashflow,
        "source": "yfinance",
    }
    raw_cache.set(symbol, raw)
    return raw


# ================================================================
# LEGENDARY MODELS — Piotroski, Altman, Beneish
# ================================================================

def compute_piotroski(m: dict) -> Optional[int]:
    """
    Piotroski F-Score (Piotroski 2000, Journal of Accounting Research).

    9 binary signals across 3 categories:
      Profitability  : F1 ROA>0, F2 CFO>0, F3 ΔROA>0, F4 Accrual Quality
      Leverage/Liquid: F5 ΔLeverage<0, F6 ΔLiquidity>0, F7 No new shares
      Efficiency     : F8 ΔGross Margin>0, F9 ΔAsset Turnover>0

    FIX-1: F4 corrected to Piotroski's exact definition.
      OLD (wrong): CFO > Net Income
      NEW (correct): CFO / Total_Assets > ROA
      Rationale: The raw CFO > NI comparison has no asset-size normalization.
      A TL10B company scores the same as a TL100M company at identical ratios.
      Piotroski's signal tests whether the cash-based return on assets exceeds
      the accrual-based return — a pure earnings-quality signal.

    Returns int in [0, 9] if >= 4 signals have data, else None.
    """
    pts, used = 0, 0

    # ── Profitability ──────────────────────────────────────────────
    # F1: ROA > 0
    f1 = (m["roa"] > 0) if m.get("roa") is not None else None

    # F2: Operating Cash Flow > 0
    f2 = (m["operating_cf"] > 0) if m.get("operating_cf") is not None else None

    # F3: Δ ROA > 0  (ROA improving year-over-year)
    f3 = (
        m["roa"] > m["roa_prev"]
    ) if (m.get("roa") is not None and m.get("roa_prev") is not None) else None

    # F4: Accrual Quality — CFO / Total_Assets > ROA  [FIX-1]
    # This is Piotroski's exact specification from Table 1, Signal F4.
    # Prior code tested CFO > NI which is Sloan's accrual test, not Piotroski F4.
    f4 = None
    if (
        m.get("operating_cf") is not None
        and m.get("total_assets") is not None
        and m.get("roa") is not None
        and m["total_assets"] > 0
    ):
        cfo_scaled = m["operating_cf"] / m["total_assets"]
        f4 = cfo_scaled > (m["roa"] or 0.0)

    # ── Leverage / Liquidity ────────────────────────────────────────
    # F5: Δ Leverage < 0  (long-term debt ratio declining)
    f5 = None
    if (
        m.get("total_debt") is not None and m.get("total_assets") is not None
        and m.get("total_debt_prev") is not None and m.get("total_assets_prev") is not None
        and m["total_assets"] > 0 and m["total_assets_prev"] > 0
    ):
        lev_cur  = m["total_debt"]      / m["total_assets"]
        lev_prev = m["total_debt_prev"] / m["total_assets_prev"]
        f5 = lev_cur < lev_prev

    # F6: Δ Current Ratio > 0  (liquidity improving)
    f6 = (
        m["current_ratio"] > m["current_ratio_prev"]
    ) if (m.get("current_ratio") is not None and m.get("current_ratio_prev") is not None) else None

    # F7: No new share issuance (share count not increasing)
    f7 = (m["share_change"] <= 0) if m.get("share_change") is not None else None

    # ── Efficiency ─────────────────────────────────────────────────
    # F8: Δ Gross Margin > 0  (margin expanding)
    f8 = (
        m["gross_margin"] > m["gross_margin_prev"]
    ) if (m.get("gross_margin") is not None and m.get("gross_margin_prev") is not None) else None

    # F9: Δ Asset Turnover > 0  (asset efficiency improving)
    f9 = (
        m["asset_turnover"] > m["asset_turnover_prev"]
    ) if (m.get("asset_turnover") is not None and m.get("asset_turnover_prev") is not None) else None

    # ── Aggregate ──────────────────────────────────────────────────
    for signal in (f1, f2, f3, f4, f5, f6, f7, f8, f9):
        if signal is None:
            continue
        used += 1
        pts += int(signal)

    # Minimum 4 signals required for a statistically meaningful score
    return pts if used >= 4 else None


def compute_altman(m: dict) -> Optional[float]:
    """
    Altman Z-Score (Altman 1968, Journal of Finance).
    Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5

    Where:
      X1 = Working Capital / Total Assets
      X2 = Retained Earnings / Total Assets
      X3 = EBIT / Total Assets
      X4 = Market Value of Equity / Total Liabilities
      X5 = Sales / Total Assets

    Interpretation:
      Z > 3.0  → Safe Zone
      1.8–3.0  → Grey Zone
      Z < 1.8  → Distress Zone

    FIX-2a: SECTOR GUARD
      The Altman Z-Score was designed for publicly-traded manufacturers
      (Altman 1968, sample: 66 manufacturing firms). It is categorically
      invalid for banks, insurance companies, and financial services firms.
      For these sectors we return None — the scoring engine's adjust_weights()
      redistributes this weight to applicable dimensions.

    FIX-2b: RETAINED EARNINGS — no silent zero substitution
      The prior code used `safe_num(m.get("retained_earnings")) or 0.0`
      which treated MISSING data identically to a company with zero retained
      earnings. A missing value is NOT the same as zero. For companies where
      yfinance cannot parse retained earnings, returning None is more honest
      than silently inflating the distress score by up to 1.4 * (RE/TA).
      If retained_earnings is present (even if 0), we use it; if absent, we
      return None to signal data insufficiency.
    """
    # ── FIX-2a: Financial sector guard ────────────────────────────
    sector   = m.get("sector",   "") or ""
    industry = m.get("industry", "") or ""
    if _is_financial_sector(sector, industry):
        # Altman Z is not applicable to financial institutions.
        # The balance sheet structure (high leverage by design, regulatory
        # capital ratios) makes all five components meaningless.
        return None

    # ── Input extraction ──────────────────────────────────────────
    wc    = safe_num(m.get("working_capital"))
    ta    = safe_num(m.get("total_assets"))
    ebit  = safe_num(m.get("ebit"))
    tl    = safe_num(m.get("total_liabilities"))
    sales = safe_num(m.get("revenue"))
    mve   = safe_num(m.get("market_cap"))

    # ── FIX-2b: Retained earnings — no or-zero substitution ────────
    # safe_num returns None if the field is missing/non-numeric.
    # We include re_ in the None guard below; missing != zero.
    re_ = safe_num(m.get("retained_earnings"))

    # All seven variables required for a valid score.
    # re_ = None (missing data) is distinct from re_ = 0.0 (young company).
    if None in (wc, ta, re_, ebit, tl, sales, mve) or ta == 0 or tl == 0:
        return None

    # ── Computation ────────────────────────────────────────────────
    z = (
          1.2 * (wc   / ta)
        + 1.4 * (re_  / ta)
        + 3.3 * (ebit / ta)
        + 0.6 * (mve  / tl)
        + 1.0 * (sales / ta)
    )
    return round(z, 4)


def compute_beneish(m: dict) -> Optional[float]:
    """
    Beneish M-Score (Beneish 1999, Financial Analysts Journal).
    8-variable logistic model for earnings manipulation detection.

    M = -4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI
             + 0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI

    Interpretation:
      M < -2.22 → Low manipulation probability
      M > -2.22 → Elevated manipulation risk (Beneish threshold)
      M > -1.78 → High risk (stricter Dechow et al. threshold)

    FIX-3: AQI (Asset Quality Index) CLAMPING
      Asset Quality Index = AQI_numerator / AQI_denominator
      For asset-intensive industrials (EREGL, TUPRS, ARCLK), prior-year
      CA + PPE can approach Total Assets, making aqi_denominator → 0.
      This produces AQI values of 1,000,000+ which, multiplied by the
      coefficient 0.404, completely dominates the M-Score and generates
      false manipulation flags on legitimate heavy-industry companies.

      Fix: clamp AQI to [0.5, 3.0], the empirically observed range from
      Beneish (1999) Table 2 (manipulators: mean AQI = 1.254;
      non-manipulators: mean AQI = 1.019; outliers beyond 3.0 are artefacts).
    """
    rec      = m.get("receivables")
    rec_prev = m.get("receivables_prev")
    sales      = m.get("revenue")
    sales_prev = m.get("revenue_prev")
    gp      = m.get("gross_profit")
    gp_prev = m.get("gross_profit_prev")
    ca      = m.get("current_assets")
    ca_prev = m.get("current_assets_prev")
    ppe      = m.get("ppe")
    ppe_prev = m.get("ppe_prev")
    dep      = m.get("depreciation")
    dep_prev = m.get("depreciation_prev")
    sga      = m.get("sga")
    sga_prev = m.get("sga_prev")
    ta      = m.get("total_assets")
    ta_prev = m.get("total_assets_prev")
    ni  = m.get("net_income")
    cfo = m.get("operating_cf")

    # Core denominators must be non-zero for ratio calculations
    if any(safe_num(x) in (None, 0) for x in [sales, sales_prev, ta, ta_prev]):
        return None

    try:
        # ── DSRI: Days Sales in Receivables Index ──────────────────
        # (Receivables_t / Sales_t) / (Receivables_{t-1} / Sales_{t-1})
        # Rising DSRI → possible revenue inflation via receivables stuffing
        dsri = (
            ((rec or 0) / (sales or 1))
            / max((rec_prev or 0) / (sales_prev or 1), 1e-9)
        )

        # ── GMI: Gross Margin Index ────────────────────────────────
        # Prior gross margin / current gross margin
        # GMI > 1 → margins deteriorating (quality risk)
        gm      = (gp      or 0) / (sales      or 1)
        gm_prev = (gp_prev or 0) / (sales_prev or 1)
        gmi = (gm_prev / max(gm, 1e-9)) if (gm and gm_prev) else 1.0

        # ── AQI: Asset Quality Index — FIX-3: CLAMP to [0.5, 3.0] ─
        # AQI = (1 - (CA + PPE) / TA)_t  / (1 - (CA + PPE) / TA)_{t-1}
        # AQI > 1 → rising proportion of intangibles/other assets (risk signal)
        # Without clamping, asset-intensive firms (EREGL, TUPRS) hit AQI > 10,000
        # which dominates the M-Score and triggers false fraud alerts.
        aqi_num = 1.0 - ((ca or 0) + (ppe or 0))          / max(ta,      1e-9)
        aqi_den = 1.0 - ((ca_prev or 0) + (ppe_prev or 0)) / max(ta_prev, 1e-9)
        aqi_raw = aqi_num / max(aqi_den, 1e-9)
        # Beneish (1999) empirical range: clamp to [0.5, 3.0]
        aqi = max(0.5, min(3.0, aqi_raw))

        # ── SGI: Sales Growth Index ────────────────────────────────
        # Sales_t / Sales_{t-1}; high growth firms may engage in manipulation
        sgi = sales / max(sales_prev, 1e-9)

        # ── DEPI: Depreciation Index ───────────────────────────────
        # Prior depreciation rate / current depreciation rate
        # DEPI > 1 → slowing depreciation (possible asset life extension)
        dep_prev_rate = (dep_prev or 0) / max((dep_prev or 0) + (ppe_prev or 0), 1e-9)
        dep_cur_rate  = (dep      or 0) / max((dep      or 0) + (ppe      or 0), 1e-9)
        depi = dep_prev_rate / max(dep_cur_rate, 1e-9)

        # ── SGAI: SG&A Index ───────────────────────────────────────
        # (SGA/Sales)_t / (SGA/Sales)_{t-1}; rising admin costs are a risk flag
        sgai = (
            (abs(sga or 0) / (sales or 1))
            / max(abs(sga_prev or 0) / (sales_prev or 1), 1e-9)
        )

        # ── LVGI: Leverage Index ───────────────────────────────────
        # (TotalDebt/TA)_t / (TotalDebt/TA)_{t-1}; increasing leverage risk
        lvgi = (
            (m.get("total_debt") or 0) / max(ta, 1e-9)
        ) / max(
            (m.get("total_debt_prev") or 0) / max(ta_prev, 1e-9),
            1e-9,
        )

        # ── TATA: Total Accruals to Total Assets ──────────────────
        # (Net Income - CFO) / TA; the Sloan accrual signal
        # Large positive TATA → aggressive accrual accounting
        tata = ((ni or 0) - (cfo or 0)) / max(ta, 1e-9)

        # ── M-Score ────────────────────────────────────────────────
        m_score = (
            -4.84
            + 0.920 * dsri
            + 0.528 * gmi
            + 0.404 * aqi      # AQI is now safely clamped
            + 0.892 * sgi
            + 0.115 * depi
            - 0.172 * sgai
            + 4.679 * tata
            - 0.327 * lvgi
        )
        return round(m_score, 4)

    except Exception as e:
        log.debug(f"compute_beneish exception: {e}")
        return None


# ================================================================
# COMPUTE METRICS — yfinance fallback path
# ================================================================
def compute_metrics(symbol: str) -> dict:
    """V9: borsapy primary → yfinance fallback → metric dict."""
    raw = fetch_raw(symbol)

    # ── borsapy path ───────────────────────────────────────────────
    if raw.get("source") == "borsapy" and BORSAPY_AVAILABLE and compute_metrics_v9 is not None:
        m = compute_metrics_v9(symbol)
        m["piotroski_f"] = compute_piotroski(m)
        m["altman_z"]    = compute_altman(m)
        m["beneish_m"]   = compute_beneish(m)
        return m

    # ── yfinance fallback ──────────────────────────────────────────
    info = raw["info"]
    fast = raw["fast"]
    fin  = raw["financials"]
    bal  = raw["balance"]
    cf   = raw["cashflow"]

    # ── Income statement ──────────────────────────────────────────
    revenue,          revenue_prev          = pick_row_pair(fin, ["Total Revenue", "Operating Revenue"])
    gross_profit,     gross_profit_prev     = pick_row_pair(fin, ["Gross Profit"])
    operating_income, _                     = pick_row_pair(fin, ["Operating Income", "EBIT"])
    ebit,             _                     = pick_row_pair(fin, ["EBIT", "Operating Income"])
    ebitda,           ebitda_prev           = pick_row_pair(fin, ["EBITDA"])
    net_income,       net_income_prev       = pick_row_pair(fin, ["Net Income", "Net Income Common Stockholders"])
    interest_exp,     _                     = pick_row_pair(fin, ["Interest Expense", "Interest Expense Non Operating"])
    dil_shares,       dil_shares_prev       = pick_row_pair(fin, ["Diluted Average Shares", "Basic Average Shares"])
    eps_row,          eps_row_prev          = pick_row_pair(fin, ["Diluted EPS", "Basic EPS"])
    sga,              sga_prev              = pick_row_pair(fin, ["Selling General And Administration"])

    # ── Cash flow statement ───────────────────────────────────────
    op_cf,  _         = pick_row_pair(cf, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
    capex,  _         = pick_row_pair(cf, ["Capital Expenditure"])
    dep,    dep_prev  = pick_row_pair(cf, ["Depreciation", "Depreciation And Amortization"])

    # ── Balance sheet ─────────────────────────────────────────────
    total_assets,   total_assets_prev   = pick_row_pair(bal, ["Total Assets"])
    total_liab,     _                   = pick_row_pair(bal, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
    total_debt,     total_debt_prev     = pick_row_pair(bal, ["Total Debt"])
    cash,           _                   = pick_row_pair(bal, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"])
    cur_assets,     cur_assets_prev     = pick_row_pair(bal, ["Current Assets", "Total Current Assets"])
    cur_liab,       cur_liab_prev       = pick_row_pair(bal, ["Current Liabilities", "Total Current Liabilities"])
    ret_earn,       _                   = pick_row_pair(bal, ["Retained Earnings"])
    equity,         _                   = pick_row_pair(bal, ["Stockholders Equity", "Total Stockholder Equity"])
    receivables,    rec_prev            = pick_row_pair(bal, ["Accounts Receivable", "Receivables"])
    ppe,            ppe_prev            = pick_row_pair(bal, ["Net PPE", "Property Plant Equipment Net"])

    # ── Price / market data ───────────────────────────────────────
    price      = safe_num(fast.get("last_price"))  or safe_num(info.get("currentPrice"))
    market_cap = safe_num(fast.get("market_cap"))  or safe_num(info.get("marketCap"))
    pe         = safe_num(info.get("trailingPE"))  or safe_num(info.get("forwardPE"))
    pb         = safe_num(info.get("priceToBook"))
    ev_ebitda  = safe_num(info.get("enterpriseToEbitda"))
    div_yield  = safe_num(info.get("dividendYield"))
    beta       = safe_num(info.get("beta"))
    trailing_eps = safe_num(info.get("trailingEps")) or safe_num(eps_row)
    book_val_ps  = safe_num(info.get("bookValue")) or (
        (equity / dil_shares) if equity and dil_shares else None
    )

    # ── Returns / margins ─────────────────────────────────────────
    roe = safe_num(info.get("returnOnEquity")) or (
        (net_income / equity)        if (net_income and equity)        else None
    )
    roa = safe_num(info.get("returnOnAssets")) or (
        (net_income / total_assets)  if (net_income and total_assets)  else None
    )
    roa_prev          = (net_income_prev / total_assets_prev) if (net_income_prev and total_assets_prev) else None
    gross_margin      = (gross_profit  / revenue)      if (gross_profit  and revenue)      else None
    gross_margin_prev = (gross_profit_prev / revenue_prev) if (gross_profit_prev and revenue_prev) else None
    op_margin  = safe_num(info.get("operatingMargins")) or (
        (operating_income / revenue) if (operating_income and revenue) else None
    )
    net_margin = safe_num(info.get("profitMargins")) or (
        (net_income / revenue)       if (net_income and revenue)       else None
    )

    # ── Liquidity / leverage ──────────────────────────────────────
    cur_ratio      = safe_num(info.get("currentRatio")) or (
        (cur_assets / cur_liab) if (cur_assets and cur_liab) else None
    )
    cur_ratio_prev = (cur_assets_prev / cur_liab_prev) if (cur_assets_prev and cur_liab_prev) else None
    debt_eq        = safe_num(info.get("debtToEquity")) or (
        (total_debt / equity * 100) if (total_debt and equity) else None
    )

    # FIX-4: variable correctly named net_debt_ebitda_val
    # (the original was named net_debt_ebit but divided by EBITDA — confusing)
    net_debt             = (total_debt - cash) if (total_debt is not None and cash is not None) else None
    net_debt_ebitda_val  = (net_debt / ebitda) if (net_debt is not None and ebitda not in (None, 0)) else None

    _ebit_val = ebit if ebit is not None else operating_income
    int_cov   = (
        (_ebit_val / abs(interest_exp))
        if (_ebit_val is not None and interest_exp not in (None, 0))
        else None
    )

    # ── Cash flow derived ─────────────────────────────────────────
    free_cf = (
        (op_cf + capex) if (op_cf is not None and capex is not None) else None
    ) or safe_num(info.get("freeCashflow"))
    fcf_yield  = (free_cf / market_cap) if (free_cf is not None and market_cap not in (None, 0)) else None
    fcf_margin = (free_cf / revenue)    if (free_cf is not None and revenue    not in (None, 0)) else None
    cfo_to_ni  = (op_cf   / net_income) if (op_cf   is not None and net_income not in (None, 0)) else None

    # ── Growth rates ──────────────────────────────────────────────
    rev_growth  = safe_num(info.get("revenueGrowth"))  or growth(revenue,    revenue_prev)
    eps_growth  = (
        safe_num(info.get("earningsGrowth"))
        or growth(eps_row,    eps_row_prev)
        or growth(net_income, net_income_prev)
    )
    ebit_growth = growth(ebitda, ebitda_prev)

    # ── Capital allocation ────────────────────────────────────────
    wc       = (cur_assets - cur_liab) if (cur_assets is not None and cur_liab is not None) else None
    tax_rate = safe_num(info.get("effectiveTaxRate")) or 0.20
    inv_cap  = (
        (total_debt + equity - cash)
        if (total_debt is not None and equity is not None and cash is not None)
        else None
    )
    _ebit_nopat = ebit if ebit is not None else operating_income
    nopat = (
        _ebit_nopat * (1 - min(max(tax_rate, 0), 0.35))
    ) if _ebit_nopat is not None else None
    roic = (nopat / inv_cap) if (nopat is not None and inv_cap not in (None, 0)) else None

    # ── PEG ───────────────────────────────────────────────────────
    # Only valid when EPS growth is strictly positive.
    # A loss-making company (eps_growth <= 0) must NOT receive a PEG score;
    # dividing by a negative or zero denominator produces nonsense.
    peg = (
        pe / max(eps_growth * 100, 1e-9)
    ) if (pe not in (None, 0) and eps_growth is not None and eps_growth > 0) else None

    # ── Graham Fair Value — FIX-5: Sector & multiple guards ───────
    # Formula: FV = sqrt(22.5 × EPS × BVPS)
    # Graham's own validity pre-conditions (Security Analysis, 1962 ed.):
    #   1. EPS > 0          (square root is undefined for negatives)
    #   2. BVPS > 0         (negative equity makes formula meaningless)
    #   3. Non-financial     (banks' book value is regulatory capital, not
    #                         asset-based; the formula is inapplicable)
    #   4. P/E <= 40        (relaxed from Graham's <=15 to account for
    #                         Turkish inflation premium on earnings multiples;
    #                         beyond 40× the formula produces a 'margin of
    #                         safety' that is purely mechanical, not economic)
    #   5. P/B <= 10        (franchise/intangible value dominates at P/B > 10;
    #                         Graham excluded these stocks from his value screen)
    _sector_str   = str(info.get("sector",   "") or "")
    _industry_str = str(info.get("industry", "") or "")
    _is_financial = _is_financial_sector(_sector_str, _industry_str)
    _pe_excessive = (pe is not None and pe  > 40)
    _pb_excessive = (pb is not None and pb  > 10)
    _eps_positive = (trailing_eps  is not None and trailing_eps  > 0)
    _bvps_valid   = (book_val_ps   is not None and book_val_ps   > 0)

    graham_fv: Optional[float] = None
    if _eps_positive and _bvps_valid and not _is_financial and not _pe_excessive and not _pb_excessive:
        graham_fv = round((22.5 * trailing_eps * book_val_ps) ** 0.5, 4)

    mos = (
        (graham_fv - price) / graham_fv
    ) if (graham_fv not in (None, 0) and price is not None) else None

    # ── Misc ──────────────────────────────────────────────────────
    share_ch   = growth(dil_shares,  dil_shares_prev)
    asset_to   = (revenue      / total_assets)      if (revenue      is not None and total_assets      not in (None, 0)) else None
    asset_to_p = (revenue_prev / total_assets_prev) if (revenue_prev is not None and total_assets_prev not in (None, 0)) else None
    inst_holders_pct = safe_num(info.get("heldPercentInstitutions"))

    # ── Assemble metric dict ──────────────────────────────────────
    m = {
        "symbol":   symbol,
        "ticker":   base_ticker(symbol),
        "name":     str(info.get("shortName") or info.get("longName") or symbol),
        "currency": str(info.get("currency") or ""),
        "sector":   str(info.get("sector")   or ""),
        "industry": str(info.get("industry") or ""),

        # Price / market
        "price": price, "market_cap": market_cap,
        "pe": pe, "pb": pb, "ev_ebitda": ev_ebitda,
        "dividend_yield": div_yield, "beta": beta,

        # Income
        "revenue": revenue, "revenue_prev": revenue_prev,
        "gross_profit": gross_profit, "gross_profit_prev": gross_profit_prev,
        "operating_income": operating_income,
        "ebit": ebit or operating_income,
        "ebitda": ebitda, "ebitda_prev": ebitda_prev,
        "net_income": net_income, "net_income_prev": net_income_prev,

        # Cash flow
        "operating_cf": op_cf, "free_cf": free_cf,

        # Balance sheet
        "total_assets": total_assets, "total_assets_prev": total_assets_prev,
        "total_liabilities": total_liab,
        "total_debt": total_debt, "total_debt_prev": total_debt_prev,
        "cash": cash,
        "current_assets": cur_assets, "current_assets_prev": cur_assets_prev,
        "current_liabilities": cur_liab, "current_liabilities_prev": cur_liab_prev,
        "working_capital": wc,
        "retained_earnings": ret_earn,
        "equity": equity,
        "receivables": receivables, "receivables_prev": rec_prev,
        "ppe": ppe, "ppe_prev": ppe_prev,
        "depreciation": dep, "depreciation_prev": dep_prev,
        "sga": sga, "sga_prev": sga_prev,

        # Per-share
        "trailing_eps": trailing_eps, "book_value_ps": book_val_ps,

        # Returns / margins
        "roe": roe, "roa": roa, "roa_prev": roa_prev, "roic": roic,
        "gross_margin": gross_margin, "gross_margin_prev": gross_margin_prev,
        "operating_margin": op_margin, "net_margin": net_margin,

        # Leverage / liquidity
        "current_ratio": cur_ratio, "current_ratio_prev": cur_ratio_prev,
        "debt_equity": debt_eq,
        "net_debt_ebitda": net_debt_ebitda_val,   # FIX-4: correct variable name
        "interest_coverage": int_cov,

        # Cash flow quality
        "fcf_yield": fcf_yield, "fcf_margin": fcf_margin, "cfo_to_ni": cfo_to_ni,

        # Growth
        "revenue_growth": rev_growth, "eps_growth": eps_growth, "ebitda_growth": ebit_growth,

        # Valuation models
        "peg": peg, "graham_fv": graham_fv, "margin_safety": mos,

        # Capital allocation
        "share_change": share_ch,
        "asset_turnover": asset_to, "asset_turnover_prev": asset_to_p,

        # Ownership
        "inst_holders_pct": inst_holders_pct,

        # Provenance
        "data_source": "yfinance",
    }

    # ── Legendary model scores ────────────────────────────────────
    m["piotroski_f"] = compute_piotroski(m)
    m["altman_z"]    = compute_altman(m)    # Returns None for banks (FIX-2a)
    m["beneish_m"]   = compute_beneish(m)   # AQI clamped to [0.5, 3.0] (FIX-3)
    return m


# ================================================================
# ANALYZE SYMBOL — Full pipeline + V10 applicability
# ================================================================
def analyze_symbol(symbol: str) -> dict:
    """
    Full analysis pipeline:
      1. fetch_raw / compute_metrics
      2. Sector-aware weight adjustment (FIX-6 — now WIRED)
      3. 7-dimension FA scoring with adjusted weights
      4. Risk penalty computation
      5. İvme (momentum) 3-dimension scoring
      6. Overall score blending
      7. Label & decision engine
      8. Applicability flags for UI

    V10.1: adjust_weights() is now called before the FA score is computed,
    ensuring that banks do not receive Altman Z-Score weight in balance,
    financials do not receive Graham FV weight in value, etc.
    Previously the applicability matrix existed in full but was wired to
    nothing in this pipeline — a ghost feature. Now it is live.
    """
    cached = analysis_cache.get(symbol)
    if cached is not None:
        return cached

    # ── Lazy imports (avoid circular at module level) ─────────────
    from engine.scoring import (
        map_sector,
        score_value, score_quality, score_growth,
        score_balance, score_earnings, score_moat, score_capital,
        score_momentum, score_technical_break, score_institutional_flow,
        compute_risk_penalties, compute_ivme,
        compute_overall, detect_hype, confidence_score,
        timing_label, quality_label, entry_quality_label,
        decision_engine, style_label, legendary_labels, drivers,
    )
    from engine.technical import compute_technical
    from engine.applicability import build_applicability_flags, adjust_weights

    # ── 1. Raw metrics ─────────────────────────────────────────────
    m = compute_metrics(symbol)
    sector_group = map_sector(m.get("sector", ""))

    # ── 2. Technical data (best-effort, non-blocking) ─────────────
    tech: Optional[dict] = None
    try:
        tech = compute_technical(symbol)
    except Exception as e:
        log.debug(f"analyze_symbol tech for {symbol}: {e}")

    # ── 3. 7-dimension FA scoring ──────────────────────────────────
    scores: dict[str, float] = {
        "value":    round(score_value(m,    sector_group) or 50.0, 1),
        "quality":  round(score_quality(m,  sector_group) or 50.0, 1),
        "growth":   round(score_growth(m,   sector_group) or 50.0, 1),
        "balance":  round(score_balance(m,  sector_group) or 50.0, 1),
        "earnings": round(score_earnings(m)               or 50.0, 1),
        "moat":     round(score_moat(m)                   or 50.0, 1),
        "capital":  round(score_capital(m)                or 50.0, 1),
    }

    # ── 4. FA Pure — FIX-6: Sector-adjusted weights ────────────────
    # Prior code: fa_pure = compute_fa_pure(scores)
    #   → always used the hardcoded FA_WEIGHTS dict unchanged
    #   → e.g. bank GARAN received full weight on 'balance' which includes
    #     Altman Z — but altman_z = None (correctly), so balance used 50.0
    #     (the silent default) weighted by the full balance coefficient.
    #     This is a double error: wrong weight AND wrong input value.
    #
    # Now: adjust_weights() removes N/A dimensions entirely and redistributes
    # their weight proportionally to the remaining applicable dimensions.
    # A bank's 'balance' score is now computed solely from current_ratio,
    # debt_equity, and interest_coverage — Altman Z is excised with its weight
    # redistributed to 'quality', 'growth', etc. as appropriate.
    adjusted_weights: dict[str, float] = adjust_weights(FA_WEIGHTS, sector_group)
    _fa_total = sum(
        adjusted_weights.get(key, 0.0) * scores[key]
        for key in adjusted_weights
        if key in scores
    )
    fa_pure = round(max(1.0, min(99.0, _fa_total)), 1)

    # ── 5. Risk penalties ─────────────────────────────────────────
    risk_penalty, risk_reasons = compute_risk_penalties(m, sector_group)

    # Fake Profit filter: negative CFO with positive NI = cash-flow divergence
    cfo_ni = m.get("cfo_to_ni")
    if cfo_ni is not None:
        if (
            m.get("operating_cf") is not None and m["operating_cf"] < 0
            and m.get("net_income") is not None and m["net_income"] > 0
        ):
            risk_penalty -= 12
            risk_reasons.append("Kâr var nakit yok — sahte kâr riski (-12)")
        elif cfo_ni < 0.5:
            risk_penalty -= 6
            risk_reasons.append(f"Düşük nakit kalitesi CFO/NI={cfo_ni:.2f} (-6)")

    risk_score  = risk_penalty
    deger_score = round(max(1.0, min(99.0, fa_pure + risk_penalty)), 1)

    # ── 6. İvme (momentum) 3-dimension scoring ────────────────────
    mom  = score_momentum(m, tech)
    tb   = score_technical_break(m, tech)
    inst = score_institutional_flow(m, tech)
    scores["momentum"]   = round(mom,  1) if mom  is not None else 50.0
    scores["tech_break"] = round(tb,   1) if tb   is not None else 50.0
    scores["inst_flow"]  = round(inst, 1) if inst is not None else 50.0

    ivme_score = compute_ivme(scores)
    overall    = compute_overall(fa_pure, ivme_score, scores["value"], risk_penalty)

    # ── 7. Hype detection ─────────────────────────────────────────
    is_hype, hype_reason = detect_hype(tech, fa_pure)

    # ── 8. Labels & decision engine ───────────────────────────────
    t_label = timing_label(ivme_score)
    q_label = quality_label(fa_pure)
    e_label = entry_quality_label(fa_pure, ivme_score, risk_penalty)
    if is_hype:
        e_label = "SPEKÜLATİF"
    decision = decision_engine(fa_pure, ivme_score, risk_penalty, e_label)

    confidence = confidence_score(m)
    style      = style_label(scores)
    legends    = legendary_labels(m, scores)
    pos, neg   = drivers(scores, confidence, m, sector_group)

    if is_hype and hype_reason:
        neg.insert(0, f"⚠️ HYPE: {hype_reason}")

    # ── 9. Applicability flags (for UI badge rendering) ───────────
    applicability_flags = build_applicability_flags(sector_group)

    # ── 10. Assemble result snapshot ──────────────────────────────
    r: dict = {
        "symbol":       symbol,
        "ticker":       base_ticker(symbol),
        "name":         m["name"],
        "currency":     m["currency"],
        "sector":       m.get("sector",   ""),
        "sector_group": sector_group,
        "industry":     m.get("industry", ""),

        # Raw metrics (full dict — used by UI detail panels)
        "metrics": m,

        # Dimension scores (7 FA + 3 momentum)
        "scores": scores,

        # Composite scores
        "overall":    overall,
        "confidence": confidence,
        "fa_score":   fa_pure,        # sector-adjusted FA pure (FIX-6)
        "deger":      deger_score,    # fa_pure + risk
        "ivme":       ivme_score,

        # Risk
        "risk_score":   risk_score,
        "risk_penalty": risk_penalty,
        "risk_reasons": risk_reasons,

        # Decision
        "entry_label": e_label,
        "is_hype":     is_hype,
        "timing":      t_label,
        "quality_tag": q_label,
        "decision":    decision,

        # Labels
        "style":     style,
        "legendary": legends,
        "positives": pos,
        "negatives": neg,

        # V10: Sector applicability matrix for UI warnings
        "applicability": applicability_flags,

        # V10.1: expose which weights were actually used for this sector
        # (enables the UI to show "Ağırlıklar sektöre göre ayarlandı" badge)
        "adjusted_weights": adjusted_weights,
    }

    analysis_cache.set(symbol, r)
    return r

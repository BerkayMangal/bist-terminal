# ================================================================
# BISTBULL TERMINAL V10.1 — DATA PROVIDERS (EODHD)
# borsapy + yfinance → EODHD API migrasyonu.
# Tek kaynak: EODHD (sınırsız plan).
# compute_metrics_v9() çıktı formatı 1:1 korunmuştur.
# ================================================================

from __future__ import annotations

import math
import os
import logging
import datetime as _dt
from typing import Optional, Any

import numpy as np
import pandas as pd
import requests

from core.cache import raw_cache
from core.circuit_breaker import cb_eodhd, CircuitBreakerOpen
from config import BATCH_HISTORY_WORKERS, EODHD_BASE_URL

log = logging.getLogger("bistbull.data")

# ================================================================
# EODHD CONFIG
# ================================================================
EODHD_API_KEY: str = os.environ.get("EODHD_API_KEY", "")
_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json"})
EODHD_TIMEOUT = 30

# ================================================================
# BANK TICKERS
# ================================================================
BANK_TICKERS: set[str] = {
    "AKBNK", "GARAN", "ISCTR", "YKBNK", "VAKBN",
    "HALKB", "TSKB", "SKBNK", "ALBRK",
}


def is_bank(ticker: str) -> bool:
    return ticker.upper().replace(".IS", "") in BANK_TICKERS


# ================================================================
# EODHD HTTP HELPER
# ================================================================
def _eodhd_get(path: str, params: dict | None = None, timeout: int = EODHD_TIMEOUT) -> Any:
    """
    EODHD API'ye GET isteği gönder. JSON döndürür.
    Hata durumunda exception fırlatır (CB yakalar).
    """
    if not EODHD_API_KEY:
        raise RuntimeError("EODHD_API_KEY ortam değişkeni tanımlı değil")

    url = f"{EODHD_BASE_URL}/{path}"
    p = {"api_token": EODHD_API_KEY, "fmt": "json"}
    if params:
        p.update(params)

    resp = _SESSION.get(url, params=p, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ================================================================
# SAFE NUM UTILITY
# ================================================================
def _safe_num(x: Any) -> Optional[float]:
    """Güvenli float dönüşüm. None/NaN/Inf/str → None veya float."""
    try:
        if x is None or x == "None" or x == "":
            return None
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


# ================================================================
# EODHD FUNDAMENTALS FIELD EXTRACTORS
# ================================================================
def _get_yearly_statements(fundamentals: dict, section: str) -> list[dict]:
    """
    Financials → Income_Statement/Balance_Sheet/Cash_Flow → yearly
    Returns sorted list of period dicts (newest first).
    """
    try:
        yearly = fundamentals.get("Financials", {}).get(section, {}).get("yearly", {})
        if not yearly:
            return []
        # yearly is dict keyed by date string: {"2024-12-31": {...}, "2023-12-31": {...}}
        items = sorted(yearly.items(), key=lambda x: x[0], reverse=True)
        return [v for _, v in items]
    except Exception:
        return []


def _stmt_val(stmts: list[dict], field: str, idx: int = 0) -> Optional[float]:
    """Statement listesinden idx'inci dönemin field değerini çek."""
    if idx >= len(stmts):
        return None
    return _safe_num(stmts[idx].get(field))


def _stmt_pair(stmts: list[dict], field: str) -> tuple[Optional[float], Optional[float]]:
    """Cari ve önceki dönem değerleri."""
    return _stmt_val(stmts, field, 0), _stmt_val(stmts, field, 1)


# ================================================================
# FETCH RAW V9 — EODHD Fundamentals + Real-Time
# ================================================================
def fetch_raw_v9(symbol: str) -> dict:
    """
    EODHD API üzerinden fundamentals + real-time veri çek.
    Circuit Breaker korumalı, SafeCache entegreli.
    """
    cached = raw_cache.get(symbol)
    if cached is not None:
        return cached

    # Circuit Breaker kontrolü
    cb_eodhd.before_call()

    tc = symbol.upper().replace(".IS", "").replace(".E", "")
    eodhd_symbol = f"{tc}.IS"

    try:
        # Fundamentals — bilanço, gelir tablosu, nakit akış, genel bilgiler, highlights
        fundamentals = _eodhd_get(f"fundamentals/{eodhd_symbol}")

        # Real-time / delayed quote
        try:
            rt = _eodhd_get(f"real-time/{eodhd_symbol}")
        except Exception:
            rt = {}

        general = fundamentals.get("General", {}) or {}
        highlights = fundamentals.get("Highlights", {}) or {}
        valuation = fundamentals.get("Valuation", {}) or {}
        shares_stats = fundamentals.get("SharesStats", {}) or {}
        technicals = fundamentals.get("Technicals", {}) or {}

        # Build info dict (yfinance-compatible field names for backward compat)
        info = {
            "shortName": general.get("Name", tc),
            "longName": general.get("Name", tc),
            "sector": general.get("Sector", ""),
            "industry": general.get("Industry", ""),
            "currency": general.get("CurrencyCode", "TRY"),
            "currentPrice": _safe_num(rt.get("close")) or _safe_num(highlights.get("WallStreetTargetPrice")),
            "marketCap": _safe_num(highlights.get("MarketCapitalization")),
            "trailingPE": _safe_num(highlights.get("PERatio")),
            "forwardPE": _safe_num(highlights.get("ForwardPE")),
            "priceToBook": _safe_num(valuation.get("PriceBookMRQ")),
            "enterpriseToEbitda": _safe_num(valuation.get("EnterpriseValueEbitda")),
            "dividendYield": _safe_num(highlights.get("DividendYield")),
            "returnOnEquity": _safe_num(highlights.get("ReturnOnEquityTTM")),
            "returnOnAssets": _safe_num(highlights.get("ReturnOnAssetsTTM")),
            "operatingMargins": _safe_num(highlights.get("OperatingMarginTTM")),
            "profitMargins": _safe_num(highlights.get("ProfitMargin")),
            "currentRatio": None,  # Will compute from balance sheet
            "debtToEquity": None,  # Will compute from balance sheet
            "beta": _safe_num(technicals.get("Beta")),
            "revenueGrowth": _safe_num(highlights.get("QuarterlyRevenueGrowthYOY")),
            "earningsGrowth": _safe_num(highlights.get("QuarterlyEarningsGrowthYOY")),
            "freeCashflow": None,  # Will compute from cash flow
            "trailingEps": _safe_num(highlights.get("EarningsShare")),
            "bookValue": _safe_num(highlights.get("BookValue")),
            "heldPercentInstitutions": None,  # EODHD doesn't have this directly
            "effectiveTaxRate": None,  # Will compute from statements
        }

        # Build fast dict (borsapy-compatible)
        fast = {
            "last_price": _safe_num(rt.get("close")),
            "open": _safe_num(rt.get("open")),
            "day_high": _safe_num(rt.get("high")),
            "day_low": _safe_num(rt.get("low")),
            "previous_close": _safe_num(rt.get("previousClose")),
            "volume": _safe_num(rt.get("volume")),
            "market_cap": _safe_num(highlights.get("MarketCapitalization")),
            "shares": _safe_num(shares_stats.get("SharesOutstanding")),
            "pe_ratio": _safe_num(highlights.get("PERatio")),
            "pb_ratio": _safe_num(valuation.get("PriceBookMRQ")),
            "year_high": _safe_num(technicals.get("52WeekHigh")),
            "year_low": _safe_num(technicals.get("52WeekLow")),
            "fifty_day_average": _safe_num(technicals.get("50DayMA")),
            "two_hundred_day_average": _safe_num(technicals.get("200DayMA")),
            "free_float": _safe_num(shares_stats.get("PercentFloat")),
            "foreign_ratio": None,
        }

        raw = {
            "info": info,
            "fast": fast,
            "fundamentals": fundamentals,
            "source": "eodhd",
            "ticker_clean": tc,
            "is_bank": is_bank(tc),
            "_fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }

        raw_cache.set(symbol, raw)
        cb_eodhd.on_success()
        return raw

    except CircuitBreakerOpen:
        raise
    except Exception as e:
        cb_eodhd.on_failure(e)
        raise


# ================================================================
# COMPUTE METRICS V9 — EODHD Fundamentals → metric dict
# ================================================================
def compute_metrics_v9(symbol: str) -> dict:
    """
    EODHD fundamentals → metric dict.
    Çıktı formatı downstream scoring ile 1:1 uyumlu.
    """
    raw = fetch_raw_v9(symbol)
    info = raw["info"]
    fast = raw["fast"]
    fundamentals = raw["fundamentals"]
    tc = raw["ticker_clean"]

    # Extract financial statements (newest first)
    inc_stmts = _get_yearly_statements(fundamentals, "Income_Statement")
    bal_stmts = _get_yearly_statements(fundamentals, "Balance_Sheet")
    cf_stmts = _get_yearly_statements(fundamentals, "Cash_Flow")

    # ── Income Statement ──────────────────────────────────────
    revenue, revenue_prev = _stmt_pair(inc_stmts, "totalRevenue")
    gross_profit, gross_profit_prev = _stmt_pair(inc_stmts, "grossProfit")
    operating_income, op_inc_prev = _stmt_pair(inc_stmts, "operatingIncome")
    ebit = _stmt_val(inc_stmts, "ebit") or operating_income
    net_income, net_income_prev = _stmt_pair(inc_stmts, "netIncome")
    # Fallback: netIncomeApplicableToCommonShares
    if net_income is None:
        net_income, net_income_prev = _stmt_pair(inc_stmts, "netIncomeApplicableToCommonShares")
    interest_exp = _stmt_val(inc_stmts, "interestExpense")
    sga, sga_prev = _stmt_pair(inc_stmts, "sellingGeneralAdministrative")

    # ── Cash Flow ─────────────────────────────────────────────
    op_cf = _stmt_val(cf_stmts, "totalCashFromOperatingActivities")
    capex = _stmt_val(cf_stmts, "capitalExpenditures")
    dep = _stmt_val(cf_stmts, "depreciation")
    dep_prev = _stmt_val(cf_stmts, "depreciation", 1)
    free_cf_direct = _stmt_val(cf_stmts, "freeCashFlow")

    # EBITDA (compute from EBIT + depreciation if not in highlights)
    highlights = fundamentals.get("Highlights", {}) or {}
    ebitda = _safe_num(highlights.get("EBITDA"))
    if ebitda is None:
        ebitda = ((ebit or operating_income or 0) + abs(dep)) if dep and (ebit or operating_income) else None
    ebitda_prev_val = _stmt_val(inc_stmts, "ebitda", 1)
    if ebitda_prev_val is None:
        ebitda_prev_val = ((op_inc_prev or 0) + abs(dep_prev)) if dep_prev and op_inc_prev else None

    # ── Balance Sheet ─────────────────────────────────────────
    total_assets, total_assets_prev = _stmt_pair(bal_stmts, "totalAssets")
    equity = _stmt_val(bal_stmts, "totalStockholderEquity")
    total_liab = _stmt_val(bal_stmts, "totalLiab")
    if total_liab is None and total_assets is not None and equity is not None:
        total_liab = total_assets - equity

    # Debt
    short_debt = _stmt_val(bal_stmts, "shortTermDebt") or 0
    long_debt = _stmt_val(bal_stmts, "longTermDebt") or 0
    short_debt_prev = _stmt_val(bal_stmts, "shortTermDebt", 1) or 0
    long_debt_prev = _stmt_val(bal_stmts, "longTermDebt", 1) or 0
    total_debt = (short_debt + long_debt) if (short_debt or long_debt) else _stmt_val(bal_stmts, "shortLongTermDebtTotal")
    total_debt_prev = (short_debt_prev + long_debt_prev) if (short_debt_prev or long_debt_prev) else _stmt_val(bal_stmts, "shortLongTermDebtTotal", 1)

    cash = _stmt_val(bal_stmts, "cash") or _stmt_val(bal_stmts, "cashAndEquivalents") or _stmt_val(bal_stmts, "cashAndShortTermInvestments")
    cur_assets, cur_assets_prev = _stmt_pair(bal_stmts, "totalCurrentAssets")
    cur_liab, cur_liab_prev = _stmt_pair(bal_stmts, "totalCurrentLiabilities")
    ret_earn = _stmt_val(bal_stmts, "retainedEarnings")
    receivables, rec_prev = _stmt_pair(bal_stmts, "netReceivables")
    ppe, ppe_prev = _stmt_pair(bal_stmts, "propertyPlantEquipment")
    # fallback ppe field name
    if ppe is None:
        ppe, ppe_prev = _stmt_pair(bal_stmts, "propertyPlantAndEquipmentNet")

    # ── Market Data ───────────────────────────────────────────
    price = _safe_num(fast.get("last_price"))
    if price is None:
        price = _safe_num(info.get("currentPrice"))
    market_cap = _safe_num(fast.get("market_cap"))
    if market_cap is None:
        market_cap = _safe_num(info.get("marketCap"))
    pe = _safe_num(fast.get("pe_ratio")) or _safe_num(info.get("trailingPE"))
    pb = _safe_num(fast.get("pb_ratio")) or _safe_num(info.get("priceToBook"))
    ev_ebitda = _safe_num(info.get("enterpriseToEbitda"))
    div_yield = _safe_num(info.get("dividendYield"))
    beta = _safe_num(info.get("beta"))

    shares = _safe_num(fast.get("shares"))
    if shares is None and market_cap and price and price > 0:
        shares = market_cap / price

    # Per-share
    trailing_eps = _safe_num(info.get("trailingEps"))
    if trailing_eps is None and net_income is not None and shares and shares > 0:
        trailing_eps = net_income / shares
    book_val_ps = _safe_num(info.get("bookValue"))
    if book_val_ps is None and equity and shares and shares > 0:
        book_val_ps = equity / shares

    # ── Ratios ────────────────────────────────────────────────
    roe = _safe_num(info.get("returnOnEquity"))
    roe = roe if roe is not None else ((net_income / equity) if net_income is not None and equity not in (None, 0) else None)
    roa = _safe_num(info.get("returnOnAssets"))
    roa = roa if roa is not None else ((net_income / total_assets) if net_income is not None and total_assets not in (None, 0) else None)
    roa_prev = (net_income_prev / total_assets_prev) if net_income_prev and total_assets_prev and total_assets_prev != 0 else None
    gross_margin = (gross_profit / revenue) if gross_profit and revenue else None
    gross_margin_prev = (gross_profit_prev / revenue_prev) if gross_profit_prev and revenue_prev else None
    op_margin = _safe_num(info.get("operatingMargins"))
    op_margin = op_margin if op_margin is not None else ((operating_income / revenue) if operating_income is not None and revenue not in (None, 0) and revenue > 0 else None)
    net_margin = _safe_num(info.get("profitMargins"))
    net_margin = net_margin if net_margin is not None else ((net_income / revenue) if net_income is not None and revenue not in (None, 0) and revenue > 0 else None)
    cur_ratio = (cur_assets / cur_liab) if cur_assets is not None and cur_liab not in (None, 0) and cur_liab > 0 else None
    cur_ratio_prev = (cur_assets_prev / cur_liab_prev) if cur_assets_prev and cur_liab_prev else None
    debt_eq = (total_debt / equity * 100) if total_debt is not None and equity not in (None, 0) and abs(equity) > 1e4 else None
    net_debt = (total_debt - cash) if total_debt is not None and cash is not None else None
    net_debt_ebit = (net_debt / ebitda) if net_debt is not None and ebitda not in (None, 0) else None
    _ev = ebit if ebit is not None else operating_income
    int_cov = (_ev / abs(interest_exp)) if _ev is not None and interest_exp not in (None, 0) else None
    _fcf_c = (op_cf + capex) if op_cf is not None and capex is not None else None
    free_cf = free_cf_direct if free_cf_direct is not None else (_fcf_c if _fcf_c is not None else None)
    fcf_yield = (free_cf / market_cap) if free_cf is not None and market_cap not in (None, 0) else None
    fcf_margin = (free_cf / revenue) if free_cf is not None and revenue not in (None, 0) else None
    cfo_to_ni = (op_cf / net_income) if op_cf is not None and net_income not in (None, 0) else None

    # Growth
    def _g(c: Optional[float], p: Optional[float]) -> Optional[float]:
        if c is None or p in (None, 0):
            return None
        return (c - p) / abs(p)

    rev_growth = _g(revenue, revenue_prev)
    rev_growth = rev_growth if rev_growth is not None else _safe_num(info.get("revenueGrowth"))
    eps_growth = _g(net_income, net_income_prev)
    eps_growth = eps_growth if eps_growth is not None else _safe_num(info.get("earningsGrowth"))
    ebit_growth = _g(ebitda, ebitda_prev_val)

    # Working capital & ROIC
    wc = (cur_assets - cur_liab) if cur_assets is not None and cur_liab is not None else None
    tax_rate = 0.20  # Turkish corporate tax rate default
    # Try to compute effective tax rate from statements
    tax_exp = _stmt_val(inc_stmts, "incomeTaxExpense")
    pretax = _stmt_val(inc_stmts, "incomeBeforeTax")
    if tax_exp is not None and pretax not in (None, 0):
        computed_tax = abs(tax_exp) / abs(pretax)
        if 0 < computed_tax < 0.5:
            tax_rate = computed_tax
    inv_cap = (total_debt + equity - cash) if total_debt is not None and equity is not None and cash is not None else None
    nopat = ((_ev or 0) * (1 - min(max(tax_rate, 0), 0.35))) if _ev else None
    roic = (nopat / inv_cap) if nopat is not None and inv_cap not in (None, 0) else None

    # Valuation extras
    peg = (pe / (eps_growth * 100)) if pe not in (None, 0) and eps_growth is not None and eps_growth > 0.01 else None
    graham_fv = ((22.5 * trailing_eps * book_val_ps) ** 0.5) if trailing_eps and book_val_ps and trailing_eps > 0.5 and book_val_ps > 0.5 else None
    mos = ((graham_fv - price) / graham_fv) if graham_fv not in (None, 0) and price else None
    asset_to = (revenue / total_assets) if revenue and total_assets not in (None, 0) else None
    asset_to_p = (revenue_prev / total_assets_prev) if revenue_prev and total_assets_prev not in (None, 0) else None
    foreign_ratio = _safe_num(fast.get("foreign_ratio"))

    # EV/EBITDA fallback
    if ev_ebitda is None and market_cap and ebitda not in (None, 0):
        ev_ebitda = (market_cap + (total_debt or 0) - (cash or 0)) / ebitda

    # Data quality diagnostics
    has_income = len(inc_stmts) > 0
    has_balance = len(bal_stmts) > 0
    has_cashflow = len(cf_stmts) > 0
    info_fallbacks_used: list[str] = []
    if not has_income and (op_margin is not None or net_margin is not None or rev_growth is not None):
        info_fallbacks_used.append("ratios_from_info")
    if not has_income and (rev_growth is not None or eps_growth is not None):
        info_fallbacks_used.append("growth_from_info")

    data_quality = {
        "income_stmt": has_income,
        "balance_sheet": has_balance,
        "cashflow": has_cashflow,
        "fast_info": bool(price),
        "info_fallbacks": info_fallbacks_used,
    }
    stmt_count = sum([has_income, has_balance, has_cashflow])
    if stmt_count == 0:
        log.warning(f"DATA QUALITY [{tc}]: No financial statements from EODHD — using highlights only")
    elif stmt_count < 3:
        missing = [s for s, ok in [("income", has_income), ("balance", has_balance), ("cashflow", has_cashflow)] if not ok]
        log.info(f"DATA QUALITY [{tc}]: Missing {', '.join(missing)} statement(s)")

    return {
        "symbol": symbol, "ticker": tc,
        "name": str(info.get("shortName") or info.get("longName") or tc),
        "currency": str(info.get("currency") or "TRY"),
        "sector": str(info.get("sector") or ""),
        "industry": str(info.get("industry") or ""),
        "price": price, "market_cap": market_cap,
        "pe": pe, "pb": pb, "ev_ebitda": ev_ebitda,
        "dividend_yield": div_yield, "beta": beta,
        "revenue": revenue, "revenue_prev": revenue_prev,
        "gross_profit": gross_profit, "gross_profit_prev": gross_profit_prev,
        "operating_income": operating_income, "ebit": ebit or operating_income,
        "ebitda": ebitda, "ebitda_prev": ebitda_prev_val,
        "net_income": net_income, "net_income_prev": net_income_prev,
        "operating_cf": op_cf, "free_cf": free_cf,
        "total_assets": total_assets, "total_assets_prev": total_assets_prev,
        "total_liabilities": total_liab,
        "total_debt": total_debt, "total_debt_prev": total_debt_prev,
        "cash": cash,
        "current_assets": cur_assets, "current_assets_prev": cur_assets_prev,
        "current_liabilities": cur_liab, "current_liabilities_prev": cur_liab_prev,
        "working_capital": wc, "retained_earnings": ret_earn, "equity": equity,
        "receivables": receivables, "receivables_prev": rec_prev,
        "ppe": ppe, "ppe_prev": ppe_prev,
        "depreciation": dep, "depreciation_prev": dep_prev,
        "sga": sga, "sga_prev": sga_prev,
        "trailing_eps": trailing_eps, "book_value_ps": book_val_ps,
        "roe": roe, "roa": roa, "roa_prev": roa_prev, "roic": roic,
        "gross_margin": gross_margin, "gross_margin_prev": gross_margin_prev,
        "operating_margin": op_margin, "net_margin": net_margin,
        "current_ratio": cur_ratio, "current_ratio_prev": cur_ratio_prev,
        "debt_equity": debt_eq, "net_debt_ebitda": net_debt_ebit,
        "interest_coverage": int_cov,
        "fcf_yield": fcf_yield, "fcf_margin": fcf_margin, "cfo_to_ni": cfo_to_ni,
        "revenue_growth": rev_growth, "eps_growth": eps_growth,
        "ebitda_growth": ebit_growth,
        "peg": peg, "graham_fv": graham_fv, "margin_safety": mos,
        "share_change": None,
        "asset_turnover": asset_to, "asset_turnover_prev": asset_to_p,
        "inst_holders_pct": foreign_ratio, "foreign_ratio": foreign_ratio,
        "free_float": _safe_num(fast.get("free_float")),
        "ciro_pd": (revenue / market_cap) if revenue is not None and market_cap not in (None, 0) else None,
        "data_source": "eodhd",
        "data_quality": data_quality,
    }


# ================================================================
# EODHD BULK PRICES — Tüm BIST hisseleri tek çağrıda
# ================================================================
_bulk_cache: dict[str, dict] = {}
_bulk_cache_ts: float = 0.0
_BULK_CACHE_TTL = 900  # 15 dakika


def fetch_bulk_prices() -> dict[str, dict]:
    """
    EODHD eod-bulk-last-day/IS endpoint'i ile tüm BIST hisselerinin
    son gün fiyat verisini tek çağrıda çek.
    Returns: {ticker: {open, high, low, close, volume, adjusted_close, ...}}
    """
    import time
    global _bulk_cache, _bulk_cache_ts

    now = time.monotonic()
    if _bulk_cache and (now - _bulk_cache_ts) < _BULK_CACHE_TTL:
        return _bulk_cache

    try:
        cb_eodhd.before_call()
        data = _eodhd_get("eod-bulk-last-day/IS", timeout=60)
        if not isinstance(data, list):
            return _bulk_cache or {}

        result: dict[str, dict] = {}
        for item in data:
            code = item.get("code", "")
            if code:
                result[code] = item

        cb_eodhd.on_success()
        _bulk_cache = result
        _bulk_cache_ts = now
        log.info(f"Bulk prices: {len(result)} hisse çekildi (EODHD)")
        return result

    except CircuitBreakerOpen:
        return _bulk_cache or {}
    except Exception as e:
        cb_eodhd.on_failure(e)
        log.error(f"Bulk prices error: {e}")
        return _bulk_cache or {}


# ================================================================
# HISTORICAL OHLCV — Tek hisse için fiyat geçmişi
# ================================================================
def fetch_eod_history(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
) -> Optional[pd.DataFrame]:
    """
    EODHD eod endpoint'i ile tek hissenin fiyat geçmişini çek.
    Returns: pandas DataFrame (Date index, OHLCV columns) veya None.
    """
    tc = symbol.upper().replace(".IS", "").replace(".E", "")
    eodhd_symbol = f"{tc}.IS"

    # Period → from date hesapla
    now = _dt.datetime.now()
    period_days = {
        "1y": 365, "6mo": 183, "3mo": 92, "1mo": 31,
        "5d": 5, "2y": 730, "5y": 1825, "max": 7300,
    }
    days = period_days.get(period, 365)
    from_date = (now - _dt.timedelta(days=days)).strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")

    # EODHD period param → d, w, m
    eodhd_period = "d"
    if interval in ("1wk", "weekly"):
        eodhd_period = "w"
    elif interval in ("1mo", "monthly"):
        eodhd_period = "m"

    try:
        data = _eodhd_get(f"eod/{eodhd_symbol}", {
            "from": from_date,
            "to": to_date,
            "period": eodhd_period,
        })

        if not data or not isinstance(data, list):
            return None

        df = pd.DataFrame(data)
        if df.empty:
            return None

        # Rename columns to match yfinance format
        col_map = {
            "date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "adjusted_close": "Adj Close",
            "volume": "Volume",
        }
        df = df.rename(columns=col_map)
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")

        # Ensure numeric types
        for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df if len(df) >= 2 else None

    except Exception as e:
        log.debug(f"EOD history {tc}: {e}")
        return None


# ================================================================
# BATCH DOWNLOAD HISTORY V9 — EODHD ile toplu fiyat geçmişi
# ================================================================
def batch_download_history_v9(
    symbols: list[str],
    period: str = "1y",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """
    EODHD ile toplu fiyat geçmişi çek.
    Her hisse paralel thread ile çekilir.
    CB korumalı.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        cb_eodhd.before_call()
    except CircuitBreakerOpen:
        log.warning("batch_history: EODHD CB OPEN, skip")
        return {}

    result: dict[str, pd.DataFrame] = {}

    def _fetch_one(sym: str) -> tuple[str, Optional[pd.DataFrame]]:
        try:
            df = fetch_eod_history(sym, period=period, interval=interval)
            if df is not None and len(df) >= 20:
                return sym, df
        except Exception:
            pass
        return sym, None

    try:
        workers = min(BATCH_HISTORY_WORKERS, len(symbols), 20)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_fetch_one, s) for s in symbols]
            for future in as_completed(futures):
                sym, df = future.result()
                if df is not None:
                    result[sym] = df
        cb_eodhd.on_success()
        log.info(f"batch_history (EODHD): {len(result)}/{len(symbols)} başarılı")
    except Exception as e:
        cb_eodhd.on_failure(e)
        log.error(f"batch_history error: {e}")

    return result


# ================================================================
# BACKWARD COMPATIBILITY — eski import'lar çalışsın
# ================================================================
BORSAPY_AVAILABLE = True  # EODHD her zaman mevcut (API key varsa)
EODHD_AVAILABLE = bool(EODHD_API_KEY)

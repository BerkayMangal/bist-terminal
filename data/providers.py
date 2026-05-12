# ================================================================
# BISTBULL TERMINAL V10.0 — DATA PROVIDERS
# borsapy (İş Yatırım/KAP) üzerinden gerçek BIST verisi çeker.
# V9.1 data_layer_v9.py birebir korunmuş + V10 değişiklikleri:
# - Circuit Breaker sarmalı (cb_borsapy)
# - Import path'ler güncellendi (core.cache, utils.helpers)
# - Veri kalitesi metadata eklendi
# ================================================================

from __future__ import annotations

import math
import logging
import re
import datetime as _dt
from typing import Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from core.cache import raw_cache
from core.circuit_breaker import cb_borsapy, CircuitBreakerOpen
from config import BATCH_HISTORY_WORKERS

log = logging.getLogger("bistbull.data")

try:
    import borsapy as bp
    BORSAPY_AVAILABLE = True
except ImportError:
    bp = None  # type: ignore
    BORSAPY_AVAILABLE = False

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
# GERÇEK İSYATIRIM SATIR İSİMLERİ
# ================================================================
BS_MAP: dict[str, list[str]] = {
    "total_assets": ["TOPLAM VARLIKLAR"],
    "current_assets": ["Dönen Varlıklar"],
    "cash": ["Nakit ve Nakit Benzerleri"],
    "receivables": ["Ticari Alacaklar"],
    "ppe": ["Maddi Duran Varlıklar"],
    "current_liabilities": ["Kısa Vadeli Yükümlülükler"],
    "long_term_liabilities": ["Uzun Vadeli Yükümlülükler"],
    "equity": ["Ana Ortaklığa Ait Özkaynaklar", "Özkaynaklar"],
    "retained_earnings": ["Geçmiş Yıllar Kar/Zararları"],
    "total_sources": ["TOPLAM KAYNAKLAR"],
}

IS_MAP: dict[str, list[str]] = {
    "revenue": ["Satış Gelirleri"],
    "gross_profit": ["BRÜT KAR (ZARAR)", "Ticari Faaliyetlerden Brüt Kar (Zarar)"],
    "operating_income": ["FAALİYET KARI (ZARARI)"],
    "ebit_before_finance": ["Finansman Gideri Öncesi Faaliyet Karı/Zararı"],
    "financial_expense": ["(Esas Faaliyet Dışı) Finansal Giderler (-)"],
    "net_income": ["DÖNEM KARI (ZARARI)", "SÜRDÜRÜLEN FAALİYETLER DÖNEM KARI/ZARARI"],
    "net_income_parent": ["Ana Ortaklık Payları"],
    "sga": ["Genel Yönetim Giderleri (-)"],
    "tax_expense": ["Sürdürülen Faaliyetler Vergi Geliri (Gideri)"],
}

CF_MAP: dict[str, list[str]] = {
    "operating_cf": ["İşletme Faaliyetlerinden Kaynaklanan Net Nakit"],
    "capex": ["Sabit Sermaye Yatırımları"],
    "depreciation": ["Amortisman & İtfa Payları", "Amortisman Giderleri"],
    "free_cf": ["Serbest Nakit Akım"],
}


# ================================================================
# SMART PICK UTILITIES
# ================================================================
def _safe_num(x: Any) -> Optional[float]:
    """Güvenli float dönüşüm. None/NaN/Inf → None."""
    try:
        if x is None:
            return None
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _norm(s: Any) -> str:
    """Robust whitespace normalization — \\xa0, double space, strip."""
    if not isinstance(s, str):
        return ""
    return re.sub(r'\s+', ' ', s.replace('\xa0', ' ')).strip()


def _is_empty_frame(df: Any) -> bool:
    """Phase A.10 Step 2-A.1: defensive guard for borsapy responses.

    borsapy occasionally returns a string (rate-limit or error message)
    instead of a DataFrame for income/balance/cashflow statements. Naive
    `df.empty` checks then raise AttributeError → '/api/bullwatch/{sym}'
    returns 502 before override or partial-data layers can run.

    Returns True when:
      - df is None
      - df is not a pandas-DataFrame-like object (no `.empty` attr)
      - df is empty (no rows or no columns)

    Returns False ONLY when df is a real, non-empty DataFrame.
    """
    if df is None:
        return True
    if not hasattr(df, "empty"):
        return True
    try:
        return bool(df.empty)
    except Exception:
        return True


def _find_data_col(df: Optional[pd.DataFrame]) -> int:
    """İlk gerçek veri kolonu (2025 boşsa skip)."""
    if _is_empty_frame(df):
        return 0
    for ci in range(len(df.columns)):
        non_zero = sum(1 for val in df.iloc[:, ci] if _safe_num(val) not in (None, 0))
        if non_zero >= 3:
            return ci
    return 0


def _pick(
    df: Optional[pd.DataFrame],
    names: list[str],
    offset: int = 0,
    base: Optional[int] = None,
) -> Optional[float]:
    """DataFrame'den satır ismine göre değer çek."""
    if _is_empty_frame(df):
        return None
    if base is None:
        base = _find_data_col(df)
    ci = base + offset
    if ci >= len(df.columns):
        return None
    # Exact match first (iloc — duplicate index safe)
    for name in names:
        ns = _norm(name).lower()
        for ri, idx in enumerate(df.index):
            if _norm(idx).lower() == ns:
                v = _safe_num(df.iloc[ri, ci])
                if v is not None:
                    return v
    # Partial match fallback
    for name in names:
        ns = _norm(name).lower()
        for ri, idx in enumerate(df.index):
            if ns in _norm(idx).lower():
                v = _safe_num(df.iloc[ri, ci])
                if v is not None:
                    return v
    return None


def _pair(
    df: Optional[pd.DataFrame],
    names: list[str],
) -> tuple[Optional[float], Optional[float]]:
    """Cari ve önceki dönem değerlerini çek."""
    if _is_empty_frame(df):
        return None, None
    b = _find_data_col(df)
    return _pick(df, names, 0, b), _pick(df, names, 1, b)


def _quarterly_series(
    df: Optional[pd.DataFrame],
    names: list[str],
    n: int = 8,
) -> list[Optional[float]]:
    """Pull up to N quarters of values for a row name, starting from the
    most recent column with real data. Returns a fixed-length list; missing
    cells are None.

    Quarterly column order from borsapy is newest-first
    (e.g. ['2025Q4', '2025Q3', ..., '2024Q1']), so series[0] is the
    latest reported quarter, series[4] is the same quarter one year
    earlier (used for YoY-Q growth).
    """
    if _is_empty_frame(df):
        return [None] * n
    base = _find_data_col(df)
    ncols = len(df.columns)
    return [
        _pick(df, names, offset=i, base=base) if (base + i) < ncols else None
        for i in range(n)
    ]


def _compute_quarterly_aggregates(
    fin_q: Optional[pd.DataFrame],
) -> dict[str, Any]:
    """From an 8-quarter income statement, compute YTD year-over-year
    growth metrics.

    borsapy's quarterly columns are CUMULATIVE YEAR-TO-DATE values (e.g.
    "2025Q3" = revenue for Jan–Sep 2025, not just Q3 alone). Verified by
    cross-checking: 2025Q4 cumulative == 2025 annual figure exactly.
    So:
      revenue_ytd       = latest cumulative YTD value
      revenue_ytd_prev  = same quarter from previous year (cumulative)
      revenue_growth_yoy_q = (ytd - ytd_prev) / ytd_prev
                           — when latest is Q4, this is full-year YoY.
                           — when latest is Q3 (mid-year), this is
                             "first 9 months YoY" — gives a fresher signal
                             than the annual figures that only update on
                             year-end reporting.

    True TTM (trailing-12-month standalone) would need a
    cumulative→standalone conversion; deferred to a follow-up.

    Plan B v1 — additive: existing annual fields are not touched.
    """
    out: dict[str, Any] = {
        "quarterly_data_available": False,
        "latest_quarter": None,
    }
    if _is_empty_frame(fin_q):
        return out

    base = _find_data_col(fin_q)
    cols = list(fin_q.columns)
    if base >= len(cols):
        return out
    out["latest_quarter"] = str(cols[base])
    out["quarterly_data_available"] = True

    fields = [
        ("revenue", IS_MAP["revenue"]),
        ("net_income", IS_MAP["net_income"]),
        ("operating_income", IS_MAP["operating_income"]),
    ]
    any_data = False
    for metric, names in fields:
        series = _quarterly_series(fin_q, names, n=8)
        ytd = series[0]               # cumulative through latest reported quarter
        ytd_prev = series[4]          # same quarter cumulative one year earlier
        growth = (
            (ytd - ytd_prev) / abs(ytd_prev)
            if ytd is not None and ytd_prev not in (None, 0)
            else None
        )
        out[f"{metric}_ytd"] = ytd
        out[f"{metric}_ytd_prev"] = ytd_prev
        out[f"{metric}_growth_yoy_q"] = growth
        if growth is not None:
            any_data = True

    if not any_data:
        # Got columns but no row-name matched (e.g. bank-format rows when
        # caller expected non-bank schema). Demote the availability flag
        # so callers can fall back to annual.
        out["quarterly_data_available"] = False
    return out


def _pick_debt(
    bal: Optional[pd.DataFrame],
) -> tuple[Optional[float], Optional[float]]:
    """Kısa + Uzun vadeli Finansal Borçlar (aynı isim, farklı section)."""
    if _is_empty_frame(bal):
        return None, None
    b = _find_data_col(bal)
    ci0 = b
    ci1 = b + 1 if b + 1 < len(bal.columns) else None
    sd: Optional[float] = None
    ld: Optional[float] = None
    sdp: Optional[float] = None
    ldp: Optional[float] = None
    in_short = False
    in_long = False
    for ri, idx in enumerate(bal.index):
        n = _norm(idx)
        if "Kısa Vadeli Yükümlülükler" in n and "Ara Toplam" not in n:
            in_short, in_long = True, False
        elif "Uzun Vadeli Yükümlülükler" in n:
            in_short, in_long = False, True
        elif "Özkaynaklar" in n:
            in_short = in_long = False
        if "Finansal Borçlar" in n and "Diğer" not in n:
            if in_short and sd is None:
                sd = _safe_num(bal.iloc[ri, ci0])
                if ci1 is not None:
                    sdp = _safe_num(bal.iloc[ri, ci1])
            elif in_long and ld is None:
                ld = _safe_num(bal.iloc[ri, ci0])
                if ci1 is not None:
                    ldp = _safe_num(bal.iloc[ri, ci1])
    total = ((sd or 0) + (ld or 0)) if sd is not None or ld is not None else None
    total_prev = ((sdp or 0) + (ldp or 0)) if sdp is not None or ldp is not None else None
    return total, total_prev


# ================================================================
# FETCH RAW V9 — SafeCache + Circuit Breaker entegreli
# ================================================================
# HOTFIX 1 (2026-Q2 production incident): ~23% of symbols (25/108)
# were failing this call. Root cause investigation showed:
#   1. Empty-string exception messages (no-args Exception, some
#      borsapy internal errors) made the "fetch_raw failed for X:"
#      log line look blank, making triage impossible.
#   2. No retry → any transient borsapy/TradingView rate-limit killed
#      that symbol for the whole scan cycle.
#   3. CB threshold=50 means 25 fails doesn't trip it (confirmed not
#      a cascade).
# Fix: log exception type + repr + exc_info; add retry-with-backoff
# around the ThreadPoolExecutor block. CB interaction preserved.
# Phase A.10 Step 2-B.1: lighter retry profile.
# Pre-2-B.1: 3 attempts × (0.5s, 1.0s, 2.0s) backoff = up to 3.5s blocked
#            per failed symbol (12 symbols × 3.5s ≈ 42s of pool blocked).
# Post-2-B.1: 2 attempts × 0.7s backoff = max 0.7s per failed symbol.
# Provider failures still surface (no exception swallowing), they just
# fail faster so the scan loop can move on. Stale-while-revalidate
# (Step 2-B) ensures users don't see 502s — the failed symbol returns
# stale data if cache exists, otherwise data_status=missing.
FETCH_RAW_MAX_ATTEMPTS = 2
FETCH_RAW_BACKOFF_SEC = (0.3, 0.7)   # per-attempt sleep before retry


def fetch_raw_v9(symbol: str) -> dict:
    """borsapy ile ham veri çek — paralel HTTP. Thread-safe SafeCache + CB."""
    if not BORSAPY_AVAILABLE:
        raise ImportError("borsapy yok")

    cached = raw_cache.get(symbol)
    if cached is not None:
        # Finansal veriler cache'te → sadece fiyat güncelle (günde 1x bilanço yeter)
        try:
            tc_ = symbol.upper().replace('.IS', '').replace('.E', '')
            tk_ = bp.Ticker(tc_)
            fi_ = tk_.fast_info
            lp_ = getattr(fi_, 'last_price', None)
            if lp_ is not None:
                cached['fast']['last_price'] = lp_
                cached['fast']['volume'] = getattr(fi_, 'volume', cached['fast'].get('volume'))
                cached['fast']['market_cap'] = getattr(fi_, 'market_cap', cached['fast'].get('market_cap'))
                raw_cache.set(symbol, cached)
        except Exception:
            pass  # Fiyat güncellenemezse eski cache dön
        return cached

    # Circuit Breaker kontrolü — borsapy devre dışıysa hemen hata fırlat
    cb_borsapy.before_call()

    tc = symbol.upper().replace(".IS", "").replace(".E", "")
    tk = bp.Ticker(tc)
    fg = "UFRS" if is_bank(tc) else None

    def _fast():
        d = {}
        try:
            fi = tk.fast_info
            for a in [
                "last_price", "open", "day_high", "day_low", "previous_close",
                "volume", "market_cap", "shares", "pe_ratio", "pb_ratio",
                "year_high", "year_low", "fifty_day_average", "two_hundred_day_average",
                "free_float", "foreign_ratio",
            ]:
                try:
                    d[a] = getattr(fi, a)
                except Exception:
                    d[a] = None
        except Exception as e:
            log.warning(f"fast_info {tc}: {type(e).__name__}: {e!r}")
        return d

    def _info():
        d = {}
        try:
            full = tk.info
            for k in [
                "sector", "industry", "shortName", "longName", "currency",
                "marketCap", "trailingPE", "forwardPE", "priceToBook",
                "enterpriseToEbitda", "dividendYield", "returnOnEquity",
                "returnOnAssets", "operatingMargins", "profitMargins",
                "currentRatio", "debtToEquity", "beta", "revenueGrowth",
                "earningsGrowth", "freeCashflow", "currentPrice", "trailingEps",
                "bookValue", "heldPercentInstitutions", "effectiveTaxRate",
            ]:
                try:
                    d[k] = full[k]
                except Exception:
                    d[k] = None
        except Exception:
            d = None
        return d

    def _income():
        try:
            return tk.get_income_stmt(quarterly=False, financial_group=fg, last_n=4)
        except Exception as e:
            log.warning(f"income {tc}: {type(e).__name__}: {e!r}")
            return None

    def _balance():
        try:
            return tk.get_balance_sheet(quarterly=False, financial_group=fg, last_n=4)
        except Exception as e:
            log.warning(f"balance {tc}: {type(e).__name__}: {e!r}")
            return None

    def _cashflow():
        try:
            return tk.get_cashflow(quarterly=False, financial_group=fg, last_n=4)
        except Exception as e:
            if not is_bank(tc):
                log.warning(f"cashflow {tc}: {type(e).__name__}: {e!r}")
            return None

    # Quarterly fetches (Plan B v1) — 8 trailing quarters for TTM and
    # year-over-year same-quarter growth metrics. Errors don't fail the
    # symbol; quarterly_data_available flag in metrics signals success.
    def _income_q():
        try:
            return tk.get_income_stmt(quarterly=True, financial_group=fg, last_n=8)
        except Exception as e:
            log.debug(f"income_q {tc}: {type(e).__name__}: {e!r}")
            return None

    def _balance_q():
        try:
            return tk.get_balance_sheet(quarterly=True, financial_group=fg, last_n=8)
        except Exception as e:
            log.debug(f"balance_q {tc}: {type(e).__name__}: {e!r}")
            return None

    def _cashflow_q():
        try:
            return tk.get_cashflow(quarterly=True, financial_group=fg, last_n=8)
        except Exception as e:
            log.debug(f"cashflow_q {tc}: {type(e).__name__}: {e!r}")
            return None

    last_exc: Optional[Exception] = None
    for attempt in range(FETCH_RAW_MAX_ATTEMPTS):
        if attempt > 0:
            sleep_sec = FETCH_RAW_BACKOFF_SEC[min(attempt, len(FETCH_RAW_BACKOFF_SEC) - 1)]
            log.info(
                f"fetch_raw_v9 {tc}: retry attempt {attempt + 1}/"
                f"{FETCH_RAW_MAX_ATTEMPTS} after {sleep_sec}s "
                f"(prev error: {type(last_exc).__name__ if last_exc else '?'})"
            )
            import time as _t
            _t.sleep(sleep_sec)

        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                f_fast = pool.submit(_fast)
                f_info = pool.submit(_info)
                f_fin = pool.submit(_income)
                f_bal = pool.submit(_balance)
                f_cf = pool.submit(_cashflow)
                f_fin_q = pool.submit(_income_q)
                f_bal_q = pool.submit(_balance_q)
                f_cf_q = pool.submit(_cashflow_q)
                fast = f_fast.result(timeout=30)
                info = f_info.result(timeout=30)
                fin = f_fin.result(timeout=15)
                bal = f_bal.result(timeout=15)
                cf = f_cf.result(timeout=15)
                # Quarterly results — soft-fail (return None) so they
                # never block the symbol on borsapy hiccups.
                try:
                    fin_q = f_fin_q.result(timeout=15)
                except Exception:
                    fin_q = None
                try:
                    bal_q = f_bal_q.result(timeout=15)
                except Exception:
                    bal_q = None
                try:
                    cf_q = f_cf_q.result(timeout=15)
                except Exception:
                    cf_q = None

            if info is None:
                info = {
                    "currentPrice": fast.get("last_price"),
                    "marketCap": fast.get("market_cap"),
                    "trailingPE": fast.get("pe_ratio"),
                    "priceToBook": fast.get("pb_ratio"),
                    "currency": "TRY",
                }

            raw = {
                "info": info, "fast": fast,
                "financials": fin, "balance": bal, "cashflow": cf,
                "financials_q": fin_q, "balance_q": bal_q, "cashflow_q": cf_q,
                "source": "borsapy", "ticker_clean": tc, "is_bank": is_bank(tc),
                "_fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "_fetch_attempts": attempt + 1,  # telemetry
            }

            raw_cache.set(symbol, raw)
            cb_borsapy.on_success()
            return raw

        except CircuitBreakerOpen:
            # Don't retry if CB is open — fail fast, let caller handle
            raise
        except Exception as e:
            last_exc = e
            # Don't retry on programmer errors; retry on transient I/O
            non_retriable = (TypeError, AttributeError, ImportError, KeyError)
            if isinstance(e, non_retriable):
                log.error(
                    f"fetch_raw_v9 {tc}: non-retriable "
                    f"{type(e).__name__}: {e!r}",
                    exc_info=True,
                )
                cb_borsapy.on_failure(e)
                break

    # All retries exhausted (or non-retriable). Log with full context
    # and fall back to stale cache if any.
    if last_exc is not None:
        log.warning(
            f"fetch_raw_v9 {tc}: all {FETCH_RAW_MAX_ATTEMPTS} attempts failed, "
            f"last error {type(last_exc).__name__}: {last_exc!r}",
            exc_info=True,
        )
        cb_borsapy.on_failure(last_exc)
    # HOTFIX: borsapy fail → stale cache fallback
    stale = raw_cache.get(symbol)
    if stale is not None:
        log.info(f"fetch_raw {symbol}: borsapy fail, stale cache kullanılıyor")
        return stale
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"fetch_raw_v9 {tc}: unreachable state, no exception + no cache")


# ================================================================
# COMPUTE METRICS V9 — SafeCache entegreli
# ================================================================
def compute_metrics_v9(symbol: str) -> dict:
    """borsapy ile Türkçe KAP verisi → metric dict. SafeCache kullanır."""
    raw = fetch_raw_v9(symbol)
    info = raw["info"]
    fast = raw["fast"]
    fin = raw["financials"]
    bal = raw["balance"]
    cf = raw["cashflow"]
    fin_q = raw.get("financials_q")  # Plan B v1 — quarterly data (optional)
    tc = raw["ticker_clean"]

    # Income statement
    revenue, revenue_prev = _pair(fin, IS_MAP["revenue"])
    gross_profit, gross_profit_prev = _pair(fin, IS_MAP["gross_profit"])
    operating_income, op_inc_prev = _pair(fin, IS_MAP["operating_income"])
    ebit = _pick(fin, IS_MAP["ebit_before_finance"]) or operating_income
    net_income, net_income_prev = _pair(fin, IS_MAP["net_income"])
    if not net_income:
        ni2, nip2 = _pair(fin, IS_MAP["net_income_parent"])
        if ni2:
            net_income, net_income_prev = ni2, nip2
    interest_exp = _pick(fin, IS_MAP["financial_expense"])
    sga, sga_prev = _pair(fin, IS_MAP["sga"])

    # Cashflow
    dep = _pick(cf, CF_MAP["depreciation"])
    dep_prev = _pick(cf, CF_MAP["depreciation"], 1)
    ebitda = ((ebit or operating_income or 0) + abs(dep)) if dep and (ebit or operating_income) else None
    ebitda_prev = ((op_inc_prev or 0) + abs(dep_prev)) if dep_prev and op_inc_prev else None
    op_cf, _ = _pair(cf, CF_MAP["operating_cf"])
    capex = _pick(cf, CF_MAP["capex"])
    free_cf_direct, _ = _pair(cf, CF_MAP["free_cf"])

    # Balance sheet
    total_assets, total_assets_prev = _pair(bal, BS_MAP["total_assets"])
    total_src, _ = _pair(bal, BS_MAP["total_sources"])
    equity, _ = _pair(bal, BS_MAP["equity"])
    total_liab = (total_src - equity) if total_src and equity else None
    total_debt, total_debt_prev = _pick_debt(bal)
    cash, _ = _pair(bal, BS_MAP["cash"])
    cur_assets, cur_assets_prev = _pair(bal, BS_MAP["current_assets"])
    cur_liab, cur_liab_prev = _pair(bal, BS_MAP["current_liabilities"])
    ret_earn, _ = _pair(bal, BS_MAP["retained_earnings"])
    receivables, rec_prev = _pair(bal, BS_MAP["receivables"])
    ppe, ppe_prev = _pair(bal, BS_MAP["ppe"])

    # Market data
    price = _safe_num(fast.get("last_price"))
    if price is None: price = _safe_num(info.get("currentPrice"))
    market_cap = _safe_num(fast.get("market_cap"))
    if market_cap is None: market_cap = _safe_num(info.get("marketCap"))
    pe = _safe_num(fast.get("pe_ratio"))
    pe = pe if pe is not None else _safe_num(info.get("trailingPE"))
    pb = _safe_num(fast.get("pb_ratio"))
    pb = pb if pb is not None else _safe_num(info.get("priceToBook"))
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

    # Ratios — financial statement derived, with info-dict fallback
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
    cur_ratio = _safe_num(info.get("currentRatio"))
    cur_ratio = cur_ratio if cur_ratio is not None else ((cur_assets / cur_liab) if cur_assets is not None and cur_liab not in (None, 0) and cur_liab > 0 else None)
    cur_ratio_prev = (cur_assets_prev / cur_liab_prev) if cur_assets_prev and cur_liab_prev else None
    debt_eq = _safe_num(info.get("debtToEquity"))
    debt_eq = debt_eq if debt_eq is not None else ((total_debt / equity * 100) if total_debt is not None and equity not in (None, 0) and abs(equity) > 1e4 else None)
    net_debt = (total_debt - cash) if total_debt is not None and cash is not None else None
    net_debt_ebit = (net_debt / ebitda) if net_debt is not None and ebitda not in (None, 0) else None
    _ev = ebit if ebit is not None else operating_income
    int_cov = (_ev / abs(interest_exp)) if _ev is not None and interest_exp not in (None, 0) else None
    _fcf_c = (op_cf + capex) if op_cf is not None and capex is not None else None
    free_cf = free_cf_direct if free_cf_direct is not None else (_fcf_c if _fcf_c is not None else _safe_num(info.get("freeCashflow")))
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
    ebit_growth = _g(ebitda, ebitda_prev)

    # Working capital & ROIC
    wc = (cur_assets - cur_liab) if cur_assets is not None and cur_liab is not None else None
    tax_rate = _safe_num(info.get("effectiveTaxRate"))
    tax_rate = tax_rate if tax_rate is not None else 0.20
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
    has_income = fin is not None and hasattr(fin, 'empty') and not fin.empty
    has_balance = bal is not None and hasattr(bal, 'empty') and not bal.empty
    has_cashflow = cf is not None and hasattr(cf, 'empty') and not cf.empty
    info_fallbacks_used = []
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
        log.warning(f"DATA QUALITY [{tc}]: No financial statements — using market data + info fallbacks only")
    elif stmt_count < 3:
        missing = [s for s, ok in [("income", has_income), ("balance", has_balance), ("cashflow", has_cashflow)] if not ok]
        log.info(f"DATA QUALITY [{tc}]: Missing {', '.join(missing)} statement(s)")

    # Phase A.10 Step 2-A: field-level source tagging.
    #
    # We tag each major field with WHERE it would have come from. This
    # is coarse — it doesn't track whether the actual returned value is
    # None or fell back through a different path inside fast/info. But it
    # gives the diagnostics endpoint enough granularity to answer
    # "for symbol X, why is free_float missing?" by looking at which
    # source was supposed to supply it. Manual overrides will overwrite
    # these labels in `_apply_overrides`.
    _field_sources = {
        # Market data — first try fast_info, fall back to info dict
        "price": "borsapy.fast_info" if fast.get("last_price") is not None else "borsapy.info",
        "market_cap": "borsapy.fast_info" if fast.get("market_cap") is not None else "borsapy.info",
        "shares": "borsapy.fast_info" if fast.get("shares") is not None else (
            "derived_market_cap_over_price" if shares is not None else "missing"
        ),
        "pe": "borsapy.fast_info" if fast.get("pe_ratio") is not None else "borsapy.info",
        "pb": "borsapy.fast_info" if fast.get("pb_ratio") is not None else "borsapy.info",
        # BIST-specific — only fast_info supplies these
        "free_float": "borsapy.fast_info" if fast.get("free_float") is not None else "missing",
        "foreign_ratio": "borsapy.fast_info" if fast.get("foreign_ratio") is not None else "missing",
        # Sector/industry — info dict only
        "sector": "borsapy.info" if info.get("sector") else "missing",
        "industry": "borsapy.info" if info.get("industry") else "missing",
        # Fundamentals — borsapy income statement (UFRS-grouped)
        "revenue": "borsapy.income_stmt_ufrs" if revenue is not None else "missing",
        "net_income": "borsapy.income_stmt_ufrs" if net_income is not None else "missing",
        "operating_income": "borsapy.income_stmt_ufrs" if operating_income is not None else "missing",
        "ebitda": "derived_income_plus_dep" if ebitda is not None else "missing",
        "total_assets": "borsapy.balance_sheet" if total_assets is not None else "missing",
        "total_debt": "borsapy.balance_sheet" if total_debt is not None else "missing",
        "equity": "borsapy.balance_sheet" if equity is not None else "missing",
        "operating_cf": "borsapy.cashflow" if op_cf is not None else "missing",
        # ohlcv source set later by batch_download_history caller
    }

    # Plan B v1 — quarterly aggregates (TTM, YoY-Q). Additive: existing
    # annual fields (revenue_growth, eps_growth, ...) are untouched so
    # nothing downstream needs to change. Scoring layer wires these in
    # in Plan B v2.
    q_aggs = _compute_quarterly_aggregates(fin_q)

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
        "ebitda": ebitda, "ebitda_prev": ebitda_prev,
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
        # Phase A.10 Step 2-A.1 Fix A: shares was computed (line ~448) and
        # tagged in _field_sources but never made it to the returned dict.
        # That caused data_status to flag it as 'missing' for every symbol.
        "shares": shares,
        "free_float": _safe_num(fast.get("free_float")),
        "ciro_pd": (revenue / market_cap) if revenue is not None and market_cap not in (None, 0) else None,
        "data_source": "borsapy",
        "data_quality": data_quality,
        # Phase A.10 Step 2-A: source tagging (additive, doesn't change
        # any existing field). May be updated by _apply_overrides.
        "_field_sources": _field_sources,
        # Plan B v1 — quarterly aggregates merged in. Keys:
        #   quarterly_data_available, latest_quarter,
        #   revenue_ytd, revenue_ytd_prev, revenue_growth_yoy_q,
        #   net_income_*, operating_income_*.
        # Cumulative YTD semantics — when latest_quarter is mid-year
        # (e.g. "2025Q3"), growth_yoy_q is the freshest signal you can
        # get because the annual fields only update on year-end reports.
        **q_aggs,
    }


# ================================================================
# BATCH DOWNLOAD HISTORY V9 — CB entegreli
# ================================================================
def batch_download_history_v9(
    symbols: list[str],
    period: str = "1y",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """borsapy ile toplu price history. Chunk+retry+cache fallback."""
    if not BORSAPY_AVAILABLE:
        return {}
    try:
        cb_borsapy.before_call()
    except CircuitBreakerOpen:
        log.warning("batch_history: borsapy CB OPEN, skip")
        return {}

    result: dict[str, pd.DataFrame] = {}
    period_map = {
        "1y": "1y", "6mo": "6ay", "3mo": "3ay", "1mo": "1ay",
        "5d": "5g", "1d": "1g", "max": "max",
    }
    bp_period = period_map.get(period, period)

    def _fetch_one(sym: str) -> tuple[str, Optional[pd.DataFrame]]:
        tc = sym.upper().replace(".IS", "").replace(".E", "")
        try:
            tk = bp.Ticker(tc)
            df = tk.history(period=bp_period, interval=interval)
            if not _is_empty_frame(df) and len(df) >= 20:
                return sym, df
        except Exception:
            pass
        return sym, None

    import time as _time
    CHUNK = 25
    WORKERS = min(BATCH_HISTORY_WORKERS, 5)
    failed: list[str] = []

    # Pass 1: Chunk halinde indir
    for ci in range(0, len(symbols), CHUNK):
        chunk = symbols[ci:ci+CHUNK]
        try:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futs = [pool.submit(_fetch_one, s) for s in chunk]
                for fut in as_completed(futs):
                    try:
                        sym, df = fut.result(timeout=30)
                        if df is not None:
                            result[sym] = df
                        else:
                            failed.append(sym)
                    except Exception:
                        pass
        except Exception:
            failed.extend(chunk)
        if ci + CHUNK < len(symbols):
            _time.sleep(1.0)

    # Pass 2: Retry başarısızlar
    if failed:
        log.info(f"batch_history retry: {len(failed)} sembol")
        _time.sleep(2.0)
        still_failed = []
        for ci in range(0, len(failed), CHUNK):
            chunk = failed[ci:ci+CHUNK]
            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futs = [pool.submit(_fetch_one, s) for s in chunk]
                    for fut in as_completed(futs):
                        try:
                            sym, df = fut.result(timeout=30)
                            if df is not None:
                                result[sym] = df
                            else:
                                still_failed.append(sym)
                        except Exception:
                            pass
            except Exception:
                still_failed.extend(chunk)
            _time.sleep(1.5)
        failed = still_failed

    # Pass 3: Cache fallback
    if failed:
        from core.cache import history_cache
        recovered = 0
        for sym in failed:
            cached = history_cache.get(sym)
            if cached is not None and len(cached) >= 20:
                result[sym] = cached
                recovered += 1
        if recovered:
            log.info(f"batch_history cache fallback: {recovered}/{len(failed)} kurtarıldı")

    if result:
        cb_borsapy.on_success()
    log.info(f"batch_history: {len(result)}/{len(symbols)} başarılı")
    return result


    def _fetch_one(sym: str) -> tuple[str, Optional[pd.DataFrame]]:
        tc = sym.upper().replace(".IS", "").replace(".E", "")
        try:
            tk = bp.Ticker(tc)
            df = tk.history(period=bp_period, interval=interval)
            if not _is_empty_frame(df) and len(df) >= 20:
                return sym, df
        except Exception:
            pass
        return sym, None

    import time as _time

    # ── CHUNK + RETRY STRATEJİSİ ──
    CHUNK_SIZE = 25
    MAX_WORKERS = min(BATCH_HISTORY_WORKERS, 5)  # Max 5 eşzamanlı (rate limit)

    all_symbols = list(symbols)  # Kopyala
    failed_symbols: list[str] = []

    # Pass 1: Chunk'lar halinde indir
    for chunk_start in range(0, len(all_symbols), CHUNK_SIZE):
        chunk = all_symbols[chunk_start:chunk_start + CHUNK_SIZE]
        try:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = [pool.submit(_fetch_one, s) for s in chunk]
                for future in as_completed(futures):
                    try:
                        sym, df = future.result(timeout=30)
                        if df is not None:
                            result[sym] = df
                        else:
                            failed_symbols.append(sym)
                    except Exception:
                        pass
        except Exception as e:
            log.warning(f"batch_history chunk error: {e}")
            failed_symbols.extend(chunk)

        # Chunk arası bekleme — rate limit koruması
        if chunk_start + CHUNK_SIZE < len(all_symbols):
            _time.sleep(1.0)

    # Pass 2: Başarısız semboller için RETRY (2s backoff)
    if failed_symbols:
        log.info(f"batch_history retry: {len(failed_symbols)} sembol tekrar deneniyor")
        _time.sleep(2.0)
        still_failed: list[str] = []
        for chunk_start in range(0, len(failed_symbols), CHUNK_SIZE):
            chunk = failed_symbols[chunk_start:chunk_start + CHUNK_SIZE]
            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [pool.submit(_fetch_one, s) for s in chunk]
                    for future in as_completed(futures):
                        try:
                            sym, df = future.result(timeout=30)
                            if df is not None:
                                result[sym] = df
                            else:
                                still_failed.append(sym)
                        except Exception:
                            pass
            except Exception:
                still_failed.extend(chunk)
            if chunk_start + CHUNK_SIZE < len(failed_symbols):
                _time.sleep(1.5)
        failed_symbols = still_failed

    # Pass 3: Hâlâ başarısız olanlar için CACHE FALLBACK
    if failed_symbols:
        from core.cache import history_cache
        cache_recovered = 0
        for sym in failed_symbols:
            cached_df = history_cache.get(sym)
            if cached_df is not None and len(cached_df) >= 20:
                result[sym] = cached_df
                cache_recovered += 1
        if cache_recovered > 0:
            log.info(f"batch_history cache fallback: {cache_recovered}/{len(failed_symbols)} sembol cache\'den alındı")
        final_missing = len(failed_symbols) - cache_recovered
        if final_missing > 0:
            log.warning(f"batch_history: {final_missing} sembol tamamen eksik (borsapy + cache başarısız)")

    if result:
        cb_borsapy.on_success()
    else:
        cb_borsapy.on_failure(Exception("batch_history: 0 result"))

    log.info(f"batch_history sonuç: {len(result)}/{len(all_symbols)} başarılı")
    return result

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


def _find_data_col(df: Optional[pd.DataFrame]) -> int:
    """İlk gerçek veri kolonu (2025 boşsa skip)."""
    if df is None or df.empty:
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
    if df is None or df.empty:
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
    if df is None or df.empty:
        return None, None
    b = _find_data_col(df)
    return _pick(df, names, 0, b), _pick(df, names, 1, b)


def _pick_debt(
    bal: Optional[pd.DataFrame],
) -> tuple[Optional[float], Optional[float]]:
    """Kısa + Uzun vadeli Finansal Borçlar (aynı isim, farklı section)."""
    if bal is None or bal.empty:
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
def fetch_raw_v9(symbol: str) -> dict:
    """borsapy ile ham veri çek — paralel HTTP. Thread-safe SafeCache + CB."""
    if not BORSAPY_AVAILABLE:
        raise ImportError("borsapy yok")

    cached = raw_cache.get(symbol)
    if cached is not None:
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
            log.warning(f"fast_info {tc}: {e}")
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
            log.warning(f"income {tc}: {e}")
            return None

    def _balance():
        try:
            return tk.get_balance_sheet(quarterly=False, financial_group=fg, last_n=4)
        except Exception as e:
            log.warning(f"balance {tc}: {e}")
            return None

    def _cashflow():
        try:
            return tk.get_cashflow(quarterly=False, financial_group=fg, last_n=4)
        except Exception as e:
            if not is_bank(tc):
                log.warning(f"cashflow {tc}: {e}")
            return None

    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            f_fast = pool.submit(_fast)
            f_info = pool.submit(_info)
            f_fin = pool.submit(_income)
            f_bal = pool.submit(_balance)
            f_cf = pool.submit(_cashflow)
            fast = f_fast.result()
            info = f_info.result()
            fin = f_fin.result()
            bal = f_bal.result()
            cf = f_cf.result()

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
            "source": "borsapy", "ticker_clean": tc, "is_bank": is_bank(tc),
        }

        raw_cache.set(symbol, raw)
        cb_borsapy.on_success()
        return raw

    except CircuitBreakerOpen:
        raise
    except Exception as e:
        cb_borsapy.on_failure(e)
        raise


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
    price = _safe_num(fast.get("last_price")) or _safe_num(info.get("currentPrice"))
    market_cap = _safe_num(fast.get("market_cap")) or _safe_num(info.get("marketCap"))
    pe = _safe_num(fast.get("pe_ratio")) or _safe_num(info.get("trailingPE"))
    pb = _safe_num(fast.get("pb_ratio")) or _safe_num(info.get("priceToBook"))
    ev_ebitda = _safe_num(info.get("enterpriseToEbitda"))
    div_yield = _safe_num(info.get("dividendYield"))
    beta = _safe_num(info.get("beta"))
    shares = _safe_num(fast.get("shares"))
    if not shares and market_cap and price and price > 0:
        shares = market_cap / price

    # Per-share
    trailing_eps = _safe_num(info.get("trailingEps"))
    if not trailing_eps and net_income and shares and shares > 0:
        trailing_eps = net_income / shares
    book_val_ps = _safe_num(info.get("bookValue"))
    if not book_val_ps and equity and shares and shares > 0:
        book_val_ps = equity / shares

    # Ratios
    roe = _safe_num(info.get("returnOnEquity")) or ((net_income / equity) if net_income and equity and equity != 0 else None)
    roa = _safe_num(info.get("returnOnAssets")) or ((net_income / total_assets) if net_income and total_assets and total_assets != 0 else None)
    roa_prev = (net_income_prev / total_assets_prev) if net_income_prev and total_assets_prev and total_assets_prev != 0 else None
    gross_margin = (gross_profit / revenue) if gross_profit and revenue else None
    gross_margin_prev = (gross_profit_prev / revenue_prev) if gross_profit_prev and revenue_prev else None
    op_margin = (operating_income / revenue) if operating_income and revenue else None
    net_margin = (net_income / revenue) if net_income and revenue else None
    cur_ratio = (cur_assets / cur_liab) if cur_assets and cur_liab else None
    cur_ratio_prev = (cur_assets_prev / cur_liab_prev) if cur_assets_prev and cur_liab_prev else None
    debt_eq = (total_debt / equity * 100) if total_debt and equity and equity != 0 else None
    net_debt = (total_debt - cash) if total_debt is not None and cash is not None else None
    net_debt_ebit = (net_debt / ebitda) if net_debt is not None and ebitda not in (None, 0) else None
    _ev = ebit or operating_income
    int_cov = (_ev / abs(interest_exp)) if _ev and interest_exp and interest_exp != 0 else None
    free_cf = free_cf_direct or ((op_cf + capex) if op_cf is not None and capex is not None else None)
    fcf_yield = (free_cf / market_cap) if free_cf is not None and market_cap not in (None, 0) else None
    fcf_margin = (free_cf / revenue) if free_cf is not None and revenue not in (None, 0) else None
    cfo_to_ni = (op_cf / net_income) if op_cf is not None and net_income not in (None, 0) else None

    # Growth
    def _g(c: Optional[float], p: Optional[float]) -> Optional[float]:
        if c is None or p in (None, 0):
            return None
        return (c - p) / abs(p)

    rev_growth = _g(revenue, revenue_prev)
    eps_growth = _g(net_income, net_income_prev)
    ebit_growth = _g(ebitda, ebitda_prev)

    # Working capital & ROIC
    wc = (cur_assets - cur_liab) if cur_assets is not None and cur_liab is not None else None
    tax_rate = _safe_num(info.get("effectiveTaxRate")) or 0.20
    inv_cap = (total_debt + equity - cash) if total_debt is not None and equity is not None and cash is not None else None
    nopat = ((_ev or 0) * (1 - min(max(tax_rate, 0), 0.35))) if _ev else None
    roic = (nopat / inv_cap) if nopat and inv_cap not in (None, 0) else None

    # Valuation extras
    peg = (pe / max(eps_growth * 100, 1e-9)) if pe not in (None, 0) and eps_growth and eps_growth > 0 else None
    graham_fv = ((22.5 * trailing_eps * book_val_ps) ** 0.5) if trailing_eps and book_val_ps and trailing_eps > 0 and book_val_ps > 0 else None
    mos = ((graham_fv - price) / graham_fv) if graham_fv not in (None, 0) and price else None
    asset_to = (revenue / total_assets) if revenue and total_assets not in (None, 0) else None
    asset_to_p = (revenue_prev / total_assets_prev) if revenue_prev and total_assets_prev not in (None, 0) else None
    foreign_ratio = _safe_num(fast.get("foreign_ratio"))

    # EV/EBITDA fallback
    if ev_ebitda is None and market_cap and ebitda not in (None, 0):
        ev_ebitda = (market_cap + (total_debt or 0) - (cash or 0)) / ebitda

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
        "free_float": _safe_num(fast.get("free_float")),
        "ciro_pd": (revenue / market_cap) if revenue is not None and market_cap not in (None, 0) else None,
        "data_source": "borsapy",
    }


# ================================================================
# BATCH DOWNLOAD HISTORY V9 — CB entegreli
# ================================================================
def batch_download_history_v9(
    symbols: list[str],
    period: str = "1y",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """borsapy ile toplu price history indir. CB korumalı."""
    if not BORSAPY_AVAILABLE:
        return {}

    # CB kontrolü — borsapy devre dışıysa boş dön
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
            if df is not None and not df.empty and len(df) >= 20:
                return sym, df
        except Exception:
            pass
        return sym, None

    try:
        with ThreadPoolExecutor(max_workers=min(BATCH_HISTORY_WORKERS, len(symbols))) as pool:
            futures = [pool.submit(_fetch_one, s) for s in symbols]
            for future in as_completed(futures):
                sym, df = future.result()
                if df is not None:
                    result[sym] = df
        cb_borsapy.on_success()
    except Exception as e:
        cb_borsapy.on_failure(e)
        log.error(f"batch_history error: {e}")

    return result

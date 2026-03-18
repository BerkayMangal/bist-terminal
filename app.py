# ================================================================
# BISTBULL TERMINAL — FastAPI Backend
# Adapted from Berkay Fundamentals V5 (fa_bot.py)
# All analysis logic preserved: Piotroski, Altman, Beneish,
# 7-dim scoring, technical analysis, Cross Hunter, AI summary
# Telegram removed → REST API endpoints
# ================================================================

import sys, os, re, math, asyncio, logging, datetime as dt, io, time, json, base64
from collections import defaultdict, OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from contextlib import asynccontextmanager

# Fix yfinance TzCache warning on Railway
os.makedirs("/tmp/yf-cache", exist_ok=True)
yf.set_tz_cache_location("/tmp/yf-cache")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    CHART_AVAILABLE = True
except ImportError:
    CHART_AVAILABLE = False

try:
    from openai import OpenAI
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

BOT_VERSION = "V5"
APP_NAME = "BISTBULL TERMINAL"
CONFIDENCE_MIN = 55

# ================================================================
# ENV VARS
# ================================================================
OPENAI_KEY = os.environ.get("OPENAI_KEY", "")
SCORING_MODEL = os.environ.get("SCORING_MODEL", "gpt-4o-mini")

# ================================================================
# UNIVERSE
# ================================================================
UNIVERSE = [
    "ASELS","THYAO","BIMAS","KCHOL","SISE","EREGL","TUPRS","AKBNK","ISCTR","YKBNK",
    "GARAN","SAHOL","MGROS","FROTO","TOASO","TCELL","KRDMD","PETKM","ENKAI","TAVHL",
    "PGSUS","EKGYO","INDES","TTKOM","ARCLK","VESTL","DOHOL","AYGAZ","LOGO","SOKM",
    "TKFEN","KONTR","ODAS","GUBRF","SASA","ISMEN","OYAKC","CIMSA","MPARK","AKSEN",
]

# ================================================================
# CACHES + LOGGING
# ================================================================
RAW_CACHE = TTLCache(maxsize=5000, ttl=86400)
ANALYSIS_CACHE = TTLCache(maxsize=5000, ttl=86400)
TECH_CACHE = TTLCache(maxsize=500, ttl=3600)
AI_CACHE = TTLCache(maxsize=200, ttl=7200)
TOP10_CACHE = {"asof": None, "items": []}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bistbull")

# ================================================================
# HELPERS — verbatim from fa_bot.py
# ================================================================
def normalize_symbol(ticker):
    t = (ticker or "").strip().upper().replace(" ", "")
    if t.endswith(".IS"): return t
    if "." in t: return t
    return f"{t}.IS"

def base_ticker(text):
    return (text or "").strip().upper().replace(".IS", "")

def safe_num(x):
    try:
        if x is None: return None
        x = float(x)
        if math.isnan(x) or math.isinf(x): return None
        return x
    except Exception: return None

def fmt_num(x, digits=2):
    x = safe_num(x)
    if x is None: return "N/A"
    if abs(x) >= 1e9: return f"{x/1e9:.2f}B"
    if abs(x) >= 1e6: return f"{x/1e6:.2f}M"
    if abs(x) >= 1e3: return f"{x:,.0f}"
    return f"{x:.{digits}f}"

def fmt_pct(x, digits=1):
    x = safe_num(x)
    if x is None: return "N/A"
    return f"{x*100:.{digits}f}%"

def pick_row_pair(df, names):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None, None
    for name in names:
        if name in df.index:
            try:
                s = df.loc[name]
                if isinstance(s, pd.DataFrame): s = s.iloc[:, 0]
                s = pd.to_numeric(s, errors="coerce").dropna()
                if s.empty: continue
                cur = safe_num(s.iloc[0])
                prev = safe_num(s.iloc[1]) if len(s) > 1 else None
                return cur, prev
            except Exception: continue
    return None, None

def growth(cur, prev):
    cur, prev = safe_num(cur), safe_num(prev)
    if cur is None or prev in (None, 0): return None
    return (cur - prev) / abs(prev)

def avg(values):
    vals = [safe_num(v) for v in values if safe_num(v) is not None]
    if not vals: return None
    return float(sum(vals) / len(vals))

def score_higher(x, bad, ok, good, great):
    x = safe_num(x)
    if x is None: return None
    if x <= bad: return 5.0
    if x >= great: return 100.0
    if x <= ok: return 5 + (x - bad) * (35 / max(ok - bad, 1e-9))
    if x <= good: return 40 + (x - ok) * (35 / max(good - ok, 1e-9))
    return 75 + (x - good) * (25 / max(great - good, 1e-9))

def score_lower(x, great, good, ok, bad):
    x = safe_num(x)
    if x is None: return None
    if x <= great: return 100.0
    if x >= bad: return 5.0
    if x <= good: return 100 - (x - great) * (25 / max(good - great, 1e-9))
    if x <= ok: return 75 - (x - good) * (35 / max(ok - good, 1e-9))
    return 40 - (x - ok) * (35 / max(bad - ok, 1e-9))

# ================================================================
# RAW FETCH (yfinance) — verbatim from fa_bot.py
# ================================================================
def fetch_raw(symbol):
    if symbol in RAW_CACHE: return RAW_CACHE[symbol]
    tk = yf.Ticker(symbol)
    info = tk.get_info() or {}
    try: fast = getattr(tk, "fast_info", {}) or {}
    except Exception: fast = {}
    try: financials = tk.financials
    except Exception: financials = None
    try: balance = tk.balance_sheet
    except Exception: balance = None
    try: cashflow = tk.cashflow
    except Exception: cashflow = None
    raw = {"info": info, "fast": fast, "financials": financials, "balance": balance, "cashflow": cashflow}
    RAW_CACHE[symbol] = raw
    return raw

# ================================================================
# LEGENDARY METRICS — verbatim from fa_bot.py
# ================================================================
def compute_piotroski(m):
    pts, used = 0, 0
    tests = [
        (m.get("roa", 0) > 0) if m.get("roa") is not None else None,
        (m.get("operating_cf", 0) > 0) if m.get("operating_cf") is not None else None,
        (m.get("roa", 0) > m.get("roa_prev", 0)) if (m.get("roa") is not None and m.get("roa_prev") is not None) else None,
        (m.get("operating_cf", 0) > m.get("net_income", 0)) if (m.get("operating_cf") is not None and m.get("net_income") is not None) else None,
        (m.get("current_ratio", 0) > m.get("current_ratio_prev", 0)) if (m.get("current_ratio") is not None and m.get("current_ratio_prev") is not None) else None,
        (m.get("share_change", 1) <= 0) if m.get("share_change") is not None else None,
        ((m.get("total_debt",0)/max(m.get("total_assets",1),1)) < (m.get("total_debt_prev",0)/max(m.get("total_assets_prev",1),1)))
            if (m.get("total_debt") is not None and m.get("total_assets") is not None and m.get("total_debt_prev") is not None and m.get("total_assets_prev") is not None) else None,
        (m.get("gross_margin", 0) > m.get("gross_margin_prev", 0)) if (m.get("gross_margin") is not None and m.get("gross_margin_prev") is not None) else None,
        (m.get("asset_turnover", 0) > m.get("asset_turnover_prev", 0)) if (m.get("asset_turnover") is not None and m.get("asset_turnover_prev") is not None) else None,
    ]
    for t in tests:
        if t is None: continue
        used += 1; pts += int(t)
    return pts if used >= 4 else None

def compute_altman(m):
    wc = safe_num(m.get("working_capital"))
    ta = safe_num(m.get("total_assets"))
    re_ = safe_num(m.get("retained_earnings")) or 0.0
    ebit = safe_num(m.get("ebit"))
    tl = safe_num(m.get("total_liabilities"))
    sales = safe_num(m.get("revenue"))
    mve = safe_num(m.get("market_cap"))
    if None in (wc, ta, ebit, tl, sales, mve) or ta == 0 or tl == 0: return None
    return 1.2*(wc/ta) + 1.4*(re_/ta) + 3.3*(ebit/ta) + 0.6*(mve/tl) + 1.0*(sales/ta)

def compute_beneish(m):
    rec, rec_prev = m.get("receivables"), m.get("receivables_prev")
    sales, sales_prev = m.get("revenue"), m.get("revenue_prev")
    gp, gp_prev = m.get("gross_profit"), m.get("gross_profit_prev")
    ca, ca_prev = m.get("current_assets"), m.get("current_assets_prev")
    ppe, ppe_prev = m.get("ppe"), m.get("ppe_prev")
    dep, dep_prev = m.get("depreciation"), m.get("depreciation_prev")
    sga, sga_prev = m.get("sga"), m.get("sga_prev")
    debt, debt_prev = m.get("total_debt"), m.get("total_debt_prev")
    ta, ta_prev = m.get("total_assets"), m.get("total_assets_prev")
    ni, cfo = m.get("net_income"), m.get("operating_cf")
    if any(safe_num(x) in (None, 0) for x in [sales, sales_prev, ta, ta_prev]):
        return None
    try:
        dsri = ((rec or 0)/(sales or 1)) / max((rec_prev or 0)/(sales_prev or 1), 1e-9)
        gm = (gp or 0)/(sales or 1)
        gm_prev = (gp_prev or 0)/(sales_prev or 1)
        gmi = (gm_prev / max(gm, 1e-9)) if gm and gm_prev else 1.0
        aqi_num = 1 - ((ca or 0) + (ppe or 0)) / max(ta, 1e-9)
        aqi_den = 1 - ((ca_prev or 0) + (ppe_prev or 0)) / max(ta_prev, 1e-9)
        aqi = aqi_num / max(aqi_den, 1e-9)
        sgi = sales / max(sales_prev, 1e-9)
        dep_prev_rate = (dep_prev or 0) / max((dep_prev or 0) + (ppe_prev or 0), 1e-9)
        dep_cur_rate = (dep or 0) / max((dep or 0) + (ppe or 0), 1e-9)
        depi = dep_prev_rate / max(dep_cur_rate, 1e-9)
        sgai = ((sga or 0)/(sales or 1)) / max((sga_prev or 0)/(sales_prev or 1), 1e-9)
        lvgi = ((debt or 0)/max(ta, 1e-9)) / max((debt_prev or 0)/max(ta_prev, 1e-9), 1e-9)
        tata = ((ni or 0) - (cfo or 0)) / max(ta, 1e-9)
        return -4.84 + 0.92*dsri + 0.528*gmi + 0.404*aqi + 0.892*sgi + 0.115*depi - 0.172*sgai + 4.679*tata - 0.327*lvgi
    except Exception: return None

# ================================================================
# METRIC BUILD — verbatim from fa_bot.py
# ================================================================
def compute_metrics(symbol):
    raw = fetch_raw(symbol)
    info, fast = raw["info"], raw["fast"]
    fin, bal, cf = raw["financials"], raw["balance"], raw["cashflow"]

    revenue, revenue_prev = pick_row_pair(fin, ["Total Revenue", "Operating Revenue"])
    gross_profit, gross_profit_prev = pick_row_pair(fin, ["Gross Profit"])
    operating_income, _ = pick_row_pair(fin, ["Operating Income", "EBIT"])
    ebit, _ = pick_row_pair(fin, ["EBIT", "Operating Income"])
    ebitda, ebitda_prev = pick_row_pair(fin, ["EBITDA"])
    net_income, net_income_prev = pick_row_pair(fin, ["Net Income", "Net Income Common Stockholders"])
    interest_exp, _ = pick_row_pair(fin, ["Interest Expense", "Interest Expense Non Operating"])
    dil_shares, dil_shares_prev = pick_row_pair(fin, ["Diluted Average Shares", "Basic Average Shares"])
    eps_row, eps_row_prev = pick_row_pair(fin, ["Diluted EPS", "Basic EPS"])
    sga, sga_prev = pick_row_pair(fin, ["Selling General And Administration"])

    op_cf, _ = pick_row_pair(cf, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
    capex, _ = pick_row_pair(cf, ["Capital Expenditure"])
    dep, dep_prev = pick_row_pair(cf, ["Depreciation", "Depreciation And Amortization"])

    total_assets, total_assets_prev = pick_row_pair(bal, ["Total Assets"])
    total_liab, _ = pick_row_pair(bal, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
    total_debt, total_debt_prev = pick_row_pair(bal, ["Total Debt"])
    cash, _ = pick_row_pair(bal, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"])
    cur_assets, cur_assets_prev = pick_row_pair(bal, ["Current Assets", "Total Current Assets"])
    cur_liab, cur_liab_prev = pick_row_pair(bal, ["Current Liabilities", "Total Current Liabilities"])
    ret_earn, _ = pick_row_pair(bal, ["Retained Earnings"])
    equity, _ = pick_row_pair(bal, ["Stockholders Equity", "Total Stockholder Equity"])
    receivables, rec_prev = pick_row_pair(bal, ["Accounts Receivable", "Receivables"])
    ppe, ppe_prev = pick_row_pair(bal, ["Net PPE", "Property Plant Equipment Net"])

    price = safe_num(fast.get("last_price")) or safe_num(info.get("currentPrice"))
    market_cap = safe_num(fast.get("market_cap")) or safe_num(info.get("marketCap"))
    pe = safe_num(info.get("trailingPE")) or safe_num(info.get("forwardPE"))
    pb = safe_num(info.get("priceToBook"))
    ev_ebitda = safe_num(info.get("enterpriseToEbitda"))
    div_yield = safe_num(info.get("dividendYield"))
    beta = safe_num(info.get("beta"))
    trailing_eps = safe_num(info.get("trailingEps")) or safe_num(eps_row)
    book_val_ps = safe_num(info.get("bookValue")) or ((equity/dil_shares) if equity and dil_shares else None)

    roe = safe_num(info.get("returnOnEquity")) or ((net_income/equity) if net_income and equity else None)
    roa = safe_num(info.get("returnOnAssets")) or ((net_income/total_assets) if net_income and total_assets else None)
    roa_prev = (net_income_prev/total_assets_prev) if net_income_prev and total_assets_prev else None
    gross_margin = (gross_profit/revenue) if gross_profit and revenue else None
    gross_margin_prev = (gross_profit_prev/revenue_prev) if gross_profit_prev and revenue_prev else None
    op_margin = safe_num(info.get("operatingMargins")) or ((operating_income/revenue) if operating_income and revenue else None)
    net_margin = safe_num(info.get("profitMargins")) or ((net_income/revenue) if net_income and revenue else None)
    cur_ratio = safe_num(info.get("currentRatio")) or ((cur_assets/cur_liab) if cur_assets and cur_liab else None)
    cur_ratio_prev = (cur_assets_prev/cur_liab_prev) if cur_assets_prev and cur_liab_prev else None
    debt_eq = safe_num(info.get("debtToEquity")) or ((total_debt/equity*100) if total_debt and equity else None)

    net_debt = (total_debt - cash) if total_debt is not None and cash is not None else None
    net_debt_ebit = (net_debt/ebitda) if net_debt is not None and ebitda not in (None, 0) else None
    _ebit_val = ebit if ebit is not None else operating_income
    int_cov = (_ebit_val/abs(interest_exp)) if _ebit_val is not None and interest_exp not in (None, 0) else None

    free_cf = ((op_cf + capex) if op_cf is not None and capex is not None else None) or safe_num(info.get("freeCashflow"))
    fcf_yield = (free_cf/market_cap) if free_cf is not None and market_cap not in (None, 0) else None
    fcf_margin = (free_cf/revenue) if free_cf is not None and revenue not in (None, 0) else None
    cfo_to_ni = (op_cf/net_income) if op_cf is not None and net_income not in (None, 0) else None

    rev_growth = safe_num(info.get("revenueGrowth")) or growth(revenue, revenue_prev)
    eps_growth = safe_num(info.get("earningsGrowth")) or growth(eps_row, eps_row_prev) or growth(net_income, net_income_prev)
    ebit_growth = growth(ebitda, ebitda_prev)

    wc = (cur_assets - cur_liab) if cur_assets is not None and cur_liab is not None else None
    tax_rate = safe_num(info.get("effectiveTaxRate")) or 0.20
    inv_cap = (total_debt + equity - cash) if total_debt is not None and equity is not None and cash is not None else None
    _ebit_nopat = ebit if ebit is not None else operating_income
    nopat = (_ebit_nopat * (1 - min(max(tax_rate, 0), 0.35))) if _ebit_nopat is not None else None
    roic = (nopat/inv_cap) if nopat is not None and inv_cap not in (None, 0) else None

    peg = (pe/max(eps_growth*100, 1e-9)) if pe not in (None, 0) and eps_growth is not None and eps_growth > 0 else None
    graham_fv = ((22.5*trailing_eps*book_val_ps)**0.5) if trailing_eps not in (None, 0) and book_val_ps not in (None, 0) and trailing_eps > 0 and book_val_ps > 0 else None
    mos = ((graham_fv - price)/graham_fv) if graham_fv not in (None, 0) and price is not None else None
    share_ch = growth(dil_shares, dil_shares_prev)
    asset_to = (revenue/total_assets) if revenue is not None and total_assets not in (None, 0) else None
    asset_to_p = (revenue_prev/total_assets_prev) if revenue_prev is not None and total_assets_prev not in (None, 0) else None

    m = {
        "symbol": symbol, "ticker": base_ticker(symbol),
        "name": str(info.get("shortName") or info.get("longName") or symbol),
        "currency": str(info.get("currency") or ""),
        "sector": str(info.get("sector") or ""),
        "industry": str(info.get("industry") or ""),
        "price": price, "market_cap": market_cap,
        "pe": pe, "pb": pb, "ev_ebitda": ev_ebitda, "dividend_yield": div_yield, "beta": beta,
        "revenue": revenue, "revenue_prev": revenue_prev,
        "gross_profit": gross_profit, "gross_profit_prev": gross_profit_prev,
        "operating_income": operating_income, "ebit": ebit or operating_income,
        "ebitda": ebitda, "ebitda_prev": ebitda_prev,
        "net_income": net_income, "net_income_prev": net_income_prev,
        "operating_cf": op_cf, "free_cf": free_cf,
        "total_assets": total_assets, "total_assets_prev": total_assets_prev,
        "total_liabilities": total_liab, "total_debt": total_debt, "total_debt_prev": total_debt_prev,
        "cash": cash, "current_assets": cur_assets, "current_assets_prev": cur_assets_prev,
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
        "revenue_growth": rev_growth, "eps_growth": eps_growth, "ebitda_growth": ebit_growth,
        "peg": peg, "graham_fv": graham_fv, "margin_safety": mos,
        "share_change": share_ch, "asset_turnover": asset_to, "asset_turnover_prev": asset_to_p,
    }
    m["piotroski_f"] = compute_piotroski(m)
    m["altman_z"] = compute_altman(m)
    m["beneish_m"] = compute_beneish(m)
    return m

# ================================================================
# SCORING — verbatim from fa_bot.py (BIST-calibrated V5.1)
# ================================================================
def score_value(m):
    ev_sales = None
    if m.get("market_cap") and m.get("total_debt") and m.get("cash") and m.get("revenue"):
        ev = m["market_cap"] + (m["total_debt"] or 0) - (m["cash"] or 0)
        if m["revenue"] > 0:
            ev_sales = ev / m["revenue"]
    return avg([
        score_lower(m.get("pe"), 6, 10, 16, 25) if (m.get("pe") or 0) > 0 else None,
        score_lower(m.get("pb"), 0.8, 1.5, 2.5, 4.5) if (m.get("pb") or 0) > 0 else None,
        score_lower(m.get("ev_ebitda"), 4, 7, 11, 16) if (m.get("ev_ebitda") or 0) > 0 else None,
        score_lower(ev_sales, 0.5, 1.2, 2.5, 5.0) if ev_sales is not None and ev_sales > 0 else None,
        score_higher(m.get("fcf_yield"), 0, 0.02, 0.05, 0.08),
        score_higher(m.get("margin_safety"), -0.2, 0, 0.15, 0.30),
    ])

def score_quality(m):
    return avg([
        score_higher(m.get("roe"), 0.01, 0.06, 0.12, 0.20),
        score_higher(m.get("roic"), 0.01, 0.06, 0.10, 0.16),
        score_higher(m.get("gross_margin"), 0.08, 0.15, 0.25, 0.40),
        score_higher(m.get("operating_margin"), 0.02, 0.06, 0.12, 0.20),
        score_higher(m.get("net_margin"), 0.005, 0.03, 0.08, 0.15),
    ])

def score_growth(m):
    return avg([
        score_higher(m.get("revenue_growth"), -0.05, 0.05, 0.15, 0.30),
        score_higher(m.get("eps_growth"), -0.10, 0.05, 0.15, 0.30),
        score_higher(m.get("ebitda_growth"), -0.05, 0.05, 0.12, 0.25),
        score_lower(m.get("peg"), 0.5, 1.0, 1.8, 3.0) if (m.get("peg") or 0) > 0 else None,
    ])

def score_balance(m):
    nde = m.get("net_debt_ebitda")
    nde_s = 100.0 if nde is not None and nde < 0 else score_lower(nde, 0.5, 1.5, 2.5, 4.0)
    return avg([nde_s,
        score_lower(m.get("debt_equity"), 30, 80, 150, 300),
        score_higher(m.get("current_ratio"), 0.8, 1.1, 1.5, 2.2),
        score_higher(m.get("interest_coverage"), 1.5, 3.0, 6.0, 12.0),
        score_higher(m.get("altman_z"), 1.2, 1.8, 3.0, 4.5),
    ])

def score_earnings(m):
    bm = m.get("beneish_m")
    bm_s = None
    if bm is not None:
        bm_s = 90 if bm < -2.22 else (65 if bm < -1.78 else 25)
    return avg([
        score_higher(m.get("cfo_to_ni"), 0.2, 0.6, 0.9, 1.2),
        score_higher(m.get("fcf_margin"), -0.02, 0, 0.05, 0.12),
        bm_s,
    ])

def score_moat(m):
    stab = None
    if m.get("gross_margin") is not None and m.get("gross_margin_prev") is not None:
        stab = score_lower(abs(m["gross_margin"] - m["gross_margin_prev"]), 0, 0.02, 0.06, 0.12)
    op_stab = None
    if m.get("operating_margin") is not None and m.get("roa") is not None and m.get("roa_prev") is not None:
        op_stab = score_lower(abs(m["roa"] - m["roa_prev"]), 0, 0.02, 0.05, 0.10)
    pricing = score_higher(m.get("gross_margin"), 0.12, 0.22, 0.35, 0.50) if m.get("gross_margin") else None
    at_trend = None
    if m.get("asset_turnover") is not None and m.get("asset_turnover_prev") is not None:
        at_trend = 75 if m["asset_turnover"] >= m["asset_turnover_prev"] else 35
    return avg([stab, op_stab, pricing, at_trend])

def score_capital(m):
    dil = None
    sc = m.get("share_change")
    if sc is not None:
        dil = 100 if sc <= 0 else score_lower(sc, 0, 0.03, 0.08, 0.20)
    capex_rev = None
    if m.get("operating_cf") and m.get("free_cf") and m.get("revenue"):
        capex = abs(m["operating_cf"] - m["free_cf"])
        if m["revenue"] > 0:
            cr = capex / m["revenue"]
            capex_rev = score_lower(cr, 0.02, 0.05, 0.10, 0.20)
    return avg([
        score_higher(m.get("dividend_yield"), 0, 0.01, 0.03, 0.06),
        score_higher(m.get("fcf_yield"), 0, 0.02, 0.05, 0.08),
        capex_rev,
        dil,
    ])

def confidence_score(m):
    keys = ["pe","pb","fcf_yield","roe","roic","operating_margin","revenue_growth","eps_growth",
            "net_debt_ebitda","interest_coverage","cfo_to_ni","piotroski_f","altman_z","peg","margin_safety"]
    have = sum(1 for k in keys if safe_num(m.get(k)) is not None)
    return round(100 * have / len(keys), 1)

def style_label(scores):
    v, q, g, moat = scores["value"], scores["quality"], scores["growth"], scores["moat"]
    bal = scores["balance"]
    if q >= 75 and g >= 60 and v >= 40 and moat >= 60: return "Quality Compounder"
    if q >= 72 and moat >= 65 and v < 40: return "Premium Compounder"
    if v >= 75 and bal >= 55: return "Deep Value"
    if g >= 70 and v >= 45: return "GARP"
    if g >= 65 and q >= 55 and v < 45: return "Growth"
    if v >= 70 and q < 45: return "Value Trap Risk"
    if bal < 40 and g >= 50: return "High-Risk Turnaround"
    if scores.get("capital", 50) >= 70 and q >= 55: return "Income / Dividend"
    return "Balanced"

def legendary_labels(m, scores):
    pf, az, bm, peg_v, mos = m.get("piotroski_f"), m.get("altman_z"), m.get("beneish_m"), m.get("peg"), m.get("margin_safety")
    pf_l = "N/A" if pf is None else (f"{int(pf)}/9 (Strong)" if pf >= 7 else f"{int(pf)}/9 (Okay)" if pf >= 5 else f"{int(pf)}/9 (Weak)")
    az_l = "N/A" if az is None else (f"{az:.2f} (Safe)" if az >= 3 else f"{az:.2f} (Grey)" if az >= 1.8 else f"{az:.2f} (Risk)")
    bm_l = "N/A" if bm is None else (f"{bm:.2f} (Low risk)" if bm < -2.22 else f"{bm:.2f} (Watch)" if bm < -1.78 else f"{bm:.2f} (Higher risk)")
    peg_l = "N/A" if peg_v is None else (f"{peg_v:.2f} (Cheap)" if peg_v < 1 else f"{peg_v:.2f} (Fair)" if peg_v <= 2 else f"{peg_v:.2f} (Rich)")
    mos_l = "N/A" if mos is None else ("High" if mos >= 0.20 else "Medium" if mos >= 0 else "Low")
    buffett = "Pass" if (scores["quality"] >= 75 and scores["moat"] >= 65 and scores["balance"] >= 60 and scores["capital"] >= 55) else ("Borderline" if scores["quality"] >= 60 and scores["moat"] >= 50 else "Fail")
    graham = "Pass" if (scores["value"] >= 70 and scores["balance"] >= 60 and (mos or -1) >= 0) else ("Borderline" if scores["value"] >= 55 else "Fail")
    is_bank = "bank" in (m.get("industry") or "").lower() or "sigorta" in (m.get("industry") or "").lower()
    if is_bank:
        az_l = "N/A (Banka)"
    return {"piotroski": pf_l, "altman": az_l, "beneish": bm_l, "peg": peg_l, "graham_mos": mos_l, "buffett_filter": buffett, "graham_filter": graham}

def drivers(scores, confidence):
    pos, neg = [], []
    if scores["quality"] >= 70: pos.append("Good business quality (ROIC / margins strong)")
    if scores["earnings"] >= 65: pos.append("Cash flow supports earnings")
    if scores["balance"] >= 70: pos.append("Balance sheet solid")
    if scores["value"] >= 70: pos.append("Looks cheap vs fundamentals")
    if scores["moat"] >= 65: pos.append("Signs of pricing power / margin stability")
    if scores["capital"] >= 65: pos.append("Shareholder-friendly capital allocation")
    if scores["growth"] >= 70: pos.append("Strong growth trajectory")
    if not pos: pos.append("Balanced profile, no single elite category")
    if scores["value"] < 40: neg.append("Valuation looks expensive")
    if scores["quality"] < 40: neg.append("Low profitability — margins or ROIC weak")
    if scores["growth"] < 40: neg.append("Growth weak or inconsistent")
    if scores["balance"] < 40: neg.append("Debt / liquidity needs watch")
    if scores["earnings"] < 40: neg.append("Cash flow trails accounting profits")
    if scores["moat"] < 35: neg.append("Margin stability weak — no pricing power")
    if confidence < 65: neg.append("Some metrics missing; treat with caution")
    if not neg: neg.append("No major red flag right now")
    return pos[:4], neg[:4]

# ================================================================
# ANALYZE — verbatim from fa_bot.py
# ================================================================
def analyze_symbol(symbol):
    if symbol in ANALYSIS_CACHE: return ANALYSIS_CACHE[symbol]
    m = compute_metrics(symbol)
    scores = {k: round((f(m) or 50), 1) for k, f in [
        ("value", score_value), ("quality", score_quality), ("growth", score_growth),
        ("balance", score_balance), ("earnings", score_earnings), ("moat", score_moat), ("capital", score_capital),
    ]}
    overall = (0.20*scores["value"] + 0.22*scores["quality"] + 0.15*scores["growth"]
              + 0.20*scores["balance"] + 0.10*scores["earnings"] + 0.08*scores["moat"] + 0.05*scores["capital"])
    if m.get("equity") is not None and m["equity"] < 0: overall -= 12
    if m.get("net_income") is not None and m["net_income"] < 0: overall -= 8
    if m.get("operating_cf") is not None and m["operating_cf"] < 0: overall -= 8
    if m.get("interest_coverage") is not None and m["interest_coverage"] < 1.5: overall -= 5
    if m.get("beneish_m") is not None and m["beneish_m"] > -1.78: overall -= 5
    if m.get("total_debt") is not None and m.get("cash") is not None:
        if m["cash"] > (m["total_debt"] or 0):
            overall += 3
    overall = round(max(1, min(99, overall)), 1)
    confidence = confidence_score(m)
    style = style_label(scores)
    legends = legendary_labels(m, scores)
    pos, neg = drivers(scores, confidence)
    r = {
        "symbol": symbol, "ticker": base_ticker(symbol), "name": m["name"], "currency": m["currency"],
        "sector": m.get("sector", ""), "industry": m.get("industry", ""),
        "metrics": m, "scores": scores, "overall": overall, "confidence": confidence,
        "style": style, "legendary": legends, "positives": pos, "negatives": neg,
    }
    ANALYSIS_CACHE[symbol] = r
    return r

# ================================================================
# TECHNICAL ANALYSIS — verbatim from fa_bot.py
# ================================================================
def compute_technical(symbol):
    if symbol in TECH_CACHE:
        return TECH_CACHE[symbol]
    try:
        tk = yf.Ticker(symbol)
        df = tk.history(period="1y", interval="1d")
        if df is None or len(df) < 50:
            return None
        c = df["Close"]
        v = df["Volume"]

        ma50 = c.rolling(50).mean()
        ma200 = c.rolling(200).mean() if len(c) >= 200 else pd.Series([np.nan]*len(c))
        price = float(c.iloc[-1])
        ma50_val = float(ma50.iloc[-1]) if not np.isnan(ma50.iloc[-1]) else None
        ma200_val = float(ma200.iloc[-1]) if len(c) >= 200 and not np.isnan(ma200.iloc[-1]) else None

        cross_signal = None
        if ma50_val and ma200_val and len(ma50) >= 2 and len(ma200) >= 2:
            prev_50 = float(ma50.iloc[-2]) if not np.isnan(ma50.iloc[-2]) else None
            prev_200 = float(ma200.iloc[-2]) if not np.isnan(ma200.iloc[-2]) else None
            if prev_50 and prev_200:
                if prev_50 <= prev_200 and ma50_val > ma200_val:
                    cross_signal = "GOLDEN_CROSS"
                elif prev_50 >= prev_200 and ma50_val < ma200_val:
                    cross_signal = "DEATH_CROSS"

        delta = c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None

        ema12 = c.ewm(span=12).mean()
        ema26 = c.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_val = float(macd_line.iloc[-1])
        signal_val = float(signal_line.iloc[-1])
        macd_hist = macd_val - signal_val
        macd_bullish = macd_val > signal_val
        macd_cross = None
        if len(macd_line) >= 2:
            prev_macd = float(macd_line.iloc[-2])
            prev_sig = float(signal_line.iloc[-2])
            if prev_macd <= prev_sig and macd_val > signal_val:
                macd_cross = "BULLISH"
            elif prev_macd >= prev_sig and macd_val < signal_val:
                macd_cross = "BEARISH"

        bb_mid = c.rolling(20).mean()
        bb_std = c.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_pos = None
        if not np.isnan(bb_upper.iloc[-1]) and not np.isnan(bb_lower.iloc[-1]):
            if price > float(bb_upper.iloc[-1]):
                bb_pos = "ABOVE"
            elif price < float(bb_lower.iloc[-1]):
                bb_pos = "BELOW"
            else:
                bb_pos = "INSIDE"

        high_52w = float(df["High"].tail(252).max()) if len(df) >= 50 else None
        low_52w = float(df["Low"].tail(252).min()) if len(df) >= 50 else None
        pct_from_high = ((price - high_52w) / high_52w * 100) if high_52w else None
        pct_from_low = ((price - low_52w) / low_52w * 100) if low_52w else None

        vol_avg = float(v.tail(20).mean()) if len(v) >= 20 else None
        vol_today = float(v.iloc[-1]) if len(v) > 0 else None
        vol_ratio = (vol_today / vol_avg) if vol_avg and vol_avg > 0 else None

        tech_score = 50.0
        components = []
        if rsi_val is not None:
            if 40 <= rsi_val <= 60: components.append({"name": "RSI", "score": 50, "desc": "Notr"})
            elif 30 <= rsi_val < 40: components.append({"name": "RSI", "score": 65, "desc": "Oversold yakinlasma"})
            elif rsi_val < 30: components.append({"name": "RSI", "score": 85, "desc": "Asiri satim"})
            elif 60 < rsi_val <= 70: components.append({"name": "RSI", "score": 40, "desc": "Overbought yakinlasma"})
            else: components.append({"name": "RSI", "score": 20, "desc": "Asiri alim"})
        if ma50_val:
            if price > ma50_val:
                components.append({"name": "MA50", "score": 70, "desc": "Fiyat MA50 uzerinde"})
            else:
                components.append({"name": "MA50", "score": 30, "desc": "Fiyat MA50 altinda"})
        if ma200_val:
            if price > ma200_val:
                components.append({"name": "MA200", "score": 75, "desc": "Fiyat MA200 uzerinde"})
            else:
                components.append({"name": "MA200", "score": 25, "desc": "Fiyat MA200 altinda"})
        if ma50_val and ma200_val:
            if ma50_val > ma200_val:
                components.append({"name": "Trend", "score": 80, "desc": "MA50 > MA200 (Yukari)"})
            else:
                components.append({"name": "Trend", "score": 20, "desc": "MA50 < MA200 (Asagi)"})
        if macd_bullish:
            components.append({"name": "MACD", "score": 70, "desc": "Bullish"})
        else:
            components.append({"name": "MACD", "score": 30, "desc": "Bearish"})
        if vol_ratio and vol_ratio > 1.5:
            components.append({"name": "Hacim", "score": 75, "desc": f"{vol_ratio:.1f}x ortalama"})
        elif vol_ratio:
            components.append({"name": "Hacim", "score": 50, "desc": f"{vol_ratio:.1f}x ortalama"})

        if components:
            tech_score = sum(c["score"] for c in components) / len(components)

        # Build price history for frontend chart (last 130 bars = ~6 months)
        chart_df = df.tail(130)
        price_history = []
        for idx, row in chart_df.iterrows():
            price_history.append({
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })

        # MA series for chart
        ma50_series = []
        ma50_full = c.rolling(50).mean().tail(130)
        for idx, val in ma50_full.items():
            if not np.isnan(val):
                ma50_series.append({"date": idx.strftime("%Y-%m-%d"), "value": round(float(val), 2)})

        ma200_series = []
        if len(c) >= 200:
            ma200_full = c.rolling(200).mean().tail(130)
            for idx, val in ma200_full.items():
                if not np.isnan(val):
                    ma200_series.append({"date": idx.strftime("%Y-%m-%d"), "value": round(float(val), 2)})

        result = {
            "price": price, "ma50": ma50_val, "ma200": ma200_val,
            "rsi": rsi_val, "macd": macd_val, "macd_signal": signal_val,
            "macd_hist": macd_hist, "macd_bullish": macd_bullish,
            "macd_cross": macd_cross, "cross_signal": cross_signal,
            "bb_pos": bb_pos,
            "bb_upper": round(float(bb_upper.iloc[-1]), 2) if not np.isnan(bb_upper.iloc[-1]) else None,
            "bb_lower": round(float(bb_lower.iloc[-1]), 2) if not np.isnan(bb_lower.iloc[-1]) else None,
            "high_52w": high_52w, "low_52w": low_52w,
            "pct_from_high": pct_from_high, "pct_from_low": pct_from_low,
            "vol_ratio": vol_ratio, "tech_score": round(tech_score, 1),
            "components": components,
            "price_history": price_history,
            "ma50_series": ma50_series,
            "ma200_series": ma200_series,
        }
        TECH_CACHE[symbol] = result
        return result
    except Exception as e:
        log.warning(f"Technical {symbol}: {e}")
        return None

# ================================================================
# CHART GENERATOR (matplotlib PNG) — adapted from fa_bot.py
# ================================================================
def generate_chart_png(symbol, tech_data=None):
    if not CHART_AVAILABLE:
        return None
    try:
        if tech_data and tech_data.get("price_history"):
            dates_str = [p["date"] for p in tech_data["price_history"]]
            closes = [p["close"] for p in tech_data["price_history"]]
            opens = [p["open"] for p in tech_data["price_history"]]
            volumes = [p["volume"] for p in tech_data["price_history"]]
            dates = pd.to_datetime(dates_str)
        else:
            tk = yf.Ticker(symbol)
            df = tk.history(period="6mo", interval="1d")
            if df is None or len(df) < 20:
                return None
            dates = df.index
            closes = df["Close"].tolist()
            opens = df["Open"].tolist()
            volumes = df["Volume"].tolist()

        close_s = pd.Series(closes)

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), height_ratios=[3, 1],
                                         gridspec_kw={"hspace": 0.05})
        fig.patch.set_facecolor("#0d1117")
        ax1.set_facecolor("#0d1117")
        ax2.set_facecolor("#0d1117")

        ax1.plot(dates, closes, color="#58a6ff", linewidth=1.5, label="Fiyat")

        ma50 = close_s.rolling(50).mean()
        ax1.plot(dates, ma50, color="#f0883e", linewidth=1, alpha=0.8, label="MA50")

        if len(close_s) >= 200:
            ma200 = close_s.rolling(200).mean()
            valid_idx = ~ma200.isna()
            if valid_idx.sum() > 5:
                ax1.plot(dates[valid_idx], ma200[valid_idx], color="#da3633", linewidth=1, alpha=0.8, label="MA200")

        ticker = base_ticker(symbol)
        price = closes[-1] if closes else 0
        ts = tech_data.get("tech_score", 50) if tech_data else 50
        rsi_v = tech_data.get("rsi") if tech_data else None
        title = f"{ticker}  {price:.2f}  |  Teknik: {ts}/100"
        if rsi_v: title += f"  |  RSI: {rsi_v:.0f}"
        ax1.set_title(title, color="white", fontsize=12, fontweight="bold", pad=10)
        ax1.legend(loc="upper left", fontsize=8, facecolor="#161b22", edgecolor="#30363d", labelcolor="white")
        ax1.tick_params(colors="gray", labelsize=8)
        ax1.grid(True, alpha=0.1, color="gray")
        ax1.set_ylabel("")
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

        colors = ["#3fb950" if c >= o else "#da3633" for c, o in zip(closes, opens)]
        ax2.bar(dates, volumes, color=colors, alpha=0.6, width=0.8)
        ax2.tick_params(colors="gray", labelsize=7)
        ax2.grid(True, alpha=0.1, color="gray")
        ax2.set_ylabel("")
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

        for ax in [ax1, ax2]:
            for spine in ax.spines.values():
                spine.set_color("#30363d")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="#0d1117", edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        log.warning(f"Chart {symbol}: {e}")
        return None

# ================================================================
# AI TRADER SUMMARY — adapted from fa_bot.py
# ================================================================
def ai_trader_summary(r, tech=None):
    if not AI_AVAILABLE or not OPENAI_KEY:
        return None
    cache_key = f"{r['symbol']}_{r['overall']}"
    if cache_key in AI_CACHE:
        return AI_CACHE[cache_key]
    try:
        s = r["scores"]
        m = r["metrics"]
        tech_str = ""
        if tech:
            tech_str = (
                f"Teknik: RSI={tech.get('rsi', '?'):.0f}, "
                f"{'MA50 uzerinde' if tech.get('price', 0) > (tech.get('ma50') or 0) else 'MA50 altinda'}, "
                f"MACD {'bullish' if tech.get('macd_bullish') else 'bearish'}, "
                f"52W high'a {abs(tech.get('pct_from_high', 0)):.0f}% mesafe"
            )
        prompt = (
            f"Sen BIST trader'isin. 2-3 cumle ile yatirim tezi yaz. Turkce.\n"
            f"Hisse: {r['ticker']} ({r['name']})\n"
            f"Stil: {r['style']} | Genel Skor: {r['overall']}/100\n"
            f"Value:{s['value']:.0f} Quality:{s['quality']:.0f} Growth:{s['growth']:.0f} "
            f"Balance:{s['balance']:.0f} Moat:{s['moat']:.0f}\n"
            f"P/E:{fmt_num(m.get('pe'))} ROE:{fmt_pct(m.get('roe'))} "
            f"Net Borc/EBITDA:{fmt_num(m.get('net_debt_ebitda'))}\n"
            f"{tech_str}\n"
            f"Pozitifler: {', '.join(r['positives'])}\n"
            f"Negatifler: {', '.join(r['negatives'])}\n\n"
            f"SADECE 2-3 cumle yaz. Kisa, net, aksiyon odakli. Hic baska birsey yazma."
        )
        client = OpenAI(api_key=OPENAI_KEY)
        resp = client.chat.completions.create(
            model=SCORING_MODEL, max_tokens=200, temperature=0.4,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.choices[0].message.content.strip()
        AI_CACHE[cache_key] = text
        return text
    except Exception as e:
        log.warning(f"AI summary: {e}")
        return None

# ================================================================
# CROSS HUNTER — adapted from fa_bot.py
# ================================================================
class CrossHunter:
    def __init__(self):
        self.last_scan = 0
        self.prev_signals = {}
        self.enabled = True
        self.last_results = []

    def scan_all(self):
        new_signals = []
        all_signals = {}
        SIGNAL_INFO = {
            "Golden Cross": {"icon": "bullish", "explanation": "MA50 yukari kesti MA200'u — orta/uzun vade yukari donusu"},
            "Death Cross": {"icon": "bearish", "explanation": "MA50 asagi kesti MA200'u — orta/uzun vade asagi donusu"},
            "MACD Bullish Cross": {"icon": "bullish", "explanation": "MACD sinyal cizgisini yukari kesti — momentum artiyor"},
            "MACD Bearish Cross": {"icon": "bearish", "explanation": "MACD sinyal cizgisini asagi kesti — momentum zayifliyor"},
            "RSI Asiri Alim": {"icon": "bearish", "explanation": "RSI 70+ — asiri alim, duzeltme riski"},
            "RSI Asiri Satim": {"icon": "bullish", "explanation": "RSI 30- — asiri satim, dip firsati olabilir"},
            "BB Ust Band Kirilim": {"icon": "neutral", "explanation": "Fiyat Bollinger ust bandini kirdi — momentum guclu ama asiri olabilir"},
            "BB Alt Band Kirilim": {"icon": "neutral", "explanation": "Fiyat Bollinger alt bandini kirdi — oversold veya trend devam"},
        }
        for t in UNIVERSE:
            try:
                symbol = normalize_symbol(t)
                tech = compute_technical(symbol)
                if not tech:
                    continue
                signals = set()
                details = {
                    "ticker": t,
                    "price": tech.get("price"),
                    "rsi": tech.get("rsi"),
                    "ma50": tech.get("ma50"),
                    "ma200": tech.get("ma200"),
                    "tech_score": tech.get("tech_score", 50),
                    "vol_ratio": tech.get("vol_ratio"),
                    "pct_from_high": tech.get("pct_from_high"),
                    "macd_bullish": tech.get("macd_bullish"),
                }
                if tech.get("cross_signal") == "GOLDEN_CROSS": signals.add("Golden Cross")
                if tech.get("cross_signal") == "DEATH_CROSS": signals.add("Death Cross")
                if tech.get("rsi") and tech["rsi"] > 70: signals.add("RSI Asiri Alim")
                if tech.get("rsi") and tech["rsi"] < 30: signals.add("RSI Asiri Satim")
                if tech.get("macd_cross") == "BULLISH": signals.add("MACD Bullish Cross")
                if tech.get("macd_cross") == "BEARISH": signals.add("MACD Bearish Cross")
                if tech.get("bb_pos") == "ABOVE": signals.add("BB Ust Band Kirilim")
                if tech.get("bb_pos") == "BELOW": signals.add("BB Alt Band Kirilim")

                all_signals[t] = signals
                prev = self.prev_signals.get(t, set())
                for sig in signals:
                    if sig not in prev:
                        sig_info = SIGNAL_INFO.get(sig, {"icon": "neutral", "explanation": ""})
                        new_signals.append({"signal": sig, "signal_type": sig_info["icon"], "explanation": sig_info["explanation"], **details})
            except Exception as e:
                log.debug(f"CrossHunter {t}: {e}")

        self.prev_signals = all_signals
        self.last_scan = time.time()
        self.last_results = new_signals
        return new_signals

cross_hunter = CrossHunter()

# ================================================================
# SCAN UNIVERSE
# ================================================================
def _analyze_safe(ticker):
    try:
        return analyze_symbol(normalize_symbol(ticker))
    except Exception as e:
        log.debug(f"scan skip {ticker}: {e}")
        return None

def scan_universe_blocking():
    ranked = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_analyze_safe, t): t for t in UNIVERSE}
        for future in as_completed(futures):
            r = future.result()
            if r and r["confidence"] >= CONFIDENCE_MIN:
                ranked.append(r)
    ranked.sort(key=lambda x: (x["overall"], x["scores"]["quality"]), reverse=True)
    TOP10_CACHE["asof"] = dt.datetime.now(dt.timezone.utc)
    TOP10_CACHE["items"] = ranked
    log.info(f"Scan tamamlandi: {len(ranked)} hisse")
    return ranked

# ================================================================
# JSON SERIALIZER HELPER
# ================================================================
def clean_for_json(obj):
    """Recursively clean NaN/Inf and non-serializable types"""
    if isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items()
                if k != "df" and not isinstance(v, pd.DataFrame)}
    elif isinstance(obj, (list, tuple)):
        return [clean_for_json(i) for i in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 4)
    elif isinstance(obj, (np.integer, np.int64)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64)):
        v = float(obj)
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 4)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, dt.datetime):
        return obj.isoformat()
    return obj

# ================================================================
# FASTAPI APP
# ================================================================
@asynccontextmanager
async def lifespan(app):
    log.info(f"BISTBULL TERMINAL starting | Universe: {len(UNIVERSE)} | AI: {'ON' if AI_AVAILABLE and OPENAI_KEY else 'OFF'} | Chart: {'ON' if CHART_AVAILABLE else 'OFF'}")
    yield
    log.info("BISTBULL TERMINAL shutting down")

app = FastAPI(title="BistBull Terminal", version="1.0", lifespan=lifespan)

# ================================================================
# API ENDPOINTS
# ================================================================

@app.get("/api/universe")
async def get_universe():
    return {"universe": UNIVERSE, "count": len(UNIVERSE)}

@app.get("/api/analyze/{ticker}")
async def api_analyze(ticker: str):
    """Full fundamental analysis — 7-dim scoring, Piotroski, Altman, Beneish, Graham, Buffett"""
    symbol = normalize_symbol(ticker)
    try:
        r = await asyncio.to_thread(analyze_symbol, symbol)
        m = r["metrics"]
        if m.get("price") is None and m.get("market_cap") is None and m.get("pe") is None:
            raise ValueError("No data from yfinance")
        return JSONResponse(content=clean_for_json(r))
    except Exception as e:
        log.warning(f"analyze {ticker}: {e}")
        raise HTTPException(status_code=404, detail=f"Veri alinamadi: {base_ticker(ticker)}")

@app.get("/api/technical/{ticker}")
async def api_technical(ticker: str):
    """Technical analysis — RSI, MACD, MA, BB, 52W, volume, components"""
    symbol = normalize_symbol(ticker)
    try:
        tech = await asyncio.to_thread(compute_technical, symbol)
        if not tech:
            raise ValueError("No technical data")
        return JSONResponse(content=clean_for_json(tech))
    except Exception as e:
        log.warning(f"technical {ticker}: {e}")
        raise HTTPException(status_code=404, detail=f"Teknik veri alinamadi: {base_ticker(ticker)}")

@app.get("/api/chart/{ticker}")
async def api_chart(ticker: str):
    """Chart PNG — matplotlib generated"""
    symbol = normalize_symbol(ticker)
    try:
        tech = await asyncio.to_thread(compute_technical, symbol)
        chart_bytes = await asyncio.to_thread(generate_chart_png, symbol, tech)
        if chart_bytes:
            return Response(content=chart_bytes, media_type="image/png")
        raise ValueError("Chart generation failed")
    except Exception as e:
        log.warning(f"chart {ticker}: {e}")
        raise HTTPException(status_code=500, detail="Chart olusturulamadi")

@app.get("/api/ai-summary/{ticker}")
async def api_ai_summary(ticker: str):
    """AI Trader Summary — GPT-4o-mini powered investment thesis"""
    symbol = normalize_symbol(ticker)
    try:
        r = await asyncio.to_thread(analyze_symbol, symbol)
        tech = await asyncio.to_thread(compute_technical, symbol)
        text = await asyncio.to_thread(ai_trader_summary, r, tech)
        return {"ticker": base_ticker(ticker), "summary": text or "AI ozet olusturulamadi (API key kontrol edin)"}
    except Exception as e:
        log.warning(f"ai-summary {ticker}: {e}")
        raise HTTPException(status_code=500, detail="AI ozet alinamadi")

@app.get("/api/top10")
async def api_top10():
    """Top 10 scan — parallel yfinance scan of full universe"""
    if TOP10_CACHE["items"]:
        items = []
        for r in TOP10_CACHE["items"]:
            items.append({
                "ticker": r["ticker"], "name": r["name"],
                "overall": r["overall"], "confidence": r["confidence"],
                "style": r["style"], "scores": r["scores"],
                "sector": r.get("sector", ""), "industry": r.get("industry", ""),
                "legendary": r["legendary"],
                "positives": r["positives"], "negatives": r["negatives"],
                "price": r["metrics"].get("price"),
                "market_cap": r["metrics"].get("market_cap"),
                "pe": r["metrics"].get("pe"),
                "pb": r["metrics"].get("pb"),
                "roe": r["metrics"].get("roe"),
                "revenue_growth": r["metrics"].get("revenue_growth"),
            })
        return {"asof": TOP10_CACHE["asof"].isoformat() if TOP10_CACHE["asof"] else None, "items": clean_for_json(items), "total_scanned": len(UNIVERSE)}
    return {"asof": None, "items": [], "total_scanned": 0, "message": "Henuz taranmadi. /api/scan ile baslatin."}

@app.get("/api/scan")
async def api_scan():
    """Trigger full universe scan"""
    try:
        ranked = await asyncio.to_thread(scan_universe_blocking)
        items = []
        for r in ranked:
            items.append({
                "ticker": r["ticker"], "name": r["name"],
                "overall": r["overall"], "confidence": r["confidence"],
                "style": r["style"], "scores": r["scores"],
                "sector": r.get("sector", ""), "industry": r.get("industry", ""),
                "legendary": r["legendary"],
                "positives": r["positives"], "negatives": r["negatives"],
                "price": r["metrics"].get("price"),
                "market_cap": r["metrics"].get("market_cap"),
                "pe": r["metrics"].get("pe"),
                "pb": r["metrics"].get("pb"),
                "roe": r["metrics"].get("roe"),
                "revenue_growth": r["metrics"].get("revenue_growth"),
            })
        return {"asof": TOP10_CACHE["asof"].isoformat() if TOP10_CACHE["asof"] else None, "items": clean_for_json(items), "total_scanned": len(UNIVERSE)}
    except Exception as e:
        log.error(f"scan: {e}")
        raise HTTPException(status_code=500, detail="Scan basarisiz")

@app.get("/api/cross")
async def api_cross():
    """Cross Hunter — scan for new technical signals"""
    try:
        new_signals = await asyncio.to_thread(cross_hunter.scan_all)
        bullish = sum(1 for s in new_signals if s.get("signal_type") == "bullish")
        bearish = sum(1 for s in new_signals if s.get("signal_type") == "bearish")
        return {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "signals": clean_for_json(new_signals),
            "summary": {"total": len(new_signals), "bullish": bullish, "bearish": bearish, "scanned": len(UNIVERSE)},
        }
    except Exception as e:
        log.error(f"cross: {e}")
        raise HTTPException(status_code=500, detail="Cross Hunter hatasi")

@app.get("/api/health")
async def api_health():
    return {
        "status": "ok",
        "version": BOT_VERSION,
        "app": APP_NAME,
        "universe": len(UNIVERSE),
        "ai": AI_AVAILABLE and bool(OPENAI_KEY),
        "chart": CHART_AVAILABLE,
        "cache": {"raw": len(RAW_CACHE), "analysis": len(ANALYSIS_CACHE), "tech": len(TECH_CACHE)},
    }

# ================================================================
# MACRO RADAR — live macro indicators via yfinance
# ================================================================
MACRO_CACHE = TTLCache(maxsize=50, ttl=300)  # 5 min cache

MACRO_SYMBOLS = {
    "XU030": {"symbol": "XU030.IS", "name": "BIST 30", "category": "borsa"},
    "XU100": {"symbol": "XU100.IS", "name": "BIST 100", "category": "borsa"},
    "USDTRY": {"symbol": "USDTRY=X", "name": "USD/TRY", "category": "doviz"},
    "EURTRY": {"symbol": "EURTRY=X", "name": "EUR/TRY", "category": "doviz"},
    "BRENT": {"symbol": "BZ=F", "name": "Brent Petrol", "category": "emtia"},
    "GOLD": {"symbol": "GC=F", "name": "Altin (oz)", "category": "emtia"},
    "DXY": {"symbol": "DX-Y.NYB", "name": "Dolar Endeksi", "category": "global"},
    "VIX": {"symbol": "^VIX", "name": "Korku Endeksi", "category": "global"},
    "SP500": {"symbol": "^GSPC", "name": "S&P 500", "category": "global"},
}

def _fetch_macro_item(key, info):
    try:
        tk = yf.Ticker(info["symbol"])
        h = tk.history(period="5d", interval="1d")
        if h is None or h.empty:
            return None
        price = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2]) if len(h) >= 2 else price
        change = price - prev
        change_pct = (change / prev * 100) if prev != 0 else 0
        return {
            "key": key, "name": info["name"], "category": info["category"],
            "price": round(price, 4), "change": round(change, 4),
            "change_pct": round(change_pct, 2),
        }
    except Exception as e:
        log.debug(f"Macro {key}: {e}")
        return None

@app.get("/api/macro")
async def api_macro():
    cache_key = "macro_all"
    if cache_key in MACRO_CACHE:
        return MACRO_CACHE[cache_key]
    try:
        results = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_fetch_macro_item, k, v): k for k, v in MACRO_SYMBOLS.items()}
            for f in as_completed(futures):
                r = f.result()
                if r:
                    results.append(r)
        result = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "items": clean_for_json(results)}
        MACRO_CACHE[cache_key] = result
        return result
    except Exception as e:
        log.error(f"macro: {e}")
        raise HTTPException(status_code=500, detail="Macro veri alinamadi")

# ================================================================
# DASHBOARD SUMMARY — aggregated hero data
# ================================================================
@app.get("/api/dashboard")
async def api_dashboard():
    """Aggregated dashboard: top picks, risks, opportunities, sector breakdown, counters"""
    items = TOP10_CACHE.get("items", [])
    scanned = len(items)

    # Top 3 picks (highest overall)
    top3 = []
    for r in items[:3]:
        top3.append({
            "ticker": r["ticker"], "name": r["name"], "overall": r["overall"],
            "style": r["style"], "scores": r["scores"],
            "price": r["metrics"].get("price"),
            "positives": r["positives"][:2],
        })

    # Top 3 opportunities (high value + growth combo)
    opps = sorted([r for r in items if r["scores"].get("value", 0) >= 55],
                  key=lambda x: x["scores"].get("value", 0) + x["scores"].get("growth", 0), reverse=True)
    opportunities = []
    for r in opps[:3]:
        opportunities.append({
            "ticker": r["ticker"], "name": r["name"], "overall": r["overall"],
            "reason": f"Value: {r['scores']['value']:.0f} + Growth: {r['scores']['growth']:.0f}",
            "price": r["metrics"].get("price"),
        })

    # Top 3 risks (low balance or low overall)
    risky = sorted([r for r in items if r["scores"].get("balance", 100) < 50 or r["overall"] < 40],
                   key=lambda x: x["overall"])
    risks = []
    for r in risky[:3]:
        risks.append({
            "ticker": r["ticker"], "name": r["name"], "overall": r["overall"],
            "reason": "; ".join(r["negatives"][:2]),
            "price": r["metrics"].get("price"),
        })

    # Sector breakdown
    sector_map = defaultdict(lambda: {"count": 0, "avg_score": 0, "tickers": []})
    for r in items:
        sec = r.get("sector") or "Diger"
        sector_map[sec]["count"] += 1
        sector_map[sec]["avg_score"] += r["overall"]
        sector_map[sec]["tickers"].append(r["ticker"])
    sectors = []
    for sec, data in sector_map.items():
        sectors.append({
            "sector": sec,
            "count": data["count"],
            "avg_score": round(data["avg_score"] / max(data["count"], 1), 1),
            "tickers": data["tickers"][:5],
        })
    sectors.sort(key=lambda x: x["avg_score"], reverse=True)

    # Style distribution
    style_map = defaultdict(int)
    for r in items:
        style_map[r["style"]] += 1

    return {
        "scanned": scanned,
        "asof": TOP10_CACHE["asof"].isoformat() if TOP10_CACHE.get("asof") else None,
        "top3": clean_for_json(top3),
        "opportunities": clean_for_json(opportunities),
        "risks": clean_for_json(risks),
        "sectors": clean_for_json(sectors),
        "styles": dict(style_map),
        "counters": {
            "total_analyzed": scanned,
            "cache_raw": len(RAW_CACHE),
            "cache_tech": len(TECH_CACHE),
            "cross_signals": len(cross_hunter.last_results),
        },
    }

# ================================================================
# BRIEFING — AI-generated market briefing
# ================================================================
BRIEFING_CACHE = TTLCache(maxsize=10, ttl=3600)

@app.get("/api/briefing")
async def api_briefing():
    if not AI_AVAILABLE or not OPENAI_KEY:
        return {"briefing": None, "error": "AI pasif — OPENAI_KEY gerekli"}
    cache_key = "daily_briefing"
    if cache_key in BRIEFING_CACHE:
        return BRIEFING_CACHE[cache_key]
    try:
        items = TOP10_CACHE.get("items", [])
        if not items:
            return {"briefing": "Henuz tarama yapilmadi. Once SCAN calistirin.", "generated": False}

        top5 = items[:5]
        summary_parts = []
        for r in top5:
            summary_parts.append(f"{r['ticker']}: {r['overall']}/100 ({r['style']}), V:{r['scores']['value']:.0f} Q:{r['scores']['quality']:.0f} G:{r['scores']['growth']:.0f} B:{r['scores']['balance']:.0f}")

        prompt = (
            "Sen BistBull Terminal'in piyasa analisti yazarisin. Turkce yaz.\n"
            "Asagidaki BIST hisse tarama sonuclarina gore kisa bir 'GUNUN OZETI' yaz.\n"
            "3-4 cumle: genel piyasa durumu, en dikkat cekici hisseler, firsatlar ve riskler.\n"
            "Sonra ayri 1-2 cumle: 'BUGUNKU STRATEJI' onerisi.\n"
            "Profesyonel, net, aksiyona yonelik.\n\n"
            f"Top 5 hisse:\n" + "\n".join(summary_parts) + "\n\n"
            f"Toplam taranan: {len(items)} hisse\n"
            "SADECE ozet ve strateji yaz. Baska birsey ekleme."
        )
        client = OpenAI(api_key=OPENAI_KEY)
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=SCORING_MODEL, max_tokens=400, temperature=0.5,
                messages=[{"role": "user", "content": prompt}]
            )
        )
        text = resp.choices[0].message.content.strip()
        result = {"briefing": text, "generated": True, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}
        BRIEFING_CACHE[cache_key] = result
        return result
    except Exception as e:
        log.warning(f"briefing: {e}")
        return {"briefing": None, "error": str(e)}

# ================================================================
# QUICK ANALYZE — batch analyze multiple tickers at once
# ================================================================
@app.get("/api/batch/{tickers}")
async def api_batch(tickers: str):
    """Analyze up to 5 tickers at once: /api/batch/ASELS,THYAO,BIMAS"""
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()][:5]
    results = []
    for t in ticker_list:
        try:
            r = await asyncio.to_thread(analyze_symbol, normalize_symbol(t))
            results.append({
                "ticker": r["ticker"], "name": r["name"], "overall": r["overall"],
                "confidence": r["confidence"], "style": r["style"],
                "scores": r["scores"], "legendary": r["legendary"],
                "positives": r["positives"], "negatives": r["negatives"],
                "price": r["metrics"].get("price"),
                "pe": r["metrics"].get("pe"),
                "roe": r["metrics"].get("roe"),
                "revenue_growth": r["metrics"].get("revenue_growth"),
                "market_cap": r["metrics"].get("market_cap"),
            })
        except Exception as e:
            results.append({"ticker": t, "error": str(e)})
    return {"items": clean_for_json(results)}

# Serve frontend — read index.html from same directory as app.py, no static/ folder needed
_INDEX_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    try:
        with open(_INDEX_HTML_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>BistBull Terminal</h1><p>index.html bulunamadi</p>", status_code=500)

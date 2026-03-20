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

# AI — Grok preferred (X/Twitter data), OpenAI fallback, Anthropic last
AI_PROVIDERS = []  # ordered list
try:
    from openai import OpenAI as _OpenAI  # Grok uses OpenAI-compatible API
    if os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY"):
        AI_PROVIDERS.append("grok")
    if os.environ.get("OPENAI_KEY") or os.environ.get("OPENAI_API_KEY"):
        AI_PROVIDERS.append("openai")
except ImportError:
    pass
try:
    import anthropic as _anthropic
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_KEY"):
        AI_PROVIDERS.append("anthropic")
except ImportError:
    pass

AI_AVAILABLE = len(AI_PROVIDERS) > 0

BOT_VERSION = "V7"
APP_NAME = "BISTBULL TERMINAL"
CONFIDENCE_MIN = 55

# ================================================================
# ENV VARS
# ================================================================
GROK_KEY = os.environ.get("XAI_API_KEY", "") or os.environ.get("GROK_API_KEY", "")
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-3-mini-fast")
OPENAI_KEY = os.environ.get("OPENAI_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_KEY", "")
ANTHROPIC_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-4-20250514")

def _call_grok(prompt, max_tokens):
    client = _OpenAI(api_key=GROK_KEY, base_url="https://api.x.ai/v1")
    resp = client.chat.completions.create(
        model=GROK_MODEL, max_tokens=max_tokens, temperature=0.4,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.choices[0].message.content.strip()

def _call_openai(prompt, max_tokens):
    client = _OpenAI(api_key=OPENAI_KEY)
    resp = client.chat.completions.create(
        model=OPENAI_MODEL, max_tokens=max_tokens, temperature=0.4,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.choices[0].message.content.strip()

def _call_anthropic(prompt, max_tokens):
    client = _anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    resp = client.messages.create(
        model=ANTHROPIC_MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text.strip()

def ai_call(prompt, max_tokens=200):
    """Try each AI provider in order: Grok → OpenAI → Anthropic"""
    for provider in AI_PROVIDERS:
        try:
            if provider == "grok":
                return _call_grok(prompt, max_tokens)
            elif provider == "openai":
                return _call_openai(prompt, max_tokens)
            elif provider == "anthropic":
                return _call_anthropic(prompt, max_tokens)
        except Exception as e:
            log.warning(f"AI {provider} failed: {e}")
            continue
    return None

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
HISTORY_CACHE = TTLCache(maxsize=500, ttl=3600)  # shared price history for tech + cross
TOP10_CACHE = {"asof": None, "items": []}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bistbull")

# ================================================================
# BATCH HISTORY DOWNLOAD — single yf.download for all symbols
# ================================================================
def batch_download_history(symbols, period="1y", interval="1d"):
    result = {}
    try:
        if not symbols: return result
        df = yf.download(symbols, period=period, interval=interval,
                         group_by="ticker", progress=False, threads=True)
        if df is None or df.empty: return result
        for sym in symbols:
            try:
                if len(symbols) == 1:
                    ticker_df = df
                else:
                    if sym in df.columns.get_level_values(0):
                        ticker_df = df[sym].dropna(how="all")
                    else: continue
                if ticker_df is not None and not ticker_df.empty and len(ticker_df) >= 20:
                    result[sym] = ticker_df
            except Exception: continue
    except Exception as e:
        log.warning(f"batch_download_history: {e}")
    return result

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

    # V7: Institutional holders — yabanci/kurum proxy
    inst_holders_pct = safe_num(info.get("heldPercentInstitutions"))

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
        "inst_holders_pct": inst_holders_pct,  # V7: yabanci/kurum orani proxy
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

# ================================================================
# V7: 3 YENi HIBRIT SKORLAR — Momentum, Technical Break, Institutional Flow
# Teknik veriyi (compute_technical output) kullanir
# ================================================================

def score_momentum(m, tech):
    """Momentum skoru (0-100) — fiyat trendi, hacim, RSI bazli
    - RSI_14 > 50 → +30
    - Price > MA50 ve MA50 > MA200 (Golden Cross pozisyon) → +30
    - Volume ratio (5d/20d) > 2.0 → +25
    - 20 gunluk fiyat degisimi > %15 → +15
    """
    if tech is None:
        return None
    pts = 0.0
    components = 0

    # RSI > 50 → momentum pozitif
    rsi = safe_num(tech.get("rsi"))
    if rsi is not None:
        components += 1
        if rsi > 70:
            pts += 30  # cok guclu ama asiri alim riski — tam puan ver momentum icin
        elif rsi > 50:
            pts += 30 * ((rsi - 50) / 20)  # 50-70 arasi lineer interpolasyon (0-30)
        # RSI < 50 → 0 puan

    # Price > MA50 AND MA50 > MA200 → guclu yukari trend
    price = safe_num(tech.get("price"))
    ma50 = safe_num(tech.get("ma50"))
    ma200 = safe_num(tech.get("ma200"))
    if price is not None and ma50 is not None:
        components += 1
        if ma200 is not None and price > ma50 and ma50 > ma200:
            pts += 30  # tam golden cross pozisyonu
        elif price > ma50:
            pts += 15  # sadece MA50 uzerinde

    # Volume ratio — son 5 gunluk hacim / 20 gunluk ortalama
    vol_ratio = safe_num(tech.get("vol_ratio"))
    if vol_ratio is not None:
        components += 1
        if vol_ratio > 2.0:
            pts += 25
        elif vol_ratio > 1.5:
            pts += 15
        elif vol_ratio > 1.2:
            pts += 8

    # 20 gunluk fiyat degisimi (price_history'den hesapla)
    ph = tech.get("price_history")
    if ph and len(ph) >= 20 and price is not None:
        components += 1
        price_20d_ago = safe_num(ph[-20].get("close")) if len(ph) >= 20 else None
        if price_20d_ago and price_20d_ago > 0:
            chg_20d = (price - price_20d_ago) / price_20d_ago
            if chg_20d > 0.15:
                pts += 15
            elif chg_20d > 0.08:
                pts += 10
            elif chg_20d > 0.03:
                pts += 5

    if components == 0:
        return None
    # Normalize: max 100 puan
    return min(round(pts, 1), 100.0)


def score_technical_break(m, tech):
    """Technical Break skoru (0-100) — kirilim sinyalleri
    - Fiyat direnc kirilimi (52W high'a %5 icinde veya uzerinde) → +40
    - Golden Cross tespit edildi → +30
    - Bollinger Upper Band kirilimi → +20
    - Bullish RSI divergence (RSI yukselen + fiyat dusuk → basit proxy) → +10
    """
    if tech is None:
        return None
    pts = 0.0
    components = 0

    # Direnc kirilimi: 52W high'a yakinlik
    price = safe_num(tech.get("price"))
    pct_from_high = safe_num(tech.get("pct_from_high"))
    if price is not None and pct_from_high is not None:
        components += 1
        if pct_from_high >= 0:
            pts += 40  # 52W high'in UZERINDE — tam kirilim
        elif pct_from_high >= -5:
            pts += 30  # %5 icinde — kirilim yaklasiyor
        elif pct_from_high >= -10:
            pts += 15  # %10 icinde — potansiyel

    # Golden Cross
    cross_signal = tech.get("cross_signal")
    if cross_signal is not None:
        components += 1
        if cross_signal == "GOLDEN_CROSS":
            pts += 30
    elif tech.get("ma50") and tech.get("ma200"):
        components += 1
        # MA50 > MA200 ama golden cross yeni olmasa da — trend puan
        if safe_num(tech.get("ma50")) > safe_num(tech.get("ma200")):
            pts += 15

    # Bollinger Upper Band kirilimi
    bb_pos = tech.get("bb_pos")
    if bb_pos is not None:
        components += 1
        if bb_pos == "ABOVE":
            pts += 20  # ust band kirildi — momentum guclu
        elif bb_pos == "INSIDE":
            pts += 5   # band icinde — notr

    # Bullish RSI divergence proxy:
    # RSI yukseliyor + MACD bullish cross → divergence sinyali
    macd_cross = tech.get("macd_cross")
    rsi = safe_num(tech.get("rsi"))
    if macd_cross is not None and rsi is not None:
        components += 1
        if macd_cross == "BULLISH" and rsi < 60:
            pts += 10  # MACD bullish cross + RSI henuz asiri alimda degil
        elif macd_cross == "BULLISH":
            pts += 5

    if components == 0:
        return None
    return min(round(pts, 1), 100.0)


def score_institutional_flow(m, tech):
    """Institutional Flow skoru (0-100) — kurum/yabanci akisi proxy
    Gercek MKK/takas verisi yok → yfinance proxy kullan:
    - yfinance heldPercentInstitutions yuksekse → +40
    - Hacim spike + fiyat artisi birlikteyse (kurum alim proxy) → +30
    - Volume trend yukari + MA50 uzerinde (akümülasyon) → +30
    """
    pts = 0.0
    components = 0

    # Institutional ownership from yfinance (proxy for yabanci orani)
    inst_pct = safe_num(m.get("inst_holders_pct"))
    if inst_pct is not None:
        components += 1
        if inst_pct > 0.70:
            pts += 40  # %70+ kurum/yabanci — cok guclu
        elif inst_pct > 0.50:
            pts += 30
        elif inst_pct > 0.30:
            pts += 20
        elif inst_pct > 0.10:
            pts += 10

    if tech is not None:
        # Hacim spike + fiyat artisi (kurum alim proxy)
        vol_ratio = safe_num(tech.get("vol_ratio"))
        ph = tech.get("price_history")
        if vol_ratio is not None and ph and len(ph) >= 5:
            components += 1
            price_now = safe_num(ph[-1].get("close"))
            price_5d = safe_num(ph[-5].get("close"))
            if price_now and price_5d and price_5d > 0:
                chg_5d = (price_now - price_5d) / price_5d
                if vol_ratio > 1.5 and chg_5d > 0.03:
                    pts += 30  # hacim yuksek + fiyat artiyor → kurum aliyor
                elif vol_ratio > 1.2 and chg_5d > 0.01:
                    pts += 15
                elif vol_ratio > 1.5 and chg_5d < -0.03:
                    pts += 0   # hacim yuksek + fiyat dusuyor → kurum satiyor

        # Volume trend + MA50 uzerinde (akumulasyon)
        price = safe_num(tech.get("price"))
        ma50 = safe_num(tech.get("ma50"))
        if price is not None and ma50 is not None and vol_ratio is not None:
            components += 1
            if price > ma50 and vol_ratio > 1.0:
                pts += 30  # fiyat MA50 uzerinde + hacim ortalamanin uzerinde
            elif price > ma50:
                pts += 15  # fiyat MA50 uzerinde ama hacim dusuk
            elif vol_ratio > 1.5:
                pts += 5   # fiyat altinda ama hacim yuksek — potansiyel dip alim

    if components == 0:
        return None
    return min(round(pts, 1), 100.0)


def confidence_score(m):
    # V7: yeni metrikler eklendi
    keys = ["pe","pb","fcf_yield","roe","roic","operating_margin","revenue_growth","eps_growth",
            "net_debt_ebitda","interest_coverage","cfo_to_ni","piotroski_f","altman_z","peg","margin_safety",
            "inst_holders_pct"]
    have = sum(1 for k in keys if safe_num(m.get(k)) is not None)
    return round(100 * have / len(keys), 1)

def style_label(scores):
    v, q, g, moat = scores["value"], scores["quality"], scores["growth"], scores["moat"]
    bal = scores["balance"]
    mom = scores.get("momentum", 50)
    tb = scores.get("tech_break", 50)
    # V7: Momentum-bazli yeni stiller eklendi
    if mom >= 75 and tb >= 70 and g >= 55: return "Momentum Leader"
    if q >= 75 and g >= 60 and v >= 40 and moat >= 60: return "Quality Compounder"
    if q >= 72 and moat >= 65 and v < 40: return "Premium Compounder"
    if v >= 75 and bal >= 55: return "Deep Value"
    if g >= 70 and v >= 45: return "GARP"
    if mom >= 65 and tb >= 60 and v < 45: return "Teknik Breakout"
    if g >= 65 and q >= 55 and v < 45: return "Growth"
    if v >= 70 and q < 45: return "Value Trap Risk"
    if bal < 40 and g >= 50: return "High-Risk Turnaround"
    if scores.get("capital", 50) >= 70 and q >= 55: return "Income / Dividend"
    if mom < 30 and tb < 30: return "Momentum Zayif"
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
    # V7: Yeni boyutlar
    if scores.get("momentum", 50) >= 70: pos.append("Guclu momentum — fiyat ve hacim destekli")
    if scores.get("tech_break", 50) >= 70: pos.append("Teknik kirilim sinyali aktif")
    if scores.get("inst_flow", 50) >= 65: pos.append("Kurumsal/yabanci alis akisi pozitif")
    if not pos: pos.append("Balanced profile, no single elite category")
    if scores["value"] < 40: neg.append("Valuation looks expensive")
    if scores["quality"] < 40: neg.append("Low profitability — margins or ROIC weak")
    if scores["growth"] < 40: neg.append("Growth weak or inconsistent")
    if scores["balance"] < 40: neg.append("Debt / liquidity needs watch")
    if scores["earnings"] < 40: neg.append("Cash flow trails accounting profits")
    if scores["moat"] < 35: neg.append("Margin stability weak — no pricing power")
    # V7: Yeni negatifler
    if scores.get("momentum", 50) < 30: neg.append("Momentum cok zayif — dusus trendinde")
    if scores.get("tech_break", 50) < 25: neg.append("Teknik goruntu olumsuz — direnc uzak")
    if scores.get("inst_flow", 50) < 25: neg.append("Kurumsal ilgi dusuk veya satis baskisi")
    if confidence < 65: neg.append("Some metrics missing; treat with caution")
    if not neg: neg.append("No major red flag right now")
    return pos[:5], neg[:5]  # V7: 4'ten 5'e cikardi — daha fazla insight

# ================================================================
# ANALYZE — V7: 9 Boyutlu Hibrit Skorlama
# Temel 7 boyut korundu + Momentum, Technical Break, Institutional Flow eklendi
# ================================================================
def analyze_symbol(symbol):
    if symbol in ANALYSIS_CACHE: return ANALYSIS_CACHE[symbol]
    m = compute_metrics(symbol)

    # V7: Teknik veriyi al — yeni 3 boyut icin gerekli
    tech = None
    try:
        tech = compute_technical(symbol)
    except Exception as e:
        log.debug(f"analyze_symbol tech for {symbol}: {e}")

    # Mevcut 7 temel boyut (korundu)
    scores = {k: round((f(m) or 50), 1) for k, f in [
        ("value", score_value), ("quality", score_quality), ("growth", score_growth),
        ("balance", score_balance), ("earnings", score_earnings), ("moat", score_moat), ("capital", score_capital),
    ]}

    # V7: Yeni 3 hibrit boyut — teknik veri gerektirir
    mom = score_momentum(m, tech)
    tb = score_technical_break(m, tech)
    inst = score_institutional_flow(m, tech)
    scores["momentum"] = round(mom, 1) if mom is not None else 50.0
    scores["tech_break"] = round(tb, 1) if tb is not None else 50.0
    scores["inst_flow"] = round(inst, 1) if inst is not None else 50.0

    # V7: 9 Boyutlu Agirlikli Ortalama (toplam %100)
    # Value:14 Quality:14 Growth:12 Balance:12 Earnings:10 Moat:10
    # Momentum:12 Tech Break:10 Inst Flow:6
    overall = (
        0.14 * scores["value"]
      + 0.14 * scores["quality"]
      + 0.12 * scores["growth"]
      + 0.12 * scores["balance"]
      + 0.10 * scores["earnings"]
      + 0.10 * scores["moat"]
      + 0.12 * scores["momentum"]
      + 0.10 * scores["tech_break"]
      + 0.06 * scores["inst_flow"]
    )

    # Penalti / bonus kuralları korundu
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
def compute_technical(symbol, hist_df=None):
    if symbol in TECH_CACHE:
        return TECH_CACHE[symbol]
    try:
        # Use provided history, shared cache, or fetch fresh
        if hist_df is not None and len(hist_df) >= 50:
            df = hist_df
        elif symbol in HISTORY_CACHE:
            df = HISTORY_CACHE[symbol]
        else:
            tk = yf.Ticker(symbol)
            df = tk.history(period="1y", interval="1d")
            if df is not None and not df.empty:
                HISTORY_CACHE[symbol] = df
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
    if not AI_AVAILABLE:
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
        text = ai_call(prompt, max_tokens=200)
        if text:
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
        # Batch download history for all stocks at once (single yf.download)
        symbols = [normalize_symbol(t) for t in UNIVERSE]
        history_map = batch_download_history(symbols, period="1y", interval="1d")
        # Cache downloaded history for reuse
        for sym, hist_df in history_map.items():
            HISTORY_CACHE[sym] = hist_df

        for t in UNIVERSE:
            try:
                symbol = normalize_symbol(t)
                hist_df = history_map.get(symbol)
                tech = compute_technical(symbol, hist_df=hist_df)
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
    with ThreadPoolExecutor(max_workers=15) as pool:
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
# BACKGROUND AUTO-SCAN — runs on startup, then every 2 hours
# ================================================================
async def _background_scanner():
    await asyncio.sleep(3)  # let server start
    while True:
        try:
            log.info("Background scan basladi...")
            await asyncio.to_thread(scan_universe_blocking)
            await asyncio.to_thread(cross_hunter.scan_all)
            log.info(f"Background scan tamamlandi. {len(TOP10_CACHE['items'])} hisse, {len(cross_hunter.last_results)} sinyal")
        except Exception as e:
            log.error(f"Background scan hatasi: {e}")
        await asyncio.sleep(7200)  # every 2 hours

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
    log.info(f"BISTBULL TERMINAL V7 starting | Universe: {len(UNIVERSE)} | AI: {','.join(AI_PROVIDERS) or 'OFF'} | Chart: {'ON' if CHART_AVAILABLE else 'OFF'}")
    task = asyncio.create_task(_background_scanner())
    yield
    task.cancel()
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
    return {"asof": None, "items": [], "total_scanned": 0, "message": "Tarama devam ediyor..."}

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
        "ai": AI_PROVIDERS or False,
        "chart": CHART_AVAILABLE,
        "scanning": TOP10_CACHE["asof"] is None,
        "cache": {"raw": len(RAW_CACHE), "analysis": len(ANALYSIS_CACHE), "tech": len(TECH_CACHE)},
    }

# ================================================================
# MACRO RADAR — expanded with EM indices + YTD/1M/1W
# ================================================================
MACRO_CACHE = TTLCache(maxsize=50, ttl=600)  # 10 min cache

MACRO_SYMBOLS = {
    # Turkiye
    "XU030": {"symbol": "XU030.IS", "name": "BIST 30", "category": "turkiye", "flag": "🇹🇷"},
    "XU100": {"symbol": "XU100.IS", "name": "BIST 100", "category": "turkiye", "flag": "🇹🇷"},
    "USDTRY": {"symbol": "USDTRY=X", "name": "USD/TRY", "category": "turkiye", "flag": "🇹🇷"},
    "EURTRY": {"symbol": "EURTRY=X", "name": "EUR/TRY", "category": "turkiye", "flag": "🇹🇷"},
    # Emerging Markets
    "EEM": {"symbol": "EEM", "name": "iShares EM ETF", "category": "em", "flag": "🌍"},
    "IBOV": {"symbol": "^BVSP", "name": "Bovespa (Brezilya)", "category": "em", "flag": "🇧🇷"},
    "SENSEX": {"symbol": "^BSESN", "name": "Sensex (Hindistan)", "category": "em", "flag": "🇮🇳"},
    "MEXIPC": {"symbol": "^MXX", "name": "IPC (Meksika)", "category": "em", "flag": "🇲🇽"},
    "JCI": {"symbol": "^JKSE", "name": "JCI (Endonezya)", "category": "em", "flag": "🇮🇩"},
    "JSE": {"symbol": "^JN0U.JO", "name": "JSE Top40 (G.Afrika)", "category": "em", "flag": "🇿🇦"},
    "KOSPI": {"symbol": "^KS11", "name": "KOSPI (G.Kore)", "category": "em", "flag": "🇰🇷"},
    "TWSE": {"symbol": "^TWII", "name": "TAIEX (Tayvan)", "category": "em", "flag": "🇹🇼"},
    "WIG20": {"symbol": "WIG20.WA", "name": "WIG20 (Polonya)", "category": "em", "flag": "🇵🇱"},
    "CSI300": {"symbol": "000300.SS", "name": "CSI 300 (Cin)", "category": "em", "flag": "🇨🇳"},
    # Global
    "SP500": {"symbol": "^GSPC", "name": "S&P 500", "category": "global", "flag": "🇺🇸"},
    "NASDAQ": {"symbol": "^IXIC", "name": "Nasdaq", "category": "global", "flag": "🇺🇸"},
    "DAX": {"symbol": "^GDAXI", "name": "DAX (Almanya)", "category": "global", "flag": "🇩🇪"},
    "FTSE": {"symbol": "^FTSE", "name": "FTSE 100 (UK)", "category": "global", "flag": "🇬🇧"},
    "NIKKEI": {"symbol": "^N225", "name": "Nikkei 225 (Japonya)", "category": "global", "flag": "🇯🇵"},
    # Emtia & Volatilite
    "BRENT": {"symbol": "BZ=F", "name": "Brent Petrol", "category": "emtia", "flag": "🛢️"},
    "GOLD": {"symbol": "GC=F", "name": "Altin (oz)", "category": "emtia", "flag": "🥇"},
    "SILVER": {"symbol": "SI=F", "name": "Gumus (oz)", "category": "emtia", "flag": "🥈"},
    "DXY": {"symbol": "DX-Y.NYB", "name": "Dolar Endeksi", "category": "emtia", "flag": "💵"},
    "VIX": {"symbol": "^VIX", "name": "VIX (Korku)", "category": "emtia", "flag": "😱"},
}

def _fetch_macro_item(key, info):
    try:
        tk = yf.Ticker(info["symbol"])
        # Get YTD data (from Jan 1 to now)
        now = dt.datetime.now()
        ytd_start = dt.datetime(now.year, 1, 1)
        h = tk.history(start=ytd_start, interval="1d")
        if h is None or h.empty or len(h) < 2:
            return None
        price = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2])
        change = price - prev
        change_pct = (change / prev * 100) if prev != 0 else 0

        # YTD performance
        first_close = float(h["Close"].iloc[0])
        ytd_pct = ((price - first_close) / first_close * 100) if first_close != 0 else 0

        # 1M performance (last ~22 trading days)
        m1_pct = None
        if len(h) >= 22:
            m1_close = float(h["Close"].iloc[-22])
            m1_pct = ((price - m1_close) / m1_close * 100) if m1_close != 0 else 0

        # 1W performance (last ~5 trading days)
        w1_pct = None
        if len(h) >= 5:
            w1_close = float(h["Close"].iloc[-5])
            w1_pct = ((price - w1_close) / w1_close * 100) if w1_close != 0 else 0

        return {
            "key": key, "name": info["name"], "category": info["category"],
            "flag": info.get("flag", ""),
            "price": round(price, 4), "change": round(change, 4),
            "change_pct": round(change_pct, 2),
            "ytd_pct": round(ytd_pct, 2),
            "m1_pct": round(m1_pct, 2) if m1_pct is not None else None,
            "w1_pct": round(w1_pct, 2) if w1_pct is not None else None,
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
        with ThreadPoolExecutor(max_workers=8) as pool:
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
    if not AI_AVAILABLE:
        return {"briefing": None, "error": "AI pasif — ANTHROPIC_API_KEY veya OPENAI_KEY gerekli"}
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
        text = await asyncio.to_thread(ai_call, prompt, 400)
        result = {"briefing": text, "generated": True, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}
        BRIEFING_CACHE[cache_key] = result
        # Save to history
        hour = dt.datetime.now().hour
        period = "sabah" if hour < 12 else "oglen" if hour < 17 else "aksam"
        BRIEFING_HISTORY.append({"text": text, "period": period, "timestamp": result["timestamp"]})
        if len(BRIEFING_HISTORY) > 10: BRIEFING_HISTORY.pop(0)
        return result
    except Exception as e:
        log.warning(f"briefing: {e}")
        return {"briefing": None, "error": str(e)}

# ================================================================
# MACRO AI COMMENTARY
# ================================================================
MACRO_AI_CACHE = TTLCache(maxsize=5, ttl=3600)

@app.get("/api/macro/commentary")
async def api_macro_commentary():
    if not AI_AVAILABLE:
        return {"commentary": None, "error": "AI pasif — ANTHROPIC_API_KEY veya OPENAI_KEY gerekli"}
    cache_key = "macro_ai"
    if cache_key in MACRO_AI_CACHE:
        return MACRO_AI_CACHE[cache_key]
    try:
        macro_data = MACRO_CACHE.get("macro_all")
        if not macro_data or not macro_data.get("items"):
            return {"commentary": "Makro veri henuz yuklenmedi. Sayfayi yenileyin.", "generated": False}

        items = macro_data["items"]
        lines = []
        for m in sorted(items, key=lambda x: x.get("ytd_pct") or 0, reverse=True):
            lines.append(f"{m.get('flag','')} {m['name']}: {m['price']}, gun:{m['change_pct']}%, YTD:{m.get('ytd_pct','?')}%")

        prompt = (
            "Sen BistBull Terminal'in makro analistisin. Turkce yaz.\n"
            "Asagidaki global makro verilere bakarak 3-4 cumle ile BUGUNKU MAKRO TABLO'yu ozetle.\n"
            "Ozellikle: Turkiye EM akranlarina karsi nasil? Riskler ne? Firsatlar ne?\n"
            "DXY, VIX, petrol, altin hareketlerinin BIST'e etkisini 1 cumle ile belirt.\n"
            "Sonra 1 cumle STRATEJI onerisi.\n"
            "Net, kisa, profesyonel.\n\n"
            + "\n".join(lines[:20])
        )
        text = await asyncio.to_thread(ai_call, prompt, 300)
        result = {"commentary": text, "generated": True, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}
        MACRO_AI_CACHE[cache_key] = result
        return result
    except Exception as e:
        log.warning(f"macro_commentary: {e}")
        return {"commentary": None, "error": str(e)}

# ================================================================
# TAKAS — Yabancı oranları (İş Yatırım public API)
# Domain whitelist gerekli: isyatirim.com.tr
# ================================================================
TAKAS_CACHE = TTLCache(maxsize=50, ttl=1800)  # 30 min cache

def _fetch_takas_isyatirim():
    """İş Yatırım hisse ekranından yabancı payı verisi çek"""
    import requests
    url = "https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/StockScreener"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/default.aspx",
            "Content-Type": "application/json"
        }
        # İş Yatırım screener POST payload
        payload = {
            "filterValues": [],
            "pageSize": 500,
            "pageNo": 1,
            "sortField": "FOREIGN_RATIO",
            "sortAsc": "false"
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("d", {}).get("Data", []) if isinstance(data.get("d"), dict) else []
            if not items:
                # Try alternate response format
                items = data.get("value", []) or data.get("data", []) or []
            results = []
            for item in items:
                ticker = item.get("HISSE_KODU") or item.get("Ticker") or item.get("hisse_kodu")
                if not ticker:
                    continue
                foreign = item.get("YABANCI_ORAN") or item.get("FOREIGN_RATIO") or item.get("foreignRatio")
                price = item.get("KAPANIS") or item.get("CLOSE") or item.get("close")
                change = item.get("YUZDE") or item.get("CHANGE_PERCENT") or item.get("changePercent")
                results.append({
                    "ticker": str(ticker).upper(),
                    "foreign_pct": round(float(foreign), 2) if foreign is not None else None,
                    "price": round(float(price), 2) if price is not None else None,
                    "change_pct": round(float(change), 2) if change is not None else None,
                })
            return results
    except Exception as e:
        log.warning(f"Takas isyatirim: {e}")
    return None

def _fetch_takas_yfinance():
    """Fallback: yfinance ile major_holders'dan yabancı payı tahmini"""
    results = []
    for ticker in UNIVERSE[:20]:  # ilk 20 hisse (hız için)
        try:
            tk = yf.Ticker(normalize_symbol(ticker))
            info = tk.get_info() or {}
            # yfinance'de institutionalHolders proxy olarak kullanılabilir
            inst_pct = info.get("heldPercentInstitutions")
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            results.append({
                "ticker": ticker,
                "foreign_pct": round(inst_pct * 100, 2) if inst_pct is not None else None,
                "price": round(float(price), 2) if price is not None else None,
                "change_pct": None,
                "source": "yfinance_institutional"
            })
        except Exception:
            continue
    return results if results else None

@app.get("/api/takas")
async def api_takas():
    """Yabancı takas oranları — İş Yatırım veya yfinance fallback"""
    cache_key = "takas_all"
    if cache_key in TAKAS_CACHE:
        return TAKAS_CACHE[cache_key]
    try:
        # Try İş Yatırım first
        data = await asyncio.to_thread(_fetch_takas_isyatirim)
        source = "isyatirim"
        if not data:
            # Fallback to yfinance institutional holders
            data = await asyncio.to_thread(_fetch_takas_yfinance)
            source = "yfinance"
        if not data:
            return {"items": [], "source": None, "error": "Takas verisi alinamadi. isyatirim.com.tr domain whitelist'e ekleyin."}

        # Sort by foreign_pct descending (en yüksek yabancı payı önce)
        data = [d for d in data if d.get("foreign_pct") is not None]
        data.sort(key=lambda x: x["foreign_pct"], reverse=True)

        result = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "items": clean_for_json(data),
            "source": source,
            "count": len(data),
        }
        TAKAS_CACHE[cache_key] = result
        return result
    except Exception as e:
        log.error(f"takas: {e}")
        raise HTTPException(status_code=500, detail="Takas verisi alinamadi")

# ================================================================
# SOSYAL MEDYA — X/Twitter sentiment via Grok + twscrape ready
# ================================================================
SOCIAL_CACHE = TTLCache(maxsize=10, ttl=1800)  # 30 min

@app.get("/api/social")
async def api_social():
    """X/Twitter BIST sentiment — uses Grok AI (has X data) or twscrape"""
    cache_key = "social_sentiment"
    if cache_key in SOCIAL_CACHE:
        return SOCIAL_CACHE[cache_key]

    # Method 1: Grok AI — ask about current BIST sentiment on X
    if AI_AVAILABLE and "grok" in AI_PROVIDERS:
        try:
            prompt = (
                "Sen bir BIST sosyal medya analistisin. X (Twitter) uzerindeki BIST hisse senedi tartismalarini analiz et.\n"
                "Su anda X'te en cok konusulan BIST hisseleri hangileri? Genel sentiment nedir?\n"
                "Asagidaki formatta JSON olarak cevap ver, SADECE JSON, baska birsey yazma:\n"
                '{"trending": [{"ticker": "THYAO", "sentiment": "bullish", "score": 78, "reason": "Yolcu rekoru haberleri"}, ...], '
                '"overall_sentiment": "cautious_bullish", '
                '"summary": "BIST\'te genel hava temkinli pozitif...", '
                '"hot_topics": ["faiz kararı", "enflasyon verisi", "yabancı girişi"]}\n'
                "En az 5 hisse, en fazla 10 hisse. score 0-100 arasi. sentiment: bullish/bearish/neutral.\n"
                "Gercekci ol, sallama."
            )
            text = await asyncio.to_thread(ai_call, prompt, 500)
            if text:
                # Try to parse JSON from response
                import json as _json
                # Clean potential markdown fences
                clean = text.strip()
                if clean.startswith("```"): clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"): clean = clean[:-3]
                clean = clean.strip()
                if clean.startswith("json"): clean = clean[4:].strip()
                try:
                    data = _json.loads(clean)
                    result = {
                        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "source": "grok_ai",
                        "trending": data.get("trending", []),
                        "overall_sentiment": data.get("overall_sentiment", "neutral"),
                        "summary": data.get("summary", ""),
                        "hot_topics": data.get("hot_topics", []),
                    }
                    SOCIAL_CACHE[cache_key] = result
                    return result
                except _json.JSONDecodeError:
                    # Grok didn't return valid JSON — return text as summary
                    result = {
                        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "source": "grok_ai",
                        "trending": [],
                        "overall_sentiment": "unknown",
                        "summary": text[:500],
                        "hot_topics": [],
                    }
                    SOCIAL_CACHE[cache_key] = result
                    return result
        except Exception as e:
            log.warning(f"social grok: {e}")

    # Method 2: twscrape (when credentials are set)
    # TODO: X_USERNAME and X_PASSWORD env vars ile twscrape entegrasyonu
    # twscrape kurulumu: pip install twscrape
    # await api.pool.add_account(username, password, email, email_password)

    return {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": None,
        "trending": [],
        "overall_sentiment": "unavailable",
        "summary": "Sosyal medya verisi icin XAI_API_KEY (Grok) ekleyin. Grok, X/Twitter verisine erisim saglar.",
        "hot_topics": [],
        "error": "XAI_API_KEY gerekli"
    }

# ================================================================
# GÜNÜN SÖZÜ — rotating finance wisdom
# ================================================================
FINANCE_QUOTES = [
    {"text": "Fiyat ne odediginizdir, deger ne aldiginizdir.", "author": "Warren Buffett"},
    {"text": "Piyasa kisa vadede oylama makinesi, uzun vadede tarti makinesidir.", "author": "Benjamin Graham"},
    {"text": "En iyi yatirim kendinize yapacaginiz yatirimdir.", "author": "Warren Buffett"},
    {"text": "Borsa sabirlidan sabirsiza para transferi yapar.", "author": "Warren Buffett"},
    {"text": "Risk, ne yaptiginizi bilmemekten kaynaklanir.", "author": "Warren Buffett"},
    {"text": "Harika sirketi makul fiyata almak, makul sirketi harika fiyata almaktan iyidir.", "author": "Warren Buffett"},
    {"text": "Herkes acgozlu iken korkun, herkes korkak iken acgozlu olun.", "author": "Warren Buffett"},
    {"text": "Basitlik, sofistikelikin nihai formudur.", "author": "Charlie Munger"},
    {"text": "Bildiginizi alin, aldiginizi bilin.", "author": "Peter Lynch"},
    {"text": "En iyi zaman agac dikmek icin 20 yil onceydi. Ikinci en iyi zaman bugun.", "author": "Cin Atasozu"},
    {"text": "Getiri pesinde kosmay in, riski yonetin. Getiri kendiligin den gelir.", "author": "Benjamin Graham"},
    {"text": "Piyasadaki en tehlikeli dort kelime: Bu sefer farkli olacak.", "author": "Sir John Templeton"},
    {"text": "Sabir, yatirimcinin en guclu silahidir.", "author": "Jesse Livermore"},
    {"text": "Trendin arkadasindir, ta ki donene kadar.", "author": "Ed Seykota"},
    {"text": "Bilesik faiz dunyanin sekizinci harikasidir.", "author": "Albert Einstein"},
    {"text": "Bir hisseyi 10 yil tutmayi dusunmuyorsaniz, 10 dakika bile tutmayin.", "author": "Warren Buffett"},
    {"text": "Kazananlari tut, kaybedenleri kes.", "author": "William O'Neil"},
    {"text": "Nakit pozisyon da bir pozisyondur.", "author": "Jesse Livermore"},
    {"text": "Enflasyon sessiz bir hirsizdir.", "author": "Milton Friedman"},
    {"text": "Iyi sirketler kotu zamanlarda buyur.", "author": "Shelby Davis"},
    {"text": "Yatirimda en onemli kalite mizactir, zeka degil.", "author": "Warren Buffett"},
    {"text": "Batmamak icin cesitlendir, zengin olmak icin yogunlas.", "author": "Andrew Carnegie"},
    {"text": "Piyasa size ders verecekse en pahalisi verir.", "author": "Wall Street Atasozu"},
    {"text": "Yalniz kalabal igin tersine gitmeye hazir olan buyuk kazanclar elde edebilir.", "author": "Sir John Templeton"},
]

@app.get("/api/quote")
async def api_quote():
    today = dt.datetime.now().timetuple().tm_yday
    idx = today % len(FINANCE_QUOTES)
    return FINANCE_QUOTES[idx]

# ================================================================
# GÜNÜN FİNANS KİTABI — rotating book recommendations
# ================================================================
FINANCE_BOOKS = [
    {"title": "Akilli Yatirimci", "author": "Benjamin Graham", "description": "Deger yatiriminin kutsal kitabi. Graham, hisse secimi ve risk yonetimini basit ama derin anlatir. Buffett'in 'hayatimi degistiren kitap' dedigi eser.", "level": "Baslangic-Orta"},
    {"title": "Borsada Teknik Analiz", "author": "John J. Murphy", "description": "Teknik analizin ansiklopedisi. Grafik okuma, trend analizi, gostergeler — hepsi tek kitapta. Terminal kullanan herkesin rafinda olmali.", "level": "Orta"},
    {"title": "Bir Adim Once", "author": "Peter Lynch", "description": "Efsanevi Magellan Fund yoneticisi, siradan yatirimcinin Wall Street'i nasil yenebilecegini anlatiyor. 'Bildiginizi alin' felsefesinin temeli.", "level": "Baslangic"},
    {"title": "Piyasa Buyuculeri", "author": "Jack D. Schwager", "description": "Dunyanin en basarili trader'lariyla roportajlar. Her birinin farkli stratejisi ama ortak noktasi: disiplin ve risk yonetimi.", "level": "Orta-Ileri"},
    {"title": "Zengin Baba Yoksul Baba", "author": "Robert Kiyosaki", "description": "Para, yatirim ve finansal ozgurluk hakkinda temel bakis acisi. Borsa oncesi herkesin okumasi gereken finansal okuryazarlik kitabi.", "level": "Baslangic"},
    {"title": "Kayip Trader'in Gunlugu", "author": "Jim Paul", "description": "75 milyon dolar kaybeden bir trader'in hikayesi. Kazanmaktan cok kaybetmeyi anlamak icin muhtesem ders kitabi.", "level": "Herkes"},
    {"title": "Borsanin Sinirlari", "author": "Nassim N. Taleb", "description": "Siyah Kugu teorisinin babasi, risk, belirsizlik ve piyasalardaki rastlantiyi gozler onune seriyor. Dusunce tarzinizi degistirir.", "level": "Ileri"},
    {"title": "Para Psikolojisi", "author": "Morgan Housel", "description": "Yatirim kararlari mantik degil psikoloji ile alinir. Neden bazi insanlar zenginlesir, bazilari zenginligi koruyamaz?", "level": "Baslangic"},
    {"title": "Hisselerde Uzun Vadeli Yatirim", "author": "Jeremy Siegel", "description": "200 yillik veriyle hisse senetlerinin neden uzun vadede en iyi yatirim araci oldugunu kanitliyor. Sabirin kitabi.", "level": "Orta"},
    {"title": "Babil'in En Zengin Adami", "author": "George S. Clason", "description": "5000 yillik para bilgeligi modern hikayelerle. 'Kazandiginizin onda birini biriktirin' kuraliyla basliyor. Kisa ve etkili.", "level": "Baslangic"},
    {"title": "Deger Yatiriminin Kucuk Kitabi", "author": "Christopher Browne", "description": "Graham-Buffett okulunun modern ozeti. Ucuz ve kaliteli hisse nasil bulunur, adim adim anlatiliyor.", "level": "Baslangic-Orta"},
    {"title": "Flash Boys", "author": "Michael Lewis", "description": "Yuksek frekanli trading dunyasinin icerisinden bir hikaye. Piyasalarin gercekte nasil calistigini gosteren nefes kesen anlati.", "level": "Herkes"},
    {"title": "Trader Vic", "author": "Victor Sperandeo", "description": "40 yillik tecrubesiyle Sperandeo, trend takibi ve risk yonetimini pratikte nasil uygulayacaginizi ogretiyor.", "level": "Orta-Ileri"},
    {"title": "Warren Buffett ve Finansal Tablolarin Yorumu", "author": "Mary Buffett", "description": "Buffett'in gelini, ustanin bilanco okuma yontemini herkesin anlayacagi dilde aktariyor. Temel analize giris icin ideal.", "level": "Baslangic"},
    {"title": "Kapital", "author": "Thomas Piketty", "description": "Servet esitsizligi ve kapitalizmin dinamikleri. Makro dusunmeyi ogreten, yatirim kararlarinda buyuk resmi gormeni saglayan eser.", "level": "Ileri"},
]

@app.get("/api/book")
async def api_book():
    today = dt.datetime.now().timetuple().tm_yday
    idx = today % len(FINANCE_BOOKS)
    return FINANCE_BOOKS[idx]

# ================================================================
# HEATMAP — Sektörel ısı haritası (günlük değişim + piyasa değeri)
# ================================================================
HEATMAP_CACHE = TTLCache(maxsize=5, ttl=900)  # 15 min

def _fetch_heatmap_data():
    """Batch download 1-day data for all universe stocks"""
    symbols = [normalize_symbol(t) for t in UNIVERSE]
    try:
        import yfinance as _yf
        df = _yf.download(symbols, period="2d", group_by="ticker", progress=False, threads=True)
        results = []
        for t in UNIVERSE:
            sym = normalize_symbol(t)
            try:
                if len(UNIVERSE) == 1:
                    ticker_df = df
                else:
                    ticker_df = df[sym] if sym in df.columns.get_level_values(0) else None
                if ticker_df is None or ticker_df.empty or len(ticker_df) < 2:
                    continue
                prev_close = float(ticker_df["Close"].iloc[-2])
                last_close = float(ticker_df["Close"].iloc[-1])
                if prev_close == 0: continue
                chg_pct = ((last_close - prev_close) / prev_close) * 100
                # Get cached scan data for sector/market_cap
                scan_item = None
                for item in TOP10_CACHE.get("items", []):
                    if item["ticker"] == t:
                        scan_item = item
                        break
                sector = scan_item.get("sector", "Diger") if scan_item else "Diger"
                mcap = scan_item["metrics"].get("market_cap") if scan_item else None
                score = scan_item["overall"] if scan_item else None
                results.append({
                    "ticker": t,
                    "price": round(last_close, 2),
                    "change_pct": round(chg_pct, 2),
                    "market_cap": mcap,
                    "sector": sector or "Diger",
                    "score": score,
                })
            except Exception:
                continue
        return results
    except Exception as e:
        log.warning(f"heatmap download: {e}")
        return []

@app.get("/api/heatmap")
async def api_heatmap():
    cache_key = "heatmap"
    if cache_key in HEATMAP_CACHE:
        return HEATMAP_CACHE[cache_key]
    data = await asyncio.to_thread(_fetch_heatmap_data)
    # Group by sector
    sectors = defaultdict(list)
    for d in data:
        sectors[d["sector"]].append(d)
    sector_list = []
    for sec, items in sectors.items():
        avg_chg = sum(i["change_pct"] for i in items) / len(items) if items else 0
        total_mcap = sum(i["market_cap"] or 0 for i in items)
        sector_list.append({
            "sector": sec, "avg_change": round(avg_chg, 2),
            "total_mcap": total_mcap, "count": len(items),
            "stocks": sorted(items, key=lambda x: abs(x["change_pct"]), reverse=True)
        })
    sector_list.sort(key=lambda x: x["avg_change"], reverse=True)
    result = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sectors": clean_for_json(sector_list),
        "total": len(data),
    }
    HEATMAP_CACHE[cache_key] = result
    return result

# ================================================================
# BORSADEDE AI AGENT — conversational BIST assistant
# ================================================================
AGENT_CACHE = TTLCache(maxsize=100, ttl=600)

@app.get("/api/agent")
async def api_agent(q: str = ""):
    if not q.strip():
        return {"answer": "Eyyay, hos geldin evladim! Bi cayini koy, dede anlatsin. Hisse analizi, piyasa, teknik sinyal — ne sorarsan soyliyim sayin yatirimci!"}
    if not AI_AVAILABLE:
        return {"answer": "AI motoru aktif degil. XAI_API_KEY veya OPENAI_KEY ekleyin.", "error": True}
    cache_key = q.strip().lower()[:100]
    if cache_key in AGENT_CACHE:
        return AGENT_CACHE[cache_key]
    try:
        context = ""
        items = TOP10_CACHE.get("items", [])
        if items:
            top5 = [f"{r['ticker']}:{r['overall']}/100({r['style']})" for r in items[:5]]
            context = f"Taranan en iyi 5 hisse: {', '.join(top5)}. Toplam {len(items)} hisse.\n"
        prompt = (
            "Sen EYYAY DEDE'sin (H. Sayilgan). 59 yasinda, tombul, beyaz sakalli, kalin hirkali, "
            "Anadolu kurnazi bir yatirim dedesisin.\n"
            "Konusma tarzin: Bilge, sicakkanli, esprili ama agir. "
            "'Evladim', 'sayin yatirimci', 'yavrum', 'eyyay bak simdi', "
            "'bi cayini koy dede anlatsin', 'eyyay bu hisse yavru gibi buyur', "
            "'dikkat et burada evladim' gibi cumleler kullan.\n"
            "ASLA 'kanka' deme. Sen bilge bir dedesin, 'evladim' veya 'sayin yatirimci' dersin.\n"
            "'Eyyay' senin selamlaman ve onaylaman — bazen cumle basinda kullan.\n"
            "Bazen basit ama dahice benzetmeler yap: 'yazin ucaklar cok ucar' gibi.\n"
            "Cevaplarin KISA olsun (4-5 cumle MAX). Laf kalabaligi yapma.\n"
            "Asla direkt 'al' veya 'sat' deme. Her zaman 'bu dedenin gorusu, sen de arastir evladim' diye bitir.\n"
            "Bilgi seviyen cok yuksek: Temel analiz, teknik sinyaller, takas, makro hepsini bilirsin. "
            "Ama basit ve anlasilir anlatirsin.\n\n"
            f"{context}"
            f"Kullanicinin sorusu: {q}\n\nEyyay Dede:"
        )
        text = await asyncio.to_thread(ai_call, prompt, 300)
        result = {"answer": text or "Cevap olusturulamadi.", "cached": False}
        AGENT_CACHE[cache_key] = result
        return result
    except Exception as e:
        log.warning(f"agent: {e}")
        return {"answer": f"Hata: {str(e)}", "error": True}

# ================================================================
# HERO SUMMARY — AI-powered market intelligence for dashboard hero
# ================================================================
HERO_CACHE = TTLCache(maxsize=5, ttl=1800)  # 30 min cache

@app.get("/api/hero-summary")
async def api_hero_summary():
    """Market mode, main story, opportunity, risk, bot commentary, watch list"""
    cache_key = "hero"
    if cache_key in HERO_CACHE:
        return HERO_CACHE[cache_key]

    items = TOP10_CACHE.get("items", [])
    macro_data = MACRO_CACHE.get("macro_all", {})
    cross_data = cross_hunter.last_results or []

    # Basic stats without AI
    bullish_count = sum(1 for r in items if r["overall"] >= 65)
    bearish_count = sum(1 for r in items if r["overall"] < 40)
    total = len(items)

    # Market mode from data
    if bullish_count > total * 0.6: mode = "POZITIF"
    elif bearish_count > total * 0.4: mode = "RISKLI"
    elif bullish_count > bearish_count: mode = "TEMKINLI_POZITIF"
    else: mode = "NOTR"

    mode_color = {"POZITIF": "green", "TEMKINLI_POZITIF": "green", "NOTR": "yellow", "RISKLI": "red"}.get(mode, "yellow")
    mode_label = {"POZITIF": "Pozitif", "TEMKINLI_POZITIF": "Temkinli Pozitif", "NOTR": "Notr", "RISKLI": "Riskli"}.get(mode, "Notr")

    # Top opportunity & risk from scan
    opp = None
    risk_item = None
    if items:
        best = max(items, key=lambda x: x["scores"].get("value", 0) + x["scores"].get("growth", 0))
        opp = {"ticker": best["ticker"], "name": best["name"], "overall": best["overall"], "reason": best["positives"][0] if best["positives"] else ""}
        worst = min(items, key=lambda x: x["overall"])
        risk_item = {"ticker": worst["ticker"], "name": worst["name"], "overall": worst["overall"], "reason": worst["negatives"][0] if worst["negatives"] else ""}

    # Sector strength from scan
    sec_map = defaultdict(lambda: {"total": 0, "count": 0})
    for r in items:
        s = r.get("sector") or "Diger"
        sec_map[s]["total"] += r["overall"]
        sec_map[s]["count"] += 1
    strong_sectors = sorted([(k, v["total"]/v["count"]) for k, v in sec_map.items() if v["count"] >= 2], key=lambda x: -x[1])[:3]
    weak_sectors = sorted([(k, v["total"]/v["count"]) for k, v in sec_map.items() if v["count"] >= 2], key=lambda x: x[1])[:2]

    # Watch list
    watch = []
    if strong_sectors: watch.append(f"{strong_sectors[0][0]} sektoru guclu")
    macro_items = macro_data.get("items", [])
    for mi in macro_items:
        if mi.get("key") == "VIX" and mi.get("change_pct", 0) > 3: watch.append("VIX yukseliyor — dikkat")
        if mi.get("key") == "DXY" and mi.get("change_pct", 0) > 0.5: watch.append("DXY yukseliste — TL baskisi")
    if cross_data: watch.append(f"{len(cross_data)} teknik sinyal aktif")
    if not watch: watch = ["Piyasa sakin, secici izle"]

    # AI-powered story + commentary
    story = None
    bot_says = None
    if AI_AVAILABLE and items:
        try:
            top3 = [f"{r['ticker']}({r['overall']})" for r in items[:3]]
            bot3 = [f"{r['ticker']}({r['overall']})" for r in items[-3:]]
            macro_str = ", ".join([f"{m['name']}:{m.get('change_pct',0):+.1f}%" for m in macro_items[:6]])
            prompt = (
                "Sen BistBull Terminal'in piyasa editoru ve stratejistisin. Turkce yaz.\n"
                f"Piyasa modu: {mode_label}. {total} hisse tarandi, {bullish_count} pozitif, {bearish_count} zayif.\n"
                f"En iyiler: {', '.join(top3)}. En zayiflar: {', '.join(bot3)}.\n"
                f"Makro: {macro_str}\n"
                f"Guclu sektorler: {', '.join([s[0] for s in strong_sectors])}\n"
                f"Cross sinyal: {len(cross_data)} adet\n\n"
                "Asagidaki 3 seyi yaz, HER BIRINI AYRI SATIRDA, baska hic birsey yazma:\n"
                "HIKAYE: 2 cumle ile bugunun ana piyasa hikayesi\n"
                "YORUM: 2 cumle ile bot yorumu ve strateji onerisi\n"
                "FIRSAT: 1 cumle ile en buyuk firsat"
            )
            text = await asyncio.to_thread(ai_call, prompt, 300)
            if text:
                for line in text.split("\n"):
                    line = line.strip()
                    if line.upper().startswith("HIKAYE:"): story = line[7:].strip()
                    elif line.upper().startswith("YORUM:"): bot_says = line[6:].strip()
                    elif line.upper().startswith("FIRSAT:") and opp: opp["ai_reason"] = line[7:].strip()
                if not story: story = text[:200]
                if not bot_says: bot_says = text[200:400] if len(text) > 200 else None
        except Exception as e:
            log.warning(f"hero AI: {e}")

    result = {
        "mode": mode, "mode_label": mode_label, "mode_color": mode_color,
        "story": story or f"{total} hisse tarandi. {bullish_count} hisse pozitif bolgede, {bearish_count} hisse zayif.",
        "opportunity": clean_for_json(opp),
        "risk": clean_for_json(risk_item),
        "bot_says": bot_says or f"Piyasa {mode_label.lower()} modda. {'Secici al stratejisi uygun.' if mode != 'RISKLI' else 'Defansif pozisyon onerilir.'}",
        "watch": watch[:4],
        "strong_sectors": [{"name": s[0], "score": round(s[1], 1)} for s in strong_sectors],
        "weak_sectors": [{"name": s[0], "score": round(s[1], 1)} for s in weak_sectors],
        "stats": {"total": total, "bullish": bullish_count, "bearish": bearish_count, "signals": len(cross_data)},
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    HERO_CACHE[cache_key] = result
    return result

# ================================================================
# LIVE STATS — system activity counters
# ================================================================
SYSTEM_STATS = {"news_processed": 0, "scans_done": 0, "signals_total": 0, "start_time": dt.datetime.now(dt.timezone.utc).isoformat()}

@app.get("/api/live/stats")
async def api_live_stats():
    uptime_seconds = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(SYSTEM_STATS["start_time"])).total_seconds()
    return {
        "scans_done": len(ANALYSIS_CACHE),
        "signals_total": len(cross_hunter.last_results),
        "macro_tracked": len(MACRO_CACHE.get("macro_all", {}).get("items", [])),
        "cache_raw": len(RAW_CACHE),
        "cache_tech": len(TECH_CACHE),
        "uptime_hours": round(uptime_seconds / 3600, 1),
        "last_scan": TOP10_CACHE["asof"].isoformat() if TOP10_CACHE.get("asof") else None,
        "universe": len(UNIVERSE),
    }

# ================================================================
# BRIEFING HISTORY — store and retrieve past briefings
# ================================================================
BRIEFING_HISTORY = []  # in-memory list, max 10

@app.get("/api/briefings/history")
async def api_briefings_history():
    return {"briefings": BRIEFING_HISTORY[-10:]}

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

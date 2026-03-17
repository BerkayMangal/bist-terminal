# ================================================================
# BIST TERMINAL — Analysis Engine
# Extracted from FA Bot V5.1, zero Telegram dependencies
# ================================================================

import math, logging, datetime as dt
import numpy as np
import pandas as pd
import yfinance as yf
from cachetools import TTLCache

log = logging.getLogger("engine")

# ================================================================
# UNIVERSE
# ================================================================
UNIVERSE = [
    "ASELS","THYAO","BIMAS","KCHOL","SISE","EREGL","TUPRS","AKBNK","ISCTR","YKBNK",
    "GARAN","SAHOL","MGROS","FROTO","TOASO","TCELL","KRDMD","PETKM","ENKAI","TAVHL",
    "PGSUS","EKGYO","KOZAL","TTKOM","ARCLK","VESTL","DOHOL","AYGAZ","LOGO","SOKM",
    "TKFEN","KONTR","ODAS","GUBRF","SASA","ISMEN","OYAKC","CIMSA","MPARK","AKSEN",
]

# ================================================================
# CACHES
# ================================================================
RAW_CACHE = TTLCache(maxsize=500, ttl=86400)
ANALYSIS_CACHE = TTLCache(maxsize=500, ttl=86400)
TECH_CACHE = TTLCache(maxsize=500, ttl=3600)
QUANTUM_CACHE = TTLCache(maxsize=500, ttl=3600)

# ================================================================
# HELPERS
# ================================================================
def normalize(ticker):
    t = (ticker or "").strip().upper().replace(" ", "")
    if t.endswith(".IS"): return t
    if "." in t: return t
    return f"{t}.IS"

def base(text):
    return (text or "").strip().upper().replace(".IS", "")

def safe(x):
    try:
        if x is None: return None
        x = float(x)
        if math.isnan(x) or math.isinf(x): return None
        return x
    except Exception: return None

def fmt(x, digits=2):
    x = safe(x)
    if x is None: return "N/A"
    if abs(x) >= 1e9: return f"{x/1e9:.2f}B"
    if abs(x) >= 1e6: return f"{x/1e6:.2f}M"
    if abs(x) >= 1e3: return f"{x:,.0f}"
    return f"{x:.{digits}f}"

def pct(x, digits=1):
    x = safe(x)
    if x is None: return "N/A"
    return f"{x*100:.{digits}f}%"

def pick(df, names):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None, None
    for name in names:
        if name in df.index:
            try:
                s = df.loc[name]
                if isinstance(s, pd.DataFrame): s = s.iloc[:, 0]
                s = pd.to_numeric(s, errors="coerce").dropna()
                if s.empty: continue
                cur = safe(s.iloc[0])
                prev = safe(s.iloc[1]) if len(s) > 1 else None
                return cur, prev
            except Exception: continue
    return None, None

def growth(cur, prev):
    cur, prev = safe(cur), safe(prev)
    if cur is None or prev in (None, 0): return None
    return (cur - prev) / abs(prev)

def avg(values):
    vals = [safe(v) for v in values if safe(v) is not None]
    if not vals: return None
    return float(sum(vals) / len(vals))

def sh(x, bad, ok, good, great):
    x = safe(x)
    if x is None: return None
    if x <= bad: return 5.0
    if x >= great: return 100.0
    if x <= ok: return 5 + (x - bad) * (35 / max(ok - bad, 1e-9))
    if x <= good: return 40 + (x - ok) * (35 / max(good - ok, 1e-9))
    return 75 + (x - good) * (25 / max(great - good, 1e-9))

def sl(x, great, good, ok, bad):
    x = safe(x)
    if x is None: return None
    if x <= great: return 100.0
    if x >= bad: return 5.0
    if x <= good: return 100 - (x - great) * (25 / max(good - great, 1e-9))
    if x <= ok: return 75 - (x - good) * (35 / max(ok - good, 1e-9))
    return 40 - (x - ok) * (35 / max(bad - ok, 1e-9))

# ================================================================
# RAW FETCH
# ================================================================
def fetch_raw(symbol):
    if symbol in RAW_CACHE: return RAW_CACHE[symbol]
    tk = yf.Ticker(symbol)
    info = tk.get_info() or {}
    try: fast = getattr(tk, "fast_info", {}) or {}
    except Exception: fast = {}
    try: fin = tk.financials
    except Exception: fin = None
    try: bal = tk.balance_sheet
    except Exception: bal = None
    try: cf = tk.cashflow
    except Exception: cf = None
    raw = {"info": info, "fast": fast, "financials": fin, "balance": bal, "cashflow": cf}
    RAW_CACHE[symbol] = raw
    return raw

# ================================================================
# LEGENDARY METRICS
# ================================================================
def piotroski(m):
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

def altman(m):
    wc, ta = safe(m.get("working_capital")), safe(m.get("total_assets"))
    re_ = safe(m.get("retained_earnings")) or 0.0
    ebit, tl = safe(m.get("ebit")), safe(m.get("total_liabilities"))
    sales, mve = safe(m.get("revenue")), safe(m.get("market_cap"))
    if None in (wc, ta, ebit, tl, sales, mve) or ta == 0 or tl == 0: return None
    return 1.2*(wc/ta) + 1.4*(re_/ta) + 3.3*(ebit/ta) + 0.6*(mve/tl) + 1.0*(sales/ta)

# ================================================================
# COMPUTE METRICS
# ================================================================
def compute_metrics(symbol):
    raw = fetch_raw(symbol)
    info, fast = raw["info"], raw["fast"]
    fin, bal, cf = raw["financials"], raw["balance"], raw["cashflow"]

    revenue, revenue_prev = pick(fin, ["Total Revenue", "Operating Revenue"])
    gross_profit, gp_prev = pick(fin, ["Gross Profit"])
    operating_income, _ = pick(fin, ["Operating Income", "EBIT"])
    ebit, _ = pick(fin, ["EBIT", "Operating Income"])
    ebitda, ebitda_prev = pick(fin, ["EBITDA"])
    net_income, ni_prev = pick(fin, ["Net Income", "Net Income Common Stockholders"])
    interest_exp, _ = pick(fin, ["Interest Expense", "Interest Expense Non Operating"])
    dil_shares, dil_shares_prev = pick(fin, ["Diluted Average Shares", "Basic Average Shares"])
    eps_row, eps_prev = pick(fin, ["Diluted EPS", "Basic EPS"])

    op_cf, _ = pick(cf, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
    capex, _ = pick(cf, ["Capital Expenditure"])

    total_assets, ta_prev = pick(bal, ["Total Assets"])
    total_liab, _ = pick(bal, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
    total_debt, td_prev = pick(bal, ["Total Debt"])
    cash, _ = pick(bal, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"])
    cur_assets, ca_prev = pick(bal, ["Current Assets", "Total Current Assets"])
    cur_liab, cl_prev = pick(bal, ["Current Liabilities", "Total Current Liabilities"])
    ret_earn, _ = pick(bal, ["Retained Earnings"])
    equity, _ = pick(bal, ["Stockholders Equity", "Total Stockholder Equity"])

    price = safe(fast.get("last_price")) or safe(info.get("currentPrice"))
    market_cap = safe(fast.get("market_cap")) or safe(info.get("marketCap"))
    pe = safe(info.get("trailingPE")) or safe(info.get("forwardPE"))
    pb = safe(info.get("priceToBook"))
    ev_ebitda = safe(info.get("enterpriseToEbitda"))
    div_yield = safe(info.get("dividendYield"))
    trailing_eps = safe(info.get("trailingEps")) or safe(eps_row)
    book_val_ps = safe(info.get("bookValue")) or ((equity/dil_shares) if equity and dil_shares else None)

    roe = safe(info.get("returnOnEquity")) or ((net_income/equity) if net_income and equity else None)
    roa = safe(info.get("returnOnAssets")) or ((net_income/total_assets) if net_income and total_assets else None)
    roa_prev = (ni_prev/ta_prev) if ni_prev and ta_prev else None
    gross_margin = (gross_profit/revenue) if gross_profit and revenue else None
    gm_prev = (gp_prev/revenue_prev) if gp_prev and revenue_prev else None
    op_margin = safe(info.get("operatingMargins")) or ((operating_income/revenue) if operating_income and revenue else None)
    net_margin = safe(info.get("profitMargins")) or ((net_income/revenue) if net_income and revenue else None)
    cur_ratio = safe(info.get("currentRatio")) or ((cur_assets/cur_liab) if cur_assets and cur_liab else None)
    cr_prev = (ca_prev/cl_prev) if ca_prev and cl_prev else None
    debt_eq = safe(info.get("debtToEquity")) or ((total_debt/equity*100) if total_debt and equity else None)

    net_debt = (total_debt - cash) if total_debt is not None and cash is not None else None
    nd_ebitda = (net_debt/ebitda) if net_debt is not None and ebitda not in (None, 0) else None
    _ev = ebit if ebit is not None else operating_income
    int_cov = (_ev/abs(interest_exp)) if _ev is not None and interest_exp not in (None, 0) else None

    free_cf = ((op_cf + capex) if op_cf is not None and capex is not None else None) or safe(info.get("freeCashflow"))
    fcf_yield = (free_cf/market_cap) if free_cf is not None and market_cap not in (None, 0) else None
    fcf_margin = (free_cf/revenue) if free_cf is not None and revenue not in (None, 0) else None
    cfo_to_ni = (op_cf/net_income) if op_cf is not None and net_income not in (None, 0) else None

    rev_growth = safe(info.get("revenueGrowth")) or growth(revenue, revenue_prev)
    eps_growth = safe(info.get("earningsGrowth")) or growth(eps_row, eps_prev) or growth(net_income, ni_prev)
    ebit_growth = growth(ebitda, ebitda_prev)

    wc = (cur_assets - cur_liab) if cur_assets is not None and cur_liab is not None else None
    tax_rate = safe(info.get("effectiveTaxRate")) or 0.20
    inv_cap = (total_debt + equity - cash) if total_debt is not None and equity is not None and cash is not None else None
    nopat_base = ebit if ebit is not None else operating_income
    nopat = (nopat_base * (1 - min(max(tax_rate, 0), 0.35))) if nopat_base is not None else None
    roic = (nopat/inv_cap) if nopat is not None and inv_cap not in (None, 0) else None

    peg = (pe/max(eps_growth*100, 1e-9)) if pe not in (None, 0) and eps_growth is not None and eps_growth > 0 else None
    graham_fv = ((22.5*trailing_eps*book_val_ps)**0.5) if trailing_eps not in (None, 0) and book_val_ps not in (None, 0) and trailing_eps > 0 and book_val_ps > 0 else None
    mos = ((graham_fv - price)/graham_fv) if graham_fv not in (None, 0) and price is not None else None
    share_ch = growth(dil_shares, dil_shares_prev)
    asset_to = (revenue/total_assets) if revenue is not None and total_assets not in (None, 0) else None
    at_prev = (revenue_prev/ta_prev) if revenue_prev is not None and ta_prev not in (None, 0) else None

    m = {
        "symbol": symbol, "ticker": base(symbol),
        "name": str(info.get("shortName") or info.get("longName") or symbol),
        "currency": str(info.get("currency") or ""),
        "sector": str(info.get("sector") or ""),
        "industry": str(info.get("industry") or ""),
        "price": price, "market_cap": market_cap,
        "pe": pe, "pb": pb, "ev_ebitda": ev_ebitda, "dividend_yield": div_yield,
        "revenue": revenue, "revenue_prev": revenue_prev,
        "gross_profit": gross_profit, "gross_profit_prev": gp_prev,
        "operating_income": operating_income, "ebit": ebit or operating_income,
        "ebitda": ebitda, "ebitda_prev": ebitda_prev,
        "net_income": net_income, "net_income_prev": ni_prev,
        "operating_cf": op_cf, "free_cf": free_cf,
        "total_assets": total_assets, "total_assets_prev": ta_prev,
        "total_liabilities": total_liab, "total_debt": total_debt, "total_debt_prev": td_prev,
        "cash": cash, "current_assets": cur_assets, "current_assets_prev": ca_prev,
        "current_liabilities": cur_liab, "working_capital": wc,
        "retained_earnings": ret_earn, "equity": equity,
        "trailing_eps": trailing_eps, "book_value_ps": book_val_ps,
        "roe": roe, "roa": roa, "roa_prev": roa_prev, "roic": roic,
        "gross_margin": gross_margin, "gross_margin_prev": gm_prev,
        "operating_margin": op_margin, "net_margin": net_margin,
        "current_ratio": cur_ratio, "current_ratio_prev": cr_prev,
        "debt_equity": debt_eq, "net_debt_ebitda": nd_ebitda,
        "interest_coverage": int_cov,
        "fcf_yield": fcf_yield, "fcf_margin": fcf_margin, "cfo_to_ni": cfo_to_ni,
        "revenue_growth": rev_growth, "eps_growth": eps_growth, "ebitda_growth": ebit_growth,
        "peg": peg, "graham_fv": graham_fv, "margin_safety": mos,
        "share_change": share_ch, "asset_turnover": asset_to, "asset_turnover_prev": at_prev,
    }
    m["piotroski_f"] = piotroski(m)
    m["altman_z"] = altman(m)
    return m

# ================================================================
# SCORING — BIST Calibrated
# ================================================================
def score_value(m):
    ev_sales = None
    if m.get("market_cap") and m.get("total_debt") and m.get("cash") and m.get("revenue"):
        ev = m["market_cap"] + (m["total_debt"] or 0) - (m["cash"] or 0)
        if m["revenue"] > 0: ev_sales = ev / m["revenue"]
    return avg([
        sl(m.get("pe"), 6, 10, 16, 25) if (m.get("pe") or 0) > 0 else None,
        sl(m.get("pb"), 0.8, 1.5, 2.5, 4.5) if (m.get("pb") or 0) > 0 else None,
        sl(m.get("ev_ebitda"), 4, 7, 11, 16) if (m.get("ev_ebitda") or 0) > 0 else None,
        sl(ev_sales, 0.5, 1.2, 2.5, 5.0) if ev_sales and ev_sales > 0 else None,
        sh(m.get("fcf_yield"), 0, 0.02, 0.05, 0.08),
        sh(m.get("margin_safety"), -0.2, 0, 0.15, 0.30),
    ])

def score_quality(m):
    return avg([
        sh(m.get("roe"), 0.01, 0.06, 0.12, 0.20),
        sh(m.get("roic"), 0.01, 0.06, 0.10, 0.16),
        sh(m.get("gross_margin"), 0.08, 0.15, 0.25, 0.40),
        sh(m.get("operating_margin"), 0.02, 0.06, 0.12, 0.20),
        sh(m.get("net_margin"), 0.005, 0.03, 0.08, 0.15),
    ])

def score_growth(m):
    return avg([
        sh(m.get("revenue_growth"), -0.05, 0.05, 0.15, 0.30),
        sh(m.get("eps_growth"), -0.10, 0.05, 0.15, 0.30),
        sh(m.get("ebitda_growth"), -0.05, 0.05, 0.12, 0.25),
        sl(m.get("peg"), 0.5, 1.0, 1.8, 3.0) if (m.get("peg") or 0) > 0 else None,
    ])

def score_balance(m):
    nde = m.get("net_debt_ebitda")
    nde_s = 100.0 if nde is not None and nde < 0 else sl(nde, 0.5, 1.5, 2.5, 4.0)
    return avg([nde_s,
        sl(m.get("debt_equity"), 30, 80, 150, 300),
        sh(m.get("current_ratio"), 0.8, 1.1, 1.5, 2.2),
        sh(m.get("interest_coverage"), 1.5, 3.0, 6.0, 12.0),
        sh(m.get("altman_z"), 1.2, 1.8, 3.0, 4.5),
    ])

def score_earnings(m):
    return avg([
        sh(m.get("cfo_to_ni"), 0.2, 0.6, 0.9, 1.2),
        sh(m.get("fcf_margin"), -0.02, 0, 0.05, 0.12),
    ])

def score_moat(m):
    stab = None
    if m.get("gross_margin") is not None and m.get("gross_margin_prev") is not None:
        stab = sl(abs(m["gross_margin"] - m["gross_margin_prev"]), 0, 0.02, 0.06, 0.12)
    pricing = sh(m.get("gross_margin"), 0.12, 0.22, 0.35, 0.50) if m.get("gross_margin") else None
    at_trend = None
    if m.get("asset_turnover") is not None and m.get("asset_turnover_prev") is not None:
        at_trend = 75 if m["asset_turnover"] >= m["asset_turnover_prev"] else 35
    return avg([stab, pricing, at_trend])

def style_label(scores):
    v, q, g, moat, bal = scores["value"], scores["quality"], scores["growth"], scores["moat"], scores["balance"]
    if q >= 75 and g >= 60 and v >= 40 and moat >= 60: return "Quality Compounder"
    if q >= 72 and moat >= 65 and v < 40: return "Premium Compounder"
    if v >= 75 and bal >= 55: return "Deep Value"
    if g >= 70 and v >= 45: return "GARP"
    if g >= 65 and q >= 55 and v < 45: return "Growth"
    if v >= 70 and q < 45: return "Value Trap Risk"
    if bal < 40 and g >= 50: return "High-Risk Turnaround"
    return "Balanced"

# ================================================================
# FULL ANALYSIS
# ================================================================
def analyze(ticker):
    symbol = normalize(ticker)
    if symbol in ANALYSIS_CACHE: return ANALYSIS_CACHE[symbol]
    m = compute_metrics(symbol)
    scores = {k: round((f(m) or 50), 1) for k, f in [
        ("value", score_value), ("quality", score_quality), ("growth", score_growth),
        ("balance", score_balance), ("earnings", score_earnings), ("moat", score_moat),
    ]}
    overall = (0.22*scores["value"] + 0.25*scores["quality"] + 0.15*scores["growth"]
              + 0.20*scores["balance"] + 0.10*scores["earnings"] + 0.08*scores["moat"])
    if m.get("equity") is not None and m["equity"] < 0: overall -= 12
    if m.get("net_income") is not None and m["net_income"] < 0: overall -= 8
    if m.get("operating_cf") is not None and m["operating_cf"] < 0: overall -= 8
    if m.get("total_debt") is not None and m.get("cash") is not None:
        if m["cash"] > (m["total_debt"] or 0): overall += 3
    overall = round(max(1, min(99, overall)), 1)
    is_bank = "bank" in (m.get("industry") or "").lower()
    r = {
        "ticker": base(symbol), "name": m["name"], "sector": m.get("sector",""),
        "industry": m.get("industry",""), "currency": m["currency"],
        "price": m["price"], "market_cap": m["market_cap"],
        "scores": scores, "overall": overall,
        "style": style_label(scores),
        "metrics": m, "is_bank": is_bank,
        "piotroski": m.get("piotroski_f"), "altman": m.get("altman_z") if not is_bank else None,
    }
    ANALYSIS_CACHE[symbol] = r
    return r

# ================================================================
# TECHNICAL ANALYSIS
# ================================================================
def compute_technical(ticker):
    symbol = normalize(ticker)
    if symbol in TECH_CACHE: return TECH_CACHE[symbol]
    try:
        tk = yf.Ticker(symbol)
        df = tk.history(period="1y", interval="1d")
        if df is None or len(df) < 50: return None
        c, v = df["Close"], df["Volume"]

        ma50 = c.rolling(50).mean()
        ma200 = c.rolling(200).mean() if len(c) >= 200 else pd.Series([np.nan]*len(c))
        price = float(c.iloc[-1])
        ma50_v = float(ma50.iloc[-1]) if not np.isnan(ma50.iloc[-1]) else None
        ma200_v = float(ma200.iloc[-1]) if len(c) >= 200 and not np.isnan(ma200.iloc[-1]) else None

        # RSI
        delta = c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None

        # RSI velocity + acceleration
        rsi_vel = float(rsi.iloc[-1] - rsi.iloc[-4]) if len(rsi) > 4 else 0
        rsi_acc = float((rsi.iloc[-1] - rsi.iloc[-4]) - (rsi.iloc[-4] - rsi.iloc[-7])) if len(rsi) > 7 else 0

        # MACD
        ema12 = c.ewm(span=12).mean()
        ema26 = c.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_hist = float(macd_line.iloc[-1] - signal_line.iloc[-1])
        macd_bullish = macd_hist > 0
        hist_vel = float(macd_hist - (macd_line.iloc[-4] - signal_line.iloc[-4])) if len(macd_line) > 4 else 0

        # Flow (buy/sell pressure)
        rng = df["High"] - df["Low"]
        buy_p = (v * ((c - df["Low"]) / rng.replace(0, np.nan))).fillna(v * 0.5)
        sell_p = v - buy_p
        flow_delta = buy_p - sell_p
        flow_ema = flow_delta.ewm(span=14).mean()
        flow_max = flow_ema.abs().rolling(100).max().iloc[-1]
        flow_score = float(flow_ema.iloc[-1] / flow_max * 100) if flow_max > 0 else 0

        # Bollinger
        bb_mid = c.rolling(20).mean()
        bb_std = c.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_width = ((bb_upper - bb_lower) / bb_mid * 100)
        is_squeeze = float(bb_width.iloc[-1]) < float(bb_width.quantile(0.2)) if len(bb_width) > 20 else False

        # 52W
        high_52w = float(df["High"].tail(252).max())
        low_52w = float(df["Low"].tail(252).min())
        pct_high = (price - high_52w) / high_52w * 100
        pct_low = (price - low_52w) / low_52w * 100

        # Volume
        vol_avg = float(v.tail(20).mean())
        vol_ratio = float(v.iloc[-1] / vol_avg) if vol_avg > 0 else 1

        # Cross detection (5 day)
        cross = None
        ma_state = None
        if ma50_v and ma200_v:
            ma_state = "BULLISH" if ma50_v > ma200_v else "BEARISH"
            if len(ma50) >= 6 and len(ma200) >= 6:
                for lb in range(1, 6):
                    p50 = float(ma50.iloc[-(lb+1)]) if not np.isnan(ma50.iloc[-(lb+1)]) else None
                    p200 = float(ma200.iloc[-(lb+1)]) if not np.isnan(ma200.iloc[-(lb+1)]) else None
                    c50 = float(ma50.iloc[-lb]) if not np.isnan(ma50.iloc[-lb]) else None
                    c200 = float(ma200.iloc[-lb]) if not np.isnan(ma200.iloc[-lb]) else None
                    if p50 and p200 and c50 and c200:
                        if p50 <= p200 and c50 > c200 and cross is None: cross = "GOLDEN"
                        elif p50 >= p200 and c50 < c200 and cross is None: cross = "DEATH"

        # Momentum score
        mom = 50
        if rsi_val:
            if rsi_val > 50 and rsi_vel > 0: mom += 10
            if rsi_val < 50 and rsi_vel < 0: mom -= 10
        if macd_bullish: mom += 10
        else: mom -= 10
        if flow_score > 20: mom += 10
        elif flow_score < -20: mom -= 10
        if ma50_v and price > ma50_v: mom += 5
        if ma200_v and price > ma200_v: mom += 5
        mom = max(0, min(100, mom))

        # Breakout score
        breakout = 0
        if is_squeeze: breakout += 30
        if vol_ratio > 2: breakout += 30
        if ma50_v and price > ma50_v and ma200_v and ma50_v > ma200_v: breakout += 20
        breakout = min(100, breakout)

        result = {
            "price": price, "ma50": ma50_v, "ma200": ma200_v, "ma_state": ma_state,
            "rsi": rsi_val, "rsi_velocity": rsi_vel,
            "macd_hist": macd_hist, "macd_bullish": macd_bullish, "hist_velocity": hist_vel,
            "flow_score": round(flow_score, 1),
            "is_squeeze": is_squeeze, "cross": cross,
            "high_52w": high_52w, "low_52w": low_52w,
            "pct_from_high": round(pct_high, 1), "pct_from_low": round(pct_low, 1),
            "vol_ratio": round(vol_ratio, 1),
            "momentum_score": mom, "breakout_score": breakout,
            "df": df,
        }
        TECH_CACHE[symbol] = result
        return result
    except Exception as e:
        log.warning(f"Tech {symbol}: {e}")
        return None

# ================================================================
# QUANTUM SCORE
# ================================================================
def quantum_score(ticker):
    """Combined fundamental + technical score"""
    if ticker in QUANTUM_CACHE: return QUANTUM_CACHE[ticker]
    fa = analyze(ticker)
    tech = compute_technical(ticker)
    if not fa: return None

    fundamental = fa["overall"]
    if tech:
        momentum = tech["momentum_score"]
        flow = max(0, min(100, 50 + tech["flow_score"]))
        breakout = tech["breakout_score"]
        regime = 70 if tech.get("ma_state") == "BULLISH" else 30
        quantum = (fundamental * 0.50 + momentum * 0.20 + flow * 0.15 + regime * 0.10 + breakout * 0.05)
    else:
        quantum = fundamental
        momentum = flow = breakout = regime = None

    signals = []
    if tech:
        if tech.get("cross") == "GOLDEN": signals.append("Golden Cross")
        if tech.get("cross") == "DEATH": signals.append("Death Cross")
        if tech.get("rsi") and tech["rsi"] > 70: signals.append("RSI Aşırı Alım")
        if tech.get("rsi") and tech["rsi"] < 30: signals.append("RSI Aşırı Satım")
        if tech.get("is_squeeze"): signals.append("BB Squeeze")
        if tech.get("vol_ratio", 0) > 2: signals.append("Yüksek Hacim")
        if tech.get("pct_from_high", -99) >= -3: signals.append("52W Zirve")
        if tech.get("pct_from_low", 99) <= 5: signals.append("52W Dip")

    r = {
        "ticker": fa["ticker"], "name": fa["name"], "sector": fa["sector"],
        "price": fa["price"], "style": fa["style"],
        "quantum": round(quantum, 1),
        "fundamental": fundamental,
        "momentum": momentum, "flow": flow, "breakout": breakout,
        "regime": regime,
        "scores": fa["scores"],
        "signals": signals,
        "tech": {k: v for k, v in (tech or {}).items() if k != "df"} if tech else None,
        "metrics": fa["metrics"],
        "piotroski": fa["piotroski"], "altman": fa["altman"],
    }
    QUANTUM_CACHE[ticker] = r
    return r

# ================================================================
# TOP 10 SCAN
# ================================================================
def scan_top10():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(quantum_score, t): t for t in UNIVERSE}
        for f in as_completed(futures):
            try:
                r = f.result()
                if r: results.append(r)
            except Exception: pass
    results.sort(key=lambda x: x["quantum"], reverse=True)
    return results[:10]

# ================================================================
# BISTBULL V9 — GERÇEK VERİ KATMANI
# yfinance → borsapy (İş Yatırım / KAP gerçek bilanço)
# Drop-in replacement: aynı metric dict yapısı, scoring engine değişmiyor
# ================================================================
#
# KURULUM: pip install borsapy
#
# DEĞİŞEN FONKSİYONLAR:
#   fetch_raw()           → fetch_raw_v9()
#   compute_metrics()     → compute_metrics_v9()  (aynı dict keys)
#   batch_download_history() → batch_download_history_v9()
#
# DEĞİŞMEYEN:
#   compute_piotroski, compute_altman, compute_beneish — aynen kalıyor
#   score_value, score_quality, ... — aynen kalıyor
#   analyze_symbol — sadece compute_metrics çağrısını v9'a çevir
#   compute_technical — history verisi borsapy'den gelecek
#   cross_hunter — history verisi borsapy'den gelecek
#
# ================================================================

import math
import logging
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

try:
    import borsapy as bp
    BORSAPY_AVAILABLE = True
except ImportError:
    BORSAPY_AVAILABLE = False

log = logging.getLogger("bistbull")

# ================================================================
# BANKA DETECTION — borsapy'de financial_group="UFRS" lazım
# ================================================================
BANK_TICKERS = {
    "AKBNK", "GARAN", "ISCTR", "YKBNK", "VAKBN", "HALKB",
    "TSKB", "SKBNK", "ALBRK",
}

HOLDING_TICKERS = {
    "SAHOL", "KCHOL", "DOHOL", "AGHOL", "ISMEN",
}

def is_bank(ticker):
    """Ticker banka mı? UFRS formatı mı kullanılacak?"""
    return ticker.upper().replace(".IS", "") in BANK_TICKERS


# ================================================================
# TURKISH ROW NAME MAPPING — İsyatırım bilanço/gelir/nakit akış
# Her metric için olası Türkçe satır isimlerini listele (öncelik sırasıyla)
# ================================================================

# --- BİLANÇO (Balance Sheet) ---
BS_MAP = {
    "total_assets": [
        "Toplam Varlıklar",
        "TOPLAM VARLIKLAR",
        "Varlık Toplamı",
    ],
    "current_assets": [
        "Dönen Varlıklar",
        "DÖNEN VARLIKLAR",
    ],
    "cash": [
        "Nakit ve Nakit Benzerleri",
        "Nakit ve nakit benzerleri",
        "Nakit Ve Nakit Benzerleri",
    ],
    "receivables": [
        "Ticari Alacaklar",
        "Kısa Vadeli Ticari Alacaklar",
    ],
    "ppe": [
        "Maddi Duran Varlıklar",
        "Maddi duran varlıklar",
    ],
    "current_liabilities": [
        "Kısa Vadeli Yükümlülükler",
        "KISA VADELİ YÜKÜMLÜLÜKLER",
    ],
    "total_liabilities": [
        "Toplam Yükümlülükler",
        "TOPLAM YÜKÜMLÜLÜKLER",
    ],
    "total_debt_short": [
        "Kısa Vadeli Borçlanmalar",
        "Finansal Borçlar",  # kısa vadeli section'da
    ],
    "total_debt_long": [
        "Uzun Vadeli Borçlanmalar",
        "Uzun Vadeli Finansal Borçlar",
    ],
    "equity": [
        "Ana Ortaklığa Ait Özkaynaklar",
        "Özkaynaklar",
        "ÖZKAYNAKLAR",
        "Ana ortaklığa ait özkaynaklar",
    ],
    "retained_earnings": [
        "Geçmiş Yıllar Karları",
        "Geçmiş Yıllar Kar/Zararları",
        "Kardan Ayrılan Kısıtlanmış Yedekler",
    ],
}

# --- GELİR TABLOSU (Income Statement) ---
IS_MAP = {
    "revenue": [
        "Hasılat",
        "HASILAT",
        "Satış Gelirleri",
        "Net Satışlar",
    ],
    "cost_of_revenue": [
        "Satışların Maliyeti (-)",
        "Satışların Maliyeti",
    ],
    "gross_profit": [
        "Brüt Kar (Zarar)",
        "BRÜT KAR (ZARAR)",
        "Ticari Faaliyetlerden Brüt Kar (Zarar)",
    ],
    "operating_income": [
        "Esas Faaliyet Karı (Zararı)",
        "ESAS FAALİYET KARI (ZARARI)",
        "Sürdürülen Faaliyetler Esas Faaliyet Karı (Zararı)",
    ],
    "ebit": [
        "Esas Faaliyet Karı (Zararı)",
        "Faiz, Vergi ve Amortisman Öncesi Kar",
    ],
    "ebitda": [
        "FAVÖK",
        "Faiz Amortisman ve Vergi Öncesi Kar",
    ],
    "net_income": [
        "Dönem Karı (Zararı)",
        "DÖNEM KARI (ZARARI)",
        "Sürdürülen Faaliyetler Dönem Karı (Zararı)",
        "Ana Ortaklık Payları",
    ],
    "interest_expense": [
        "Finansman Giderleri (-)",
        "Finansman Giderleri",
        "Faiz Giderleri",
    ],
    "sga": [
        "Genel Yönetim Giderleri (-)",
        "Genel Yönetim Giderleri",
        "Pazarlama, Satış ve Dağıtım Giderleri (-)",
    ],
    "depreciation": [
        "Amortisman ve İtfa Gideri",
        "Amortisman Giderleri",
        "Amortisman ve İtfa Giderleri",
    ],
    "diluted_shares": [
        "Sulandırılmış Pay Başına Kazanç",
        "Sürdürülen Faaliyetlerden Pay Başına Kazanç",
    ],
    "eps": [
        "Pay Başına Kazanç",
        "Sürdürülen Faaliyetlerden Pay Başına Kazanç",
    ],
    "tax_expense": [
        "Vergi Gideri",
        "Sürdürülen Faaliyetler Vergi Gideri (-)",
        "Dönem Vergi Gideri",
    ],
}

# --- NAKİT AKIŞ (Cash Flow) ---
CF_MAP = {
    "operating_cf": [
        "İşletme Faaliyetlerinden Nakit Akışları",
        "İŞLETME FAALİYETLERİNDEN NAKİT AKIŞLARI",
        "A. İşletme Faaliyetlerinden Nakit Akışları",
    ],
    "investing_cf": [
        "Yatırım Faaliyetlerinden Nakit Akışları",
        "B. Yatırım Faaliyetlerinden Nakit Akışları",
    ],
    "financing_cf": [
        "Finansman Faaliyetlerinden Nakit Akışları",
        "C. Finansman Faaliyetlerinden Nakit Akışları",
    ],
    "capex": [
        "Maddi ve Maddi Olmayan Duran Varlık Alımlarından Kaynaklanan Nakit Çıkışları",
        "Maddi Duran Varlık Alımları",
        "Maddi ve Maddi Olmayan Duran Varlık Alımları",
    ],
    "depreciation_cf": [
        "Amortisman ve İtfa Gideri İle İlgili Düzeltmeler",
        "Amortisman ve İtfa Giderleri",
    ],
}


# ================================================================
# PICK HELPERS — borsapy DataFrame'den Türkçe satır ismiyle veri çek
# ================================================================

def _safe_num(x):
    """Güvenli sayı dönüşümü"""
    try:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _pick_from_df(df, name_list, col_idx=0):
    """
    DataFrame'den Türkçe satır ismi listesiyle değer çek.
    col_idx=0 → en güncel dönem, col_idx=1 → bir önceki dönem
    
    Returns: value (float or None)
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    if col_idx >= len(df.columns):
        return None
    
    col = df.columns[col_idx]
    
    for name in name_list:
        # Exact match
        if name in df.index:
            val = _safe_num(df.loc[name, col])
            if val is not None:
                return val
        # Case-insensitive / partial match
        for idx_name in df.index:
            if isinstance(idx_name, str) and name.lower() in idx_name.lower():
                val = _safe_num(df.loc[idx_name, col])
                if val is not None:
                    return val
    return None


def _pick_pair(df, name_list):
    """
    En güncel ve bir önceki dönem değerini al.
    Returns: (current, previous) — both float or None
    """
    cur = _pick_from_df(df, name_list, col_idx=0)
    prev = _pick_from_df(df, name_list, col_idx=1)
    return cur, prev


# ================================================================
# FETCH RAW V9 — borsapy ile gerçek KAP verisi
# ================================================================

def fetch_raw_v9(symbol, raw_cache=None):
    """
    borsapy ile hisse verisi çek. yfinance fetch_raw() yerine geçer.
    
    Returns dict: {
        "info": dict,          # fast_info + info alanları
        "fast": dict,          # fast_info verileri
        "financials": DataFrame,  # gelir tablosu (yıllık)
        "balance": DataFrame,     # bilanço (yıllık)
        "cashflow": DataFrame,    # nakit akış (yıllık)
        "source": "borsapy",
    }
    """
    if not BORSAPY_AVAILABLE:
        raise ImportError("borsapy kurulu değil: pip install borsapy")
    
    # Cache check
    if raw_cache is not None and symbol in raw_cache:
        return raw_cache[symbol]
    
    ticker_clean = symbol.upper().replace(".IS", "").replace(".E", "")
    
    try:
        tk = bp.Ticker(ticker_clean)
    except Exception as e:
        log.warning(f"borsapy Ticker init failed for {ticker_clean}: {e}")
        raise
    
    # --- Fast Info ---
    info = {}
    fast = {}
    try:
        fi = tk.fast_info
        fast_attrs = [
            "last_price", "open", "day_high", "day_low", "previous_close",
            "volume", "market_cap", "shares", "pe_ratio", "pb_ratio",
            "year_high", "year_low", "fifty_day_average", "two_hundred_day_average",
            "free_float", "foreign_ratio",
        ]
        for attr in fast_attrs:
            try:
                fast[attr] = getattr(fi, attr)
            except Exception:
                fast[attr] = None
    except Exception as e:
        log.warning(f"fast_info failed for {ticker_clean}: {e}")
    
    # --- Enriched Info (TradingView-backed, may fail) ---
    try:
        full_info = tk.info
        # full_info is EnrichedInfo, access like dict
        info_keys = [
            "sector", "industry", "shortName", "longName", "longBusinessSummary",
            "website", "currency", "marketCap", "trailingPE", "forwardPE",
            "priceToBook", "enterpriseToEbitda", "dividendYield",
            "returnOnEquity", "returnOnAssets", "operatingMargins",
            "profitMargins", "currentRatio", "debtToEquity", "beta",
            "revenueGrowth", "earningsGrowth", "freeCashflow", "currentPrice",
            "trailingEps", "bookValue", "heldPercentInstitutions",
            "effectiveTaxRate",
        ]
        for k in info_keys:
            try:
                info[k] = full_info[k]
            except (KeyError, Exception):
                info[k] = None
    except Exception as e:
        log.debug(f"info (TradingView) failed for {ticker_clean}: {e}")
        # Fall back to fast_info mapping
        info["currentPrice"] = fast.get("last_price")
        info["marketCap"] = fast.get("market_cap")
        info["trailingPE"] = fast.get("pe_ratio")
        info["priceToBook"] = fast.get("pb_ratio")
        info["currency"] = "TRY"
    
    # --- Finansal Tablolar (KAP gerçek veri) ---
    fin_group = "UFRS" if is_bank(ticker_clean) else None
    
    financials = None
    balance = None
    cashflow = None
    
    try:
        financials = tk.get_income_stmt(
            quarterly=False,
            financial_group=fin_group,
            last_n=4,
        )
    except Exception as e:
        log.warning(f"income_stmt failed for {ticker_clean}: {e}")
    
    try:
        balance = tk.get_balance_sheet(
            quarterly=False,
            financial_group=fin_group,
            last_n=4,
        )
    except Exception as e:
        log.warning(f"balance_sheet failed for {ticker_clean}: {e}")
    
    try:
        cashflow = tk.get_cashflow(
            quarterly=False,
            financial_group=fin_group,
            last_n=4,
        )
    except Exception as e:
        # Bankalar için nakit akış yok (UFRS)
        if is_bank(ticker_clean):
            log.debug(f"cashflow N/A for bank {ticker_clean}")
        else:
            log.warning(f"cashflow failed for {ticker_clean}: {e}")
    
    raw = {
        "info": info,
        "fast": fast,
        "financials": financials,
        "balance": balance,
        "cashflow": cashflow,
        "source": "borsapy",
        "ticker_clean": ticker_clean,
        "is_bank": is_bank(ticker_clean),
    }
    
    if raw_cache is not None:
        raw_cache[symbol] = raw
    
    return raw


# ================================================================
# COMPUTE METRICS V9 — aynı dict yapısı, borsapy veriden
# ================================================================

def compute_metrics_v9(symbol, raw_cache=None):
    """
    borsapy raw verisinden tüm metrikleri hesapla.
    Dönen dict, mevcut scoring engine'in beklediği tüm key'leri içerir.
    """
    raw = fetch_raw_v9(symbol, raw_cache)
    info = raw["info"]
    fast = raw["fast"]
    fin = raw["financials"]
    bal = raw["balance"]
    cf = raw["cashflow"]
    ticker_clean = raw["ticker_clean"]
    
    # =============================================
    # GELİR TABLOSU — pick_pair ile current + prev
    # =============================================
    revenue, revenue_prev = _pick_pair(fin, IS_MAP["revenue"])
    gross_profit, gross_profit_prev = _pick_pair(fin, IS_MAP["gross_profit"])
    operating_income, _ = _pick_pair(fin, IS_MAP["operating_income"])
    ebit = _pick_from_df(fin, IS_MAP["ebit"], 0) or operating_income
    
    # EBITDA: ya direkt satırdan ya da EBIT + amortisman
    ebitda_raw, ebitda_prev = _pick_pair(fin, IS_MAP["ebitda"])
    dep_is, dep_prev_is = _pick_pair(fin, IS_MAP["depreciation"])
    if ebitda_raw is None and ebit is not None and dep_is is not None:
        ebitda_raw = ebit + abs(dep_is)
    ebitda = ebitda_raw
    if ebitda_prev is None and revenue_prev is not None and dep_prev_is is not None:
        ebit_prev = _pick_from_df(fin, IS_MAP["ebit"], 1)
        if ebit_prev is not None:
            ebitda_prev = ebit_prev + abs(dep_prev_is)
    
    net_income, net_income_prev = _pick_pair(fin, IS_MAP["net_income"])
    interest_exp = _pick_from_df(fin, IS_MAP["interest_expense"], 0)
    sga, sga_prev = _pick_pair(fin, IS_MAP["sga"])
    eps_row = _pick_from_df(fin, IS_MAP["eps"], 0)
    eps_row_prev = _pick_from_df(fin, IS_MAP["eps"], 1)
    
    # =============================================
    # NAKİT AKIŞ
    # =============================================
    op_cf = _pick_from_df(cf, CF_MAP["operating_cf"], 0)
    capex = _pick_from_df(cf, CF_MAP["capex"], 0)
    dep, dep_prev = _pick_pair(cf, CF_MAP["depreciation_cf"])
    if dep is None:
        dep = dep_is  # gelir tablosundan fallback
    if dep_prev is None:
        dep_prev = dep_prev_is
    
    # =============================================
    # BİLANÇO
    # =============================================
    total_assets, total_assets_prev = _pick_pair(bal, BS_MAP["total_assets"])
    total_liab = _pick_from_df(bal, BS_MAP["total_liabilities"], 0)
    
    # Toplam borç = kısa vadeli + uzun vadeli finansal borçlar
    debt_short = _pick_from_df(bal, BS_MAP["total_debt_short"], 0)
    debt_long = _pick_from_df(bal, BS_MAP["total_debt_long"], 0)
    total_debt = None
    if debt_short is not None or debt_long is not None:
        total_debt = (debt_short or 0) + (debt_long or 0)
    
    debt_short_prev = _pick_from_df(bal, BS_MAP["total_debt_short"], 1)
    debt_long_prev = _pick_from_df(bal, BS_MAP["total_debt_long"], 1)
    total_debt_prev = None
    if debt_short_prev is not None or debt_long_prev is not None:
        total_debt_prev = (debt_short_prev or 0) + (debt_long_prev or 0)
    
    cash = _pick_from_df(bal, BS_MAP["cash"], 0)
    cur_assets, cur_assets_prev = _pick_pair(bal, BS_MAP["current_assets"])
    cur_liab, cur_liab_prev = _pick_pair(bal, BS_MAP["current_liabilities"])
    ret_earn = _pick_from_df(bal, BS_MAP["retained_earnings"], 0)
    equity = _pick_from_df(bal, BS_MAP["equity"], 0)
    receivables, rec_prev = _pick_pair(bal, BS_MAP["receivables"])
    ppe, ppe_prev = _pick_pair(bal, BS_MAP["ppe"])
    
    # =============================================
    # TÜRETME — fiyat, oran, büyüme
    # =============================================
    price = _safe_num(fast.get("last_price")) or _safe_num(info.get("currentPrice"))
    market_cap = _safe_num(fast.get("market_cap")) or _safe_num(info.get("marketCap"))
    pe = _safe_num(fast.get("pe_ratio")) or _safe_num(info.get("trailingPE"))
    pb = _safe_num(fast.get("pb_ratio")) or _safe_num(info.get("priceToBook"))
    ev_ebitda = _safe_num(info.get("enterpriseToEbitda"))
    div_yield = _safe_num(info.get("dividendYield"))
    beta = _safe_num(info.get("beta"))
    
    # Shares — borsapy fast_info
    shares = _safe_num(fast.get("shares"))
    
    # EPS
    trailing_eps = _safe_num(info.get("trailingEps")) or eps_row
    if trailing_eps is None and net_income is not None and shares is not None and shares > 0:
        trailing_eps = net_income / shares
    
    # Book value per share
    book_val_ps = _safe_num(info.get("bookValue"))
    if book_val_ps is None and equity is not None and shares is not None and shares > 0:
        book_val_ps = equity / shares
    
    # ROE, ROA
    roe = _safe_num(info.get("returnOnEquity"))
    if roe is None and net_income is not None and equity is not None and equity != 0:
        roe = net_income / equity
    
    roa = _safe_num(info.get("returnOnAssets"))
    if roa is None and net_income is not None and total_assets is not None and total_assets != 0:
        roa = net_income / total_assets
    
    roa_prev = None
    if net_income_prev is not None and total_assets_prev is not None and total_assets_prev != 0:
        roa_prev = net_income_prev / total_assets_prev
    
    # Margins
    gross_margin = (gross_profit / revenue) if gross_profit is not None and revenue not in (None, 0) else None
    gross_margin_prev = (gross_profit_prev / revenue_prev) if gross_profit_prev is not None and revenue_prev not in (None, 0) else None
    
    op_margin = _safe_num(info.get("operatingMargins"))
    if op_margin is None and operating_income is not None and revenue not in (None, 0):
        op_margin = operating_income / revenue
    
    net_margin = _safe_num(info.get("profitMargins"))
    if net_margin is None and net_income is not None and revenue not in (None, 0):
        net_margin = net_income / revenue
    
    # Ratios
    cur_ratio = _safe_num(info.get("currentRatio"))
    if cur_ratio is None and cur_assets is not None and cur_liab not in (None, 0):
        cur_ratio = cur_assets / cur_liab
    cur_ratio_prev = None
    if cur_assets_prev is not None and cur_liab_prev not in (None, 0):
        cur_ratio_prev = cur_assets_prev / cur_liab_prev
    
    debt_eq = _safe_num(info.get("debtToEquity"))
    if debt_eq is None and total_debt is not None and equity not in (None, 0):
        debt_eq = (total_debt / equity) * 100
    
    # Net Debt
    net_debt = (total_debt - cash) if total_debt is not None and cash is not None else None
    net_debt_ebit = (net_debt / ebitda) if net_debt is not None and ebitda not in (None, 0) else None
    
    # Interest Coverage
    _ebit_val = ebit if ebit is not None else operating_income
    int_cov = None
    if _ebit_val is not None and interest_exp is not None and interest_exp != 0:
        int_cov = _ebit_val / abs(interest_exp)
    
    # FCF
    free_cf = None
    if op_cf is not None and capex is not None:
        free_cf = op_cf + capex  # capex genelde negatif
    if free_cf is None:
        free_cf = _safe_num(info.get("freeCashflow"))
    
    fcf_yield = (free_cf / market_cap) if free_cf is not None and market_cap not in (None, 0) else None
    fcf_margin = (free_cf / revenue) if free_cf is not None and revenue not in (None, 0) else None
    cfo_to_ni = (op_cf / net_income) if op_cf is not None and net_income not in (None, 0) else None
    
    # Growth
    def _growth(cur, prev):
        if cur is None or prev in (None, 0):
            return None
        return (cur - prev) / abs(prev)
    
    rev_growth = _safe_num(info.get("revenueGrowth")) or _growth(revenue, revenue_prev)
    eps_growth = _safe_num(info.get("earningsGrowth")) or _growth(eps_row, eps_row_prev) or _growth(net_income, net_income_prev)
    ebit_growth = _growth(ebitda, ebitda_prev)
    
    # Working Capital
    wc = (cur_assets - cur_liab) if cur_assets is not None and cur_liab is not None else None
    
    # Tax
    tax_rate = _safe_num(info.get("effectiveTaxRate")) or 0.20
    
    # Invested Capital
    inv_cap = None
    if total_debt is not None and equity is not None and cash is not None:
        inv_cap = total_debt + equity - cash
    
    _ebit_nopat = ebit if ebit is not None else operating_income
    nopat = (_ebit_nopat * (1 - min(max(tax_rate, 0), 0.35))) if _ebit_nopat is not None else None
    roic = (nopat / inv_cap) if nopat is not None and inv_cap not in (None, 0) else None
    
    # PEG
    peg = None
    if pe not in (None, 0) and eps_growth is not None and eps_growth > 0:
        peg = pe / max(eps_growth * 100, 1e-9)
    
    # Graham Fair Value
    graham_fv = None
    if trailing_eps not in (None, 0) and book_val_ps not in (None, 0) and trailing_eps > 0 and book_val_ps > 0:
        graham_fv = (22.5 * trailing_eps * book_val_ps) ** 0.5
    
    mos = None
    if graham_fv not in (None, 0) and price is not None:
        mos = (graham_fv - price) / graham_fv
    
    # Share dilution — borsapy'den shares değişimi
    # (şimdilik None, ileriki versiyonda KAP sermaye artırımlarından)
    share_ch = None
    
    # Asset turnover
    asset_to = (revenue / total_assets) if revenue is not None and total_assets not in (None, 0) else None
    asset_to_p = (revenue_prev / total_assets_prev) if revenue_prev is not None and total_assets_prev not in (None, 0) else None
    
    # Foreign ratio (borsapy fast_info — gerçek MKK verisi!)
    foreign_ratio = _safe_num(fast.get("foreign_ratio"))
    inst_holders_pct = foreign_ratio  # yfinance uyumu — heldPercentInstitutions yerine
    if inst_holders_pct is None:
        inst_holders_pct = _safe_num(info.get("heldPercentInstitutions"))
    
    # EV/EBITDA hesaplama (eğer info'dan gelmediyse)
    if ev_ebitda is None and market_cap is not None and ebitda not in (None, 0):
        ev = market_cap + (total_debt or 0) - (cash or 0)
        ev_ebitda = ev / ebitda
    
    # =============================================
    # METRIC DICT — mevcut scoring engine ile %100 uyumlu
    # =============================================
    m = {
        "symbol": symbol,
        "ticker": ticker_clean,
        "name": str(info.get("shortName") or info.get("longName") or ticker_clean),
        "currency": str(info.get("currency") or "TRY"),
        "sector": str(info.get("sector") or ""),
        "industry": str(info.get("industry") or ""),
        "price": price,
        "market_cap": market_cap,
        "pe": pe,
        "pb": pb,
        "ev_ebitda": ev_ebitda,
        "dividend_yield": div_yield,
        "beta": beta,
        "revenue": revenue,
        "revenue_prev": revenue_prev,
        "gross_profit": gross_profit,
        "gross_profit_prev": gross_profit_prev,
        "operating_income": operating_income,
        "ebit": ebit or operating_income,
        "ebitda": ebitda,
        "ebitda_prev": ebitda_prev,
        "net_income": net_income,
        "net_income_prev": net_income_prev,
        "operating_cf": op_cf,
        "free_cf": free_cf,
        "total_assets": total_assets,
        "total_assets_prev": total_assets_prev,
        "total_liabilities": total_liab,
        "total_debt": total_debt,
        "total_debt_prev": total_debt_prev,
        "cash": cash,
        "current_assets": cur_assets,
        "current_assets_prev": cur_assets_prev,
        "current_liabilities": cur_liab,
        "current_liabilities_prev": cur_liab_prev,
        "working_capital": wc,
        "retained_earnings": ret_earn,
        "equity": equity,
        "receivables": receivables,
        "receivables_prev": rec_prev,
        "ppe": ppe,
        "ppe_prev": ppe_prev,
        "depreciation": dep,
        "depreciation_prev": dep_prev,
        "sga": sga,
        "sga_prev": sga_prev,
        "trailing_eps": trailing_eps,
        "book_value_ps": book_val_ps,
        "roe": roe,
        "roa": roa,
        "roa_prev": roa_prev,
        "roic": roic,
        "gross_margin": gross_margin,
        "gross_margin_prev": gross_margin_prev,
        "operating_margin": op_margin,
        "net_margin": net_margin,
        "current_ratio": cur_ratio,
        "current_ratio_prev": cur_ratio_prev,
        "debt_equity": debt_eq,
        "net_debt_ebitda": net_debt_ebit,
        "interest_coverage": int_cov,
        "fcf_yield": fcf_yield,
        "fcf_margin": fcf_margin,
        "cfo_to_ni": cfo_to_ni,
        "revenue_growth": rev_growth,
        "eps_growth": eps_growth,
        "ebitda_growth": ebit_growth,
        "peg": peg,
        "graham_fv": graham_fv,
        "margin_safety": mos,
        "share_change": share_ch,
        "asset_turnover": asset_to,
        "asset_turnover_prev": asset_to_p,
        "inst_holders_pct": inst_holders_pct,
        # === V9 EK: borsapy özgü alanlar ===
        "foreign_ratio": foreign_ratio,
        "free_float": _safe_num(fast.get("free_float")),
        "data_source": "borsapy",
    }
    
    # Piotroski, Altman, Beneish — mevcut fonksiyonlar aynen kullanılacak
    # (bunlar app.py'de kalıyor, burada import etmiyoruz)
    # analyze_symbol içinde çağrılacak:
    #   m["piotroski_f"] = compute_piotroski(m)
    #   m["altman_z"] = compute_altman(m)
    #   m["beneish_m"] = compute_beneish(m)
    
    return m


# ================================================================
# BATCH DOWNLOAD HISTORY V9 — borsapy ile fiyat geçmişi
# ================================================================

def batch_download_history_v9(symbols, period="1y", interval="1d"):
    """
    borsapy ile batch fiyat geçmişi indir.
    Returns: dict of {symbol: DataFrame} — yfinance formatında (Open, High, Low, Close, Volume)
    
    NOT: borsapy TradingView WebSocket kullanıyor, batch yokmuş gibi teker teker.
    ThreadPoolExecutor ile paralelize ediyoruz.
    """
    if not BORSAPY_AVAILABLE:
        return {}
    
    result = {}
    
    # borsapy period formatı: "1y" → "1y", "1ay" → "1ay"
    # İngilizce period'u Türkçe'ye çevir
    period_map = {
        "1y": "1y",
        "6mo": "6ay",
        "3mo": "3ay",
        "1mo": "1ay",
        "5d": "5g",
        "1d": "1g",
        "max": "max",
    }
    bp_period = period_map.get(period, period)
    
    # Interval mapping
    interval_map = {
        "1d": "1d",
        "1h": "1h",
        "15m": "15m",
        "5m": "5m",
    }
    bp_interval = interval_map.get(interval, interval)
    
    def _fetch_one(sym):
        ticker_clean = sym.upper().replace(".IS", "").replace(".E", "")
        try:
            tk = bp.Ticker(ticker_clean)
            df = tk.history(period=bp_period, interval=bp_interval)
            if df is not None and not df.empty and len(df) >= 20:
                # borsapy columns: Open, High, Low, Close, Volume (aynı)
                return sym, df
        except Exception as e:
            log.debug(f"history failed for {ticker_clean}: {e}")
        return sym, None
    
    with ThreadPoolExecutor(max_workers=min(10, len(symbols))) as pool:
        futures = [pool.submit(_fetch_one, s) for s in symbols]
        for future in as_completed(futures):
            sym, df = future.result()
            if df is not None:
                result[sym] = df
    
    return result


# ================================================================
# MIGRATION HELPER — app.py'ye patch uygula
# ================================================================
"""
app.py'de yapılacak değişiklikler:

1. import ekle:
   from data_layer_v9 import fetch_raw_v9, compute_metrics_v9, batch_download_history_v9

2. fetch_raw() çağrılarını değiştir:
   - def compute_metrics(symbol):
   -     raw = fetch_raw(symbol)
   + def compute_metrics(symbol):
   +     return compute_metrics_v9(symbol, raw_cache=RAW_CACHE)

3. batch_download_history() çağrısını değiştir:
   - history_map = batch_download_history(symbols, period="1y", interval="1d")
   + history_map = batch_download_history_v9(symbols, period="1y", interval="1d")

4. yfinance import'unu kaldır (veya fallback olarak bırak):
   - import yfinance as yf
   + # import yfinance as yf  # V9: artık borsapy kullanıyoruz

5. normalize_symbol() güncelle:
   def normalize_symbol(ticker):
       t = (ticker or "").strip().upper().replace(" ", "")
       if t.endswith(".IS"): return t
       return t  # borsapy .IS suffix istemiyor
"""


# ================================================================
# DIAGNOSTIC — veri kalitesi raporu
# ================================================================

def diagnose_ticker(symbol):
    """
    Bir ticker için veri kalitesi raporu üret.
    Hangi alanlar gerçek, hangileri None?
    Fintables ile cross-check için kullan.
    """
    try:
        m = compute_metrics_v9(symbol)
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}
    
    critical_fields = [
        "price", "market_cap", "pe", "pb", "revenue", "net_income",
        "total_assets", "equity", "operating_cf", "free_cf",
        "roe", "roa", "gross_margin", "net_margin",
        "current_ratio", "debt_equity", "peg", "graham_fv",
    ]
    
    report = {
        "symbol": symbol,
        "name": m.get("name"),
        "data_source": m.get("data_source"),
        "foreign_ratio": m.get("foreign_ratio"),
        "free_float": m.get("free_float"),
        "fields": {},
        "coverage": 0,
    }
    
    filled = 0
    for field in critical_fields:
        val = m.get(field)
        has_data = val is not None
        report["fields"][field] = {
            "value": val,
            "has_data": has_data,
        }
        if has_data:
            filled += 1
    
    report["coverage"] = round(100 * filled / len(critical_fields), 1)
    return report


# ================================================================
# TEST — Railway'de çalıştır
# ================================================================
if __name__ == "__main__":
    import json
    
    test_tickers = ["THYAO", "AKBNK", "ASELS", "BIMAS", "EREGL"]
    
    for t in test_tickers:
        print(f"\n{'='*60}")
        print(f"TESTING: {t}")
        print(f"{'='*60}")
        try:
            report = diagnose_ticker(t)
            print(f"  Name: {report.get('name')}")
            print(f"  Source: {report.get('data_source')}")
            print(f"  Coverage: {report.get('coverage')}%")
            print(f"  Foreign: {report.get('foreign_ratio')}")
            print(f"  Free Float: {report.get('free_float')}")
            print(f"  Fields:")
            for field, info in report.get("fields", {}).items():
                status = "✓" if info["has_data"] else "✗"
                val = info["value"]
                if isinstance(val, float) and abs(val) > 1e6:
                    val = f"{val/1e9:.2f}B"
                print(f"    {status} {field}: {val}")
        except Exception as e:
            print(f"  ERROR: {e}")

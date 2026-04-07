# ================================================================
# BISTBULL TERMINAL V10.0 — ANALYSIS ENGINE
# compute_metrics, Piotroski, Altman, Beneish, analyze_symbol
# yfinance + borsapy unified data path.
# V9.1 birebir korunmuş + V10 applicability flags eklendi.
# ================================================================

from __future__ import annotations

import os
import logging
from typing import Optional, Any

import pandas as pd

from utils.helpers import safe_num, pick_row_pair, growth, base_ticker
from core.cache import raw_cache, analysis_cache
from config import UNIVERSE
from engine.metrics import normalize_metrics, compute_score_coverage, confidence_penalty_for_imputed_scores
from engine.explainability import build_explanation

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
    pts, used = 0, 0
    tests = [
        (m.get("roa", 0) > 0) if m.get("roa") is not None else None,
        (m.get("operating_cf", 0) > 0) if m.get("operating_cf") is not None else None,
        (m.get("roa", 0) > m.get("roa_prev", 0)) if (m.get("roa") is not None and m.get("roa_prev") is not None) else None,
        (m.get("operating_cf", 0) > m.get("net_income", 0)) if (m.get("operating_cf") is not None and m.get("net_income") is not None) else None,
        (m.get("current_ratio", 0) > m.get("current_ratio_prev", 0)) if (m.get("current_ratio") is not None and m.get("current_ratio_prev") is not None) else None,
        (m.get("share_change", 1) <= 0) if m.get("share_change") is not None else None,
        ((m.get("total_debt", 0) / max(m.get("total_assets", 1), 1)) < (m.get("total_debt_prev", 0) / max(m.get("total_assets_prev", 1), 1)))
            if (m.get("total_debt") is not None and m.get("total_assets") is not None
                and m.get("total_debt_prev") is not None and m.get("total_assets_prev") is not None) else None,
        (m.get("gross_margin", 0) > m.get("gross_margin_prev", 0)) if (m.get("gross_margin") is not None and m.get("gross_margin_prev") is not None) else None,
        (m.get("asset_turnover", 0) > m.get("asset_turnover_prev", 0)) if (m.get("asset_turnover") is not None and m.get("asset_turnover_prev") is not None) else None,
    ]
    for t in tests:
        if t is None:
            continue
        used += 1
        pts += int(t)
    return pts if used >= 4 else None


def compute_altman(m: dict) -> Optional[float]:
    wc = safe_num(m.get("working_capital"))
    ta = safe_num(m.get("total_assets"))
    re_ = safe_num(m.get("retained_earnings")) or 0.0
    ebit = safe_num(m.get("ebit"))
    tl = safe_num(m.get("total_liabilities"))
    sales = safe_num(m.get("revenue"))
    mve = safe_num(m.get("market_cap"))
    if None in (wc, ta, ebit, tl, sales, mve) or ta == 0 or tl == 0:
        return None
    return 1.2 * (wc / ta) + 1.4 * (re_ / ta) + 3.3 * (ebit / ta) + 0.6 * (mve / tl) + 1.0 * (sales / ta)


def compute_beneish(m: dict) -> Optional[float]:
    rec = m.get("receivables")
    rec_prev = m.get("receivables_prev")
    sales = m.get("revenue")
    sales_prev = m.get("revenue_prev")
    gp = m.get("gross_profit")
    gp_prev = m.get("gross_profit_prev")
    ca = m.get("current_assets")
    ca_prev = m.get("current_assets_prev")
    ppe = m.get("ppe")
    ppe_prev = m.get("ppe_prev")
    dep = m.get("depreciation")
    dep_prev = m.get("depreciation_prev")
    sga = m.get("sga")
    sga_prev = m.get("sga_prev")
    ta = m.get("total_assets")
    ta_prev = m.get("total_assets_prev")
    ni = m.get("net_income")
    cfo = m.get("operating_cf")

    if any(safe_num(x) in (None, 0) for x in [sales, sales_prev, ta, ta_prev]):
        return None
    try:
        dsri = ((rec or 0) / (sales or 1)) / max((rec_prev or 0) / (sales_prev or 1), 1e-9)
        gm = (gp or 0) / (sales or 1)
        gm_prev = (gp_prev or 0) / (sales_prev or 1)
        gmi = (gm_prev / max(gm, 1e-9)) if gm and gm_prev else 1.0
        aqi_num = 1 - ((ca or 0) + (ppe or 0)) / max(ta, 1e-9)
        aqi_den = 1 - ((ca_prev or 0) + (ppe_prev or 0)) / max(ta_prev, 1e-9)
        aqi = aqi_num / max(aqi_den, 1e-9)
        sgi = sales / max(sales_prev, 1e-9)
        dep_prev_rate = (dep_prev or 0) / max((dep_prev or 0) + (ppe_prev or 0), 1e-9)
        dep_cur_rate = (dep or 0) / max((dep or 0) + (ppe or 0), 1e-9)
        depi = dep_prev_rate / max(dep_cur_rate, 1e-9)
        sgai = (abs(sga or 0) / (sales or 1)) / max(abs(sga_prev or 0) / (sales_prev or 1), 1e-9)
        lvgi = ((m.get("total_debt") or 0) / max(ta, 1e-9)) / max((m.get("total_debt_prev") or 0) / max(ta_prev, 1e-9), 1e-9)
        tata = ((ni or 0) - (cfo or 0)) / max(ta, 1e-9)
        return -4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi
    except Exception:
        return None


# ================================================================
# COMPUTE METRICS — yfinance fallback path
# ================================================================
def compute_metrics(symbol: str) -> dict:
    """V9: borsapy primary → yfinance fallback → metric dict."""
    raw = fetch_raw(symbol)

    # borsapy path
    if raw.get("source") == "borsapy" and BORSAPY_AVAILABLE and compute_metrics_v9 is not None:
        m = compute_metrics_v9(symbol)
        m["piotroski_f"] = compute_piotroski(m)
        m["altman_z"] = compute_altman(m)
        m["beneish_m"] = compute_beneish(m)
        return m

    # yfinance fallback
    info = raw["info"]
    fast = raw["fast"]
    fin = raw["financials"]
    bal = raw["balance"]
    cf = raw["cashflow"]

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
    book_val_ps = safe_num(info.get("bookValue")) or ((equity / dil_shares) if equity and dil_shares else None)

    roe = safe_num(info.get("returnOnEquity")) or ((net_income / equity) if net_income and equity else None)
    roa = safe_num(info.get("returnOnAssets")) or ((net_income / total_assets) if net_income and total_assets else None)
    roa_prev = (net_income_prev / total_assets_prev) if net_income_prev and total_assets_prev else None
    gross_margin = (gross_profit / revenue) if gross_profit and revenue else None
    gross_margin_prev = (gross_profit_prev / revenue_prev) if gross_profit_prev and revenue_prev else None
    op_margin = safe_num(info.get("operatingMargins")) or ((operating_income / revenue) if operating_income and revenue else None)
    net_margin = safe_num(info.get("profitMargins")) or ((net_income / revenue) if net_income and revenue else None)
    cur_ratio = safe_num(info.get("currentRatio")) or ((cur_assets / cur_liab) if cur_assets and cur_liab else None)
    cur_ratio_prev = (cur_assets_prev / cur_liab_prev) if cur_assets_prev and cur_liab_prev else None
    debt_eq = safe_num(info.get("debtToEquity")) or ((total_debt / equity * 100) if total_debt and equity else None)

    net_debt = (total_debt - cash) if total_debt is not None and cash is not None else None
    net_debt_ebit = (net_debt / ebitda) if net_debt is not None and ebitda not in (None, 0) else None
    _ebit_val = ebit if ebit is not None else operating_income
    int_cov = (_ebit_val / abs(interest_exp)) if _ebit_val is not None and interest_exp not in (None, 0) else None

    free_cf = ((op_cf + capex) if op_cf is not None and capex is not None else None) or safe_num(info.get("freeCashflow"))
    fcf_yield = (free_cf / market_cap) if free_cf is not None and market_cap not in (None, 0) else None
    fcf_margin = (free_cf / revenue) if free_cf is not None and revenue not in (None, 0) else None
    cfo_to_ni = (op_cf / net_income) if op_cf is not None and net_income not in (None, 0) else None

    rev_growth = safe_num(info.get("revenueGrowth")) or growth(revenue, revenue_prev)
    eps_growth = safe_num(info.get("earningsGrowth")) or growth(eps_row, eps_row_prev) or growth(net_income, net_income_prev)
    ebit_growth = growth(ebitda, ebitda_prev)

    wc = (cur_assets - cur_liab) if cur_assets is not None and cur_liab is not None else None
    tax_rate = safe_num(info.get("effectiveTaxRate")) or 0.20
    inv_cap = (total_debt + equity - cash) if total_debt is not None and equity is not None and cash is not None else None
    _ebit_nopat = ebit if ebit is not None else operating_income
    nopat = (_ebit_nopat * (1 - min(max(tax_rate, 0), 0.35))) if _ebit_nopat is not None else None
    roic = (nopat / inv_cap) if nopat is not None and inv_cap not in (None, 0) else None

    peg = (pe / max(eps_growth * 100, 1e-9)) if pe not in (None, 0) and eps_growth is not None and eps_growth > 0 else None
    graham_fv = ((22.5 * trailing_eps * book_val_ps) ** 0.5) if trailing_eps not in (None, 0) and book_val_ps not in (None, 0) and trailing_eps > 0 and book_val_ps > 0 else None
    mos = ((graham_fv - price) / graham_fv) if graham_fv not in (None, 0) and price is not None else None
    share_ch = growth(dil_shares, dil_shares_prev)
    asset_to = (revenue / total_assets) if revenue is not None and total_assets not in (None, 0) else None
    asset_to_p = (revenue_prev / total_assets_prev) if revenue_prev is not None and total_assets_prev not in (None, 0) else None
    inst_holders_pct = safe_num(info.get("heldPercentInstitutions"))

    # Data quality diagnostics
    has_fin = fin is not None and hasattr(fin, 'empty') and not fin.empty
    has_bal = bal is not None and hasattr(bal, 'empty') and not bal.empty
    has_cf = cf is not None and hasattr(cf, 'empty') and not cf.empty
    stmt_count = sum([has_fin, has_bal, has_cf])
    if stmt_count == 0:
        log.warning(f"DATA QUALITY [{base_ticker(symbol)}]: No financial statements via yfinance — using info-dict only")
    elif stmt_count < 3:
        missing = [s for s, ok in [("income", has_fin), ("balance", has_bal), ("cashflow", has_cf)] if not ok]
        log.info(f"DATA QUALITY [{base_ticker(symbol)}]: yfinance missing {', '.join(missing)}")

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
        "inst_holders_pct": inst_holders_pct,
        "ciro_pd": (revenue / market_cap) if revenue is not None and market_cap not in (None, 0) else None,
        "data_source": "yfinance",
        "data_quality": {"income_stmt": has_fin, "balance_sheet": has_bal, "cashflow": has_cf, "fast_info": bool(price)},
    }
    m["piotroski_f"] = compute_piotroski(m)
    m["altman_z"] = compute_altman(m)
    m["beneish_m"] = compute_beneish(m)
    return m


# ================================================================
# ANALYZE SYMBOL — Full pipeline + V10 applicability
# ================================================================
def analyze_symbol(symbol: str) -> dict:
    """Full analysis: metrics → 7-dim scoring → risk → ivme → labels → decision.
    V10: applicability_flags eklendi.
    V10.1: score_coverage + imputation tracking + confidence penalty."""
    cached = analysis_cache.get(symbol)
    if cached is not None:
        return cached

    from engine.scoring import (
        map_sector, score_value, score_quality, score_growth,
        score_balance, score_earnings, score_moat, score_capital,
        score_momentum, score_technical_break, score_institutional_flow,
        compute_risk_penalties, compute_fa_pure, compute_ivme,
        compute_overall, detect_hype, confidence_score,
        timing_label, quality_label, entry_quality_label,
        decision_engine, style_label, legendary_labels, drivers,
    )
    from engine.technical import compute_technical
    from engine.applicability import build_applicability_flags

    m = normalize_metrics(compute_metrics(symbol))
    sector_group = map_sector(m.get("sector", ""))

    tech = None
    try:
        tech = compute_technical(symbol)
    except Exception as e:
        log.debug(f"analyze_symbol tech for {symbol}: {e}")

    # 7 boyut FA — compute raw scores (may be None)
    _IMPUTE_DEFAULT = 50
    _raw_fa = {
        "value": score_value(m, sector_group),
        "quality": score_quality(m, sector_group),
        "growth": score_growth(m, sector_group),
        "balance": score_balance(m, sector_group),
        "earnings": score_earnings(m),
        "moat": score_moat(m),
        "capital": score_capital(m),
    }

    # Track which dimensions were imputed (raw score was None)
    scores_imputed: list[str] = [k for k, v in _raw_fa.items() if v is None]

    # Apply imputation: None → 50 (backward compatible default)
    scores: dict[str, float] = {
        k: round(v if v is not None else _IMPUTE_DEFAULT, 1)
        for k, v in _raw_fa.items()
    }

    if scores_imputed:
        log.debug(
            f"{symbol}: {len(scores_imputed)} FA dimension(s) imputed to {_IMPUTE_DEFAULT}: "
            f"{', '.join(scores_imputed)}"
        )

    fa_pure = compute_fa_pure(scores)
    risk_penalty, risk_reasons = compute_risk_penalties(m, sector_group)

    # Fake Profit filtresi
    cfo_ni = m.get("cfo_to_ni")
    if cfo_ni is not None:
        if m.get("operating_cf") is not None and m["operating_cf"] < 0 and m.get("net_income") is not None and m["net_income"] > 0:
            risk_penalty -= 12
            risk_reasons.append("Kâr var nakit yok — sahte kâr riski (-12)")
        elif cfo_ni < 0.5:
            risk_penalty -= 6
            risk_reasons.append(f"Düşük nakit kalitesi CFO/NI={cfo_ni:.2f} (-6)")

    risk_score = risk_penalty
    deger_score = round(max(1, min(99, fa_pure + risk_penalty)), 1)

    # İvme
    mom = score_momentum(m, tech)
    tb = score_technical_break(m, tech)
    inst = score_institutional_flow(m, tech)
    scores["momentum"] = round(mom, 1) if mom is not None else 50.0
    scores["tech_break"] = round(tb, 1) if tb is not None else 50.0
    scores["inst_flow"] = round(inst, 1) if inst is not None else 50.0

    ivme_score = compute_ivme(scores)
    overall = compute_overall(fa_pure, ivme_score, scores["value"], risk_penalty)

    # Hype
    is_hype, hype_reason = detect_hype(tech, fa_pure)

    # Labels
    t_label = timing_label(ivme_score)
    q_label = quality_label(fa_pure)
    e_label = entry_quality_label(fa_pure, ivme_score, risk_penalty)
    if is_hype:
        e_label = "SPEKÜLATİF"
    decision = decision_engine(fa_pure, ivme_score, risk_penalty, e_label)

    # Confidence — base score minus penalty for imputed dimensions
    confidence = confidence_score(m)
    if scores_imputed:
        imputation_penalty = confidence_penalty_for_imputed_scores(scores_imputed)
        confidence = round(max(0, confidence - imputation_penalty), 1)

    style = style_label(scores)
    legends = legendary_labels(m, scores)
    pos, neg = drivers(scores, confidence, m, sector_group)

    if is_hype and hype_reason:
        neg.insert(0, f"⚠️ HYPE: {hype_reason}")

    if scores_imputed:
        neg.append(f"Veri eksik: {', '.join(scores_imputed)} boyutları tahmini")

    # V10: Applicability flags
    applicability_flags = build_applicability_flags(sector_group)

    # Score coverage — tracks data completeness per dimension
    score_coverage = compute_score_coverage(m)

    r = {
        "symbol": symbol, "ticker": base_ticker(symbol), "name": m["name"], "currency": m["currency"],
        "sector": m.get("sector", ""), "sector_group": sector_group, "industry": m.get("industry", ""),
        "metrics": m, "scores": scores, "overall": overall, "confidence": confidence,
        "fa_score": fa_pure, "deger": deger_score, "ivme": ivme_score,
        "risk_score": risk_score, "entry_label": e_label, "is_hype": is_hype,
        "timing": t_label, "quality_tag": q_label, "decision": decision,
        "risk_penalty": risk_penalty, "risk_reasons": risk_reasons,
        "style": style, "legendary": legends, "positives": pos, "negatives": neg,
        "applicability": applicability_flags,
        "scores_imputed": scores_imputed,
        "score_coverage": score_coverage,
    }

    # Data Quality — trust & anomaly layer (never blocks analysis)
    try:
        from engine.data_quality import assess_data_quality, build_decision_context
        r["data_health"] = assess_data_quality(m, scores_imputed)
        r["data_context"] = r["data_health"]
        r["decision_context"] = build_decision_context(
            r["data_health"], confidence, is_hype, scores_imputed,
        )
    except Exception as e:
        log.debug(f"Data quality layer skipped for {symbol}: {e}")

    # Valuation Trust Layer — range, confidence, assumptions (never blocks analysis)
    try:
        from engine.valuation import build_valuation_layer
        val_layer = build_valuation_layer(m, r)
        r.update(val_layer)
    except Exception as e:
        log.debug(f"Valuation layer skipped for {symbol}: {e}")

    # Timing Intelligence — plain-language timing context (never blocks analysis)
    try:
        from engine.timing_intel import build_timing_intel
        timing_data = build_timing_intel(scores, tech, m)
        r.update(timing_data)
    except Exception as e:
        log.debug(f"Timing intel skipped for {symbol}: {e}")

    # Delta — daily snapshot + 7d change tracking (never blocks analysis)
    try:
        from engine.delta import save_daily_snapshot, compute_delta
        save_daily_snapshot(symbol, r)
        delta_data = compute_delta(symbol, r)
        if delta_data:
            r.update(delta_data)
    except Exception as e:
        log.debug(f"Delta layer skipped for {symbol}: {e}")

    # Explainability — structured scoring explanation (never blocks analysis)
    try:
        r["explanation"] = build_explanation(r)
    except Exception as e:
        log.warning(f"Explainability skipped for {symbol}: {e}")
        r["explanation"] = None

    # V11 Enrichment — mevcut alanları bozmadan v11 block ekler
    try:
        from engine.scoring_v11 import enrich_analysis_v11, enrich_with_tech_v11
        from engine.labels import compute_all_labels
        r = enrich_analysis_v11(r)
        r = enrich_with_tech_v11(r, tech)
        r["v11_labels"] = compute_all_labels(r, tech)
    except Exception as e:
        log.debug(f"V11 enrichment skipped for {symbol}: {e}")

    analysis_cache.set(symbol, r)
    return r

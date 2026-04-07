# ================================================================
# BISTBULL TERMINAL — VALUATION TRUST LAYER
# engine/valuation.py
# ================================================================
from __future__ import annotations
import logging, math
from datetime import date
from typing import Any, Optional

log = logging.getLogger("bistbull.valuation")

_RISK_FREE = 0.30
_EQUITY_PREMIUM = 0.08
_DEFAULT_DISCOUNT = _RISK_FREE + _EQUITY_PREMIUM
_PROJECTION_YEARS = 5
_TERMINAL_GROWTH = 0.04
_BEAR_HAIRCUT = 0.60
_BULL_PREMIUM = 1.40


def build_valuation_layer(metrics: dict, analysis: dict) -> dict:
    try:
        return _build(metrics, analysis)
    except Exception as exc:
        log.warning(f"valuation layer failed: {exc}")
        return _empty()


def _build(m: dict, a: dict) -> dict:
    price = m.get("price")
    market_cap = m.get("market_cap")
    revenue = m.get("revenue")
    ebitda = m.get("ebitda")
    net_income = m.get("net_income")
    free_cf = m.get("free_cf")
    total_debt = m.get("total_debt") or 0
    cash = m.get("cash") or 0
    net_debt = total_debt - cash
    shares = (market_cap / price) if market_cap and price and price > 0 else None
    pe = m.get("pe")
    pb = m.get("pb")
    rev_growth = m.get("revenue_growth")
    net_margin = m.get("net_margin")
    graham_fv = m.get("graham_fv")

    val_inputs = {
        "revenue": revenue, "ebitda": ebitda, "net_income": net_income,
        "free_cf": free_cf, "net_debt": round(net_debt, 0) if net_debt else None,
        "shares_outstanding": round(shares, 0) if shares else None,
        "last_price": price, "market_cap": market_cap,
    }

    growth_rate = _clamp(rev_growth, -0.30, 1.00) if rev_growth is not None else 0.10
    margin_assumption = _clamp(net_margin, 0.01, 0.50) if net_margin is not None else 0.10
    discount_rate = _DEFAULT_DISCOUNT

    assumptions = {
        "growth_rate": round(growth_rate, 4), "discount_rate": round(discount_rate, 4),
        "margin_assumption": round(margin_assumption, 4), "terminal_growth": _TERMINAL_GROWTH,
        "projection_years": _PROJECTION_YEARS, "method": _pick_method(m),
    }

    valuation = _compute_range(m, growth_rate, margin_assumption, discount_rate, shares, net_debt)
    val_confidence = _compute_val_confidence(m, valuation)

    val_data_health = {}
    for key in ("revenue", "ebitda", "net_income", "free_cf", "net_debt", "shares_outstanding"):
        raw = val_inputs.get(key)
        if raw is None: val_data_health[key] = "missing"
        elif key == "shares_outstanding" and shares and shares < 1000: val_data_health[key] = "warning"
        else: val_data_health[key] = "ok"

    val_data_context = {"financial_period": _guess_period(), "market_data_date": date.today().isoformat(), "freshness": "daily"}
    val_context = _sector_comparison(m, a)
    val_risks = _derive_risks(m)
    val_scenarios = {"bull_case": "büyüme hızlanır, marjlar korunursa", "base_case": "mevcut büyüme trendi devam ederse", "risk_case": "büyüme yavaşlar veya marjlar düşerse"}

    return {
        "valuation": valuation, "valuation_confidence": val_confidence,
        "valuation_assumptions": assumptions, "valuation_inputs": val_inputs,
        "valuation_data_context": val_data_context, "valuation_data_health": val_data_health,
        "valuation_context": val_context, "valuation_risks": val_risks,
        "valuation_scenarios": val_scenarios,
    }


def _compute_range(m, growth, margin, discount, shares, net_debt):
    base_ev = None
    method_used = "none"
    fcf = m.get("free_cf")
    if fcf and fcf > 0 and shares and shares > 0:
        base_ev = _simple_dcf(fcf, growth, discount); method_used = "dcf_fcf"
    if base_ev is None:
        ni = m.get("net_income")
        if ni and ni > 0 and shares and shares > 0:
            base_ev = _simple_dcf(ni * 0.7, growth, discount); method_used = "dcf_earnings"
    if base_ev is None:
        rev = m.get("revenue")
        if rev and rev > 0 and margin > 0 and shares and shares > 0:
            base_ev = _simple_dcf(rev * margin * 0.7, growth, discount); method_used = "dcf_revenue"
    if base_ev is None or shares is None or shares <= 0:
        gfv = m.get("graham_fv"); price = m.get("price")
        if gfv and gfv > 0 and price and price > 0:
            return {"bear_case": round(gfv * 0.75, 2), "base_case": round(gfv, 2), "bull_case": round(gfv * 1.25, 2),
                    "range": f"{gfv * 0.75:.0f}–{gfv * 1.25:.0f} TL", "currency": m.get("currency", "TRY"),
                    "method": "graham", "vs_price": round((gfv / price - 1) * 100, 1) if price > 0 else None}
        return {"method": "unavailable"}
    equity_val = base_ev - net_debt
    if equity_val <= 0: equity_val = base_ev * 0.1
    base_ps = equity_val / shares; bear_ps = base_ps * _BEAR_HAIRCUT; bull_ps = base_ps * _BULL_PREMIUM
    price = m.get("price")
    return {"bear_case": round(bear_ps, 2), "base_case": round(base_ps, 2), "bull_case": round(bull_ps, 2),
            "range": f"{bear_ps:.0f}–{bull_ps:.0f} TL", "currency": m.get("currency", "TRY"),
            "method": method_used, "vs_price": round((base_ps / price - 1) * 100, 1) if price and price > 0 else None}


def _simple_dcf(cf, growth, discount):
    if discount <= _TERMINAL_GROWTH: discount = _TERMINAL_GROWTH + 0.05
    total = 0.0; projected_cf = cf
    for yr in range(1, _PROJECTION_YEARS + 1):
        projected_cf *= (1 + growth); total += projected_cf / ((1 + discount) ** yr)
    terminal = projected_cf * (1 + _TERMINAL_GROWTH) / (discount - _TERMINAL_GROWTH)
    total += terminal / ((1 + discount) ** _PROJECTION_YEARS)
    return total


def _compute_val_confidence(m, valuation):
    method = valuation.get("method", "unavailable")
    if method == "unavailable": return {"level": "low", "reason": "değerleme için yeterli veri yok"}
    score = 0; reasons = []
    for k in ("revenue", "net_income", "free_cf", "ebitda"):
        if m.get(k) is not None: score += 1
    if m.get("market_cap") and m.get("price"): score += 1
    if method == "dcf_fcf": score += 2; reasons.append("FCF bazlı DCF")
    elif method == "dcf_earnings": score += 1; reasons.append("kâr bazlı DCF")
    elif method == "graham": reasons.append("Graham değerlemesi")
    if score >= 6: return {"level": "high", "reason": "temel veriler mevcut — " + ", ".join(reasons)}
    elif score >= 3: return {"level": "medium", "reason": "kısmi veri — " + ", ".join(reasons)}
    else: return {"level": "low", "reason": "sınırlı veri — " + ", ".join(reasons)}


def _sector_comparison(m, a):
    pe = m.get("pe"); sector = a.get("sector_group", ""); ctx = {}
    if pe is not None and sector:
        median = _get_sector_median_pe(sector)
        if median:
            interp = "sektör ortalamasının altında" if pe < median * 0.8 else "sektör ortalamasının üstünde" if pe > median * 1.2 else "sektör ortalamasına yakın"
            ctx["pe_vs_sector"] = {"company": round(pe, 1), "sector_median": round(median, 1), "interpretation": interp}
    pb = m.get("pb")
    if pb is not None:
        if pb < 1.0: ctx["pb_note"] = "defter değerinin altında işlem görüyor"
        elif pb > 5.0: ctx["pb_note"] = "defter değerinin çok üstünde"
    return ctx


def _get_sector_median_pe(sector_group):
    try:
        from aggregation import heatmap_cache
        cached = heatmap_cache.get("heatmap")
        if not cached or "items" not in cached: return None
        peers = [it["pe"] for it in cached["items"] if it.get("sector_group") == sector_group and it.get("pe") is not None and 0 < it["pe"] < 500]
        if len(peers) < 3: return None
        peers.sort(); return peers[len(peers) // 2]
    except Exception: return None


def _derive_risks(m):
    risks = []
    rg = m.get("revenue_growth")
    if rg is not None and rg > 0.30: risks.append("büyüme sürdürülebilir olmayabilir")
    nm = m.get("net_margin")
    if nm is not None and nm < 0.05: risks.append("düşük marjlar baskı altında kalabilir")
    de = m.get("debt_equity")
    if de is not None and de > 2.0: risks.append("yüksek kaldıraç — borç maliyeti artabilir")
    if not risks: risks.append("makro koşullar değişirse varsayımlar geçersiz kalabilir")
    return risks[:3]


def _pick_method(m):
    if m.get("free_cf") and m["free_cf"] > 0: return "dcf"
    if m.get("net_income") and m["net_income"] > 0: return "dcf"
    if m.get("graham_fv"): return "graham"
    return "multiples"

def _guess_period():
    today = date.today(); q = (today.month - 1) // 3; yr = today.year if q > 0 else today.year - 1
    return f"{yr} Q{q if q > 0 else 4}"

def _clamp(val, lo, hi): return max(lo, min(hi, val))

def _empty():
    return {"valuation": {"method": "unavailable"}, "valuation_confidence": {"level": "low", "reason": "hesaplanamadı"},
            "valuation_assumptions": {}, "valuation_inputs": {}, "valuation_data_context": {},
            "valuation_data_health": {}, "valuation_context": {}, "valuation_risks": [], "valuation_scenarios": {}}

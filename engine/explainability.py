# ================================================================
# BISTBULL TERMINAL — EXPLAINABILITY ENGINE (v2 — polished)
# engine/explainability.py
#
# Decomposes every score into human-readable structured explanations.
# Pure observation layer — reads the same metrics, applies the same
# thresholds, but does NOT change any scoring behavior.
#
# v2 improvements:
# - Human-friendly Turkish driver names with per-driver explanations
# - Contribution-based ranking (weight x score impact, not raw score)
# - 5-level strength system (strong_positive -> strong_negative)
# - Deterministic 1-2 sentence summary
# - Diversity constraint on top drivers (no same-dimension flooding)
# - Simpler missing-data wording
#
# NO side effects. NO caching. NO AI. NO randomness.
# ================================================================

from __future__ import annotations

from typing import Optional

from config import (
    FA_WEIGHTS, IVME_WEIGHTS, CONFIDENCE_KEYS,
    OVERALL_FA_WEIGHT, OVERALL_MOMENTUM_WEIGHT, OVERALL_RISK_FACTOR, OVERALL_RISK_CAP,
)
from utils.helpers import safe_num, score_higher, score_lower
from engine.scoring import get_threshold, compute_valuation_stretch


# ================================================================
# HUMAN-FRIENDLY NAME + EXPLANATION REGISTRY
# ================================================================
_FRIENDLY = {
    "pe":              {"name": "Ucuz F/K orani",                  "pos": "Kazancina gore ucuz fiyatlaniyor.",                           "neg": "Kazancina gore pahali fiyatlaniyor."},
    "pb":              {"name": "Defter degerine gore fiyat",      "pos": "Varliklarina gore makul fiyatlaniyor.",                       "neg": "Varliklarina gore pahali fiyatlaniyor."},
    "ev_ebitda":       {"name": "FD/FAVOK degerleme",              "pos": "Isletme degeri nakit uretimine gore uygun.",                  "neg": "Isletme degeri nakit uretimine gore yuksek."},
    "fcf_yield":       {"name": "Serbest nakit akisi gucu",       "pos": "Guclu serbest nakit akisi uretiyor.",                         "neg": "Serbest nakit akisi zayif."},
    "margin_safety":   {"name": "Guvenlik marji",                  "pos": "Gercek degerinin altinda fiyatlaniyor.",                      "neg": "Gercek degerine yakin veya uzerinde fiyatlaniyor."},
    "roe":             {"name": "Yuksek ozsermaye karliligi",      "pos": "Ozsermayesini verimli kullaniyor.",                           "neg": "Ozsermaye karliligi sektorun altinda."},
    "roic":            {"name": "Yatirim getirisi (ROIC)",         "pos": "Yatirdigi sermayeden guclu getiri elde ediyor.",              "neg": "Yatirim getirisi dusuk."},
    "net_margin":      {"name": "Net kar marji",                   "pos": "Her 100 TL gelirden guclu kar elde ediyor.",                  "neg": "Kar marji zayif."},
    "revenue_growth":  {"name": "Gelir buyumesi",                  "pos": "Gelirler guclu buyuyor.",                                    "neg": "Gelir buyumesi zayif veya geriliyor."},
    "eps_growth":      {"name": "Hisse basi kar buyumesi",         "pos": "Hisse basi kar artiyor.",                                    "neg": "Hisse basi kar dusuyor."},
    "ebitda_growth":   {"name": "FAVOK buyumesi",                  "pos": "Operasyonel karlilik artiyor.",                               "neg": "Operasyonel karlilik geriliyor."},
    "peg":             {"name": "Buyumeye gore fiyat (PEG)",       "pos": "Buyume hizina gore ucuz.",                                   "neg": "Buyume hizina gore pahali."},
    "net_debt_ebitda": {"name": "Borc yuku (NB/FAVOK)",            "pos": "Borc yuku yonetilebilir seviyede.",                           "neg": "Borc yuku yuksek."},
    "debt_equity":     {"name": "Borcluluk seviyesi",              "pos": "Borclanma ozsermayeye gore makul.",                           "neg": "Ozsermayeye gore asiri borclu."},
    "current_ratio":   {"name": "Kisa vadeli odeme gucu",          "pos": "Kisa vadeli yukumluluklerini rahat karsilayabiliyor.",         "neg": "Kisa vadeli borclarini karsilamakta zorlanabilir."},
    "interest_cov":    {"name": "Faiz karsilama gucu",             "pos": "Faiz odemelerini rahat karsiyor.",                            "neg": "Faiz odemelerini karsilamakta zorlaniyor."},
    "altman_z":        {"name": "Iflas riski (Altman Z)",          "pos": "Iflas riski dusuk.",                                          "neg": "Iflas riski yuksek."},
    "cfo_to_ni":       {"name": "Nakit akisi / kar kalitesi",     "pos": "Kar gercek nakit akisiyla destekleniyor.",                     "neg": "Kar nakit akisiyla desteklenmiyor."},
    "fcf_margin":      {"name": "Serbest nakit marji",             "pos": "Gelirlerin anlamli kismi serbest nakde donuyor.",              "neg": "Serbest nakit akisi marji zayif."},
    "beneish":         {"name": "Muhasebe guvenirliligi",          "pos": "Finansal tablolar guvenilir gorunuyor.",                      "neg": "Finansal tablolarda manipulasyon riski var."},
    "momentum":        {"name": "Fiyat momentumu",                 "pos": "Fiyat ve hacim yukari yonlu guclu.",                          "neg": "Fiyat momentumu zayif."},
    "tech_break":      {"name": "Teknik kirilim",                  "pos": "Teknik sinyaller kirilim gosteriyor.",                        "neg": "Teknik gorunum zayif."},
    "inst_flow":       {"name": "Kurumsal yatirimci ilgisi",       "pos": "Kurumsal alis akisi pozitif.",                                "neg": "Kurumsal ilgi dusuk."},
}

_DIM_NAMES = {
    "value": "degerleme", "quality": "karlilik", "growth": "buyume",
    "balance": "bilanco", "earnings": "nakit kalitesi", "moat": "rekabet gucu",
    "capital": "sermaye disiplini", "momentum": "momentum",
    "tech_break": "teknik", "inst_flow": "kurumsal akis",
}


def _friendly_name(key):
    return _FRIENDLY.get(key, {}).get("name", key)


def _friendly_explanation(key, is_positive):
    f = _FRIENDLY.get(key, {})
    return f.get("pos", "") if is_positive else f.get("neg", "")


# ================================================================
# STRENGTH SYSTEM — 5 levels
# ================================================================
def _strength(score):
    if score is None:
        return "neutral"
    if score >= 75:
        return "strong_positive"
    if score >= 55:
        return "positive"
    if score >= 45:
        return "neutral"
    if score >= 25:
        return "negative"
    return "strong_negative"


def _direction(score):
    if score is None:
        return "neutral"
    if score >= 55:
        return "positive"
    if score <= 45:
        return "negative"
    return "neutral"


# ================================================================
# DRIVER ITEM
# ================================================================
def _driver(key, score=None, value=None, unit="", contribution=0.0, dimension=""):
    is_pos = score is not None and score >= 50
    d = {
        "name": _friendly_name(key),
        "key": key,
        "direction": _direction(score),
        "strength": _strength(score),
        "explanation": _friendly_explanation(key, is_pos),
    }
    if score is not None:
        d["score"] = round(score, 1)
    d["contribution"] = round(contribution, 2)
    if value is not None:
        d["value"] = value
    if unit:
        d["unit"] = unit
    if dimension:
        d["dimension"] = dimension
    return d


# ================================================================
# CONTRIBUTION CALCULATION
# ================================================================
def _sub_contribution(sub_score, dim_weight, n_subs):
    if sub_score is None or n_subs == 0:
        return 0.0
    return dim_weight * (sub_score - 50.0) / n_subs


# ================================================================
# SUB-COMPONENT DETAIL FUNCTIONS
# ================================================================
def _detail_value(m, sg, w):
    th_pe = get_threshold(sg, "pe")
    th_pb = get_threshold(sg, "pb")
    th_ev = get_threshold(sg, "ev_ebitda")
    pe_s = score_lower(m.get("pe"), *th_pe) if th_pe and (m.get("pe") or 0) > 0 else None
    pb_s = score_lower(m.get("pb"), *th_pb) if th_pb and (m.get("pb") or 0) > 0 else None
    ev_s = score_lower(m.get("ev_ebitda"), *th_ev) if th_ev and (m.get("ev_ebitda") or 0) > 0 else None
    fcf_s = score_higher(m.get("fcf_yield"), 0, 0.02, 0.05, 0.08)
    mos_raw = score_higher(m.get("margin_safety"), -0.2, 0, 0.15, 0.30)
    mos_s = min(mos_raw, 70) if mos_raw is not None else None
    valid = [x for x in [pe_s, pb_s, ev_s, fcf_s, mos_s] if x is not None]
    n = max(len(valid), 1)
    return [
        _driver("pe", pe_s, safe_num(m.get("pe")), "", _sub_contribution(pe_s, w, n), "value"),
        _driver("pb", pb_s, safe_num(m.get("pb")), "", _sub_contribution(pb_s, w, n), "value"),
        _driver("ev_ebitda", ev_s, safe_num(m.get("ev_ebitda")), "x", _sub_contribution(ev_s, w, n), "value"),
        _driver("fcf_yield", fcf_s, round(m["fcf_yield"] * 100, 1) if m.get("fcf_yield") is not None else None, "%", _sub_contribution(fcf_s, w, n), "value"),
        _driver("margin_safety", mos_s, round(m["margin_safety"] * 100, 1) if m.get("margin_safety") is not None else None, "%", _sub_contribution(mos_s, w, n), "value"),
    ]


def _detail_quality(m, sg, w):
    th_roe = get_threshold(sg, "roe")
    th_roic = get_threshold(sg, "roic")
    th_nm = get_threshold(sg, "net_margin")
    roe_s = score_higher(m.get("roe"), *th_roe) if th_roe else None
    roic_s = score_higher(m.get("roic"), *th_roic) if th_roic else None
    nm_s = score_higher(m.get("net_margin"), *th_nm) if th_nm else None
    valid = [x for x in [roe_s, roic_s, nm_s] if x is not None]
    n = max(len(valid), 1)
    return [
        _driver("roe", roe_s, round(m["roe"] * 100, 1) if m.get("roe") is not None else None, "%", _sub_contribution(roe_s, w, n), "quality"),
        _driver("roic", roic_s, round(m["roic"] * 100, 1) if m.get("roic") is not None else None, "%", _sub_contribution(roic_s, w, n), "quality"),
        _driver("net_margin", nm_s, round(m["net_margin"] * 100, 1) if m.get("net_margin") is not None else None, "%", _sub_contribution(nm_s, w, n), "quality"),
    ]


def _detail_growth(m, sg, w):
    th_rg = get_threshold(sg, "revenue_growth")
    rg_s = score_higher(m.get("revenue_growth"), *th_rg) if th_rg else None
    eps_s = score_higher(m.get("eps_growth"), -0.10, 0.05, 0.15, 0.30)
    eb_s = score_higher(m.get("ebitda_growth"), -0.05, 0.05, 0.12, 0.25)
    peg_s = score_lower(m.get("peg"), 0.5, 1.0, 1.8, 3.0) if (m.get("peg") or 0) > 0 else None
    valid = [x for x in [rg_s, eps_s, eb_s, peg_s] if x is not None]
    n = max(len(valid), 1)
    return [
        _driver("revenue_growth", rg_s, round(m["revenue_growth"] * 100, 1) if m.get("revenue_growth") is not None else None, "%", _sub_contribution(rg_s, w, n), "growth"),
        _driver("eps_growth", eps_s, round(m["eps_growth"] * 100, 1) if m.get("eps_growth") is not None else None, "%", _sub_contribution(eps_s, w, n), "growth"),
        _driver("ebitda_growth", eb_s, round(m["ebitda_growth"] * 100, 1) if m.get("ebitda_growth") is not None else None, "%", _sub_contribution(eb_s, w, n), "growth"),
        _driver("peg", peg_s, safe_num(m.get("peg")), "x", _sub_contribution(peg_s, w, n), "growth"),
    ]


def _detail_balance(m, sg, w):
    th_nde = get_threshold(sg, "net_debt_ebitda")
    th_de = get_threshold(sg, "debt_equity")
    th_cr = get_threshold(sg, "current_ratio")
    th_az = get_threshold(sg, "altman_z")
    nde = m.get("net_debt_ebitda")
    nde_s = None
    if th_nde:
        nde_s = 100.0 if nde is not None and nde < 0 else score_lower(nde, *th_nde)
    de_s = score_lower(m.get("debt_equity"), *th_de) if th_de else None
    cr_s = score_higher(m.get("current_ratio"), *th_cr) if th_cr else None
    ic_s = score_higher(m.get("interest_coverage"), 1.5, 3.0, 6.0, 12.0)
    az_s = score_higher(m.get("altman_z"), *th_az) if th_az else None
    valid = [x for x in [nde_s, de_s, cr_s, ic_s, az_s] if x is not None]
    n = max(len(valid), 1)
    return [
        _driver("net_debt_ebitda", nde_s, safe_num(nde), "x", _sub_contribution(nde_s, w, n), "balance"),
        _driver("debt_equity", de_s, safe_num(m.get("debt_equity")), "%", _sub_contribution(de_s, w, n), "balance"),
        _driver("current_ratio", cr_s, safe_num(m.get("current_ratio")), "x", _sub_contribution(cr_s, w, n), "balance"),
        _driver("interest_cov", ic_s, safe_num(m.get("interest_coverage")), "x", _sub_contribution(ic_s, w, n), "balance"),
        _driver("altman_z", az_s, safe_num(m.get("altman_z")), "", _sub_contribution(az_s, w, n), "balance"),
    ]


def _detail_earnings(m, w):
    cfo_s = score_higher(m.get("cfo_to_ni"), 0.2, 0.6, 0.9, 1.2)
    fcf_s = score_higher(m.get("fcf_margin"), -0.02, 0, 0.05, 0.12)
    bm = m.get("beneish_m")
    bm_s = None
    if bm is not None:
        bm_s = 90.0 if bm < -2.22 else (65.0 if bm < -1.78 else 25.0)
    valid = [x for x in [cfo_s, fcf_s, bm_s] if x is not None]
    n = max(len(valid), 1)
    return [
        _driver("cfo_to_ni", cfo_s, safe_num(m.get("cfo_to_ni")), "x", _sub_contribution(cfo_s, w, n), "earnings"),
        _driver("fcf_margin", fcf_s, round(m["fcf_margin"] * 100, 1) if m.get("fcf_margin") is not None else None, "%", _sub_contribution(fcf_s, w, n), "earnings"),
        _driver("beneish", bm_s, safe_num(bm), "", _sub_contribution(bm_s, w, n), "earnings"),
    ]


_DETAIL_FNS = {
    "value":    lambda m, sg, w: _detail_value(m, sg, w),
    "quality":  lambda m, sg, w: _detail_quality(m, sg, w),
    "growth":   lambda m, sg, w: _detail_growth(m, sg, w),
    "balance":  lambda m, sg, w: _detail_balance(m, sg, w),
    "earnings": lambda m, sg, w: _detail_earnings(m, w),
}


# ================================================================
# DIMENSION BREAKDOWN
# ================================================================
def build_dimension_breakdown(scores, metrics, sector_group, scores_imputed):
    breakdown = {}
    for dim, weight in FA_WEIGHTS.items():
        dim_score = scores.get(dim, 50.0)
        imputed = dim in scores_imputed
        fn = _DETAIL_FNS.get(dim)
        subs = fn(metrics, sector_group, weight) if fn else []
        breakdown[dim] = {
            "score": dim_score, "weight": weight,
            "contribution": round(weight * (dim_score - 50.0), 2),
            "imputed": imputed, "sub_components": subs,
        }
    for dim, weight in IVME_WEIGHTS.items():
        dim_score = scores.get(dim, 50.0)
        breakdown[dim] = {
            "score": dim_score, "weight": weight,
            "contribution": round(weight * (dim_score - 50.0), 2),
            "imputed": False, "sub_components": [],
        }
    return breakdown


# ================================================================
# TOP DRIVERS — contribution-based, diversity-aware
# ================================================================
def extract_top_drivers(breakdown, risk_reasons, risk_penalty):
    all_pos, all_neg = [], []
    for dim_name, dim_data in breakdown.items():
        if dim_data.get("imputed"):
            all_neg.append({
                "name": _DIM_NAMES.get(dim_name, dim_name).title() + " verisi eksik",
                "key": dim_name + "_imputed", "dimension": dim_name,
                "direction": "negative", "strength": "negative",
                "explanation": _DIM_NAMES.get(dim_name, dim_name).title() + " boyutu veri eksikliginden tahmin edildi.",
                "score": 30.0, "contribution": round(dim_data["weight"] * -20, 2),
            })
            continue
        for sub in dim_data.get("sub_components", []):
            if sub.get("score") is None or sub["direction"] == "neutral":
                continue
            if sub["contribution"] > 0:
                all_pos.append(sub)
            elif sub["contribution"] < 0:
                all_neg.append(sub)
        if not dim_data.get("sub_components"):
            s = dim_data["score"]
            c = dim_data["contribution"]
            if c > 0:
                all_pos.append(_driver(dim_name, s, contribution=c, dimension=dim_name))
            elif c < 0:
                all_neg.append(_driver(dim_name, s, contribution=c, dimension=dim_name))
    if risk_penalty < 0:
        per_reason = round(risk_penalty * OVERALL_RISK_FACTOR / max(len(risk_reasons), 1), 2)
        for reason in risk_reasons[:3]:
            all_neg.append({
                "name": "Risk penaltisi", "key": "risk_penalty",
                "direction": "negative", "strength": "strong_negative",
                "explanation": reason, "contribution": per_reason,
            })
    all_pos.sort(key=lambda x: x.get("contribution", 0), reverse=True)
    all_neg.sort(key=lambda x: x.get("contribution", 0))

    def _diverse(items, limit):
        result, seen = [], {}
        for item in items:
            dim = item.get("dimension", item.get("key", ""))
            seen.setdefault(dim, 0)
            if seen[dim] < 2:
                result.append(item)
                seen[dim] += 1
            if len(result) >= limit:
                break
        return result

    return _diverse(all_pos, 5), _diverse(all_neg, 5)


# ================================================================
# DETERMINISTIC SUMMARY
# ================================================================
def build_summary(top_pos, top_neg, scores_imputed, overall):
    pos_parts, neg_parts = [], []
    _POS_MAP = [
        (["ucuz", "f/k", "deger"], "ucuz degerleme"),
        (["karlilik", "roe", "roic"], "guclu karlilik"),
        (["buyume", "gelir"], "guclu buyume"),
        (["nakit"], "guclu nakit akisi"),
        (["bilanco", "borc", "odeme"], "saglam bilanco"),
        (["momentum", "fiyat"], "guclu momentum"),
        (["marj"], "yuksek marjlar"),
        (["guvenilir", "muhasebe"], "guvenilir finansallar"),
    ]
    _NEG_MAP = [
        (["buyume", "gelir", "hisse basi"], "buyume zayif"),
        (["borc", "bilanco", "iflas"], "borcluluk riski var"),
        (["pahali", "deger", "fiyat"], "degerleme yuksek"),
        (["eksik"], "bazi veriler eksik"),
        (["momentum", "teknik"], "teknik gorunum zayif"),
        (["risk", "penalti"], "risk faktorleri mevcut"),
        (["nakit", "muhasebe"], "nakit akisi zayif"),
    ]

    def _match(name_lower, mapping, parts):
        for keywords, label in mapping:
            if any(kw in name_lower for kw in keywords):
                if label not in parts:
                    parts.append(label)
                return
        cleaned = name_lower.split("(")[0].strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)

    for d in top_pos[:2]:
        _match(d.get("name", "").lower(), _POS_MAP, pos_parts)
    for d in top_neg[:2]:
        _match(d.get("name", "").lower(), _NEG_MAP, neg_parts)

    pos_parts = pos_parts[:2]
    neg_parts = neg_parts[:2]

    if pos_parts and neg_parts:
        return pos_parts[0].capitalize() + (" ve " + pos_parts[1] if len(pos_parts) > 1 else "") + " sayesinde one cikiyor, ancak " + " ve ".join(neg_parts) + "."
    elif pos_parts:
        return " ve ".join(pos_parts).capitalize() + " sayesinde guclu bir profil ciziyor."
    elif neg_parts:
        return "Dikkat: " + " ve ".join(neg_parts) + "."
    else:
        return "Dengeli bir profil -- belirgin bir guclu veya zayif yon yok."


# ================================================================
# CONFIDENCE EXPLANATION
# ================================================================
def explain_confidence(confidence, metrics, scores_imputed):
    present = sum(1 for k in CONFIDENCE_KEYS if safe_num(metrics.get(k)) is not None)
    total = len(CONFIDENCE_KEYS)
    missing_keys = [k for k in CONFIDENCE_KEYS if safe_num(metrics.get(k)) is None]
    if confidence >= 80:
        level = "Veri kalitesi iyi -- skora guvenebilirsiniz."
    elif confidence >= 60:
        level = "Bazi veriler eksik ama skor genel olarak guvenilir."
    elif confidence >= 40:
        level = "Onemli veriler eksik -- skoru dikkatli degerlendirin."
    else:
        level = "Veri cok yetersiz -- skor guvenilir degil."
    parts = [str(present) + "/" + str(total) + " temel metrik mevcut.", level]
    if scores_imputed:
        dims = ", ".join(_DIM_NAMES.get(d, d) for d in scores_imputed)
        parts.append("Eksik boyutlar: " + dims + ".")
    return {
        "score": confidence, "metrics_present": present, "metrics_total": total,
        "missing_metrics": missing_keys[:5], "imputed_dimensions": scores_imputed,
        "summary": " ".join(parts),
    }


# ================================================================
# MISSING DATA IMPACT
# ================================================================
def explain_missing_data(scores_imputed, score_coverage):
    if not scores_imputed:
        return {"has_impact": False, "summary": "Tum boyutlar gercek veriye dayali.", "imputed_dimensions": []}
    total_weight = sum(FA_WEIGHTS.get(d, 0) for d in scores_imputed)
    dim_details = []
    for dim in scores_imputed:
        weight = FA_WEIGHTS.get(dim, 0)
        cov = score_coverage.get(dim, {})
        dim_details.append({
            "dimension": dim, "dimension_name": _DIM_NAMES.get(dim, dim),
            "weight": weight, "weight_pct": round(weight * 100, 1),
            "default_score": 50,
            "data_available": cov.get("available", 0), "data_total": cov.get("total", 0),
        })
    if len(scores_imputed) <= 2:
        summary = "Bazi onemli veriler eksik oldugu icin skorun bir kismi tahmine dayaniyor."
    else:
        summary = "Onemli veriler eksik -- skorun buyuk kismi tahmine dayaniyor. Dikkatli degerlendirin."
    return {
        "has_impact": True, "summary": summary,
        "total_weight_imputed_pct": round(total_weight * 100, 1),
        "imputed_dimensions": dim_details,
    }


# ================================================================
# OVERALL FORMULA
# ================================================================
def explain_overall_formula(fa_pure, ivme_score, value_score, risk_penalty, overall):
    momentum_effect = ivme_score * (fa_pure / 100.0)
    val_stretch = compute_valuation_stretch(value_score)
    capped_risk = max(risk_penalty, OVERALL_RISK_CAP)
    return {
        "formula": "FA*0.55 + Momentum(gated)*0.35 + ValStretch + Risk*0.3",
        "components": {
            "fa_pure": {"value": fa_pure, "weight": OVERALL_FA_WEIGHT, "contribution": round(fa_pure * OVERALL_FA_WEIGHT, 1)},
            "momentum_effect": {"value": round(momentum_effect, 1), "weight": OVERALL_MOMENTUM_WEIGHT,
                                "contribution": round(momentum_effect * OVERALL_MOMENTUM_WEIGHT, 1),
                                "note": "Ivme(" + str(round(ivme_score)) + ") x FA gate(" + str(round(fa_pure / 100, 2)) + ")"},
            "valuation_stretch": {"value": val_stretch},
            "risk_penalty": {"raw": risk_penalty, "capped": capped_risk,
                             "contribution": round(capped_risk * OVERALL_RISK_FACTOR, 1),
                             "note": "Cap: " + str(OVERALL_RISK_CAP) if risk_penalty < OVERALL_RISK_CAP else ""},
        },
        "result": overall,
    }


# ================================================================
# MAIN ENTRY POINT
# ================================================================
def build_explanation(analysis_result):
    scores = analysis_result["scores"]
    metrics = analysis_result["metrics"]
    sector_group = analysis_result.get("sector_group", "sanayi")
    scores_imputed = analysis_result.get("scores_imputed", [])
    score_coverage = analysis_result.get("score_coverage", {})
    risk_reasons = analysis_result.get("risk_reasons", [])
    risk_penalty = analysis_result.get("risk_penalty", 0)
    fa_pure = analysis_result.get("fa_score", 50)
    ivme_score = analysis_result.get("ivme", 50)
    overall = analysis_result.get("overall", 50)
    confidence = analysis_result.get("confidence", 50)
    breakdown = build_dimension_breakdown(scores, metrics, sector_group, scores_imputed)
    top_pos, top_neg = extract_top_drivers(breakdown, risk_reasons, risk_penalty)
    return {
        "summary": build_summary(top_pos, top_neg, scores_imputed, overall),
        "driver_breakdown": breakdown,
        "top_positive_drivers": top_pos,
        "top_negative_drivers": top_neg,
        "overall_formula": explain_overall_formula(fa_pure, ivme_score, scores.get("value", 50), risk_penalty, overall),
        "confidence_explanation": explain_confidence(confidence, metrics, scores_imputed),
        "missing_data_impact": explain_missing_data(scores_imputed, score_coverage),
    }

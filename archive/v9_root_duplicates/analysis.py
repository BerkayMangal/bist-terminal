# ================================================================
# BISTBULL TERMINAL V10.0 — ANALYSIS ENGINE
# compute_metrics, Piotroski, Altman, Beneish, analyze_symbol
# yfinance + borsapy unified data path.
# V9.1 birebir korunmuş + V10 applicability flags eklendi.
# ================================================================

from __future__ import annotations

import os
import logging
import datetime as dt
from typing import Optional, Any

import pandas as pd

from utils.helpers import safe_num, pick_row_pair, growth, base_ticker, first_valid
from core.cache import raw_cache, analysis_cache
from config import UNIVERSE
from engine.metrics import normalize_metrics, compute_score_coverage, confidence_penalty_for_imputed_scores
from engine.explainability import build_explanation

log = logging.getLogger("bistbull.analysis")

# ================================================================
# BORSAPY PROVIDER — tek veri kaynağı
# ================================================================
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
    """borsapy → gerçek KAP verisi. yfinance kaldırıldı."""
    cached = raw_cache.get(symbol)
    if cached is not None:
        return cached

    if BORSAPY_AVAILABLE and fetch_raw_v9 is not None:
        try:
            raw = fetch_raw_v9(symbol)
            log.debug(f"fetch_raw OK: {symbol} (source: borsapy)")
            return raw
        except Exception as e:
            log.warning(f"fetch_raw failed for {symbol}: {e}")
            raise

    raise RuntimeError(f"borsapy kullanılamıyor — {symbol} verisi alınamadı")


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
        _bm = -4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi
        if _bm < -10 or _bm > 10: return None
        return _bm
    except Exception:
        return None


# ================================================================
# COMPUTE METRICS — borsapy only
# ================================================================
def compute_metrics(symbol: str) -> dict:
    """borsapy → metric dict. yfinance kaldırıldı."""
    raw = fetch_raw(symbol)

    if BORSAPY_AVAILABLE and compute_metrics_v9 is not None:
        m = compute_metrics_v9(symbol)
        m["piotroski_f"] = compute_piotroski(m)
        m["altman_z"] = compute_altman(m)
        m["beneish_m"] = compute_beneish(m)
        return m

    raise RuntimeError(f"borsapy kullanılamıyor — {symbol} metrikleri hesaplanamadı")


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
    from engine.metric_guards import validate_metrics

    m = validate_metrics(normalize_metrics(compute_metrics(symbol)))
    sector_group = map_sector(m.get("sector", ""))

    tech = None
    try:
        tech = compute_technical(symbol)
    except Exception as e:
        log.debug(f"analyze_symbol tech for {symbol}: {e}")

    # 7 boyut FA — compute raw scores (may be None)
    _IMPUTE_PENALTY = 35
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
        k: round(v if v is not None else _IMPUTE_PENALTY, 1)
        for k, v in _raw_fa.items()
    }

    if scores_imputed:
        log.debug(
            f"{symbol}: {len(scores_imputed)} FA dimension(s) imputed to {_IMPUTE_PENALTY}: "
            f"{', '.join(scores_imputed)}"
        )

    from config import FA_WEIGHTS
    _active = {k: v for k, v in _raw_fa.items() if v is not None}
    if len(_active) >= 3:
        _wsum = sum(FA_WEIGHTS[k] for k in _active)
        fa_pure = round(max(1, min(99, sum((FA_WEIGHTS[k] / _wsum) * _active[k] for k in _active))), 1)
    else:
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
        "data_source": m.get("data_source", "unknown"),
        "data_fetched_at": raw_cache.get(symbol, {}).get("_fetched_at") if raw_cache.get(symbol) else None,
        "analyzed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "_metric_violations": m.get("_metric_violations", 0),
    }

    # Data Quality (never blocks)
    try:
        from engine.data_quality import assess_data_quality, build_decision_context
        r["data_health"] = assess_data_quality(m, scores_imputed)
        r["data_context"] = r["data_health"]
        r["decision_context"] = build_decision_context(r["data_health"], confidence, is_hype, scores_imputed)
    except Exception as e:
        log.debug(f"Data quality skipped for {symbol}: {e}")

    # Valuation Trust Layer (never blocks)
    try:
        from engine.valuation import build_valuation_layer
        r.update(build_valuation_layer(m, r))
    except Exception as e:
        log.debug(f"Valuation skipped for {symbol}: {e}")

    # Timing Intelligence (never blocks)
    try:
        from engine.timing_intel import build_timing_intel
        r.update(build_timing_intel(scores, tech, m))
    except Exception as e:
        log.debug(f"Timing intel skipped for {symbol}: {e}")

    # Dimension Explanations — plain-language per-dimension (never blocks)
    try:
        from engine.dimension_explainer import build_dimension_explanations
        r["dimension_explanations"] = build_dimension_explanations(scores, m)
    except Exception as e:
        log.debug(f"Dimension explainer skipped for {symbol}: {e}")

    # Turkey Context — inflation, profit quality, accounting risk (never blocks)
    try:
        from engine.turkey_context import build_turkey_context
        r["turkey_context"] = build_turkey_context(m, r)
    except Exception as e:
        log.debug(f"Turkey context skipped for {symbol}: {e}")

    # Delta — daily snapshot + 7d change (never blocks)
    try:
        from engine.delta import save_daily_snapshot, compute_delta
        save_daily_snapshot(symbol, r)
        dd = compute_delta(symbol, r)
        if dd: r.update(dd)
    except Exception as e:
        log.debug(f"Delta skipped for {symbol}: {e}")

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

    # V12: Turkey Realities — 4 makro filtre (never blocks)
    try:
        from engine.turkey_realities import compute_turkey_realities
        from config import STATIC_RATES
        _tcmb = next((s for s in STATIC_RATES if s["key"] == "TCMB"), None)
        _policy_rate = _tcmb["rate"] if _tcmb else 37.0

        tr_result = compute_turkey_realities(
            m=m, sector_group=sector_group,
            fa_pure=fa_pure, deger_score=deger_score,
            policy_rate=_policy_rate,
        )
        r["turkey_realities"] = tr_result

        if tr_result.get("adjusted_fa") is not None:
            r["tr_adjusted_fa"] = tr_result["adjusted_fa"]
        if tr_result.get("adjusted_deger") is not None:
            r["tr_adjusted_deger"] = tr_result["adjusted_deger"]

        for _fval in tr_result.get("filters", {}).values():
            if isinstance(_fval, dict) and _fval.get("grade") in ("D", "F"):
                r["risk_reasons"].append(f"\U0001f1f9\U0001f1f7 {_fval['explanation']}")
                r["negatives"].append(f"\U0001f1f9\U0001f1f7 {_fval['explanation']}")
    except Exception as e:
        log.debug(f"Turkey realities skipped for {symbol}: {e}")

    # V13: Academic Layer — Damodaran + Greenwald (never blocks)
    try:
        from engine.academic_layer import compute_academic_adjustments
        from config import STATIC_RATES as _SR2
        _tcmb2 = next((s for s in _SR2 if s["key"] == "TCMB"), None)
        _pr2 = _tcmb2["rate"] if _tcmb2 else 37.0

        _ac_fa = r.get("tr_adjusted_fa", fa_pure)
        _ac_deger = r.get("tr_adjusted_deger", deger_score)

        ac_result = compute_academic_adjustments(
            m=m, sector_group=sector_group,
            fa_input=_ac_fa, deger_input=_ac_deger,
            policy_rate=_pr2, inflation_rate=0.40,
        )
        r["academic"] = ac_result

        if ac_result.get("adjusted_fa") is not None:
            r["ac_adjusted_fa"] = ac_result["adjusted_fa"]
        if ac_result.get("adjusted_deger") is not None:
            r["ac_adjusted_deger"] = ac_result["adjusted_deger"]

        for _aval in ac_result.get("filters", {}).values():
            if isinstance(_aval, dict) and _aval.get("grade") in ("D", "F"):
                r["risk_reasons"].append(f"\U0001f393 {_aval['explanation']}")
                r["negatives"].append(f"\U0001f393 {_aval['explanation']}")
    except Exception as e:
        log.debug(f"Academic layer skipped for {symbol}: {e}")

    analysis_cache.set(symbol, r)
    return r

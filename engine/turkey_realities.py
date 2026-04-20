# ================================================================
# BISTBULL TERMINAL — TURKEY REALITIES LAYER
# engine/turkey_realities.py
#
# 4 filtre: Döviz Kalkanı, Faiz Direnci, Fiyat Geçişkenliği, TMS 29
# Türkiye'nin makro gerçeklerine göre FA skorunu ayarlar.
#
# Çarpan sistemi: 0.70 – 1.15 (geometrik ortalama)
# Mevcut scoring.py'ye DOKUNMAZ. analysis.py'den çağrılır.
# Saf fonksiyonlar. IO/Cache SIFIR. Crash ETMEZ.
# ================================================================

from __future__ import annotations
import math, logging
from typing import Any, Optional

log = logging.getLogger("bistbull.turkey_realities")


def _sf(v: Any, d: float = 0.0) -> float:
    if v is None: return d
    try:
        f = float(v)
        return d if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return d


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# Sektör döviz profilleri (ihracat/gelir tahmini)
_SECTOR_EXPORT = {
    "savunma": 0.65, "ulasim": 0.55, "sanayi": 0.35,
    "teknoloji": 0.40, "holding": 0.25, "enerji": 0.15,
    "perakende": 0.05, "banka": 0.10, "telekom": 0.08,
}

# TMS 29 sektör hassasiyeti (0=hiç, 1=çok)
_SECTOR_TMS29 = {
    "gayrimenkul": 0.90, "enerji": 0.85, "ulasim": 0.80,
    "sanayi": 0.70, "savunma": 0.65, "holding": 0.50,
    "perakende": 0.35, "banka": 0.25,
}

# Sektör normal net marjı (TMS 29 karşılaştırma)
_SECTOR_NORMAL_NM = {
    "enerji": 0.08, "ulasim": 0.10, "sanayi": 0.08,
    "perakende": 0.04, "banka": 0.20, "holding": 0.10,
    "savunma": 0.12, "gayrimenkul": 0.15,
}


# ================================================================
# FİLTRE 1: DÖVİZ KALKANI
# ================================================================
def _fx_shield(m: dict, sg: str, pr: float) -> dict:
    export_r = _sf(m.get("foreign_ratio")) or _SECTOR_EXPORT.get(sg, 0.20)
    nd = _sf(m.get("net_debt_ebitda"))
    rg = _sf(m.get("revenue_growth"))
    inflation = max(pr * 0.8, 0.25)  # proxy: %80 of policy rate

    # Döviz gelir puanı (0-40)
    fx_pts = 40 if export_r >= 0.60 else 30 if export_r >= 0.40 else 18 if export_r >= 0.20 else 10 if export_r >= 0.10 else 3

    # Borç yapısı (0-30)
    debt_pts = 30 if nd < 0 else 22 if nd <= 1.5 else 12 if nd <= 3.0 else 5 if nd <= 5.0 else 0

    # Reel büyüme (0-30)
    real_g = rg - inflation
    growth_pts = 30 if real_g >= 0.10 else 20 if real_g >= 0 else 10 if real_g >= -0.10 else 5 if rg >= 0 else 0

    score = _clamp(fx_pts + debt_pts + growth_pts, 0, 100)

    if score >= 75: mult, grade = 1.10, "A"
    elif score >= 55: mult, grade = 1.02, "B"
    elif score >= 35: mult, grade = 0.92, "C"
    elif score >= 20: mult, grade = 0.82, "D"
    else: mult, grade = 0.75, "F"

    detail = []
    if fx_pts >= 30: detail.append(f"Döviz geliri ~%{export_r*100:.0f}")
    elif fx_pts <= 10: detail.append(f"Döviz geliri düşük (~%{export_r*100:.0f}) — kur riski")
    if debt_pts <= 5 and nd > 0: detail.append(f"Yüksek borç (NB/FAVÖK {nd:.1f}x)")
    if growth_pts <= 5: detail.append(f"Reel küçülme (%{real_g*100:+.0f})")

    return {
        "name": "Döviz Kalkanı", "score": round(score, 1),
        "multiplier": mult, "grade": grade,
        "explanation": " | ".join(detail) if detail else "Döviz pozisyonu nötr",
        "components": {"export_ratio": round(export_r, 2), "real_growth": round(real_g, 3)},
    }


# ================================================================
# FİLTRE 2: FAİZ DİRENCİ
# ================================================================
def _rate_resistance(m: dict, sg: str, pr: float) -> dict:
    nd = _sf(m.get("net_debt_ebitda"))
    ic = _sf(m.get("interest_coverage"))
    fcf_m = _sf(m.get("fcf_margin"))
    de = _sf(m.get("debt_equity"))
    spread = 0.08  # kurumsal spread

    eff_rate = pr + spread
    eff_cost = nd * eff_rate if nd > 0 else 0

    # Efektif maliyet puanı (0-35)
    cost_pts = 35 if nd <= 0 else (35 if eff_cost < 0.30 else 25 if eff_cost < 0.50 else 15 if eff_cost < 0.80 else 8 if eff_cost < 1.0 else 0)

    # Faiz karşılama (0-30)
    ic_pts = 0 if ic <= 0 else (30 if ic >= 6 else 22 if ic >= 4 else 14 if ic >= 2.5 else 6 if ic >= 1.5 else 0)

    # FCF (0-20)
    fcf_pts = 20 if fcf_m >= 0.10 else 14 if fcf_m >= 0.05 else 8 if fcf_m >= 0.02 else 4 if fcf_m >= 0 else 0

    # Kaldıraç (0-15)
    lev_pts = 15 if de <= 0.3 else 12 if de <= 0.8 else 7 if de <= 1.5 else 3 if de <= 3 else 0

    score = _clamp(cost_pts + ic_pts + fcf_pts + lev_pts, 0, 100)

    if score >= 75: mult, grade = 1.08, "A"
    elif score >= 55: mult, grade = 1.00, "B"
    elif score >= 35: mult, grade = 0.88, "C"
    elif score >= 20: mult, grade = 0.78, "D"
    else: mult, grade = 0.70, "F"

    detail = []
    if nd > 0:
        detail.append(f"Efektif borç maliyeti: FAVÖK'ün %{eff_cost*100:.0f}'i (NB/FAVÖK {nd:.1f}x × %{eff_rate*100:.0f})")
    else:
        detail.append("Net nakit — faiz riski minimal")
    if 0 < ic < 3: detail.append(f"Faiz karşılama {ic:.1f}x — %{pr*100:.0f} faizde kritik")
    if fcf_m < 0: detail.append("Negatif FCF — borç çeviremez")

    return {
        "name": "Faiz Direnci", "score": round(score, 1),
        "multiplier": mult, "grade": grade,
        "explanation": " | ".join(detail) if detail else "Faiz direnci nötr",
        "components": {"effective_cost": round(eff_cost, 3), "policy_rate": round(pr, 4)},
    }


# ================================================================
# FİLTRE 3: FİYAT GEÇİŞKENLİĞİ
# ================================================================
def _pricing_power(m: dict, sg: str) -> dict:
    gm = _sf(m.get("gross_margin"))
    gm_prev = _sf(m.get("gross_margin_prev"))
    rg = _sf(m.get("revenue_growth"))
    eg = _sf(m.get("ebitda_growth"))
    nm = _sf(m.get("net_margin"))

    gm_pts = 30 if gm >= 0.40 else 22 if gm >= 0.25 else 14 if gm >= 0.15 else 7 if gm >= 0.08 else 2

    if gm > 0 and gm_prev > 0:
        mc = abs(gm - gm_prev)
        stab_pts = 30 if mc < 0.02 else 22 if mc < 0.04 else 14 if mc < 0.08 else 7 if mc < 0.12 else 0
        if gm > gm_prev: stab_pts = min(30, stab_pts + 5)
    else:
        stab_pts = 12

    if rg > 0 and eg > 0:
        ol_pts = 25 if eg >= rg * 1.2 else 18 if eg >= rg * 0.9 else 10 if eg >= rg * 0.5 else 3
    elif rg > 0 and eg <= 0:
        ol_pts = 0
    else:
        ol_pts = 10

    nm_pts = 15 if nm >= 0.12 else 10 if nm >= 0.06 else 5 if nm >= 0.02 else 2 if nm >= 0 else 0

    score = _clamp(gm_pts + stab_pts + ol_pts + nm_pts, 0, 100)

    if score >= 70: mult, grade = 1.08, "A"
    elif score >= 50: mult, grade = 1.00, "B"
    elif score >= 30: mult, grade = 0.90, "C"
    else: mult, grade = 0.82, "D"

    detail = []
    if gm >= 0.25: detail.append(f"Brüt marj %{gm*100:.0f} — fiyatlama gücü var")
    elif gm < 0.10: detail.append(f"Brüt marj %{gm*100:.0f} — baskıya açık")
    if rg > 0 and eg <= 0: detail.append("Gelir artıyor ama FAVÖK düşüyor — maliyetler yansıtılamıyor")

    return {
        "name": "Fiyat Geçişkenliği", "score": round(score, 1),
        "multiplier": mult, "grade": grade,
        "explanation": " | ".join(detail) if detail else "Fiyatlama gücü nötr",
        "components": {"gm": round(gm, 3), "gm_change": round(gm - gm_prev, 4) if gm_prev else None},
    }


# ================================================================
# FİLTRE 4: TMS 29 ENFLASYON MUHASEBESİ
# ================================================================
def _tms29_filter(m: dict, sg: str) -> dict:
    cfo_ni = _sf(m.get("cfo_to_ni"))
    ni = _sf(m.get("net_income"))
    nm = _sf(m.get("net_margin"))
    pe = _sf(m.get("pe"))
    rg = _sf(m.get("revenue_growth"))
    eg = _sf(m.get("ebitda_growth"))
    sens = _SECTOR_TMS29.get(sg, 0.50)
    normal_nm = _SECTOR_NORMAL_NM.get(sg, 0.08)

    if ni <= 0:
        cq_pts, cq_flag = 15, "zarar"
    elif cfo_ni >= 1.0:
        cq_pts, cq_flag = 35, "güvenilir"
    elif cfo_ni >= 0.7:
        cq_pts, cq_flag = 25, "kabul edilebilir"
    elif cfo_ni >= 0.4:
        cq_pts, cq_flag = 14, "şüpheli"
    elif cfo_ni >= 0.1:
        cq_pts, cq_flag = 6, "zayıf"
    else:
        cq_pts, cq_flag = 0, "alarm"

    if nm <= 0: margin_pts = 10
    elif nm <= normal_nm * 1.5: margin_pts = 25
    elif nm <= normal_nm * 2.5: margin_pts = 15
    elif nm <= normal_nm * 4: margin_pts = 7
    else: margin_pts = 0

    if rg > 0 and eg > 0:
        gap = rg - eg
        cons_pts = 20 if gap < 0.05 else 14 if gap < 0.15 else 7 if gap < 0.30 else 2
    else:
        cons_pts = 10

    sens_pts = round(20 * (1 - sens))

    score = _clamp(cq_pts + margin_pts + cons_pts + sens_pts, 0, 100)

    # Tahmini gerçek F/K
    est_pe = None
    if pe > 0 and 0 < cfo_ni < 0.8 and ni > 0:
        est_pe = round(pe / max(cfo_ni, 0.2), 1)

    if score >= 70: mult, grade = 1.02, "A"
    elif score >= 50: mult, grade = 0.98, "B"
    elif score >= 30: mult, grade = 0.88, "C"
    else: mult, grade = 0.78, "D"

    detail = []
    if cq_flag in ("zayıf", "alarm"):
        detail.append(f"Nakit kalitesi {cq_flag} (CFO/NI: {cfo_ni:.2f})")
    if nm > normal_nm * 2.5 and ni > 0:
        detail.append(f"Net marj %{nm*100:.0f}, sektör ort %{normal_nm*100:.0f} — TMS 29 etkisi olabilir")
    if est_pe and est_pe > pe * 1.4:
        detail.append(f"Raporlanan F/K {pe:.1f}, nakit bazlı tahmin ~{est_pe:.0f}")
    if sens >= 0.70:
        detail.append("Sektör TMS 29'a yüksek hassasiyetli")

    return {
        "name": "TMS 29 Filtresi", "score": round(score, 1),
        "multiplier": mult, "grade": grade,
        "explanation": " | ".join(detail) if detail else "Kâr kalitesi normal görünüyor",
        "components": {"cfo_ni": round(cfo_ni, 3), "cash_flag": cq_flag, "estimated_pe": est_pe, "sensitivity": sens},
    }


# ================================================================
# ANA FONKSİYON
# ================================================================
def compute_turkey_realities(
    m: dict,
    sector_group: str = "sanayi",
    fa_pure: Optional[float] = None,
    deger_score: Optional[float] = None,
    policy_rate: float = 37.0,
) -> dict:
    """
    4 Turkey Reality filtresini hesapla.
    policy_rate: TCMB faizi (%, ör: 37.0 → 0.37 olarak kullanılır)
    Returns: dict with filters, composite_multiplier, adjusted scores, summary.
    """
    try:
        pr = policy_rate / 100.0 if policy_rate > 1 else policy_rate

        fx = _fx_shield(m, sector_group, pr)
        rate = _rate_resistance(m, sector_group, pr)
        pricing = _pricing_power(m, sector_group)
        tms = _tms29_filter(m, sector_group)

        filters = {"fx_shield": fx, "rate_resistance": rate, "pricing_power": pricing, "tms29": tms}

        # Geometrik ortalama
        mults = [fx["multiplier"], rate["multiplier"], pricing["multiplier"], tms["multiplier"]]
        product = 1.0
        for mult in mults:
            product *= mult
        composite_mult = round(product ** 0.25, 4)

        # Composite grade
        gm = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
        avg_g = sum(gm.get(f["grade"], 2) for f in filters.values()) / 4
        if avg_g >= 3.5: cg = "A"
        elif avg_g >= 2.5: cg = "B"
        elif avg_g >= 1.5: cg = "C"
        elif avg_g >= 0.5: cg = "D"
        else: cg = "F"

        adj_fa = round(fa_pure * composite_mult, 1) if fa_pure is not None else None
        adj_deger = round(_clamp(deger_score * composite_mult, 1, 99), 1) if deger_score is not None else None

        weak = [f for f in filters.values() if f["grade"] in ("D", "F")]
        strong = [f for f in filters.values() if f["grade"] == "A"]

        if cg in ("A", "B"):
            summary = f"Türkiye filtresi olumlu ({cg}). "
            if strong: summary += f"{', '.join(f['name'] for f in strong)} güçlü."
        elif cg == "C":
            summary = "Türkiye filtresi orta. "
            if weak: summary += f"Dikkat: {', '.join(f['name'] for f in weak)} zayıf."
        else:
            summary = f"Türkiye filtresi olumsuz ({cg}). "
            if weak: summary += f"{', '.join(f['name'] for f in weak)} kritik."

        return {
            "composite_multiplier": composite_mult,
            "composite_grade": cg,
            "filters": filters,
            "adjusted_fa": adj_fa,
            "adjusted_deger": adj_deger,
            "summary": summary,
        }

    except Exception as e:
        log.warning(f"turkey_realities error: {e}")
        return {
            "composite_multiplier": 1.0, "composite_grade": "?",
            "filters": {}, "adjusted_fa": fa_pure, "adjusted_deger": deger_score,
            "summary": "Türkiye filtresi hesaplanamadı.",
        }

# ================================================================
# BISTBULL TERMINAL — ACADEMIC LAYER
# engine/academic_layer.py
#
# 3 Akademik Filtre (Damodaran + Greenwald):
#   1. Değer Yaratımı  (ROE vs Cost of Equity)
#   2. Büyüme Tuzağı   (Growth Trap — FCF quality)
#   3. Yaşam Döngüsü   (Capital Allocation)
#
# turkey_realities.py'den SONRA çağrılır.
# Mevcut scoring.py'ye DOKUNMAZ.
# Saf fonksiyonlar. IO/Cache SIFIR.
# ================================================================

from __future__ import annotations
import math, logging
from typing import Any, Optional

log = logging.getLogger("bistbull.academic")

ERP = 0.08  # Equity Risk Premium (Türkiye CDS bazlı ~800bps)

_MATURE_SECTORS = {"enerji", "perakende", "ulasim", "banka", "gayrimenkul", "telekom"}
_GROWTH_SECTORS = {"savunma", "teknoloji"}


def _sf(v: Any, d: float = 0.0) -> float:
    if v is None: return d
    try:
        f = float(v)
        return d if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return d


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ================================================================
# 1. DEĞER YARATIMI (ROE vs Cost of Equity)
# ================================================================
def _value_creation(m: dict, sg: str, pr: float) -> dict:
    roe = _sf(m.get("roe"))
    roic = _sf(m.get("roic"))
    de = _sf(m.get("debt_equity"))
    pb = _sf(m.get("pb"))
    beta = _clamp(_sf(m.get("beta"), 1.0), 0.6, 2.0)

    ke = pr + beta * ERP
    excess = roe - ke

    if roe <= 0:
        score, penalty, grade = 5, -10, "F"
        label = "ZARAR"
        expl = "Şirket zarar ediyor — sermaye erimekte."
    elif roe < pr * 0.5:
        score, penalty, grade = 12, -10, "F"
        label = "AĞIR DEĞER YIKIMI"
        expl = (f"ROE %{roe*100:.0f}, mevduat faizinin (%{pr*100:.0f}) yarısı bile değil. "
                f"Hissedar bankada {pr/max(roe,0.01):.1f}x daha çok kazanırdı.")
    elif roe < pr:
        spread = (pr - roe) * 100
        score, penalty, grade = 25, -7, "D"
        label = "DEĞER YIKIMI"
        expl = f"ROE %{roe*100:.0f} < mevduat %{pr*100:.0f} (fark: %{spread:.0f}). Alternatif getiriye göre değer yıkıyor."
    elif roe < ke:
        score, penalty, grade = 45, -3, "C"
        label = "YETERSİZ GETİRİ"
        expl = f"ROE %{roe*100:.0f} mevduatı geçiyor ama risk primi karşılanmıyor (Ke: %{ke*100:.0f})."
    elif roe < ke + 0.05:
        score, penalty, grade = 65, 0, "B"
        label = "SINIRDA"
        expl = f"ROE %{roe*100:.0f} ≈ Ke %{ke*100:.0f}. Sermaye maliyetini kıl payı karşılıyor."
    else:
        score, penalty, grade = 88, 3, "A"
        label = "DEĞER YARATIMI"
        expl = f"ROE %{roe*100:.0f} > Ke %{ke*100:.0f}. Excess return %{excess*100:+.0f}. Gerçek değer yaratıcısı."

    # ROIC cross-check
    if de > 2.0 and roe > pr and 0 < roic < pr * 0.5:
        expl += f" ⚠️ Ancak D/E {de:.1f}x, ROIC %{roic*100:.0f} — ROE kaldıraçla şişirilmiş olabilir."
        score = max(score - 12, 15)
        if grade == "A": grade = "B"

    return {
        "name": "Değer Yaratımı", "score": round(score, 1), "penalty": penalty,
        "grade": grade, "label": label, "explanation": expl,
        "components": {"roe": round(roe, 4), "ke": round(ke, 4), "excess_return": round(excess, 4), "roic": round(roic, 4)},
    }


# ================================================================
# 2. BÜYÜME TUZAĞI (Growth Trap)
# ================================================================
def _growth_trap(m: dict, sg: str, inflation: float) -> dict:
    rg = _sf(m.get("revenue_growth"))
    fcf_m = _sf(m.get("fcf_margin"))
    cfo_ni = _sf(m.get("cfo_to_ni"))
    ni = _sf(m.get("net_income"))
    ocf = _sf(m.get("operating_cf"))
    fcf = _sf(m.get("free_cf"))
    rev = _sf(m.get("revenue"))
    rev_prev = _sf(m.get("revenue_prev"))
    ca = _sf(m.get("current_assets"))
    ca_prev = _sf(m.get("current_assets_prev"))
    cl = _sf(m.get("current_liabilities"))
    cl_prev = _sf(m.get("current_liabilities_prev"))

    real_g = rg - inflation
    capex_r = abs(ocf - fcf) / rev if rev > 0 and ocf != 0 else 0

    wc = ca - cl if ca > 0 and cl > 0 else 0
    wc_prev = ca_prev - cl_prev if ca_prev > 0 and cl_prev > 0 else 0
    wc_rev = wc / rev if rev > 0 else 0
    wc_rev_prev = wc_prev / rev_prev if rev_prev > 0 else 0
    wc_delta = wc_rev - wc_rev_prev

    # Tuzak sinyalleri
    sigs = 0
    reasons = []
    if real_g > 0.10 and fcf_m < 0:
        sigs += 2; reasons.append(f"Reel %{real_g*100:+.0f} büyüme ama FCF negatif — nakit yutıyor")
    if wc_delta > 0.05 and real_g > 0.05:
        sigs += 2; reasons.append(f"İşletme sermayesi yoğunluğu %{wc_delta*100:.1f}pp arttı")
    if real_g > 0.10 and 0 < cfo_ni < 0.5 and ni > 0:
        sigs += 1; reasons.append(f"CFO/NI {cfo_ni:.2f} — kâr nakde dönmüyor")
    if real_g > 0.05 and capex_r > 0.15:
        sigs += 1; reasons.append(f"CAPEX/Gelir %{capex_r*100:.0f}")

    is_trap = sigs >= 3

    if real_g > 0.10 and fcf_m >= 0.05:
        score, grade, label = 88, "A", "NAKİT YARATAN BÜYÜME"
        expl = f"Reel %{real_g*100:+.0f} büyüme + FCF pozitif (%{fcf_m*100:.1f}). Greenwald'un ideali."
    elif is_trap:
        score, grade, label = 15, "F", "BÜYÜME TUZAĞI"
        expl = f"Tuzak: {'; '.join(reasons[:2])}. Dış finansmana bağımlı, riskli."
    elif real_g > 0 and fcf_m >= 0:
        score, grade, label = 65, "B", "DENGELİ BÜYÜME"
        expl = f"Reel %{real_g*100:+.0f} büyüme, FCF pozitif."
    elif real_g <= 0 and fcf_m >= 0.05:
        score, grade, label = 55, "B", "NAKİT HASADI"
        expl = f"Reel küçülme (%{real_g*100:+.0f}) ama güçlü FCF. Harvest modu."
    elif real_g <= -0.10:
        score, grade, label = 18, "D", "REEL KÜÇÜLME"
        expl = f"Reel %{real_g*100:+.0f} küçülme (nominal %{rg*100:.0f} - enflasyon %{inflation*100:.0f}). Nominal büyüme yanıltıcı."
    else:
        score, grade, label = 42, "C", "BELİRSİZ"
        expl = f"Reel büyüme %{real_g*100:+.0f}, FCF marj %{fcf_m*100:.1f}."

    penalty = -5 if is_trap else (3 if grade == "A" else (-3 if grade in ("D", "F") else 0))

    return {
        "name": "Büyüme Analizi", "score": round(score, 1), "penalty": penalty,
        "grade": grade, "label": label, "explanation": expl,
        "is_trap": is_trap,
        "components": {"real_growth_pct": round(real_g * 100, 1), "fcf_margin": round(fcf_m, 4),
                        "wc_delta": round(wc_delta, 4), "trap_signals": sigs, "capex_ratio": round(capex_r, 3)},
    }


# ================================================================
# 3. YAŞAM DÖNGÜSÜ (Life Cycle & Capital Allocation)
# ================================================================
def _life_cycle(m: dict, sg: str, inflation: float, pr: float) -> dict:
    rg = _sf(m.get("revenue_growth"))
    fcf_m = _sf(m.get("fcf_margin"))
    div_y = _sf(m.get("dividend_yield"))
    roic = _sf(m.get("roic"))
    gm = _sf(m.get("gross_margin"))
    gm_prev = _sf(m.get("gross_margin_prev"))
    nm = _sf(m.get("net_margin"))
    sc = _sf(m.get("share_change"))
    ocf = _sf(m.get("operating_cf"))
    fcf = _sf(m.get("free_cf"))
    rev = _sf(m.get("revenue"))

    real_g = rg - inflation
    capex_r = abs(ocf - fcf) / rev if rev > 0 and ocf != 0 else 0
    total_ret = div_y + (abs(sc) if sc < 0 else 0)

    # Faz tespiti
    g_s, m_s, d_s = 0, 0, 0
    if real_g > 0.15: g_s += 2
    elif real_g > 0.05: g_s += 1
    if capex_r > 0.12: g_s += 1
    if div_y < 0.01: g_s += 1
    if sg in _GROWTH_SECTORS: g_s += 1

    if -0.05 <= real_g <= 0.05: m_s += 2
    if fcf_m > 0.05: m_s += 1
    if gm > 0 and gm_prev > 0 and abs(gm - gm_prev) < 0.03: m_s += 1
    if sg in _MATURE_SECTORS: m_s += 1

    if real_g < -0.10: d_s += 2
    elif real_g < -0.03: d_s += 1
    if gm > 0 and gm_prev > 0 and gm < gm_prev - 0.05: d_s += 1
    if nm < 0: d_s += 2

    if d_s >= 3: phase = "DÜŞÜŞ"
    elif g_s >= 3 and g_s > m_s: phase = "GENÇ BÜYÜME"
    elif m_s >= 3: phase = "OLGUN"
    else: phase = "GEÇİŞ"

    # Sermaye tahsisi
    if phase == "GENÇ BÜYÜME":
        if roic > pr * 0.5 and real_g > 0.10:
            cap_g, cap_n = "A", "Yüksek ROIC'le büyümeye yatırım — doğru strateji"
        elif roic > pr * 0.3:
            cap_g, cap_n = "B", "Büyüyor, ROIC kabul edilebilir"
        else:
            cap_g, cap_n = "C", "Büyüyor ama ROIC düşük"
    elif phase == "OLGUN":
        if total_ret >= 0.05:
            cap_g, cap_n = "A", f"Hissedara %{total_ret*100:.1f} dönüş — olgun için doğru"
        elif total_ret >= 0.02:
            cap_g, cap_n = "C", f"Kısmen dağıtıyor, mevduat %{pr*100:.0f} — yetersiz"
        elif fcf_m > 0.05:
            cap_g, cap_n = "D", f"FCF %{fcf_m*100:.0f} ama dağıtmıyor — mevduat %{pr*100:.0f} verirken nakit erir"
        else:
            cap_g, cap_n = "C", "Sermaye politikası belirsiz"
    elif phase == "DÜŞÜŞ":
        if total_ret >= 0.03:
            cap_g, cap_n = "B", "Küçülse de nakdi dağıtıyor"
        elif capex_r > 0.10:
            cap_g, cap_n = "D", "Küçülürken ağır yatırım — israf riski"
        else:
            cap_g, cap_n = "D", "Ne büyüyor ne dağıtıyor — sermaye hapiste"
    else:
        cap_g, cap_n = "C", "Geçiş fazı"

    gp = {"A": 85, "B": 65, "C": 45, "D": 25, "F": 10}
    score = gp.get(cap_g, 45)

    if phase == "OLGUN" and cap_g in ("D", "F"): penalty = -5
    elif cap_g == "A": penalty = 2
    elif cap_g in ("D", "F"): penalty = -3
    else: penalty = 0

    return {
        "name": "Yaşam Döngüsü", "score": round(score, 1), "penalty": penalty,
        "grade": cap_g, "label": f"{phase} — Sermaye: {cap_g}",
        "explanation": cap_n, "phase": phase,
        "components": {"phase": phase, "cap_grade": cap_g, "real_growth_pct": round(real_g * 100, 1),
                        "total_return": round(total_ret, 4), "g_sig": g_s, "m_sig": m_s, "d_sig": d_s},
    }


# ================================================================
# ANA FONKSİYON
# ================================================================
def compute_academic_adjustments(
    m: dict,
    sector_group: str = "sanayi",
    fa_input: Optional[float] = None,
    deger_input: Optional[float] = None,
    policy_rate: float = 37.0,
    inflation_rate: float = 0.40,
) -> dict:
    """
    3 Akademik Filtreyi çalıştır.
    fa_input/deger_input: turkey_adjusted veya ham skorlar.
    policy_rate: TCMB (%, ör: 37.0)
    Returns: dict with filters, composite, adjusted scores.
    """
    try:
        pr = policy_rate / 100.0 if policy_rate > 1 else policy_rate

        vc = _value_creation(m, sector_group, pr)
        gt = _growth_trap(m, sector_group, inflation_rate)
        lc = _life_cycle(m, sector_group, inflation_rate, pr)

        filters = {"value_creation": vc, "growth_trap": gt, "life_cycle": lc}

        W = {"value_creation": 0.45, "growth_trap": 0.30, "life_cycle": 0.25}
        composite = sum(filters[k]["score"] * W[k] for k in W)
        total_penalty = sum(f["penalty"] for f in filters.values())
        total_penalty = max(-15, min(6, total_penalty))

        gm = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0, "?": 2}
        avg_g = sum(gm.get(f["grade"], 2) for f in filters.values()) / 3
        if avg_g >= 3.5: cg = "A"
        elif avg_g >= 2.5: cg = "B"
        elif avg_g >= 1.5: cg = "C"
        elif avg_g >= 0.5: cg = "D"
        else: cg = "F"

        adj_fa = round(_clamp(fa_input + total_penalty, 1, 99), 1) if fa_input is not None else None
        adj_deger = round(_clamp(deger_input + total_penalty, 1, 99), 1) if deger_input is not None else None

        weak = [f for f in filters.values() if f["grade"] in ("D", "F")]
        strong = [f for f in filters.values() if f["grade"] == "A"]
        if cg in ("A", "B"):
            summary = f"Akademik filtre olumlu ({cg}). "
            if strong: summary += f"{', '.join(f['name'] for f in strong)} güçlü."
        elif cg == "C":
            summary = "Akademik filtre orta. "
            if weak: summary += f"Dikkat: {', '.join(f['name'] for f in weak)} zayıf."
        else:
            summary = f"Akademik filtre olumsuz ({cg}). "
            if weak: summary += f"{', '.join(f['name'] for f in weak)} kritik."

        return {
            "composite_score": round(composite, 1), "composite_penalty": total_penalty,
            "composite_grade": cg, "filters": filters,
            "adjusted_fa": adj_fa, "adjusted_deger": adj_deger,
            "summary": summary,
        }

    except Exception as e:
        log.warning(f"academic error: {e}")
        return {
            "composite_score": 50, "composite_penalty": 0, "composite_grade": "?",
            "filters": {}, "adjusted_fa": fa_input, "adjusted_deger": deger_input,
            "summary": "Akademik katman hesaplanamadı.",
        }

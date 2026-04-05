# ================================================================
# BISTBULL TERMINAL V10.0 — SCORING ENGINE
# 7-boyut FA + 3-boyut İvme + Risk + Label + Decision
# Saf fonksiyonlar. Cache/IO SIFIR.
# V9.1 birebir korunmuş — sadece import path'ler güncellendi.
# ================================================================

from __future__ import annotations

from typing import Any, Optional

from config import (
    SECTOR_THRESHOLDS, DEFAULT_THRESHOLDS, SECTOR_KEYWORDS, SECTOR_DEFAULT,
    CONFIDENCE_KEYS,
    PENALTY_ND_EBITDA_DEFAULT, PENALTY_ND_EBITDA_HIGH_DEBT, HIGH_DEBT_SECTORS,
    PENALTY_DILUTION, PENALTY_BENEISH,
    PENALTY_NEGATIVE_EQUITY, PENALTY_NET_LOSS, PENALTY_NEGATIVE_CFO,
    PENALTY_FAKE_PROFIT, PENALTY_LOW_CASH_QUALITY,
    BONUS_NET_CASH, NET_CASH_THRESHOLD_MULTIPLIER,
    INT_COV_PENALTIES,
    FA_WEIGHTS, IVME_WEIGHTS,
    OVERALL_FA_WEIGHT, OVERALL_MOMENTUM_WEIGHT, OVERALL_RISK_CAP, OVERALL_RISK_FACTOR,
    VAL_STRETCH_MAP,
    HYPE_STRICT_PCT, HYPE_STRICT_VOL, HYPE_STRICT_FA,
    HYPE_SOFT_PCT, HYPE_SOFT_VOL, HYPE_SOFT_FA,
)
from utils.helpers import safe_num, avg, score_higher, score_lower, fmt_num, fmt_pct


# ================================================================
# SEKTÖR MAPPING
# ================================================================
def map_sector(sector_str: str) -> str:
    """yfinance sector string → bizim sektör grubu."""
    s = (sector_str or "").lower()
    for group, keywords in SECTOR_KEYWORDS.items():
        if any(k in s for k in keywords):
            return group
    return SECTOR_DEFAULT


def get_threshold(
    sector_group: str,
    metric_key: str,
    default: Optional[tuple] = None,
) -> Optional[tuple]:
    """Sektör override varsa döndür, yoksa default eşik."""
    grp = SECTOR_THRESHOLDS.get(sector_group, {})
    if metric_key in grp:
        return grp[metric_key]
    if default is not None:
        return default
    return DEFAULT_THRESHOLDS.get(metric_key)


# ================================================================
# KADEMELİ RİSK PENALTI
# ================================================================
def _graduated_penalty(value: Optional[float], thresholds: list[tuple[float, int]]) -> int:
    """Kademeli penalti: [(eşik, penalti), ...] — ilk eşleşen uygulanır."""
    if value is None:
        return 0
    for threshold, penalty in thresholds:
        if value >= threshold:
            return penalty
    return 0


def compute_risk_penalties(
    m: dict,
    sector_group: Optional[str] = None,
) -> tuple[int, list[str]]:
    """Kademeli risk penaltileri hesapla. Returns: (total_penalty, reasons)"""
    total = 0
    reasons: list[str] = []

    # Negatif özsermaye
    if m.get("equity") is not None and m["equity"] < 0:
        total += PENALTY_NEGATIVE_EQUITY
        reasons.append(f"Negatif özsermaye ({PENALTY_NEGATIVE_EQUITY})")

    # Net zarar
    if m.get("net_income") is not None and m["net_income"] < 0:
        total += PENALTY_NET_LOSS
        reasons.append(f"Net zarar ({PENALTY_NET_LOSS})")

    # Negatif nakit akış
    if m.get("operating_cf") is not None and m["operating_cf"] < 0:
        total += PENALTY_NEGATIVE_CFO
        reasons.append(f"Negatif nakit akışı ({PENALTY_NEGATIVE_CFO})")

    # NB/FAVÖK kademeli — sektör-bazlı
    nd = m.get("net_debt_ebitda")
    if nd is not None and nd > 0:
        nd_thresholds = (
            PENALTY_ND_EBITDA_HIGH_DEBT
            if sector_group in HIGH_DEBT_SECTORS
            else PENALTY_ND_EBITDA_DEFAULT
        )
        p = _graduated_penalty(nd, nd_thresholds)
        if p:
            total += p
            reasons.append(f"Yüksek NB/FAVÖK {nd:.1f}x ({p:+d})")

    # Faiz karşılama (düşük = kötü)
    ic = m.get("interest_coverage")
    if ic is not None and ic < 3.0:
        p = 0
        for threshold, penalty in INT_COV_PENALTIES:
            if ic < threshold:
                p = penalty
                break
        if p:
            total += p
            reasons.append(f"Düşük faiz karşılama {ic:.1f}x ({p:+d})")

    # Beneish kademeli
    bm = m.get("beneish_m")
    if bm is not None and bm > -2.22:
        p = _graduated_penalty(bm, PENALTY_BENEISH)
        if p:
            total += p
            reasons.append(f"Beneish risk {bm:.2f} ({p:+d})")

    # Hisse seyreltme
    sc = m.get("share_change")
    if sc is not None and sc > 0.02:
        p = _graduated_penalty(sc, PENALTY_DILUTION)
        if p:
            total += p
            reasons.append(f"Hisse seyreltme %{sc * 100:.0f} ({p:+d})")

    # Bonus: net nakit
    if m.get("total_debt") is not None and m.get("cash") is not None:
        if m["cash"] > (m["total_debt"] or 0) * NET_CASH_THRESHOLD_MULTIPLIER:
            total += BONUS_NET_CASH
            reasons.append(f"Güçlü net nakit pozisyonu (+{BONUS_NET_CASH})")

    return total, reasons


# ================================================================
# 7 BOYUT SKORLAMA FONKSİYONLARI
# ================================================================
def score_value(m: dict, sector_group: Optional[str] = None) -> Optional[float]:
    th_pe = get_threshold(sector_group, "pe")
    th_pb = get_threshold(sector_group, "pb")
    th_ev = get_threshold(sector_group, "ev_ebitda")

    ev_sales = None
    mc = m.get("market_cap")
    td = m.get("total_debt")
    cash = m.get("cash")
    rev = m.get("revenue")
    if mc and td is not None and cash is not None and rev and rev > 0:
        ev = mc + (td or 0) - (cash or 0)
        ev_sales = ev / rev

    mos_raw = score_higher(m.get("margin_safety"), -0.2, 0, 0.15, 0.30)
    mos_capped = min(mos_raw, 70) if mos_raw is not None else None

    parts = [
        score_lower(m.get("pe"), *th_pe) if th_pe and (m.get("pe") or 0) > 0 else None,
        score_lower(m.get("pb"), *th_pb) if th_pb and (m.get("pb") or 0) > 0 else None,
        score_lower(m.get("ev_ebitda"), *th_ev) if th_ev and (m.get("ev_ebitda") or 0) > 0 else None,
        score_lower(ev_sales, 0.5, 1.2, 2.5, 5.0) if ev_sales is not None and ev_sales > 0 else None,
        score_higher(m.get("fcf_yield"), 0, 0.02, 0.05, 0.08),
        mos_capped,
    ]
    return avg(parts)


def score_quality(m: dict, sector_group: Optional[str] = None) -> Optional[float]:
    th_roe = get_threshold(sector_group, "roe")
    th_roic = get_threshold(sector_group, "roic")
    th_nm = get_threshold(sector_group, "net_margin")
    return avg([
        score_higher(m.get("roe"), *th_roe) if th_roe else None,
        score_higher(m.get("roic"), *th_roic) if th_roic else None,
        score_higher(m.get("net_margin"), *th_nm) if th_nm else None,
    ])


def score_growth(m: dict, sector_group: Optional[str] = None) -> Optional[float]:
    th_rg = get_threshold(sector_group, "revenue_growth")
    return avg([
        score_higher(m.get("revenue_growth"), *th_rg) if th_rg else None,
        score_higher(m.get("eps_growth"), -0.10, 0.05, 0.15, 0.30),
        score_higher(m.get("ebitda_growth"), -0.05, 0.05, 0.12, 0.25),
        score_lower(m.get("peg"), 0.5, 1.0, 1.8, 3.0) if (m.get("peg") or 0) > 0 else None,
    ])


def score_balance(m: dict, sector_group: Optional[str] = None) -> Optional[float]:
    th_nde = get_threshold(sector_group, "net_debt_ebitda")
    th_de = get_threshold(sector_group, "debt_equity")
    th_cr = get_threshold(sector_group, "current_ratio")
    th_az = get_threshold(sector_group, "altman_z")

    nde = m.get("net_debt_ebitda")
    nde_s = None
    if th_nde:
        nde_s = 100.0 if nde is not None and nde < 0 else score_lower(nde, *th_nde)

    return avg([
        nde_s,
        score_lower(m.get("debt_equity"), *th_de) if th_de else None,
        score_higher(m.get("current_ratio"), *th_cr) if th_cr else None,
        score_higher(m.get("interest_coverage"), 1.5, 3.0, 6.0, 12.0),
        score_higher(m.get("altman_z"), *th_az) if th_az else None,
    ])


def score_earnings(m: dict) -> Optional[float]:
    bm = m.get("beneish_m")
    bm_s = None
    if bm is not None:
        bm_s = 90 if bm < -2.22 else (65 if bm < -1.78 else 25)
    return avg([
        score_higher(m.get("cfo_to_ni"), 0.2, 0.6, 0.9, 1.2),
        score_higher(m.get("fcf_margin"), -0.02, 0, 0.05, 0.12),
        bm_s,
    ])


def score_moat(m: dict) -> Optional[float]:
    gm_stab = None
    if m.get("gross_margin") is not None and m.get("gross_margin_prev") is not None:
        gm_stab = score_lower(abs(m["gross_margin"] - m["gross_margin_prev"]), 0, 0.02, 0.06, 0.12)
    roa_stab = None
    if m.get("roa") is not None and m.get("roa_prev") is not None:
        roa_stab = score_lower(abs(m["roa"] - m["roa_prev"]), 0, 0.02, 0.05, 0.10)
    pricing = score_higher(m.get("gross_margin"), 0.08, 0.15, 0.25, 0.40) if m.get("gross_margin") else None
    op_power = score_higher(m.get("operating_margin"), 0.02, 0.06, 0.12, 0.20) if m.get("operating_margin") else None
    at_trend = None
    if m.get("asset_turnover") is not None and m.get("asset_turnover_prev") is not None:
        delta_at = m["asset_turnover"] - m["asset_turnover_prev"]
        if abs(delta_at) < 0.02:
            at_trend = 55
        elif delta_at >= 0:
            at_trend = 75
        else:
            at_trend = 35
    return avg([gm_stab, roa_stab, pricing, op_power, at_trend])


def score_capital(m: dict) -> Optional[float]:
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
    roic_quality = None
    roic = m.get("roic")
    if roic is not None:
        roic_quality = score_higher(roic, 0.02, 0.08, 0.14, 0.22)
    cf_consistency = None
    if m.get("operating_cf") is not None and m.get("net_income") is not None:
        if m["operating_cf"] > 0 and m["net_income"] > 0:
            ratio = m["operating_cf"] / m["net_income"]
            cf_consistency = score_higher(ratio, 0.5, 0.8, 1.0, 1.3)
        elif m["operating_cf"] > 0:
            cf_consistency = 60
        else:
            cf_consistency = 15
    return avg([
        score_higher(m.get("dividend_yield"), 0, 0.01, 0.03, 0.06),
        score_higher(m.get("fcf_yield"), 0, 0.02, 0.05, 0.08),
        capex_rev, dil, roic_quality, cf_consistency,
    ])


# ================================================================
# İVME SKORLARI (teknik veriye dayalı)
# ================================================================
def score_momentum(m: dict, tech: Optional[dict]) -> Optional[float]:
    if tech is None:
        return None
    pts = 0.0
    components = 0

    rsi = safe_num(tech.get("rsi"))
    if rsi is not None:
        components += 1
        if rsi > 70:
            pts += 30
        elif rsi > 50:
            pts += 30 * ((rsi - 50) / 20)

    price = safe_num(tech.get("price"))
    ma50 = safe_num(tech.get("ma50"))
    ma200 = safe_num(tech.get("ma200"))
    if price is not None and ma50 is not None:
        components += 1
        if ma200 is not None and price > ma50 and ma50 > ma200:
            pts += 30
        elif price > ma50:
            pts += 15

    vol_ratio = safe_num(tech.get("vol_ratio"))
    if vol_ratio is not None:
        components += 1
        if vol_ratio > 2.0:
            pts += 25
        elif vol_ratio > 1.5:
            pts += 15
        elif vol_ratio > 1.2:
            pts += 8

    ph = tech.get("price_history")
    if ph and len(ph) >= 20 and price is not None:
        components += 1
        price_20d_ago = safe_num(ph[-20].get("close")) if isinstance(ph[-20], dict) else None
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
    return min(round(pts, 1), 100.0)


def score_technical_break(m: dict, tech: Optional[dict]) -> Optional[float]:
    if tech is None:
        return None
    pts = 0.0
    components = 0

    price = safe_num(tech.get("price"))
    pct_from_high = safe_num(tech.get("pct_from_high"))
    if price is not None and pct_from_high is not None:
        components += 1
        if pct_from_high >= 0:
            pts += 40
        elif pct_from_high >= -5:
            pts += 30
        elif pct_from_high >= -10:
            pts += 15

    cross_signal = tech.get("cross_signal")
    if cross_signal is not None:
        components += 1
        if cross_signal == "GOLDEN_CROSS":
            pts += 30
    elif tech.get("ma50") and tech.get("ma200"):
        components += 1
        if safe_num(tech.get("ma50")) > safe_num(tech.get("ma200")):
            pts += 15

    bb_pos = tech.get("bb_pos")
    if bb_pos is not None:
        components += 1
        if bb_pos == "ABOVE":
            pts += 20
        elif bb_pos == "INSIDE":
            pts += 5

    macd_cross = tech.get("macd_cross")
    rsi = safe_num(tech.get("rsi"))
    if macd_cross is not None and rsi is not None:
        components += 1
        if macd_cross == "BULLISH" and rsi < 60:
            pts += 10
        elif macd_cross == "BULLISH":
            pts += 5

    if components == 0:
        return None
    return min(round(pts, 1), 100.0)


def score_institutional_flow(m: dict, tech: Optional[dict]) -> Optional[float]:
    pts = 0.0
    components = 0

    inst_pct = safe_num(m.get("inst_holders_pct"))
    if inst_pct is not None:
        components += 1
        if inst_pct > 0.70:
            pts += 40
        elif inst_pct > 0.50:
            pts += 30
        elif inst_pct > 0.30:
            pts += 20
        elif inst_pct > 0.10:
            pts += 10

    if tech is not None:
        vol_ratio = safe_num(tech.get("vol_ratio"))
        ph = tech.get("price_history")
        if vol_ratio is not None and ph and len(ph) >= 5:
            components += 1
            price_now = safe_num(ph[-1].get("close")) if isinstance(ph[-1], dict) else None
            price_5d = safe_num(ph[-5].get("close")) if isinstance(ph[-5], dict) else None
            if price_now and price_5d and price_5d > 0:
                chg_5d = (price_now - price_5d) / price_5d
                if vol_ratio > 1.5 and chg_5d > 0.03:
                    pts += 30
                elif vol_ratio > 1.2 and chg_5d > 0.01:
                    pts += 15

        price = safe_num(tech.get("price"))
        ma50 = safe_num(tech.get("ma50"))
        if price is not None and ma50 is not None and vol_ratio is not None:
            components += 1
            if price > ma50 and vol_ratio > 1.0:
                pts += 30
            elif price > ma50:
                pts += 15
            elif vol_ratio > 1.5:
                pts += 5

    if components == 0:
        return None
    return min(round(pts, 1), 100.0)


# ================================================================
# CONFIDENCE
# ================================================================
def confidence_score(m: dict) -> float:
    have = sum(1 for k in CONFIDENCE_KEYS if safe_num(m.get(k)) is not None)
    return round(100 * have / len(CONFIDENCE_KEYS), 1)


# ================================================================
# LABELS
# ================================================================
def timing_label(ivme_score: float) -> str:
    s = ivme_score or 50
    if s < 30:
        return "ERKEN"
    if s < 55:
        return "GELİŞİYOR"
    if s < 75:
        return "TEYİTLİ"
    if s < 88:
        return "GEÇ"
    return "AŞIRI"


def quality_label(fa_pure: float) -> str:
    s = fa_pure or 50
    if s >= 78:
        return "ELİT"
    if s >= 62:
        return "GÜÇLÜ"
    if s >= 45:
        return "ORTA"
    if s >= 30:
        return "ZAYIF"
    return "RİSKLİ"


def entry_quality_label(fa_pure: float, ivme: float, risk_penalty: int) -> str:
    fa = fa_pure or 50
    iv = ivme or 50
    rp = risk_penalty or 0
    if fa < 40 and iv >= 65:
        return "SPEKÜLATİF"
    if fa < 30 or rp <= -25:
        return "KAÇIN"
    if fa >= 60 and iv >= 45 and iv < 80 and rp > -15:
        return "TEYİTLİ"
    if fa >= 55 and iv < 45:
        return "ERKEN"
    if fa >= 50 and iv >= 80:
        return "GEÇ"
    if fa >= 45 and iv >= 40 and iv < 70 and rp > -15:
        return "FIRSAT"
    return "BEKLE"


def decision_engine(fa_pure: float, ivme: float, risk_penalty: int, entry_label: str) -> str:
    fa = fa_pure or 50
    rp = risk_penalty or 0
    if entry_label in ("SPEKÜLATİF", "KAÇIN"):
        return "KAÇIN"
    if rp <= -25 or fa < 30:
        return "KAÇIN"
    if entry_label == "TEYİTLİ" and fa >= 60 and rp > -15:
        return "AL"
    if entry_label == "ERKEN" and fa >= 55:
        return "İZLE"
    if entry_label == "GEÇ":
        return "BEKLE"
    if entry_label == "FIRSAT":
        return "İZLE"
    if fa >= 45 and rp > -20:
        return "BEKLE"
    return "KAÇIN"


def style_label(scores: dict) -> str:
    v, q, g = scores["value"], scores["quality"], scores["growth"]
    moat, bal = scores["moat"], scores["balance"]
    mom = scores.get("momentum", 50)
    tb = scores.get("tech_break", 50)
    if mom >= 75 and tb >= 70 and g >= 55:
        return "Momentum Lideri"
    if q >= 75 and g >= 60 and v >= 40 and moat >= 60:
        return "Kaliteli Bileşik"
    if q >= 72 and moat >= 65 and v < 40:
        return "Premium Kalite"
    if v >= 75 and bal >= 55:
        return "Derin Değer"
    if g >= 70 and v >= 45:
        return "GARP"
    if mom >= 65 and tb >= 60 and v < 45:
        return "Teknik Kırılım"
    if g >= 65 and q >= 55 and v < 45:
        return "Büyüme Odaklı"
    if v >= 70 and q < 45:
        return "Değer Tuzağı Riski"
    if bal < 40 and g >= 50:
        return "Yüksek Riskli Dönüş"
    if scores.get("capital", 50) >= 70 and q >= 55:
        return "Temettü / Gelir"
    if mom < 30 and tb < 30:
        return "Momentum Zayıf"
    return "Dengeli"


def legendary_labels(m: dict, scores: dict) -> dict:
    pf = m.get("piotroski_f")
    az = m.get("altman_z")
    bm = m.get("beneish_m")
    peg_v = m.get("peg")
    mos = m.get("margin_safety")

    pf_l = "N/A" if pf is None else (
        f"{int(pf)}/9 (Güçlü)" if pf >= 7
        else f"{int(pf)}/9 (Orta)" if pf >= 5
        else f"{int(pf)}/9 (Zayıf)"
    )
    az_l = "N/A" if az is None else (
        f"{az:.2f} (Güvenli)" if az >= 3
        else f"{az:.2f} (Gri Bölge)" if az >= 1.8
        else f"{az:.2f} (Riskli)"
    )
    bm_l = "N/A" if bm is None else (
        f"{bm:.2f} (Düşük risk)" if bm < -2.22
        else f"{bm:.2f} (İzle)" if bm < -1.78
        else f"{bm:.2f} (Yüksek risk)"
    )
    peg_l = "N/A" if peg_v is None else (
        f"{peg_v:.2f} (Ucuz)" if peg_v < 1
        else f"{peg_v:.2f} (Makul)" if peg_v <= 2
        else f"{peg_v:.2f} (Pahalı)"
    )
    mos_l = "N/A" if mos is None else (
        "Yüksek" if mos >= 0.20 else "Orta" if mos >= 0 else "Düşük"
    )
    buffett = (
        "Geçti" if (scores["quality"] >= 75 and scores["moat"] >= 65 and scores["balance"] >= 60 and scores["capital"] >= 55)
        else "Sınırda" if scores["quality"] >= 60 and scores["moat"] >= 50
        else "Kaldı"
    )
    graham = (
        "Geçti" if (scores["value"] >= 70 and scores["balance"] >= 60 and (mos or -1) >= 0)
        else "Sınırda" if scores["value"] >= 55
        else "Kaldı"
    )
    is_bank = "bank" in (m.get("industry") or "").lower() or "sigorta" in (m.get("industry") or "").lower()
    if is_bank:
        az_l = "N/A (Banka)"

    return {
        "piotroski": pf_l, "altman": az_l, "beneish": bm_l,
        "peg": peg_l, "graham_mos": mos_l,
        "buffett_filter": buffett, "graham_filter": graham,
    }


def drivers(
    scores: dict,
    confidence: float,
    m: Optional[dict] = None,
    sector_group: Optional[str] = None,
) -> tuple[list[str], list[str]]:
    """Pozitif ve negatif driver'ları hesapla."""
    pos: list[str] = []
    neg: list[str] = []
    roe = m.get("roe") if m else None
    pe = m.get("pe") if m else None
    nd = m.get("net_debt_ebitda") if m else None
    rg = m.get("revenue_growth") if m else None

    if scores["quality"] >= 70:
        pos.append(f"Yüksek iş kalitesi{f' — ROE %{roe * 100:.0f}' if roe else ''}")
    if scores["earnings"] >= 65:
        pos.append("Nakit akışı kârı destekliyor")
    if scores["balance"] >= 70:
        pos.append(f"Sağlam bilanço{f' — NB/FAVÖK {nd:.1f}x' if nd is not None else ''}")
    if scores["value"] >= 70:
        pos.append(f"Ucuz değerleme{f' — F/K {pe:.1f}' if pe else ''}")
    if scores["moat"] >= 65:
        pos.append("Fiyatlama gücü ve marj stabilitesi")
    if scores["growth"] >= 70:
        pos.append(f"Güçlü büyüme{f' — gelir +%{rg * 100:.0f}' if rg else ''}")
    if scores.get("momentum", 50) >= 70:
        pos.append("Güçlü momentum — fiyat ve hacim destekli")
    if scores.get("tech_break", 50) >= 70:
        pos.append("Teknik kırılım sinyali aktif")
    if scores.get("inst_flow", 50) >= 65:
        pos.append("Kurumsal alış akışı pozitif")
    if not pos:
        pos.append("Dengeli profil, öne çıkan kategorisi yok")

    if scores["value"] < 40:
        neg.append(f"Pahalı değerleme{f' — F/K {pe:.1f}' if pe else ''}")
    if scores["quality"] < 40:
        neg.append("Düşük kârlılık — marjlar veya ROIC zayıf")
    if scores["growth"] < 40:
        neg.append("Büyüme zayıf veya tutarsız")
    if scores["balance"] < 40:
        neg.append(f"Borç/likidite riski{f' — NB/FAVÖK {nd:.1f}x' if nd is not None else ''}")
    if scores["earnings"] < 40:
        neg.append("Nakit akış muhasebe kârının gerisinde")
    if scores["moat"] < 35:
        neg.append("Marj stabilitesi zayıf — fiyatlama gücü yok")
    if scores.get("momentum", 50) < 30:
        neg.append("Momentum çok zayıf — düşüş trendinde")
    if scores.get("tech_break", 50) < 25:
        neg.append("Teknik görünüm olumsuz")
    if scores.get("inst_flow", 50) < 25:
        neg.append("Kurumsal ilgi düşük veya satış baskısı")
    if confidence < 65:
        neg.append("Bazı veriler eksik — dikkatli değerlendir")
    if not neg:
        neg.append("Şu an belirgin bir risk yok")

    return pos[:5], neg[:5]


# ================================================================
# VALUATION STRETCH
# ================================================================
def compute_valuation_stretch(value_score: float) -> int:
    """Value skoruna göre stretch puanı (bonus/ceza)."""
    for threshold, stretch in VAL_STRETCH_MAP:
        if value_score >= threshold:
            return stretch
    return -15


# ================================================================
# HYPE DETECTION
# ================================================================
def detect_hype(
    tech: Optional[dict],
    fa_pure: float,
) -> tuple[bool, Optional[str]]:
    """Hype tespiti. Returns: (is_hype, reason)"""
    if not tech:
        return False, None
    pct_20d = tech.get("pct_20d") or 0
    vol_ratio = tech.get("vol_ratio") or 1
    if pct_20d > HYPE_STRICT_PCT and vol_ratio > HYPE_STRICT_VOL and fa_pure < HYPE_STRICT_FA:
        return True, f"20 günde +%{pct_20d:.0f}, hacim {vol_ratio:.1f}x, FA sadece {fa_pure:.0f}"
    if pct_20d > HYPE_SOFT_PCT and vol_ratio > HYPE_SOFT_VOL and fa_pure < HYPE_SOFT_FA:
        return True, f"Hızlı yükseliş +%{pct_20d:.0f} ama temel zayıf (FA:{fa_pure:.0f})"
    return False, None


# ================================================================
# OVERALL SCORE HESAPLAMA
# ================================================================
def compute_overall(
    fa_pure: float,
    ivme_score: float,
    value_score: float,
    risk_penalty: int,
) -> float:
    """FA × 0.55 + Momentum(FA-gated) × 0.35 + Valuation Stretch + Risk"""
    momentum_effect = ivme_score * (fa_pure / 100.0)
    val_stretch = compute_valuation_stretch(value_score)
    overall = (
        fa_pure * OVERALL_FA_WEIGHT
        + momentum_effect * OVERALL_MOMENTUM_WEIGHT
        + val_stretch
        + max(risk_penalty, OVERALL_RISK_CAP) * OVERALL_RISK_FACTOR
    )
    return round(max(1, min(99, overall)), 1)


def compute_fa_pure(scores: dict) -> float:
    """Saf FA skoru — sadece 7 temel boyut ağırlıklı ortalaması."""
    total = sum(
        FA_WEIGHTS[key] * scores.get(key, 50)
        for key in FA_WEIGHTS
    )
    return round(max(1, min(99, total)), 1)


def compute_ivme(scores: dict) -> float:
    """İvme skoru — 3 teknik boyut ağırlıklı ortalaması."""
    total = sum(
        IVME_WEIGHTS[key] * scores.get(key, 50)
        for key in IVME_WEIGHTS
    )
    return round(max(1, min(99, total)), 1)

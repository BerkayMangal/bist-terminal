# ================================================================
# BISTBULL TERMINAL — TURKEY CONTEXT LAYER
# engine/turkey_context.py
#
# Inflation accounting awareness, profit quality interpretation,
# Beneish-to-human rename, Turkey-specific scoring notes.
# Additive, never crashes.
# ================================================================
from __future__ import annotations
import logging
from typing import Any
log = logging.getLogger("bistbull.turkey")

def _sf(v, d=0.0):
    if v is None: return d
    try: return float(v)
    except: return d


def build_turkey_context(metrics: dict, analysis: dict) -> dict:
    """Master entry. Returns turkey_context dict. Never raises."""
    try:
        return _build(metrics, analysis)
    except Exception as e:
        log.warning(f"turkey_context failed: {e}")
        return {}


def _build(m: dict, a: dict) -> dict:
    return {
        "inflation_accounting": _inflation_context(m, a),
        "profit_quality_interpretation": _profit_quality(m),
        "accounting_risk": _accounting_risk(m),
        "turkey_notes": _turkey_notes(m, a),
    }


# ── Inflation Accounting ─────────────────────────────────────────

def _inflation_context(m: dict, a: dict) -> dict:
    """Detect likely inflation-accounting-sensitive situations."""
    sector = a.get("sector_group", "")
    
    # Signals that inflation accounting may distort
    signals = 0
    notes = []
    
    # High asset base sectors more affected
    if sector in ("Sanayi", "Enerji", "Ulaştırma", "GYO"):
        signals += 1
    
    # Large gap between net income and operating CF suggests accrual effects
    ni = _sf(m.get("net_income"))
    ocf = _sf(m.get("operating_cf"))
    if ni > 0 and ocf != 0:
        ratio = ocf / ni if ni != 0 else 0
        if ratio < 0.3:
            signals += 2
            notes.append("Nakit akışı kâra göre düşük — muhasebe etkileri olabilir")
        elif ratio < 0.6:
            signals += 1
    
    # Very high margins in high-inflation environment can be distorted
    nm = _sf(m.get("net_margin"))
    if nm > 0.25:
        signals += 1
    
    # Revenue growth much higher than sector norm may include revaluation
    rg = _sf(m.get("revenue_growth"))
    if rg > 0.50:
        signals += 1
        notes.append("Yüksek gelir artışı kısmen enflasyon etkisi olabilir")
    
    if signals >= 3:
        status = "material"
        note = "Enflasyon muhasebesi bazı kalemleri önemli ölçüde etkiliyor olabilir."
    elif signals >= 1:
        status = "watch"
        note = "Enflasyon muhasebesi etkisi olabilir, kârlılık daha temkinli okunmalı."
    else:
        status = "normal"
        note = ""
    
    return {"status": status, "note": note, "details": notes[:2]}


# ── Profit Quality Interpretation ────────────────────────────────

def _profit_quality(m: dict) -> dict:
    """Plain-language profit quality — replaces raw CFO/NI display."""
    cfo_ni = _sf(m.get("cfo_to_ni"))
    fcf_margin = _sf(m.get("fcf_margin"))
    beneish = m.get("beneish_m")
    ni = _sf(m.get("net_income"))
    ocf = _sf(m.get("operating_cf"))
    
    # Receivables vs revenue growth (accrual quality)
    rec = _sf(m.get("receivables"))
    rec_prev = _sf(m.get("receivables_prev"))
    rev = _sf(m.get("revenue"))
    rev_prev = _sf(m.get("revenue_prev"))
    
    accrual_flag = False
    if rec_prev > 0 and rev_prev > 0 and rev > 0:
        rec_growth = (rec - rec_prev) / rec_prev
        rev_growth_r = (rev - rev_prev) / rev_prev
        if rec_growth > rev_growth_r + 0.15:
            accrual_flag = True
    
    # Build interpretation
    lines = []
    level = "iyi"  # iyi / orta / zayıf
    
    if ni > 0 and ocf > 0 and cfo_ni >= 1.0:
        lines.append("Kâr nakde iyi dönüyor")
    elif ni > 0 and ocf > 0 and cfo_ni >= 0.6:
        lines.append("Kâr var, nakit tarafı kabul edilebilir")
        level = "orta"
    elif ni > 0 and (ocf <= 0 or cfo_ni < 0.5):
        lines.append("Kâr var ama nakit tarafı zayıf")
        level = "zayıf"
    elif ni <= 0:
        lines.append("Şirket zarar ediyor")
        level = "zayıf"
    
    if accrual_flag:
        lines.append("Alacaklar gelirden hızlı büyüyor — tahsilat riski olabilir")
        if level == "iyi": level = "orta"
    
    if beneish is not None:
        try:
            bm = float(beneish)
            if bm > -1.78:
                lines.append("Muhasebe etkileri kârı olduğundan iyi gösterebilir")
                level = "zayıf"
        except:
            pass
    
    return {
        "level": level,
        "summary": lines[0] if lines else "Yeterli veri yok",
        "details": lines[:3],
    }


# ── Accounting Risk (Beneish rename) ────────────────────────────

def _accounting_risk(m: dict) -> dict:
    """Beneish M-Score reframed as plain-language accounting risk."""
    bm = m.get("beneish_m")
    if bm is None:
        return {"level": "bilinmiyor", "note": "Yeterli veri yok", "raw_score": None}
    
    try:
        bm_val = float(bm)
    except:
        return {"level": "bilinmiyor", "note": "Hesaplanamadı", "raw_score": None}
    
    if bm_val < -2.22:
        return {"level": "düşük", "note": "Muhasebe kalemleri normal aralıkta görünüyor.", "raw_score": round(bm_val, 2)}
    elif bm_val < -1.78:
        return {"level": "orta", "note": "Bazı kalemlerde dikkat isteyen hareketler var.", "raw_score": round(bm_val, 2)}
    else:
        return {"level": "yüksek", "note": "Gelir ve kârlılık kalemlerinde dikkat isteyen hareketler var.", "raw_score": round(bm_val, 2)}


# ── Turkey-Specific Notes ────────────────────────────────────────

def _turkey_notes(m: dict, a: dict) -> list[str]:
    """Contextual notes about Turkish market realities."""
    notes = []
    sector = a.get("sector_group", "")
    
    # High interest rate environment
    de = _sf(m.get("debt_equity"))
    nd_ebitda = _sf(m.get("net_debt_ebitda"))
    if de > 1.5 or nd_ebitda > 3:
        notes.append("Yüksek faiz ortamında borç baskı yaratabilir")
    
    # Bank-specific
    if sector == "Banka":
        notes.append("Banka bilançoları sanayi şirketlerinden farklı okunmalı")
        nm = _sf(m.get("net_margin"))
        if nm > 0 and nm < 0.15:
            notes.append("Net faiz marjı baskı altında olabilir")
    
    # Holding discount
    if sector == "Holding":
        notes.append("Holding iskontosu nedeniyle PD/DD düşük olabilir — bu yapısal")
    
    # GYO
    if sector == "GYO":
        notes.append("Gayrimenkul yeniden değerleme kârı nakit değil")
    
    # Export-heavy sectors
    if sector in ("Otomotiv", "Savunma"):
        notes.append("Döviz geliri TL zayıflığında avantaj sağlayabilir")
    
    # High growth + high inflation
    rg = _sf(m.get("revenue_growth"))
    if rg > 0.30:
        notes.append("Nominal büyüme kısmen enflasyon etkisi içerebilir")
    
    return notes[:3]

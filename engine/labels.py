# ================================================================
# BISTBULL V11 — DYNAMIC LABEL ENGINE
# Mevcut scoring.py label fonksiyonlarına DOKUNMAZ.
# Üzerine binen ek etiketler, Legendary filtreler, conviction.
#
# 3 kaynak sentezi:
# 1. Citadel Quant → Buffett-Graham Hybrid, Smart Momentum Alpha
# 2. Forensic Professor → Anti-Bubble Compounder, Value Trap Eliminator
# 3. Berkay Factor → Ciro/PD etiket sistemi, BIST-specific signals
#
# Kullanım:
#   from engine.labels import compute_all_labels
#   labels = compute_all_labels(analysis_result, tech_data)
# ================================================================

from __future__ import annotations

from typing import Optional

from utils.helpers import safe_num


# ================================================================
# 1. DEĞER ETİKETLERİ — Ciro/PD bazlı
# ================================================================
def value_label_ciro_pd(
    ciro_pd: Optional[float],
    risk_penalty: int = 0,
    sector_group: str = "sanayi",
) -> Optional[dict]:
    """
    Ciro/PD bazlı değer etiketi.

    >=10 → KELEPİR (altın badge)
    6-10 → ÇOK UCUZ (neon yeşil)
    4-6  → UCUZ (yeşil)
    1-4  → NORMAL (gri)
    <1   → PAHALI (kırmızı)

    Güvenlik: Risk penalty <= -15 ise ucuz etiketler → "UCUZ AMA RİSKLİ"
    """
    if ciro_pd is None or sector_group in ("banka", "holding"):
        return None

    if ciro_pd >= 10:
        label, color, tier = "KELEPİR", "#FFD700", 5
    elif ciro_pd >= 6:
        label, color, tier = "ÇOK UCUZ", "#00e676", 4
    elif ciro_pd >= 4:
        label, color, tier = "UCUZ", "#66bb6a", 3
    elif ciro_pd >= 1:
        label, color, tier = "NORMAL", "#78909c", 2
    else:
        label, color, tier = "PAHALI", "#ef5350", 1

    # Güvenlik filtresi
    if risk_penalty <= -15 and tier >= 3:
        label = "UCUZ AMA RİSKLİ"
        color = "#ff9800"
        tier = 0  # Özel durum

    return {"label": label, "color": color, "tier": tier, "value": round(ciro_pd, 2)}


# ================================================================
# 2. KALİTE ETİKETLERİ — Kâr Kalitesi (earnings quality) odaklı
# ================================================================
def earnings_quality_label(m: dict) -> dict:
    """
    "Kazandığı para gerçek mi?" sorusunun cevabı.

    CFO/NI >= 1.0 ve Beneish < -2.22 → GERÇEK KÂR (yeşil)
    CFO/NI >= 0.6 ve Beneish < -1.78 → TEYİTLİ (açık yeşil)
    CFO/NI 0.3-0.6 → DİKKAT (sarı)
    CFO/NI < 0.3 veya Beneish > -1.78 → SAHTE KÂR RİSKİ (kırmızı)
    CFO < 0 ve NI > 0 → KAĞIT KÂR (koyu kırmızı)
    """
    cfo = safe_num(m.get("operating_cf"))
    ni = safe_num(m.get("net_income"))
    cfo_ni = safe_num(m.get("cfo_to_ni"))
    beneish = safe_num(m.get("beneish_m"))

    # Özel durum: kâr var nakit yok
    if cfo is not None and ni is not None and cfo < 0 and ni > 0:
        return {
            "label": "KAĞIT KÂR",
            "color": "#b71c1c",
            "detail": "Muhasebe kârı var ama nakit akışı negatif",
            "severity": "critical",
        }

    # CFO/NI bazlı derecelendirme
    if cfo_ni is None:
        return {"label": "VERİ YOK", "color": "#546e7a", "detail": "CFO/NI hesaplanamadı", "severity": "unknown"}

    beneish_ok = beneish is not None and beneish < -2.22
    beneish_watch = beneish is not None and beneish < -1.78

    if cfo_ni >= 1.0 and beneish_ok:
        return {"label": "GERÇEK KÂR", "color": "#00e676", "detail": f"CFO/NI {cfo_ni:.2f} + Beneish temiz", "severity": "safe"}
    elif cfo_ni >= 0.6 and beneish_watch:
        return {"label": "TEYİTLİ", "color": "#66bb6a", "detail": f"CFO/NI {cfo_ni:.2f} — kabul edilebilir", "severity": "ok"}
    elif cfo_ni >= 0.3:
        return {"label": "DİKKAT", "color": "#ffb300", "detail": f"CFO/NI {cfo_ni:.2f} — nakit kalitesi zayıf", "severity": "warning"}
    else:
        return {"label": "SAHTE KÂR RİSKİ", "color": "#ef5350", "detail": f"CFO/NI {cfo_ni:.2f} — çok düşük", "severity": "danger"}


# ================================================================
# 3. SERMAYE TAHSİSİ ETİKETİ — Yönetim kalitesi
# ================================================================
def capital_allocation_label(m: dict, scores: dict) -> dict:
    """
    Yönetim parayı nasıl kullanıyor?

    Compounder: Yüksek ROIC + düşük seyreltme + pozitif FCF
    Dağıtıcı: İyi temettü + geri alım + disiplinli capex
    Tüketici: Yüksek capex ama düşük ROIC (para yakıyor)
    Seyreltici: Hisse seyreltme > %5 (hissedar değer tahribi)
    """
    capital_score = scores.get("capital", 50)
    dilution = safe_num(m.get("share_change"))
    roic = safe_num(m.get("roic"))
    fcf_yield = safe_num(m.get("fcf_yield"))

    if capital_score >= 75 and dilution is not None and dilution <= 0:
        return {"label": "COMPOUNDER", "color": "#00e676", "detail": "Geri alım + yüksek sermaye verimi"}
    elif capital_score >= 65:
        return {"label": "DİSİPLİNLİ", "color": "#66bb6a", "detail": "İyi sermaye tahsisi"}
    elif dilution is not None and dilution > 0.05:
        pct = round(dilution * 100, 1)
        return {"label": "SEYRELTİCİ", "color": "#ef5350", "detail": f"Hisse seyreltme %{pct}"}
    elif roic is not None and roic < 0.05 and fcf_yield is not None and fcf_yield < 0:
        return {"label": "PARA YAKIYOR", "color": "#b71c1c", "detail": "Düşük ROIC + negatif FCF"}
    elif capital_score >= 50:
        return {"label": "ORTA", "color": "#78909c", "detail": "Normal sermaye tahsisi"}
    else:
        return {"label": "ZAYIF", "color": "#ff9800", "detail": "Sermaye tahsisi zayıf"}


# ================================================================
# 4. REJİM ETİKETİ — Hisse profil özeti (tek satır)
# ================================================================
def regime_label(analysis_result: dict) -> str:
    """
    Tek satırlık hisse profil özeti — dashboard card'larında kullanılır.

    Örnekler:
    "HIGH CONVICTION + EARLY ACCUMULATION"
    "GOOD FA + RETAIL SPIKE / DO NOT CHASE"
    "CHEAP BUT FRAGILE / BALANCE SHEET RISK"
    "COMPOUNDER ON VOLUME IGNITION"
    """
    fa = analysis_result.get("fa_score", analysis_result.get("deger", 50))
    ivme = analysis_result.get("ivme", 50)
    risk = analysis_result.get("risk_penalty", 0)
    decision = analysis_result.get("decision", "BEKLE")
    is_hype = analysis_result.get("is_hype", False)
    scores = analysis_result.get("scores", {})

    v11 = analysis_result.get("v11", {})
    acc = v11.get("accumulation", {}) or {}
    acc_type = acc.get("type", "NEUTRAL")
    is_fatal = v11.get("is_fatal", False)
    ciro_label = (v11.get("ciro_pd_label") or {}).get("label", "")

    # Fatal durumlar
    if is_fatal:
        return "KIRMIZI BAYRAK — YÜKSEK RİSK"
    if is_hype:
        return "SPEKÜLATİF — DİKKATLİ OL"

    # Temel + momentum + akış kombinasyonları
    if fa >= 65 and acc_type == "ACCUMULATION":
        return "GÜÇLÜ TEMEL + SESSİZ ALIM"
    if fa >= 65 and ivme >= 60 and decision == "AL":
        return "GÜÇLÜ TEMEL + UYGUN MOMENTUM"
    if fa >= 55 and ivme < 40:
        return "KALİTELİ AMA HENÜZ İVME YOK"
    if fa >= 55 and acc_type == "FOMO":
        return "İYİ TEMEL AMA FOMO RİSKİ"
    if fa < 40 and ivme >= 65:
        return "TEMELSİZ MOMENTUM — DİKKAT"

    # Ciro/PD bazlı
    if ciro_label in ("KELEPİR", "ÇOK UCUZ") and scores.get("balance", 50) >= 55:
        return "UCUZ + SAĞLAM BİLANÇO"
    if ciro_label in ("KELEPİR", "ÇOK UCUZ") and scores.get("balance", 50) < 45:
        return "UCUZ AMA BİLANÇO ZAYIF — DİKKAT"

    # Genel durumlar
    if decision == "AL":
        return "GÜÇLÜ GÖRÜNÜYOR"
    if decision == "İZLE":
        return "İZLENEBİLİR — GELİŞİYOR"
    if decision == "BEKLE":
        return "BEKLEMEDE — NET SİNYAL YOK"
    return "ZAYIF GÖRÜNÜYOR — DİKKAT"


# ================================================================
# 5. LEGENDARY ALPHA FILTERS (Efsanevi Modeller V11)
# ================================================================

def filter_buffett_graham_hybrid(r: dict) -> dict:
    """
    Buffett-Graham Hybrid — Core Compounder Filter.

    Amaç: Kaliteli, ucuz, güçlü bilançolu, iyi yönetilen şirketler.
    Orta/uzun vade portföy çekirdeği. False positive oranı düşük.

    Koşullar:
    - Kalite >= 72 (iş kalitesi)
    - Kâr Kalitesi >= 65 (nakit teyidi)
    - Bilanço >= 60 (borç güvenliği)
    - Sermaye >= 60 (yönetim disiplini)
    - Değerleme >= 50 (pahalı değil)
    - Hendek >= 55 (fiyatlama gücü)
    - Risk > -15 (ciddi sorun yok)
    """
    scores = r.get("scores", {})
    m = r.get("metrics", {})
    risk = r.get("risk_penalty", 0)

    passed = (
        scores.get("quality", 0) >= 72
        and scores.get("earnings", 0) >= 65
        and scores.get("balance", 0) >= 60
        and scores.get("capital", 0) >= 60
        and scores.get("value", 0) >= 50
        and scores.get("moat", 0) >= 55
        and risk > -15
    )

    reasons = []
    if passed:
        pf = m.get("piotroski_f")
        mos = m.get("margin_safety")
        if pf is not None and pf >= 7:
            reasons.append(f"Piotroski {int(pf)}/9")
        if mos is not None and mos > 0.15:
            reasons.append(f"Graham MoS %{mos*100:.0f}")
        reasons.append(f"Q:{scores.get('quality',0):.0f} B:{scores.get('balance',0):.0f} C:{scores.get('capital',0):.0f}")

    return {
        "name": "Buffett-Graham Hybrid",
        "icon": "🏛️",
        "passed": passed,
        "detail": " · ".join(reasons) if reasons else None,
        "style": "Kaliteli compounder — uzun vade çekirdeği",
    }


def filter_anti_bubble_compounder(r: dict) -> dict:
    """
    Anti-Bubble Compounder — Cash-Is-King Filter.

    Amaç: Hype'tan uzak, gerçek nakit üreten bileşik getiriciler.
    "Henüz herkes fark etmeden" kaliteliyi bulmak.

    Koşullar:
    - Kalite >= 70 (iyi kazanıyor)
    - Kâr Kalitesi >= 75 (nakit akışı güçlü)
    - CFO/NI >= 0.9 (kazandığı gerçek)
    - FCF marjı >= %5 (serbest nakit üretiyor)
    - Seyreltme <= %2 (hissedarı sulandırmıyor)
    - Momentum 40-70 (hype yok ama ilgi var)
    - 20g fiyat değişimi < %12 (parabolic değil)
    """
    scores = r.get("scores", {})
    m = r.get("metrics", {})
    tech_pct = r.get("metrics", {})  # analyze_symbol tech verisi koymaz, ama ivme var

    cfo_ni = safe_num(m.get("cfo_to_ni"))
    fcf_margin = safe_num(m.get("fcf_margin"))
    dilution = safe_num(m.get("share_change"))
    momentum = scores.get("momentum", 50)

    passed = (
        scores.get("quality", 0) >= 70
        and scores.get("earnings", 0) >= 75
        and cfo_ni is not None and cfo_ni >= 0.9
        and fcf_margin is not None and fcf_margin >= 0.05
        and (dilution is None or dilution <= 0.02)
        and 40 <= momentum <= 70
    )

    reasons = []
    if passed:
        if cfo_ni:
            reasons.append(f"CFO/NI {cfo_ni:.2f}")
        if fcf_margin:
            reasons.append(f"FCF %{fcf_margin*100:.1f}")
        bm = m.get("beneish_m")
        if bm is not None and bm < -2.22:
            reasons.append("Beneish temiz")
        reasons.append("Hype yok, nakit güçlü")

    return {
        "name": "Anti-Bubble Compounder",
        "icon": "🛡️",
        "passed": passed,
        "detail": " · ".join(reasons) if reasons else None,
        "style": "Nakit üreten, hype'sız bileşik getirici",
    }


def filter_value_trap_eliminator(r: dict) -> dict:
    """
    Value Trap Eliminator — Broken Value Filter.

    Amaç: Ucuz görünen ama aslında kırık hisseleri ELİMİNE ETMEK.
    Bu filtre GEÇERSE hisse ucuz VE sağlam demektir.
    FAIL ise ucuz ama bozuk — değer tuzağı.

    Bu filtre BIST'te özellikle çok değerli:
    "Ucuz" hisselerin önemli kısmı gerçekten bozuk.

    Koşullar:
    Ucuzluk testi: Değerleme >= 70 VE (Ciro/PD >= 4 veya Graham MoS > 0)
    Sağlamlık testi: Aşağıdakilerin HİÇBİRİ olmamalı:
    - Kâr Kalitesi < 45
    - Bilanço < 45
    - CFO/NI < 0.5
    - Faiz karşılama < 2
    - Beneish > -1.78
    - Risk <= -20
    """
    scores = r.get("scores", {})
    m = r.get("metrics", {})
    risk = r.get("risk_penalty", 0)

    # Ucuzluk kontrolü
    v11 = r.get("v11", {})
    ciro_pd = v11.get("ciro_pd")
    mos = safe_num(m.get("margin_safety"))
    is_cheap = (
        scores.get("value", 0) >= 70
        and ((ciro_pd is not None and ciro_pd >= 4) or (mos is not None and mos > 0))
    )

    if not is_cheap:
        return {
            "name": "Value Trap Eliminator",
            "icon": "🎯",
            "passed": None,  # None = test uygulanmadı (ucuz değil)
            "detail": "Hisse ucuz değil — filtre uygulanmadı",
            "style": "Değer tuzağı koruyucusu",
        }

    # Sağlamlık kontrolleri — bozukluk sinyalleri
    traps: list[str] = []
    cfo_ni = safe_num(m.get("cfo_to_ni"))
    ic = safe_num(m.get("interest_coverage"))
    beneish = safe_num(m.get("beneish_m"))

    if scores.get("earnings", 50) < 45:
        traps.append(f"Kâr kalitesi düşük ({scores.get('earnings',0):.0f})")
    if scores.get("balance", 50) < 45:
        traps.append(f"Bilanço zayıf ({scores.get('balance',0):.0f})")
    if cfo_ni is not None and cfo_ni < 0.5:
        traps.append(f"Düşük CFO/NI ({cfo_ni:.2f})")
    if ic is not None and ic < 2.0:
        traps.append(f"Faiz karşılama yetersiz ({ic:.1f}x)")
    if beneish is not None and beneish > -1.78:
        traps.append(f"Beneish kırmızı ({beneish:.2f})")
    if risk <= -20:
        traps.append(f"Risk penaltisi yüksek ({risk})")

    passed = len(traps) == 0

    return {
        "name": "Value Trap Eliminator",
        "icon": "🎯",
        "passed": passed,
        "detail": ("✅ Ucuz VE sağlam" if passed
                   else "⚠️ DEĞER TUZAĞI: " + " | ".join(traps)),
        "style": "Değer tuzağı koruyucusu",
        "trap_reasons": traps if not passed else None,
    }


# ================================================================
# 6. CONVICTION SCORE — tüm sinyallerin birleşik güveni
# ================================================================
def compute_conviction(r: dict) -> dict:
    """
    Conviction = tüm sinyallerin birleşik güven skoru.

    FA tek başına yeterli değil. Conviction şunlara bakar:
    - FA Pure (temel kalite)
    - Risk durumu (fatal var mı)
    - Legendary filtre sonuçları
    - Kâr kalitesi etiketi
    - Ciro/PD etiketi
    - Accumulation/FOMO durumu

    Output: 0-100 conviction + "LOW" / "MEDIUM" / "HIGH" label
    """
    fa = r.get("fa_score", r.get("deger", 50))
    risk = r.get("risk_penalty", 0)
    scores = r.get("scores", {})
    v11 = r.get("v11", {})

    points = 0

    # FA bazı (max 40)
    if fa >= 70:
        points += 40
    elif fa >= 55:
        points += 25
    elif fa >= 45:
        points += 15
    else:
        points += 5

    # Risk durumu (max 20, min -10)
    if v11.get("is_fatal"):
        points -= 10
    elif risk > -10:
        points += 20
    elif risk > -20:
        points += 10
    else:
        points += 0

    # Kâr kalitesi (max 15)
    cfo_ni = safe_num(r.get("metrics", {}).get("cfo_to_ni"))
    if cfo_ni is not None:
        if cfo_ni >= 1.0:
            points += 15
        elif cfo_ni >= 0.6:
            points += 8
        elif cfo_ni < 0.3:
            points -= 5

    # Bilanço güvenliği (max 10)
    if scores.get("balance", 50) >= 65:
        points += 10
    elif scores.get("balance", 50) < 40:
        points -= 5

    # Legendary filtre bonusu (max 15)
    bg = filter_buffett_graham_hybrid(r)
    ac = filter_anti_bubble_compounder(r)
    if bg["passed"]:
        points += 15
    elif ac["passed"]:
        points += 10

    # Accumulation bonusu (max 10)
    acc = v11.get("accumulation", {}) or {}
    if acc.get("type") == "ACCUMULATION":
        points += 10
    elif acc.get("type") == "FOMO":
        points -= 5

    # Normalize
    conviction = max(0, min(100, points))
    if conviction >= 70:
        level = "HIGH"
    elif conviction >= 45:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {
        "score": conviction,
        "level": level,
    }


# ================================================================
# 7. MASTER LABEL FUNCTION — hepsini birleştir
# ================================================================
def compute_all_labels(analysis_result: dict, tech: Optional[dict] = None) -> dict:
    """
    Tüm V11 etiketlerini tek seferde hesapla.

    Input: analyze_symbol() çıktısı + compute_technical() çıktısı
    Output: {
        "value_label": {...},
        "earnings_quality": {...},
        "capital_allocation": {...},
        "regime": str,
        "conviction": {...},
        "legendary": {
            "buffett_graham": {...},
            "anti_bubble": {...},
            "value_trap": {...},
        }
    }
    """
    m = analysis_result.get("metrics", {})
    scores = analysis_result.get("scores", {})
    v11 = analysis_result.get("v11", {})

    # Ciro/PD etiketi
    ciro_pd = v11.get("ciro_pd")
    risk = analysis_result.get("risk_penalty", 0)
    sector = analysis_result.get("sector_group", "sanayi")
    val_label = value_label_ciro_pd(ciro_pd, risk, sector)

    # Kâr kalitesi etiketi
    eq_label = earnings_quality_label(m)

    # Sermaye tahsisi etiketi
    ca_label = capital_allocation_label(m, scores)

    # Rejim etiketi
    reg = regime_label(analysis_result)

    # Legendary filtreler
    bg = filter_buffett_graham_hybrid(analysis_result)
    ac = filter_anti_bubble_compounder(analysis_result)
    vt = filter_value_trap_eliminator(analysis_result)

    # Conviction
    conv = compute_conviction(analysis_result)

    return {
        "value_label": val_label,
        "earnings_quality": eq_label,
        "capital_allocation": ca_label,
        "regime": reg,
        "conviction": conv,
        "legendary": {
            "buffett_graham": bg,
            "anti_bubble": ac,
            "value_trap": vt,
        },
    }

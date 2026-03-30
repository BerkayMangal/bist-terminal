# ================================================================
# BISTBULL V11 SCORING SIDECAR
# Mevcut scoring.py'ye DOKUNMAZ. Üzerine binen overlay fonksiyonlar.
#
# Yeni özellikler:
# 1. Non-linear momentum gate (piecewise)
# 2. Fatal risk trigger sistemi
# 3. Ciro/PD skorlama + değer etiketi
# 4. V11 ağırlıklarla overall hesaplama
# 5. Accumulation Divergence (Smart Money vs FOMO)
#
# Sentez kaynakları:
# - Citadel Quant Strategist (non-linear gating, risk hardening)
# - Forensic Accounting Professor (earnings quality, manipulation)
# - Berkay Factor (Ciro/PD, BIST-specific value signals)
# ================================================================

from __future__ import annotations

import math
from typing import Optional

from config import (
    V11_FA_WEIGHTS,
    V11_OVERALL_FA_WEIGHT, V11_OVERALL_MOMENTUM_WEIGHT, V11_OVERALL_RISK_FACTOR,
    V11_RISK_CAP_NORMAL, V11_RISK_CAP_FATAL,
    V11_MOMENTUM_GATE,
    V11_CIRO_PD_THRESHOLDS, V11_CIRO_PD_LABELS,
    VAL_STRETCH_MAP,
)
from utils.helpers import safe_num, score_higher


# ================================================================
# 1. NON-LINEAR MOMENTUM GATE
# ================================================================
def fa_momentum_gate(fa_pure: float) -> float:
    """
    V11 non-linear momentum gate.

    V10'da lineer: gate = fa_pure / 100
    V11'de piecewise: FA < 35 → %8, FA 35-45 → %18, ...

    Bu yapının avantajı:
    - FA < 40 hisselerde hype'ı çok daha sert boğar
    - Orta kalitede (45-55) momentuma kontrollü izin verir
    - Gerçekten kaliteli hisselerde (65+) trendi ödüllendirir
    - Cliff-edge behavior yok, kademeli geçiş var

    Quant justification:
    BIST gibi spekülatif akışların hızlı olduğu piyasada,
    40-55 bandında momentum V10'da gereğinden fazla konuşuyordu.
    """
    for threshold, gate_value in V11_MOMENTUM_GATE:
        if fa_pure >= threshold:
            return gate_value
    return 0.08


# ================================================================
# 2. FATAL RISK TRIGGERS
# ================================================================
def detect_fatal_risks(m: dict) -> tuple[bool, list[str]]:
    """
    V11 fatal risk trigger detector.

    Normal risk penaltileri -42'de cap'lenir.
    Fatal trigger varsa -55'e kadar gider VE decision-level veto uygulanır.

    Bu sistem "ucuz ama kırık" hisselerin scoreboard'da yukarıda
    durmasını engeller — BIST'te özellikle yüksek faiz ortamında
    borç kırılması ve sahte kâr riski hayati.

    Returns: (is_fatal, trigger_list)
    """
    triggers: list[str] = []

    # Trigger 1: Negatif özsermaye → şirket teknik olarak batık
    equity = safe_num(m.get("equity"))
    if equity is not None and equity < 0:
        triggers.append("negative_equity")

    # Trigger 2: Sahte kâr + yetersiz faiz karşılama
    # Kâr var ama nakit yok + borç servisini zar zor karşılıyor
    cfo = safe_num(m.get("operating_cf"))
    ni = safe_num(m.get("net_income"))
    ic = safe_num(m.get("interest_coverage"))
    if cfo is not None and ni is not None and ic is not None:
        if cfo < 0 and ni > 0 and ic < 1.5:
            triggers.append("fake_profit_critical")

    # Trigger 3: Borç stresi — yüksek kaldıraç + zayıf faiz karşılama
    nd_ebitda = safe_num(m.get("net_debt_ebitda"))
    if nd_ebitda is not None and ic is not None:
        if nd_ebitda > 4.5 and ic < 2.0:
            triggers.append("debt_distress")

    # Trigger 4: Muhasebe manipülasyonu + zayıf nakit kalitesi
    beneish = safe_num(m.get("beneish_m"))
    cfo_ni = safe_num(m.get("cfo_to_ni"))
    if beneish is not None and cfo_ni is not None:
        if beneish > -1.78 and cfo_ni < 0.5:
            triggers.append("manipulation_plus_fake")

    # Trigger 5: Aşırı seyreltme + negatif FCF
    dilution = safe_num(m.get("share_change"))
    fcf_margin = safe_num(m.get("fcf_margin"))
    if dilution is not None and fcf_margin is not None:
        if dilution > 0.10 and fcf_margin < 0:
            triggers.append("dilution_plus_negative_fcf")

    return len(triggers) > 0, triggers


def get_risk_cap(m: dict) -> int:
    """Fatal trigger varsa sert cap, yoksa normal V11 cap."""
    is_fatal, _ = detect_fatal_risks(m)
    return V11_RISK_CAP_FATAL if is_fatal else V11_RISK_CAP_NORMAL


# ================================================================
# 3. CİRO / PİYASA DEĞERİ SKORLAMA (Berkay Factor)
# ================================================================
def compute_ciro_pd(m: dict) -> Optional[float]:
    """
    Revenue / Market Cap oranı.

    Neden önemli:
    - Yüksek enflasyon ortamında cirolar hızlı şişer
    - Ama piyasa değeri her zaman takip edemez
    - Bu asimetri BIST'te gerçek değer fırsatlarını yakalama aracı

    EV/Sales'ten farkı:
    - EV/Sales borç dahil → yüksek borçlu şirketlerde noisy
    - Ciro/PD saf ve basit → Türk yatırımcısı anlar

    Bankalar hariç tutulur (ciro konsepti farklı).
    """
    revenue = safe_num(m.get("revenue"))
    market_cap = safe_num(m.get("market_cap"))
    if revenue is None or market_cap is None or market_cap <= 0:
        return None
    return revenue / market_cap


def score_ciro_pd(
    ciro_pd: Optional[float],
    sector_group: str = "sanayi",
) -> Optional[float]:
    """
    Ciro/PD'yi 0-100 puana dönüştür.

    Eşikler: (1.0, 3.0, 6.0, 10.0)
    1.0 → 10 puan (pahalı)
    3.0 → 40 puan (orta)
    6.0 → 70 puan (ucuz)
    10.0 → 95 puan (kelepir)

    Bankalar ve holdingler hariç — ciro konsepti farklı.
    """
    if ciro_pd is None:
        return None
    if sector_group in ("banka", "holding"):
        return None
    return score_higher(ciro_pd, *V11_CIRO_PD_THRESHOLDS)


def get_ciro_pd_label(
    ciro_pd: Optional[float],
    risk_penalty: int = 0,
    sector_group: str = "sanayi",
) -> Optional[dict]:
    """
    Ciro/PD etiket sistemi.

    KELEPİR / ÇOK UCUZ / UCUZ / NORMAL / PAHALI

    Güvenlik filtresi:
    Risk penalty <= -15 ise → "UCUZ AMA RİSKLİ" override
    Bu değer tuzağından korur.

    Returns: {"label": "KELEPİR", "color": "#FFD700", "value": 12.3}
    """
    if ciro_pd is None:
        return None
    if sector_group in ("banka", "holding"):
        return None

    # Etiket belirle
    label = "PAHALI"
    color = "#ef5350"
    for threshold, lbl, col in V11_CIRO_PD_LABELS:
        if ciro_pd >= threshold:
            label = lbl
            color = col
            break

    # Güvenlik filtresi — risk penalty çok yüksekse override
    if risk_penalty <= -15 and label in ("KELEPİR", "ÇOK UCUZ", "UCUZ"):
        label = "UCUZ AMA RİSKLİ"
        color = "#ff9800"

    return {
        "label": label,
        "color": color,
        "value": round(ciro_pd, 2),
    }


# ================================================================
# 4. CİRO/PD'NİN DEĞERLEME BOYUTUNA ENTEGRASYONU
# ================================================================
def compute_value_with_ciro_pd(
    v10_value_score: float,
    ciro_pd_score: Optional[float],
    net_debt_ebitda: Optional[float] = None,
) -> float:
    """
    V10 değerleme skoruna Ciro/PD'yi dinamik ağırlıkla entegre et.

    Dinamik ağırlık mantığı (leverage-adjusted):
    - Net nakit (NB/FAVÖK <= 1.0): Ciro/PD %12, EV bazlı %88
    - Orta borç (1.0 < NB/FAVÖK <= 2.5): Ciro/PD %10, EV bazlı %90
    - Yüksek borç (NB/FAVÖK > 2.5): Ciro/PD %6, EV bazlı %94
      (Yüksek borçta EV/Sales daha güvenilir — borcu hesaba katar)

    Neden dinamik?
    Revenue/MCap borcu görmezden gelir. Net nakit şirketlerde
    bu sorun yok, ama ağır borçlu şirketlerde EV/Sales daha doğru.
    """
    if ciro_pd_score is None:
        return v10_value_score

    # Leverage-adjusted ağırlık
    nd = safe_num(net_debt_ebitda)
    if nd is not None and nd <= 1.0:
        ciro_weight = 0.12
    elif nd is not None and nd <= 2.5:
        ciro_weight = 0.10
    else:
        ciro_weight = 0.06

    v10_weight = 1.0 - ciro_weight
    return round(v10_value_score * v10_weight + ciro_pd_score * ciro_weight, 1)


# ================================================================
# 5. V11 FA PURE — yeni ağırlıklarla
# ================================================================
def compute_fa_pure_v11(scores: dict) -> float:
    """
    V11 FA Pure skoru — güncelllenmiş ağırlıklarla.

    Değişiklikler vs V10:
    - Bilanço: %10 → %15 (borç koruması)
    - Kâr Kalitesi: %10 → %13 (forensic upgrade)
    - Büyüme: %15 → %12 (nominal büyüme yanıltıcı)
    - Kalite: %30 → %25 (alt başlıklara dağıtıldı)
    - Moat: %8 → %7 (data noisy)
    """
    total = sum(
        V11_FA_WEIGHTS[key] * scores.get(key, 50)
        for key in V11_FA_WEIGHTS
    )
    return round(max(1, min(99, total)), 1)


# ================================================================
# 6. V11 OVERALL — güncellenmiş formül
# ================================================================
def compute_overall_v11(
    fa_pure: float,
    ivme_score: float,
    value_score: float,
    risk_penalty: int,
    risk_cap: int = V11_RISK_CAP_NORMAL,
) -> float:
    """
    V11 Overall Score.

    Formül:
    OVERALL = FA × 0.58 + MomentumEffect × 0.28 + Stretch + Risk × 0.38

    V10'dan farklar:
    - FA ağırlığı: 0.55 → 0.58 (fundamentals daha baskın)
    - Momentum ağırlığı: 0.35 → 0.28 (hype riski azaltıldı)
    - Risk multiplier: 0.30 → 0.38 (downside daha çok konuşuyor)
    - Risk cap: -30 → -42 (normal) / -55 (fatal)

    Non-linear momentum gate:
    V10: momentum_effect = ivme × (fa / 100)  → lineer
    V11: momentum_effect = ivme × fa_momentum_gate(fa) → piecewise
    """
    # Non-linear momentum gate
    gate = fa_momentum_gate(fa_pure)
    momentum_effect = ivme_score * gate

    # Valuation stretch (V10 ile aynı)
    val_stretch = 0
    for threshold, stretch in VAL_STRETCH_MAP:
        if value_score >= threshold:
            val_stretch = stretch
            break

    # Overall
    capped_risk = max(risk_penalty, risk_cap)
    overall = (
        fa_pure * V11_OVERALL_FA_WEIGHT
        + momentum_effect * V11_OVERALL_MOMENTUM_WEIGHT
        + val_stretch
        + capped_risk * V11_OVERALL_RISK_FACTOR
    )
    return round(max(1, min(99, overall)), 1)


# ================================================================
# 7. ACCUMULATION DIVERGENCE (Smart Money vs FOMO)
# ================================================================
def compute_accumulation_divergence(tech: Optional[dict]) -> Optional[dict]:
    """
    Smart Money vs Retail FOMO ayrımı.

    Mantık:
    - Yüksek hacim + düşük fiyat hareketi = sessiz akümülasyon (SMART)
    - Yüksek hacim + aşırı fiyat hareketi = retail FOMO spike

    Input: compute_technical() çıktısı
    Output: {
        "score": 0-100,  (yüksek = smart money sinyali güçlü)
        "type": "ACCUMULATION" | "FOMO" | "NEUTRAL",
        "detail": str,
    }
    """
    if tech is None:
        return None

    vol_ratio = safe_num(tech.get("vol_ratio"))
    pct_20d = safe_num(tech.get("pct_20d"))
    price = safe_num(tech.get("price"))
    ma50 = safe_num(tech.get("ma50"))
    rsi = safe_num(tech.get("rsi"))

    if vol_ratio is None:
        return None

    score = 50  # başlangıç: nötr
    signal_type = "NEUTRAL"
    detail_parts: list[str] = []

    # --- Smart Money sinyalleri (puan artırır) ---

    # Hacim yüksek ama fiyat hareketi kontrollü (stealth accumulation)
    if vol_ratio > 1.3 and pct_20d is not None and abs(pct_20d) < 8:
        score += 20
        detail_parts.append(f"Hacim {vol_ratio:.1f}x ama fiyat sakin ({pct_20d:+.1f}%)")

    # Fiyat MA50 üzerinde + hacim normal-yüksek (trend takibi)
    if price is not None and ma50 is not None and price > ma50 and vol_ratio > 1.0:
        score += 10
        detail_parts.append("MA50 üstü + pozitif hacim")

    # RSI nötr bölgede + hacim artışı (alım baskısı ama aşırı değil)
    if rsi is not None and 40 <= rsi <= 65 and vol_ratio > 1.2:
        score += 10
        detail_parts.append(f"RSI {rsi:.0f} nötr + hacim artışı")

    # --- FOMO sinyalleri (puan düşürür) ---

    # Tek günde aşırı fiyat + hacim spike (retail FOMO klasik)
    if vol_ratio > 2.5 and pct_20d is not None and pct_20d > 15:
        score -= 30
        detail_parts.append(f"⚠️ FOMO spike: {vol_ratio:.1f}x hacim + %{pct_20d:.0f} fiyat")
        signal_type = "FOMO"

    # RSI aşırı alım + yüksek hacim (top sinyali)
    if rsi is not None and rsi > 72 and vol_ratio > 1.8:
        score -= 20
        detail_parts.append(f"RSI {rsi:.0f} aşırı alım + hacim spike")

    # Fiyat MA50 altında + hacim artışı (panik satış veya distribution)
    if price is not None and ma50 is not None and price < ma50 and vol_ratio > 1.5:
        score -= 15
        detail_parts.append("MA50 altı + yüksek hacim (dağıtım riski)")

    # Normalize
    score = max(0, min(100, score))

    if score >= 65:
        signal_type = "ACCUMULATION"
    elif score <= 35:
        signal_type = "FOMO"

    return {
        "score": score,
        "type": signal_type,
        "detail": " | ".join(detail_parts) if detail_parts else "Belirgin sinyal yok",
    }


# ================================================================
# 8. V11 ENRICHMENT — analyze_symbol çıktısını zenginleştirir
# ================================================================
def enrich_analysis_v11(analysis_result: dict) -> dict:
    """
    V10 analyze_symbol() çıktısını V11 metrikleriyle zenginleştirir.
    Mevcut hiçbir alanı SILMEZ veya DEĞIŞTIRMEZ — sadece ekler.

    Eklenen alanlar:
    - v11.fa_pure: V11 ağırlıklı FA skoru
    - v11.overall: V11 overall skoru
    - v11.gate: momentum gate değeri
    - v11.risk_cap: uygulanan risk cap (-42 veya -55)
    - v11.fatal_risks: fatal trigger listesi
    - v11.ciro_pd: Ciro/PD oranı
    - v11.ciro_pd_score: Ciro/PD puanı (0-100)
    - v11.ciro_pd_label: {"label": "KELEPİR", "color": "#FFD700", "value": 12.3}
    - v11.value_score: Ciro/PD entegreli değerleme skoru
    - v11.accumulation: Smart Money vs FOMO analizi

    Kullanım:
        r = analyze_symbol(symbol)
        r = enrich_analysis_v11(r)
    """
    m = analysis_result.get("metrics", {})
    scores = analysis_result.get("scores", {})
    tech = None  # tech verisi analysis_result içinde yok, ayrı çekilmeli

    sector_group = analysis_result.get("sector_group", "sanayi")
    risk_penalty = analysis_result.get("risk_penalty", 0)

    # --- Ciro/PD ---
    ciro_pd = compute_ciro_pd(m)
    ciro_pd_score = score_ciro_pd(ciro_pd, sector_group)
    ciro_pd_label = get_ciro_pd_label(ciro_pd, risk_penalty, sector_group)

    # --- V10 value score + Ciro/PD entegrasyon ---
    v10_value = scores.get("value", 50)
    nd_ebitda = safe_num(m.get("net_debt_ebitda"))
    v11_value = compute_value_with_ciro_pd(v10_value, ciro_pd_score, nd_ebitda)

    # --- V11 FA Pure (yeni ağırlıklar) ---
    v11_fa = compute_fa_pure_v11(scores)

    # --- Fatal risk check ---
    is_fatal, fatal_triggers = detect_fatal_risks(m)
    risk_cap = get_risk_cap(m)

    # --- Non-linear gate ---
    gate = fa_momentum_gate(v11_fa)

    # --- V11 Overall ---
    ivme = analysis_result.get("ivme", 50)
    v11_overall = compute_overall_v11(v11_fa, ivme, v11_value, risk_penalty, risk_cap)

    # --- V11 block oluştur ---
    v11_block = {
        "fa_pure": v11_fa,
        "overall": v11_overall,
        "gate": round(gate, 2),
        "risk_cap": risk_cap,
        "fatal_risks": fatal_triggers,
        "is_fatal": is_fatal,
        "ciro_pd": round(ciro_pd, 2) if ciro_pd is not None else None,
        "ciro_pd_score": round(ciro_pd_score, 1) if ciro_pd_score is not None else None,
        "ciro_pd_label": ciro_pd_label,
        "value_score": v11_value,
    }

    # --- Sonuca ekle (mevcut alanları bozmadan) ---
    analysis_result["v11"] = v11_block
    return analysis_result


def enrich_with_tech_v11(analysis_result: dict, tech: Optional[dict]) -> dict:
    """
    Tech verisi ayrı geliyorsa accumulation divergence ekle.
    enrich_analysis_v11 SONRA çağrılır.
    """
    if "v11" not in analysis_result:
        analysis_result["v11"] = {}

    acc = compute_accumulation_divergence(tech)
    analysis_result["v11"]["accumulation"] = acc
    return analysis_result

# ================================================================
# BISTBULL TERMINAL — ACTION SUMMARY ("Bugün ne yapmalı?")
# engine/action_summary.py
#
# Deterministic, rule-based. No AI needed for core output.
# Max 4 sentences. Every sentence tied to data.
# ================================================================

from __future__ import annotations
from typing import Optional
from engine.macro_decision import RegimeResult, get_sector_rotation


def generate_action_summary(
    result: RegimeResult,
    upcoming_event: Optional[str] = None,
) -> str:
    """
    Produce 3-4 sentence action summary in plain Turkish.
    Trust-aware: reflects confidence level and data quality in wording.
    """

    regime = result.regime
    confidence = result.confidence
    signals = result.signals
    contradictions = result.contradictions

    neg = [s for s in signals if s.score == -1]
    pos = [s for s in signals if s.score == 1]
    n_estimated = sum(1 for s in signals if s.source in ("tahmini", "eski"))

    sectors = get_sector_rotation(regime)

    # --- Sentence 1: Regime + confidence-aware tone ---
    if confidence == "LOW":
        s1 = "Tablo eksik, net bir yön vermek zor."
    elif regime == "RISK_OFF":
        s1 = "Piyasa temkinli, sen de ol."
    elif regime == "RISK_ON":
        s1 = "Ortam destekleyici, ama kontrollü ol."
    else:
        s1 = "Sinyaller karışık, acele etme."

    # --- Sentence 2: Key reason (even in LOW, show what we have) ---
    if confidence == "LOW":
        # Still point to strongest available signals
        if neg and pos:
            s2 = f"Elimizdekiler: {pos[0].name} olumlu, {neg[0].name} olumsuz — ama veri sınırlı."
        elif neg:
            s2 = f"{neg[0].name} olumsuz görünüyor ama teyit için daha fazla veri gerekli."
        elif pos:
            s2 = f"{pos[0].name} olumlu görünüyor ama teyit için daha fazla veri gerekli."
        else:
            s2 = "Yeterli sinyal yok, bekle."
    elif regime == "RISK_OFF" and neg:
        drivers = " ve ".join(s.name for s in neg[:2])
        s2 = f"{drivers} olumsuz yönde."
    elif regime == "RISK_ON" and pos:
        drivers = " ve ".join(s.name for s in pos[:2])
        s2 = f"{drivers} olumlu sinyaller veriyor."
    else:
        if neg and pos:
            s2 = f"{pos[0].name} olumlu ama {neg[0].name} baskı yapıyor."
        else:
            s2 = "Sinyaller net bir yön göstermiyor."

    # --- Sentence 3: Action (softened for LOW/NEUTRAL) ---
    if confidence == "LOW":
        s3 = "Yeni işlem için daha net veri bekle. Mevcut pozisyonlarda sabırlı ol."
    elif regime == "RISK_OFF":
        strong = " ve ".join(sectors["strong"][:2])
        s3 = f"Yeni pozisyon açma. {strong} tarafı görece güçlü görünüyor."
    elif regime == "RISK_ON":
        strong = " ve ".join(sectors["strong"][:2])
        if confidence == "MEDIUM":
            s3 = f"Kademeli alım düşünülebilir. {strong} yakından izlenebilir."
        else:
            s3 = f"Kademeli alım düşünülebilir. {strong} öne çıkıyor."
    else:
        # NEUTRAL
        s3 = "Mevcut pozisyonları koru, yeni alım için sinyal bekle."

    # --- Sentence 4: Contradiction, event, or data quality note ---
    s4 = ""
    if contradictions:
        s4 = f"Dikkat: {contradictions[0].message.split('.')[0]}."
    elif n_estimated >= 2:
        s4 = "Not: Bazı veriler tahmini — teyit gerekiyor."
    elif upcoming_event:
        s4 = f"Bu hafta önemli: {upcoming_event}."

    parts = [s1, s2, s3]
    if s4:
        parts.append(s4)

    return " ".join(parts)

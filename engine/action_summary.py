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
    Style: experienced investor speaking to a peer.

    Rules:
    - Every sentence must be tied to a concrete signal or regime.
    - No jargon. No hype. No generic filler.
    - Must include: regime, reason, action, optional event.
    """

    regime = result.regime
    score = result.score
    signals = result.signals
    contradictions = result.contradictions

    neg = [s for s in signals if s.score == -1]
    pos = [s for s in signals if s.score == 1]

    sectors = get_sector_rotation(regime)

    # --- Sentence 1: Regime + feeling ---
    if regime == "RISK_OFF":
        s1 = "Piyasa temkinli, sen de ol."
    elif regime == "RISK_ON":
        s1 = "Ortam destekleyici, ama kontrollü ol."
    else:
        s1 = "Piyasa kararsız, acele etme."

    # --- Sentence 2: Key reason (top 2 negative or positive drivers) ---
    if regime == "RISK_OFF" and neg:
        drivers = " ve ".join(s.name for s in neg[:2])
        s2 = f"{drivers} olumsuz yönde."
    elif regime == "RISK_ON" and pos:
        drivers = " ve ".join(s.name for s in pos[:2])
        s2 = f"{drivers} olumlu sinyaller veriyor."
    else:
        # Mixed
        if neg and pos:
            s2 = f"{pos[0].name} olumlu ama {neg[0].name} baskı yapıyor."
        else:
            s2 = "Sinyaller net bir yön göstermiyor."

    # --- Sentence 3: Action ---
    if regime == "RISK_OFF":
        strong = " ve ".join(sectors["strong"][:2])
        s3 = f"Yeni pozisyon açma. {strong} sektörlerine yakın dur."
    elif regime == "RISK_ON":
        strong = " ve ".join(sectors["strong"][:2])
        s3 = f"Kademeli alım düşünülebilir. {strong} öne çıkıyor."
    else:
        s3 = "Mevcut pozisyonları koru, yeni alım için sinyal bekle."

    # --- Sentence 4: Contradiction or upcoming event ---
    s4 = ""
    if contradictions:
        s4 = f"Dikkat: {contradictions[0].message.split('.')[0]}."
    elif upcoming_event:
        s4 = f"Bu hafta önemli: {upcoming_event}."

    parts = [s1, s2, s3]
    if s4:
        parts.append(s4)

    return " ".join(parts)

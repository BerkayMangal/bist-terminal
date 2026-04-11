# ================================================================
# BISTBULL TERMINAL — AI SAFETY SYSTEM
# ai/safety.py
#
# Strict rules for AI commentary generation.
# AI NEVER influences decisions. AI ONLY narrates Truth + Decision output.
# ================================================================

from __future__ import annotations

import re
import logging
from typing import Optional
from core.trust import DataPoint, Classification, check_minimum_data

log = logging.getLogger("bistbull.ai.safety")


# ================================================================
# FORBIDDEN WORDS — output containing these gets rejected
# ================================================================
FORBIDDEN_WORDS = [
    "kesinlikle", "garanti", "garantili", "kaçırma", "kaçırmayın",
    "çok büyük fırsat", "şimdi alınmalı", "hemen al", "hemen sat",
    "patlayacak", "patlama", "uçacak", "uçuş", "roket",
    "trend başlıyor", "büyük kırılım", "tarihsel fırsat",
    "acil", "son şans", "mutlaka",
]

# Vague filler sentences that add no value
FILLER_PATTERNS = [
    r"piyasalarda karışık bir görünüm hakim",
    r"piyasalarda belirsizlik devam",
    r"yatırımcıların dikkatli olması önerilir",
    r"portföy çeşitlendirmesi yapılmalı",
    r"gelişmeler yakından takip edilmeli",
]

# Max lengths per role
MAX_LENGTHS = {
    "interpreter": 4,
    "risk_controller": 3,
    "action_coach": 4,
    "reality_checker": 3,
}

# Minimum trusted data points for AI to generate
MIN_TRUSTED_FOR_AI = 3


# ================================================================
# DETERMINISTIC FALLBACKS — when AI cannot speak
# ================================================================
FALLBACK_MESSAGES = {
    "interpreter": "Şu an yeterli piyasa verisi yok. Makro yorum üretmek doğru olmaz.",
    "risk_controller": "Veri yetersiz. Risk değerlendirmesi için daha fazla sinyal gerekiyor.",
    "action_coach": "Yeterli veri olmadan aksiyon önerisi vermek doğru olmaz. Veri güncellenince tekrar bak.",
    "reality_checker": "Kontrol için yeterli veri yok. Daha fazla sinyal gerekli.",
    "generic": "Şu an yeterli veri yok. Yorum yapmak doğru olmaz.",
}


# ================================================================
# AI INPUT BUILDER — structured, classified input for prompts
# ================================================================
def build_ai_input(
    datapoints: dict[str, DataPoint],
    regime: Optional[str] = None,
    confidence: Optional[str] = None,
    action_summary: Optional[str] = None,
    contradictions: Optional[list] = None,
) -> Optional[dict]:
    """
    Build a structured input dict for AI prompts.
    Only includes data that passes AI eligibility.
    Returns None if minimum data requirement is not met.
    """
    if not check_minimum_data(datapoints, MIN_TRUSTED_FOR_AI):
        return None

    signals = {}
    warnings = []

    for key, dp in datapoints.items():
        if not dp.safe_for_ai:
            continue
        entry = {
            "value": dp.value,
            "source": dp.source,
            "freshness": dp.freshness_label,
        }
        if dp.is_estimated:
            entry["warning"] = "tahmini veri"
            warnings.append(f"{key}: tahmini")
        if dp.is_editorial:
            entry["warning"] = "editöryal görüş"
        signals[key] = entry

    return {
        "signals": signals,
        "regime": regime,
        "confidence": confidence,
        "action_summary": action_summary,
        "contradictions": contradictions or [],
        "data_warnings": warnings,
        "trusted_count": sum(1 for dp in datapoints.values()
                            if dp.classification in (Classification.TRUSTED_DELAYED,
                                                      Classification.TRUSTED_PERIODIC)),
        "total_count": len(signals),
    }


# ================================================================
# AI OUTPUT VALIDATOR — hardened
# ================================================================
class AIValidationResult:
    def __init__(self, ok: bool, text: str, reason: str = ""):
        self.ok = ok
        self.text = text
        self.reason = reason


# Overconfidence markers — suspect when data quality is low
OVERCONFIDENCE_WORDS = [
    "açıkça", "net olarak", "şüphesiz", "kuşkusuz", "tartışmasız",
    "çok güçlü sinyal", "kuvvetli sinyal", "net yükseliş", "net düşüş",
]

# Jargon that doesn't help retail users
JARGON_WORDS = [
    "likidite daralması", "konjonktür", "monetize", "deleverage",
    "carry trade", "quantitative", "hawkish", "dovish",
    "yield curve inversion", "credit spread",
]

# Unsupported causal claims — AI inventing reasons
CAUSAL_PATTERNS = [
    r"bunun nedeni .{30,}",         # long causal chains AI invents
    r"arkasında .{30,} var",        # "behind this is..." speculation
    r"büyük oyuncular .{10,}",      # conspiracy-style claims
    r"manipülasyon",                 # unless it's from scoring guards
    r"içeriden .{5,} bilgi",        # insider info claims
]


def validate_ai_output(
    text: str,
    role: str = "generic",
    regime: Optional[str] = None,
    action_summary: Optional[str] = None,
    confidence: Optional[str] = None,
) -> AIValidationResult:
    """
    Validate AI output for safety, quality, and consistency.
    Returns AIValidationResult with ok=False if output should be rejected.
    """
    if not text or not text.strip():
        return AIValidationResult(False, "", "empty output")

    text_lower = text.lower().strip()

    # 1. Forbidden words (hype, manipulation)
    for word in FORBIDDEN_WORDS:
        if word in text_lower:
            return AIValidationResult(False, text, f"forbidden: '{word}'")

    # 2. Filler patterns (vague, useless)
    for pattern in FILLER_PATTERNS:
        if re.search(pattern, text_lower):
            return AIValidationResult(False, text, "filler pattern")

    # 3. Length check
    sentences = [s.strip() for s in re.split(r'[.!?→]\s+', text.strip()) if s.strip()]
    max_len = MAX_LENGTHS.get(role, 5)
    if len(sentences) > max_len + 1:
        return AIValidationResult(False, text, f"too long: {len(sentences)} vs max {max_len}")

    # 4. Regime contradiction (hardened)
    if regime == "RISK_OFF":
        bullish_phrases = ["fırsat", "alım yap", "güçlü alım", "agresif gir",
                           "yükseliş başla", "toparlanma sinyali", "dip seviye"]
        if role in ("action_coach", "interpreter") and any(w in text_lower for w in bullish_phrases):
            return AIValidationResult(False, text, f"contradicts RISK_OFF regime")

    if regime == "RISK_ON":
        bearish_phrases = ["çok tehlikeli", "büyük düşüş", "çökme riski", "panik"]
        if role == "interpreter" and any(w in text_lower for w in bearish_phrases):
            return AIValidationResult(False, text, f"contradicts RISK_ON regime")

    # 5. Overconfidence on weak data
    if confidence == "LOW":
        for word in OVERCONFIDENCE_WORDS:
            if word in text_lower:
                return AIValidationResult(False, text, f"overconfident for LOW data: '{word}'")

    # 6. Unsupported causal claims
    for pattern in CAUSAL_PATTERNS:
        if re.search(pattern, text_lower):
            return AIValidationResult(False, text, "unsupported causal claim")

    # 7. Jargon check (warn but don't hard-reject, just prefer re-generation)
    jargon_count = sum(1 for j in JARGON_WORDS if j in text_lower)
    if jargon_count >= 2:
        return AIValidationResult(False, text, f"too much jargon ({jargon_count} terms)")

    return AIValidationResult(True, text.strip())


# ================================================================
# SAFE AI GENERATION — with instrumentation
# ================================================================
class AIGenerationMeta:
    """Debug/instrumentation metadata for AI generation."""
    def __init__(self):
        self.attempts = 0
        self.passed_first_try = False
        self.retries_needed = 0
        self.used_fallback = False
        self.rejection_reasons: list[str] = []


def safe_ai_generate(
    prompt: str,
    role: str,
    regime: Optional[str] = None,
    action_summary: Optional[str] = None,
    ai_call_fn=None,
    max_tokens: int = 300,
    max_retries: int = 2,
    confidence: Optional[str] = None,
) -> str:
    """
    Generate AI output with safety checks + instrumentation.
    Retries on validation failure. Returns fallback if all retries fail.
    Prefers shorter valid output over long risky output.
    """
    meta = AIGenerationMeta()

    if ai_call_fn is None:
        meta.used_fallback = True
        return FALLBACK_MESSAGES.get(role, FALLBACK_MESSAGES["generic"])

    # Low-confidence data → go straight to deterministic fallback
    if confidence == "LOW":
        meta.used_fallback = True
        log.info(f"AI [{role}]: LOW confidence → deterministic fallback")
        return FALLBACK_MESSAGES.get(role, FALLBACK_MESSAGES["generic"])

    for attempt in range(max_retries):
        meta.attempts += 1
        try:
            # On retry, request shorter output to increase chance of passing
            tokens = max_tokens if attempt == 0 else max(150, max_tokens // 2)
            text = ai_call_fn(prompt, tokens)
            if not text:
                meta.rejection_reasons.append("empty")
                continue
            result = validate_ai_output(text, role, regime, action_summary, confidence)
            if result.ok:
                if attempt == 0:
                    meta.passed_first_try = True
                else:
                    meta.retries_needed = attempt
                return result.text
            else:
                meta.rejection_reasons.append(result.reason)
                log.info(f"AI [{role}] rejected (attempt {attempt+1}/{max_retries}): {result.reason}")
        except Exception as e:
            meta.rejection_reasons.append(str(e))
            log.warning(f"AI [{role}] error (attempt {attempt+1}): {e}")

    meta.used_fallback = True
    log.info(f"AI [{role}]: all {max_retries} attempts failed → fallback. Reasons: {meta.rejection_reasons}")
    return FALLBACK_MESSAGES.get(role, FALLBACK_MESSAGES["generic"])

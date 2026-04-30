# ================================================================
# BISTBULL TERMINAL — Phase 5.2.3 — AI Multi-Model Consensus
# engine/ai_consensus.py
#
# Sorumluluk: 4 AI provider'ı (Perplexity, Grok, OpenAI, Anthropic)
# paralel çağırır, çıkışları skorlar, lider modeli seçer ve uzlaşı
# metriklerini hesaplar.
#
# RULE 8 (KRİTİK):
#   - ai/prompts.py DOKUNULMAZ
#   - Bu modül `ai/prompts.py` içindeki mevcut prompt'ları çağırır,
#     prompt içeriğini ASLA değiştirmez
#   - Çağrılar `ai.engine._CALLERS` üzerinden yapılır
#   - prompt yapısı, dili, sıralaması, max_tokens hepsi olduğu gibi
#
# Determinism:
#   - Aynı 4 provider yanıtı için aynı consensus skoru üretilir
#   - Sentiment kelime listesi ve key-fact tokenizer dilden bağımsız
#     basit kurallar — neural net YOK (Rule: ML yasak)
# ================================================================

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Optional

log = logging.getLogger("bistbull.ai_consensus")


# ================================================================
# Sentiment & key-fact extraction — simple deterministic heuristics
# ================================================================

# Türkçe + İngilizce sentiment kelimeleri (small fixed lexicon — no ML)
_BULLISH_WORDS = (
    "yükseliş", "yukarı", "alım", "al ", "alın", "güçlü", "iyi", "olumlu",
    "pozitif", "büyüme", "kazanç", "kâr", "kar artış", "fırsat", "destek",
    "kırılım", "ralli", "yukari", "potansiyel",
    "bullish", "buy", "strong", "positive", "rally", "upside", "upgrade",
    "growth", "outperform", "long",
)
_BEARISH_WORDS = (
    "düşüş", "aşağı", "satış", "sat ", "satın", "zayıf", "kötü", "olumsuz",
    "negatif", "kayıp", "zarar", "risk", "tehlike", "kırılma", "düşüyor",
    "asagi", "baskı", "satıs",
    "bearish", "sell", "weak", "negative", "downside", "downgrade", "short",
    "underperform", "decline",
)

_NEUTRAL_HINT = ("nötr", "izle", "bekle", "neutral", "hold", "watch", "sideways")

# Token blacklist — common stop words that shouldn't count as "key facts"
_STOPWORDS = {
    "bir", "bu", "şu", "için", "ile", "ve", "veya", "ama", "fakat", "the", "a",
    "an", "of", "to", "is", "are", "in", "on", "at", "by", "for", "with", "as",
    "olan", "olarak", "kadar", "gibi", "her", "çok", "az", "daha",
}


def _normalize(s: str) -> str:
    """Lowercase + Turkish dotless-i flatten + strip extra whitespace."""
    if not s:
        return ""
    s = s.lower()
    s = (s.replace("İ", "i").replace("I", "ı")
           .replace("ı", "i")  # collapse ı→i for matching
           .replace("ş", "s").replace("ğ", "g").replace("ü", "u")
           .replace("ö", "o").replace("ç", "c"))
    return re.sub(r"\s+", " ", s).strip()


def classify_sentiment(text: str) -> str:
    """Return one of: 'bullish' | 'bearish' | 'neutral'.

    Counts occurrences of fixed sentiment words. Higher count wins.
    Tie or both zero → neutral.
    """
    if not text:
        return "neutral"
    norm = _normalize(text)
    bull = sum(norm.count(_normalize(w).strip()) for w in _BULLISH_WORDS)
    bear = sum(norm.count(_normalize(w).strip()) for w in _BEARISH_WORDS)
    # Boost neutral hints slightly
    neut_boost = sum(norm.count(_normalize(w).strip()) for w in _NEUTRAL_HINT)

    if bull > bear and bull > neut_boost:
        return "bullish"
    if bear > bull and bear > neut_boost:
        return "bearish"
    return "neutral"


def extract_keywords(text: str, top_n: int = 12) -> set[str]:
    """Pull 4-15 char alphanumeric tokens, drop stopwords.

    Deterministic: same text → same set (set ordering doesn't matter
    for our Jaccard math)."""
    if not text:
        return set()
    norm = _normalize(text)
    tokens = re.findall(r"[a-z0-9çğıöşü]{4,15}", norm)
    out = set()
    counts: dict[str, int] = {}
    for t in tokens:
        if t in _STOPWORDS:
            continue
        counts[t] = counts.get(t, 0) + 1
    # top-N most frequent (stable: alphabetical break ties)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    out = {k for k, _ in ranked}
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    """Standard Jaccard similarity. Empty ∩ empty → 0.0."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


# ================================================================
# Per-response confidence — 0..1, deterministic
# ================================================================
def estimate_confidence(text: str, declared: Optional[float] = None) -> float:
    """If the provider declared a confidence, prefer it (clamped 0..1).
    Otherwise estimate from text characteristics:
      - length (very short = low confidence)
      - decisive sentiment ratio (one-sided > neutral)
    """
    if declared is not None:
        try:
            v = float(declared)
            # Negative → clamp to 0
            if v < 0:
                return 0.0
            # 0..1 — use as fraction
            if v <= 1.0:
                return v
            # 1..100 — treat as percentage
            if v <= 100.0:
                return v / 100.0
            # >100 — out of range, clamp
            return 1.0
        except Exception:
            pass

    if not text:
        return 0.0
    n = len(text.strip())
    # length factor: 0..1 maxing out around 400 chars
    length_factor = min(1.0, n / 400.0)
    # decisiveness: |bull - bear| / max(1, bull+bear)
    norm = _normalize(text)
    bull = sum(norm.count(_normalize(w).strip()) for w in _BULLISH_WORDS)
    bear = sum(norm.count(_normalize(w).strip()) for w in _BEARISH_WORDS)
    if bull + bear == 0:
        decisive = 0.4  # neutral/ambivalent baseline
    else:
        decisive = abs(bull - bear) / (bull + bear)
    # weighted blend
    return round(0.35 * length_factor + 0.65 * decisive, 3)


# ================================================================
# Aggregation — public API
# ================================================================
def compute_consensus(responses: list[dict]) -> dict:
    """Aggregate 1..N model responses into a consensus payload.

    Args:
        responses: list of dicts, each with:
            - provider: str  (e.g. 'perplexity')
            - text: str | None
            - confidence: Optional[float]  (if provider self-reports)
            - error: Optional[str]
    Returns dict:
        - leader: provider name of the consensus leader (or None)
        - leader_text: leader's response text
        - sentiment: 'bullish' | 'bearish' | 'neutral' | 'split'
        - sentiment_distribution: {'bullish': n, 'neutral': n, 'bearish': n}
        - agreement_score: 0..1 (higher = models agree)
        - keyword_overlap: 0..1 (mean pairwise jaccard)
        - is_split: bool — True if no clear majority
        - per_model: list of {provider, sentiment, confidence, score, has_error}
    """
    valid = [r for r in (responses or []) if r and r.get("text") and not r.get("error")]
    if not valid:
        return {
            "leader": None,
            "leader_text": None,
            "sentiment": "neutral",
            "sentiment_distribution": {"bullish": 0, "neutral": 0, "bearish": 0},
            "agreement_score": 0.0,
            "keyword_overlap": 0.0,
            "is_split": False,
            "model_count": 0,
            "per_model": [
                {
                    "provider": r.get("provider", "unknown"),
                    "sentiment": "neutral",
                    "confidence": 0.0,
                    "score": 0.0,
                    "has_error": True,
                    "error": r.get("error"),
                }
                for r in (responses or [])
            ],
        }

    # Per-model sentiment + keywords + confidence
    per_model_data = []
    for r in valid:
        sent = classify_sentiment(r["text"])
        kws = extract_keywords(r["text"])
        conf = estimate_confidence(r["text"], r.get("confidence"))
        per_model_data.append({
            "provider": r.get("provider", "unknown"),
            "text": r["text"],
            "sentiment": sent,
            "keywords": kws,
            "confidence": conf,
        })

    # Sentiment distribution
    dist = {"bullish": 0, "neutral": 0, "bearish": 0}
    for d in per_model_data:
        dist[d["sentiment"]] += 1

    n = len(per_model_data)
    max_count = max(dist.values())
    is_split = max_count <= n / 2 and n >= 2  # no strict majority

    # Overall sentiment
    if is_split:
        overall = "split"
    else:
        overall = max(dist.items(), key=lambda kv: kv[1])[0]

    # Keyword overlap — mean pairwise jaccard across all pairs
    pairs = []
    for i in range(len(per_model_data)):
        for j in range(i + 1, len(per_model_data)):
            pairs.append(jaccard(per_model_data[i]["keywords"], per_model_data[j]["keywords"]))
    keyword_overlap = sum(pairs) / len(pairs) if pairs else 1.0

    # Sentiment agreement: fraction of models matching the majority sentiment
    if is_split:
        sentiment_agreement = max_count / n
    else:
        sentiment_agreement = max_count / n

    # Composite agreement score: weighted blend
    agreement_score = round(0.6 * sentiment_agreement + 0.4 * keyword_overlap, 3)

    # Leader selection: highest (confidence + agreement bonus)
    # Models matching the majority sentiment get a +0.1 bonus
    def _score(d: dict) -> float:
        bonus = 0.1 if (not is_split and d["sentiment"] == overall) else 0.0
        return d["confidence"] + bonus

    leader_data = max(per_model_data, key=_score)
    leader_score = _score(leader_data)

    per_model_out = []
    for d in per_model_data:
        per_model_out.append({
            "provider": d["provider"],
            "sentiment": d["sentiment"],
            "confidence": d["confidence"],
            "score": round(_score(d), 3),
            "has_error": False,
        })
    # Append errored models for visibility
    for r in (responses or []):
        if not r or r in valid:
            continue
        per_model_out.append({
            "provider": (r or {}).get("provider", "unknown"),
            "sentiment": "neutral",
            "confidence": 0.0,
            "score": 0.0,
            "has_error": True,
            "error": (r or {}).get("error"),
        })

    return {
        "leader": leader_data["provider"],
        "leader_text": leader_data["text"],
        "leader_score": round(leader_score, 3),
        "sentiment": overall,
        "sentiment_distribution": dist,
        "agreement_score": agreement_score,
        "keyword_overlap": round(keyword_overlap, 3),
        "is_split": is_split,
        "per_model": per_model_out,
        "model_count": n,
    }


# ================================================================
# Parallel provider invocation
# ================================================================
def call_all_providers(
    prompt: str,
    max_tokens: int = 220,
    timeout_s: float = 18.0,
    providers: Optional[list[str]] = None,
) -> list[dict]:
    """Call every available provider IN PARALLEL with the same prompt.

    Returns a list of {provider, text, error?} dicts in stable order
    (alphabetical by provider name) for downstream determinism.

    RULE 8: This function does NOT modify the prompt. It is the
    caller's responsibility to build the prompt via ai/prompts.py
    and pass it through unchanged.
    """
    # Late import: ai/engine has heavy import side-effects (config, redis)
    try:
        from ai.engine import _CALLERS, AI_PROVIDERS
    except Exception as e:
        log.warning(f"ai_consensus: ai.engine import failed: {e}")
        return []

    target = providers or list(AI_PROVIDERS)
    target = [p for p in target if p in _CALLERS]
    if not target:
        return []

    def _one(provider: str) -> dict:
        try:
            caller = _CALLERS[provider]
            text = caller(prompt, max_tokens)
            if not text:
                return {"provider": provider, "text": None, "error": "empty_response"}
            return {"provider": provider, "text": text}
        except Exception as e:
            return {"provider": provider, "text": None, "error": str(e)[:160]}

    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, len(target))) as ex:
        futures = {ex.submit(_one, p): p for p in target}
        for fut, prov in futures.items():
            try:
                out.append(fut.result(timeout=timeout_s))
            except FuturesTimeoutError:
                out.append({"provider": prov, "text": None, "error": "timeout"})
            except Exception as e:  # pragma: no cover
                out.append({"provider": prov, "text": None, "error": str(e)[:160]})

    # Deterministic ordering for downstream consumers
    out.sort(key=lambda r: r.get("provider", "zzz"))
    return out

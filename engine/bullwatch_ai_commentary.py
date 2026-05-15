# ================================================================
# BISTBULL TERMINAL — BULLWATCH CONVICTION AI COMMENTARY
# engine/bullwatch_ai_commentary.py
#
# Faz 4. Generates a 3-4 sentence Turkish narrative for a single
# BullWatch CONVICTION-tier ticker. Composes context from the current
# in-memory BullWatch snapshot (engine grouping, reasons, tahtacı
# signals, sector rotation, KAP highlights) and asks the AI to weave
# them into a human story.
#
# Why CONVICTION-only:
#   Only the top-zone tickers are worth burning AI tokens on. EARLY /
#   CONFIRMED zones already have programmatic narratives from
#   bullwatch_explainability.py.
#
# Caching:
#   Per-ticker LRU keyed on (symbol, score-bucket). Score-bucket
#   rounds to the nearest 5 so a fresh commentary isn't regenerated
#   for a 0.3-point score wiggle.
# ================================================================

from __future__ import annotations

import logging
import time
from typing import Any, Optional

log = logging.getLogger("bistbull.bw_ai_commentary")


# In-memory commentary cache: {(symbol, score_bucket) -> (text, ts)}
_CACHE: dict[tuple[str, int], tuple[str, float]] = {}
_CACHE_TTL_SEC = 6 * 3600   # 6 hours — commentary stays fresh
_CACHE_MAX_ENTRIES = 200    # bounded so a misbehaving caller can't grow it forever


def _score_bucket(score: Optional[float]) -> int:
    """Round score to nearest 5 so 75.0/75.3/77.2 all hit the same
    cache entry. Avoids re-spending AI tokens on minor wiggles."""
    if score is None:
        return 0
    try:
        return int(round(float(score) / 5.0) * 5)
    except (ValueError, TypeError):
        return 0


def _cache_get(symbol: str, score: Optional[float]) -> Optional[str]:
    key = ((symbol or "").upper(), _score_bucket(score))
    rec = _CACHE.get(key)
    if rec is None:
        return None
    text, ts = rec
    if time.time() - ts > _CACHE_TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return text


def _cache_set(symbol: str, score: Optional[float], text: str) -> None:
    if not text:
        return
    key = ((symbol or "").upper(), _score_bucket(score))
    _CACHE[key] = (text, time.time())
    # Bounded LRU-ish: drop oldest if we're over the cap
    if len(_CACHE) > _CACHE_MAX_ENTRIES:
        oldest_key = min(_CACHE, key=lambda k: _CACHE[k][1])
        _CACHE.pop(oldest_key, None)


def cache_stats() -> dict[str, Any]:
    return {
        "entries": len(_CACHE),
        "max_entries": _CACHE_MAX_ENTRIES,
        "ttl_sec": _CACHE_TTL_SEC,
    }


def clear_cache() -> None:
    _CACHE.clear()


# ────────────────────────────────────────────────────────────────
# Prompt builder
# ────────────────────────────────────────────────────────────────


def build_commentary_prompt(item: dict[str, Any]) -> str:
    """Compose the Turkish AI prompt from a BullWatch item payload.

    `item` follows the BullWatchResult dict shape: symbol, score, zone,
    pattern, reasons, components, metrics, narrative, explainability...
    """
    symbol = item.get("symbol") or "?"
    score = item.get("score") or 0
    zone = item.get("zone") or "?"
    pattern = item.get("pattern") or ""
    reasons = (item.get("reasons") or [])[:5]
    components = item.get("components") or {}
    metrics = item.get("metrics") or {}
    sector = metrics.get("sector") or "?"

    # Engine grouping (from explainability layer) — already digested
    # into 3 buckets: float/squeeze, tahtacı, fundamental.
    explainability = item.get("explainability") or {}
    engine_groups = explainability.get("engine_groups") or {}
    tahtaci_strength = explainability.get("tahtaci_strength")

    def _fmt_pct(v, d=1):
        if v is None:
            return "?"
        try:
            return f"{float(v) * 100:.{d}f}%"
        except Exception:
            return "?"

    def _fmt_num(v):
        if v is None:
            return "?"
        try:
            v = float(v)
            if abs(v) >= 1e9:
                return f"{v/1e9:.1f}B"
            if abs(v) >= 1e6:
                return f"{v/1e6:.1f}M"
            return f"{v:,.0f}"
        except Exception:
            return "?"

    # Top 3 component contributions (descending)
    sorted_comps = sorted(
        ((k, v) for k, v in components.items() if isinstance(v, (int, float))),
        key=lambda kv: kv[1] or 0,
        reverse=True,
    )[:3]
    top_components_str = "\n  ".join(
        f"- {k}: {v:.1f}" for k, v in sorted_comps
    ) or "  (component breakdown yok)"

    reasons_str = "\n  ".join(f"- {r}" for r in reasons) or "  (reason yok)"

    prompt = f"""
Aşağıdaki BullWatch CONVICTION sinyali için 3-4 cümle Türkçe yorum yaz.
Spekülatif alım/satım önerme; sadece "neden bu skoru hakkı?" sorusunu cevapla.
Dil: yatırımcı odaklı, kısa, somut.

HİSSE: {symbol}
SEKTÖR: {sector}
SKOR: {score:.0f} (zone: {zone})
PATTERN: {pattern}
TAHTACI GÜCÜ: {tahtaci_strength if tahtaci_strength is not None else '?'}

EN GÜÇLÜ KOMPONENTLER:
  {top_components_str}

BULLWATCH GEREKÇELERİ (system-generated):
  {reasons_str}

TEMEL METRİKLER:
  Piyasa değeri: {_fmt_num(metrics.get('market_cap'))} TL
  Free float: {_fmt_pct(metrics.get('free_float'))}
  20g ortalama hacim: {_fmt_num(metrics.get('avg_traded_value_20d'))} TL
  Son 20g getiri: {_fmt_pct(metrics.get('return_20d'))}
  ROE: {_fmt_pct(metrics.get('roe'))}
  F/K: {metrics.get('pe') if metrics.get('pe') is not None else '?'}

KISITLAR:
- 3-4 cümleyi geçme
- Liste / başlık / madde işareti kullanma — düz prose
- "Hisse alın / satın" yazma; gerekçeyi açıkla
- Çıktı: yalın Türkçe, sayısal referans ver
"""
    return prompt.strip()


# ────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────


def generate_commentary(item: dict[str, Any]) -> Optional[str]:
    """Generate or return cached AI commentary for a CONVICTION ticker.

    Returns None if:
      - item is not CONVICTION-zone
      - AI provider unavailable
      - AI call failed or output rejected by safety layer

    Cache: keyed on (symbol, rounded-score-bucket). 6-hour TTL.
    """
    if not item:
        return None
    zone = (item.get("zone") or "").upper()
    if zone != "CONVICTION":
        # Programmatic explainability is enough for sub-CONVICTION;
        # AI tokens only on the highest-confidence tier.
        return None
    symbol = item.get("symbol")
    if not symbol:
        return None

    cached = _cache_get(symbol, item.get("score"))
    if cached is not None:
        return cached

    try:
        from ai.service import AI_AVAILABLE, ai_call
        from ai.safety import validate_ai_output
    except Exception as exc:
        log.debug("AI imports unavailable: %r", exc)
        return None

    if not AI_AVAILABLE:
        return None

    try:
        prompt = build_commentary_prompt(item)
        raw = ai_call(prompt, max_tokens=350)
        if not raw:
            return None
        result = validate_ai_output(raw, "interpreter")
        if not result.ok:
            log.info(
                "BullWatch AI commentary rejected for %s: %s",
                symbol, getattr(result, "reason", "?"),
            )
            return None
        text = result.text.strip()
        _cache_set(symbol, item.get("score"), text)
        return text
    except Exception as exc:
        log.warning("BullWatch AI commentary failed for %s: %r", symbol, exc)
        return None


def lookup_item_from_cache(symbol: str) -> Optional[dict]:
    """Helper for the endpoint: pull the latest BullWatch item for
    `symbol` from the in-memory snapshot."""
    sym = (symbol or "").upper()
    if not sym:
        return None
    try:
        from api.bullwatch import _CACHE as _BW_CACHE
    except Exception:
        return None
    items = ((_BW_CACHE.get("items") or {}).get("items")) or []
    for it in items:
        if (it.get("symbol") or "").upper() == sym:
            return it
    return None

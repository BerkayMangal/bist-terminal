# ================================================================
# BISTBULL TERMINAL — PERPLEXITY SONAR INTEGRATION
# ai/perplexity.py
#
# Uses Perplexity Sonar API for:
# 1. External market brief (harici piyasa özeti)
# 2. Live CDS/yield data search
# 3. Stock-specific news context
#
# ENV: PERPLEXITY_API_KEY
# ================================================================

from __future__ import annotations
import os, json, logging, time
from typing import Optional

log = logging.getLogger("bistbull.perplexity")

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
PERPLEXITY_MODEL = "sonar"  # or "sonar-pro" for deeper search
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_AVAILABLE = bool(PERPLEXITY_API_KEY)

# Simple in-memory cache (TTL-based)
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 900  # 15 minutes


def _cached(key: str) -> Optional[dict]:
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return val
    return None


def _set_cache(key: str, val: dict):
    _cache[key] = (time.time(), val)


def _call_perplexity(system: str, user: str, max_tokens: int = 500) -> Optional[str]:
    """Call Perplexity Sonar API. Returns text or None."""
    if not PERPLEXITY_AVAILABLE:
        return None
    try:
        import urllib.request
        body = json.dumps({
            "model": PERPLEXITY_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(
            PERPLEXITY_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"]
            citations = data.get("citations", [])
            return text
    except Exception as e:
        log.warning(f"Perplexity call failed: {e}")
        return None


# ================================================================
# 1. EXTERNAL MARKET BRIEF
# ================================================================
def fetch_external_brief() -> dict:
    """Fetch latest market brief via Perplexity web search.
    NOT part of decision engine. Labeled as external."""
    cached = _cached("external_brief")
    if cached:
        return cached

    if not PERPLEXITY_AVAILABLE:
        return {"available": False, "brief": None, "reason": "PERPLEXITY_API_KEY not set"}

    system = (
        "Sen bir Türk piyasa analisti asistanısın. "
        "Güncel piyasa durumunu 4-5 cümlede özetle. Türkçe yaz. "
        "Somut rakamlar kullan (endeks seviyeleri, döviz, petrol, vb). "
        "Kaynak belirt. Hype yapma."
    )
    user = (
        "Bugün BIST 100, USD/TRY, Brent petrol, altın ve küresel piyasalarda "
        "ne oluyor? Güncel rakamlarla kısa özet ver."
    )

    text = _call_perplexity(system, user, 400)
    if text:
        result = {
            "available": True,
            "brief": text,
            "source": "Perplexity Sonar (web search)",
            "feeds_decision": False,
            "feeds_regime": False,
            "label": "Harici Piyasa Özeti",
            "disclaimer": "Bu bölüm karar motorunu ETKİLEMEZ. Sadece ek bağlam sağlar.",
        }
        _set_cache("external_brief", result)
        return result

    return {"available": False, "brief": None, "reason": "Perplexity yanıt vermedi"}


# ================================================================
# 2. LIVE CDS/YIELD SEARCH
# ================================================================
def search_cds_data() -> dict:
    """Search for latest Turkey 5Y CDS spread via web.
    Returns estimated value — still classified as 'estimated'."""
    cached = _cached("cds_search")
    if cached:
        return cached

    if not PERPLEXITY_AVAILABLE:
        return {"found": False}

    system = (
        "Sen bir finansal veri asistanısın. Sadece somut rakam ver, açıklama yapma. "
        "JSON formatında yanıt ver."
    )
    user = (
        "Turkey 5 year CDS spread bugün kaç bps? "
        'Sadece şu JSON formatında cevap ver: {"cds_bps": 295, "source": "kaynak adı", "date": "2026-04-11"}'
    )

    text = _call_perplexity(system, user, 150)
    if text:
        try:
            # Try to parse JSON from response
            cleaned = text.strip()
            if "```" in cleaned:
                cleaned = cleaned.split("```")[1].replace("json", "").strip()
            data = json.loads(cleaned)
            if "cds_bps" in data:
                result = {
                    "found": True,
                    "cds_bps": float(data["cds_bps"]),
                    "source": data.get("source", "Perplexity"),
                    "date": data.get("date", ""),
                    "classification": "estimated",  # still estimated — web search not authoritative
                }
                _set_cache("cds_search", result)
                return result
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.warning(f"CDS parse failed: {e}, raw: {text[:200]}")

    return {"found": False}


# ================================================================
# 3. STOCK NEWS CONTEXT
# ================================================================
def fetch_stock_news(ticker: str) -> dict:
    """Fetch latest news about a BIST stock. For AI context only."""
    cache_key = f"stock_news_{ticker}"
    cached = _cached(cache_key)
    if cached:
        return cached

    if not PERPLEXITY_AVAILABLE:
        return {"available": False, "news": None}

    clean_ticker = ticker.replace(".IS", "").upper()
    system = (
        "Sen bir Türk borsa haber analisti asistanısın. "
        "Son 1 haftanın en önemli 2-3 gelişmesini kısa özetle. Türkçe yaz. "
        "Söylenti değil, doğrulanmış haberler. Kaynak belirt."
    )
    user = f"BIST hissesi {clean_ticker} hakkında son 1 haftanın önemli haberleri neler?"

    text = _call_perplexity(system, user, 300)
    if text:
        result = {
            "available": True,
            "news": text,
            "ticker": clean_ticker,
            "source": "Perplexity Sonar",
            "classification": "ai_generated",
            "feeds_decision": False,
        }
        _set_cache(cache_key, result)
        return result

    return {"available": False, "news": None}


# ================================================================
# 4. COMPARE CONTEXT ENRICHMENT
# ================================================================
def fetch_compare_context(left: str, right: str) -> Optional[str]:
    """Fetch brief comparison context from web for two BIST stocks."""
    cache_key = f"compare_{left}_{right}"
    cached = _cached(cache_key)
    if cached:
        return cached.get("context")

    if not PERPLEXITY_AVAILABLE:
        return None

    lt = left.replace(".IS", "").upper()
    rt = right.replace(".IS", "").upper()
    system = (
        "Sen Türk borsa analisti asistanısın. Kısa ve somut yaz. Türkçe. "
        "Sadece son 1 ayın önemli gelişmelerini karşılaştır."
    )
    user = (
        f"BIST'te {lt} vs {rt} karşılaştırması: "
        f"Son 1 ayda bu iki hissede öne çıkan gelişmeler neler? "
        f"Max 3 cümle, somut bilgi."
    )

    text = _call_perplexity(system, user, 250)
    if text:
        _set_cache(cache_key, {"context": text})
        return text
    return None

# ================================================================
# BISTBULL TERMINAL — AI ENGINE (core)
# ai/engine.py
#
# Low-level AI provider chain with circuit breaker fallback.
# Grok → OpenAI → Anthropic. Uses singleton clients from ai/clients.py.
#
# This module provides ONE public function: ai_call(prompt, max_tokens)
# All higher-level AI features live in ai/service.py.
# ================================================================

from __future__ import annotations

import logging
from typing import Optional

from config import (
    GROK_MODEL, OPENAI_MODEL, ANTHROPIC_MODEL, PERPLEXITY_MODEL,
)
from core.circuit_breaker import cb_grok, cb_openai, cb_anthropic, cb_perplexity, CircuitBreakerOpen
from ai.clients import get_grok_client, get_openai_client, get_anthropic_client, get_perplexity_client

log = logging.getLogger("bistbull.ai")

# ================================================================
# PROVIDER DISCOVERY
#
# AI Quality Overhaul (2026-05): the call order is now driven by
# config.AI_PRIMARY_PROVIDER (default "anthropic"). The primary is
# tried FIRST; the rest stay in the list as a dormant fallback chain
# so the system still degrades gracefully if the primary key lapses.
# In practice, with a funded Claude key, only the primary ever fires.
# ================================================================
_DISCOVERED: list[str] = []

try:
    from openai import OpenAI as _OpenAI
    from config import PERPLEXITY_KEY, GROK_KEY, OPENAI_KEY
    if PERPLEXITY_KEY:
        _DISCOVERED.append("perplexity")
    if GROK_KEY:
        _DISCOVERED.append("grok")
    if OPENAI_KEY:
        _DISCOVERED.append("openai")
except ImportError:
    _OpenAI = None  # type: ignore

try:
    import anthropic as _anthropic_mod
    from config import ANTHROPIC_KEY
    if ANTHROPIC_KEY:
        _DISCOVERED.append("anthropic")
except ImportError:
    _anthropic_mod = None  # type: ignore


def _ordered_providers(discovered: list[str]) -> list[str]:
    """Put the configured primary provider first, keep the rest as
    fallback. Unknown primary → discovered order unchanged."""
    try:
        from config import AI_PRIMARY_PROVIDER as _primary
    except Exception:
        _primary = "anthropic"
    if _primary in discovered:
        return [_primary] + [p for p in discovered if p != _primary]
    return list(discovered)


# AI Consolidation (2026-05): Claude is the ONLY provider. Even if a
# stale GROK / OPENAI / PERPLEXITY key lingers in the environment we do
# NOT call them — their credit is gone and their Turkish financial
# output was lower quality. _ordered_providers / _DISCOVERED stay in
# place (dormant) so a future multi-provider revival is a one-line
# change, but the live list is hard-pinned to anthropic.
_ANTHROPIC_LIVE = "anthropic" in _DISCOVERED
AI_PROVIDERS: list[str] = ["anthropic"] if _ANTHROPIC_LIVE else []
AI_AVAILABLE: bool = len(AI_PROVIDERS) > 0


# ================================================================
# LOW-LEVEL CALLERS
# ================================================================
def _call_perplexity(prompt: str, max_tokens: int) -> str:
    cb_perplexity.before_call()
    try:
        client = get_perplexity_client()
        if client is None:
            raise RuntimeError("Perplexity client not available")
        resp = client.chat.completions.create(
            model=PERPLEXITY_MODEL, max_tokens=max_tokens, temperature=__import__("config").V13_AI_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        result = resp.choices[0].message.content.strip()
        cb_perplexity.on_success()
        return result
    except CircuitBreakerOpen:
        raise
    except Exception as e:
        cb_perplexity.on_failure(e)
        raise
def _call_grok(prompt: str, max_tokens: int) -> str:
    cb_grok.before_call()
    try:
        client = get_grok_client()
        if client is None:
            raise RuntimeError("Grok client not available")
        resp = client.chat.completions.create(
            model=GROK_MODEL, max_tokens=max_tokens, temperature=__import__("config").V13_AI_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        result = resp.choices[0].message.content.strip()
        cb_grok.on_success()
        return result
    except CircuitBreakerOpen:
        raise
    except Exception as e:
        cb_grok.on_failure(e)
        raise


def _call_openai(prompt: str, max_tokens: int) -> str:
    cb_openai.before_call()
    try:
        client = get_openai_client()
        if client is None:
            raise RuntimeError("OpenAI client not available")
        resp = client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=max_tokens, temperature=__import__("config").V13_AI_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        result = resp.choices[0].message.content.strip()
        cb_openai.on_success()
        return result
    except CircuitBreakerOpen:
        raise
    except Exception as e:
        cb_openai.on_failure(e)
        raise


def _call_anthropic(prompt: str, max_tokens: int) -> str:
    cb_anthropic.before_call()
    try:
        client = get_anthropic_client()
        if client is None:
            raise RuntimeError("Anthropic client not available")
        resp = client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=max_tokens,
            temperature=__import__("config").V13_AI_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        result = resp.content[0].text.strip()
        cb_anthropic.on_success()
        return result
    except CircuitBreakerOpen:
        raise
    except Exception as e:
        cb_anthropic.on_failure(e)
        raise


_CALLERS = {
    "perplexity": _call_perplexity,
    "grok": _call_grok,
    "openai": _call_openai,
    "anthropic": _call_anthropic,
}


# Phase A.10 Step 2-B: throttle quota-exhausted log spam. The CB layer
# already trips fast and logs once at OPEN; this set ensures the
# "AI <provider> failed: ..." warning at this layer also fires once.
_QUOTA_LOGGED: set[str] = set()


# ── AI telemetry (AI Quality Overhaul) ──────────────────────────
# Lightweight in-memory record of the last N AI calls so /api/diag
# can answer "is the AI healthy / which provider served the last
# request / how slow was it". Bounded ring buffer — never grows.
import time as _time
import threading as _threading

_TELEMETRY_LOCK = _threading.Lock()
_TELEMETRY_MAX = 50
_TELEMETRY: list[dict] = []
_TELEMETRY_TOTALS: dict[str, int] = {"calls": 0, "ok": 0, "fail": 0}


def _record_call(provider: str, model: str, ok: bool,
                 latency_ms: float, error: str = "") -> None:
    """Append one call to the telemetry ring buffer (thread-safe)."""
    with _TELEMETRY_LOCK:
        _TELEMETRY_TOTALS["calls"] += 1
        _TELEMETRY_TOTALS["ok" if ok else "fail"] += 1
        _TELEMETRY.append({
            "ts": _time.time(),
            "provider": provider,
            "model": model,
            "ok": ok,
            "latency_ms": round(latency_ms, 1),
            "error": error[:200] if error else "",
        })
        if len(_TELEMETRY) > _TELEMETRY_MAX:
            del _TELEMETRY[0]


def get_ai_telemetry() -> dict:
    """Snapshot of AI call telemetry for /api/diag/ai-status."""
    with _TELEMETRY_LOCK:
        recent = list(_TELEMETRY[-15:])
        totals = dict(_TELEMETRY_TOTALS)
    last = recent[-1] if recent else None
    return {
        "providers_configured": list(AI_PROVIDERS),
        "primary": AI_PROVIDERS[0] if AI_PROVIDERS else None,
        "ai_available": AI_AVAILABLE,
        "totals": totals,
        "success_rate": (
            round(totals["ok"] / totals["calls"] * 100, 1)
            if totals["calls"] else None
        ),
        "last_call": last,
        "recent_calls": recent,
        "quota_exhausted": sorted(_QUOTA_LOGGED),
    }


_MODEL_BY_PROVIDER = {
    "perplexity": PERPLEXITY_MODEL,
    "grok": GROK_MODEL,
    "openai": OPENAI_MODEL,
    "anthropic": ANTHROPIC_MODEL,
}


# ================================================================
# PUBLIC API — single entry point for all AI calls
# ================================================================
def ai_call(prompt: str, max_tokens: int = 600) -> Optional[str]:
    """Run an AI completion. Tries each provider in AI_PROVIDERS order
    (primary first — see _ordered_providers). CB-OPEN providers are
    skipped silently.

    AI Quality Overhaul (2026-05):
      - Default max_tokens raised 200 → 600. The old 200-token ceiling
        truncated multi-sentence Turkish commentary mid-thought, which
        was a major source of "saçma sapan" output.
      - Every attempt is recorded in the telemetry ring buffer so
        /api/diag/ai-status can show provider health.
    """
    for provider in AI_PROVIDERS:
        caller = _CALLERS.get(provider)
        if not caller:
            continue
        model = _MODEL_BY_PROVIDER.get(provider, "?")
        t0 = _time.time()
        try:
            result = caller(prompt, max_tokens)
            _record_call(provider, model, True,
                         (_time.time() - t0) * 1000.0)
            return result
        except CircuitBreakerOpen:
            log.info(f"AI {provider} CB OPEN — skip to next")
            _record_call(provider, model, False,
                         (_time.time() - t0) * 1000.0, "circuit_breaker_open")
            continue
        except Exception as e:
            err_lower = str(e).lower()
            is_quota = any(s in err_lower for s in (
                "insufficient_quota",
                "exceeded your current quota",
                "all available credits",
                "spending limit",
                "quota exhausted",
            ))
            if is_quota:
                # Log ONCE per provider per process — the CB trip log
                # already covers the fact; subsequent failures silent.
                if provider not in _QUOTA_LOGGED:
                    log.warning(
                        f"AI {provider} disabled: quota/credits exhausted "
                        f"(suppressing further failure logs from this provider)"
                    )
                    _QUOTA_LOGGED.add(provider)
            else:
                log.warning(f"AI {provider} failed: {e}")
            _record_call(provider, model, False,
                         (_time.time() - t0) * 1000.0, str(e))
            continue
    return None

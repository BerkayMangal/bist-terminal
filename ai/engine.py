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
    GROK_MODEL, OPENAI_MODEL, ANTHROPIC_MODEL,
)
from core.circuit_breaker import cb_grok, cb_openai, cb_anthropic, CircuitBreakerOpen
from ai.clients import get_grok_client, get_openai_client, get_anthropic_client

log = logging.getLogger("bistbull.ai")

# ================================================================
# PROVIDER DISCOVERY
# ================================================================
AI_PROVIDERS: list[str] = []

try:
    from openai import OpenAI as _OpenAI
    from config import GROK_KEY, OPENAI_KEY
    if GROK_KEY:
        AI_PROVIDERS.append("grok")
    if OPENAI_KEY:
        AI_PROVIDERS.append("openai")
except ImportError:
    _OpenAI = None  # type: ignore

try:
    import anthropic as _anthropic_mod
    from config import ANTHROPIC_KEY
    if ANTHROPIC_KEY:
        AI_PROVIDERS.append("anthropic")
except ImportError:
    _anthropic_mod = None  # type: ignore

AI_AVAILABLE: bool = len(AI_PROVIDERS) > 0


# ================================================================
# LOW-LEVEL CALLERS — singleton clients + circuit breakers
# ================================================================
def _call_grok(prompt: str, max_tokens: int) -> str:
    cb_grok.before_call()
    try:
        client = get_grok_client()
        if client is None:
            raise RuntimeError("Grok client not available")
        resp = client.chat.completions.create(
            model=GROK_MODEL, max_tokens=max_tokens, temperature=0.4,
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
            model=OPENAI_MODEL, max_tokens=max_tokens, temperature=0.4,
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
    "grok": _call_grok,
    "openai": _call_openai,
    "anthropic": _call_anthropic,
}


# ================================================================
# PUBLIC API — single entry point for all AI calls
# ================================================================
def ai_call(prompt: str, max_tokens: int = 200) -> Optional[str]:
    """Try each AI provider in order: Grok → OpenAI → Anthropic.
    CB OPEN olan provider atlanır — sessiz fallback."""
    for provider in AI_PROVIDERS:
        try:
            caller = _CALLERS.get(provider)
            if caller:
                return caller(prompt, max_tokens)
        except CircuitBreakerOpen:
            log.info(f"AI {provider} CB OPEN — skip to next")
            continue
        except Exception as e:
            log.warning(f"AI {provider} failed: {e}")
            continue
    return None

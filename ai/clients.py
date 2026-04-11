# ================================================================
# BISTBULL TERMINAL — AI CLIENT POOL
# ai/clients.py
#
# Singleton HTTP clients for each AI provider.
# Created once at module load, reused on every call.
#
# Previous behavior: _OpenAI() and Anthropic() instantiated per-call
# → new TCP connection + TLS handshake every time.
# New behavior: one client per provider, connection pooling automatic.
# ================================================================

from __future__ import annotations

import logging
from typing import Optional

from config import (
    GROK_KEY, OPENAI_KEY, ANTHROPIC_KEY, PERPLEXITY_KEY,
)

log = logging.getLogger("bistbull.ai.clients")

# ================================================================
# SINGLETON CLIENTS — created once, reused everywhere
# ================================================================
_grok_client = None
_openai_client = None
_anthropic_client = None
_perplexity_client = None


def get_perplexity_client():
    """Lazy singleton for Perplexity Sonar client (OpenAI-compatible)."""
    global _perplexity_client
    if _perplexity_client is None and PERPLEXITY_KEY:
        try:
            from openai import OpenAI
            _perplexity_client = OpenAI(
                api_key=PERPLEXITY_KEY,
                base_url="https://api.perplexity.ai",
            )
            log.info("Perplexity client initialized (singleton)")
        except ImportError:
            pass
    return _perplexity_client


def get_grok_client():
    """Lazy singleton for Grok (xAI) client."""
    global _grok_client
    if _grok_client is None and GROK_KEY:
        try:
            from openai import OpenAI
            _grok_client = OpenAI(api_key=GROK_KEY, base_url="https://api.x.ai/v1")
            log.info("Grok client initialized (singleton)")
        except ImportError:
            pass
    return _grok_client


def get_openai_client():
    """Lazy singleton for OpenAI client."""
    global _openai_client
    if _openai_client is None and OPENAI_KEY:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=OPENAI_KEY)
            log.info("OpenAI client initialized (singleton)")
        except ImportError:
            pass
    return _openai_client


def get_anthropic_client():
    """Lazy singleton for Anthropic client."""
    global _anthropic_client
    if _anthropic_client is None and ANTHROPIC_KEY:
        try:
            import anthropic
            _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
            log.info("Anthropic client initialized (singleton)")
        except ImportError:
            pass
    return _anthropic_client

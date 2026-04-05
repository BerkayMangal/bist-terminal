# ================================================================
# BISTBULL TERMINAL — AI SERVICE LAYER
# ai/service.py
#
# High-level service functions for each AI-backed feature.
# Each function: build prompt → ai_call → parse → cache → return.
#
# Route handlers call these instead of ai_call directly.
# NO FastAPI dependencies. NO HTTP concerns.
# ================================================================

from __future__ import annotations

import logging
from typing import Optional, Callable

from ai.engine import ai_call, AI_AVAILABLE, AI_PROVIDERS
from ai.prompts import (
    hero_prompt, parse_hero_response,
    briefing_prompt, macro_commentary_prompt,
    cross_commentary_prompt, agent_prompt,
    SOCIAL_PROMPT, clean_json_response,
    trader_summary_prompt, build_rich_context,
)
from core.cache import ai_cache, macro_ai_cache, social_cache
from core.response_envelope import now_iso

log = logging.getLogger("bistbull.ai.service")


# ================================================================
# TRADER SUMMARY — per-stock investment thesis
# ================================================================
def generate_trader_summary(r: dict, tech: Optional[dict] = None) -> Optional[str]:
    """Generate AI-powered investment thesis for a single stock.
    Cached by symbol + overall + ivme + entry_label."""
    if not AI_AVAILABLE:
        return None
    cache_key = f"{r['symbol']}_{r['overall']}_{r.get('ivme', 0)}_{r.get('entry_label', '')}"
    cached = ai_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        prompt = trader_summary_prompt(r, tech)
        text = ai_call(prompt, max_tokens=300)
        if text:
            ai_cache.set(cache_key, text)
        return text
    except Exception as e:
        log.warning(f"AI trader summary: {e}")
        return None


# ================================================================
# HERO STORY — market narrative for hero section
# ================================================================
def generate_hero_story(
    hero_data: dict,
    items: list[dict],
    macro_items: list[dict],
    cross_count: int,
) -> dict:
    """Generate AI story/commentary for the hero section.
    Mutates hero_data dict with story/bot_says fields. Returns it."""
    if not AI_AVAILABLE or not items:
        return hero_data
    try:
        prompt = hero_prompt(
            mode_label=hero_data["mode_label"],
            total=hero_data["stats"]["total"],
            bullish_count=hero_data["stats"]["bullish"],
            deger_leaders=hero_data["deger_leaders"],
            ivme_leaders=hero_data["ivme_leaders"],
            items=items,
            macro_items=macro_items,
            cross_count=cross_count,
        )
        text = ai_call(prompt, max_tokens=300)
        if text:
            parsed = parse_hero_response(text)
            hero_data["story"] = parsed["story"]
            hero_data["bot_says"] = parsed["bot_says"]
            if parsed["ai_reason"] and hero_data.get("opportunity"):
                hero_data["opportunity"]["ai_reason"] = parsed["ai_reason"]
    except Exception as e:
        log.warning(f"hero AI: {e}")
    return hero_data


# ================================================================
# BRIEFING — daily market briefing
# ================================================================
def generate_briefing(ctx: dict) -> Optional[str]:
    """Generate AI briefing from pre-built context. Returns raw text."""
    if not AI_AVAILABLE:
        return None
    try:
        prompt = briefing_prompt(ctx)
        return ai_call(prompt, max_tokens=400)
    except Exception as e:
        log.warning(f"briefing AI: {e}")
        return None


# ================================================================
# MACRO COMMENTARY
# ================================================================
def generate_macro_commentary(macro_items: list[dict]) -> Optional[str]:
    """Generate AI macro commentary. Returns raw text."""
    if not AI_AVAILABLE:
        return None
    try:
        prompt = macro_commentary_prompt(macro_items)
        return ai_call(prompt, max_tokens=300)
    except Exception as e:
        log.warning(f"macro AI: {e}")
        return None


# ================================================================
# CROSS SIGNAL COMMENTARY
# ================================================================
def generate_cross_commentary(
    signals: list[dict],
    bullish: int,
    bearish: int,
) -> Optional[str]:
    """Generate AI commentary on cross signals. Returns raw text."""
    if not AI_AVAILABLE or not signals:
        return None
    try:
        prompt = cross_commentary_prompt(signals, bullish, bearish)
        return ai_call(prompt, max_tokens=250)
    except Exception as e:
        log.debug(f"cross AI: {e}")
        return None


# ================================================================
# Q AGENT — conversational Q&A
# ================================================================
def generate_agent_answer(
    context: str,
    query: str,
) -> Optional[str]:
    """Generate Q agent answer. Returns raw text."""
    if not AI_AVAILABLE:
        return None
    try:
        prompt = agent_prompt(context, query)
        return ai_call(prompt, max_tokens=300)
    except Exception as e:
        log.warning(f"agent AI: {e}")
        return None


# ================================================================
# SOCIAL SENTIMENT
# ================================================================
def generate_social_sentiment() -> Optional[dict]:
    """Generate social sentiment analysis via Grok.
    Returns structured dict or None."""
    if not AI_AVAILABLE or "grok" not in AI_PROVIDERS:
        return None
    try:
        text = ai_call(SOCIAL_PROMPT, max_tokens=500)
        if not text:
            return None
        data = clean_json_response(text)
        if data:
            return {
                "timestamp": now_iso(), "source": "grok_ai",
                "trending": data.get("trending", []),
                "overall_sentiment": data.get("overall_sentiment", "neutral"),
                "summary": data.get("summary", ""),
                "hot_topics": data.get("hot_topics", []),
            }
        else:
            return {
                "timestamp": now_iso(), "source": "grok_ai",
                "trending": [], "overall_sentiment": "unknown",
                "summary": text[:500], "hot_topics": [],
            }
    except Exception as e:
        log.warning(f"social AI: {e}")
        return None

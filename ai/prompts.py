# ================================================================
# BISTBULL TERMINAL — AI PROMPT TEMPLATES & PARSERS
# ai/prompts.py
#
# All AI prompt construction and response parsing in one place.
# Previously scattered across 6 route handlers in app.py.
#
# NO I/O, NO AI calls, NO cache.
# Functions build prompt strings and parse response strings.
# ================================================================

from __future__ import annotations

import json
from typing import Optional


# ================================================================
# HERO SUMMARY
# ================================================================
def hero_prompt(
    mode_label: str,
    total: int,
    bullish_count: int,
    deger_leaders: list[dict],
    ivme_leaders: list[dict],
    items: list[dict],
    macro_items: list[dict],
    cross_count: int,
) -> str:
    """Build the hero summary AI prompt."""
    d_top = [f"{r['ticker']}(D:{r.get('deger', 50):.0f})" for r in (deger_leaders or items[:3])]
    i_top = [f"{r['ticker']}(I:{r.get('ivme', 50):.0f})" for r in (ivme_leaders or items[:3])]
    bot3 = [
        f"{r['ticker']}(D:{r.get('deger', r.get('overall', 50)):.0f})"
        for r in sorted(items, key=lambda x: x.get("deger", x.get("overall", 50)))[:3]
    ]
    macro_str = ", ".join(f"{m['name']}:{m.get('change_pct', 0):+.1f}%" for m in macro_items[:6])

    return (
        f"BistBull stratejist. Türkçe, somut.\n"
        f"Piyasa: {mode_label}. {total} hisse, {bullish_count} pozitif.\n"
        f"DEGER: {', '.join(d_top)}. IVME: {', '.join(i_top)}.\n"
        f"Zayif: {', '.join(bot3)}. Makro: {macro_str}\n"
        f"{cross_count} sinyal.\n\n"
        f"HİKÂYE: 2 cümle\nYORUM: 2 cümle\nFIRSAT: 1 cümle"
    )


def parse_hero_response(text: str) -> dict:
    """Parse AI hero response into story / bot_says / ai_reason components."""
    story = None
    bot_says = None
    ai_reason = None

    for line in text.split("\n"):
        lu = line.strip().upper()
        if any(lu.startswith(p) for p in ("HİKÂYE:", "HIKAYE:", "HİKAYE:")):
            story = line.split(":", 1)[1].strip()
        elif lu.startswith("YORUM:"):
            bot_says = line[6:].strip()
        elif lu.startswith("FIRSAT:"):
            ai_reason = line.split(":", 1)[1].strip()

    if not story:
        story = text[:200]
    if not bot_says:
        bot_says = text[200:400] if len(text) > 200 else None

    return {"story": story, "bot_says": bot_says, "ai_reason": ai_reason}


# ================================================================
# BRIEFING
# ================================================================
def briefing_prompt(ctx: dict) -> str:
    """Build the daily briefing AI prompt from briefing context dict."""
    return (
        f"Sen BistBull analisti. Türkçe, somut.\n"
        f"TARAMA: {ctx['count']} hisse.\n"
        f"DEGER: {ctx['deger_str']}\n"
        f"IVME: {ctx['ivme_str']}\n"
        f"Zayif: {ctx['worst_str']}\n"
        f"Top 5: {'; '.join(ctx['summary_parts'])}\n"
        f"Sinyal: {ctx['signal_count']} ({ctx['sig_str']})\n\n"
        f"ÖZET: 2-3 cümle\nYATIRIMCI: 2 cümle\nTRADER: 2 cümle\nDİKKAT: 1 cümle"
    )


# ================================================================
# MACRO COMMENTARY
# ================================================================
def macro_commentary_prompt(macro_items: list[dict]) -> str:
    """Build the macro commentary AI prompt."""
    lines = [
        f"{m.get('flag', '')} {m['name']}: {m['price']}, gun:{m['change_pct']}%, YTD:{m.get('ytd_pct', '?')}%"
        for m in sorted(macro_items, key=lambda x: x.get("ytd_pct") or 0, reverse=True)
    ]
    return (
        "Sen BistBull makro stratejistisin. Türkçe, somut.\n\n"
        + "\n".join(lines[:20])
        + "\n\nTABLO: 2 cümle\nEM: 1 cümle\nBIST: 1 cümle\nSTRATEJİ: 1 cümle"
    )


# ================================================================
# CROSS COMMENTARY
# ================================================================
def cross_commentary_prompt(
    signals: list[dict],
    bullish: int,
    bearish: int,
) -> str:
    """Build the cross signal commentary AI prompt."""
    from collections import defaultdict

    ticker_groups: dict[str, list[str]] = defaultdict(list)
    for s in signals[:15]:
        ticker_groups[s["ticker"]].append(f"{s['signal']}({'*' * s.get('stars', 1)})")
    sig_summary = "; ".join(
        f"{t}: {', '.join(sigs)}" for t, sigs in list(ticker_groups.items())[:8]
    )
    return (
        f"Sen BistBull Cross Hunter sinyal analistisin. Türkçe, somut.\n"
        f"{len(signals)} sinyal ({bullish} yukari, {bearish} asagi).\n"
        f"Sinyaller: {sig_summary}\n\n"
        f"2-3 cümle: Dikkat çekici sinyal? Hacim teyidi?"
    )


# ================================================================
# Q AGENT
# ================================================================
def agent_prompt(context: str, query: str) -> str:
    """Build the Q agent AI prompt."""
    return (
        f"Sen Q'sun — BistBull asistanı. Kurumsal, kısa, Türkçe. 3-5 cümle MAX.\n\n"
        f"{context}"
        f"Soru: {query}\n\nQ:"
    )


# ================================================================
# SOCIAL SENTIMENT
# ================================================================
SOCIAL_PROMPT = (
    'Sen BIST sosyal medya analistisin. X\'teki BIST tartışmalarını analiz et.\n'
    'JSON formatında: {"trending": [{"ticker": "THYAO", "sentiment": "bullish", '
    '"score": 78, "reason": "..."}], "overall_sentiment": "...", "summary": "...", '
    '"hot_topics": ["..."]}\nEn az 5, en fazla 10 hisse.'
)


def clean_json_response(text: str) -> Optional[dict]:
    """
    Clean and parse a JSON response from an AI model.
    Handles markdown code fences and other wrapping.

    Returns parsed dict, or None if parsing fails.
    """
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()
    if clean.startswith("json"):
        clean = clean[4:].strip()
    try:
        return json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        return None

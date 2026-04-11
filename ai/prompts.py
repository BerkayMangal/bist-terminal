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


# ================================================================
# RICH CONTEXT BUILDER — detailed data context for AI prompts
# ================================================================
def build_rich_context(r: dict, tech: Optional[dict] = None) -> str:
    """Build rich text context from analysis result for AI prompts.
    Moved from ai/engine.py to centralize all prompt-related logic."""
    from utils.helpers import fmt_num, fmt_pct

    s = r["scores"]
    m = r["metrics"]
    L = r.get("legendary", {})
    lines = [
        f"Hisse: {r['ticker']} ({r['name']}) | Sektör: {m.get('sector', '')} ({r.get('sector_group', '')}) | {r['style']}",
        f"FA SCORE (saf kalite): {r.get('fa_score', 50)}/100 | RİSK: {r.get('risk_score', 0)} | KARAR SKORU: {r['overall']}/100",
        f"GİRİŞ: {r.get('entry_label', '?')} | KARAR: {r.get('decision', '?')} | KALİTE: {r.get('quality_tag', '?')}",
        f"İVME: {r.get('ivme', 50)}/100 | ZAMANLAMA: {r.get('timing', '?')}",
        f"Value:{s['value']:.0f} Quality:{s['quality']:.0f} Growth:{s['growth']:.0f} Balance:{s['balance']:.0f} Earnings:{s['earnings']:.0f} Moat:{s['moat']:.0f} Capital:{s['capital']:.0f}",
        f"Momentum:{s.get('momentum', 50):.0f} TechBreak:{s.get('tech_break', 50):.0f} InstFlow:{s.get('inst_flow', 50):.0f}",
        f"Fiyat:{fmt_num(m.get('price'))} PiyasaDeg:{fmt_num(m.get('market_cap'))} F/K:{fmt_num(m.get('pe'))} PD/DD:{fmt_num(m.get('pb'))} FD/FAVÖK:{fmt_num(m.get('ev_ebitda'))}",
        f"ROE:{fmt_pct(m.get('roe'))} ROIC:{fmt_pct(m.get('roic'))} Brüt Marj:{fmt_pct(m.get('gross_margin'))} Net Marj:{fmt_pct(m.get('net_margin'))}",
        f"Gelir Büyüme:{fmt_pct(m.get('revenue_growth'))} HBK Büyüme:{fmt_pct(m.get('eps_growth'))}",
        f"NB/FAVÖK:{fmt_num(m.get('net_debt_ebitda'))} Cari Oran:{fmt_num(m.get('current_ratio'))} Faiz Karşılama:{fmt_num(m.get('interest_coverage'))}",
        f"FCF Getiri:{fmt_pct(m.get('fcf_yield'))} CFO/NI:{fmt_num(m.get('cfo_to_ni'))}",
        f"Piotroski:{L.get('piotroski', 'N/A')} Altman:{L.get('altman', 'N/A')} Beneish:{L.get('beneish', 'N/A')}",
        f"Graham:{L.get('graham_filter', 'N/A')} Buffett:{L.get('buffett_filter', 'N/A')}",
    ]
    if tech:
        rsi_val = tech.get("rsi")
        rsi_str = f"{rsi_val:.0f}" if isinstance(rsi_val, (int, float)) else "?"
        vol_val = tech.get("vol_ratio")
        vol_str = f"{vol_val:.1f}x" if isinstance(vol_val, (int, float)) else "?"
        pct_20d = tech.get("pct_20d")
        pct_str = f"{pct_20d:+.1f}%" if isinstance(pct_20d, (int, float)) else "?"
        ma50_above = (tech.get("price", 0) or 0) > (tech.get("ma50") or 0)
        lines.append(
            f"Teknik: RSI={rsi_str}, "
            f"MACD={'bullish' if tech.get('macd_bullish') else 'bearish'}, "
            f"{'MA50 üzerinde' if ma50_above else 'MA50 altında'}, "
            f"BB:{tech.get('bb_pos', '?')}, Hacim:{vol_str}, "
            f"20g değişim:{pct_str}, "
            f"52W zirveden {abs(tech.get('pct_from_high', 0)):.0f}% uzakta"
        )
    if r.get("is_hype"):
        lines.append("⚠️ HYPE TESPİTİ: Fiyat hızla yükseliyor ama temel zayıf — spekülasyon riski yüksek!")
    lines.append(f"Risk faktörleri: {', '.join(r.get('risk_reasons', ['Yok']))}")
    lines.append(f"Güçlü: {', '.join(r.get('positives', []))}")
    lines.append(f"Zayıf: {', '.join(r.get('negatives', []))}")
    return "\n".join(lines)


# ================================================================
# TRADER SUMMARY — investment thesis prompt
# ================================================================
def trader_summary_prompt(r: dict, tech: Optional[dict] = None) -> str:
    """Build the trader summary / investment thesis AI prompt.
    Trust-aware: reflects data quality in prompt."""
    ctx = build_rich_context(r, tech)
    entry = r.get("entry_label", "?")
    is_hype = r.get("is_hype", False)
    grade = r.get("data_health", {}).get("grade", "A")
    confidence = r.get("confidence", 50)

    quality_note = ""
    if grade in ("C", "D"):
        quality_note = (
            "\n⚠️ VERİ KALİTESİ DÜŞÜK (grade: {grade}). "
            "Bazı finansal veriler eksik. Bunu açıkça belirt.\n"
        )
    elif confidence < 50:
        quality_note = "\n⚠️ Güven skoru düşük. Temkinli yaz, abartma.\n"

    return (
        "Sen kurumsal BIST analisti ve portföy yöneticisisin. 20 yıllık tecrüben var.\n"
        "Aşağıdaki veriye dayanarak bu hisse için yatırım tezi yaz. Türkçe.\n"
        "ASLA sallama, SADECE verideki rakamlara dayan. Gerçekçi, spesifik, kısa ol.\n"
        "YASAK KELİMELER: kesinlikle, garanti, kaçırma, patlayacak, uçacak, hemen al.\n"
        f"{'⚠️ DİKKAT: Bu hisse HYPE/SPEKÜLATİF olarak işaretlenmiş — temel zayıf ama fiyat uçuyor.' if is_hype else ''}\n"
        f"{quality_note}\n"
        f"{ctx}\n\n"
        "Şu formatta yaz (her satır ayrı, başka HİÇBİR ŞEY yazma):\n"
        f"GİRİŞ: {entry} — bu ne anlama geliyor? 1 cümle açıkla.\n"
        "TEZ: 1 spesifik cümle — NEDEN bu karar? (rakam kullan)\n"
        "RİSK: 1 spesifik cümle — en büyük risk? (rakam kullan)\n"
        "ZAMANLAMA: 1 cümle — giriş zamanı uygun mu, ne beklemeli?\n"
        "TÜRKİYE: 1 cümle — Türkiye piyasası bağlamında özel not (döviz, enflasyon, sektör)\n"
    )

# ================================================================
# COMPARISON AI PROMPT — analyst-style, data-grounded
# ================================================================
def comparison_prompt(ai_context: str, deterministic_summary: str) -> str:
    """Build AI comparison prompt from structured comparison data."""
    return (
        "Sen kurumsal BIST analisti ve portföy yöneticisisin.\n"
        "İki hisseyi karşılaştıran yapısal veri var. Buna dayanarak keskin, kısa bir yorum yaz.\n\n"
        "KURALLAR:\n"
        "- SADECE verideki rakamlara dayan. Sallama, uydurmama.\n"
        "- Max 4 cümle. Her cümle bir rakama referans versin.\n"
        "- Bir hisseyi öv değil — farkları açıkla.\n"
        "- 'Hangisini almalıyım?' sorusuna CEVAP VERME. Sadece farkları sun.\n"
        "- Türkçe yaz, jargonsuz.\n"
        "- YASAK: kesinlikle, garanti, kaçırma, patlayacak, uçacak, hemen al, muhteşem.\n\n"
        f"VERİLER:\n{ai_context}\n\n"
        f"DETERMİNİSTİK ÖZET (bununla çelişme):\n{deterministic_summary}\n\n"
        "Şimdi kendi analist yorumunu yaz (4 cümle, rakam kullan):"
    )

# ================================================================
# BISTBULL TERMINAL V9.1 — AI ENGINE
# AI provider chain (Grok → OpenAI → Anthropic), context builder,
# trader summary. Tüm AI logic tek dosyada.
# ================================================================

from __future__ import annotations

import logging
from typing import Optional

from config import (
    GROK_KEY, GROK_MODEL,
    OPENAI_KEY, OPENAI_MODEL,
    ANTHROPIC_KEY, ANTHROPIC_MODEL,
)
from helpers import fmt_num, fmt_pct
from cache import ai_cache

log = logging.getLogger("bistbull")

# ================================================================
# PROVIDER DISCOVERY — import-time, bir kere çalışır
# ================================================================
AI_PROVIDERS: list[str] = []

try:
    from openai import OpenAI as _OpenAI
    if GROK_KEY:
        AI_PROVIDERS.append("grok")
    if OPENAI_KEY:
        AI_PROVIDERS.append("openai")
except ImportError:
    _OpenAI = None  # type: ignore

try:
    import anthropic as _anthropic
    if ANTHROPIC_KEY:
        AI_PROVIDERS.append("anthropic")
except ImportError:
    _anthropic = None  # type: ignore

AI_AVAILABLE: bool = len(AI_PROVIDERS) > 0


# ================================================================
# LOW-LEVEL CALLERS
# ================================================================
def _call_grok(prompt: str, max_tokens: int) -> str:
    client = _OpenAI(api_key=GROK_KEY, base_url="https://api.x.ai/v1")
    resp = client.chat.completions.create(
        model=GROK_MODEL, max_tokens=max_tokens, temperature=0.4,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()


def _call_openai(prompt: str, max_tokens: int) -> str:
    client = _OpenAI(api_key=OPENAI_KEY)
    resp = client.chat.completions.create(
        model=OPENAI_MODEL, max_tokens=max_tokens, temperature=0.4,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()


def _call_anthropic(prompt: str, max_tokens: int) -> str:
    client = _anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    resp = client.messages.create(
        model=ANTHROPIC_MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


_CALLERS = {
    "grok": _call_grok,
    "openai": _call_openai,
    "anthropic": _call_anthropic,
}


def ai_call(prompt: str, max_tokens: int = 200) -> Optional[str]:
    """Try each AI provider in order: Grok → OpenAI → Anthropic."""
    for provider in AI_PROVIDERS:
        try:
            caller = _CALLERS.get(provider)
            if caller:
                return caller(prompt, max_tokens)
        except Exception as e:
            log.warning(f"AI {provider} failed: {e}")
            continue
    return None


# ================================================================
# RICH CONTEXT BUILDER
# ================================================================
def build_rich_context(r: dict, tech: Optional[dict] = None) -> str:
    """Zengin AI context — FA pure + Risk ayrı + Entry Label + Türkiye bağlamı."""
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
# AI TRADER SUMMARY
# ================================================================
def ai_trader_summary(r: dict, tech: Optional[dict] = None) -> Optional[str]:
    """AI destekli yatırım tezi."""
    if not AI_AVAILABLE:
        return None
    cache_key = f"{r['symbol']}_{r['overall']}_{r.get('ivme', 0)}_{r.get('entry_label', '')}"
    cached = ai_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        ctx = build_rich_context(r, tech)
        entry = r.get("entry_label", "?")
        is_hype = r.get("is_hype", False)
        prompt = (
            "Sen kurumsal BIST analisti ve portföy yöneticisisin. 20 yıllık tecrüben var. Türkiye piyasasını çok iyi bilirsin.\n"
            "Aşağıdaki veriye dayanarak bu hisse için yatırım tezi yaz. Türkçe.\n"
            "ASLA sallama, SADECE verideki rakamlara dayan. Gerçekçi, spesifik, kısa ol.\n"
            f"{'⚠️ DİKKAT: Bu hisse HYPE/SPEKÜLATİF olarak işaretlenmiş — temel zayıf ama fiyat uçuyor.' if is_hype else ''}\n\n"
            f"{ctx}\n\n"
            "Şu formatta yaz (her satır ayrı, başka HİÇBİR ŞEY yazma):\n"
            f"GİRİŞ: {entry} — bu ne anlama geliyor? 1 cümle açıkla.\n"
            "TEZ: 1 spesifik cümle — NEDEN bu karar? (rakam kullan)\n"
            "RİSK: 1 spesifik cümle — en büyük risk? (rakam kullan)\n"
            "ZAMANLAMA: 1 cümle — giriş zamanı uygun mu, ne beklemeli?\n"
            "TÜRKİYE: 1 cümle — Türkiye piyasası bağlamında özel not (döviz, enflasyon, sektör)\n"
        )
        text = ai_call(prompt, max_tokens=300)
        if text:
            ai_cache.set(cache_key, text)
        return text
    except Exception as e:
        log.warning(f"AI summary: {e}")
        return None

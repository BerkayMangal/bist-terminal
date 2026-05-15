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
        "Sen BistBull'un piyasa stratejistisin. Borsa İstanbul'u takip eden, "
        "veriye dayalı, abartısız konuşan bir analist.\n\n"
        "GÖREV: Aşağıdaki tarama verisinden günün piyasa fotoğrafını çıkar.\n\n"
        "VERİ:\n"
        f"  Piyasa modu: {mode_label}\n"
        f"  Taranan: {total} hisse — {bullish_count} tanesi pozitif\n"
        f"  En güçlü değerleme: {', '.join(d_top)}\n"
        f"  En güçlü ivme: {', '.join(i_top)}\n"
        f"  En zayıf: {', '.join(bot3)}\n"
        f"  Makro: {macro_str}\n"
        f"  Aktif teknik sinyal: {cross_count}\n\n"
        "KURALLAR:\n"
        "- Her cümlede yukarıdaki sayılardan en az birine atıf yap\n"
        "- 'Kesinlikle / patlayacak / kaçırma' gibi spekülatif dil YASAK\n"
        "- Genel geçer laf etme ('piyasalar dalgalı' gibi) — somut hisse/sayı ver\n"
        "- Düz, akıcı Türkçe; başlık/madde işareti kullanma\n\n"
        "ÇIKTI FORMATI (tam olarak bu 3 satır, başka hiçbir şey yazma):\n"
        "HİKÂYE: <2 cümle — bugün piyasada ne oluyor, hangi grup öne çıkıyor>\n"
        "YORUM: <2 cümle — bu tablo yatırımcı için ne anlama geliyor>\n"
        "FIRSAT: <1 cümle — dikkat çeken tek bir somut isim ve nedeni>"
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
        "Sen BistBull'un günlük bülten yazarısın. Borsa İstanbul taramasını "
        "yorumlayan kıdemli bir analist. Veriye dayan, abartma.\n\n"
        "GÜNÜN TARAMA VERİSİ:\n"
        f"  Taranan hisse: {ctx['count']}\n"
        f"  Değerleme liderleri: {ctx['deger_str']}\n"
        f"  İvme liderleri: {ctx['ivme_str']}\n"
        f"  Zayıf kalanlar: {ctx['worst_str']}\n"
        f"  İlk 5 özeti: {'; '.join(ctx['summary_parts'])}\n"
        f"  Teknik sinyal: {ctx['signal_count']} adet ({ctx['sig_str']})\n\n"
        "KURALLAR:\n"
        "- Her bölümde somut hisse adı + sayı kullan\n"
        "- 'Yatırımcı' = uzun vadeli/temel bakış; 'Trader' = kısa vadeli/teknik bakış\n"
        "- Spekülatif/garanti dili yok; al-sat emri verme, durum tasvir et\n"
        "- Akıcı Türkçe paragraf, madde işareti yok\n\n"
        "ÇIKTI FORMATI (tam olarak bu 4 satır):\n"
        "ÖZET: <2-3 cümle — günün taraması ne gösteriyor>\n"
        "YATIRIMCI: <2 cümle — uzun vadeli yatırımcı bu tablodan ne çıkarmalı>\n"
        "TRADER: <2 cümle — kısa vadeli trader hangi sinyale/isme bakmalı>\n"
        "DİKKAT: <1 cümle — günün en önemli risk veya uyarı notu>"
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
        "Sen BistBull'un makro stratejistisin. Küresel piyasaların Borsa "
        "İstanbul'a etkisini okuyan bir ekonomist.\n\n"
        "GÜNCEL MAKRO TABLOSU (YTD'ye göre sıralı):\n"
        + "\n".join(lines[:20])
        + "\n\n"
        "KURALLAR:\n"
        "- Her cümlede yukarıdaki enstrümanlardan birine + yüzdesine atıf yap\n"
        "- 'EM' = gelişmekte olan piyasalar bağlamı\n"
        "- BIST satırında Türkiye'ye özel etkiyi söyle (döviz, faiz, risk iştahı)\n"
        "- Spekülatif tahmin yok; tabloyu yorumla, fal bakma\n\n"
        "ÇIKTI FORMATI (tam olarak bu 4 satır):\n"
        "TABLO: <2 cümle — küresel tablo bugün ne diyor>\n"
        "EM: <1 cümle — gelişmekte olan piyasalar açısından>\n"
        "BIST: <1 cümle — bunun Borsa İstanbul'a yansıması>\n"
        "STRATEJİ: <1 cümle — bu makro tabloda nasıl pozisyonlanmalı>"
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
        "Sen BistBull Cross Hunter'ın teknik sinyal analistisin. Borsa "
        "İstanbul'da teknik kırılımları okuyan bir uzman.\n\n"
        "BUGÜNKÜ SİNYAL TABLOSU:\n"
        f"  Toplam: {len(signals)} sinyal ({bullish} yukarı yönlü, {bearish} aşağı yönlü)\n"
        f"  Detay (yıldız = sinyal gücü): {sig_summary}\n\n"
        "KURALLAR:\n"
        "- En çok yıldızlı (en güçlü) sinyallere odaklan\n"
        "- Hacim teyidi olan sinyalleri ayırt et — hacimsiz kırılım zayıftır\n"
        "- Spesifik hisse adı ver, genel laf etme\n"
        "- 'Al/sat' demek yerine sinyalin ne anlama geldiğini açıkla\n\n"
        "ÇIKTI: 2-3 cümlelik düz Türkçe paragraf. Hangi sinyal dikkat çekici, "
        "hacim teyidi var mı, yatırımcı neye dikkat etmeli."
    )


# ================================================================
# Q AGENT
# ================================================================
def agent_prompt(context: str, query: str) -> str:
    """Build the Q agent AI prompt."""
    return (
        "Sen Q'sun — BistBull'un yapay zekâ asistanı. Borsa İstanbul "
        "konusunda uzman, kurumsal ama anlaşılır konuşan bir danışman.\n\n"
        "KURALLAR:\n"
        "- SADECE aşağıdaki bağlamdaki verilere dayan; veri yoksa 'elimde bu "
        "veri yok' de, uydurma\n"
        "- 3-5 cümleyi geçme; net ve doğrudan ol\n"
        "- Yatırım tavsiyesi verme ('al/sat' deme) — veriyi yorumla, kararı "
        "kullanıcıya bırak\n"
        "- Sayı varsa kullan; soyut genelleme yapma\n\n"
        "BAĞLAM:\n"
        f"{context}\n"
        f"KULLANICI SORUSU: {query}\n\n"
        "Q'nun cevabı:"
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

    hype_note = (
        "DİKKAT: Bu hisse HYPE/SPEKÜLATİF olarak işaretlendi — fiyat hızlı "
        "yükseliyor ama temeller zayıf. Tezde bu çelişkiyi açıkça vurgula.\n"
        if is_hype else ""
    )
    return (
        "Sen 20 yıllık tecrübeli bir kurumsal Borsa İstanbul (BIST) analisti "
        "ve portföy yöneticisisin. Görevin: aşağıdaki veriye dayanarak bu "
        "hisse için kısa, somut bir yatırım tezi yazmak.\n\n"
        "İYİ TEZ NASIL OLUR:\n"
        "- Her cümle verideki SOMUT bir rakama dayanır (F/K 8.2, ROE %24 gibi)\n"
        "- Soyut övgü yok ('güçlü şirket' değil → 'ROE %24 ile sektör "
        "ortalamasının üzerinde')\n"
        "- Çelişkileri saklamaz (ucuz F/K ama düşen ciro → ikisini de söyle)\n\n"
        "YASAK: kesinlikle, garanti, kaçırma, patlayacak, uçacak, hemen al/sat. "
        "Bunlar spekülatif dil — kullanma. Durumu tasvir et, emir verme.\n"
        f"{hype_note}"
        f"{quality_note}\n"
        "── HİSSE VERİSİ ──\n"
        f"{ctx}\n\n"
        "── ÇIKTI ──\n"
        "Tam olarak şu 5 satırı yaz, her biri ayrı satırda, başka HİÇBİR şey "
        "ekleme (selamlama, kapanış, açıklama yok):\n"
        f"GİRİŞ: {entry} sinyali ne anlama geliyor? 1 cümlede açıkla.\n"
        "TEZ: Bu karara neden varıldı? 1 spesifik cümle, rakam ver.\n"
        "RİSK: En büyük risk nedir? 1 spesifik cümle, rakam ver.\n"
        "ZAMANLAMA: Giriş zamanlaması uygun mu, ne beklenmeli? 1 cümle.\n"
        "TÜRKİYE: Türkiye'ye özel bir not (döviz/enflasyon/sektör dinamiği). 1 cümle.\n"
    )

# ================================================================
# KAP DISCLOSURE ANALYSIS PROMPT
# ================================================================
def kap_disclosure_prompt(disclosure: dict, metrics: Optional[dict] = None,
                          analysis: Optional[dict] = None) -> str:
    """Detailed Turkish analysis of a freshly-released balance sheet.

    Inputs:
      disclosure  KAP DisclosureRecord-as-dict
      metrics     compute_metrics_v9 output for the ticker (post-Plan C
                  invalidation, so this reflects the new bilanço)
      analysis    analyze_symbol output (overall score, decision, ...)
                  for the same ticker (optional)
    """
    ticker = disclosure.get("ticker") or "?"
    name = disclosure.get("kap_title") or ticker
    rule_type = disclosure.get("rule_type") or ""
    year = disclosure.get("year")
    period = disclosure.get("period")
    publish = disclosure.get("publish_date_raw") or disclosure.get("publish_date") or ""

    m = metrics or {}
    a = analysis or {}

    # Build the metric context — only include fields with values
    def _fmt_pct(v, decimals=1):
        if v is None:
            return "?"
        try:
            return f"{float(v) * 100:.{decimals}f}%"
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

    fundamentals = (
        f"Mevcut metrikler (Plan C: bilanço tazelendi sonrası):\n"
        f"  Piyasa değeri: {_fmt_num(m.get('market_cap'))} TL\n"
        f"  F/K: {m.get('pe') if m.get('pe') is not None else '?'}\n"
        f"  PD/DD: {m.get('pb') if m.get('pb') is not None else '?'}\n"
        f"  ROE: {_fmt_pct(m.get('roe'))}\n"
        f"  Net marj: {_fmt_pct(m.get('net_margin'))}\n"
        f"  Brüt marj: {_fmt_pct(m.get('gross_margin'))}\n"
        f"  Borç/Özsermaye: {m.get('debt_equity') if m.get('debt_equity') is not None else '?'}\n"
        f"  Altman Z: {m.get('altman_z') if m.get('altman_z') is not None else '?'}\n"
        f"  Yıllık ciro büyüme: {_fmt_pct(m.get('revenue_growth'))}\n"
        f"  Yıllık kar büyüme: {_fmt_pct(m.get('eps_growth'))}\n"
    )
    if m.get("quarterly_data_available"):
        fundamentals += (
            f"  Çeyreklik YoY ciro: {_fmt_pct(m.get('revenue_growth_yoy_q'))} "
            f"(en son: {m.get('latest_quarter') or '?'})\n"
            f"  Çeyreklik YoY net kar: {_fmt_pct(m.get('net_income_growth_yoy_q'))}\n"
        )

    score_ctx = ""
    if a:
        decision = a.get("decision") or "?"
        overall = a.get("overall") or "?"
        score_ctx = (
            f"\nMevcut V13 saf değer skoru: {overall} ({decision}). "
            f"Sektör: {a.get('sector') or '?'}.\n"
        )

    return (
        "Sen kurumsal BIST analisti ve bilanço uzmanısın. 20 yıllık tecrüben var.\n"
        "Bir şirket az önce KAP'a bilanço açıkladı. Aşağıdaki bağlama dayanarak "
        "detaylı bir analiz hazırla. Türkçe yaz.\n"
        "\n"
        "KURALLAR:\n"
        "- ASLA al/sat tavsiyesi VERME. Sadece analiz yap.\n"
        "- YASAK KELİMELER: kesinlikle, garanti, mutlaka, uçacak, patlayacak, hemen al.\n"
        "- Sadece verideki rakamlara dayan, hayalî veri uydurma.\n"
        "- Her madde 1-2 cümle. Spesifik ol, rakam kullan.\n"
        "\n"
        f"AÇIKLAMA: {name} ({ticker})\n"
        f"Dönem: {year} {rule_type} (period={period})\n"
        f"Tarih: {publish}\n"
        f"Konu: {disclosure.get('subject') or '?'}\n"
        "\n"
        f"{fundamentals}"
        f"{score_ctx}"
        "\n"
        "Şu formatta yaz (her madde başlığıyla, başka HİÇBİR ŞEY yazma):\n"
        "ÖZET: 1-2 cümle — bilançonun ana mesajı (büyüme mi, daralma mı, marj mı?).\n"
        "POZİTİF: 1 cümle — en güçlü 1-2 olumlu gelişme (rakam ile).\n"
        "NEGATİF: 1 cümle — en dikkat çekici 1-2 risk veya zayıflık (rakam ile).\n"
        "DEĞİŞİM: 1 cümle — geçen döneme göre en önemli değişim (Y/Y veya Q/Q ile).\n"
        "SEKTÖR: 1 cümle — bu sonuç sektör trendine uyuyor mu, ayrışıyor mu?\n"
        "TAKİP: 1 cümle — bir sonraki çeyrekte/yıllıkta neyi izlemeli, kritik metrik?\n"
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

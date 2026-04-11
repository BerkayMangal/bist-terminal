# ================================================================
# BISTBULL TERMINAL — MACRO AI ROLES (Analyst-Style Narrative)
# ai/macro_roles.py
#
# 3 distinct AI perspectives. Analyst-grade, not guru-grade.
# Sharp, grounded, data-tied, max 3-4 sentences.
# Integrated with ai/safety.py for validation.
# ================================================================

from __future__ import annotations
from engine.macro_decision import RegimeResult


def _signals_block(result: RegimeResult) -> str:
    lines = []
    for s in result.signals:
        tag = " [TAHMİNİ]" if s.source in ("tahmini", "eski") else ""
        lines.append(f"- {s.name}: {s.note} → {s.label}{tag}")
    if result.contradictions:
        lines.append("\nÇelişkiler:")
        for c in result.contradictions:
            lines.append(f"- {c.message}")
    return "\n".join(lines)


def _base_context(result: RegimeResult) -> str:
    return (
        f"Rejim: {result.regime} (skor: {result.score}/6, güven: {result.confidence})\n"
        f"Açıklama: {result.explanation}\n\n"
        f"Sinyaller:\n{_signals_block(result)}"
    )


ANALYST_STYLE_RULES = """
YAZIM TARZI (KESİN UYULMALI):
- Keskin, kısa, veri odaklı Türkçe yaz. Kaliteli piyasa analisti gibi.
- Her cümle en az 1 somut veriye (CDS, faiz, kur, VIX, endeks hareketi) referans versin.
- Max sınırı AŞ. Fazla yazma.
- Direkt başla, giriş cümlesi yok.
- Belirsizlik varsa açıkça söyle: "net değil", "karışık", "yorum yapmak zor".

YASAK KELİMELER (bunları kullanırsan çıktı reddedilir):
kesinlikle, garanti, kaçırma, kaçırmayın, çok büyük fırsat,
şimdi alınmalı, hemen al, hemen sat, patlayacak, patlama,
uçacak, uçuş, roket, trend başlıyor, büyük kırılım,
tarihsel fırsat, acil, son şans, mutlaka

YASAK KALIPLAR:
- "Piyasalarda karışık bir görünüm hakim" → YASAK
- "Yatırımcıların dikkatli olması önerilir" → YASAK
- "Portföy çeşitlendirmesi yapılmalı" → YASAK
- "Gelişmeler yakından takip edilmeli" → YASAK

"Sen" dili kullan, "yatırımcılar" değil.
"""


def macro_interpreter_prompt(result: RegimeResult) -> str:
    ctx = _base_context(result)
    return f"""Sen BistBull'un makro yorumcususun.

ROL: Piyasada ne oluyor, 3-4 cümlede anlat.
{ANALYST_STYLE_RULES}
MAX: 4 cümle. Başka bir şey yazma.

VERİLER:
{ctx}

Şimdi yaz:"""


def risk_controller_prompt(result: RegimeResult) -> str:
    ctx = _base_context(result)
    return f"""Sen BistBull'un risk kontrolcüsüsün.

ROL: Ne ters gidebilir? Somut risk senaryosu ver.
"Dikkat:" ile başla.
Makro Yorumcu ile AYNI şeyi söyleme — farklı açı sun.
Ortam olumlu görünse bile bir risk bul.
{ANALYST_STYLE_RULES}
MAX: 3 cümle. Başka bir şey yazma.

VERİLER:
{ctx}

Şimdi yaz:"""


def action_coach_prompt(result: RegimeResult) -> str:
    ctx = _base_context(result)
    from engine.macro_decision import get_sector_rotation
    sectors = get_sector_rotation(result.regime)
    sector_ctx = (
        f"Güçlü sektörler (editöryal): {', '.join(sectors['strong'])}\n"
        f"Zayıf sektörler (editöryal): {', '.join(sectors['weak'])}"
    )
    return f"""Sen BistBull'un aksiyon koçusun.

ROL: "Ben ne yapayım?" sorusunu cevapla.
Her madde "→" ile başlasın.
Sektör önerisi ver ama bunların EDİTÖRYAL GÖRÜŞ olduğunu belirt.
Tek hisse ismi verme.
{ANALYST_STYLE_RULES}
MAX: 4 madde. Başka bir şey yazma.

VERİLER:
{ctx}

SEKTÖR ROTASYONU (editöryal):
{sector_ctx}

Şimdi yaz:"""


MACRO_AI_ROLES = {
    "interpreter": {
        "label": "Makro Yorumcu",
        "icon": "📊",
        "prompt_fn": macro_interpreter_prompt,
    },
    "risk_controller": {
        "label": "Risk Kontrolcü",
        "icon": "🛡️",
        "prompt_fn": risk_controller_prompt,
    },
    "action_coach": {
        "label": "Aksiyon Koçu",
        "icon": "🎯",
        "prompt_fn": action_coach_prompt,
    },
}

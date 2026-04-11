# ================================================================
# BISTBULL TERMINAL — MACRO AI ROLES
# ai/macro_roles.py
#
# 3 distinct AI perspectives on the same macro data.
# Each has a different role, tone, and objective.
# ================================================================

from __future__ import annotations
from engine.macro_decision import RegimeResult


def _signals_block(result: RegimeResult) -> str:
    """Format signals as plain text for AI context."""
    lines = []
    for s in result.signals:
        lines.append(f"- {s.name}: {s.note} → {s.label}")
    if result.contradictions:
        lines.append("\nÇelişkiler:")
        for c in result.contradictions:
            lines.append(f"- {c.message}")
    return "\n".join(lines)


def _base_context(result: RegimeResult) -> str:
    return (
        f"Rejim: {result.regime} (skor: {result.score}, güven: {result.confidence})\n"
        f"Açıklama: {result.explanation}\n\n"
        f"Sinyaller:\n{_signals_block(result)}"
    )


# ================================================================
# AI #1 — MAKRO YORUMCU (The Interpreter)
# ================================================================
def macro_interpreter_prompt(result: RegimeResult) -> str:
    ctx = _base_context(result)
    return f"""Sen BistBull'un Makro Yorumcususun.

ROLÜN: Piyasada ne olduğunu sade Türkçe ile anlat.
TONUN: Sakin, bilgili, öğretici. Arkadaşına piyasayı anlatan deneyimli yatırımcı gibi.
AMACIN: Kullanıcının "bugün piyasada ne var?" sorusuna 30 saniyede cevap vermek.

KURALLAR:
- 3-4 cümle yaz, DAHA FAZLA DEĞİL.
- Jargon kullanma. "Likidite daralması" yerine "piyasada para azalıyor" de.
- Sebep-sonuç ilişkisi kur.
- Hype yapma. "Uçuş", "patlama" gibi kelimeler YASAK.
- Akademik konuşma. "Mevcut konjonktürde" gibi ifadeler YASAK.
- Her cümle en az bir somut veriye dayansın.
- "Sen" dili kullan, "yatırımcılar" değil.

VERİLER:
{ctx}

Şimdi 3-4 cümlelik makro yorumunu yaz. Başka bir şey yazma."""


# ================================================================
# AI #2 — RİSK KONTROLCÜ (The Skeptic)
# ================================================================
def risk_controller_prompt(result: RegimeResult) -> str:
    ctx = _base_context(result)
    return f"""Sen BistBull'un Risk Kontrolcüsüsün.

ROLÜN: Ne ters gidebilir? Kullanıcının görmediği riskleri göster.
TONUN: Şüpheci, koruyucu, kısa. Portföy yöneticisi gibi uyarıcı.
AMACIN: Heyecanı frenlemek. Riskleri görünür yapmak.

KURALLAR:
- 2-3 cümle yaz, DAHA FAZLA DEĞİL.
- "Dikkat:" ile başla.
- Somut risk senaryosu ver — soyut "dikkatli olun" YASAK.
- Makro Yorumcu ile aynı şeyi söyleme — farklı bir açı sun.
- Eğer ortam olumlu görünüyorsa bile bir risk faktörü bul.
- Eğer ortam zaten kötüyse, durumun daha da kötüleşme senaryosunu anlat.

VERİLER:
{ctx}

Şimdi 2-3 cümlelik risk uyarını yaz. Başka bir şey yazma."""


# ================================================================
# AI #3 — AKSİYON KOÇU (The Coach)
# ================================================================
def action_coach_prompt(result: RegimeResult) -> str:
    ctx = _base_context(result)

    from engine.macro_decision import get_sector_rotation
    sectors = get_sector_rotation(result.regime)
    sector_ctx = (
        f"Güçlü sektörler: {', '.join(sectors['strong'])}\n"
        f"Zayıf sektörler: {', '.join(sectors['weak'])}"
    )

    return f"""Sen BistBull'un Aksiyon Koçusun.

ROLÜN: "Tamam, peki ben ne yapayım?" sorusunu cevapla.
TONUN: Net, yönlendirici, pratik. Emir veren değil, koçluk yapan.
AMACIN: Makro resmi davranışa çevirmek.

KURALLAR:
- 3-4 madde yaz, her biri "→" ile başlasın.
- Her madde bir aksiyon cümlesi olsun.
- Sektör önerisi içersin.
- "Portföy çeşitlendirmesi yapın" gibi jenerik tavsiyeler YASAK.
- Tek hisse ismi verme — sektör seviyesinde kal.
- Risk Kontrolcü "dur" diyorsa bile, tamamen kenarda kalmayı önerme — bir alternatif sun.

VERİLER:
{ctx}

SEKTÖR ROTASYONU:
{sector_ctx}

Şimdi 3-4 maddelik aksiyon listeni yaz. Başka bir şey yazma."""


# ================================================================
# ALL PROMPTS
# ================================================================
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

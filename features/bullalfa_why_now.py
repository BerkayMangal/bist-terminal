# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# features/bullalfa_why_now.py
#
# §18 — `why_now` Turkish bullet generation.
#
# 2–4 short bullets per signal, mode-routed. The user must read every
# bullet in under 2 seconds. Concrete numbers > vague adjectives.
#
# Phrasing patterns (locked by spec; do not improvise alternatives):
#
#   HIZLI / SWING:
#     "20g kırılım"                               (E4 fired on 20d high)
#     "55g kırılım"                               (E4 fired on 55d high)
#     "Hacim {rvol:.1f}x ortalama"                (E3 rvol)
#     "Endeksten %{rs_20d:+.1f} güçlü"            (E2 rs_short)
#     "Volatilite daralmış, açılım başladı"       (E5 expansion)
#     "Trend EMA20/50/200 dizilimi"               (E1 SWING stack)
#
#   POZİSYON:
#     "Kaliteli iş modeli (TEMEL {temel_score})"
#     "EMA200 üzerinde, 60g RS pozitif"
#     "Değerleme makul ({fk_oran} F/K)"
#
#   TOPLANIYOR:
#     "Sessiz hacimlenme (rvol 5g ort. {rvol_5d:.2f})"
#     "BB genişliği 60g'nin alt %{bb_pct}'inde"
#     "ADX yükseliyor, trend şekilleniyor"
#     "Yüksek dipler — yapı sıkışıyor"
#
#   UZAK DUR:
#     "RSI {rsi}, son 5g %{ret_5d:+.1f} — yorgun"
#     "Hacim azalıyor, ters dönüş barı"
#
#   SAKİN:
#     (no why_now — UI shows single line:
#      "Şu an dikkat çekici bir kurulum yok")
# ================================================================

from __future__ import annotations

from typing import Optional

__all__ = [
    "why_now",
    "SAKIN_SINGLE_LINE_TR",
    "MAX_BULLETS",
    "MIN_BULLETS",
]


SAKIN_SINGLE_LINE_TR = "Şu an dikkat çekici bir kurulum yok"

MAX_BULLETS = 4
MIN_BULLETS = 2


# ----------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------

def why_now(
    *,
    mode: str,
    engines: Optional[dict] = None,
    quality: Optional[dict] = None,
    valuation: Optional[dict] = None,
    technicals: Optional[dict] = None,
    toplaniyor: Optional[dict] = None,
) -> list[str]:
    """Return 2–4 Turkish bullets for the §18 `why_now` field.

    The function is total; missing data simply yields fewer bullets
    (down to an empty list if nothing is available). The orchestrator
    is responsible for ensuring at least the minimum signal-quality
    floor before emitting an actionable mode, so the empty-list case
    in practice only occurs for SAKİN (which renders the single-line
    fallback in the UI) or for severely degraded data.

    Args (all optional dicts; see schema below):
      engines: {
        e2_score: 0|0.5|1, rs_short: float, rs_long: float,
        e3_rvol: float, e4_breakout_type: "20d"|"55d"|"6m"|None,
        e4_bars_ago: int, e5_expansion: bool, e1_pass: bool,
        e7_exhaustion: float
      }
      quality: { temel_score: int }
      valuation: { fk_oran: float | None }
      technicals: { ema200_above: bool, rs_60d_positive: bool,
                    rsi: int|None, ret_5d: float|None,
                    rvol_today_drop: bool, reversal_bar: bool }
      toplaniyor: { rvol_5d_avg: float, bb_pctile: int (0-100),
                    adx_rising: bool, higher_lows: bool }

    Bullets are returned in priority order; the renderer should slice
    to MAX_BULLETS but display all returned items if it has space.

    SAKİN: returns []. UI shows SAKIN_SINGLE_LINE_TR as a static line
    instead of bullet points (per §18 / §23).
    """
    m = (mode or "").upper().replace("İ", "I")  # tolerant comparison

    if m == "SAKIN":
        return []

    if mode in {"HIZLI", "SWING"}:
        return _hizli_swing(engines or {})

    if mode == "POZİSYON":
        return _pozisyon(quality or {}, technicals or {}, valuation or {})

    if mode == "TOPLANIYOR":
        return _toplaniyor(toplaniyor or {})

    if m in {"UZAK DUR", "UZAK_DUR"}:
        return _uzak_dur(technicals or {})

    return []


# ----------------------------------------------------------------
# Per-mode generators
# ----------------------------------------------------------------

def _hizli_swing(eng: dict) -> list[str]:
    bullets: list[str] = []

    bo_type = eng.get("e4_breakout_type")
    bars_ago = eng.get("e4_bars_ago")
    if bo_type == "20d" and bars_ago is not None and bars_ago <= 1:
        bullets.append("20g kırılım")
    elif bo_type == "55d" and bars_ago is not None and bars_ago <= 1:
        bullets.append("55g kırılım")
    elif bo_type == "6m" and bars_ago is not None and bars_ago <= 1:
        # Spec only lists 20g/55g phrasing explicitly; 6m falls out of
        # the same family. Use the same shape for consistency.
        bullets.append("6a kırılım")

    rvol = eng.get("e3_rvol")
    if isinstance(rvol, (int, float)) and rvol > 0:
        bullets.append(f"Hacim {float(rvol):.1f}x ortalama")

    rs_short = eng.get("rs_short")
    if isinstance(rs_short, (int, float)):
        # rs_short is a return spread expressed as a fraction (e.g. 0.04
        # = 4 pp). The §18 example shows the percent-point form.
        pp = float(rs_short) * 100.0
        # Only mention RS when the spread is meaningful — ±1 pp jitter
        # is noise on 20-bar windows.
        if abs(pp) >= 1.0:
            bullets.append(f"Endeksten %{pp:+.1f} güçlü")

    if eng.get("e5_expansion") is True:
        bullets.append("Volatilite daralmış, açılım başladı")

    if eng.get("e1_pass") is True:
        # Spec phrasing names all three EMAs; only emit when E1 passes
        # in a context where the stack is in fact aligned.
        bullets.append("Trend EMA20/50/200 dizilimi")

    return _trim(bullets)


def _pozisyon(quality: dict, tech: dict, val: dict) -> list[str]:
    bullets: list[str] = []

    temel = quality.get("temel_score")
    if isinstance(temel, (int, float)):
        bullets.append(f"Kaliteli iş modeli (TEMEL {int(round(float(temel)))})")

    if tech.get("ema200_above") is True and tech.get("rs_60d_positive") is True:
        bullets.append("EMA200 üzerinde, 60g RS pozitif")
    elif tech.get("ema200_above") is True:
        # Partial match — ema200 alone still informative for POZİSYON.
        bullets.append("EMA200 üzerinde")

    fk = val.get("fk_oran")
    if isinstance(fk, (int, float)) and fk > 0 and fk < 100:
        # Render with at most one decimal; whole numbers without it.
        if abs(fk - round(fk)) < 0.05:
            bullets.append(f"Değerleme makul ({int(round(fk))} F/K)")
        else:
            bullets.append(f"Değerleme makul ({float(fk):.1f} F/K)")

    return _trim(bullets)


def _toplaniyor(top: dict) -> list[str]:
    bullets: list[str] = []

    rvol5 = top.get("rvol_5d_avg")
    if isinstance(rvol5, (int, float)) and rvol5 > 0:
        bullets.append(f"Sessiz hacimlenme (rvol 5g ort. {float(rvol5):.2f})")

    bb_pct = top.get("bb_pctile")
    if isinstance(bb_pct, (int, float)):
        # bb_pctile is an integer percentile (e.g. 25). Spec phrasing
        # is "BB genişliği 60g'nin alt %{bb_pct}'inde".
        bullets.append(f"BB genişliği 60g'nin alt %{int(round(float(bb_pct)))}'inde")

    if top.get("adx_rising") is True:
        bullets.append("ADX yükseliyor, trend şekilleniyor")

    if top.get("higher_lows") is True:
        bullets.append("Yüksek dipler — yapı sıkışıyor")

    return _trim(bullets)


def _uzak_dur(tech: dict) -> list[str]:
    bullets: list[str] = []

    rsi = tech.get("rsi")
    ret_5d = tech.get("ret_5d")
    if isinstance(rsi, (int, float)) and isinstance(ret_5d, (int, float)):
        # ret_5d is a fraction (e.g. -0.085 → -8.5%); spec template
        # spells "%{ret_5d:+.1f}" which assumes the percent form.
        pp = float(ret_5d) * 100.0
        bullets.append(f"RSI {int(round(float(rsi)))}, son 5g %{pp:+.1f} — yorgun")

    if tech.get("rvol_today_drop") is True or tech.get("reversal_bar") is True:
        bullets.append("Hacim azalıyor, ters dönüş barı")

    return _trim(bullets)


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

def _trim(bullets: list[str]) -> list[str]:
    """Cap at MAX_BULLETS; preserve order; deduplicate while preserving order.

    No padding — if the generators produce fewer than MIN_BULLETS, the
    UI is responsible for falling back gracefully (e.g. for severely
    degraded data the SAKİN single-line template applies).
    """
    seen: set[str] = set()
    deduped: list[str] = []
    for b in bullets:
        if b in seen:
            continue
        seen.add(b)
        deduped.append(b)
        if len(deduped) >= MAX_BULLETS:
            break
    return deduped

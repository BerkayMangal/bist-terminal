# ================================================================
# BISTBULL TERMINAL — TIMING & SIGNAL INTELLIGENCE
# engine/timing_intel.py
#
# Converts technical jargon into plain-language timing context.
# Reuses existing scores — zero extra computation.
# Never crashes, never gives buy/sell signals.
# ================================================================
from __future__ import annotations
import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.timing_intel")


def build_timing_intel(scores: dict, tech: Optional[dict], metrics: dict) -> dict:
    """Master entry. Returns timing_intel dict. Never raises."""
    try:
        return _build(scores, tech, metrics)
    except Exception as exc:
        log.warning(f"timing_intel failed: {exc}")
        return _empty()


def _build(scores: dict, tech: Optional[dict], m: dict) -> dict:
    mom = scores.get("momentum", 50)
    tb = scores.get("tech_break", 50)
    inst = scores.get("inst_flow", 50)
    ivme = (mom * 0.4 + tb * 0.35 + inst * 0.25)

    # ── Timing state ─────────────────────────────────────────────
    state, state_conf = _timing_state(ivme, tech, m)

    # ── Recent activity (what changed) ───────────────────────────
    recent = _recent_activity(tech, m)

    # ── Watch points (what to watch) ─────────────────────────────
    watch = _watch_points(tech, m, scores)

    # ── Signal summary (plain language) ──────────────────────────
    signals = _signal_summary(tech, scores)

    # ── Trend timeline ───────────────────────────────────────────
    timeline = _trend_timeline(tech)

    return {
        "timing_intel": {
            "state": state,
            "confidence": state_conf,
            "text": _state_text(state),
        },
        "recent_activity": recent,
        "watch_points": watch,
        "signal_summary": signals,
        "trend_timeline": timeline,
    }


# ── Timing state logic ──────────────────────────────────────────

def _timing_state(ivme: float, tech: Optional[dict], m: dict) -> tuple[str, str]:
    if tech is None:
        return "belirsiz", "low"

    rsi = tech.get("rsi") or 50
    vol = tech.get("vol_ratio") or 1.0
    macd_bull = tech.get("macd_bullish", False)
    pct_20d = tech.get("pct_20d") or 0

    score = 0

    # Momentum component
    if ivme >= 60: score += 2
    elif ivme >= 45: score += 1

    # RSI sweet spot (not overbought, not oversold)
    if 35 <= rsi <= 65: score += 1
    elif rsi < 30: score += 1  # oversold = potential entry
    elif rsi > 75: score -= 1  # overbought = wait

    # MACD direction
    if macd_bull: score += 1

    # Volume confirmation
    if vol > 1.3: score += 1

    # Recent trend not exhausted
    if -10 < pct_20d < 20: score += 1
    elif pct_20d > 30: score -= 1  # too extended

    # Determine state
    if score >= 4:
        state = "uygun"
    elif score >= 2:
        state = "erken"
    else:
        state = "bekle"

    # Confidence from data availability
    data_count = sum(1 for k in ("rsi", "vol_ratio", "macd_bullish", "pct_20d", "ma50")
                     if tech.get(k) is not None)
    conf = "high" if data_count >= 4 else "medium" if data_count >= 2 else "low"

    return state, conf


def _state_text(state: str) -> str:
    return {
        "uygun": "Zamanlama uygun görünüyor",
        "erken": "Biraz erken olabilir",
        "bekle": "Şu an beklemek daha mantıklı",
        "belirsiz": "Teknik veri yetersiz",
    }.get(state, "Değerlendirme yapılamadı")


# ── Recent activity ──────────────────────────────────────────────

def _recent_activity(tech: Optional[dict], m: dict) -> list[str]:
    items: list[str] = []
    if tech is None:
        return items

    pct_20d = tech.get("pct_20d")
    if pct_20d is not None:
        if pct_20d > 5:
            items.append(f"Son 20 günde fiyat %{pct_20d:.0f} yükseldi")
        elif pct_20d < -5:
            items.append(f"Son 20 günde fiyat %{abs(pct_20d):.0f} düştü")
        else:
            items.append("Fiyat son 20 günde yatay seyretti")

    vol = tech.get("vol_ratio")
    if vol is not None:
        if vol > 1.5:
            items.append(f"Hacim ortalamanın {vol:.1f}x üzerinde — ilgi artmış")
        elif vol < 0.6:
            items.append("Hacim düşük — piyasa ilgisi zayıf")

    rsi = tech.get("rsi")
    if rsi is not None:
        if rsi > 70:
            items.append("Momentum güçlü ama aşırı alım bölgesine yakın")
        elif rsi < 30:
            items.append("Aşırı satım bölgesinde — toparlanma potansiyeli olabilir")

    macd_cross = tech.get("macd_cross")
    if macd_cross == "bullish":
        items.append("Kısa vadeli trend yukarı dönüyor")
    elif macd_cross == "bearish":
        items.append("Kısa vadeli trend aşağı dönüyor")

    return items[:3]


# ── Watch points ─────────────────────────────────────────────────

def _watch_points(tech: Optional[dict], m: dict, scores: dict) -> list[str]:
    items: list[str] = []
    if tech is None:
        return ["Teknik veri oluştuğunda zamanlama daha net okunabilir"]

    price = tech.get("price") or m.get("price")
    ma50 = tech.get("ma50")
    ma200 = tech.get("ma200")
    bb_upper = tech.get("bb_upper")
    high_52w = tech.get("high_52w")

    if ma50 and price and price < ma50:
        items.append(f"{ma50:.0f} TL (MA50) üzeri kapanış olursa trend güçlenir")
    elif ma50 and price and price > ma50 and ma200 and price < ma200:
        items.append(f"{ma200:.0f} TL (MA200) üzerini kırarsa orta vadeli trend olumlu döner")

    vol = tech.get("vol_ratio")
    if vol is not None and vol < 1.0:
        items.append("Hacim artışı gelmezse hareket zayıf kalabilir")

    if high_52w and price:
        dist = (high_52w - price) / high_52w * 100
        if dist < 5:
            items.append("52 haftalık zirveye yakın — kırılım veya geri çekilme izlenmeli")
        elif dist > 30:
            items.append("Zirveden uzak — toparlanma sinyalleri önemli")

    mom = scores.get("momentum", 50)
    if mom < 35:
        items.append("Momentum skorunun yükselmesi olumlu sinyal olur")

    return items[:3]


# ── Signal summary (jargon-free) ─────────────────────────────────

def _signal_summary(tech: Optional[dict], scores: dict) -> list[str]:
    items: list[str] = []
    if tech is None:
        return items

    # Trend direction
    ma50 = tech.get("ma50")
    ma200 = tech.get("ma200")
    price = tech.get("price")
    if ma50 and ma200:
        if ma50 > ma200:
            items.append("Orta vadeli trend yukarı yönlü")
        else:
            items.append("Orta vadeli trend aşağı yönlü")

    # Momentum
    mom = scores.get("momentum", 50)
    if mom >= 65:
        items.append("Fiyat hareketi güçlü görünüyor")
    elif mom <= 35:
        items.append("Fiyat son dönemde güç kaybetti")

    # Volume
    vol = tech.get("vol_ratio")
    if vol and vol > 1.5:
        items.append("Yüksek hacim — piyasa bu hisseye ilgi gösteriyor")

    # BB position
    bb = tech.get("bb_pos")
    if bb and bb > 0.9:
        items.append("Fiyat üst bandına yakın — kısa vadede geri çekilme olabilir")
    elif bb and bb < 0.1:
        items.append("Fiyat alt bandına yakın — toparlanma fırsatı olabilir")

    return items[:3]


# ── Trend timeline ───────────────────────────────────────────────

def _trend_timeline(tech: Optional[dict]) -> dict:
    if tech is None:
        return {}

    rsi = tech.get("rsi") or 50
    pct_20d = tech.get("pct_20d") or 0
    macd_bull = tech.get("macd_bullish", False)

    # Approximate short/medium from available data
    short = "güçlü" if macd_bull and rsi > 50 else "zayıf" if not macd_bull and rsi < 45 else "nötr"
    medium = "güçlü" if pct_20d > 8 else "zayıf" if pct_20d < -8 else "toparlıyor" if 0 < pct_20d <= 8 else "nötr"

    ma50 = tech.get("ma50")
    ma200 = tech.get("ma200")
    price = tech.get("price")
    if ma50 and ma200 and price:
        long_ = "güçlü" if price > ma50 > ma200 else "zayıf" if price < ma50 < ma200 else "karışık"
    else:
        long_ = "belirsiz"

    return {"kısa_vade": short, "orta_vade": medium, "uzun_vade": long_}


def _empty() -> dict:
    return {
        "timing_intel": {"state": "belirsiz", "confidence": "low", "text": "Değerlendirme yapılamadı"},
        "recent_activity": [], "watch_points": [], "signal_summary": [], "trend_timeline": {},
    }

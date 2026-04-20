from __future__ import annotations
import logging; from typing import Optional
log = logging.getLogger("bistbull.timing_intel")

def _sf(v, d=0.0):
    """Safe float — handles None, str, anything."""
    if v is None: return d
    try: return float(v)
    except: return d

def build_timing_intel(scores, tech, metrics):
    try: return _build(scores, tech, metrics)
    except Exception as e: log.warning(f"timing_intel failed: {e}"); return _empty()

def _build(scores, tech, m):
    mom = _sf(scores.get("momentum"), 50)
    tb = _sf(scores.get("tech_break"), 50)
    inst = _sf(scores.get("inst_flow"), 50)
    ivme = mom * 0.4 + tb * 0.35 + inst * 0.25
    state, conf = _timing_state(ivme, tech)
    return {
        "timing_intel": {"state": state, "confidence": conf, "text": _text(state)},
        "recent_activity": _recent(tech),
        "watch_points": _watch(tech, scores),
        "signal_summary": _signals(tech, scores),
        "trend_timeline": _timeline(tech),
    }

def _timing_state(ivme, tech):
    if tech is None: return "belirsiz", "low"
    rsi = _sf(tech.get("rsi"), 50)
    vol = _sf(tech.get("vol_ratio"), 1.0)
    macd_bull = bool(tech.get("macd_bullish"))
    pct = _sf(tech.get("pct_20d"), 0)
    sc = 0
    if ivme >= 60: sc += 2
    elif ivme >= 45: sc += 1
    if 35 <= rsi <= 65: sc += 1
    elif rsi < 30: sc += 1
    elif rsi > 75: sc -= 1
    if macd_bull: sc += 1
    if vol > 1.3: sc += 1
    if -10 < pct < 20: sc += 1
    elif pct > 30: sc -= 1
    state = "uygun" if sc >= 4 else "erken" if sc >= 2 else "bekle"
    dc = sum(1 for k in ("rsi", "vol_ratio", "macd_bullish", "pct_20d", "ma50") if tech.get(k) is not None)
    return state, ("high" if dc >= 4 else "medium" if dc >= 2 else "low")

def _text(s):
    return {"uygun": "Zamanlama uygun görünüyor", "erken": "Biraz erken olabilir",
            "bekle": "Şu an beklemek daha mantıklı", "belirsiz": "Teknik veri yetersiz"}.get(s, "")

def _recent(tech):
    if tech is None: return []
    items = []
    pct = _sf(tech.get("pct_20d"))
    if pct > 5: items.append(f"Son 20 günde fiyat %{pct:.0f} yükseldi")
    elif pct < -5: items.append(f"Son 20 günde fiyat %{abs(pct):.0f} düştü")
    else: items.append("Fiyat son 20 günde yatay seyretti")
    vol = _sf(tech.get("vol_ratio"))
    if vol > 1.5: items.append(f"Hacim ortalamanın {vol:.1f}x üzerinde")
    elif vol > 0 and vol < 0.6: items.append("Hacim düşük")
    rsi = _sf(tech.get("rsi"))
    if rsi > 70: items.append("Aşırı alım bölgesine yakın")
    elif rsi > 0 and rsi < 30: items.append("Aşırı satım bölgesinde")
    mc = tech.get("macd_cross")
    if mc == "bullish": items.append("Kısa vadeli trend yukarı dönüyor")
    elif mc == "bearish": items.append("Kısa vadeli trend aşağı dönüyor")
    return items[:3]

def _watch(tech, scores):
    if tech is None: return ["Teknik veri oluştuğunda zamanlama daha net okunabilir"]
    items = []
    price = _sf(tech.get("price")); ma50 = _sf(tech.get("ma50")); ma200 = _sf(tech.get("ma200"))
    if ma50 > 0 and price > 0 and price < ma50:
        items.append(f"{ma50:.0f} TL (MA50) üzeri kapanış olursa trend güçlenir")
    elif ma50 > 0 and ma200 > 0 and price > ma50 and price < ma200:
        items.append(f"{ma200:.0f} TL (MA200) üzerini kırarsa trend döner")
    vol = _sf(tech.get("vol_ratio"))
    if 0 < vol < 1.0: items.append("Hacim artışı gelmezse hareket zayıf kalabilir")
    h52 = _sf(tech.get("high_52w"))
    if h52 > 0 and price > 0:
        dist = (h52 - price) / h52 * 100
        if dist < 5: items.append("52h zirveye yakın — kırılım veya geri çekilme izlenmeli")
        elif dist > 30: items.append("Zirveden uzak — toparlanma sinyalleri önemli")
    if _sf(scores.get("momentum"), 50) < 35: items.append("Momentum skorunun yükselmesi olumlu sinyal olur")
    return items[:3]

def _signals(tech, scores):
    if tech is None: return []
    items = []
    ma50 = _sf(tech.get("ma50")); ma200 = _sf(tech.get("ma200"))
    if ma50 > 0 and ma200 > 0:
        items.append("Orta vadeli trend yukarı yönlü" if ma50 > ma200 else "Orta vadeli trend aşağı yönlü")
    mom = _sf(scores.get("momentum"), 50)
    if mom >= 65: items.append("Fiyat hareketi güçlü görünüyor")
    elif mom <= 35: items.append("Fiyat son dönemde güç kaybetti")
    vol = _sf(tech.get("vol_ratio"))
    if vol > 1.5: items.append("Yüksek hacim — piyasa ilgi gösteriyor")
    return items[:3]

def _timeline(tech):
    if tech is None: return {}
    rsi = _sf(tech.get("rsi"), 50); pct = _sf(tech.get("pct_20d")); mb = bool(tech.get("macd_bullish"))
    short = "güçlü" if mb and rsi > 50 else "zayıf" if not mb and rsi < 45 else "nötr"
    med = "güçlü" if pct > 8 else "zayıf" if pct < -8 else "toparlıyor" if 0 < pct <= 8 else "nötr"
    p = _sf(tech.get("price")); m50 = _sf(tech.get("ma50")); m200 = _sf(tech.get("ma200"))
    lng = "güçlü" if p > m50 > m200 > 0 else "zayıf" if 0 < p < m50 < m200 else "karışık" if m50 > 0 else "belirsiz"
    return {"kısa_vade": short, "orta_vade": med, "uzun_vade": lng}

def _empty():
    return {"timing_intel": {"state": "belirsiz", "confidence": "low", "text": ""}, "recent_activity": [], "watch_points": [], "signal_summary": [], "trend_timeline": {}}

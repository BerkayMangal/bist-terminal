# ================================================================
# BISTBULL TERMINAL — DIMENSION EXPLAINER
# engine/dimension_explainer.py
#
# Generates plain-language one-line explanation for each score dimension.
# Dynamic: text changes based on score + relevant metrics.
# ================================================================
from __future__ import annotations
import logging
log = logging.getLogger("bistbull.dimension_explainer")

def _sf(v, d=0.0):
    if v is None: return d
    try: return float(v)
    except: return d


def build_dimension_explanations(scores: dict, metrics: dict) -> dict:
    """Returns {dimension: explanation_text} for each score dimension."""
    try:
        return _build(scores, metrics)
    except Exception as e:
        log.debug(f"dimension_explainer failed: {e}")
        return {}


def _build(s: dict, m: dict) -> dict:
    ex = {}
    
    # Value
    v = _sf(s.get("value"), 50)
    pe = m.get("pe")
    if v >= 65:
        ex["value"] = f"Hisse ucuz tarafta görünüyor{f' (F/K: {pe:.1f})' if pe else ''}"
    elif v >= 45:
        ex["value"] = "Değerleme makul aralıkta"
    else:
        ex["value"] = f"Pahalı tarafta görünüyor{f' (F/K: {pe:.1f})' if pe else ''}"
    
    # Quality
    q = _sf(s.get("quality"), 50)
    roe = _sf(m.get("roe"))
    if q >= 65:
        ex["quality"] = f"Şirket kârlı ve verimli{f' — ROE %{roe*100:.0f}' if roe > 0 else ''}"
    elif q >= 45:
        ex["quality"] = "Kârlılık orta seviyede"
    else:
        ex["quality"] = "Kârlılık zayıf veya baskı altında"
    
    # Growth
    g = _sf(s.get("growth"), 50)
    rg = m.get("revenue_growth")
    if g >= 65:
        ex["growth"] = f"Büyüme güçlü{f' — gelir %{_sf(rg)*100:.0f} arttı' if rg else ''}"
    elif g >= 45:
        ex["growth"] = "Büyüme orta seviyede"
    else:
        ex["growth"] = "Büyüme yavaş veya geriliyor"
    
    # Balance
    b = _sf(s.get("balance"), 50)
    de = m.get("debt_equity")
    if b >= 65:
        ex["balance"] = f"Bilanço sağlam{f' — borç/özkaynak: {_sf(de):.1f}' if de else ''}"
    elif b >= 45:
        ex["balance"] = "Borç seviyesi kabul edilebilir"
    else:
        ex["balance"] = "Borç yüksek — faiz ortamında baskı riski"
    
    # Earnings (Kâr Kalitesi)
    e = _sf(s.get("earnings"), 50)
    cfo = _sf(m.get("cfo_to_ni"))
    if e >= 65:
        ex["earnings"] = f"Kazandığı para gerçek{f' — nakit/kâr oranı {cfo:.1f}' if cfo > 0 else ''}"
    elif e >= 45:
        ex["earnings"] = "Nakit akışı kârı kısmen destekliyor"
    else:
        ex["earnings"] = "Kâr var ama nakit tarafı zayıf — dikkatli oku"
    
    # Moat
    mo = _sf(s.get("moat"), 50)
    if mo >= 65:
        ex["moat"] = "Rekabet avantajı var — marjlar stabil"
    elif mo >= 45:
        ex["moat"] = "Orta düzey rekabet gücü"
    else:
        ex["moat"] = "Rekabet avantajı zayıf görünüyor"
    
    # Capital
    c = _sf(s.get("capital"), 50)
    if c >= 65:
        ex["capital"] = "Yönetim sermayeyi verimli kullanıyor"
    elif c >= 45:
        ex["capital"] = "Sermaye tahsisi orta seviyede"
    else:
        ex["capital"] = "Sermaye kullanımı verimsiz görünüyor"
    
    # Momentum
    mom = _sf(s.get("momentum"), 50)
    if mom >= 65:
        ex["momentum"] = "Fiyat hareketi güçlü — trend yukarı"
    elif mom >= 45:
        ex["momentum"] = "Momentum nötr — net yön yok"
    else:
        ex["momentum"] = "Fiyat baskı altında — trend aşağı"
    
    return ex

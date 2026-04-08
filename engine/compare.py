# ================================================================
# BISTBULL TERMINAL — STOCK COMPARISON ENGINE
# engine/compare.py
# ================================================================
from __future__ import annotations
import logging
log = logging.getLogger("bistbull.compare")

def _sf(v, d=0.0):
    if v is None: return d
    try: return float(v)
    except: return d

DIMS = [
    ("value", "Değerleme", "scores", "value"),
    ("quality", "Kalite", "scores", "quality"),
    ("growth", "Büyüme", "scores", "growth"),
    ("balance", "Bilanço", "scores", "balance"),
    ("earnings", "Kâr Kalitesi", "scores", "earnings"),
    ("momentum", "Momentum", "scores", "momentum"),
]

def compare_stocks(left: dict, right: dict) -> dict:
    """Compare two analysis results. Never raises."""
    try:
        return _compare(left, right)
    except Exception as e:
        log.warning(f"compare failed: {e}")
        return {"error": str(e)}

def _compare(L: dict, R: dict) -> dict:
    lt = L.get("ticker", "?")
    rt = R.get("ticker", "?")
    ls = L.get("scores", {})
    rs = R.get("scores", {})

    # Dimension winners
    dims = {}
    diffs = []
    for key, label, src, field in DIMS:
        lv = _sf(L.get(src, {}).get(field))
        rv = _sf(R.get(src, {}).get(field))
        diff = lv - rv
        if abs(diff) < 3:
            dims[key] = "eşit"
        elif diff > 0:
            dims[key] = "left"
            if abs(diff) >= 8:
                diffs.append(f"{lt} {label.lower()} tarafında daha güçlü (+{diff:.0f})")
        else:
            dims[key] = "right"
            if abs(diff) >= 8:
                diffs.append(f"{rt} {label.lower()} tarafında daha güçlü (+{abs(diff):.0f})")

    # Overall / valuation
    lo = _sf(L.get("overall") or L.get("deger"))
    ro = _sf(R.get("overall") or R.get("deger"))
    li = _sf(L.get("ivme"))
    ri = _sf(R.get("ivme"))

    # Valuation
    lv_base = _sf((L.get("valuation") or {}).get("vs_price"))
    rv_base = _sf((R.get("valuation") or {}).get("vs_price"))
    if lv_base > rv_base + 10:
        diffs.append(f"{lt} değerleme tarafında daha iskontolu görünüyor")
    elif rv_base > lv_base + 10:
        diffs.append(f"{rt} değerleme tarafında daha iskontolu görünüyor")

    # Timing
    lt_state = (L.get("timing_intel") or {}).get("state", "")
    rt_state = (R.get("timing_intel") or {}).get("state", "")
    if lt_state == "uygun" and rt_state != "uygun":
        diffs.append(f"{lt} zamanlama tarafında daha uygun")
    elif rt_state == "uygun" and lt_state != "uygun":
        diffs.append(f"{rt} zamanlama tarafında daha uygun")

    # Profit quality
    lpq = (L.get("turkey_context") or {}).get("profit_quality_interpretation", {}).get("level", "")
    rpq = (R.get("turkey_context") or {}).get("profit_quality_interpretation", {}).get("level", "")
    pq_rank = {"iyi": 3, "orta": 2, "zayıf": 1}
    if pq_rank.get(lpq, 0) > pq_rank.get(rpq, 0):
        dims["profit_quality"] = "left"
    elif pq_rank.get(rpq, 0) > pq_rank.get(lpq, 0):
        dims["profit_quality"] = "right"
    else:
        dims["profit_quality"] = "eşit"

    # Risk
    lr = _sf(L.get("risk_score"))
    rr = _sf(R.get("risk_score"))
    if lr > rr + 3:
        dims["risk"] = "left"  # less negative = better
    elif rr > lr + 3:
        dims["risk"] = "right"
    else:
        dims["risk"] = "eşit"

    # Summary sentence
    if lo > ro + 5:
        summary = f"{lt} genel skorda öne çıkıyor ama detaylara bakmak gerekir."
    elif ro > lo + 5:
        summary = f"{rt} genel skorda öne çıkıyor ama detaylara bakmak gerekir."
    else:
        summary = "İki hisse genel skorda yakın — fark detaylarda."

    # Conclusion
    left_wins = sum(1 for v in dims.values() if v == "left")
    right_wins = sum(1 for v in dims.values() if v == "right")
    if left_wins > right_wins + 2:
        conclusion = f"{lt} birçok boyutta öne çıkıyor, ama karar ne aradığına bağlı."
    elif right_wins > left_wins + 2:
        conclusion = f"{rt} birçok boyutta öne çıkıyor, ama karar ne aradığına bağlı."
    else:
        conclusion = "Biri daha ucuz, diğeri daha güçlü büyüyor olabilir. Karar, ne aradığına göre değişir."

    return {
        "left_ticker": lt,
        "right_ticker": rt,
        "summary": summary,
        "key_differences": diffs[:4],
        "dimensions": dims,
        "scores": {
            "left": {"overall": lo, "ivme": li, "fa": _sf(L.get("fa_score")), "risk": lr},
            "right": {"overall": ro, "ivme": ri, "fa": _sf(R.get("fa_score")), "risk": rr},
        },
        "conclusion": conclusion,
    }

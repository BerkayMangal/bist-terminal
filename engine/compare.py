# ================================================================
# BISTBULL TERMINAL — STOCK COMPARISON ENGINE
# engine/compare.py
#
# Smart, data-grounded comparison with analyst-style deterministic
# commentary + optional AI enhancement.
# ================================================================
from __future__ import annotations
import logging
log = logging.getLogger("bistbull.compare")

def _sf(v, d=0.0):
    if v is None: return d
    try: return float(v)
    except: return d

def _fmt(v, suffix=""):
    if v is None: return "—"
    try:
        f = float(v)
        if abs(f) >= 100: return f"{f:.0f}{suffix}"
        return f"{f:.1f}{suffix}"
    except: return "—"

DIMS = [
    ("value", "Değerleme", "scores", "value"),
    ("quality", "Kalite", "scores", "quality"),
    ("growth", "Büyüme", "scores", "growth"),
    ("balance", "Bilanço", "scores", "balance"),
    ("earnings", "Kâr Kalitesi", "scores", "earnings"),
    ("momentum", "Momentum", "scores", "momentum"),
]

def compare_stocks(left: dict, right: dict) -> dict:
    """Compare two analysis results. Returns rich comparison with
    analyst-style deterministic commentary. Never raises."""
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
    lm = L.get("metrics", {})
    rm = R.get("metrics", {})

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
        dims["risk"] = "left"
    elif rr > lr + 3:
        dims["risk"] = "right"
    else:
        dims["risk"] = "eşit"

    left_wins = sum(1 for v in dims.values() if v == "left")
    right_wins = sum(1 for v in dims.values() if v == "right")

    # ================================================================
    # ANALYST-STYLE DETERMINISTIC COMMENTARY
    # ================================================================
    commentary = _build_smart_commentary(
        lt, rt, L, R, ls, rs, lm, rm,
        lo, ro, li, ri, lr, rr, dims, left_wins, right_wins
    )

    return {
        "left_ticker": lt,
        "right_ticker": rt,
        "summary": commentary["headline"],
        "key_differences": diffs[:4],
        "dimensions": dims,
        "scores": {
            "left": {"overall": lo, "ivme": li, "fa": _sf(L.get("fa_score")), "risk": lr},
            "right": {"overall": ro, "ivme": ri, "fa": _sf(R.get("fa_score")), "risk": rr},
        },
        "conclusion": commentary["conclusion"],
        "analyst_commentary": commentary["full"],
        "ai_context": commentary["ai_context"],
    }


def _build_smart_commentary(lt, rt, L, R, ls, rs, lm, rm,
                             lo, ro, li, ri, lr, rr, dims, lw, rw):
    """Build data-grounded comparison commentary. Every sentence tied to numbers."""

    parts = []

    # 1. Headline — who leads and by how much
    if lo > ro + 10:
        headline = f"{lt} genel skorda belirgin öne çıkıyor ({lo:.0f} vs {ro:.0f})."
    elif ro > lo + 10:
        headline = f"{rt} genel skorda belirgin öne çıkıyor ({ro:.0f} vs {lo:.0f})."
    elif abs(lo - ro) <= 5:
        headline = f"{lt} ({lo:.0f}) ve {rt} ({ro:.0f}) genel skorda çok yakın."
    else:
        leader = lt if lo > ro else rt
        headline = f"{leader} genel skorda hafif önde ({max(lo,ro):.0f} vs {min(lo,ro):.0f})."

    parts.append(headline)

    # 2. Value comparison — F/K, PD/DD actual numbers
    lpe = _sf(lm.get("pe")); rpe = _sf(rm.get("pe"))
    lpb = _sf(lm.get("pb")); rpb = _sf(rm.get("pb"))
    if lpe > 0 and rpe > 0:
        if lpe < rpe * 0.7:
            parts.append(f"Değerleme: {lt} daha ucuz (F/K {lpe:.1f} vs {rpe:.1f}).")
        elif rpe < lpe * 0.7:
            parts.append(f"Değerleme: {rt} daha ucuz (F/K {rpe:.1f} vs {lpe:.1f}).")
        else:
            parts.append(f"F/K oranları yakın ({lt}: {lpe:.1f}, {rt}: {rpe:.1f}).")

    # 3. Quality — ROE comparison
    lroe = _sf(lm.get("roe")); rroe = _sf(rm.get("roe"))
    if lroe and rroe:
        if lroe > rroe + 0.05:
            parts.append(f"Karlılık: {lt}'nin ROE'si daha yüksek ({lroe*100:.0f}% vs {rroe*100:.0f}%).")
        elif rroe > lroe + 0.05:
            parts.append(f"Karlılık: {rt}'nin ROE'si daha yüksek ({rroe*100:.0f}% vs {lroe*100:.0f}%).")

    # 4. Growth — revenue growth
    lrg = _sf(lm.get("revenue_growth")); rrg = _sf(rm.get("revenue_growth"))
    if lrg and rrg:
        if lrg > rrg + 0.1:
            parts.append(f"Büyüme: {lt} gelir tarafında daha hızlı ({lrg*100:.0f}% vs {rrg*100:.0f}%).")
        elif rrg > lrg + 0.1:
            parts.append(f"Büyüme: {rt} gelir tarafında daha hızlı ({rrg*100:.0f}% vs {lrg*100:.0f}%).")

    # 5. Momentum
    if li > ri + 10:
        parts.append(f"İvme: {lt} daha güçlü ({li:.0f} vs {ri:.0f}).")
    elif ri > li + 10:
        parts.append(f"İvme: {rt} daha güçlü ({ri:.0f} vs {li:.0f}).")

    # 6. Risk
    if abs(lr - rr) > 5:
        riskier = lt if lr < rr else rt
        safer = rt if lr < rr else lt
        parts.append(f"Risk: {riskier} daha riskli ({min(lr,rr):.0f} vs {max(lr,rr):.0f} risk skoru).")

    # 7. Conclusion
    if lw > rw + 2:
        conclusion = f"{lt} birçok boyutta öne çıkıyor. Ama bu tek başına karar sebebi değil — ne aradığına bağlı."
    elif rw > lw + 2:
        conclusion = f"{rt} birçok boyutta öne çıkıyor. Ama bu tek başına karar sebebi değil — ne aradığına bağlı."
    elif abs(lo - ro) <= 5:
        conclusion = f"İkisi de yakın. {lt} daha çok değer odaklıysa, {rt} daha çok büyüme odaklı olabilir. Ne istediğini bil."
    else:
        conclusion = "Biri daha ucuz, diğeri daha sağlam olabilir. Karar, ne aradığına göre değişir."

    # Full commentary = all parts joined
    full = " ".join(parts[:5])  # cap at 5 sentences

    # Build AI context (structured data for optional AI enhancement)
    ai_context = (
        f"{lt}: Skor={lo:.0f}, İvme={li:.0f}, F/K={_fmt(lm.get('pe'))}, "
        f"ROE={_fmt(lm.get('roe'), '%')}, Büyüme={_fmt(lm.get('revenue_growth'), '%')}, "
        f"Risk={lr:.0f}, Giriş={L.get('entry_label','?')}\n"
        f"{rt}: Skor={ro:.0f}, İvme={ri:.0f}, F/K={_fmt(rm.get('pe'))}, "
        f"ROE={_fmt(rm.get('roe'), '%')}, Büyüme={_fmt(rm.get('revenue_growth'), '%')}, "
        f"Risk={rr:.0f}, Giriş={R.get('entry_label','?')}\n"
        f"Boyut kazananlar: {lt}={lw}, {rt}={rw}"
    )

    return {
        "headline": headline,
        "conclusion": conclusion,
        "full": full,
        "ai_context": ai_context,
    }

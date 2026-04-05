# ================================================================
# BISTBULL TERMINAL — AGGREGATION ENGINE
# engine/aggregation.py
#
# Pure data transformation functions extracted from app.py.
# NO I/O, NO cache, NO AI calls, NO network.
# Every function takes data in, returns data out.
#
# These were previously inline in route handlers, making them
# untestable and tangled with HTTP/cache concerns.
# ================================================================

from __future__ import annotations

from collections import defaultdict
from typing import Optional, Any

from utils.helpers import clean_for_json
from core.response_envelope import now_iso


# ================================================================
# SCAN ITEM — shape analysis result for frontend list display
# ================================================================
def build_scan_item(r: dict) -> dict:
    """Convert a full analysis result into the compact scan-list format.
    Used by /api/top10, /api/scan, and the background scanner."""
    v11 = r.get("v11", {})
    v11l = r.get("v11_labels", {})
    return {
        "ticker": r["ticker"], "name": r["name"],
        "overall": r["overall"], "confidence": r["confidence"],
        "fa_score": r.get("fa_score", r.get("deger", r["overall"])),
        "deger": r.get("deger", r["overall"]),
        "ivme": r.get("ivme", 50), "risk_score": r.get("risk_score", 0),
        "entry_label": r.get("entry_label", ""), "is_hype": r.get("is_hype", False),
        "timing": r.get("timing", ""), "quality_tag": r.get("quality_tag", ""),
        "decision": r.get("decision", ""), "sector_group": r.get("sector_group", ""),
        "style": r["style"], "scores": r["scores"],
        "sector": r.get("sector", ""), "industry": r.get("industry", ""),
        "legendary": r["legendary"], "positives": r["positives"], "negatives": r["negatives"],
        "price": r["metrics"].get("price"), "market_cap": r["metrics"].get("market_cap"),
        "pe": r["metrics"].get("pe"), "pb": r["metrics"].get("pb"),
        "roe": r["metrics"].get("roe"), "revenue_growth": r["metrics"].get("revenue_growth"),
        # V11 enrichment
        "ciro_pd": v11.get("ciro_pd"),
        "ciro_pd_label": v11.get("ciro_pd_label"),
        "is_fatal": v11.get("is_fatal", False),
        "fatal_risks": v11.get("fatal_risks", []),
        "conviction": (v11l.get("conviction") or {}).get("score"),
        "conviction_level": (v11l.get("conviction") or {}).get("level"),
        "earnings_quality_label": (v11l.get("earnings_quality") or {}).get("label"),
        "capital_label": (v11l.get("capital_allocation") or {}).get("label"),
        "regime": v11l.get("regime"),
        "legendary_v11": {
            "buffett_graham": (v11l.get("legendary", {}).get("buffett_graham") or {}).get("passed"),
            "anti_bubble": (v11l.get("legendary", {}).get("anti_bubble") or {}).get("passed"),
            "value_trap": (v11l.get("legendary", {}).get("value_trap") or {}).get("passed"),
        },
        "data_quality_tier": r.get("data_quality_tier", "full"),
    }


def build_batch_item(r: dict) -> dict:
    """Compact item shape for /api/batch responses."""
    return {
        "ticker": r["ticker"], "name": r["name"],
        "overall": r["overall"], "confidence": r["confidence"],
        "style": r["style"], "scores": r["scores"],
        "legendary": r["legendary"],
        "positives": r["positives"], "negatives": r["negatives"],
        "price": r["metrics"].get("price"),
        "pe": r["metrics"].get("pe"),
        "roe": r["metrics"].get("roe"),
        "revenue_growth": r["metrics"].get("revenue_growth"),
        "market_cap": r["metrics"].get("market_cap"),
    }


# ================================================================
# DASHBOARD — aggregate scan results for dashboard view
# ================================================================
def build_dashboard_data(
    items: list[dict],
    raw_cache_len: int = 0,
    tech_cache_len: int = 0,
    cross_signal_count: int = 0,
    as_of: Any = None,
) -> dict:
    """Build dashboard payload from scan items. Pure aggregation."""
    scanned = len(items)
    top3 = [
        {
            "ticker": r["ticker"], "name": r["name"], "overall": r["overall"],
            "style": r["style"], "scores": r["scores"],
            "price": r["metrics"].get("price"), "positives": r["positives"][:2],
        }
        for r in items[:3]
    ]

    opps = sorted(
        [r for r in items if r["scores"].get("value", 0) >= 55],
        key=lambda x: x["scores"].get("value", 0) + x["scores"].get("growth", 0),
        reverse=True,
    )
    opportunities = [
        {
            "ticker": r["ticker"], "name": r["name"], "overall": r["overall"],
            "reason": f"Value: {r['scores']['value']:.0f} + Growth: {r['scores']['growth']:.0f}",
            "price": r["metrics"].get("price"),
        }
        for r in opps[:3]
    ]

    risky = sorted(
        [r for r in items if r["scores"].get("balance", 100) < 50 or r["overall"] < 40],
        key=lambda x: x["overall"],
    )
    risks = [
        {
            "ticker": r["ticker"], "name": r["name"], "overall": r["overall"],
            "reason": "; ".join(r["negatives"][:2]), "price": r["metrics"].get("price"),
        }
        for r in risky[:3]
    ]

    sec_map: dict[str, dict] = defaultdict(lambda: {"count": 0, "avg_score": 0, "tickers": []})
    for r in items:
        sec = r.get("sector") or "Diger"
        sec_map[sec]["count"] += 1
        sec_map[sec]["avg_score"] += r["overall"]
        sec_map[sec]["tickers"].append(r["ticker"])
    sectors = sorted(
        [
            {
                "sector": sec, "count": d["count"],
                "avg_score": round(d["avg_score"] / max(d["count"], 1), 1),
                "tickers": d["tickers"][:5],
            }
            for sec, d in sec_map.items()
        ],
        key=lambda x: x["avg_score"],
        reverse=True,
    )

    style_map: dict[str, int] = defaultdict(int)
    for r in items:
        style_map[r["style"]] += 1

    return {
        "scanned": scanned, "top3": top3,
        "opportunities": opportunities, "risks": risks,
        "sectors": sectors, "styles": dict(style_map),
        "counters": {
            "total_analyzed": scanned, "cache_raw": raw_cache_len,
            "cache_tech": tech_cache_len, "cross_signals": cross_signal_count,
        },
    }


# ================================================================
# HERO — aggregate scan + macro + cross for hero summary
# ================================================================
_MODE_COLORS = {"POZITIF": "green", "TEMKINLI_POZITIF": "green", "NOTR": "yellow", "RISKLI": "red"}
_MODE_LABELS = {"POZITIF": "Pozitif", "TEMKINLI_POZITIF": "Temkinli Pozitif", "NOTR": "Notr", "RISKLI": "Riskli"}


def build_hero_data(
    items: list[dict],
    macro_data: dict,
    cross_data: list[dict],
) -> dict:
    """
    Build the non-AI portion of the hero summary.

    Returns a dict with all hero fields EXCEPT 'story' and 'bot_says'
    (those are filled in by the AI caller after this function returns).
    """
    bullish_count = sum(1 for r in items if r["overall"] >= 65)
    bearish_count = sum(1 for r in items if r["overall"] < 40)
    total = len(items)

    if bullish_count > total * 0.6:
        mode = "POZITIF"
    elif bearish_count > total * 0.4:
        mode = "RISKLI"
    elif bullish_count > bearish_count:
        mode = "TEMKINLI_POZITIF"
    else:
        mode = "NOTR"
    mode_color = _MODE_COLORS.get(mode, "yellow")
    mode_label = _MODE_LABELS.get(mode, "Notr")

    opp = risk_item = None
    deger_leaders: list[dict] = []
    ivme_leaders: list[dict] = []

    if items:
        by_d = sorted(items, key=lambda x: x.get("deger", x["overall"]), reverse=True)
        deger_leaders = [
            {"ticker": r["ticker"], "name": r["name"], "deger": r.get("deger", r["overall"]),
             "ivme": r.get("ivme", 50), "style": r["style"],
             "reason": r["positives"][0] if r["positives"] else ""}
            for r in by_d[:3]
        ]
        by_i = sorted(items, key=lambda x: x.get("ivme", 50), reverse=True)
        ivme_leaders = [
            {"ticker": r["ticker"], "name": r["name"], "deger": r.get("deger", r["overall"]),
             "ivme": r.get("ivme", 50), "style": r["style"],
             "reason": r["positives"][0] if r["positives"] else ""}
            for r in by_i[:3]
        ]
        worst = min(items, key=lambda x: x.get("deger", x["overall"]))
        risk_item = {"ticker": worst["ticker"], "name": worst["name"],
                     "deger": worst.get("deger", worst["overall"]),
                     "reason": worst["negatives"][0] if worst["negatives"] else ""}
        best = max(items, key=lambda x: x["scores"].get("value", 0) + x["scores"].get("growth", 0))
        opp = {"ticker": best["ticker"], "name": best["name"], "overall": best["overall"],
               "reason": best["positives"][0] if best["positives"] else ""}

    # Sector analysis
    sec_map: dict[str, dict] = defaultdict(lambda: {"total": 0, "count": 0})
    for r in items:
        s = r.get("sector") or "Diger"
        sec_map[s]["total"] += r["overall"]
        sec_map[s]["count"] += 1
    strong_sectors = sorted(
        [(k, v["total"] / v["count"]) for k, v in sec_map.items() if v["count"] >= 2],
        key=lambda x: -x[1],
    )[:3]
    weak_sectors = sorted(
        [(k, v["total"] / v["count"]) for k, v in sec_map.items() if v["count"] >= 2],
        key=lambda x: x[1],
    )[:2]

    # Watchlist
    watch: list[str] = []
    if strong_sectors:
        watch.append(f"{strong_sectors[0][0]} sektörü güçlü")
    macro_items = macro_data.get("items", [])
    for mi in macro_items:
        if mi.get("key") == "VIX" and mi.get("change_pct", 0) > 3:
            watch.append("VIX yükseliyor")
        if mi.get("key") == "DXY" and mi.get("change_pct", 0) > 0.5:
            watch.append("DXY yükselişte")
    if cross_data:
        watch.append(f"{len(cross_data)} sinyal aktif")
    if not watch:
        watch = ["Piyasa sakin"]

    return {
        "mode": mode, "mode_label": mode_label, "mode_color": mode_color,
        "opportunity": clean_for_json(opp), "risk": clean_for_json(risk_item),
        "deger_leaders": clean_for_json(deger_leaders),
        "ivme_leaders": clean_for_json(ivme_leaders),
        "watch": watch[:4],
        "strong_sectors": [{"name": s[0], "score": round(s[1], 1)} for s in strong_sectors],
        "weak_sectors": [{"name": s[0], "score": round(s[1], 1)} for s in weak_sectors],
        "stats": {"total": total, "bullish": bullish_count, "bearish": bearish_count,
                  "signals": len(cross_data)},
        # AI caller fills these:
        "story": None,
        "bot_says": None,
        "timestamp": now_iso(),
    }


# ================================================================
# HEATMAP — group raw price data by sector
# ================================================================
def build_heatmap_sectors(raw_data: list[dict]) -> dict:
    """Group heatmap data by sector, compute averages."""
    sectors: dict[str, list] = defaultdict(list)
    for d in raw_data:
        sectors[d["sector"]].append(d)
    sector_list = sorted(
        [
            {
                "sector": sec,
                "avg_change": round(sum(i["change_pct"] for i in si) / len(si), 2) if si else 0,
                "total_mcap": sum(i["market_cap"] or 0 for i in si),
                "count": len(si),
                "stocks": sorted(si, key=lambda x: abs(x["change_pct"]), reverse=True),
            }
            for sec, si in sectors.items()
        ],
        key=lambda x: x["avg_change"],
        reverse=True,
    )
    return {"timestamp": now_iso(), "sectors": clean_for_json(sector_list), "total": len(raw_data)}


# ================================================================
# BRIEFING CONTEXT — prepare data for AI briefing prompt
# ================================================================
def build_briefing_context(items: list[dict], cross_results: list[dict]) -> dict:
    """Build structured context for AI briefing generation."""
    top3_d = sorted(items, key=lambda x: x.get("deger", x["overall"]), reverse=True)[:3]
    top3_i = sorted(items, key=lambda x: x.get("ivme", 50), reverse=True)[:3]
    worst = sorted(items, key=lambda x: x.get("deger", x["overall"]))[:2]
    cross_data = cross_results[:5]

    return {
        "count": len(items),
        "deger_str": ", ".join(f"{r['ticker']}(D:{r.get('deger', r['overall']):.0f})" for r in top3_d),
        "ivme_str": ", ".join(f"{r['ticker']}(I:{r.get('ivme', 50):.0f})" for r in top3_i),
        "worst_str": ", ".join(f"{r['ticker']}(D:{r.get('deger', r['overall']):.0f})" for r in worst),
        "summary_parts": [
            f"{r['ticker']}: D:{r.get('deger', r['overall']):.0f} I:{r.get('ivme', 50):.0f} ({r['style']})"
            for r in items[:5]
        ],
        "sig_str": ", ".join(f"{s['ticker']}:{s['signal']}" for s in cross_data[:3]),
        "signal_count": len(cross_data),
    }


# ================================================================
# AGENT CONTEXT — prepare data for Q agent prompt
# ================================================================
def build_agent_context(
    items: list[dict],
    cross_results: list[dict],
    query: str,
    rich_context_fn=None,
) -> str:
    """Build context string for the Q agent AI prompt."""
    context = ""
    if items:
        top3_d = sorted(items, key=lambda x: x.get("deger", x["overall"]), reverse=True)[:3]
        top3_i = sorted(items, key=lambda x: x.get("ivme", 50), reverse=True)[:3]
        d_parts = ", ".join(f"{r['ticker']}(D:{r.get('deger', r['overall']):.0f})" for r in top3_d)
        i_parts = ", ".join(f"{r['ticker']}(I:{r.get('ivme', 50):.0f})" for r in top3_i)
        context = f"Taranan {len(items)} hisseden DEGER: {d_parts}. IVME: {i_parts}.\n"
        if rich_context_fn:
            for r in items:
                if r["ticker"].lower() in query.lower():
                    context += f"\n{r['ticker']} DETAY:\n{rich_context_fn(r)}\n"
                    break
    cross_signals = cross_results[:5]
    if cross_signals:
        sig_parts = ", ".join(f"{s['ticker']}:{s['signal']}" for s in cross_signals)
        context += f"Sinyaller: {sig_parts}\n"
    return context

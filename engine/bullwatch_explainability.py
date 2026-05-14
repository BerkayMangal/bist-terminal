"""BullWatch score explainability — "Niye bu skor?"

Frontend modal'ı için backend bundle'ı oluşturur. Her motor için:
  - Raw sub-score (0-1)
  - Engine weight (WEIGHTS_WITH_OWNERSHIP'ten)
  - Normalized contribution to final 100-point score
  - Reasons (evidence statements) — keyword matching ile gruplanır
  - Available flag (motorun veri sağlayıp sağlamadığı)

Plus:
  - Data quality breakdown (tier + missing fields)
  - Previous snapshot delta (eğer önceki snapshot varsa)
  - Narrative bundle (whats_happening / what_to_watch / caveats)

Pure read-side — yan etki yok, score'u değiştirmez, sadece halihazırda
hesaplanmış olanları frontend-friendly forma çevirir.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.bw_explain")


# Human-readable labels for each engine — Turkish for UI.
ENGINE_LABELS: dict[str, str] = {
    "float_pressure":      "Float Baskısı",
    "revenue_mispricing":  "Gelir/PD Düşüklüğü",
    "silent_volume":       "Sessiz Hacim",
    "price_action":        "Fiyat Aksiyonu",
    "compression":         "Volatilite Sıkışması",
    "ownership":           "Sahiplik Yapısı",
    "fundamental_quality": "Temel Kalite",
    "kap_activity":        "KAP Operatör Aktivitesi",
}

# Tahtacı-centric grouping. BullWatch'ın amacı tahtacıyı önceden tespit
# etmek; bu grup atamaları UI'de "İmza" / "Teyit" / "Bağlam" başlıkları
# altında reason'ları öbeklemek için kullanılır.
ENGINE_CATEGORY: dict[str, str] = {
    "kap_activity":        "tahtaci",   # Doğrudan tahtacı imzası
    "ownership":           "tahtaci",   # İnsider'ın yarısı bu
    "float_pressure":      "teyit",     # Tahtacı pozisyon biriktirdiği için float baskısı
    "silent_volume":       "teyit",     # Tahtacı yavaş alım = sessiz hacim
    "price_action":        "teyit",     # Walk-up / absorption = tahtacı markup
    "compression":         "teyit",     # Sıkışma = tahtacı set-up'ı
    "revenue_mispricing":  "baglam",    # Tahtacı NEDEN bu hisseyi seçti
    "fundamental_quality": "baglam",    # Junk-pump filtresi
}

CATEGORY_LABELS = {
    "tahtaci": "🎯 Tahtacı İmzaları",
    "teyit":   "📊 Teknik Teyit",
    "baglam":  "🏛️ Temel Bağlam",
}

CATEGORY_DESCRIPTIONS = {
    "tahtaci": "Tahtacının doğrudan parmak izleri: KAP'a düşen INSIDER/MNA/BUYBACK olayları + insider 90g alış + holding-grup peer aktivitesi.",
    "teyit":   "Tahtacının davranışı: float baskısı, sessiz hacim artışı, walk-up pattern, volatilite sıkışması.",
    "baglam":  "Tahtacı neden bu hisseyi seçti: ucuz mu (revenue/PD), sağlam mı (ROE/borç/cashflow).",
}

# What does each engine actually measure? Tooltip-friendly 1-line.
ENGINE_DESCRIPTIONS: dict[str, str] = {
    "float_pressure":      "Dolaşımdaki hisse / piyasa değeri oranı. Düşük float = az kişiye çok hisse, fiyat hassas.",
    "revenue_mispricing":  "Yıllık ciro / piyasa değeri. >5× = piyasa hisseyi cirosunun çok altında fiyatlıyor.",
    "silent_volume":       "Son 20g'ye göre relative volume. >1.5× = hacim erkenden artıyor.",
    "price_action":        "Shakeout / Absorption / Tight Closes / Walk-Up gibi accumulation pattern'leri.",
    "compression":         "ATR + BB width sıkışması. <60d medyan = volatility ezildi, kırılım yakın.",
    "ownership":           "Insider buy 90g + free float değişim + fon hareketleri (varsa).",
    "fundamental_quality": "PE<15, ROE>15%, Net debt/EBITDA<2 filtresi. Junk pump koruması.",
    "kap_activity":        "Tahtacı imzalı son 14g KAP olayları: INSIDER/MNA/BUYBACK/CAPITAL/MGMT.",
}

# Keyword → engine mapping for reasons-to-engine grouping. Order matters
# (more specific keywords first).
_REASON_KEYWORDS: list[tuple[str, str]] = [
    # (keyword.lower(), engine_key)
    ("float pressure",      "float_pressure"),
    ("float squeeze",       "float_pressure"),
    ("market cap",          "revenue_mispricing"),
    ("revenue",             "revenue_mispricing"),
    ("ciro",                "revenue_mispricing"),
    ("relative volume",     "silent_volume"),
    ("rvol",                "silent_volume"),
    ("hacim",               "silent_volume"),
    ("atr",                 "compression"),
    ("bb width",            "compression"),
    ("compress",            "compression"),
    ("compression",         "compression"),
    ("walk-up",             "price_action"),
    ("walk up",             "price_action"),
    ("shakeout",            "price_action"),
    ("absorption",          "price_action"),
    ("tight closes",        "price_action"),
    ("pattern",             "price_action"),
    ("price action",        "price_action"),
    ("sustained walk",      "price_action"),
    ("insider",             "kap_activity"),
    ("kap activity",        "kap_activity"),
    ("kap aktivitesi",      "kap_activity"),
    ("tahtacı",             "kap_activity"),
    ("operator",            "kap_activity"),
    ("holding-group",       "kap_activity"),
    ("group",               "kap_activity"),
    ("buyback",             "kap_activity"),
    ("pay alım",            "kap_activity"),
    ("roe",                 "fundamental_quality"),
    ("pe outside",          "fundamental_quality"),
    ("net debt",            "fundamental_quality"),
    ("ebitda",              "fundamental_quality"),
    ("ownership",           "ownership"),
    ("foreign",             "ownership"),
    ("free float",          "ownership"),
    ("sahip",               "ownership"),
]


def _classify_reason(reason: str) -> Optional[str]:
    """Map a reason string to its engine key via keyword match.
    Returns None when no keyword matches (rare — usually means we should
    surface it under 'other'/'misc')."""
    if not reason:
        return None
    low = reason.lower()
    for kw, engine in _REASON_KEYWORDS:
        if kw in low:
            return engine
    return None


def _get_weights() -> dict[str, float]:
    """Read the canonical engine weights from engine.bullwatch."""
    try:
        from engine.bullwatch import WEIGHTS_WITH_OWNERSHIP
        return dict(WEIGHTS_WITH_OWNERSHIP)
    except Exception as exc:
        log.warning("could not load WEIGHTS_WITH_OWNERSHIP: %r", exc)
        # Fallback to defaults — matches bullwatch.py line 67 weights
        return {
            "float_pressure":      20.0,
            "revenue_mispricing":  12.0,
            "silent_volume":       12.0,
            "price_action":        18.0,
            "compression":         8.0,
            "ownership":           10.0,
            "fundamental_quality": 5.0,
            "kap_activity":        15.0,
        }


def build_engine_breakdown(
    components: dict[str, float],
    reasons: list[str],
) -> list[dict[str, Any]]:
    """Per-engine breakdown — sub-score, weight, normalized contribution,
    and grouped reasons.

    `components` keys with None values are treated as "engine not available
    for this ticker" — they get `available=False` and contribute 0.
    """
    weights = _get_weights()
    # Group reasons by engine
    reasons_by_engine: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for r in (reasons or []):
        eng = _classify_reason(r)
        if eng is None:
            unmatched.append(r)
        else:
            reasons_by_engine.setdefault(eng, []).append(r)

    # Only NON-NONE engines contribute to weight normalization
    available_engines = {
        k: v for k, v in (components or {}).items() if v is not None
    }
    weight_total = sum(weights.get(k, 0.0) for k in available_engines)
    norm = (100.0 / weight_total) if weight_total > 0 else 0.0

    out: list[dict[str, Any]] = []
    # Preserve canonical engine order (matches WEIGHTS_WITH_OWNERSHIP)
    for k in weights:
        sub = (components or {}).get(k)
        is_available = sub is not None
        w = weights.get(k, 0.0)
        # Normalized contribution to the final 0-100 score
        if is_available:
            contribution_pct = round(sub * w * norm, 2)
        else:
            contribution_pct = 0.0
        out.append({
            "key": k,
            "label": ENGINE_LABELS.get(k, k),
            "description": ENGINE_DESCRIPTIONS.get(k, ""),
            "sub_score": round(sub, 2) if is_available else None,
            "weight": round(w, 1),
            "contribution_pct": contribution_pct,
            "reasons": reasons_by_engine.get(k, []),
            "available": is_available,
        })
    return out, unmatched


def _get_previous_components(symbol: str) -> Optional[dict[str, Any]]:
    """Fetch the prior bullwatch snapshot's items for this symbol.
    Returns {score, components, zone} or None if unavailable.
    """
    try:
        from core.snapshot_store import get_default_store
    except Exception:
        return None
    sym = (symbol or "").upper().strip().replace(".IS", "")
    if not sym:
        return None
    try:
        store = get_default_store()
        # Snapshot store has a "previous" pointer separate from latest
        scan_ids = store.list_scan_ids("bullwatch", limit=5) if hasattr(
            store, "list_scan_ids"
        ) else []
        # If a list helper isn't there, fall back: read latest items then
        # try the "previous" tag.
        prev_scan_id = None
        if hasattr(store, "read_previous_scan_id"):
            prev_scan_id = store.read_previous_scan_id("bullwatch")
        if prev_scan_id is None and len(scan_ids) >= 2:
            prev_scan_id = scan_ids[1]
        if prev_scan_id is None:
            return None
        items_map = store.read_items(
            "bullwatch", [sym], scan_id=prev_scan_id,
        )
        prev_item = items_map.get(sym) if items_map else None
        if not prev_item:
            return None
        return {
            "score": prev_item.get("score"),
            "components": prev_item.get("components") or {},
            "zone": prev_item.get("zone"),
            "scan_id": prev_scan_id,
        }
    except Exception as exc:
        log.debug("previous components lookup %s: %r", sym, exc)
        return None


def _data_quality_breakdown(item: dict[str, Any]) -> dict[str, Any]:
    """Surface what's behind the data_quality tier so the user knows
    why a row is 'medium' vs 'high' (e.g. banka, cashflow yok)."""
    tier = item.get("data_quality") or "?"
    missing = item.get("missing_fields") or []
    is_bank = bool((item.get("metrics") or {}).get("is_bank")) \
        or (item.get("sector_tr") == "Finansal")
    return {
        "tier": tier,
        "missing_fields": missing,
        "is_bank": is_bank,
        "provider_used": item.get("provider_used"),
        "data_status": item.get("data_status"),
        "tier_explanation": {
            "high":   "≥6 motor data sağladı, banka değil veya tüm beklenen field'lar geldi.",
            "medium": "Bazı motorlar veri sağlayamadı (örn. bankalarda cashflow yok) ya da kısmi data.",
            "low":    "Çoğu motor None döndü. Skor yine de bounded — ama düşük güvenle değerlendir.",
        }.get(tier, ""),
    }


def compute_tahtaci_signal_strength(
    components: dict[str, float],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Derived "Tahtacı Signal Strength" — BullWatch'ın asıl amacının
    ne kadar net karşılanıyor olduğu.

    Formula combines:
      • kap_activity sub-score     (doğrudan KAP imzası)
      • ownership sub-score        (insider 90g)
      • group_activity_boost       (holding-grup peer)
      • walkup_days bonus          (≥5g sustained walk-up)

    Output 0..1 normalized. UI'de büyük altın daire olarak gösterilir
    — "tahtacı confidence" headline.
    """
    out = {"score": 0.0, "label": "—", "components": {}}
    kap = (components or {}).get("kap_activity") or 0.0
    own = (components or {}).get("ownership") or 0.0
    # group_activity_boost is 0..6 points additive on the 100-scale;
    # normalize to 0..1 by /6.
    group_boost = float((metrics or {}).get("group_activity_boost") or 0.0)
    group = min(1.0, group_boost / 6.0)
    walkup_days = int((metrics or {}).get("walkup_days") or 0)
    # walkup: 5d → 0.5, 10d → 1.0
    walkup = min(1.0, max(0.0, (walkup_days - 4) / 6.0)) if walkup_days >= 5 else 0.0

    # Weighted sum — KAP activity ve ownership ana tahtacı sinyalleri;
    # group_boost + walkup teyit / amplifier.
    weights = {"kap": 0.45, "own": 0.25, "group": 0.20, "walkup": 0.10}
    score = (
        weights["kap"]    * kap
        + weights["own"]    * own
        + weights["group"]  * group
        + weights["walkup"] * walkup
    )
    out["score"] = round(score, 3)
    out["components"] = {
        "kap_activity":  round(kap, 2),
        "ownership":     round(own, 2),
        "group_boost":   round(group, 2),
        "walkup_days":   walkup_days,
    }
    # Verdict bands — calibrated against typical CONVICTION alarms
    if score >= 0.6:
        out["label"] = "Net tahtacı imzası"
    elif score >= 0.4:
        out["label"] = "Güçlü ısınma"
    elif score >= 0.2:
        out["label"] = "Erken belirtiler"
    elif score > 0:
        out["label"] = "Zayıf sinyal"
    else:
        out["label"] = "Tahtacı yok"
    return out


def group_engines_by_category(
    engines: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Bucket engines into Tahtacı / Teknik / Bağlam categories so the
    UI can render three distinct sections rather than a flat 8-row table.
    """
    out: dict[str, list[dict[str, Any]]] = {
        "tahtaci": [], "teyit": [], "baglam": [],
    }
    for e in engines:
        cat = ENGINE_CATEGORY.get(e.get("key"), "teyit")
        out[cat].append(e)
    return out


def build_explanation(item: dict[str, Any]) -> dict[str, Any]:
    """Top-level helper — takes a single bullwatch item dict (the shape
    returned by `/api/bullwatch`'s items list) and returns the explain
    bundle for the modal.

    Output shape:
        {
          "symbol": str,
          "score": float, "zone": str, "pattern": str,
          "data_quality": {tier, missing_fields, ...},
          "engines": [{key, label, sub_score, weight, contribution_pct,
                       reasons, available}, ...],
          "unmatched_reasons": [...],     # reasons that no engine claimed
          "previous": {score, components, zone, scan_id} | None,
          "delta": {                       # only when previous exists
              "score": float,
              "by_engine": {engine_key: float},
          } | None,
          "narrative": {whats_happening, what_to_watch, caveats},
        }
    """
    if not item:
        return {}
    sym = (item.get("symbol") or "").upper().strip()
    components = item.get("components") or {}
    reasons = item.get("reasons") or []
    engines, unmatched = build_engine_breakdown(components, reasons)
    prev = _get_previous_components(sym)
    delta = None
    if prev:
        try:
            score_delta = round(
                (item.get("score") or 0.0) - (prev.get("score") or 0.0),
                1,
            )
            by_engine = {}
            prev_components = prev.get("components") or {}
            for k, v in components.items():
                if v is None:
                    continue
                pv = prev_components.get(k)
                if pv is None:
                    continue
                by_engine[k] = round(float(v) - float(pv), 2)
            delta = {"score": score_delta, "by_engine": by_engine}
        except Exception as exc:
            log.debug("delta compute failed: %r", exc)
            delta = None
    tahtaci_strength = compute_tahtaci_signal_strength(
        components, item.get("metrics") or {},
    )
    grouped = group_engines_by_category(engines)
    return {
        "symbol": sym,
        "score": item.get("score"),
        "zone": item.get("zone"),
        "pattern": item.get("pattern"),
        "data_quality": _data_quality_breakdown(item),
        "tahtaci_strength": tahtaci_strength,
        "engines": engines,
        "engines_grouped": {
            "tahtaci": {"label": CATEGORY_LABELS["tahtaci"],
                        "description": CATEGORY_DESCRIPTIONS["tahtaci"],
                        "engines": grouped["tahtaci"]},
            "teyit":   {"label": CATEGORY_LABELS["teyit"],
                        "description": CATEGORY_DESCRIPTIONS["teyit"],
                        "engines": grouped["teyit"]},
            "baglam":  {"label": CATEGORY_LABELS["baglam"],
                        "description": CATEGORY_DESCRIPTIONS["baglam"],
                        "engines": grouped["baglam"]},
        },
        "unmatched_reasons": unmatched,
        "previous": prev,
        "delta": delta,
        "narrative": item.get("narrative") or {},
    }

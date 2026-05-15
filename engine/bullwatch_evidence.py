"""
BullWatch v2 — Evidence Layer (Addendum Module 10).

Strict legal-safe language guard. Every BullWatch card output goes
through this layer:

  1. Convert raw motor results into a structured `evidence` list:
     each item has metric name, numeric value, Turkish interpretation
  2. Build a narrative string from playbook + maturity + conflict
     resolution
  3. Audit the narrative for forbidden terms — never let trade
     directives or manipulation accusations leak through

Forbidden terms (illustrative; full list below):
  - "buy", "sell", "al", "sat", "kar al"
  - "target", "hedef fiyat", "stop"
  - "manipülasyon", "insider trading"
  - performance claims ("garanti", "kesin yükselecek")

The audit returns `language_safety = {uses_observation_language, ...}`
which is included in every BullWatch card. If the audit ever fails
(forbidden term detected), the card includes a flag and the safety
report — surfaces bugs in narrative generation immediately.
"""
from __future__ import annotations

import re
from typing import Any, Optional


# ────────────────────────────────────────────────────────────────────
# Forbidden terms list.
#
# We use word-boundary regex matching to avoid false positives
# (e.g. "satıcı" should NOT trigger because it contains "sat").
# ────────────────────────────────────────────────────────────────────
FORBIDDEN_TERMS = (
    # Trade directives — Turkish
    "al", "sat", "alın", "satın", "kar al", "kâr al", "stop",
    "pozisyon kapat", "çıkış yap", "giriş yap", "alış yap",
    "satış yap", "bekleme yapma",
    # Trade directives — English
    "buy", "sell", "exit now", "stop loss", "take profit",
    "target price", "buy now", "sell now",
    # Manipulation accusations
    "manipulation", "manipülasyon", "fraud", "dolandırıcılık",
    "insider trading", "içeriden öğrenenlerin",
    # Performance claims
    "garanti", "guaranteed", "kesin yükselecek", "kesin düşecek",
    "kazanç garantili",
    # Direct directives
    "hedef fiyat", "target fiyat",
)


def safety_audit(text: str) -> dict:
    """
    Audit a string for forbidden terms.

    Uses word-boundary regex to avoid partial-word false positives.
    Some forbidden tokens are very short ("al", "sat") — these are
    matched only as standalone words.

    Returns:
        {
          "uses_observation_language": bool,
          "forbidden_terms_detected": list[str],
        }
    """
    if not text:
        return {"uses_observation_language": True, "forbidden_terms_detected": []}

    text_lower = text.lower()
    detected: list[str] = []

    for term in FORBIDDEN_TERMS:
        # Multi-word terms: substring match is fine, they're distinctive
        if " " in term:
            if term in text_lower and term not in detected:
                detected.append(term)
            continue
        # Single-word terms: require word boundary
        # This is what prevents "satıcı" from matching "sat"
        pattern = r"\b" + re.escape(term) + r"\b"
        if re.search(pattern, text_lower) and term not in detected:
            detected.append(term)

    return {
        "uses_observation_language": len(detected) == 0,
        "forbidden_terms_detected": detected,
    }


# ────────────────────────────────────────────────────────────────────
# Evidence builder
#
# Takes raw motor / Phase A module results and produces a structured
# evidence list with Turkish interpretations. Each item:
#   {metric, value, interpretation}
# ────────────────────────────────────────────────────────────────────
def _format_pct(v: float) -> str:
    return f"{v*100:.1f}%" if abs(v) < 1 else f"{v:.1f}%"


def build_evidence_list(
    metrics: dict,
    sub_scores: dict,
    pinning_result: Optional[dict] = None,
    maturity_result: Optional[dict] = None,
    playbook_result: Optional[dict] = None,
) -> list[dict]:
    """
    Construct the evidence list from available signals.

    Each entry: {"metric", "value", "interpretation"}.
    Items are filtered: only signals with actual data are included.
    """
    evidence: list[dict] = []

    # ── From metrics_dict (existing) ──
    fp = metrics.get("float_pressure")
    if fp is not None:
        evidence.append({
            "metric": "float_pressure",
            "value": f"{fp*100:.2f}%",
            "interpretation": (
                "yüksek float baskısı" if fp >= 0.04
                else "orta float baskısı" if fp >= 0.02
                else "düşük float baskısı"
            ),
        })

    rvol = metrics.get("rvol")
    if rvol is not None:
        evidence.append({
            "metric": "relative_volume",
            "value": f"{rvol:.2f}x",
            "interpretation": (
                "hacim patlaması" if rvol >= 2.0
                else "yüksek hacim" if rvol >= 1.3
                else "normal hacim" if rvol >= 0.7
                else "sessiz hacim"
            ),
        })

    rev_mc = metrics.get("revenue_to_marketcap")
    if rev_mc is not None:
        evidence.append({
            "metric": "revenue_to_marketcap",
            "value": f"{rev_mc:.2f}x",
            "interpretation": (
                "ciro market cap'in çok üstünde" if rev_mc >= 3.0
                else "ciro yüksek" if rev_mc >= 1.5
                else "normal değerleme" if rev_mc >= 0.5
                else "ciro market cap'in altında"
            ),
        })

    fmc = metrics.get("float_market_cap")
    if fmc is not None:
        evidence.append({
            "metric": "float_market_cap",
            "value": f"{fmc/1e6:.0f}M TL",
            "interpretation": (
                "düşük floated mikro-kap" if fmc < 500e6
                else "düşük floated küçük-kap" if fmc < 1.5e9
                else "orta floated"
            ),
        })

    # ── From price_action patterns ──
    patterns = metrics.get("patterns") or []
    if patterns:
        pattern_labels_tr = {
            "shakeout": "shakeout (spring)",
            "absorption": "alıcı emilmesi",
            "tight_closes": "dar kapanışlar",
            "walk_up": "walk-up birikimi",
        }
        for p in patterns:
            evidence.append({
                "metric": f"pattern_{p}",
                "value": "tespit edildi",
                "interpretation": pattern_labels_tr.get(p, p),
            })

    # ── From Price Pinning module ──
    if pinning_result and pinning_result.get("price_pinning_score"):
        pp_score = pinning_result["price_pinning_score"]
        band = pinning_result.get("control_band")
        band_str = f"{band[0]}-{band[1]} TL" if band else "—"
        evidence.append({
            "metric": "price_pinning_score",
            "value": f"{pp_score}",
            "interpretation": pinning_result.get("interpretation", "yok"),
        })
        if pinning_result.get("band_width_pct"):
            evidence.append({
                "metric": "control_band_width",
                "value": f"{pinning_result['band_width_pct']:.2f}%",
                "interpretation": (
                    "fiyat dar bant" if pinning_result["band_width_pct"] < 3
                    else "normal aralık"
                ),
            })
        if pinning_result.get("closes_inside_band_pct"):
            evidence.append({
                "metric": "closes_inside_band_pct",
                "value": f"{pinning_result['closes_inside_band_pct']}%",
                "interpretation": "kapanışların büyük çoğunluğu bantta",
            })
        if band:
            evidence.append({
                "metric": "control_band",
                "value": band_str,
                "interpretation": "kontrol bandı",
            })

    # ── From Move Maturity ──
    if maturity_result and maturity_result.get("maturity"):
        mat = maturity_result["maturity"]
        ind = maturity_result.get("indicators") or {}
        evidence.append({
            "metric": "move_maturity",
            "value": mat,
            "interpretation": {
                "EARLY": "hareket erken aşamada",
                "MID": "hareket orta aşamada",
                "LATE": "hareket geç aşamada",
                "EXHAUSTED": "hareket tükenmiş olabilir",
                "UNCLEAR": "olgunluk belirsiz",
            }.get(mat, mat),
        })
        if ind.get("position_in_range") is not None:
            evidence.append({
                "metric": "position_in_12m_range",
                "value": f"{int(ind['position_in_range']*100)}%",
                "interpretation": (
                    "12-aylık range alt dilimi" if ind["position_in_range"] < 0.4
                    else "12-aylık range üst dilimi" if ind["position_in_range"] > 0.7
                    else "12-aylık range orta dilimi"
                ),
            })
        if ind.get("rsi") is not None:
            rsi = ind["rsi"]
            evidence.append({
                "metric": "rsi_14",
                "value": f"{rsi:.0f}",
                "interpretation": (
                    "overbought zone" if rsi > 70
                    else "oversold zone" if rsi < 30
                    else "nötr"
                ),
            })

    # ── From Playbook Sequence ──
    if playbook_result and playbook_result.get("playbook") and playbook_result["playbook"] != "UNCLEAR":
        evidence.append({
            "metric": "playbook",
            "value": playbook_result["playbook"],
            "interpretation": f"{playbook_result.get('confidence', 0)}% güven",
        })

    return evidence


# ────────────────────────────────────────────────────────────────────
# Narrative builder
# ────────────────────────────────────────────────────────────────────
_PLAYBOOK_HEADLINES = {
    "ACCUMULATION_SEQUENCE": ("🟢", "Toplama döngüsü ilerliyor olabilir"),
    "DISTRIBUTION_SEQUENCE": ("🟠", "Dağıtım döngüsü güçleniyor olabilir"),
    "MARKUP_SEQUENCE": ("🟢", "Markup sequence aktif olabilir"),
    "UNCLEAR": ("⚪", "Belirgin döngü pattern'i gözlemlenmedi"),
}

_DOMINANT_LABELS = {
    "ACCUMULATION": "BİRİKİM",
    "DISTRIBUTION": "DAĞITIM",
    "MARKUP": "MARKUP",
    "RE_ACCUMULATION": "YENİDEN BİRİKİM",
    "NOISE": "SİNYAL ZAYIF",
    "UNCLEAR": "BELİRSİZ",
}

# Turkish-correct lowercase forms (Python .lower() mangles "DAĞITIM" → "dağitim").
_DOMINANT_LABELS_LOWER = {
    "ACCUMULATION": "birikim",
    "DISTRIBUTION": "dağıtım",
    "MARKUP": "markup",
    "RE_ACCUMULATION": "yeniden birikim",
    "NOISE": "sinyal zayıf",
    "UNCLEAR": "belirsiz",
}


def _detect_playbook_conflict_override(playbook: Optional[str],
                                       dominant: Optional[str],
                                       cm_conf: int) -> bool:
    """
    A.7: Detect when conflict matrix's dominant_read contradicts the
    playbook engine's choice with sufficient confidence.

    When True, narrative leads with conflict resolution and treats
    playbook as secondary.

    Triggers (cm_conf must be >= 50):
      - playbook=ACCUMULATION_SEQUENCE + dominant=DISTRIBUTION
      - playbook=DISTRIBUTION_SEQUENCE + dominant=ACCUMULATION
      - playbook=MARKUP_SEQUENCE + dominant=DISTRIBUTION
    """
    if cm_conf < 50:
        return False
    if dominant not in ("ACCUMULATION", "DISTRIBUTION", "MARKUP"):
        return False
    if playbook == "ACCUMULATION_SEQUENCE" and dominant == "DISTRIBUTION":
        return True
    if playbook == "DISTRIBUTION_SEQUENCE" and dominant == "ACCUMULATION":
        return True
    if playbook == "MARKUP_SEQUENCE" and dominant == "DISTRIBUTION":
        return True
    return False


_PLAYBOOK_LABEL_TR = {
    "ACCUMULATION_SEQUENCE": "toplama",
    "DISTRIBUTION_SEQUENCE": "dağıtım",
    "MARKUP_SEQUENCE": "markup",
}


def build_narrative(
    playbook_result: Optional[dict],
    conflict_result: Optional[dict],
    maturity_result: Optional[dict],
) -> str:
    """
    Build the user-facing narrative string. Observation-only language.
    Every claim is hedged ("olabilir", "gözlemleniyor").

    A.7 precedence rule: if conflict matrix's dominant_read contradicts
    the playbook (e.g. playbook ACC + conflict DIST), narrative LEADS
    with conflict resolution and treats playbook as secondary/contradicted.
    Without contradiction, narrative starts with playbook headline as before.

    Hierarchy when conflict overrides:
      1. Conflict Matrix dominant_read
      2. Move Maturity
      3. Playbook (mentioned as contradicted)

    Hierarchy when no conflict:
      1. Playbook headline
      2. Maturity context
      3. Conflict resolution (as supporting evidence)
    """
    parts: list[str] = []

    pb = playbook_result.get("playbook", "UNCLEAR") if playbook_result else "UNCLEAR"
    pb_conf = playbook_result.get("confidence", 0) if playbook_result else 0
    dom = conflict_result.get("dominant_read", "UNCLEAR") if conflict_result else "UNCLEAR"
    cm_conf = conflict_result.get("confidence", 0) if conflict_result else 0
    mat = maturity_result.get("maturity") if maturity_result else None

    overridden = _detect_playbook_conflict_override(pb, dom, cm_conf)

    if overridden:
        # 1. Lead with conflict
        label_lower = _DOMINANT_LABELS_LOWER.get(dom, dom.lower())
        parts.append(f"🟠 Çelişki çözümü {label_lower} yönünde ({cm_conf}% güven).")
        # 2. Maturity
        if mat and mat != "UNCLEAR":
            parts.append(f"Hareket olgunluğu: {mat}.")
        # 3. Top rule rationale
        if conflict_result and conflict_result.get("resolved_by"):
            top = conflict_result["resolved_by"][0]
            parts.append(f"Ana etken: {top['rationale']}.")
        # 4. Mention playbook as secondary/contradicted
        pb_label = _PLAYBOOK_LABEL_TR.get(pb, pb.lower())
        parts.append(
            f"Playbook tarafında {pb_label} adımları görünse de "
            f"bu okuma çelişki matrisi tarafından override edildi."
        )
    else:
        # 1. Playbook headline (unchanged from A.6)
        if playbook_result:
            emoji, headline = _PLAYBOOK_HEADLINES.get(pb, _PLAYBOOK_HEADLINES["UNCLEAR"])
            if pb != "UNCLEAR":
                parts.append(f"{emoji} {headline} ({pb_conf}% güven).")
            else:
                parts.append(f"{emoji} {headline}.")
            missing = playbook_result.get("missing_next_confirmation") or []
            if missing:
                parts.append(f"Bekleyen: {missing[0]}.")
        # 2. Maturity context
        if mat and mat != "UNCLEAR":
            parts.append(f"Hareket olgunluğu: {mat}.")
        # 3. Conflict resolution as supporting evidence
        if conflict_result:
            if dom not in ("UNCLEAR", "NOISE") and cm_conf >= 50:
                label = _DOMINANT_LABELS.get(dom, dom)
                parts.append(f"Çelişki çözümü: {label} ({cm_conf}% güven).")
                resolved = conflict_result.get("resolved_by") or []
                if resolved:
                    top = resolved[0]
                    parts.append(f"Ana etken: {top['rationale']}.")

    return " ".join(parts) if parts else "Yeterli veri yok."


# ────────────────────────────────────────────────────────────────────
# Top-level: build the evidence card
# ────────────────────────────────────────────────────────────────────
def build_evidence_card(
    metrics: dict,
    sub_scores: dict,
    pinning_result: Optional[dict] = None,
    maturity_result: Optional[dict] = None,
    playbook_result: Optional[dict] = None,
    conflict_result: Optional[dict] = None,
) -> dict:
    """
    Build the user-facing card with structured evidence + narrative
    + safety audit. This is the final output that goes into the API
    response.
    """
    evidence = build_evidence_list(
        metrics, sub_scores, pinning_result, maturity_result, playbook_result
    )
    narrative = build_narrative(playbook_result, conflict_result, maturity_result)
    safety = safety_audit(narrative)

    return {
        "evidence": evidence,
        "narrative": narrative,
        "language_safety": safety,
    }

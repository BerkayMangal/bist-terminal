"""BullWatch pre-alarm detector — "Tahtacı yaklaşıyor".

BullWatch CONVICTION alarmları SÖKÜLMEDİ — score≥75 + zone + ≥2 motor
+ data_quality=high kriterleri aynen kalıyor. Bu modül SADECE ekstra bir
katman: CONVICTION'a giremeyen ama eşiğe çok yaklaşmış adayları surface
ediyor — tahtacının operasyon hazırlıkta olduğu hisseler.

Kullanıcının iki amacı bu modülde birleşiyor:
  1) "Mevcut CONVICTION mantığını bozmadan iyileştir"
  2) "Tahtacının operasyon yapmasını önceden anla"

Bu read-side bir helper — yan etkisi yok, kalıcı alarm tetiklemez, sadece
mevcut scan output'unu filtreleyip ranking yapar. CONVICTION alarmları
yine engine.bullwatch_alerts'tan gelir; bu modül "pre-alarms" feed'i.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.bw_prealarm")


# Score window: 70–74.9. CONVICTION threshold 75'i bozma, AMA hemen
# altındaki adayları görünür yap. Alt sınır 70 — daha düşük score
# ile tahtacı imzası eşleştiğinde de gösterelim, kullanıcı erkenden
# yakalayabilsin.
PRE_SCORE_MIN = 70.0
PRE_SCORE_MAX = 75.0      # exclusive — 75+ zaten CONVICTION'a aday

# Tahtacı sinyal eşiği. Bu eşiğin altındaki adaylar "rastgele yüksek
# skor" — tahtacı imzası yok, sadece teknik. Sadece tahtacı ısınmasını
# göstermek istediğimiz için 0.30 cutoff (compute_tahtaci_signal_strength
# bandı: 0.20-0.40 = "Erken belirtiler", 0.40+ = "Güçlü ısınma").
TAHTACI_MIN_STRENGTH = 0.30

# Zone gate — EARLY'deki hisseleri görmek istemiyoruz. CONFIRMED'da
# olmalı (motor onayı + ownership veya pattern count >=2). Bu sayede
# "ham erken" değil "olgunlaşmaya yaklaşmış" listesi olur.
ELIGIBLE_ZONES = ("CONFIRMED",)


def _score_of(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _missing_engines_for_conviction(item: dict[str, Any]) -> list[str]:
    """Hangi motorlar henüz devrede değil? CONVICTION genellikle ≥2
    motor onayı bekler. Bu hisse 1 motorla geliyorsa, "eksik motor"
    listesini surface ederiz — kullanıcı niye CONVICTION'a girmediğini
    görür."""
    components = item.get("components") or {}
    THRESH = 0.5
    active = []
    inactive = []
    LABEL_TR = {
        "float_pressure":      "Float Baskısı",
        "revenue_mispricing":  "Gelir/PD Düşüklüğü",
        "silent_volume":       "Sessiz Hacim",
        "price_action":        "Fiyat Aksiyonu",
        "compression":         "Volatilite Sıkışması",
        "ownership":           "Sahiplik",
        "fundamental_quality": "Temel Kalite",
        "kap_activity":        "KAP Operatör",
    }
    for k, v in components.items():
        if v is None:
            continue
        if v >= THRESH:
            active.append(LABEL_TR.get(k, k))
        elif v >= 0.30:
            # Borderline — close to firing
            inactive.append(LABEL_TR.get(k, k))
    return inactive[:3]


def _data_quality_blocker(item: dict[str, Any]) -> Optional[str]:
    """CONVICTION engine.bullwatch_alerts'ta data_quality='high' bekliyor.
    Eğer şu an medium/low ise, bunu surface ederiz — kullanıcı niye
    sistemin alarm vermediğini anlar (veri eksiği yüzünden)."""
    dq = item.get("data_quality")
    if dq == "medium":
        return "Veri kalitesi orta — bazı motorlar None döndü."
    if dq == "low":
        return "Veri kalitesi düşük — çoğu motor None."
    return None


def compute_pre_alarm_score(
    item: dict[str, Any],
    tahtaci_strength: float,
) -> float:
    """Pre-alarm adaylarını sıralamak için derived score.

    Mevcut bullwatch score + tahtaci_strength bonus. CONVICTION
    score'unu BOZMUYOR — sadece "yakında alarm verebilir" sıralaması
    için derived bir score.

    Formula:
      base       = bullwatch score (70-74 range)
      bonus      = tahtaci_strength * 8.0     (0..8 nokta)
      proximity  = (score - 70) / 5 * 4       (0..4 nokta — 74'te 4 puan)
      pre_score  = base + bonus + proximity   (78-86 range typical)

    Sıralama buna göre yapılır; alarmı tetiklemez.
    """
    base = _score_of(item)
    bonus = float(tahtaci_strength) * 8.0
    proximity = max(0.0, min(1.0, (base - PRE_SCORE_MIN) / 5.0)) * 4.0
    return round(base + bonus + proximity, 2)


def find_pre_alarm_candidates(
    items: list[dict[str, Any]],
    score_min: float = PRE_SCORE_MIN,
    score_max: float = PRE_SCORE_MAX,
    tahtaci_min: float = TAHTACI_MIN_STRENGTH,
    require_zone: tuple[str, ...] = ELIGIBLE_ZONES,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Mevcut bullwatch items listesinden pre-alarm adaylarını filtrele.

    Args:
        items: bullwatch /api/bullwatch'tan gelen items listesi
        score_min, score_max: score penceresi (default 70..75)
        tahtaci_min: tahtaci_signal_strength alt sınırı
        require_zone: zone whitelist (default CONFIRMED only)
        limit: dönen aday sayısı

    Returns:
        List of dicts:
            {
              "symbol": str,
              "score": float,
              "zone": str,
              "pattern": str,
              "tahtaci_strength": float,
              "tahtaci_label": str,
              "missing_engines": [list of engine labels],
              "data_quality_blocker": str|None,
              "pre_alarm_score": float,    # sorting only, NOT a real score
              "sector_tr": str|None,
              "metrics": {kept-thin},
            }
        Sorted by pre_alarm_score desc.
    """
    # Lazy import — explainability module computes tahtacı strength
    try:
        from engine.bullwatch_explainability import (
            compute_tahtaci_signal_strength,
        )
    except Exception as exc:
        log.warning("explainability import failed: %r", exc)
        return []

    out: list[dict[str, Any]] = []
    for it in (items or []):
        score = _score_of(it)
        if not (score_min <= score < score_max):
            continue
        zone = it.get("zone") or ""
        if require_zone and zone not in require_zone:
            continue
        ts = compute_tahtaci_signal_strength(
            it.get("components") or {},
            it.get("metrics") or {},
        )
        ts_score = ts.get("score") or 0.0
        if ts_score < tahtaci_min:
            continue
        out.append({
            "symbol": it.get("symbol"),
            "score": round(score, 1),
            "zone": zone,
            "pattern": it.get("pattern"),
            "sector_tr": it.get("sector_tr"),
            "tahtaci_strength": ts_score,
            "tahtaci_label": ts.get("label"),
            "tahtaci_components": ts.get("components") or {},
            "missing_engines": _missing_engines_for_conviction(it),
            "data_quality_blocker": _data_quality_blocker(it),
            "pre_alarm_score": compute_pre_alarm_score(it, ts_score),
        })

    out.sort(key=lambda r: -r["pre_alarm_score"])
    return out[: max(1, limit)]


def get_pre_alarm_summary(
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Banner aggregate — kaç hisse alarma yaklaşıyor, top tahtacı_strength."""
    candidates = find_pre_alarm_candidates(items, limit=100)
    top_ts = max(
        (c.get("tahtaci_strength") or 0.0 for c in candidates),
        default=0.0,
    )
    # Bucket by tahtacı strength
    bucket = {"net": 0, "guclu": 0, "erken": 0}
    for c in candidates:
        ts = c.get("tahtaci_strength") or 0.0
        if ts >= 0.6:
            bucket["net"] += 1
        elif ts >= 0.4:
            bucket["guclu"] += 1
        else:
            bucket["erken"] += 1
    return {
        "count": len(candidates),
        "top_tahtaci_strength": round(top_ts, 2),
        "buckets": bucket,
    }

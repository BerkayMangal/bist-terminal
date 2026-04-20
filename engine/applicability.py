# ================================================================
# BISTBULL TERMINAL V10.0 — SECTOR APPLICABILITY MATRIX
# Hangi metrik hangi sektör için geçerli?
#
# PROBLEM:
# V9.1'de tüm hisseler aynı metriklerle puanlanıyordu.
# Bankalar için Graham fair value, klasik Altman Z-Score anlamsız.
# Holdinglere EV/EBITDA uygulamak yanıltıcı.
#
# ÇÖZÜM:
# Her sektör-metrik kombinasyonu için üç durum:
#   "full" = tam uygulanabilir (normal puanlama)
#   "low"  = düşük güvenilirlik (puan üretilir, UI'da uyarı)
#   "na"   = uygulanamaz (puan üretilmez, ağırlık dağıtılır)
#
# KULLANIM:
#   from engine.applicability import get_applicability, adjust_weights
#
#   app = get_applicability("banka", "altman_z")  # → "na"
#   adjusted = adjust_weights(FA_WEIGHTS, "banka") # → N/A olanlar çıkar
# ================================================================

from __future__ import annotations

from typing import Optional

from config import SECTOR_APPLICABILITY, FA_WEIGHTS


# ================================================================
# CORE LOOKUP
# ================================================================
def get_applicability(sector_group: str, metric_key: str) -> str:
    """
    Sektör-metrik uygulanabilirlik durumunu döner.

    Args:
        sector_group: map_sector() çıktısı (ör: "banka", "sanayi")
        metric_key: Metrik ismi (ör: "altman_z", "graham_fair_value")

    Returns:
        "full" | "low" | "na"
    """
    sector_rules = SECTOR_APPLICABILITY.get(sector_group, {})
    return sector_rules.get(metric_key, "full")


def is_applicable(sector_group: str, metric_key: str) -> bool:
    """Metrik bu sektör için uygulanabilir mi? (full veya low = True, na = False)"""
    return get_applicability(sector_group, metric_key) != "na"


def is_low_confidence(sector_group: str, metric_key: str) -> bool:
    """Metrik düşük güvenilirlik mi?"""
    return get_applicability(sector_group, metric_key) == "low"


# ================================================================
# SKOR BOYUTLARININ METRİK HARİTASI
# Her skor boyutunun hangi metriklere bağlı olduğu.
# N/A metrik varsa o boyut etkilenir.
# ================================================================
SCORE_METRIC_MAP: dict[str, list[str]] = {
    "value": ["pe", "pb", "ev_ebitda", "fcf_yield", "graham_fair_value"],
    "quality": ["roe", "roic", "operating_margin", "net_margin"],
    "growth": ["revenue_growth", "eps_growth", "ebitda_growth"],
    "balance": ["altman_z", "net_debt_ebitda", "debt_equity", "current_ratio"],
    "earnings": ["beneish_m", "cfo_to_ni", "fcf_yield"],
    "moat": ["gross_margin", "operating_margin", "asset_turnover"],
    "capital": ["roic", "fcf_yield", "dividend_yield"],
}


def get_score_applicability(sector_group: str, score_key: str) -> str:
    """
    Bir skor boyutunun sektör bazlı toplam uygulanabilirliğini döner.

    Mantık:
    - Boyuttaki tüm metrikler "na" → boyut "na"
    - Boyuttaki herhangi bir metrik "na" → boyut "low"
    - Hepsi "full" → boyut "full"
    """
    metrics = SCORE_METRIC_MAP.get(score_key, [])
    if not metrics:
        return "full"

    statuses = [get_applicability(sector_group, m) for m in metrics]

    if all(s == "na" for s in statuses):
        return "na"
    if any(s == "na" for s in statuses):
        return "low"
    if any(s == "low" for s in statuses):
        return "low"
    return "full"


# ================================================================
# AĞIRLIK AYARLAMA — N/A boyutları çıkar, ağırlık redistribüte et
# ================================================================
def adjust_weights(
    weights: dict[str, float],
    sector_group: str,
) -> dict[str, float]:
    """
    N/A olan skor boyutlarını çıkar ve ağırlıklarını
    kalan boyutlara orantılı dağıt.

    Örnek:
        FA_WEIGHTS = {"quality": 0.30, "value": 0.18, "balance": 0.10, ...}
        Banka sektörü → balance "na" (Altman N/A)
        → balance çıkar, 0.10 ağırlık diğerlerine orantılı dağılır

    Args:
        weights: Orijinal ağırlık dict'i (ör: FA_WEIGHTS)
        sector_group: Sektör grubu

    Returns:
        Ayarlanmış ağırlık dict'i (toplam = 1.0)
    """
    applicable: dict[str, float] = {}
    removed_weight: float = 0.0

    for key, weight in weights.items():
        app = get_score_applicability(sector_group, key)
        if app == "na":
            removed_weight += weight
        else:
            applicable[key] = weight

    if not applicable:
        return weights.copy()

    if removed_weight > 0:
        total_remaining = sum(applicable.values())
        if total_remaining > 0:
            scale = 1.0 / total_remaining
            return {k: round(v * scale, 4) for k, v in applicable.items()}

    return applicable


# ================================================================
# APPLICABILITY FLAGS — analiz çıktısına eklenir
# ================================================================
def build_applicability_flags(sector_group: str) -> dict[str, str]:
    """
    Tüm metrikler ve skor boyutları için applicability flag'leri üret.
    AnalysisSnapshot'a eklenir — UI bu bilgiyle uyarı gösterir.

    Returns:
        {
            "metrics": {"altman_z": "na", "roe": "full", ...},
            "scores": {"balance": "low", "quality": "full", ...}
        }
    """
    # Metrik bazlı
    all_metrics = set()
    for metrics in SCORE_METRIC_MAP.values():
        all_metrics.update(metrics)

    metric_flags = {m: get_applicability(sector_group, m) for m in sorted(all_metrics)}

    # Skor bazlı
    score_flags = {
        key: get_score_applicability(sector_group, key)
        for key in SCORE_METRIC_MAP
    }

    return {
        "metrics": metric_flags,
        "scores": score_flags,
    }

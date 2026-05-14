"""Portfolio exit signal engine — "Ne zaman satayım?".

Açık pozisyon için BullWatch'ın güncel durumu + fiyat hareketi
karşılaştırılır. Dört kriter weight'li bir araya gelir:

  1. Zone degradation  (CONVICTION → CONFIRMED → EARLY → kaybolma)
  2. Score drop        (entry score'dan >10 puan düşüş)
  3. Tahtacı weakening (kap_activity + ownership zayıfladı = tahtacı çekildi)
  4. Stop loss         (entry'den -%X düşüş — kullanıcı tanımlı)
  5. Take profit       (entry'den +%Y yükseliş — kullanıcı tanımlı, opsiyonel)

Her kriter 0-100 arası "exit_points" üretir; weighted sum verdict
üretir:
  ≥ 65   → "sell"     (kırmızı banner, sat)
  ≥ 35   → "caution"  (sarı, izlemeye al)
  <  35  → "hold"     (yeşil, devam)

Mantık: BullWatch mantığını BOZMUYORUZ — bu read-only bir overlay.
Pozisyon storage'a yazma yok, sadece açık pozisyonu güncel scan
output'uyla karşılaştırıyor.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.portfolio_signals")


# Zone ranking — degradation tespiti için
_ZONE_RANK = {"EARLY": 1, "CONFIRMED": 2, "CONVICTION": 3}


# Kriterlerin verdict skorundaki ağırlıkları. Toplam %100.
WEIGHTS = {
    "zone_degradation": 0.30,
    "score_drop":       0.25,
    "tahtaci_weak":     0.20,
    "stop_loss":        0.20,
    "take_profit":      0.05,    # düşük çünkü sadece "kar al" tavsiyesi
}


# Calibrated against the "single strong criterion" use cases:
#   - Delisting alone (zone_degradation=100, weighted 30) → CAUTION
#   - 2 zones down + score drop → CAUTION/SELL
#   - Stop loss hit → SELL (hard override below)
# A combination of 2 weakly-firing signals (each ~50pt) → SELL.
SELL_THRESHOLD    = 50.0
CAUTION_THRESHOLD = 25.0


def _norm(t: str) -> str:
    return (t or "").upper().strip().replace(".IS", "")


def _zone_degradation_points(
    entry_zone: Optional[str],
    current_zone: Optional[str],
    listed: bool,
) -> tuple[float, Optional[str]]:
    """Zone düştü mü? Listeden kaybolduysa en güçlü sinyal."""
    if not listed:
        return 100.0, "Hisse BullWatch listesinden düştü (eligible değil)"
    if not entry_zone or not current_zone:
        return 0.0, None
    er = _ZONE_RANK.get(entry_zone.upper(), 0)
    cr = _ZONE_RANK.get(current_zone.upper(), 0)
    if cr < er:
        steps = er - cr
        # 1 step → 50pt, 2 steps → 100pt
        pts = min(100.0, 50.0 * steps)
        return pts, f"Zone düştü: {entry_zone} → {current_zone}"
    return 0.0, None


def _score_drop_points(
    entry_score: Optional[float],
    current_score: Optional[float],
) -> tuple[float, Optional[str]]:
    """Entry score'dan ne kadar düştü?
    -10pt → 50pt, -20pt+ → 100pt, +ise 0pt"""
    if entry_score is None or current_score is None:
        return 0.0, None
    diff = float(current_score) - float(entry_score)
    if diff >= 0:
        return 0.0, None
    drop = abs(diff)
    # Linear 0..100 between 5pt drop and 20pt drop
    pts = min(100.0, max(0.0, (drop - 5.0) / 15.0 * 100.0))
    if pts > 5:
        return pts, f"Skor düştü: {entry_score:.0f} → {current_score:.0f} (-{drop:.0f})"
    return 0.0, None


def _tahtaci_weak_points(
    entry_kap: Optional[float],
    entry_own: Optional[float],
    current_kap: Optional[float],
    current_own: Optional[float],
) -> tuple[float, Optional[str]]:
    """Tahtacı imzaları zayıfladı mı?
    Entry'de strong (≥0.5) iken now <0.3 → güçlü çekiliş.
    """
    flags: list[str] = []
    pts = 0.0
    # KAP activity
    if (entry_kap or 0) >= 0.5 and (current_kap or 0) < 0.3:
        pts += 50
        flags.append("KAP aktivitesi kayboldu")
    elif (entry_kap or 0) - (current_kap or 0) > 0.3:
        pts += 30
        flags.append("KAP aktivitesi zayıfladı")
    # Ownership
    if (entry_own or 0) >= 0.5 and (current_own or 0) < 0.3:
        pts += 50
        flags.append("Insider sinyali kayboldu")
    elif (entry_own or 0) - (current_own or 0) > 0.3:
        pts += 30
        flags.append("Sahiplik sinyali zayıfladı")
    pts = min(100.0, pts)
    return pts, " · ".join(flags) if flags else None


def _stop_loss_points(
    entry_price: Optional[float],
    current_price: Optional[float],
    stop_pct: float,
) -> tuple[float, Optional[str]]:
    """Stop seviyesine ne kadar yaklaştı / aştı?
    stop_pct negatif (örn. -8.0 = entry'den %8 aşağı).
    Aşıldıysa 100pt; yaklaşıyorsa kademeli."""
    if entry_price is None or current_price is None or entry_price <= 0:
        return 0.0, None
    if stop_pct >= 0:
        return 0.0, None   # safety: stop loss negatif olmalı
    change_pct = (float(current_price) - float(entry_price)) / float(entry_price) * 100.0
    if change_pct <= stop_pct:
        # Stop seviyesi aşıldı
        return 100.0, f"Stop tetiklendi: %{change_pct:.1f} (limit %{stop_pct:.1f})"
    # Kademeli: stop seviyesinin %50'sine geldiyse 40pt, %75'inde 70pt
    progress = change_pct / stop_pct    # 0..1 between entry and stop
    if progress >= 0.75:
        return 70.0, f"Stop'a yakın: %{change_pct:.1f} (limit %{stop_pct:.1f})"
    if progress >= 0.5:
        return 40.0, f"Stop'a %50 yaklaştı: %{change_pct:.1f}"
    return 0.0, None


def _take_profit_points(
    entry_price: Optional[float],
    current_price: Optional[float],
    target_pct: float,
) -> tuple[float, Optional[str]]:
    """Take profit hedefine ulaşıldı mı?"""
    if entry_price is None or current_price is None or entry_price <= 0:
        return 0.0, None
    if target_pct <= 0:
        return 0.0, None    # take profit off
    change_pct = (float(current_price) - float(entry_price)) / float(entry_price) * 100.0
    if change_pct >= target_pct:
        return 100.0, f"Hedef tetiklendi: %{change_pct:.1f} (hedef %{target_pct:.1f})"
    return 0.0, None


def compute_exit_signal(
    position: dict[str, Any],
    current_item: Optional[dict[str, Any]] = None,
    current_price: Optional[float] = None,
) -> dict[str, Any]:
    """Tek pozisyon için exit signal hesapla.

    Args:
        position: portfolio_storage.get_by_id() output
        current_item: live BullWatch item — None ise "listeden düştü"
        current_price: opsiyonel, current_item'da yoksa explicit

    Returns:
        {
          "verdict": "hold"|"caution"|"sell",
          "score": 0..100 (weighted),
          "reasons": [string],
          "details": {
              "zone_degradation": pts,
              "score_drop": pts,
              "tahtaci_weak": pts,
              "stop_loss": pts,
              "take_profit": pts,
          },
          "pnl_pct": float | None,
        }
    """
    out: dict[str, Any] = {
        "verdict": "hold", "score": 0.0,
        "reasons": [], "details": {},
        "pnl_pct": None, "current_price": None, "current_zone": None,
    }

    listed = current_item is not None
    # Resolve current price — prefer explicit, fallback to item
    if current_price is None and current_item:
        m = current_item.get("metrics") or {}
        # Try a few common spots
        current_price = m.get("last_price") or current_item.get("price")
    if current_price is None and current_item:
        # Some scan outputs put it under nested keys
        current_price = (current_item.get("snapshot") or {}).get("last_price")
    out["current_price"] = current_price

    current_zone = (current_item or {}).get("zone")
    current_score = (current_item or {}).get("score")
    current_components = (current_item or {}).get("components") or {}
    current_kap = current_components.get("kap_activity")
    current_own = current_components.get("ownership")
    out["current_zone"] = current_zone

    entry_zone = position.get("zone_at_entry")
    entry_score = position.get("score_at_entry")
    entry_kap = position.get("kap_at_entry")
    entry_own = position.get("own_at_entry")
    entry_price = position.get("entry_price")
    stop_pct = position.get("stop_loss_pct") or -8.0
    target_pct = position.get("take_profit_pct") or 0.0

    if entry_price and current_price:
        out["pnl_pct"] = round(
            (float(current_price) - float(entry_price))
            / float(entry_price) * 100.0,
            2,
        )

    pts_zone, r_zone = _zone_degradation_points(entry_zone, current_zone, listed)
    pts_score, r_score = _score_drop_points(entry_score, current_score)
    pts_tahtaci, r_tahtaci = _tahtaci_weak_points(
        entry_kap, entry_own, current_kap, current_own,
    )
    pts_stop, r_stop = _stop_loss_points(entry_price, current_price, stop_pct)
    pts_tp, r_tp = _take_profit_points(entry_price, current_price, target_pct)

    out["details"] = {
        "zone_degradation": round(pts_zone, 1),
        "score_drop":       round(pts_score, 1),
        "tahtaci_weak":     round(pts_tahtaci, 1),
        "stop_loss":        round(pts_stop, 1),
        "take_profit":      round(pts_tp, 1),
    }

    # Weighted sum
    weighted = (
        pts_zone     * WEIGHTS["zone_degradation"]
        + pts_score    * WEIGHTS["score_drop"]
        + pts_tahtaci  * WEIGHTS["tahtaci_weak"]
        + pts_stop     * WEIGHTS["stop_loss"]
        + pts_tp       * WEIGHTS["take_profit"]
    )
    out["score"] = round(weighted, 1)

    # Reasons compilation
    for r in (r_zone, r_score, r_tahtaci, r_stop, r_tp):
        if r:
            out["reasons"].append(r)

    # Hard overrides — these signals are USER-DEFINED hard limits,
    # not part of the weighted system:
    #   - Stop loss hit: kullanıcı zaten kabul etti bu noktada çıkacağını
    #   - Take profit hit: hedef tamam, kar al
    # Bunlar tek başına SELL'i tetiklemeli, weighted skor ne olursa olsun.
    if pts_stop >= 100:
        out["verdict"] = "sell"
        if "Stop tetiklendi" not in " ".join(out["reasons"]):
            pass  # already in reasons
    elif pts_tp >= 100:
        out["verdict"] = "sell"
    elif weighted >= SELL_THRESHOLD:
        out["verdict"] = "sell"
    elif weighted >= CAUTION_THRESHOLD:
        out["verdict"] = "caution"
    else:
        out["verdict"] = "hold"
    return out


def compute_signals_for_open_positions(
    positions: list[dict[str, Any]],
    items_by_ticker: dict[str, dict[str, Any]],
    prices_by_ticker: Optional[dict[str, float]] = None,
) -> list[dict[str, Any]]:
    """Tüm açık pozisyonlar için signal hesapla.

    Args:
        positions: portfolio_storage.get_open()
        items_by_ticker: bullwatch live items keyed by ticker
        prices_by_ticker: optional, ticker → current price map
    """
    out: list[dict[str, Any]] = []
    prices = prices_by_ticker or {}
    for pos in positions:
        sym = _norm(pos.get("ticker") or "")
        item = items_by_ticker.get(sym)
        price = prices.get(sym)
        sig = compute_exit_signal(pos, current_item=item, current_price=price)
        out.append({**pos, "signal": sig})
    # Sort: sell first, then caution, then hold; within band by score desc
    verdict_rank = {"sell": 0, "caution": 1, "hold": 2}
    out.sort(key=lambda r: (
        verdict_rank.get(r["signal"]["verdict"], 9),
        -(r["signal"]["score"] or 0),
    ))
    return out

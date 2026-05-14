"""Tahtacı Sektör Rotasyonu — "Tahtacılar hangi sektöre yöneldi?"

BullWatch storage'ından (alarm history + membership events) sektör
bazında net aktivite skoru çıkarır. Hangi sektör ısınıyor, hangisi
soğuyor görsel olarak göstermek için.

Pozitif sinyaller (sektör ısınıyor):
  • ENTRY        — liste'ye yeni girdi
  • ZONE_UPGRADE — zone yükseldi
  • CONVICTION alarmı (en güçlü)

Negatif sinyaller (sektör soğuyor):
  • EXIT         — listeden düştü
  • ZONE_DOWNGRADE

Net aktivite = (positives × weight) − (negatives × weight)
Sektörler net aktiviteye göre sıralanır.

Read-only — mevcut alarm/membership storage'larını sadece okur, yan
etkisi yoktur.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.bw_sector_rotation")


# Per-event weights — CONVICTION alarmı en güçlü "tahtacı bu sektöre
# yöneldi" sinyali; ENTRY/UPGRADE daha hafif. EXIT/DOWNGRADE simetrik
# negatif ama yarı ağırlık (çıkış kararı gel-git'li olur).
EVENT_WEIGHTS: dict[str, float] = {
    "ALARM":          3.0,      # CONVICTION alarm = en güçlü sinyal
    "ENTRY":          1.0,
    "ZONE_UPGRADE":   1.5,
    "EXIT":          -0.5,
    "ZONE_DOWNGRADE": -1.0,
}


def _norm_sector(s: Optional[str]) -> str:
    if not s:
        return "Diğer"
    return str(s).strip() or "Diğer"


def _parse_iso(s: Optional[str]) -> Optional[_dt.datetime]:
    if not s:
        return None
    try:
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d
    except (TypeError, ValueError):
        return None


def _sector_for_ticker(ticker: str) -> Optional[str]:
    """Resolve ticker → sector via the live BullWatch cache (current
    scan items). Membership events / alarms persist only ticker; sector
    comes from the snapshot."""
    if not ticker:
        return None
    sym = ticker.upper().strip().replace(".IS", "")
    try:
        from api.bullwatch import _CACHE
        items = ((_CACHE.get("items") or {}).get("items")) or []
        for it in items:
            if (it.get("symbol") or "").upper() == sym:
                return _norm_sector(it.get("sector_tr"))
    except Exception:
        pass
    return None


def _gather_alarms(cutoff: _dt.datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        from infra import bullwatch_alerts_storage as st
        rows = st.get_recent(limit=500, since_days=30)
    except Exception as exc:
        log.debug("alarms fetch: %r", exc)
        return out
    for r in rows:
        ts = r.get("alarmed_at")
        d = _parse_iso(ts)
        if d is None or d < cutoff:
            continue
        sector = r.get("sector_tr") or _sector_for_ticker(r.get("ticker"))
        out.append({
            "event_type": "ALARM",
            "ticker": (r.get("ticker") or "").upper(),
            "sector": _norm_sector(sector),
            "occurred_at": ts,
            "ts": d.timestamp(),
        })
    return out


def _gather_membership(cutoff: _dt.datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        from infra import bullwatch_membership_storage as st
        rows = st.get_recent(limit=1000, since_days=30)
    except Exception as exc:
        log.debug("membership fetch: %r", exc)
        return out
    for r in rows:
        ts = r.get("occurred_at")
        d = _parse_iso(ts)
        if d is None or d < cutoff:
            continue
        etype = r.get("event_type")
        if etype not in EVENT_WEIGHTS:
            continue
        # Membership storage doesn't include sector — derive from current
        # bullwatch cache. Tickers that have rotated out won't resolve
        # cleanly; bucket them under "Diğer" so they don't get lost.
        sector = _sector_for_ticker(r.get("ticker"))
        out.append({
            "event_type": etype,
            "ticker": (r.get("ticker") or "").upper(),
            "sector": _norm_sector(sector),
            "occurred_at": ts,
            "ts": d.timestamp(),
        })
    return out


def compute_rotation(
    window_days: int = 7,
    include_alarms: bool = True,
    include_membership: bool = True,
) -> dict[str, Any]:
    """Per-sector activity scores within the lookback window.

    Output schema:
        {
          "as_of": ISO,
          "window_days": int,
          "sectors": [
            {
              "sector": "Endüstri",
              "net_score": 8.5,         # weighted sum
              "events": {
                "ALARM": 2,
                "ENTRY": 3,
                "ZONE_UPGRADE": 1,
                "EXIT": 1,
                "ZONE_DOWNGRADE": 0,
              },
              "positives": 6,            # heating events
              "negatives": 1,            # cooling events
              "trend": "hot"|"warm"|"neutral"|"cooling",
              "top_tickers": [string],  # most-active tickers in this sector
            },
            ...
          ],
          "total_events": int,
        }

    Trend bands (calibrated against typical week):
        ≥6 net   → "hot"     (🔥 ısınıyor)
        ≥2 net   → "warm"    (⚡ uyanık)
        −2..2    → "neutral" (➡️ sakin)
        <−2 net  → "cooling" (❄️ soğuyor)
    """
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(
        days=max(1, window_days)
    )
    events: list[dict[str, Any]] = []
    if include_alarms:
        events += _gather_alarms(cutoff)
    if include_membership:
        events += _gather_membership(cutoff)

    by_sector: dict[str, dict[str, Any]] = {}
    for ev in events:
        sec = ev["sector"]
        entry = by_sector.setdefault(sec, {
            "sector": sec,
            "net_score": 0.0,
            "events": {k: 0 for k in EVENT_WEIGHTS},
            "positives": 0,
            "negatives": 0,
            "tickers": {},
        })
        etype = ev["event_type"]
        entry["events"][etype] = entry["events"].get(etype, 0) + 1
        w = EVENT_WEIGHTS[etype]
        entry["net_score"] += w
        if w > 0:
            entry["positives"] += 1
        elif w < 0:
            entry["negatives"] += 1
        # Track ticker activity within sector
        tk = ev["ticker"]
        if tk:
            entry["tickers"][tk] = entry["tickers"].get(tk, 0) + abs(w)

    out_sectors: list[dict[str, Any]] = []
    for sec, e in by_sector.items():
        net = round(e["net_score"], 1)
        trend = (
            "hot"      if net >= 6 else
            "warm"     if net >= 2 else
            "cooling"  if net <= -2 else
            "neutral"
        )
        # Top 3 tickers in this sector by activity
        top_tickers = sorted(
            e["tickers"].items(), key=lambda kv: -kv[1],
        )[:3]
        out_sectors.append({
            "sector": sec,
            "net_score": net,
            "events": e["events"],
            "positives": e["positives"],
            "negatives": e["negatives"],
            "trend": trend,
            "top_tickers": [t for t, _ in top_tickers],
        })

    out_sectors.sort(key=lambda s: -s["net_score"])
    return {
        "as_of": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "window_days": window_days,
        "sectors": out_sectors,
        "total_events": len(events),
    }


def get_rotation_summary(window_days: int = 7) -> dict[str, Any]:
    """One-line summary for the BullWatch banner — "3 sektör ısınıyor,
    1 soğuyor"."""
    data = compute_rotation(window_days=window_days)
    counts = {"hot": 0, "warm": 0, "neutral": 0, "cooling": 0}
    for s in data.get("sectors") or []:
        counts[s.get("trend", "neutral")] = counts.get(s.get("trend", "neutral"), 0) + 1
    return {
        "window_days": window_days,
        "total_events": data.get("total_events", 0),
        "trend_counts": counts,
        "sectors_count": len(data.get("sectors") or []),
    }

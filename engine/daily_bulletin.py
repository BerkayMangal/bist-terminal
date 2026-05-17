# ================================================================
# BISTBULL TERMINAL — DAILY BULLETIN GENERATOR
# engine/daily_bulletin.py
#
# Stage 7b. Builds the daily summary that fires after the 18:30 IST
# post_close scan. The generator pulls from already-computed sources
# (BullWatch snapshot, sector rotation, KAP feed, pre-alarms, heatmap)
# — it does NOT trigger fresh fetches. By the time the post_close
# scheduler runs this, all caches are warm.
#
# Output shape (versioned via schema_version in storage):
#   {
#     "headline": str — one-sentence summary
#     "conviction_top": [{symbol, score, pattern, reasons[]}, ...]
#     "confirmed_new": [{symbol, score, ...}, ...]  — entered today
#     "sector_rotation": [{sector, activity_score, ...}, ...]
#     "biggest_movers": {"gainers": [...], "losers": [...]}
#     "kap_highlights": [{ticker, type, subject, timestamp}, ...]
#     "pre_alarms": [{symbol, score, hints}, ...]
#     "stats": {scanned, eligible, conviction, confirmed, early}
#   }
# ================================================================

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.daily_bulletin")


SCHEMA_VERSION = 1


def _safe(fn, default=None, label: str = ""):
    """Call ``fn()`` and swallow exceptions. Bulletin pieces are
    best-effort — one broken source must not block the whole bulletin."""
    try:
        return fn()
    except Exception as exc:
        log.debug("daily_bulletin: %s source failed: %r", label, exc)
        return default


def _bullwatch_top(limit: int = 5) -> tuple[list[dict], dict[str, int]]:
    """Pull current BullWatch items + zone counts. Returns
    (top_conviction, stats).

    Önce kalıcı snapshot store'dan okur (Railway deploy/restart sonrası
    da doludur); yoksa in-memory _CACHE'e düşer. Eskiden yalnız
    in-memory _CACHE okunuyordu — restart sonrası boş kalıyor, bülten
    `scanned:0` ile tamamen boş üretiliyordu."""
    items_blob: list = []
    try:
        from api.bullwatch import _read_snapshot_payload
        snap = _read_snapshot_payload(limit=500)
        if snap and isinstance(snap.get("items"), list):
            items_blob = snap["items"]
    except Exception:
        pass
    if not items_blob:
        from api.bullwatch import _CACHE
        items_blob = (_CACHE.get("items") or {}).get("items") or []
    if not isinstance(items_blob, list):
        return [], {}
    by_zone: dict[str, int] = {"CONVICTION": 0, "CONFIRMED": 0, "EARLY": 0}
    conviction: list[dict] = []
    for it in items_blob:
        zone = (it.get("zone") or "").upper()
        if zone in by_zone:
            by_zone[zone] += 1
        if zone == "CONVICTION":
            conviction.append({
                "symbol": it.get("symbol"),
                "score": it.get("score"),
                "zone": zone,
                "pattern": it.get("pattern"),
                "reasons": it.get("reasons") or [],
            })
    conviction.sort(key=lambda r: (r.get("score") or 0), reverse=True)
    return conviction[:limit], {
        "scanned": len(items_blob),
        "conviction": by_zone["CONVICTION"],
        "confirmed": by_zone["CONFIRMED"],
        "early": by_zone["EARLY"],
        "eligible": by_zone["CONVICTION"] + by_zone["CONFIRMED"] + by_zone["EARLY"],
    }


def _confirmed_new_today() -> list[dict]:
    """CONFIRMED tickers that entered the zone today (memberships
    table)."""
    try:
        from infra import bullwatch_membership_storage as mship
    except Exception:
        return []
    today_ist = dt.datetime.now(dt.timezone.utc).astimezone(
        dt.timezone(dt.timedelta(hours=3))
    ).date().isoformat()
    rows = _safe(
        lambda: mship.get_recent(limit=200, since_days=2) or [],
        default=[],
        label="memberships",
    )
    out: list[dict] = []
    for r in rows or []:
        when = r.get("entered_at") or r.get("created_at") or ""
        if today_ist not in str(when):
            continue
        zone = (r.get("zone") or "").upper()
        if zone not in ("CONFIRMED", "CONVICTION"):
            continue
        out.append({
            "symbol": r.get("ticker"),
            "zone": zone,
            "entered_at": when,
            "score": r.get("score"),
        })
    return out[:10]


def _sector_rotation_top(limit: int = 3) -> list[dict]:
    try:
        from engine import bullwatch_sector_rotation as rot
    except Exception:
        return []
    res = _safe(
        lambda: rot.compute_sector_rotation(window_days=5),
        default=None,
        label="sector_rotation",
    )
    if not res:
        return []
    sectors = res.get("sectors") or []
    return sectors[:limit]


def _biggest_movers() -> dict[str, list[dict]]:
    """Pull from heatmap cache (already populated by background loop)."""
    try:
        from core.cache import heatmap_cache
    except Exception:
        return {"gainers": [], "losers": []}
    hm = _safe(lambda: heatmap_cache.get("heatmap"), default=None, label="heatmap")
    if not hm:
        return {"gainers": [], "losers": []}
    # Heatmap stores sectors → stocks. Flatten and rank.
    rows: list[dict] = []
    for sec in (hm.get("sectors") or []):
        for s in (sec.get("stocks") or []):
            rows.append({
                "ticker": s.get("ticker"),
                "change_pct": s.get("change_pct"),
                "price": s.get("price"),
                "sector": sec.get("sector"),
            })
    rows = [r for r in rows if r.get("change_pct") is not None]
    rows.sort(key=lambda r: r["change_pct"], reverse=True)
    gainers = rows[:5]
    losers = list(reversed(rows[-5:])) if len(rows) >= 5 else list(reversed(rows))
    return {"gainers": gainers, "losers": losers}


def _kap_highlights(limit: int = 8) -> list[dict]:
    """Today's most impactful operator-signal disclosures."""
    try:
        from infra import kap_storage
    except Exception:
        return []
    # Pull last 24h; daily_bulletin runs at 18:30 IST → that window
    # comfortably covers the trading day plus prior evening news.
    rows = _safe(
        lambda: kap_storage.get_recent(limit=80) or [],
        default=[],
        label="kap_storage",
    )
    out: list[dict] = []
    for r in rows or []:
        op_tag = (r.get("operator_tag") or r.get("rule_type") or "")
        if not op_tag:
            # Skip pure financial-report disclosures from the headline list —
            # they're noisy without context.
            continue
        out.append({
            "ticker": r.get("ticker"),
            "type": op_tag,
            "subject": r.get("subject"),
            "publish_date": r.get("publish_date"),
            "disclosure_index": r.get("disclosure_index"),
        })
        if len(out) >= limit:
            break
    return out


def _pre_alarm_candidates(limit: int = 5) -> list[dict]:
    try:
        from engine import bullwatch_prealarm as pa
    except Exception:
        return []
    cands = _safe(lambda: pa.compute_pre_alarms(limit=limit) or [],
                  default=[], label="prealarm")
    out: list[dict] = []
    for c in cands or []:
        out.append({
            "symbol": c.get("symbol") or c.get("ticker"),
            "score": c.get("score"),
            "tahtaci_strength": c.get("tahtaci_strength"),
            "hints": c.get("hints") or [],
        })
    return out


def _build_headline(stats: dict[str, int], conviction_top: list[dict]) -> str:
    """One-sentence executive summary."""
    conv_n = stats.get("conviction") or 0
    conf_n = stats.get("confirmed") or 0
    if conv_n == 0 and conf_n == 0:
        return "Bugün eligible sinyal yok — radar sessiz."
    parts: list[str] = []
    if conv_n:
        parts.append(f"{conv_n} CONVICTION")
    if conf_n:
        parts.append(f"{conf_n} CONFIRMED")
    sigs = " + ".join(parts)
    if conviction_top:
        top = conviction_top[0]
        return f"{sigs}; ön sıralarda {top.get('symbol')} ({(top.get('score') or 0):.0f})."
    return f"{sigs}."


def generate_bulletin_payload(
    now_utc: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    """Build a complete bulletin payload from current state. Pure
    composition — no fetches, no side effects (except module-level
    safe calls into other engines which are themselves read-only).

    Args:
        now_utc: override "now" for testing. Defaults to actual now.

    Returns:
        dict matching the contract documented at the top of this module.
        Every section is independently safe — a single engine outage
        only zeroes out its own section, the rest of the bulletin still
        renders.
    """
    if now_utc is None:
        now_utc = dt.datetime.now(dt.timezone.utc)

    conviction_top, stats = _bullwatch_top()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_utc.isoformat(),
        "stats": stats,
        "conviction_top": conviction_top,
        "confirmed_new": _confirmed_new_today(),
        "sector_rotation": _sector_rotation_top(),
        "biggest_movers": _biggest_movers(),
        "kap_highlights": _kap_highlights(),
        "pre_alarms": _pre_alarm_candidates(),
    }
    payload["headline"] = _build_headline(stats, conviction_top)
    return payload


def generate_and_persist(bulletin_date: Optional[str] = None) -> dict[str, Any]:
    """Build the bulletin AND save it to SQLite.

    Args:
        bulletin_date: YYYY-MM-DD override (tests/backfill). Default:
            today's Istanbul date.

    Returns:
        The full saved record (same shape as bulletin_storage.get).
    """
    from infra import bulletin_storage as _bs

    when = bulletin_date or _bs.istanbul_today()
    payload = generate_bulletin_payload()
    _bs.save(when, payload)
    log.info(
        "Daily bulletin generated for %s: %d CONVICTION, %d CONFIRMED, %d hot KAP",
        when,
        len(payload.get("conviction_top") or []),
        len(payload.get("confirmed_new") or []),
        len(payload.get("kap_highlights") or []),
    )
    return {
        "bulletin_date": when,
        "generated_at": payload["generated_at"],
        "schema_version": SCHEMA_VERSION,
        "content": payload,
    }

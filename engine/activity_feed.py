"""Unified activity feed — son 24 saatte BistBull'da ne oldu?

Tek bir chronologically-sorted listede:

  • ALARM           — yeni CONVICTION alarmı (bullwatch_alerts)
  • MEMBERSHIP      — listeye giriş / çıkış / zone değişimi
  • KAP_FINANCIAL   — yeni bilanço KAP'a düştü
  • SCORE_CHANGE    — auto-refresh anlamlı skor değişimi yakaladı

Watchlist filtresi opsiyonel — verilirse SADECE o ticker'ların eventleri
döner; verilmezse tüm universe.

Bu modül salt okur; depolama yapmaz, yan etkisi yoktur. Aggregation
sorgularını parallel thread'de çalıştırmak çağıranın işidir.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.activity_feed")

# Activity types. Tutarlı string keys — UI bunlara göre icon/renk seçer.
TYPE_ALARM = "ALARM"
TYPE_MEMBERSHIP = "MEMBERSHIP"
TYPE_KAP_FINANCIAL = "KAP_FINANCIAL"
TYPE_SCORE_CHANGE = "SCORE_CHANGE"


def _norm_ticker(t: str) -> str:
    return (t or "").upper().strip().replace(".IS", "")


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


def _within(iso: Optional[str], cutoff: _dt.datetime) -> bool:
    d = _parse_iso(iso)
    return d is not None and d >= cutoff


def _match_watchlist(ticker: str, wl_set: Optional[set[str]]) -> bool:
    """If wl_set is None → match everything. Otherwise only this set."""
    if wl_set is None:
        return True
    return _norm_ticker(ticker) in wl_set


def _fetch_alarms(cutoff: _dt.datetime,
                  wl_set: Optional[set[str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        from infra import bullwatch_alerts_storage as st
        rows = st.get_recent(limit=200, since_days=2)
    except Exception as exc:
        log.debug("alarms fetch: %r", exc)
        return out
    for r in rows:
        sym = _norm_ticker(r.get("ticker") or "")
        if not _match_watchlist(sym, wl_set):
            continue
        ts = r.get("alarmed_at")
        if not _within(ts, cutoff):
            continue
        score = r.get("score_at_alarm")
        zone = r.get("zone_at_alarm")
        pattern = r.get("pattern_at_alarm") or ""
        out.append({
            "type": TYPE_ALARM,
            "ticker": sym,
            "occurred_at": ts,
            "severity": "high",
            "summary": f"CONVICTION alarm · {zone or ''} · Skor {score or '?'}".strip(),
            "detail": pattern,
            "link": {"kind": "alarm", "alert_id": r.get("alert_id")},
        })
    return out


def _fetch_membership(cutoff: _dt.datetime,
                      wl_set: Optional[set[str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        from infra import bullwatch_membership_storage as st
        rows = st.get_recent(limit=300, since_days=2)
    except Exception as exc:
        log.debug("membership fetch: %r", exc)
        return out
    type_to_severity = {
        "ENTRY": "medium", "ZONE_UPGRADE": "medium",
        "EXIT": "low",     "ZONE_DOWNGRADE": "low",
    }
    type_to_summary = {
        "ENTRY":          lambda r: f"Listeye girdi · Zone: {r.get('new_zone') or '?'}",
        "EXIT":           lambda r: f"Listeden düştü · Önceki: {r.get('prev_zone') or '?'}",
        "ZONE_UPGRADE":   lambda r: f"Zone yükseldi · {r.get('prev_zone') or '?'} → {r.get('new_zone') or '?'}",
        "ZONE_DOWNGRADE": lambda r: f"Zone düştü · {r.get('prev_zone') or '?'} → {r.get('new_zone') or '?'}",
    }
    for r in rows:
        sym = _norm_ticker(r.get("ticker") or "")
        if not _match_watchlist(sym, wl_set):
            continue
        ts = r.get("occurred_at")
        if not _within(ts, cutoff):
            continue
        etype = r.get("event_type") or ""
        summary_fn = type_to_summary.get(etype)
        if summary_fn is None:
            continue
        out.append({
            "type": TYPE_MEMBERSHIP,
            "ticker": sym,
            "occurred_at": ts,
            "severity": type_to_severity.get(etype, "low"),
            "summary": summary_fn(r),
            "detail": (r.get("new_pattern") or r.get("prev_pattern") or ""),
            "event_type": etype,
            "link": {"kind": "membership", "event_id": r.get("event_id")},
        })
    return out


def _fetch_kap_financials(
    cutoff: _dt.datetime,
    wl_set: Optional[set[str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        from infra import kap_storage as st
    except Exception as exc:
        log.debug("kap_storage import: %r", exc)
        return out
    try:
        from data.kap_client import (
            FINANCIAL_REPORT_SUBJECTS,
            DISCLOSURE_TYPE_FINANCIAL,
        )
    except Exception:
        FINANCIAL_REPORT_SUBJECTS = {
            "finansal rapor",
            "konsolide finansal tablolar",
            "konsolide olmayan finansal tablolar",
        }
        DISCLOSURE_TYPE_FINANCIAL = "FR"

    rows: list[dict[str, Any]] = []
    if wl_set:
        # Fetch per-ticker — much cheaper when watchlist is small
        for sym in wl_set:
            try:
                rows.extend(st.get_by_ticker(sym, limit=20))
            except Exception:
                continue
    else:
        try:
            rows = st.get_recent(limit=500)
        except Exception as exc:
            log.debug("kap get_recent: %r", exc)
            return out

    seen_idx: set[int] = set()
    for r in rows:
        idx = r.get("disclosure_index")
        if idx is not None:
            if idx in seen_idx:
                continue
            seen_idx.add(idx)
        sym = _norm_ticker(r.get("ticker") or "")
        if not _match_watchlist(sym, wl_set):
            continue
        dtype = (r.get("disclosure_type") or "").upper()
        if dtype != DISCLOSURE_TYPE_FINANCIAL:
            continue
        subj = (r.get("subject") or "").lower().strip()
        if not any(s in subj for s in FINANCIAL_REPORT_SUBJECTS):
            continue
        ts = r.get("publish_date")
        if not _within(ts, cutoff):
            continue
        period = r.get("period")
        year = r.get("year")
        pq = f"Q{period} {year}" if period and year else (str(year) if year else "")
        rule = r.get("rule_type") or ""
        out.append({
            "type": TYPE_KAP_FINANCIAL,
            "ticker": sym,
            "occurred_at": ts,
            "severity": "high" if rule == "Yıllık" else "medium",
            "summary": f"KAP'a finansal rapor · {rule}{' · ' + pq if pq else ''}".strip(),
            "detail": r.get("subject") or "",
            "link": {
                "kind": "kap_disclosure",
                "disclosure_index": r.get("disclosure_index"),
            },
        })
    return out


def _fetch_score_changes(
    cutoff: _dt.datetime,
    wl_set: Optional[set[str]],
) -> list[dict[str, Any]]:
    """Pull the most recent auto_refresh cycle's score_changes."""
    out: list[dict[str, Any]] = []
    try:
        from engine.auto_refresh_stale import get_last_cycle
        cyc = get_last_cycle()
    except Exception as exc:
        log.debug("auto_refresh status: %r", exc)
        return out
    if not cyc:
        return out
    finished_at = cyc.get("finished_at")
    if not finished_at:
        return out
    # The cycle's finished_at is a unix timestamp (float)
    try:
        ts_dt = _dt.datetime.fromtimestamp(
            float(finished_at), tz=_dt.timezone.utc,
        )
    except (TypeError, ValueError):
        return out
    if ts_dt < cutoff:
        return out
    ts_iso = ts_dt.isoformat()
    for c in (cyc.get("score_changes") or []):
        sym = _norm_ticker(c.get("ticker") or "")
        if not _match_watchlist(sym, wl_set):
            continue
        delta = c.get("delta")
        before = c.get("before")
        after = c.get("after")
        if delta is None:
            continue
        out.append({
            "type": TYPE_SCORE_CHANGE,
            "ticker": sym,
            "occurred_at": ts_iso,
            "severity": "medium" if abs(delta) >= 5 else "low",
            "summary": (
                f"Auto-refresh skor değişimi · {before} → {after} "
                f"({'+' if delta > 0 else ''}{delta})"
            ),
            "detail": "",
            "link": {"kind": "freshness", "ticker": sym},
        })
    return out


def get_recent_activity(
    since_hours: int = 24,
    watchlist: Optional[list[str]] = None,
    limit: int = 80,
) -> dict[str, Any]:
    """Return a unified activity feed.

    Args:
        since_hours: lookback window (default 24h).
        watchlist:   if provided, only include events for these tickers.
        limit:       cap on number of events returned.

    Returns:
        {
            "items": [event, ...] sorted by occurred_at DESC,
            "counts": {ALARM: int, MEMBERSHIP: int, ...},
            "watchlist_filter": bool,
            "since_hours": int,
            "generated_at": ISO8601 str,
        }
    """
    since_hours = max(1, min(int(since_hours), 168))    # 1h .. 7d
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(
        hours=since_hours
    )
    wl_set: Optional[set[str]] = None
    if watchlist:
        wl_set = {_norm_ticker(t) for t in watchlist if t}
        if not wl_set:
            wl_set = None

    all_items: list[dict[str, Any]] = []
    all_items += _fetch_alarms(cutoff, wl_set)
    all_items += _fetch_membership(cutoff, wl_set)
    all_items += _fetch_kap_financials(cutoff, wl_set)
    all_items += _fetch_score_changes(cutoff, wl_set)

    # Sort by occurred_at DESC; events without a parseable date sink to bottom
    def _sort_key(e: dict) -> tuple[int, float]:
        d = _parse_iso(e.get("occurred_at"))
        if d is None:
            return (1, 0.0)
        return (0, -d.timestamp())

    all_items.sort(key=_sort_key)
    all_items = all_items[: max(1, limit)]

    counts: dict[str, int] = {}
    for ev in all_items:
        t = ev.get("type") or "?"
        counts[t] = counts.get(t, 0) + 1

    return {
        "items": all_items,
        "counts": counts,
        "watchlist_filter": wl_set is not None,
        "since_hours": since_hours,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }

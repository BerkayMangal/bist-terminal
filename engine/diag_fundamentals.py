"""Per-ticker fundamental-data freshness diagnostic.

Answers the question "is FORTE's bilanço actually fresh, or has my radar
been showing the same names because of stale cache?" by joining together:

  • raw_cache._fetched_at        → when did borsapy last refresh the ticker?
  • raw.latest_quarter           → which quarter the data points to
  • raw.quarterly_data_available → did the quarterly endpoint return rows?
  • KAP disclosures              → when was the most recent financial
                                    report (Q1/Q2/Q3/Yıllık) published?
  • gap = KAP date - borsapy fetch → if positive, borsapy missed a new
                                    filing; if negative, borsapy is current

Pure read-side — never writes, never fetches new data, safe to call on
every Radar render.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

log = logging.getLogger("bistbull.diag_fundamentals")


# Status bands for the data-age badge.
# Hours since last borsapy fetch.
_AGE_FRESH_HRS = 26.0     # background scan is 1h open / 3h closed; 26h
                          # gives a one-cycle grace before flagging "old"
_AGE_STALE_HRS = 72.0     # 3 days since last refresh = something broken


def _norm(t: str) -> str:
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


def _hours_since(iso: Optional[str]) -> Optional[float]:
    d = _parse_iso(iso)
    if d is None:
        return None
    delta = _dt.datetime.now(_dt.timezone.utc) - d
    return round(delta.total_seconds() / 3600.0, 1)


def _age_status(hours: Optional[float]) -> str:
    if hours is None:
        return "unknown"
    if hours <= _AGE_FRESH_HRS:
        return "fresh"
    if hours <= _AGE_STALE_HRS:
        return "old"
    return "stale"


def _lookup_raw_cache(sym: str) -> Optional[dict]:
    """Indirection layer so tests can monkeypatch raw_cache access at
    module scope (SafeCache.get is read-only at the instance level)."""
    try:
        from core.cache import raw_cache
    except Exception as exc:
        log.debug("raw_cache import failed for %s: %r", sym, exc)
        return None
    return raw_cache.get(sym + ".IS") or raw_cache.get(sym)


def _latest_kap_financial(ticker: str) -> dict[str, Any]:
    """Return the latest financial-report disclosure for ticker, or {}."""
    out: dict[str, Any] = {}
    sym = _norm(ticker)
    if not sym:
        return out
    try:
        from infra import kap_storage
        rows = kap_storage.get_by_ticker(sym, limit=50)
    except Exception as exc:
        log.debug("kap_storage lookup failed for %s: %r", sym, exc)
        return out

    # Pull data.kap_client.FINANCIAL_REPORT_SUBJECTS lazily so this module
    # works even when kap_client can't load (e.g. tests without pykap).
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

    for row in rows:
        dtype = (row.get("disclosure_type") or "").upper()
        if dtype != DISCLOSURE_TYPE_FINANCIAL:
            continue
        subj = (row.get("subject") or "").lower().strip()
        if not any(s in subj for s in FINANCIAL_REPORT_SUBJECTS):
            continue
        out = {
            "publish_date":   row.get("publish_date"),
            "subject":        row.get("subject"),
            "rule_type":      row.get("rule_type"),     # "3 Aylık" / "Yıllık" / ...
            "period":         row.get("period"),
            "year":           row.get("year"),
            "disclosure_index": row.get("disclosure_index"),
        }
        return out
    return out


def compute_data_freshness(ticker: str) -> dict[str, Any]:
    """Bundle every freshness signal we have for one ticker.

    Schema:
        {
            "ticker": "FORTE",
            "borsapy": {
                "fetched_at": "2026-05-13T..." | None,
                "age_hours": 12.3 | None,
                "latest_quarter": "2025Q4" | None,
                "quarterly_available": True/False,
                "is_bank": False,
                "source": "borsapy",
                "fetch_attempts": 1,
            },
            "kap": {
                "latest_financial_at": "2026-04-22T...",
                "subject": "...Konsolide Finansal Tablolar...",
                "rule_type": "Yıllık",
                "period": 4, "year": 2025,
                "age_days": 21,
                "disclosure_index": 1234567,
            },
            "gap_days": -8.0,                # borsapy is 8d AHEAD of KAP date
            "age_status": "fresh"|"old"|"stale"|"unknown",
            "warnings": [string, ...],       # human-readable nits
        }
    """
    sym = _norm(ticker)
    out: dict[str, Any] = {
        "ticker": sym,
        "borsapy": {
            "fetched_at": None,
            "age_hours": None,
            "latest_quarter": None,
            "quarterly_available": None,
            "is_bank": None,
            "source": None,
            "fetch_attempts": None,
        },
        "kap": {},
        "gap_days": None,
        "age_status": "unknown",
        "warnings": [],
    }
    if not sym:
        out["warnings"].append("empty ticker")
        return out

    # ---- borsapy raw cache ---------------------------------------
    cached = _lookup_raw_cache(sym)

    if cached is not None:
        fetched_at = cached.get("_fetched_at")
        out["borsapy"]["fetched_at"] = fetched_at
        out["borsapy"]["age_hours"] = _hours_since(fetched_at)
        out["borsapy"]["source"] = cached.get("source")
        out["borsapy"]["is_bank"] = cached.get("is_bank")
        out["borsapy"]["fetch_attempts"] = cached.get("_fetch_attempts")
        # Compute latest_quarter from financials_q columns when present
        fin_q = cached.get("financials_q")
        latest_q = None
        q_avail = False
        try:
            if fin_q is not None and hasattr(fin_q, "columns") and len(fin_q.columns):
                # First column is the most recent quarter in borsapy's layout
                latest_q = str(list(fin_q.columns)[0])
                q_avail = True
        except Exception:
            pass
        out["borsapy"]["latest_quarter"] = latest_q
        out["borsapy"]["quarterly_available"] = q_avail
    else:
        out["warnings"].append("borsapy cache miss")

    # ---- KAP latest financial-report disclosure ------------------
    kap = _latest_kap_financial(sym)
    if kap:
        out["kap"] = dict(kap)
        kdate = _parse_iso(kap.get("publish_date"))
        if kdate is not None:
            delta_days = (_dt.datetime.now(_dt.timezone.utc) - kdate).total_seconds() / 86400.0
            out["kap"]["age_days"] = round(delta_days, 1)
    else:
        out["warnings"].append("no KAP financial report on record")

    # ---- gap: KAP vs borsapy --------------------------------------
    bdate = _parse_iso(out["borsapy"]["fetched_at"])
    kdate = _parse_iso(out["kap"].get("publish_date"))
    if bdate is not None and kdate is not None:
        out["gap_days"] = round(
            (kdate - bdate).total_seconds() / 86400.0,
            1,
        )
        # If KAP filed AFTER our last borsapy fetch, we are behind.
        if out["gap_days"] is not None and out["gap_days"] > 1.0:
            out["warnings"].append(
                f"KAP filed {out['gap_days']:.1f}d AFTER last borsapy fetch — "
                "borsapy may not yet have ingested the new bilanço"
            )

    # ---- age status from borsapy fetch ----------------------------
    out["age_status"] = _age_status(out["borsapy"]["age_hours"])

    # ---- quarterly missing warning --------------------------------
    if (cached is not None
            and out["borsapy"]["quarterly_available"] is False
            and out["borsapy"]["is_bank"] is False):
        out["warnings"].append(
            "quarterly data missing — scoring falls back to annual (slower signal)"
        )

    # ---- score velocity (30-day) ---------------------------------
    # Surfaces "score is frozen for a month despite cache turning over"
    # — exactly the FORTE/LOGO complaint that started this whole tool.
    try:
        vel = compute_score_velocity(sym, days=30)
        out["velocity"] = vel
        if vel.get("frozen"):
            out["warnings"].append(
                f"score frozen {vel.get('n_snapshots',0)} gündür "
                f"(max günlük değişim {vel.get('max_jump',0)}) — "
                "fundamentals değişmiyor mu, yoksa pipeline tıkalı mı?"
            )
    except Exception as exc:
        log.debug("velocity %s: %r", sym, exc)
        out["velocity"] = None

    return out


# Severity used by the stale-list sort: stale > unknown > old > fresh.
# Higher is worse, so reverse-sorting puts the worst rows first.
STALE_SEVERITY = {"stale": 3, "unknown": 2, "old": 1, "fresh": 0}


def filter_stale_rows(
    rows: list[dict[str, Any]],
    threshold: str = "stale",
) -> list[dict[str, Any]]:
    """Filter + sort the summary rows for the stale-tickers panel.

    threshold:
      'stale'        → only stale + unknown
      'old'          → stale + unknown + old (anything not fresh)
      'any-warning'  → anything with warnings or non-fresh status
    """

    def _matches(r: dict) -> bool:
        st = r.get("age_status")
        if threshold == "old":
            return st in ("stale", "old", "unknown")
        if threshold == "any-warning":
            return bool(r.get("warnings")) or st != "fresh"
        # default: stale
        return st in ("stale", "unknown")

    matched = [r for r in rows if _matches(r)]
    matched.sort(
        key=lambda r: (
            -STALE_SEVERITY.get(r.get("age_status") or "fresh", 0),
            -(r.get("age_hours") or 0),
        )
    )
    return matched


def compute_score_velocity(
    ticker: str,
    days: int = 30,
    frozen_max_delta: float = 1.0,
) -> dict[str, Any]:
    """Pull `days` of score history and characterize movement.

    Output:
        {
            "n_snapshots": int,
            "score_first": float | None,
            "score_last":  float | None,
            "delta": float | None,
            "max_jump": float | None,    # largest single-day move
            "abs_mean_jump": float | None,
            "frozen": bool,              # n≥5 and ALL daily moves < frozen_max_delta
            "lookback_days": int,
        }

    The 'frozen' verdict is what answers "FORTE 1 aydır donmuş":
    enough snapshots, but no daily change ≥ 1 point — a strong signal
    that something upstream isn't updating.
    """
    sym = _norm(ticker)
    out: dict[str, Any] = {
        "n_snapshots": 0,
        "score_first": None,
        "score_last": None,
        "delta": None,
        "max_jump": None,
        "abs_mean_jump": None,
        "frozen": False,
        "lookback_days": days,
    }
    if not sym:
        return out
    try:
        from infra.storage import _get_conn
        c = _get_conn()
        rows = c.execute(
            "SELECT score FROM score_history "
            "WHERE symbol = ? AND scoring_version = 'v13_handpicked' "
            "ORDER BY snap_date ASC LIMIT ?",
            (sym, int(days)),
        ).fetchall()
    except Exception as exc:
        log.debug("score_velocity %s: %r", sym, exc)
        return out
    scores = [r[0] for r in rows if r[0] is not None]
    out["n_snapshots"] = len(scores)
    if len(scores) < 2:
        return out
    out["score_first"] = round(float(scores[0]), 2)
    out["score_last"] = round(float(scores[-1]), 2)
    out["delta"] = round(out["score_last"] - out["score_first"], 2)
    jumps = [abs(scores[i] - scores[i - 1]) for i in range(1, len(scores))]
    out["max_jump"] = round(max(jumps), 2)
    out["abs_mean_jump"] = round(sum(jumps) / len(jumps), 2)
    # Frozen verdict: need at least 5 snapshots, AND every single-day
    # jump must be below the threshold. Both conditions together rule
    # out "small dataset" false positives.
    if len(scores) >= 5 and out["max_jump"] < frozen_max_delta:
        out["frozen"] = True
    return out


def compute_summary(
    tickers: list[str],
) -> dict[str, Any]:
    """Cheap batch view for the Radar table — just the per-ticker fields
    the row needs (age_hours, age_status, latest_quarter, kap_age_days)."""
    items: list[dict[str, Any]] = []
    n_fresh = n_old = n_stale = n_unknown = 0
    for t in tickers:
        f = compute_data_freshness(t)
        st = f.get("age_status", "unknown")
        if st == "fresh":   n_fresh += 1
        elif st == "old":   n_old += 1
        elif st == "stale": n_stale += 1
        else:               n_unknown += 1
        items.append({
            "ticker": f["ticker"],
            "age_hours": f["borsapy"]["age_hours"],
            "age_status": st,
            "latest_quarter": f["borsapy"]["latest_quarter"],
            "quarterly_available": f["borsapy"]["quarterly_available"],
            "kap_age_days": f["kap"].get("age_days"),
            "kap_rule_type": f["kap"].get("rule_type"),
            "gap_days": f["gap_days"],
            "warnings": f["warnings"],
        })
    return {
        "items": items,
        "summary": {
            "fresh": n_fresh,
            "old": n_old,
            "stale": n_stale,
            "unknown": n_unknown,
            "total": len(items),
        },
        "thresholds": {
            "fresh_hours": _AGE_FRESH_HRS,
            "stale_hours": _AGE_STALE_HRS,
        },
    }

"""Phase 4.9 user-facing endpoints for manual paper trading + A/B telemetry.

All endpoints are READ-ONLY, not authenticated (public API so users can
share CSV output), and rate-limited via core.rate_limiter.

Endpoints:
  GET /api/signals/today                    list today's active signals
  GET /api/signals/history                  CSV of past signals for ledger reconciliation
  GET /api/ensemble/weights                 current ensemble weight vector
  GET /api/paper_trading/template           seed-capital allocation CSV
  GET /api/scoring/ab_report                V13 vs calibrated comparison per symbol
  GET /ab_report                            server-rendered HTML dashboard

The endpoints read from:
  - reports/phase_4_ensemble.json          FAZ 4.5 output
  - reports/phase_4_weights.json           FAZ 4.2 output
  - score_history table                    telemetry snapshots with scoring_version
  - deep_events.csv (ground truth)         signal history

Phase 4.9 constraint (Rule 6 backward compat): these are NEW endpoints;
they don't touch existing endpoint behaviour.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

log = logging.getLogger("bistbull.phase4_endpoints")

router = APIRouter()


# ==========================================================================
# Config / shared paths (cwd-independent)
# ==========================================================================

_REPO_ROOT = Path(__file__).resolve().parent.parent
ENSEMBLE_JSON = _REPO_ROOT / "reports" / "phase_4_ensemble.json"
WEIGHTS_JSON = _REPO_ROOT / "reports" / "phase_4_weights.json"
WALKFORWARD_CSV = _REPO_ROOT / "reports" / "walkforward.csv"


# ==========================================================================
# Helpers
# ==========================================================================

def _load_ensemble_weights() -> Optional[dict]:
    """Load reports/phase_4_ensemble.json if available."""
    if not ENSEMBLE_JSON.exists():
        return None
    try:
        return json.loads(ENSEMBLE_JSON.read_text())
    except Exception as e:
        log.warning(f"failed to parse {ENSEMBLE_JSON}: {e}")
        return None


def _load_signal_weights() -> Optional[dict]:
    """Load reports/phase_4_weights.json if available (sector-conditional)."""
    if not WEIGHTS_JSON.exists():
        return None
    try:
        return json.loads(WEIGHTS_JSON.read_text())
    except Exception as e:
        log.warning(f"failed to parse {WEIGHTS_JSON}: {e}")
        return None


def _csv_response(rows: list[dict], columns: list[str],
                  filename: str) -> Response:
    """Return a CSV Response with the given rows/columns."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _compute_signals_for_universe(
    as_of: date, universe: list[str], min_rank_pct: float = 0.7,
) -> list[dict]:
    """For each (symbol, signal) in the universe, compute cs_rank_pct and
    return the set whose rank >= min_rank_pct (top 30% by default)."""
    from research.ranking import (
        signal_strength, cs_rank_pct, modulation_factor, STRENGTH_FUNCTIONS,
    )
    from research.sectors import get_sector

    weights = _load_signal_weights() or {}
    ensemble = _load_ensemble_weights() or {}
    ens_weights_by_signal: dict[str, float] = {}
    if ensemble and isinstance(ensemble.get("signals"), list):
        for sig, w in zip(ensemble["signals"], ensemble["weights"]):
            ens_weights_by_signal[sig] = w

    out: list[dict] = []
    for signal_name in STRENGTH_FUNCTIONS:
        # Compute strengths across universe; keep only those with >= min_rank_pct
        for sym in universe:
            try:
                rank = cs_rank_pct(sym, signal_name, as_of)
            except Exception:
                continue
            if rank is None or rank < min_rank_pct:
                continue
            try:
                strength = signal_strength(sym, signal_name, as_of)
            except Exception:
                strength = None
            sector = get_sector(sym) or "Unknown"
            # Sector-conditional calibrated weight (from Phase 4.2 output)
            sig_weights = weights.get(signal_name, {})
            sect_weights = sig_weights.get(sector) or sig_weights.get("_default") or {}
            w20 = sect_weights.get("weight_20d")
            w60 = sect_weights.get("weight_60d")
            # Apply modulation + ensemble weight
            mod_factor = modulation_factor(rank)
            ens_w = ens_weights_by_signal.get(signal_name, 0.0)
            modulated_weight = None
            if w20 is not None:
                modulated_weight = w20 * mod_factor * ens_w
            out.append({
                "symbol": sym,
                "signal": signal_name,
                "strength": round(strength, 4) if strength is not None else None,
                "cs_rank_pct": round(rank, 4),
                "weight_20d": round(w20, 4) if w20 is not None else None,
                "weight_60d": round(w60, 4) if w60 is not None else None,
                "ensemble_weight": round(ens_w, 4) if ens_w else 0.0,
                "modulation_factor": round(mod_factor, 4),
                "modulated_weight": round(modulated_weight, 4)
                                    if modulated_weight is not None else None,
                "sector": sector,
                "timestamp": as_of.isoformat(),
            })
    # Sort: highest modulated_weight first, then by rank
    out.sort(key=lambda r: (
        -(r.get("modulated_weight") or 0),
        -(r.get("cs_rank_pct") or 0),
    ))
    return out


# ==========================================================================
# GET /api/signals/today
# ==========================================================================

@router.get("/api/signals/today")
async def api_signals_today(
    request: Request,
    format: str = Query("json", pattern="^(json|csv)$"),
    min_rank_pct: float = Query(0.7, ge=0.0, le=1.0),
    universe: str = Query("BIST30"),
):
    """Return the top 30% (by default) active signals across the universe.

    format: 'json' (default) or 'csv' (Excel-openable)
    min_rank_pct: per-signal cross-sectional rank threshold (default 0.7)
    universe: membership filter (default BIST30)
    """
    from core.rate_limiter import check_rate_limit
    check_rate_limit(request, "signals_today")

    # Universe members: union of SECTOR_MAP symbols (Phase 4 standalone,
    # not dependent on yfinance data availability)
    from research.sectors import SECTOR_MAP
    members = sorted(SECTOR_MAP.keys())

    rows = _compute_signals_for_universe(
        as_of=date.today(), universe=members, min_rank_pct=min_rank_pct,
    )

    if format == "csv":
        return _csv_response(
            rows,
            columns=["symbol", "signal", "strength", "cs_rank_pct",
                     "weight_20d", "weight_60d", "ensemble_weight",
                     "modulation_factor", "modulated_weight",
                     "sector", "timestamp"],
            filename=f"signals_today_{date.today().isoformat()}.csv",
        )
    return JSONResponse({
        "as_of": date.today().isoformat(),
        "universe": universe,
        "min_rank_pct": min_rank_pct,
        "count": len(rows),
        "signals": rows,
    })


# ==========================================================================
# GET /api/signals/history
# ==========================================================================

@router.get("/api/signals/history")
async def api_signals_history(
    request: Request,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
    format: str = Query("csv", pattern="^(json|csv)$"),
):
    """Past signal events from score_history. Ledger reconciliation use.

    from/to: ISO dates (YYYY-MM-DD). Max 365 days.
    """
    from core.rate_limiter import check_rate_limit
    check_rate_limit(request, "signals_history")

    try:
        d_from = datetime.fromisoformat(from_date).date()
        d_to = datetime.fromisoformat(to_date).date()
    except ValueError:
        return JSONResponse(
            {"error": "Invalid date format; use YYYY-MM-DD"}, status_code=400,
        )
    if d_to < d_from:
        return JSONResponse(
            {"error": "to_date must be >= from_date"}, status_code=400,
        )
    if (d_to - d_from).days > 365:
        return JSONResponse(
            {"error": "Range > 365 days not supported"}, status_code=400,
        )

    from infra.storage import _get_conn
    conn = _get_conn()
    rows = conn.execute(
        "SELECT symbol, snap_date, score, momentum, risk, fa_score, "
        "decision, scoring_version "
        "FROM score_history "
        "WHERE snap_date BETWEEN ? AND ? "
        "ORDER BY snap_date DESC, symbol ASC",
        (d_from.isoformat(), d_to.isoformat()),
    ).fetchall()

    data = [
        {
            "symbol": r[0], "snap_date": r[1],
            "score": r[2], "momentum": r[3], "risk": r[4],
            "fa_score": r[5], "decision": r[6],
            "scoring_version": r[7],
        }
        for r in rows
    ]

    if format == "csv":
        return _csv_response(
            data,
            columns=["snap_date", "symbol", "score", "momentum", "risk",
                     "fa_score", "decision", "scoring_version"],
            filename=f"signals_history_{d_from.isoformat()}_to_{d_to.isoformat()}.csv",
        )
    return JSONResponse({"count": len(data), "rows": data})


# ==========================================================================
# GET /api/ensemble/weights
# ==========================================================================

@router.get("/api/ensemble/weights")
async def api_ensemble_weights(request: Request):
    """Current ensemble weight vector + correlation matrix + hold-out verdict."""
    from core.rate_limiter import check_rate_limit
    check_rate_limit(request, "ensemble_weights")

    data = _load_ensemble_weights()
    if data is None:
        return JSONResponse(
            {"error": "Ensemble weights not available. Run Phase 4.5 first."},
            status_code=503,
        )
    # Pretty projection: zip signals + weights into a sorted list for the user
    signals = data.get("signals", [])
    weights = data.get("weights", [])
    mu = data.get("mu", [])
    caps = data.get("caps_applied", {}) or {}
    rows = sorted(
        [{"signal": s, "weight": w, "mu": m,
          "cap_applied": caps.get(s)}
         for s, w, m in zip(signals, weights, mu)],
        key=lambda r: -r["weight"],
    )
    return JSONResponse({
        "weights": rows,
        "expected_sharpe": data.get("expected_sharpe"),
        "ensemble_vol": data.get("ensemble_vol"),
        "excluded_signals": data.get("excluded_signals", []),
        "holdout_evaluation": data.get("holdout_evaluation"),
    })


# ==========================================================================
# GET /api/paper_trading/template
# ==========================================================================

@router.get("/api/paper_trading/template")
async def api_paper_trading_template(
    request: Request,
    seed_capital: float = Query(100000.0, gt=0),
    format: str = Query("csv", pattern="^(json|csv)$"),
    top_n_per_signal: int = Query(3, ge=1, le=10),
):
    """Suggested initial portfolio allocation for paper trading.

    seed_capital: starting cash (default 100,000 TL)
    top_n_per_signal: how many top-ranked stocks per signal to split the
                     signal's ensemble-weight bucket across (default 3)
    """
    from core.rate_limiter import check_rate_limit
    check_rate_limit(request, "paper_trading_template")

    ensemble = _load_ensemble_weights()
    if ensemble is None:
        return JSONResponse(
            {"error": "Ensemble not available. Run Phase 4.5 first."},
            status_code=503,
        )

    # Build weights dict {signal: ensemble_weight}
    ens_w: dict[str, float] = {}
    for sig, w in zip(ensemble.get("signals", []), ensemble.get("weights", [])):
        if w > 0:  # skip zero-weighted signals
            ens_w[sig] = w

    # For each signal, find today's top-ranked symbols via cs_rank_pct.
    from research.ranking import cs_rank_pct, signal_strength
    from research.sectors import SECTOR_MAP, get_sector

    members = sorted(SECTOR_MAP.keys())
    allocations: list[dict] = []
    unallocated_cash = seed_capital
    today = date.today()

    for signal_name, ens_weight in ens_w.items():
        # Find strengths across universe for this signal
        ranked: list[tuple[str, float, float]] = []  # (sym, rank, strength)
        for sym in members:
            try:
                rank = cs_rank_pct(sym, signal_name, today)
            except Exception:
                continue
            if rank is None or rank < 0.7:  # top 30% only
                continue
            strength = signal_strength(sym, signal_name, today)
            ranked.append((sym, rank, strength or 0.0))
        # Keep top_n by rank
        ranked.sort(key=lambda x: -x[1])
        top = ranked[:top_n_per_signal]
        if not top:
            continue
        signal_bucket = seed_capital * ens_weight
        # Equal split among top N (simplicity; strength-weighted is phase 5)
        per_symbol = signal_bucket / len(top)
        for sym, rank, strength in top:
            allocations.append({
                "signal": signal_name,
                "symbol": sym,
                "sector": get_sector(sym) or "Unknown",
                "cs_rank_pct": round(rank, 4),
                "strength": round(strength, 4),
                "ensemble_weight_pct": round(ens_weight * 100, 2),
                "signal_bucket_tl": round(signal_bucket, 2),
                "allocation_tl": round(per_symbol, 2),
                "allocation_pct": round(per_symbol / seed_capital * 100, 2),
            })
            unallocated_cash -= per_symbol

    # Add a "cash" row to make the CSV sum to seed_capital
    allocations.append({
        "signal": "(cash)",
        "symbol": "—",
        "sector": "—",
        "cs_rank_pct": None,
        "strength": None,
        "ensemble_weight_pct": None,
        "signal_bucket_tl": None,
        "allocation_tl": round(max(unallocated_cash, 0.0), 2),
        "allocation_pct": round(max(unallocated_cash, 0.0) / seed_capital * 100, 2),
    })

    if format == "csv":
        return _csv_response(
            allocations,
            columns=["signal", "symbol", "sector", "cs_rank_pct", "strength",
                     "ensemble_weight_pct", "signal_bucket_tl",
                     "allocation_tl", "allocation_pct"],
            filename=f"paper_trading_template_{today.isoformat()}_{int(seed_capital)}.csv",
        )
    return JSONResponse({
        "seed_capital": seed_capital,
        "as_of": today.isoformat(),
        "allocations": allocations,
        "unallocated_cash_tl": round(max(unallocated_cash, 0.0), 2),
    })


# ==========================================================================
# GET /api/scoring/ab_report
# ==========================================================================

@router.get("/api/scoring/ab_report")
async def api_scoring_ab_report(
    request: Request,
    symbol: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """V13 vs calibrated A/B comparison from score_history telemetry.

    symbol: if None, aggregate across all symbols with both-version rows.
    days: lookback window (default 30).
    """
    from core.rate_limiter import check_rate_limit
    check_rate_limit(request, "ab_report")

    from infra.storage import _get_conn
    conn = _get_conn()
    d_from = (date.today() - timedelta(days=days)).isoformat()

    params: tuple
    sql = (
        "SELECT h.snap_date, h.symbol, "
        "       h.score AS v13_score, c.score AS cal_score, "
        "       h.decision AS v13_dec, c.decision AS cal_dec "
        "FROM score_history h "
        "JOIN score_history c "
        "  ON h.symbol = c.symbol AND h.snap_date = c.snap_date "
        " AND h.scoring_version = 'v13_handpicked' "
        " AND c.scoring_version = 'calibrated_2026Q1' "
        "WHERE h.snap_date >= ?"
    )
    params = (d_from,)
    if symbol:
        sql += " AND h.symbol = ?"
        params = (d_from, symbol.upper())
    sql += " ORDER BY h.snap_date DESC, h.symbol ASC"

    rows = conn.execute(sql, params).fetchall()

    data: list[dict] = []
    decision_flips = 0
    score_diffs: list[float] = []
    for r in rows:
        v13, cal = r[2], r[3]
        v13_dec, cal_dec = r[4], r[5]
        diff = None
        if v13 is not None and cal is not None:
            diff = cal - v13
            score_diffs.append(diff)
        if v13_dec and cal_dec and v13_dec != cal_dec:
            decision_flips += 1
        data.append({
            "snap_date": r[0], "symbol": r[1],
            "v13_score": v13, "cal_score": cal,
            "diff": round(diff, 4) if diff is not None else None,
            "v13_decision": v13_dec, "cal_decision": cal_dec,
            "decision_match": v13_dec == cal_dec,
        })

    # Aggregate stats
    n = len(data)
    mean_diff = sum(score_diffs) / len(score_diffs) if score_diffs else None
    # Rank correlation (Spearman): rank both series, compute Pearson on ranks.
    rho = None
    if len(score_diffs) >= 3:
        pairs = [(r["v13_score"], r["cal_score"]) for r in data
                 if r["v13_score"] is not None and r["cal_score"] is not None]
        if len(pairs) >= 3:
            rho = _spearman([p[0] for p in pairs], [p[1] for p in pairs])

    decision_match_rate = (
        sum(1 for r in data if r["decision_match"]) / n if n > 0 else None
    )

    payload = {
        "_meta": {
            "n_paired_rows": n,
            "lookback_days": days,
            "symbol_filter": symbol,
            "version_match_rate": round(decision_match_rate, 4)
                                   if decision_match_rate is not None else None,
            "decision_flip_count": decision_flips,
            "mean_score_diff": round(mean_diff, 4) if mean_diff is not None else None,
            "spearman_rho": round(rho, 4) if rho is not None else None,
        },
        "rows": data,
    }

    if format == "csv":
        return _csv_response(
            data,
            columns=["snap_date", "symbol", "v13_score", "cal_score", "diff",
                     "v13_decision", "cal_decision", "decision_match"],
            filename=f"ab_report_{d_from}_to_{date.today().isoformat()}.csv",
        )
    return JSONResponse(payload)


def _spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    """Spearman rank correlation. Returns None if insufficient variation."""
    n = len(xs)
    if n < 3:
        return None

    def _ranks(vals: list[float]) -> list[float]:
        # Fractional ranks with tie averaging
        indexed = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(indexed):
            j = i
            while j + 1 < len(indexed) and vals[indexed[j + 1]] == vals[indexed[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1  # 1-indexed midpoint
            for k in range(i, j + 1):
                ranks[indexed[k]] = avg_rank
            i = j + 1
        return ranks

    rx = _ranks(xs); ry = _ranks(ys)
    mx = sum(rx) / n; my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((r - mx) ** 2 for r in rx)
    dy = sum((r - my) ** 2 for r in ry)
    denom = (dx * dy) ** 0.5
    if denom == 0:
        return None
    return num / denom


# ==========================================================================
# GET /ab_report  (server-rendered HTML, no JS framework)
# ==========================================================================

@router.get("/ab_report", response_class=HTMLResponse)
async def ab_report_page(days: int = Query(30, ge=1, le=365)):
    """Read-only HTML dashboard summarizing A/B scoring telemetry."""
    from infra.storage import _get_conn
    conn = _get_conn()
    d_from = (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute(
        "SELECT h.snap_date, h.symbol, h.score, c.score, h.decision, c.decision "
        "FROM score_history h "
        "JOIN score_history c "
        "  ON h.symbol = c.symbol AND h.snap_date = c.snap_date "
        " AND h.scoring_version = 'v13_handpicked' "
        " AND c.scoring_version = 'calibrated_2026Q1' "
        "WHERE h.snap_date >= ? "
        "ORDER BY h.snap_date DESC, h.symbol ASC "
        "LIMIT 500",
        (d_from,),
    ).fetchall()

    # Summary stats
    n = len(rows)
    diffs = [r[3] - r[2] for r in rows if r[2] is not None and r[3] is not None]
    mean_diff = sum(diffs) / len(diffs) if diffs else None
    flips = sum(1 for r in rows if r[4] and r[5] and r[4] != r[5])

    body_rows = "".join(
        f"<tr><td>{r[0]}</td><td><strong>{r[1]}</strong></td>"
        f"<td>{r[2] if r[2] is not None else '—'}</td>"
        f"<td>{r[3] if r[3] is not None else '—'}</td>"
        f"<td>{round(r[3]-r[2],2) if (r[2] is not None and r[3] is not None) else '—'}</td>"
        f"<td>{r[4] or '—'}</td><td>{r[5] or '—'}</td></tr>"
        for r in rows[:100]
    ) or '<tr><td colspan="7" style="text-align:center;opacity:0.7">No A/B rows in this window. '\
         'Either calibrated scoring is not yet enabled (SCORING_VERSION_DEFAULT=v13_handpicked) '\
         'or the calibrated fits haven\'t been uploaded yet (reports/fa_isotonic_fits.json).</td></tr>'

    html = f"""<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8"/>
<title>BistBull — A/B Scoring Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif;
         max-width: 1100px; margin: 20px auto; padding: 0 20px; color: #222; }}
  h1 {{ border-bottom: 2px solid #333; padding-bottom: 8px; }}
  .summary {{ background: #f7f7f7; border-left: 4px solid #2a7;
              padding: 12px 16px; margin: 20px 0; }}
  .summary b {{ color: #2a7; font-size: 1.1em; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px;
           font-size: 14px; }}
  th, td {{ padding: 6px 10px; border-bottom: 1px solid #eee; text-align: right; }}
  th {{ background: #fafafa; text-align: left; }}
  td:nth-child(2) {{ text-align: left; }}
  td:nth-child(6), td:nth-child(7) {{ text-align: left; }}
  .meta {{ color: #666; font-size: 12px; margin-top: 30px; }}
  .downloads a {{ display: inline-block; margin-right: 12px;
                  padding: 6px 12px; background: #2a7; color: white;
                  text-decoration: none; border-radius: 4px; }}
</style>
</head>
<body>
<h1>A/B Scoring Report — Son {days} gün</h1>
<div class="summary">
  <b>{n}</b> eşleşmiş satır · Ortalama skor farkı (calibrated − V13):
  <b>{round(mean_diff, 2) if mean_diff is not None else "—"}</b> ·
  Decision flip: <b>{flips}</b>
</div>

<div class="downloads">
  <a href="/api/scoring/ab_report?days={days}&format=csv">📊 CSV indir</a>
  <a href="/api/scoring/ab_report?days={days}&format=json">🗒 JSON</a>
  <a href="/api/ensemble/weights">⚖ Ensemble weights</a>
</div>

<table>
<thead><tr>
<th>Tarih</th><th>Sembol</th><th>V13</th><th>Cal</th>
<th>Δ</th><th>V13 karar</th><th>Cal karar</th>
</tr></thead>
<tbody>
{body_rows}
</tbody>
</table>

<p class="meta">
  İlk 100 satır gösteriliyor. Tam veri için CSV indirin. Veri
  <code>score_history</code> tablosundan, iki scoring_version'un aynı
  gün, aynı sembol için yazıldığı durumlar. Calibrated scoring'in etkin
  olması için <code>SCORING_VERSION_DEFAULT=calibrated_2026Q1</code> env
  var'ı set edilmeli <em>veya</em> her istek <code>?scoring_version=calibrated_2026Q1</code>
  query param'ıyla çağrılmalı.
</p>
</body>
</html>"""
    return HTMLResponse(html)


# ==========================================================================
# Phase 4.8 — GET /api/scoring/ab_report_breakdown
# Sector + symbol breakdown of paired telemetry
# ==========================================================================

@router.get("/api/scoring/ab_report_breakdown")
async def api_scoring_ab_report_breakdown(
    request: Request,
    days: int = Query(30, ge=1, le=365),
):
    """Phase 4.8: deep breakdown of A/B paired telemetry.

    Returns per-sector + per-symbol aggregations beyond the base
    /api/scoring/ab_report. Useful for identifying which segments
    drive divergence between V13 and calibrated_2026Q1.

    Read-only, rate-limited.
    """
    from core.rate_limiter import check_rate_limit
    check_rate_limit(request, "ab_report")

    from infra.storage import _get_conn
    from collections import defaultdict
    import statistics

    conn = _get_conn()
    d_from = (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute("""
        SELECT
            h.snap_date, h.symbol,
            h.score AS v13_score, c.score AS cal_score,
            h.decision AS v13_dec, c.decision AS cal_dec,
            h.fa_score AS v13_fa, c.fa_score AS cal_fa
        FROM score_history h
        JOIN score_history c
          ON h.symbol = c.symbol
         AND h.snap_date = c.snap_date
         AND h.scoring_version = 'v13_handpicked'
         AND c.scoring_version = 'calibrated_2026Q1'
        WHERE h.snap_date >= ?
        ORDER BY h.snap_date DESC, h.symbol ASC
    """, (d_from,)).fetchall()

    if not rows:
        return JSONResponse({
            "_meta": {"n_paired_rows": 0, "lookback_days": days},
            "by_sector": {},
            "by_symbol": [],
            "decision_quadrant": {},
        })

    # Sector lookup (best-effort, current mapping)
    from engine.scoring import map_sector
    try:
        from data.providers import compute_metrics_v9
    except Exception:
        compute_metrics_v9 = None

    sym_to_sector: dict[str, str] = {}
    for r in rows:
        sym = r[1]
        if sym in sym_to_sector:
            continue
        sector_str = ""
        if compute_metrics_v9 is not None:
            try:
                m = compute_metrics_v9(sym)
                sector_str = (m.get("sector") or m.get("sector_group") or "")
            except Exception:
                sector_str = ""
        sym_to_sector[sym] = map_sector(sector_str) if sector_str else "sanayi"

    # Aggregate
    by_sector: dict[str, dict] = defaultdict(lambda: {
        "n_rows": 0, "symbols": set(),
        "diffs": [], "matches": 0, "dec_total": 0,
    })
    by_symbol: dict[str, dict] = defaultdict(lambda: {
        "n_rows": 0, "diffs": [], "flips": 0,
        "latest_v13": None, "latest_cal": None,
    })
    quadrant: dict[tuple, int] = defaultdict(int)

    for r in rows:
        snap, sym = r[0], r[1]
        v13s, cals = r[2], r[3]
        v13d, cald = r[4], r[5]
        sg = sym_to_sector.get(sym, "sanayi")

        if v13s is not None and cals is not None:
            d = cals - v13s
            by_sector[sg]["diffs"].append(d)
            by_symbol[sym]["diffs"].append(d)
        by_sector[sg]["n_rows"] += 1
        by_sector[sg]["symbols"].add(sym)
        by_symbol[sym]["n_rows"] += 1

        if v13d and cald:
            quadrant[(v13d, cald)] += 1
            by_sector[sg]["dec_total"] += 1
            if v13d == cald:
                by_sector[sg]["matches"] += 1
            else:
                by_symbol[sym]["flips"] += 1

        # Latest snapshot per symbol (rows ORDER BY snap_date DESC)
        if by_symbol[sym]["latest_v13"] is None:
            by_symbol[sym]["latest_v13"] = v13s
            by_symbol[sym]["latest_cal"] = cals

    # Format output
    sector_out = {}
    for sg, s in by_sector.items():
        sector_out[sg] = {
            "n_rows": s["n_rows"],
            "n_symbols": len(s["symbols"]),
            "mean_diff": round(statistics.mean(s["diffs"]), 4)
                          if s["diffs"] else None,
            "max_abs_diff": round(max(abs(d) for d in s["diffs"]), 4)
                             if s["diffs"] else None,
            "decision_match_rate": round(s["matches"] / s["dec_total"], 4)
                                    if s["dec_total"] else None,
        }

    symbol_out = []
    for sym, s in by_symbol.items():
        if not s["diffs"]:
            continue
        symbol_out.append({
            "symbol": sym,
            "sector": sym_to_sector.get(sym, "sanayi"),
            "n_rows": s["n_rows"],
            "mean_diff": round(statistics.mean(s["diffs"]), 4),
            "max_abs_diff": round(max(abs(d) for d in s["diffs"]), 4),
            "decision_flips": s["flips"],
            "latest_v13": s["latest_v13"],
            "latest_cal": s["latest_cal"],
        })
    # Sort by max_abs_diff desc
    symbol_out.sort(key=lambda r: r["max_abs_diff"], reverse=True)

    return JSONResponse({
        "_meta": {
            "n_paired_rows": len(rows),
            "lookback_days": days,
            "n_symbols": len(by_symbol),
            "n_sectors": len(by_sector),
        },
        "by_sector": sector_out,
        "by_symbol": symbol_out,
        "decision_quadrant": {f"{a}->{b}": c for (a, b), c in quadrant.items()},
    })

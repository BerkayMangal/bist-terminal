#!/usr/bin/env python3
"""Phase 4.8 — A/B telemetry analysis.

Reads paired V13 vs calibrated_2026Q1 snapshots from score_history,
produces a deep analysis report covering:

  - Spearman correlation (overall + per-sector)
  - Decision flip rate (AL/SAT/IZLE quadrant breakdown)
  - Score diff distribution (mean, median, p10, p90, max-abs)
  - Sector × time heatmap (mean diff per sector per week)
  - Symbol-ranked diff CSV (most-divergent and most-aligned)
  - Buckets where calibrated and V13 disagree the most

USAGE:
    python scripts/analyze_ab_telemetry.py \\
        --db=/path/to/bistbull.db \\
        --days=30 \\
        --out-md=reports/ab_telemetry_analysis_<date>.md \\
        --out-csv=reports/ab_telemetry_<date>.csv

The script is idempotent and read-only on the DB.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("bistbull.ab_analyze")


# ==========================================================================
# Statistics helpers (no scipy/numpy dependency — pure stdlib)
# ==========================================================================

def spearman_rho(xs: list[float], ys: list[float]) -> Optional[float]:
    """Spearman rank correlation. Returns None if n<3 or zero variance."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    rx = _ranks(xs)
    ry = _ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n))
    dy = sum((ry[i] - my) ** 2 for i in range(n))
    denom = math.sqrt(dx * dy)
    if denom == 0:
        return None
    return num / denom


def _ranks(values: list[float]) -> list[float]:
    """Assign average ranks (handles ties)."""
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based ranks
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg
        i = j + 1
    return ranks


def percentile(values: list[float], p: float) -> Optional[float]:
    """Linear-interpolation percentile (p in [0, 100])."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


# ==========================================================================
# DB access
# ==========================================================================

@dataclass
class PairedRow:
    snap_date: str
    symbol: str
    v13_score: Optional[float]
    cal_score: Optional[float]
    v13_decision: Optional[str]
    cal_decision: Optional[str]
    v13_fa: Optional[float]
    cal_fa: Optional[float]

    @property
    def diff(self) -> Optional[float]:
        if self.v13_score is None or self.cal_score is None:
            return None
        return self.cal_score - self.v13_score

    @property
    def fa_diff(self) -> Optional[float]:
        if self.v13_fa is None or self.cal_fa is None:
            return None
        return self.cal_fa - self.v13_fa

    @property
    def decision_match(self) -> Optional[bool]:
        if not self.v13_decision or not self.cal_decision:
            return None
        return self.v13_decision == self.cal_decision


def fetch_paired_snapshots(
    db_path: str,
    lookback_days: int,
) -> list[PairedRow]:
    """Read paired (v13, calibrated) rows from score_history.

    Joins score_history to itself on (symbol, snap_date), filtered to
    rows where one is v13_handpicked and the other is calibrated_2026Q1.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    d_from = (date.today() - timedelta(days=lookback_days)).isoformat()

    rows = conn.execute("""
        SELECT
            h.snap_date AS snap_date,
            h.symbol AS symbol,
            h.score AS v13_score,
            c.score AS cal_score,
            h.decision AS v13_dec,
            c.decision AS cal_dec,
            h.fa_score AS v13_fa,
            c.fa_score AS cal_fa
        FROM score_history h
        JOIN score_history c
          ON h.symbol = c.symbol
         AND h.snap_date = c.snap_date
         AND h.scoring_version = 'v13_handpicked'
         AND c.scoring_version = 'calibrated_2026Q1'
        WHERE h.snap_date >= ?
        ORDER BY h.snap_date DESC, h.symbol ASC
    """, (d_from,)).fetchall()
    conn.close()

    out = []
    for r in rows:
        out.append(PairedRow(
            snap_date=r["snap_date"],
            symbol=r["symbol"],
            v13_score=r["v13_score"],
            cal_score=r["cal_score"],
            v13_decision=r["v13_dec"],
            cal_decision=r["cal_dec"],
            v13_fa=r["v13_fa"],
            cal_fa=r["cal_fa"],
        ))
    return out


# ==========================================================================
# Sector mapping (best-effort, current mapping applied to historic snapshots)
# ==========================================================================

def build_sector_map(symbols: list[str]) -> dict[str, str]:
    """Best-effort symbol → sector_group mapping.

    Uses engine.scoring.map_sector with a yfinance-style sector string
    pulled from data.providers cache. If the sector cannot be resolved,
    falls back to 'sanayi' (default).

    NOTE: This applies the CURRENT sector to all historic snapshots.
    BIST companies rarely change classification, so this is a reasonable
    approximation. Documented as a known limitation.
    """
    from engine.scoring import map_sector

    out: dict[str, str] = {}
    # Try to use cached metric data which often contains sector info
    try:
        from data.providers import compute_metrics_v9
    except Exception:
        compute_metrics_v9 = None

    for sym in symbols:
        sector_str = ""
        if compute_metrics_v9 is not None:
            try:
                m = compute_metrics_v9(sym)
                sector_str = (m.get("sector") or m.get("sector_group") or "")
            except Exception:
                sector_str = ""
        out[sym] = map_sector(sector_str) if sector_str else "sanayi"
    return out


# ==========================================================================
# Analysis
# ==========================================================================

def analyze(rows: list[PairedRow], sector_map: dict[str, str]) -> dict[str, Any]:
    """Top-level analysis: aggregates + breakdowns."""
    n = len(rows)
    if n == 0:
        return {
            "n_paired_rows": 0,
            "warning": "No paired rows found in lookback window. "
                       "Either A/B dual-write is not running or window too short.",
        }

    # Score diff distribution (overall)
    diffs = [r.diff for r in rows if r.diff is not None]
    fa_diffs = [r.fa_diff for r in rows if r.fa_diff is not None]
    v13_scores = [r.v13_score for r in rows if r.v13_score is not None
                                              and r.cal_score is not None]
    cal_scores = [r.cal_score for r in rows if r.v13_score is not None
                                              and r.cal_score is not None]

    rho_overall = spearman_rho(v13_scores, cal_scores) if len(v13_scores) >= 3 else None
    rho_fa = None
    if len(fa_diffs) >= 3:
        v13_fas = [r.v13_fa for r in rows if r.v13_fa is not None and r.cal_fa is not None]
        cal_fas = [r.cal_fa for r in rows if r.v13_fa is not None and r.cal_fa is not None]
        rho_fa = spearman_rho(v13_fas, cal_fas)

    # Decision quadrant: V13 decision × calibrated decision
    quadrant: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        if r.v13_decision and r.cal_decision:
            quadrant[(r.v13_decision, r.cal_decision)] += 1
    decision_match = sum(c for (a, b), c in quadrant.items() if a == b)
    decision_total = sum(quadrant.values())
    decision_match_rate = decision_match / decision_total if decision_total else None

    # Per-sector stats
    by_sector: dict[str, dict] = {}
    sector_groups: dict[str, list[PairedRow]] = defaultdict(list)
    for r in rows:
        sector_groups[sector_map.get(r.symbol, "sanayi")].append(r)
    for sg, sg_rows in sector_groups.items():
        sg_diffs = [r.diff for r in sg_rows if r.diff is not None]
        sg_v13 = [r.v13_score for r in sg_rows if r.diff is not None]
        sg_cal = [r.cal_score for r in sg_rows if r.diff is not None]
        sg_match = sum(1 for r in sg_rows
                       if r.v13_decision and r.cal_decision
                       and r.v13_decision == r.cal_decision)
        sg_dec_total = sum(1 for r in sg_rows
                           if r.v13_decision and r.cal_decision)
        by_sector[sg] = {
            "n_rows": len(sg_rows),
            "n_symbols": len({r.symbol for r in sg_rows}),
            "spearman_rho": round(spearman_rho(sg_v13, sg_cal), 4)
                            if len(sg_v13) >= 3
                            and spearman_rho(sg_v13, sg_cal) is not None
                            else None,
            "mean_diff": round(statistics.mean(sg_diffs), 4) if sg_diffs else None,
            "median_diff": round(statistics.median(sg_diffs), 4) if sg_diffs else None,
            "max_abs_diff": round(max(abs(d) for d in sg_diffs), 4) if sg_diffs else None,
            "decision_match_rate": round(sg_match / sg_dec_total, 4)
                                    if sg_dec_total else None,
        }

    # Per-symbol ranking (most divergent + most aligned)
    by_symbol: dict[str, dict] = {}
    sym_groups: dict[str, list[PairedRow]] = defaultdict(list)
    for r in rows:
        sym_groups[r.symbol].append(r)
    for sym, sym_rows in sym_groups.items():
        sym_diffs = [r.diff for r in sym_rows if r.diff is not None]
        if not sym_diffs:
            continue
        flips = sum(1 for r in sym_rows
                    if r.v13_decision and r.cal_decision
                    and r.v13_decision != r.cal_decision)
        by_symbol[sym] = {
            "n_rows": len(sym_rows),
            "sector": sector_map.get(sym, "sanayi"),
            "mean_diff": round(statistics.mean(sym_diffs), 4),
            "max_abs_diff": round(max(abs(d) for d in sym_diffs), 4),
            "decision_flips": flips,
            "latest_v13": sym_rows[0].v13_score,
            "latest_cal": sym_rows[0].cal_score,
            "latest_diff": sym_rows[0].diff,
        }

    return {
        "n_paired_rows": n,
        "n_symbols": len(sym_groups),
        "n_sectors": len(by_sector),
        "overall": {
            "spearman_rho_overall": round(rho_overall, 4) if rho_overall is not None else None,
            "spearman_rho_fa_only": round(rho_fa, 4) if rho_fa is not None else None,
            "score_diff_mean": round(statistics.mean(diffs), 4) if diffs else None,
            "score_diff_median": round(statistics.median(diffs), 4) if diffs else None,
            "score_diff_p10": round(percentile(diffs, 10), 4) if diffs else None,
            "score_diff_p90": round(percentile(diffs, 90), 4) if diffs else None,
            "score_diff_max_abs": round(max(abs(d) for d in diffs), 4) if diffs else None,
            "decision_match_rate": round(decision_match_rate, 4)
                                    if decision_match_rate is not None
                                    else None,
            "decision_total": decision_total,
            "decision_flips": decision_total - decision_match,
        },
        "decision_quadrant": {f"{a}->{b}": c for (a, b), c in quadrant.items()},
        "by_sector": by_sector,
        "by_symbol": by_symbol,
    }


def interpret(analysis: dict[str, Any]) -> str:
    """Convert the analysis dict to a human-readable verdict.

    Returns one of:
      - "very_aligned"  — Spearman > 0.95, calibrated = V13 essentially
      - "moderate"       — 0.70-0.95, real differences in some segments
      - "divergent"      — < 0.70, calibrated produces meaningfully different scores
      - "insufficient_data" — fewer than 30 paired rows
    """
    n = analysis.get("n_paired_rows", 0)
    if n < 30:
        return "insufficient_data"

    rho = (analysis.get("overall") or {}).get("spearman_rho_overall")
    if rho is None:
        return "insufficient_data"

    if rho > 0.95:
        return "very_aligned"
    if rho > 0.70:
        return "moderate"
    return "divergent"


# ==========================================================================
# Markdown report
# ==========================================================================

def render_markdown(
    analysis: dict[str, Any],
    verdict: str,
    lookback_days: int,
    db_path: str,
) -> str:
    """Produce the human-readable analysis report."""
    lines: list[str] = []
    today = date.today().isoformat()
    lines.append(f"# Phase 4.8 — A/B Telemetry Analysis ({today})")
    lines.append("")
    lines.append(f"**Lookback:** {lookback_days} days from {today}")
    lines.append(f"**Database:** `{db_path}`")
    lines.append("")

    n = analysis.get("n_paired_rows", 0)
    if n == 0:
        lines.append("## ⚠️ No paired data")
        lines.append("")
        lines.append(analysis.get("warning", "No data."))
        lines.append("")
        lines.append("Possible causes:")
        lines.append("- A/B dual-write in `app.py:_record_score_snapshot` is not running")
        lines.append("- `reports/fa_isotonic_fits.json` is missing → calibrated rows skipped")
        lines.append("- Lookback window too short for telemetry maturation")
        return "\n".join(lines)

    lines.append(f"**Paired rows:** {n}")
    lines.append(f"**Distinct symbols:** {analysis['n_symbols']}")
    lines.append(f"**Sectors covered:** {analysis['n_sectors']}")
    lines.append("")

    # Verdict callout
    lines.append("## Verdict")
    lines.append("")
    verdict_msgs = {
        "very_aligned": (
            "🟢 **Very aligned** (Spearman > 0.95). Calibrated and V13 produce "
            "essentially the same rankings. Calibrated is a defensible choice "
            "but not a meaningful improvement on average. Phase 5 recalibration "
            "would need to address specific segments rather than overall fit."
        ),
        "moderate": (
            "🟡 **Moderate divergence** (Spearman 0.70-0.95). Calibrated produces "
            "meaningfully different scores in some segments. Look at the per-sector "
            "and per-symbol breakdowns below to identify where the divergence concentrates. "
            "Phase 5 recalibration with broader sample is justified."
        ),
        "divergent": (
            "🔴 **Strongly divergent** (Spearman < 0.70). Calibrated produces "
            "substantially different rankings from V13. This is either a real "
            "improvement (data-driven fits caught what handpicked weights missed) "
            "or a problem (5-symbol sample bias). The decision flip count and "
            "max-abs diff sections below help distinguish."
        ),
        "insufficient_data": (
            "⚪ **Insufficient data** (fewer than 30 paired rows or correlation "
            "undefined). Either A/B dual-write started recently or telemetry is "
            "not being collected. Check `app.py` background scanner first."
        ),
    }
    lines.append(verdict_msgs[verdict])
    lines.append("")

    # Overall stats
    o = analysis["overall"]
    lines.append("## Overall statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Spearman ρ (final score) | {o['spearman_rho_overall']} |")
    lines.append(f"| Spearman ρ (FA-only) | {o['spearman_rho_fa_only']} |")
    lines.append(f"| Mean score diff (cal − v13) | {o['score_diff_mean']} |")
    lines.append(f"| Median score diff | {o['score_diff_median']} |")
    lines.append(f"| 10th percentile | {o['score_diff_p10']} |")
    lines.append(f"| 90th percentile | {o['score_diff_p90']} |")
    lines.append(f"| Max abs diff | {o['score_diff_max_abs']} |")
    lines.append(f"| Decision match rate | {o['decision_match_rate']} |")
    lines.append(f"| Decision flips | {o['decision_flips']} / {o['decision_total']} |")
    lines.append("")

    # Decision quadrant
    lines.append("## Decision quadrant (V13 → calibrated)")
    lines.append("")
    decisions = ["AL", "İZLE", "İZLE_2", "SAT"]
    quad = analysis["decision_quadrant"]
    lines.append("| V13 \\ cal | " + " | ".join(decisions) + " |")
    lines.append("|" + "---|" * (len(decisions) + 1))
    for v13d in decisions:
        row = [v13d]
        for cald in decisions:
            row.append(str(quad.get(f"{v13d}->{cald}", 0)))
        lines.append("| " + " | ".join(row) + " |")
    other_quad = {k: v for k, v in quad.items()
                  if not any(k.startswith(f"{d}->") for d in decisions)
                  or not any(k.endswith(f"->{d}") for d in decisions)}
    if other_quad:
        lines.append("")
        lines.append("Other transitions: " +
                     ", ".join(f"{k}={v}" for k, v in other_quad.items()))
    lines.append("")

    # Per-sector
    lines.append("## By sector")
    lines.append("")
    sectors_sorted = sorted(
        analysis["by_sector"].items(),
        key=lambda kv: kv[1]["n_rows"],
        reverse=True,
    )
    lines.append("| Sector | Rows | Symbols | ρ | Mean diff | Max abs | Match rate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for sg, stats in sectors_sorted:
        lines.append(f"| {sg} | {stats['n_rows']} | {stats['n_symbols']} | "
                     f"{stats['spearman_rho']} | {stats['mean_diff']} | "
                     f"{stats['max_abs_diff']} | {stats['decision_match_rate']} |")
    lines.append("")

    # Most divergent / most aligned symbols
    by_sym = analysis["by_symbol"]
    if by_sym:
        sorted_by_div = sorted(by_sym.items(),
                               key=lambda kv: kv[1]["max_abs_diff"],
                               reverse=True)
        top_n = min(15, len(sorted_by_div))
        bot_n = min(10, len(sorted_by_div))

        lines.append(f"## Top {top_n} most divergent symbols")
        lines.append("")
        lines.append("| Symbol | Sector | Latest V13 | Latest cal | Latest diff | Mean diff | Max abs | Flips |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for sym, s in sorted_by_div[:top_n]:
            lines.append(f"| {sym} | {s['sector']} | {s['latest_v13']} | "
                         f"{s['latest_cal']} | {s['latest_diff']} | "
                         f"{s['mean_diff']} | {s['max_abs_diff']} | "
                         f"{s['decision_flips']} |")
        lines.append("")

        lines.append(f"## Top {bot_n} most aligned symbols")
        lines.append("")
        lines.append("| Symbol | Sector | Latest V13 | Latest cal | Mean diff | Max abs |")
        lines.append("|---|---|---:|---:|---:|---:|")
        for sym, s in sorted_by_div[-bot_n:][::-1]:
            lines.append(f"| {sym} | {s['sector']} | {s['latest_v13']} | "
                         f"{s['latest_cal']} | {s['mean_diff']} | "
                         f"{s['max_abs_diff']} |")
        lines.append("")

    # Recommendations
    lines.append("## Recommendations for next phases")
    lines.append("")
    rec_by_verdict = {
        "very_aligned": [
            "- Phase 4.8.1 hardening still valuable (defensive)",
            "- Phase 4.9 PIT shares may not yield big improvements — calibrated already matches V13",
            "- Phase 5 recalibration: focus on segments with non-trivial divergence rather than overall",
            "- Phase 6 banks: highest expected user-facing impact (bank rows produce no calibrated output today)",
        ],
        "moderate": [
            "- Look at `by_sector` table above: which sector(s) drive the divergence?",
            "- Phase 5 recalibration: directly justified — broader sample resolves the divergence",
            "- Phase 4.9 PIT shares: expected to help PE/PB-driven divergences",
            "- Phase 6 banks: independent from this divergence",
        ],
        "divergent": [
            "- Investigate before next phase: is calibrated correctly applying fits?",
            "- Run smoke_test_calibrated.py against production",
            "- Spot-check 3-5 most divergent symbols' raw FA inputs",
            "- If real: Phase 5 recalibration urgent (5-symbol bias suspected)",
            "- If artifact: file a regression note before Phase 4.9",
        ],
        "insufficient_data": [
            "- Verify A/B dual-write is running: app.py background scanner logs",
            "- Confirm reports/fa_isotonic_fits.json is committed and >5 metrics",
            "- Run smoke_test_calibrated.py against production for live confirmation",
            "- Re-run this analysis after telemetry has matured (≥30 paired rows)",
        ],
    }
    for line in rec_by_verdict[verdict]:
        lines.append(line)
    lines.append("")

    # Notes
    lines.append("## Notes & limitations")
    lines.append("")
    lines.append("- Sector mapping uses the **current** symbol→sector map applied "
                 "to all historic snapshots. BIST companies rarely change "
                 "classification, so this is a reasonable approximation but "
                 "should be acknowledged.")
    lines.append("- Decision flip counts include only rows where both versions "
                 "produced a non-null decision. NULL/unknown decisions are "
                 "excluded from match-rate denominators.")
    lines.append("- Spearman ρ is computed on score values (final overall + "
                 "FA-only). FA-only ρ tends to differ more from V13 than "
                 "overall ρ because K2 (technical), K3, K4 layers are "
                 "version-agnostic and dilute the FA signal in the overall "
                 "score.")
    lines.append("")

    return "\n".join(lines)


# ==========================================================================
# CSV output
# ==========================================================================

def write_symbol_csv(analysis: dict[str, Any], path: Path) -> None:
    """Write symbol-ranked CSV (sortable in Excel)."""
    by_sym = analysis.get("by_symbol", {})
    rows_sorted = sorted(
        by_sym.items(),
        key=lambda kv: kv[1].get("max_abs_diff", 0),
        reverse=True,
    )
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "symbol", "sector", "n_rows",
                    "latest_v13", "latest_cal", "latest_diff",
                    "mean_diff", "max_abs_diff", "decision_flips"])
        for i, (sym, s) in enumerate(rows_sorted, 1):
            w.writerow([i, sym, s["sector"], s["n_rows"],
                        s["latest_v13"], s["latest_cal"], s["latest_diff"],
                        s["mean_diff"], s["max_abs_diff"], s["decision_flips"]])


# ==========================================================================
# CLI
# ==========================================================================

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True,
                   help="SQLite path (e.g. /var/lib/bistbull/bistbull.db)")
    p.add_argument("--days", type=int, default=30, help="Lookback days (default 30)")
    p.add_argument("--out-md", required=True, help="Markdown report output path")
    p.add_argument("--out-csv", required=True, help="Symbol CSV output path")
    p.add_argument("--out-json", default=None,
                   help="Optional JSON dump of full analysis for downstream tooling")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path = args.db
    if not Path(db_path).exists():
        log.error("DB not found: %s", db_path)
        return 1

    log.info("Reading paired snapshots from %s, lookback=%dd", db_path, args.days)
    rows = fetch_paired_snapshots(db_path, args.days)
    log.info("Got %d paired rows", len(rows))

    if rows:
        symbols = sorted({r.symbol for r in rows})
        sector_map = build_sector_map(symbols)
    else:
        sector_map = {}

    analysis = analyze(rows, sector_map)
    verdict = interpret(analysis)
    log.info("Verdict: %s", verdict)

    md = render_markdown(analysis, verdict, args.days, db_path)
    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_md).write_text(md)
    log.info("Markdown report -> %s", args.out_md)

    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    write_symbol_csv(analysis, Path(args.out_csv))
    log.info("Symbol CSV -> %s", args.out_csv)

    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(analysis, indent=2, default=str))
        log.info("JSON analysis -> %s", args.out_json)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

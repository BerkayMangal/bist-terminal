"""PIT data coverage report (Phase 3 FAZ 3.0.5).

Given the PIT-loaded DB, report per-symbol × per-metric × per-quarter
fill rates. Target: >85% coverage. Metrics with <50% get flagged for
Phase 4 exclusion.

Emits:
  reports/phase_3_coverage.md   -- human-readable matrix + summary
  reports/phase_3_coverage.csv  -- machine-readable full matrix
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# Avoid circular; import storage access directly
from infra.storage import _get_conn

log = logging.getLogger("bistbull.research.coverage")


EXPECTED_METRICS = ("revenue", "net_income", "roe", "debt_to_equity")
CRITICAL_THRESHOLD = 0.85
EXCLUDE_THRESHOLD = 0.50


def _quarter_ends(start: date, end: date) -> list[date]:
    ends = []
    year = start.year
    while year <= end.year:
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q = date(year, m, d)
            if start <= q <= end:
                ends.append(q)
        year += 1
    return ends


@dataclass
class CoverageRow:
    symbol: str
    metric: str
    expected_quarters: int
    filled_quarters: int
    coverage: float

    @property
    def excluded_from_phase_4(self) -> bool:
        return self.coverage < EXCLUDE_THRESHOLD


def compute_coverage(
    symbols: list[str],
    from_date: date,
    to_date: date,
    metrics: tuple[str, ...] = EXPECTED_METRICS,
) -> list[CoverageRow]:
    """Return per-(symbol, metric) coverage rows."""
    q_ends = _quarter_ends(from_date, to_date)
    n_expected = len(q_ends)
    q_ends_iso = set(qe.isoformat() for qe in q_ends)

    conn = _get_conn()
    rows: list[CoverageRow] = []

    for symbol in symbols:
        sym_up = symbol.upper()
        for metric in metrics:
            # Count distinct period_ends in-range with non-null value
            got = conn.execute(
                """SELECT DISTINCT period_end FROM fundamentals_pit
                   WHERE symbol = ? AND metric = ? AND value IS NOT NULL
                     AND period_end BETWEEN ? AND ?""",
                (sym_up, metric, from_date.isoformat(), to_date.isoformat()),
            ).fetchall()
            filled_periods = {r[0] for r in got}
            filled = len(filled_periods & q_ends_iso)
            coverage = (filled / n_expected) if n_expected else 0.0
            rows.append(CoverageRow(
                symbol=sym_up, metric=metric,
                expected_quarters=n_expected,
                filled_quarters=filled,
                coverage=round(coverage, 4),
            ))
    return rows


def write_coverage_reports(
    rows: list[CoverageRow],
    from_date: date,
    to_date: date,
    out_dir: Path,
    data_source: str = "synthetic",
) -> tuple[Path, Path]:
    """Emit phase_3_coverage.md + phase_3_coverage.csv."""
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "phase_3_coverage.md"
    csv_path = out_dir / "phase_3_coverage.csv"

    # CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "metric", "expected_quarters",
                    "filled_quarters", "coverage", "excluded_from_phase_4"])
        for r in rows:
            w.writerow([r.symbol, r.metric, r.expected_quarters,
                        r.filled_quarters, f"{r.coverage:.4f}",
                        "yes" if r.excluded_from_phase_4 else "no"])

    # Aggregate
    symbols = sorted({r.symbol for r in rows})
    metrics = sorted({r.metric for r in rows})
    coverage_by_sym = {}
    coverage_by_metric = {}
    excluded = []
    for r in rows:
        coverage_by_sym.setdefault(r.symbol, []).append(r.coverage)
        coverage_by_metric.setdefault(r.metric, []).append(r.coverage)
        if r.excluded_from_phase_4:
            excluded.append((r.symbol, r.metric, r.coverage))

    def avg(xs): return sum(xs) / len(xs) if xs else 0.0

    # MD report
    header = f"""# Phase 3 Coverage Report

**Data source:** `{data_source}`
**Date range:** {from_date.isoformat()} → {to_date.isoformat()}
**Expected quarters per symbol:** {rows[0].expected_quarters if rows else 0}
**Symbols:** {len(symbols)}  ·  **Metrics:** {len(metrics)}

## Summary

**Overall coverage:** {avg([r.coverage for r in rows])*100:.2f}%
**Target:** {CRITICAL_THRESHOLD*100:.0f}%  ·  **Phase-4 exclude threshold:** <{EXCLUDE_THRESHOLD*100:.0f}%

"""
    # Per-metric averages
    metric_table = "\n### Coverage by metric\n\n| Metric | Avg coverage | Symbols excluded |\n|---|---|---|\n"
    for m in metrics:
        avg_c = avg(coverage_by_metric.get(m, []))
        n_excl = sum(1 for x in excluded if x[1] == m)
        metric_table += f"| {m} | {avg_c*100:.2f}% | {n_excl} |\n"

    # Per-symbol averages
    symbol_table = "\n### Coverage by symbol\n\n| Symbol | Avg coverage | Metrics excluded |\n|---|---|---|\n"
    for s in symbols:
        avg_c = avg(coverage_by_sym.get(s, []))
        n_excl = sum(1 for x in excluded if x[0] == s)
        symbol_table += f"| {s} | {avg_c*100:.2f}% | {n_excl} |\n"

    # Excluded list
    if excluded:
        excl_table = f"\n### Excluded (coverage <{EXCLUDE_THRESHOLD*100:.0f}%)\n\n| Symbol | Metric | Coverage |\n|---|---|---|\n"
        for s, m, c in sorted(excluded):
            excl_table += f"| {s} | {m} | {c*100:.2f}% |\n"
    else:
        excl_table = "\n### Excluded\n\n_None — all (symbol, metric) pairs pass the Phase 4 threshold._\n"

    # Full matrix (wide)
    matrix = "\n### Full matrix\n\n| Symbol | " + " | ".join(metrics) + " |\n"
    matrix += "|---|" + "---|" * len(metrics) + "\n"
    by_pair = {(r.symbol, r.metric): r.coverage for r in rows}
    for s in symbols:
        line = f"| {s} | "
        cells = []
        for m in metrics:
            c = by_pair.get((s, m), 0.0)
            if c < EXCLUDE_THRESHOLD:
                cells.append(f"⛔ {c*100:.1f}%")
            elif c < CRITICAL_THRESHOLD:
                cells.append(f"⚠ {c*100:.1f}%")
            else:
                cells.append(f"{c*100:.1f}%")
        line += " | ".join(cells) + " |\n"
        matrix += line

    md_content = header + metric_table + symbol_table + excl_table + matrix
    md_content += f"\n\n---\n\n_Phase 4 note: metrics with <{EXCLUDE_THRESHOLD*100:.0f}% coverage are excluded from calibration feature set per reviewer spec._\n"

    md_path.write_text(md_content)
    return md_path, csv_path

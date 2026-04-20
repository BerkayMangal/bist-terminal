"""Multi-source fundamental reconciliation (Phase 3 FAZ 3.3).

When both 'kap' and 'borsapy' rows exist for the same (symbol, period_end,
metric), report the diff. Phase 3 audit uses this to pick which source is
authoritative and feeds into the SOURCE_PRIORITY_DEFAULT tuning.

Usage:
  python -m research.compare_sources [--symbols A,B,C] [--from YYYY-MM-DD]
    [--to YYYY-MM-DD] [--metrics m1,m2] [--out reports/source_diff.csv]
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

log = logging.getLogger("bistbull.research.compare_sources")


def find_source_disagreements(
    symbols: Optional[list[str]] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    metrics: Optional[list[str]] = None,
    rel_tol: float = 0.01,  # 1%
) -> list[dict]:
    """Find (symbol, period_end, metric) cells where sources disagree beyond rel_tol.

    Returns rows: {symbol, period_end, metric, source_a, value_a, source_b,
                   value_b, rel_diff, abs_diff}
    """
    from infra.storage import _get_conn
    conn = _get_conn()

    where = ["f1.symbol = f2.symbol",
             "f1.period_end = f2.period_end",
             "f1.metric = f2.metric",
             "f1.source < f2.source"]  # canonical ordering avoid dupes
    params: list = []

    if symbols:
        placeholders = ",".join("?" * len(symbols))
        where.append(f"f1.symbol IN ({placeholders})")
        params.extend(s.upper() for s in symbols)
    if from_date:
        where.append("f1.period_end >= ?"); params.append(from_date.isoformat())
    if to_date:
        where.append("f1.period_end <= ?"); params.append(to_date.isoformat())
    if metrics:
        placeholders = ",".join("?" * len(metrics))
        where.append(f"f1.metric IN ({placeholders})")
        params.extend(metrics)

    sql = f"""
        SELECT f1.symbol, f1.period_end, f1.metric,
               f1.source AS source_a, f1.value AS value_a,
               f2.source AS source_b, f2.value AS value_b
        FROM fundamentals_pit f1
        JOIN fundamentals_pit f2 ON {' AND '.join(where)}
        WHERE f1.value IS NOT NULL AND f2.value IS NOT NULL
    """
    rows = conn.execute(sql, params).fetchall()
    out: list[dict] = []
    for r in rows:
        va, vb = float(r["value_a"]), float(r["value_b"])
        denom = max(abs(va), abs(vb), 1e-9)
        rel = abs(va - vb) / denom
        if rel > rel_tol:
            out.append({
                "symbol": r["symbol"],
                "period_end": r["period_end"],
                "metric": r["metric"],
                "source_a": r["source_a"],
                "value_a": va,
                "source_b": r["source_b"],
                "value_b": vb,
                "abs_diff": va - vb,
                "rel_diff": rel,
            })
    out.sort(key=lambda d: (-d["rel_diff"], d["symbol"], d["period_end"], d["metric"]))
    return out


def write_diff_csv(rows: list[dict], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "symbol", "period_end", "metric",
            "source_a", "value_a", "source_b", "value_b",
            "abs_diff", "rel_diff",
        ])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return out


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--from", dest="from_date", default=None)
    ap.add_argument("--to", dest="to_date", default=None)
    ap.add_argument("--metrics", default=None)
    ap.add_argument("--rel-tol", type=float, default=0.01)
    ap.add_argument("--out", default="reports/source_diff.csv")
    args = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    metrics = [m.strip() for m in args.metrics.split(",")] if args.metrics else None
    fd = date.fromisoformat(args.from_date) if args.from_date else None
    td = date.fromisoformat(args.to_date) if args.to_date else None

    from infra.storage import init_db
    init_db()
    rows = find_source_disagreements(symbols, fd, td, metrics, args.rel_tol)
    p = write_diff_csv(rows, Path(args.out))
    print(f"{len(rows)} disagreements > {args.rel_tol:.0%} written to {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

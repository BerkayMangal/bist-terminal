#!/usr/bin/env python3
"""Convert fa_events.csv into fa_isotonic_fits.json.

Input : reports/fa_events.csv  (produced by ingest_fa_for_calibration.py)
Output: reports/fa_isotonic_fits.json
        reports/fa_calibration_summary.md  (human-readable fit quality)

USAGE:
    python scripts/calibrate_fa_from_events.py \\
        --events=reports/fa_events.csv \\
        --out-fits=reports/fa_isotonic_fits.json \\
        --out-summary=reports/fa_calibration_summary.md

PIPELINE:
  1. Load fa_events.csv, group by metric.
  2. Coverage check: metrics with <MIN_COVERAGE_PCT of symbols covered
     are excluded (default 50% matches Phase 3 convention).
  3. Per-metric fit_isotonic with direction from METRIC_DIRECTIONS
     registry. min_samples=20 per metric (MIN_FIT_SAMPLES).
  4. Sanity check: fitted y-series must be monotone in registered
     direction. If registry says ROE increasing but fit is flat/
     decreasing, flag as WARNING (don't include in output).
  5. Write fa_isotonic_fits.json (consumed by
     engine/scoring_calibrated.py:_get_fits at runtime).
  6. Write human-readable summary with per-metric n, knot count,
     domain, range, direction, quality flags.

SANITY CHECKS THAT CAN WARN OR FAIL:
  - Metric direction mismatch (PE fit should be decreasing; if it
    comes out increasing, data is noise → exclude).
  - <20 samples after filtering → exclude.
  - Fit is degenerate (y_min == y_max, i.e. all y values pooled to
    one bucket) → keep but flag as low-signal.
  - Unknown metric (not in METRIC_DIRECTIONS) → skip silently.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.isotonic import fit_isotonic, write_isotonic_fits_json
from engine.scoring_calibrated import METRIC_DIRECTIONS

log = logging.getLogger("bistbull.fa_calibrate")


MIN_FIT_SAMPLES = 20          # research/isotonic.py default
MIN_COVERAGE_PCT = 0.50       # 50% of symbols must have the metric
DEFAULT_TARGET = "forward_return_60d"


def _load_events(events_csv: Path) -> list[dict]:
    rows: list[dict] = []
    with events_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                r["metric_value"] = float(r["metric_value"])
                r[DEFAULT_TARGET] = float(r[DEFAULT_TARGET])
            except (KeyError, ValueError, TypeError) as e:
                log.debug(f"skipping bad row: {e!r}")
                continue
            if math.isnan(r["metric_value"]) or math.isnan(r[DEFAULT_TARGET]):
                continue
            if math.isinf(r["metric_value"]) or math.isinf(r[DEFAULT_TARGET]):
                continue
            rows.append(r)
    return rows


def _coverage_by_metric(events: list[dict]) -> dict[str, float]:
    """Fraction of distinct symbols per metric."""
    by_metric: dict[str, set] = defaultdict(set)
    all_symbols: set[str] = set()
    for r in events:
        by_metric[r["metric"]].add(r["symbol"])
        all_symbols.add(r["symbol"])
    if not all_symbols:
        return {}
    total = len(all_symbols)
    return {m: len(syms) / total for m, syms in by_metric.items()}


def _check_fit_direction(
    fit, expected_increasing: bool,
) -> tuple[bool, str]:
    """Sanity check that the fit's monotonic direction matches the
    registered expectation. Returns (ok, reason)."""
    if not fit or len(fit.y_values) < 2:
        return False, "degenerate fit (fewer than 2 knots)"
    # Compare y_min to y_max — isotonic guarantees y is monotone in
    # the passed direction, but if everything pooled into one block
    # the span is zero and we have no signal.
    if fit.y_max == fit.y_min:
        return False, "degenerate fit (all y pooled to single value)"
    actual_increasing = fit.y_values[-1] > fit.y_values[0]
    if expected_increasing and not actual_increasing:
        return False, "direction mismatch (expected ↑, got ↓)"
    if (not expected_increasing) and actual_increasing:
        return False, "direction mismatch (expected ↓, got ↑)"
    return True, "ok"


def calibrate(
    events_csv: Path,
    out_fits: Path,
    out_summary: Path,
    min_coverage: float = MIN_COVERAGE_PCT,
    min_samples: int = MIN_FIT_SAMPLES,
    target_col: str = DEFAULT_TARGET,
) -> dict:
    """Full pipeline. Returns stats dict (usable from tests)."""
    if not events_csv.exists():
        raise FileNotFoundError(f"events CSV not found: {events_csv}")

    events = _load_events(events_csv)
    log.info(f"Loaded {len(events)} events from {events_csv}")

    # Coverage check
    coverage = _coverage_by_metric(events)
    log.info(
        "Coverage by metric (fraction of distinct symbols):\n  " +
        "\n  ".join(f"{m:20s} {c:.2%}" for m, c in sorted(coverage.items(),
                                                           key=lambda kv: -kv[1]))
    )

    excluded_low_coverage = {
        m for m, c in coverage.items() if c < min_coverage
    }
    if excluded_low_coverage:
        log.warning(
            f"Excluding {len(excluded_low_coverage)} metrics for coverage "
            f"< {min_coverage:.0%}: {sorted(excluded_low_coverage)}"
        )

    # Group by metric (filter excluded)
    by_metric: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in events:
        if r["metric"] in excluded_low_coverage:
            continue
        by_metric[r["metric"]].append((r["metric_value"], r[target_col]))

    fits: dict = {}
    stats: dict = {
        "input_events": len(events),
        "metrics_in_registry": len(METRIC_DIRECTIONS),
        "metrics_in_events": len(coverage),
        "excluded_low_coverage": sorted(excluded_low_coverage),
        "excluded_unknown": [],
        "excluded_sanity": [],
        "fitted": [],
        "coverage": {m: round(c, 4) for m, c in coverage.items()},
    }

    for metric, pairs in by_metric.items():
        if metric not in METRIC_DIRECTIONS:
            stats["excluded_unknown"].append(metric)
            log.debug(f"{metric}: not in METRIC_DIRECTIONS, skipping")
            continue

        direction = METRIC_DIRECTIONS[metric]
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        fit = fit_isotonic(xs, ys, increasing=direction, min_samples=min_samples)
        if fit is None:
            stats["excluded_sanity"].append((metric, "<min_samples"))
            log.warning(f"{metric}: insufficient samples ({len(pairs)}), excluded")
            continue
        ok, reason = _check_fit_direction(fit, direction)
        if not ok:
            stats["excluded_sanity"].append((metric, reason))
            log.warning(f"{metric}: excluded ({reason})")
            continue
        fits[metric] = fit
        stats["fitted"].append({
            "metric": metric,
            "direction": "↑" if direction else "↓",
            "n_samples": fit.n_samples,
            "n_knots": len(fit.x_knots),
            "domain": [round(fit.domain_min, 4), round(fit.domain_max, 4)],
            "y_range": [round(fit.y_min, 4), round(fit.y_max, 4)],
        })

    out_fits.parent.mkdir(parents=True, exist_ok=True)
    write_isotonic_fits_json(fits, out_fits)
    _write_summary_markdown(out_summary, fits, stats)

    log.info(
        f"=== Calibration complete. "
        f"{len(fits)} metrics fitted, "
        f"{len(excluded_low_coverage)} excluded (low coverage), "
        f"{len(stats['excluded_sanity'])} excluded (sanity). ==="
    )
    log.info(f"fits -> {out_fits}")
    log.info(f"summary -> {out_summary}")
    return stats


def _write_summary_markdown(
    out_path: Path, fits: dict, stats: dict,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# FA Calibration Summary\n",
        f"**Input events:** {stats['input_events']}\n",
        f"**Metrics in registry:** {stats['metrics_in_registry']}\n",
        f"**Metrics fitted:** {len(stats['fitted'])}\n",
        f"**Excluded — low coverage (<50% of symbols):** "
        f"{len(stats['excluded_low_coverage'])}\n",
        f"**Excluded — sanity check:** "
        f"{len(stats['excluded_sanity'])}\n",
    ]

    if stats["fitted"]:
        lines += [
            "\n## Per-metric fit quality\n",
            "| Metric | Dir | n | knots | x domain | y range |",
            "|---|:-:|---:|---:|---|---|",
        ]
        for f in sorted(stats["fitted"], key=lambda x: x["metric"]):
            lines.append(
                f"| `{f['metric']}` | {f['direction']} | "
                f"{f['n_samples']} | {f['n_knots']} | "
                f"[{f['domain'][0]}, {f['domain'][1]}] | "
                f"[{f['y_range'][0]}, {f['y_range'][1]}] |"
            )

    if stats["excluded_low_coverage"]:
        lines += [
            "\n## Excluded — low coverage",
            "These metrics had <50% of symbols covered in the ingest. "
            "Production scoring will silently fall back to V13 for these.",
            "",
        ]
        for m in stats["excluded_low_coverage"]:
            cov = stats["coverage"].get(m, 0)
            lines.append(f"- `{m}` — coverage {cov:.0%}")

    if stats["excluded_sanity"]:
        lines += [
            "\n## Excluded — sanity check",
            "",
        ]
        for m, reason in stats["excluded_sanity"]:
            lines.append(f"- `{m}` — {reason}")

    out_path.write_text("\n".join(lines) + "\n")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--events", default="reports/fa_events.csv")
    p.add_argument("--out-fits", default="reports/fa_isotonic_fits.json")
    p.add_argument("--out-summary", default="reports/fa_calibration_summary.md")
    p.add_argument("--min-coverage", type=float, default=MIN_COVERAGE_PCT)
    p.add_argument("--min-samples", type=int, default=MIN_FIT_SAMPLES)
    p.add_argument("--target", default=DEFAULT_TARGET,
                   help="Forward-return column in the events CSV")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    calibrate(
        events_csv=Path(args.events),
        out_fits=Path(args.out_fits),
        out_summary=Path(args.out_summary),
        min_coverage=args.min_coverage,
        min_samples=args.min_samples,
        target_col=args.target,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

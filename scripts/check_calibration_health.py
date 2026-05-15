#!/usr/bin/env python3
"""Phase 4.8.1 — Calibration artifact health check.

Run as a pre-commit hook or CI step. Verifies that the
calibrated scoring artifacts in reports/ are not silently
broken. The empty-zip drama in Phase 4.7 was the kind of
regression this script exists to catch.

CHECKS:
  1. reports/fa_isotonic_fits.json
     - file exists
     - parses as JSON
     - is a non-empty dict
     - contains at least MIN_FITTED_METRICS (default 5) metrics
     - each entry has the expected keys (x_knots, y_values, increasing,
       n_samples, domain_min, domain_max)
     - x_knots and y_values are non-empty lists with >=2 entries each
     - same length

  2. reports/fa_events.csv
     - file exists
     - has header + at least MIN_EVENT_ROWS data rows (default 100)
     - header includes the expected columns
     - non-trivial number of distinct symbols (default >=3)

  3. reports/fa_calibration_summary.md
     - file exists
     - "Input events:" line claims a number > 0
     - "Metrics fitted:" line claims a number >= MIN_FITTED_METRICS

EXIT CODES:
  0   all checks passed
  1   one or more checks failed (lists which)
  2   misconfigured (missing required arg etc.)

USAGE:
  python scripts/check_calibration_health.py          # default thresholds
  python scripts/check_calibration_health.py --strict # tighter thresholds for CI

  As a git pre-commit hook (.git/hooks/pre-commit):
    #!/bin/bash
    python scripts/check_calibration_health.py || exit 1

  As a GitHub Actions step:
    - run: python scripts/check_calibration_health.py --strict
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Repo root resolves regardless of CWD (Phase 4.3.5 pattern)
_REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_FIT_KEYS = {
    "x_knots", "y_values", "increasing",
    "n_samples", "domain_min", "domain_max",
}

EXPECTED_CSV_COLUMNS = {
    "symbol", "period_end", "filed_at", "metric",
    "metric_value", "forward_return_60d",
}


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str

    def render(self) -> str:
        icon = "✅" if self.ok else "❌"
        return f"  {icon} {self.name}: {self.message}"


def check_fits_json(
    path: Path,
    min_metrics: int,
) -> list[CheckResult]:
    """Validate reports/fa_isotonic_fits.json structure + content."""
    results: list[CheckResult] = []

    if not path.exists():
        results.append(CheckResult(
            "fits.json:exists", False,
            f"file not found: {path}",
        ))
        return results
    results.append(CheckResult("fits.json:exists", True, str(path)))

    # Parse JSON
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        results.append(CheckResult(
            "fits.json:parses", False,
            f"JSON parse error: {e}",
        ))
        return results
    results.append(CheckResult("fits.json:parses", True, "valid JSON"))

    # Type check
    if not isinstance(data, dict):
        results.append(CheckResult(
            "fits.json:is_dict", False,
            f"expected dict, got {type(data).__name__}",
        ))
        return results

    # Metric count
    n_metrics = len(data)
    if n_metrics < min_metrics:
        results.append(CheckResult(
            "fits.json:metric_count", False,
            f"only {n_metrics} metrics fitted, expected >= {min_metrics} "
            f"(an empty {{}} dict means calibration produced 0 metrics — "
            f"check Colab backfill output before committing)",
        ))
        return results
    results.append(CheckResult(
        "fits.json:metric_count", True,
        f"{n_metrics} metrics fitted",
    ))

    # Per-metric structural validation
    bad: list[str] = []
    for metric, fit in data.items():
        if not isinstance(fit, dict):
            bad.append(f"{metric}: not a dict")
            continue
        missing = EXPECTED_FIT_KEYS - set(fit.keys())
        if missing:
            bad.append(f"{metric}: missing keys {missing}")
            continue
        if not isinstance(fit.get("x_knots"), list) or len(fit["x_knots"]) < 2:
            bad.append(f"{metric}: x_knots must have >=2 entries")
            continue
        if not isinstance(fit.get("y_values"), list) or len(fit["y_values"]) < 2:
            bad.append(f"{metric}: y_values must have >=2 entries")
            continue
        if len(fit["x_knots"]) != len(fit["y_values"]):
            bad.append(
                f"{metric}: x_knots ({len(fit['x_knots'])}) "
                f"≠ y_values ({len(fit['y_values'])})"
            )
            continue
        if not isinstance(fit.get("increasing"), bool):
            bad.append(f"{metric}: 'increasing' must be bool")

    if bad:
        results.append(CheckResult(
            "fits.json:per_metric_structure", False,
            f"{len(bad)} bad fits: " + "; ".join(bad[:3])
            + (f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""),
        ))
    else:
        results.append(CheckResult(
            "fits.json:per_metric_structure", True,
            f"all {n_metrics} fits structurally valid",
        ))

    return results


def check_events_csv(
    path: Path,
    min_rows: int,
    min_symbols: int,
) -> list[CheckResult]:
    """Validate reports/fa_events.csv structure + content."""
    results: list[CheckResult] = []

    if not path.exists():
        results.append(CheckResult(
            "events.csv:exists", False,
            f"file not found: {path}",
        ))
        return results
    results.append(CheckResult("events.csv:exists", True, str(path)))

    # Read with csv.DictReader to handle header
    try:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            header = set(reader.fieldnames or [])
            rows = list(reader)
    except Exception as e:
        results.append(CheckResult(
            "events.csv:parses", False,
            f"CSV parse error: {e}",
        ))
        return results
    results.append(CheckResult("events.csv:parses", True, "valid CSV"))

    # Header columns
    missing_cols = EXPECTED_CSV_COLUMNS - header
    if missing_cols:
        results.append(CheckResult(
            "events.csv:header", False,
            f"missing columns: {missing_cols}",
        ))
    else:
        results.append(CheckResult(
            "events.csv:header", True,
            f"all expected columns present",
        ))

    # Row count
    n = len(rows)
    if n < min_rows:
        results.append(CheckResult(
            "events.csv:row_count", False,
            f"only {n} data rows, expected >= {min_rows} "
            f"(header-only CSV means ingest produced 0 events — "
            f"check Colab logs)",
        ))
        return results
    results.append(CheckResult(
        "events.csv:row_count", True,
        f"{n} data rows",
    ))

    # Distinct symbols
    symbols = {r.get("symbol") for r in rows if r.get("symbol")}
    if len(symbols) < min_symbols:
        results.append(CheckResult(
            "events.csv:distinct_symbols", False,
            f"only {len(symbols)} distinct symbols, expected >= {min_symbols}",
        ))
    else:
        results.append(CheckResult(
            "events.csv:distinct_symbols", True,
            f"{len(symbols)} distinct symbols",
        ))

    return results


def check_summary_md(
    path: Path,
    min_events: int,
    min_metrics: int,
) -> list[CheckResult]:
    """Validate reports/fa_calibration_summary.md claims."""
    results: list[CheckResult] = []

    if not path.exists():
        results.append(CheckResult(
            "summary.md:exists", False,
            f"file not found: {path}",
        ))
        return results
    results.append(CheckResult("summary.md:exists", True, str(path)))

    text = path.read_text()

    # Match "**Input events:** N"
    m = re.search(r"\*\*Input events:\*\*\s*(\d+)", text)
    if not m:
        results.append(CheckResult(
            "summary.md:input_events_line", False,
            "missing 'Input events:' line",
        ))
    else:
        n = int(m.group(1))
        if n < min_events:
            results.append(CheckResult(
                "summary.md:input_events", False,
                f"reports {n} events, expected >= {min_events}",
            ))
        else:
            results.append(CheckResult(
                "summary.md:input_events", True,
                f"{n} input events reported",
            ))

    # Match "**Metrics fitted:** N"
    m = re.search(r"\*\*Metrics fitted:\*\*\s*(\d+)", text)
    if not m:
        results.append(CheckResult(
            "summary.md:metrics_fitted_line", False,
            "missing 'Metrics fitted:' line",
        ))
    else:
        n = int(m.group(1))
        if n < min_metrics:
            results.append(CheckResult(
                "summary.md:metrics_fitted", False,
                f"reports {n} metrics fitted, expected >= {min_metrics}",
            ))
        else:
            results.append(CheckResult(
                "summary.md:metrics_fitted", True,
                f"{n} metrics fitted reported",
            ))

    return results


def run_all_checks(
    repo_root: Path,
    min_metrics: int,
    min_rows: int,
    min_symbols: int,
    min_events: int,
) -> tuple[bool, list[CheckResult]]:
    """Run all checks. Returns (overall_ok, list_of_results)."""
    results: list[CheckResult] = []

    fits_path = repo_root / "reports" / "fa_isotonic_fits.json"
    events_path = repo_root / "reports" / "fa_events.csv"
    summary_path = repo_root / "reports" / "fa_calibration_summary.md"

    results.extend(check_fits_json(fits_path, min_metrics))
    results.extend(check_events_csv(events_path, min_rows, min_symbols))
    results.extend(check_summary_md(summary_path, min_events, min_metrics))

    overall_ok = all(r.ok for r in results)
    return overall_ok, results


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--strict", action="store_true",
                   help="CI mode: tighter thresholds")
    p.add_argument("--min-metrics", type=int, default=None,
                   help="Override min metrics in fits.json (default 5, strict 10)")
    p.add_argument("--min-rows", type=int, default=None,
                   help="Override min rows in events.csv (default 100, strict 500)")
    p.add_argument("--min-symbols", type=int, default=None,
                   help="Override min distinct symbols (default 3, strict 5)")
    p.add_argument("--min-events", type=int, default=None,
                   help="Override min events in summary.md (default 100, strict 500)")
    p.add_argument("--repo-root", default=None,
                   help="Override repo root path (default: parent of scripts/)")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-check output, only print summary")
    args = p.parse_args(argv)

    # Threshold defaults
    if args.strict:
        defaults = {"min_metrics": 10, "min_rows": 500,
                    "min_symbols": 5, "min_events": 500}
    else:
        defaults = {"min_metrics": 5, "min_rows": 100,
                    "min_symbols": 3, "min_events": 100}

    min_metrics = args.min_metrics or defaults["min_metrics"]
    min_rows = args.min_rows or defaults["min_rows"]
    min_symbols = args.min_symbols or defaults["min_symbols"]
    min_events = args.min_events or defaults["min_events"]

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _REPO_ROOT

    if not args.quiet:
        print(f"Calibration artifact health check ({'STRICT' if args.strict else 'normal'} mode)")
        print(f"Repo root: {repo_root}")
        print(f"Thresholds: metrics>={min_metrics}, rows>={min_rows}, "
              f"symbols>={min_symbols}, events>={min_events}")
        print()

    ok, results = run_all_checks(
        repo_root=repo_root,
        min_metrics=min_metrics,
        min_rows=min_rows,
        min_symbols=min_symbols,
        min_events=min_events,
    )

    if not args.quiet:
        for r in results:
            print(r.render())
        print()

    n_ok = sum(1 for r in results if r.ok)
    n_total = len(results)
    if ok:
        print(f"✅ All {n_total} checks passed.")
        return 0
    else:
        n_failed = n_total - n_ok
        print(f"❌ {n_failed}/{n_total} checks failed.")
        print()
        print("This is the empty-zip canary. If you see failures here, "
              "calibration artifacts are broken — DO NOT push.")
        print("Re-run the Colab backfill, verify outputs locally, then commit.")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

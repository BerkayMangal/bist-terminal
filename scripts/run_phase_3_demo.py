"""Phase 3 end-to-end demo run (Phase 3 FAZ 3.0.5 + 3.4).

Wires the ingest scripts, labeler, validator, signal registry, coverage
and compare_sources together. Populates a fresh DB with a larger synthetic
backfill (all of seeded BIST30 × 2018-2026 fundamentals + prices) and
runs every signal in research.signals.SIGNAL_DETECTORS, emitting:

  reports/phase_3_coverage.md  + .csv
  reports/phase_3_universe_audit.md
  reports/validator/{signal}.json + {signal}.md  -- per signal
  reports/OUTCOMES.md   -- expected vs actual + kill list
  reports/summary.csv   -- one row per signal

THIS DOES NOT CALL REAL BORSAPY. The real backfill ships as a separate
operator-run action (see PHASE_3_REPORT.md §Follow-up). All data below
is synthetic and deterministic; the numbers will NOT match real Turkish
market behavior. What the demo proves: the whole pipeline runs end-to-end.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

# Script is invoked as `python3 scripts/run_phase_3_demo.py` from repo root;
# Python does NOT auto-add the cwd to sys.path when running a subdirectory
# script, so `from infra...` etc. would fail. Prepend the repo root (parent
# of scripts/) explicitly. This keeps the script runnable via either
#   python3 scripts/run_phase_3_demo.py
#   PYTHONPATH=. python3 scripts/run_phase_3_demo.py
#   python3 -m scripts.run_phase_3_demo
# without assuming which one the operator chose.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("phase_3_demo")

# Signal -> expected direction (agent's a-priori guess for OUTCOMES.md)
EXPECTED = {
    "Golden Cross":           ("bullish", "strong", "classic trend-follow; expected positive Sharpe 0.5-1.0"),
    "Death Cross":            ("bearish", "strong", "mirror of Golden; useful as bearish filter if Sharpe<0"),
    "52W High Breakout":      ("bullish", "strong", "momentum breakout; expected strong Sharpe >0.8"),
    "MACD Bullish Cross":     ("bullish", "medium", "noisy on shorter windows; expected modest 0.2-0.5"),
    "MACD Bearish Cross":     ("bearish", "medium", "same"),
    "RSI Asiri Alim":         ("bearish", "weak", "contrarian or exit signal; weak expected Sharpe"),
    "RSI Asiri Satim":        ("bullish", "weak", "mean-reversion; weak expected Sharpe"),
    "BB Ust Band Kirilim":    ("neutral", "weak", "can be continuation or reversal; expect near-zero"),
    "BB Alt Band Kirilim":    ("neutral", "weak", "same"),
    "Ichimoku Kumo Breakout":   ("bullish", "strong", "STUB — returns 0 trades this run"),
    "Ichimoku Kumo Breakdown":  ("bearish", "strong", "STUB"),
    "Ichimoku TK Cross":        ("bullish", "medium", "STUB"),
    "VCP Kirilim":              ("bullish", "strong", "STUB"),
    "Rectangle Breakout":       ("bullish", "medium", "STUB"),
    "Rectangle Breakdown":      ("bearish", "medium", "STUB"),
    "Direnc Kirilimi":          ("bullish", "medium", "STUB"),
    "Destek Kirilimi":          ("bearish", "medium", "STUB"),
}


def ensure_data(db_path: Path) -> None:
    """Load universe history + backfill synthetic fundamentals + prices."""
    os.environ["BISTBULL_DB_PATH"] = str(db_path)
    from infra.storage import init_db
    init_db()

    from infra.pit import load_universe_history_csv, get_universe_at
    n = load_universe_history_csv()
    log.info(f"universe_history: {n} rows loaded")

    # Combine today's + 2020 BIST30 to cover all seeded symbols
    all_syms = set(get_universe_at("BIST30", "2026-04-20"))
    all_syms |= set(get_universe_at("BIST30", "2020-06-15"))
    symbols = sorted(all_syms)
    log.info(f"backfilling {len(symbols)} symbols")

    from research.ingest_filings import ingest as ingest_fund
    from research.ingest_prices import ingest as ingest_px

    # Clear stale checkpoints so tests are reproducible
    for p in (Path("/tmp/bistbull_ingest_checkpoint.json"),
              Path("/tmp/bistbull_ingest_prices_checkpoint.json")):
        if p.exists():
            p.unlink()

    ingest_fund(symbols=symbols, from_date=date(2018, 1, 1), to_date=date(2026, 1, 1),
                dry_run=True, threaded=False)
    log.info("fundamentals backfill done")

    ingest_px(symbols=symbols, from_date=date(2018, 1, 1), to_date=date(2026, 4, 30),
              dry_run=True, threaded=True, max_workers=5)
    log.info("prices backfill done")


def run_validators(out_dir: Path, today: date = date(2026, 4, 20)) -> list[dict]:
    from research.validator import run_validator, write_report
    from research.signals import SIGNAL_DETECTORS

    rows: list[dict] = []
    validator_dir = out_dir / "validator"
    validator_dir.mkdir(parents=True, exist_ok=True)

    for signal_name, detector in sorted(SIGNAL_DETECTORS.items()):
        log.info(f"validating signal: {signal_name}")
        r = run_validator(
            signal_name=signal_name,
            detector=detector,
            universe="BIST30",
            from_date=date(2020, 1, 1), to_date=date(2025, 12, 31),
            sample_every_n_days=5,
            benchmark_symbol=None,  # no XU100 synthetic -- skip IR for demo
            today=today,
        )
        jp, mp = write_report(r, validator_dir)
        rows.append(r.as_dict() | {"json_path": str(jp), "md_path": str(mp)})
    return rows


def write_summary(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["signal", "universe", "n_trades", "hit_rate_20d",
                    "avg_return_20d", "t_stat_20d", "sharpe_20d_ann",
                    "ir_vs_benchmark_20d", "decision"])
        for r in rows:
            w.writerow([
                r["signal"], r["universe"], r["n_trades"],
                f"{(r['hit_rate_20d'] or 0)*100:.2f}%" if r["hit_rate_20d"] is not None else "",
                f"{(r['avg_return_20d'] or 0)*100:.3f}%" if r["avg_return_20d"] is not None else "",
                f"{r['t_stat_20d']:.2f}" if r["t_stat_20d"] is not None else "",
                f"{r['sharpe_20d_ann']:.2f}" if r["sharpe_20d_ann"] is not None else "",
                f"{r['ir_vs_benchmark_20d']:.2f}" if r["ir_vs_benchmark_20d"] is not None else "",
                r["decision"],
            ])


def write_outcomes(rows: list[dict], out_path: Path) -> None:
    """OUTCOMES.md: expected vs actual, kill list."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Phase 3 OUTCOMES — Expected vs Actual\n",
             "**NOTE: The figures below were produced on SYNTHETIC random-walk "
             "price data**, not real market history. They exist to prove the "
             "validator pipeline is wired; actual signal quality must be "
             "reassessed once Phase 3b runs the real borsapy backfill.\n",
             "## Expected vs Actual\n",
             "| Signal | Expected direction | Expected strength | Actual decision | Actual Sharpe_20d | n_trades |",
             "|---|---|---|---|---|---|"]

    by_sig = {r["signal"]: r for r in rows}
    kept_strong, kept_weak, killed = [], [], []

    for sig in EXPECTED:
        exp = EXPECTED[sig]
        r = by_sig.get(sig)
        if not r:
            lines.append(f"| {sig} | {exp[0]} | {exp[1]} | (not run) | — | — |")
            continue
        sharpe = r["sharpe_20d_ann"]
        sharpe_s = f"{sharpe:.2f}" if sharpe is not None else "—"
        lines.append(f"| {sig} | {exp[0]} | {exp[1]} | `{r['decision']}` | {sharpe_s} | {r['n_trades']} |")
        if r["decision"] == "keep_strong":
            kept_strong.append(sig)
        elif r["decision"] == "keep_weak":
            kept_weak.append(sig)
        else:
            killed.append((sig, r["n_trades"]))

    lines.append("\n## Keep (strong)\n")
    lines.append("\n".join(f"- {s}" for s in kept_strong) if kept_strong else "_None._")

    lines.append("\n\n## Keep (weak) — use as filter, not trigger\n")
    lines.append("\n".join(f"- {s}" for s in kept_weak) if kept_weak else "_None._")

    lines.append("\n\n## Kill list\n")
    if killed:
        for s, n in killed:
            lines.append(f"- {s} (n_trades={n})")
    else:
        lines.append("_None._")

    lines.append("\n\n---\n\n_Phase 4 feature-selection prior: start from the 'Keep strong' list; "
                 "the 'Keep weak' signals enter as filter candidates with lower weights._\n")

    out_path.write_text("\n".join(lines))


def write_universe_audit(out_path: Path) -> None:
    """Phase 3 FAZ 3.1 universe audit report."""
    from infra.storage import _get_conn
    out_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()
    rows = conn.execute(
        """SELECT universe_name, symbol, from_date, to_date, reason, source_url
           FROM universe_history ORDER BY universe_name, symbol, from_date"""
    ).fetchall()

    total = len(rows)
    by_reason: dict[str, int] = {}
    unverified_count = 0
    for r in rows:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
        if r["reason"] == "approximate":
            unverified_count += 1

    lines = [
        "# Phase 3 Universe Audit\n",
        f"**Total rows:** {total}",
        "",
        "## Rows by reason\n",
        "| Reason | Count |",
        "|---|---|",
    ]
    for k in ("verified", "addition", "removal", "approximate"):
        lines.append(f"| `{k}` | {by_reason.get(k, 0)} |")

    lines.append("\n## Status\n")
    if unverified_count == 0:
        lines.append("✅ All entries verified with source URLs.")
    else:
        lines.append(f"⚠ {unverified_count}/{total} rows are `approximate` "
                     "(no KAP / Borsa Istanbul source URL).")
        lines.append("")
        lines.append("**Next action:** Phase 3b audit run with access to KAP / "
                     "Borsa Istanbul historical index membership announcements. "
                     "Each `approximate` row either becomes `verified` + source_url, "
                     "or stays `approximate` with an explicit note in the report.")

    lines.append("\n## Full list\n")
    lines.append("| Universe | Symbol | from_date | to_date | reason | source_url |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        lines.append(f"| {r['universe_name']} | {r['symbol']} | "
                     f"{r['from_date']} | {r['to_date'] or '—'} | "
                     f"`{r['reason']}` | {r['source_url'] or '—'} |")

    out_path.write_text("\n".join(lines) + "\n")


def main(argv: list[str]) -> int:
    from datetime import date
    from pathlib import Path

    db_path = Path("/tmp/phase_3_demo.db")
    reports_dir = Path("reports")

    # Wipe prior demo DB
    if db_path.exists():
        db_path.unlink()

    # 1. Populate synthetic backfill
    ensure_data(db_path)

    # 2. Coverage report (from fundamentals_pit after synthetic backfill)
    from research.coverage import compute_coverage, write_coverage_reports
    from infra.pit import get_universe_at
    all_syms = set(get_universe_at("BIST30", "2026-04-20"))
    all_syms |= set(get_universe_at("BIST30", "2020-06-15"))
    symbols = sorted(all_syms)
    cov_rows = compute_coverage(symbols=symbols,
                                from_date=date(2018, 1, 1),
                                to_date=date(2026, 1, 1))
    md, csvp = write_coverage_reports(cov_rows, date(2018, 1, 1), date(2026, 1, 1),
                                      reports_dir, data_source="synthetic")
    log.info(f"coverage: {md}")

    # 3. Universe audit
    write_universe_audit(reports_dir / "phase_3_universe_audit.md")
    log.info("universe audit: reports/phase_3_universe_audit.md")

    # 4. Validators (all 17 signals)
    vrows = run_validators(reports_dir, today=date(2026, 4, 20))

    # 5. summary.csv + OUTCOMES.md
    write_summary(vrows, reports_dir / "summary.csv")
    write_outcomes(vrows, reports_dir / "OUTCOMES.md")
    log.info(f"summary.csv + OUTCOMES.md written")

    # 6. compare_sources demo (no real/synth dual-source in this demo,
    # but demonstrate the script runs cleanly)
    from research.compare_sources import find_source_disagreements, write_diff_csv
    diffs = find_source_disagreements()
    write_diff_csv(diffs, reports_dir / "source_diff.csv")
    log.info(f"source_diff: {len(diffs)} disagreements (expected 0 in single-source demo)")

    print(f"Phase 3 demo complete. {len(vrows)} signal reports in {reports_dir/'validator'}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

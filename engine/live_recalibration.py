"""Phase 10 — Live recalibration pipeline.

The Phase 4.7 → 9 rollout produced a one-shot calibration: events
were ingested from KAP, fitted in Colab, committed to the repo.
After that, fits were static. New filings, new market behaviour,
new return windows — none of it updated the curves.

Phase 10 adds a continuous-learning pipeline:

  1. INGEST: every quarter (when KAP releases new earnings),
     pull the new filings via the existing borsapy + provider
     stack. Compute forward returns once enough trading days
     have passed.

  2. CALIBRATE: re-run isotonic regression on the expanded event
     set. Compare new fits to current production fits via:
       - knot count delta
       - mean prediction shift
       - max y-value shift
       - n_samples ratio

  3. PROMOTE: if shifts are within tolerance bands (no surprise
     regression), automatically promote new fits to production
     by atomically replacing reports/fa_isotonic_fits.json. If
     shifts exceed bands, raise alert and require operator review.

  4. ROLLBACK: keep last-good fits as backup; restore on demand
     if production scoring degrades.

THIS TURN: scaffold. The actual scheduling (apscheduler / cron)
and KAP push-notification integration is environment-dependent
and requires Phase 10 deploy turn to wire up. The scaffold:
  - Promotion validator (compare two fits dicts, return verdict)
  - Backup + restore primitives
  - Recalibration runner that reads from score_history
  - Operator CLI for manual trigger

USAGE:
  # CLI manual trigger
  python -m engine.live_recalibration \\
      --since-days 90 --dry-run

  # Programmatic
  from engine.live_recalibration import (
      LiveRecalibrator, validate_promotion,
  )
  r = LiveRecalibrator()
  result = r.run(dry_run=True)
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("bistbull.live_recalibration")


# ==========================================================================
# Promotion thresholds (configurable)
# ==========================================================================

@dataclass
class PromotionConfig:
    """Tolerance bands for automatic fits promotion.

    Phase 10 deploy may tune these. Defaults are conservative:
    only auto-promote when changes are small. Big shifts require
    operator review.
    """
    # Maximum |Δ| in number of metrics fitted (e.g. v1 had 15, v2 has 20 → +5)
    max_metric_delta: int = 5

    # Maximum mean shift in y-values (return prediction shift)
    max_mean_y_shift: float = 0.03  # 3 percentage points return delta

    # Maximum max-y shift across all metrics
    max_per_metric_y_shift: float = 0.10  # 10 pp at any single x

    # Minimum n_samples ratio (new / current). 1.0 = same data, > 1 = more data
    min_n_samples_ratio: float = 0.95

    # Minimum metrics overlap fraction (intersection / union)
    min_metric_overlap: float = 0.70


# ==========================================================================
# Promotion verdict
# ==========================================================================

@dataclass
class PromotionVerdict:
    promote: bool
    reason: str
    diagnostics: dict

    def to_dict(self) -> dict:
        return {
            "promote": self.promote,
            "reason": self.reason,
            "diagnostics": self.diagnostics,
        }


# ==========================================================================
# Validation logic
# ==========================================================================

def validate_promotion(
    current_fits: dict,
    candidate_fits: dict,
    config: Optional[PromotionConfig] = None,
) -> PromotionVerdict:
    """Compare two fits dicts; decide whether candidate can auto-promote.

    Returns a PromotionVerdict with promote=True/False and reasoning.

    Each fits dict is the JSON shape produced by IsotonicFit serialization
    (the same shape as reports/fa_isotonic_fits.json).
    """
    cfg = config or PromotionConfig()

    if not current_fits:
        # No prior fits — anything is an upgrade
        return PromotionVerdict(
            promote=True,
            reason="no_current_fits",
            diagnostics={"candidate_metric_count": len(candidate_fits)},
        )

    if not candidate_fits:
        return PromotionVerdict(
            promote=False,
            reason="empty_candidate",
            diagnostics={"current_metric_count": len(current_fits)},
        )

    cur_keys = set(current_fits.keys())
    cand_keys = set(candidate_fits.keys())

    # Metric count delta
    delta_metrics = len(cand_keys) - len(cur_keys)
    if abs(delta_metrics) > cfg.max_metric_delta:
        return PromotionVerdict(
            promote=False,
            reason="metric_count_shift_too_large",
            diagnostics={
                "current_count": len(cur_keys),
                "candidate_count": len(cand_keys),
                "delta": delta_metrics,
                "threshold": cfg.max_metric_delta,
            },
        )

    # Metrics overlap
    common = cur_keys & cand_keys
    union = cur_keys | cand_keys
    overlap = len(common) / len(union) if union else 1.0
    if overlap < cfg.min_metric_overlap:
        return PromotionVerdict(
            promote=False,
            reason="insufficient_metric_overlap",
            diagnostics={
                "overlap_fraction": overlap,
                "threshold": cfg.min_metric_overlap,
                "removed": sorted(cur_keys - cand_keys),
                "added": sorted(cand_keys - cur_keys),
            },
        )

    # n_samples ratio (per common metric)
    n_ratios: list[float] = []
    for k in common:
        cur_n = current_fits[k].get("n_samples", 0)
        cand_n = candidate_fits[k].get("n_samples", 0)
        if cur_n > 0:
            n_ratios.append(cand_n / cur_n)
    avg_n_ratio = sum(n_ratios) / len(n_ratios) if n_ratios else 1.0
    if avg_n_ratio < cfg.min_n_samples_ratio:
        return PromotionVerdict(
            promote=False,
            reason="sample_size_regression",
            diagnostics={
                "avg_n_ratio": avg_n_ratio,
                "threshold": cfg.min_n_samples_ratio,
            },
        )

    # Per-metric y-shift comparison
    max_per_metric_shift = 0.0
    mean_y_shifts: list[float] = []
    for k in common:
        cur_fit = current_fits[k]
        cand_fit = candidate_fits[k]
        cur_y = cur_fit.get("y_values", [])
        cand_y = cand_fit.get("y_values", [])
        if not cur_y or not cand_y:
            continue
        # Compare at the median y of each (proxy for "central tendency")
        cur_mid = sorted(cur_y)[len(cur_y) // 2]
        cand_mid = sorted(cand_y)[len(cand_y) // 2]
        shift = abs(cand_mid - cur_mid)
        mean_y_shifts.append(shift)
        # Compare overall y-range max
        if cur_y and cand_y:
            cur_yr = max(cur_y) - min(cur_y)
            cand_yr = max(cand_y) - min(cand_y)
            metric_shift = abs(cand_yr - cur_yr)
            max_per_metric_shift = max(max_per_metric_shift, metric_shift)

    avg_y_shift = sum(mean_y_shifts) / len(mean_y_shifts) if mean_y_shifts else 0.0

    if avg_y_shift > cfg.max_mean_y_shift:
        return PromotionVerdict(
            promote=False,
            reason="mean_y_shift_too_large",
            diagnostics={
                "avg_y_shift": avg_y_shift,
                "threshold": cfg.max_mean_y_shift,
            },
        )

    if max_per_metric_shift > cfg.max_per_metric_y_shift:
        return PromotionVerdict(
            promote=False,
            reason="per_metric_y_range_shift_too_large",
            diagnostics={
                "max_per_metric_shift": max_per_metric_shift,
                "threshold": cfg.max_per_metric_y_shift,
            },
        )

    return PromotionVerdict(
        promote=True,
        reason="passed_all_thresholds",
        diagnostics={
            "metric_overlap": overlap,
            "avg_n_ratio": avg_n_ratio,
            "avg_y_shift": avg_y_shift,
            "max_per_metric_shift": max_per_metric_shift,
        },
    )


# ==========================================================================
# Backup + restore primitives
# ==========================================================================

def backup_fits(
    fits_path: Path,
    backups_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Create a timestamped backup of fits_path.

    Returns the backup path on success, None if source missing.
    """
    if not fits_path.exists():
        return None
    backups_dir = backups_dir or fits_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backups_dir / f"{fits_path.stem}_{timestamp}{fits_path.suffix}"
    shutil.copy2(fits_path, backup_path)
    log.info(f"backed up {fits_path} → {backup_path}")
    return backup_path


def restore_fits(
    fits_path: Path,
    backup_path: Path,
) -> bool:
    """Restore fits_path from backup_path. Returns True on success."""
    if not backup_path.exists():
        log.error(f"backup not found: {backup_path}")
        return False
    shutil.copy2(backup_path, fits_path)
    log.info(f"restored {fits_path} from {backup_path}")
    return True


def list_backups(backups_dir: Path, prefix: str = "fa_isotonic_fits") -> list[Path]:
    """List available backup files for prefix, newest first."""
    if not backups_dir.exists():
        return []
    backups = sorted(
        [p for p in backups_dir.glob(f"{prefix}_*.json")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return backups


# ==========================================================================
# Live recalibration runner
# ==========================================================================

@dataclass
class RecalibrationResult:
    started_at: str
    finished_at: str
    dry_run: bool
    candidate_fits_path: Optional[str]
    verdict: dict
    promoted: bool
    backup_path: Optional[str]
    n_events: int
    errors: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


class LiveRecalibrator:
    """Orchestrates the periodic recalibration cycle.

    Phase 10 scaffold: methods are stubs that delegate to existing
    Phase 4.7 calibration code (calibrate_fa_metrics) with telemetry-
    driven event filtering. Wiring to score_history events done in
    Phase 10 deploy turn.
    """

    def __init__(
        self,
        fits_path: Optional[Path] = None,
        backups_dir: Optional[Path] = None,
        config: Optional[PromotionConfig] = None,
    ):
        from engine.scoring_calibrated import DEFAULT_FITS_PATH
        self.fits_path = fits_path or DEFAULT_FITS_PATH
        self.backups_dir = backups_dir or self.fits_path.parent / "backups"
        self.config = config or PromotionConfig()

    def load_current_fits(self) -> dict:
        """Read current production fits artifact."""
        if not self.fits_path.exists():
            return {}
        try:
            return json.loads(self.fits_path.read_text())
        except Exception as e:
            log.error(f"failed to read {self.fits_path}: {e}")
            return {}

    def fetch_events(self, since_days: int = 90) -> list[dict]:
        """Read events from score_history for recalibration.

        Phase 10 scaffold: returns empty list. Real implementation
        joins score_history with fundamentals_pit to reconstruct
        (metric_value, forward_return) pairs. This is the Phase 10
        deploy turn's main work.
        """
        log.info(
            f"fetch_events(since_days={since_days}) — Phase 10 scaffold "
            f"returns []; deploy turn will implement"
        )
        return []

    def calibrate(self, events: list[dict]) -> dict:
        """Run isotonic calibration on events. Returns fits dict."""
        if not events:
            return {}
        from engine.scoring_calibrated import calibrate_fa_metrics
        try:
            fits = calibrate_fa_metrics(events)
            # Convert IsotonicFit objects to dicts (json-serializable)
            return {k: v.to_dict() if hasattr(v, "to_dict") else v
                    for k, v in fits.items()}
        except Exception as e:
            log.error(f"calibration failed: {e}")
            return {}

    def run(
        self,
        since_days: int = 90,
        dry_run: bool = False,
    ) -> RecalibrationResult:
        """Execute one recalibration cycle.

        Steps:
          1. Fetch events from score_history (last `since_days` days)
          2. Calibrate candidate fits
          3. Validate promotion against current fits
          4. If promote: backup current, atomically swap in candidate
          5. If not promote: leave fits unchanged, log verdict for review

        dry_run=True: do everything except step 4 (no fits file changes).
        """
        started = datetime.now().isoformat()
        errors: list[str] = []

        try:
            events = self.fetch_events(since_days=since_days)
        except Exception as e:
            errors.append(f"fetch_events failed: {e}")
            events = []

        n_events = len(events)
        candidate_fits = self.calibrate(events)
        current_fits = self.load_current_fits()
        verdict = validate_promotion(current_fits, candidate_fits, self.config)

        promoted = False
        backup_path = None
        candidate_path = None

        if verdict.promote and not dry_run and candidate_fits:
            # Backup current
            backup_path = backup_fits(self.fits_path, self.backups_dir)
            # Atomic swap: write to temp then rename
            tmp = self.fits_path.with_suffix(self.fits_path.suffix + ".tmp")
            tmp.write_text(json.dumps(candidate_fits, indent=2))
            tmp.replace(self.fits_path)
            promoted = True
            log.info(f"PROMOTED candidate fits to {self.fits_path}")
        elif verdict.promote and dry_run:
            # Dry run — write candidate to a temp file for inspection
            candidate_path = (
                self.fits_path.parent / f"{self.fits_path.stem}_candidate.json"
            )
            candidate_path.write_text(json.dumps(candidate_fits, indent=2))
            log.info(f"DRY RUN: would have promoted; candidate at {candidate_path}")

        finished = datetime.now().isoformat()
        return RecalibrationResult(
            started_at=started,
            finished_at=finished,
            dry_run=dry_run,
            candidate_fits_path=str(candidate_path) if candidate_path else None,
            verdict=verdict.to_dict(),
            promoted=promoted,
            backup_path=str(backup_path) if backup_path else None,
            n_events=n_events,
            errors=errors,
        )


# ==========================================================================
# CLI
# ==========================================================================

def main(argv: Optional[list[str]] = None) -> int:
    """Operator CLI for manual recalibration trigger."""
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since-days", type=int, default=90,
                   help="Look back this many days of score_history (default: 90)")
    p.add_argument("--dry-run", action="store_true",
                   help="Run all steps but don't replace fits")
    p.add_argument("--max-mean-y-shift", type=float, default=None,
                   help="Override mean y-shift tolerance (default: 0.03)")
    p.add_argument("--list-backups", action="store_true",
                   help="List existing backups and exit")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.list_backups:
        from engine.scoring_calibrated import DEFAULT_FITS_PATH
        backups = list_backups(DEFAULT_FITS_PATH.parent / "backups")
        if not backups:
            print("No backups found.")
        else:
            print(f"Found {len(backups)} backup(s):")
            for b in backups:
                size = b.stat().st_size
                mtime = datetime.fromtimestamp(b.stat().st_mtime)
                print(f"  {b.name}  {size:6d} bytes  {mtime.isoformat()}")
        return 0

    config = PromotionConfig()
    if args.max_mean_y_shift is not None:
        config.max_mean_y_shift = args.max_mean_y_shift

    r = LiveRecalibrator(config=config)
    result = r.run(since_days=args.since_days, dry_run=args.dry_run)
    print(json.dumps(result.to_dict(), indent=2))

    return 0 if result.promoted or args.dry_run else 1


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))

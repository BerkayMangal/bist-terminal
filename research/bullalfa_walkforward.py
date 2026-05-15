# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# research/bullalfa_walkforward.py
#
# v2 calibration scaffold — DEFERRED at v1.4. This module only
# defines the contract the v2 calibration step will fulfil; it does
# NOT run a backtest yet (spec §24 v1 launches without backtest).
#
# When v2 lands, this module will:
#   1. Load PIT-sourced OHLCV + fundamentals across the 250-name
#      superset (BIST 100 + 50 + 100), 2018-01 → current.
#   2. Walk-forward split: 12mo train / 3mo test, non-overlapping.
#   3. Run `engine.bullalfa.build_bullalfa_signal` on each train bar,
#      record (raw_combined, mode, realized 1R/stop/expiry over the
#      configured horizon).
#   4. Fit isotonic calibrators per mode (HIZLI/SWING/POZİSYON) using
#      `sklearn.isotonic.IsotonicRegression` mapping raw_combined →
#      realized hit rate.
#   5. Validate: bucket signals by predicted confidence (deciles),
#      assert realized hit-rate is monotonically non-decreasing AND
#      within ±10pp of predicted (spec §24 calibration check).
#   6. Compute aggregate metrics per mode (hit rate / profit factor /
#      Sharpe, net of 30bps round-trip cost) and gate on the §24
#      acceptance table:
#          HIZLI:    hit_rate ≥ 0.52, PF ≥ 1.20, Sharpe ≥ 0.8
#          SWING:    hit_rate ≥ 0.55, PF ≥ 1.40, Sharpe ≥ 1.0
#          POZİSYON: hit_rate ≥ 0.58, PF ≥ 1.60, Sharpe ≥ 1.2
#   7. Validate TOPLANIYOR upgrade rate (TOPLANIYOR → HIZLI/SWING
#      within 10 bars) ≥ 25%.
#   8. Write the fitted isotonic models to
#      `phase_4_isotonic_fits.json` for the orchestrator to load
#      via `isotonic_fits` arg.
#   9. If any heuristic in `BULLALFA_PARAMS` is materially miscalibrated,
#      override from the calibration report rather than editing code.
#
# v3 (optional, post-v2): swap the isotonic for a LightGBM ranker
# fit on engine outputs + TEMEL dimensions + regime + calendar
# features, post-processed by the SAME isotonic for monotonicity.
# Same `BullAlfaSignal` schema; no UI change required.
# ================================================================

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger("bistbull.bullalfa.walkforward")


# ----------------------------------------------------------------
# v2 acceptance thresholds (spec §24)
# ----------------------------------------------------------------

ACCEPTANCE_THRESHOLDS: dict[str, dict[str, float]] = {
    "HIZLI":    {"hit_rate": 0.52, "profit_factor": 1.20, "sharpe": 0.8},
    "SWING":    {"hit_rate": 0.55, "profit_factor": 1.40, "sharpe": 1.0},
    "POZİSYON": {"hit_rate": 0.58, "profit_factor": 1.60, "sharpe": 1.2},
}

ROUND_TRIP_COST_BPS = 30
TOPLANIYOR_UPGRADE_TARGET = 0.25       # ≥ 25% upgrade rate within 10 bars
WALKFORWARD_TRAIN_MONTHS = 12
WALKFORWARD_TEST_MONTHS  = 3


# ----------------------------------------------------------------
# Config dataclass
# ----------------------------------------------------------------

@dataclass(frozen=True)
class WalkForwardConfig:
    universe:        list[str]
    start_iso:       str = "2018-01-02"
    end_iso:         str = "2026-01-01"
    train_months:    int = WALKFORWARD_TRAIN_MONTHS
    test_months:     int = WALKFORWARD_TEST_MONTHS
    cost_bps:        int = ROUND_TRIP_COST_BPS
    output_path:     str = "phase_4_isotonic_fits.json"


# ----------------------------------------------------------------
# Public entry point — v1 raises NotImplementedError
# ----------------------------------------------------------------

def fit_isotonic_calibrators(config: WalkForwardConfig) -> dict[str, Any]:
    """Run walk-forward + fit isotonic per mode.

    DEFERRED at v1.4. Raises NotImplementedError until v2 lands.
    The v1 orchestrator falls back to `sigmoid_squash` and surfaces
    the `Kalibrasyon: ön-aşama` caveat (spec §15 isotonic_unavailable).
    """
    raise NotImplementedError(
        "v2 walk-forward fit is scheduled post-launch. "
        "v1 uses sigmoid heuristic; orchestrator already wired to "
        "load `phase_4_isotonic_fits.json` via the `isotonic_fits` "
        "kwarg when v2 fits land."
    )


def load_isotonic_fits(path: str = "phase_4_isotonic_fits.json") -> Optional[dict[str, Any]]:
    """Load fitted isotonic calibrators from disk if present.

    Returns None when the file is absent — orchestrator then logs
    `isotonic_unavailable` and surfaces the §15 caveat. This is the
    only function a v1.4 deployment will actually call from this
    module (the rest is a placeholder for v2).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        log.info("isotonic fits not found at %s — using v1 sigmoid", path)
        return None
    except Exception as exc:
        log.warning("isotonic fits load failed at %s: %s", path, exc)
        return None


# ----------------------------------------------------------------
# Validation gate — applied to v2 fit output before publishing
# ----------------------------------------------------------------

def validate_v2_fits(fits: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check that a fits dict satisfies the §24 v2 acceptance gate.

    Returns (ok, [reasons]) — ok is True only when every mode meets
    its hit-rate / profit-factor / Sharpe thresholds AND the
    TOPLANIYOR upgrade rate target is met. v2 should refuse to
    publish fits that fail this gate.
    """
    failures: list[str] = []

    metrics = fits.get("metrics") or {}
    for mode, thresh in ACCEPTANCE_THRESHOLDS.items():
        m = metrics.get(mode) or {}
        for metric_name, floor in thresh.items():
            val = m.get(metric_name)
            if val is None or float(val) < float(floor):
                failures.append(
                    f"{mode}.{metric_name}: {val} < {floor}"
                )

    upgrade_rate = (metrics.get("TOPLANIYOR") or {}).get("upgrade_rate")
    if upgrade_rate is None or float(upgrade_rate) < TOPLANIYOR_UPGRADE_TARGET:
        failures.append(
            f"TOPLANIYOR.upgrade_rate: {upgrade_rate} < {TOPLANIYOR_UPGRADE_TARGET}"
        )

    return (not failures), failures


__all__ = [
    "WalkForwardConfig",
    "fit_isotonic_calibrators",
    "load_isotonic_fits",
    "validate_v2_fits",
    "ACCEPTANCE_THRESHOLDS",
    "TOPLANIYOR_UPGRADE_TARGET",
    "ROUND_TRIP_COST_BPS",
]

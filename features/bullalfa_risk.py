# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# features/bullalfa_risk.py
#
# Layer 4 — risk frame (spec §10).
#
# Required when mode ∈ {HIZLI, SWING, POZİSYON}.
# TOPLANIYOR / SAKİN / UZAK DUR carry no risk frame (None).
#
# 7 invariants (§10):
#   1. entry_low < entry_high
#   2. stop      < entry_low
#   3. stop_pct  < 0
#   4. target_1r > entry_high
#   5. target_2r ≈ entry + 2 × (entry - stop) within ±1% rounding tol
#   6. target_3r > target_2r > target_1r
#   7. max_hold_bars > 0
#
# Failure handling: any invariant failure → orchestrator MUST downgrade
# to TOPLANIYOR (NOT SAKİN, NOT hidden). The user sees the stock with
# the caveat "Kurulum şekilleniyor — risk çerçevesi henüz net değil."
#
# All numeric heuristics (entry-zone band, ATR multiples, hold bars,
# trail rules, rounding tolerance) live in BULLALFA_PARAMS — never
# inlined here. v2 calibration will overwrite those values; this
# module's logic is unchanged.
# ================================================================

from __future__ import annotations

from typing import Optional, Tuple

from engine.bullalfa_params import (
    BULLALFA_PARAMS,
    ENTRY_ZONE_LOW_MULT,
    ENTRY_ZONE_HIGH_MULT,
    RISK_FRAME_R_TOLERANCE_PCT,
    max_hold_bars,
    stop_atr_mult,
    trail_rule,
)

__all__ = [
    "ACTIONABLE_MODES",
    "build_risk_frame",
    "validate_risk_frame",
    "try_build_risk_frame",
    "DOWNGRADE_REASON_INVALID",
    "DOWNGRADE_CAVEAT_TR",
]


# ----------------------------------------------------------------
# Constants
# ----------------------------------------------------------------

ACTIONABLE_MODES: frozenset[str] = frozenset({"HIZLI", "SWING", "POZİSYON"})

DOWNGRADE_REASON_INVALID = "risk_frame_invalid"
DOWNGRADE_CAVEAT_TR = "Kurulum şekilleniyor — risk çerçevesi henüz net değil."

# Spec §10 invalidation phrasing template — "Günlük kapanış {stop} altına düşerse"
_INVALIDATION_TEMPLATE_TR = "Günlük kapanış {stop} altına düşerse"


# ----------------------------------------------------------------
# Builder
# ----------------------------------------------------------------

def build_risk_frame(
    *,
    price: Optional[float],
    atr14: Optional[float],
    mode: str,
) -> Optional[dict]:
    """Build the §10 risk_frame dict for an actionable mode.

    Returns:
        - The frame dict if mode is actionable AND inputs are usable.
        - None if mode is non-actionable (TOPLANIYOR/SAKİN/UZAK DUR).
        - None if price or atr14 is missing/non-positive — caller
          must treat this the same as a failed invariant
          (downgrade to TOPLANIYOR).

    Note: this function does NOT validate the resulting frame against
    the 7 invariants. Use `try_build_risk_frame` for the full
    build + validate + downgrade-signal pipeline.
    """
    if mode not in ACTIONABLE_MODES:
        return None
    if price is None or atr14 is None:
        return None
    p = float(price)
    a = float(atr14)
    if p <= 0.0 or a <= 0.0:
        return None

    mult = stop_atr_mult(mode)
    hold = max_hold_bars(mode)
    trail = trail_rule(mode)
    if mult is None or hold is None or trail is None:
        return None

    entry_low  = round(p * ENTRY_ZONE_LOW_MULT,  2)
    entry_high = round(p * ENTRY_ZONE_HIGH_MULT, 2)
    # Stop is anchored on the latest close (`entry`), per spec snippet
    # `stop = entry - atr14 * stop_mult[mode]` where `entry == price`.
    stop      = round(p - a * mult, 2)
    if p == 0:  # already guarded above, but be explicit
        return None
    stop_pct  = round((stop - p) / p * 100.0, 2)
    r_unit    = p - stop
    target_1r = round(p + 1.0 * r_unit, 2)
    target_2r = round(p + 2.0 * r_unit, 2)
    target_3r = round(p + 3.0 * r_unit, 2)

    return {
        "entry_zone":    (entry_low, entry_high),
        "stop":          stop,
        "stop_pct":      stop_pct,
        "target_1r":     target_1r,
        "target_2r":     target_2r,
        "target_3r":     target_3r,
        "invalidation":  _INVALIDATION_TEMPLATE_TR.format(stop=stop),
        "max_hold_bars": int(hold),
        "trail_rule":    str(trail),
    }


# ----------------------------------------------------------------
# Validation — the 7 invariants
# ----------------------------------------------------------------

def validate_risk_frame(rf: Optional[dict]) -> Tuple[bool, list[str]]:
    """Run the 7 invariants. Returns (ok, failures).

    Each failure is a short stable code suitable for logging. The list
    is empty iff `ok is True`.

    A None frame is treated as a structural failure (`missing_frame`)
    so callers can collapse "couldn't build" and "built-but-invalid"
    into a single downgrade path.
    """
    if rf is None:
        return False, ["missing_frame"]

    failures: list[str] = []

    # Required keys — anything missing is a structural failure that
    # prevents the invariant checks from running.
    required = (
        "entry_zone", "stop", "stop_pct",
        "target_1r", "target_2r", "target_3r",
        "max_hold_bars",
    )
    missing = [k for k in required if k not in rf]
    if missing:
        return False, [f"missing_key:{k}" for k in missing]

    entry_zone = rf["entry_zone"]
    if not (isinstance(entry_zone, (tuple, list)) and len(entry_zone) == 2):
        return False, ["entry_zone_shape"]
    entry_low, entry_high = float(entry_zone[0]), float(entry_zone[1])
    stop      = float(rf["stop"])
    stop_pct  = float(rf["stop_pct"])
    target_1r = float(rf["target_1r"])
    target_2r = float(rf["target_2r"])
    target_3r = float(rf["target_3r"])
    hold      = int(rf["max_hold_bars"])

    # Invariant 1
    if not (entry_low < entry_high):
        failures.append("inv1_entry_band")

    # Invariant 2
    if not (stop < entry_low):
        failures.append("inv2_stop_below_entry")

    # Invariant 3
    if not (stop_pct < 0):
        failures.append("inv3_stop_pct_negative")

    # Invariant 4
    if not (target_1r > entry_high):
        failures.append("inv4_target_above_entry")

    # Invariant 5 — target_2r ≈ entry + 2 × (entry - stop) within tol.
    # We don't have `entry` as its own field; the spec uses entry == price.
    # entry_high is `price * 1.010`, so reconstruct entry from the band:
    # entry == entry_high / ENTRY_ZONE_HIGH_MULT  (== price)
    # The implied 2R = entry + 2 × (entry - stop). We compare target_2r
    # to that recomputed value within the configured tolerance.
    entry_implied = entry_high / ENTRY_ZONE_HIGH_MULT
    expected_2r   = entry_implied + 2.0 * (entry_implied - stop)
    tol           = max(abs(expected_2r), 1e-9) * RISK_FRAME_R_TOLERANCE_PCT
    if abs(target_2r - expected_2r) > tol:
        failures.append("inv5_target_2r_arithmetic")

    # Invariant 6
    if not (target_3r > target_2r > target_1r):
        failures.append("inv6_target_monotonicity")

    # Invariant 7
    if not (hold > 0):
        failures.append("inv7_max_hold_positive")

    return (not failures), failures


# ----------------------------------------------------------------
# Combined build + validate (orchestrator entry point)
# ----------------------------------------------------------------

def try_build_risk_frame(
    *,
    price: Optional[float],
    atr14: Optional[float],
    mode: str,
) -> Tuple[Optional[dict], Optional[str], list[str]]:
    """Build and validate in one call.

    Returns `(frame, downgrade_reason, caveats)`:

    - mode is non-actionable
        → (None, None, [])
        Risk frame intentionally absent. Not a failure.

    - mode is actionable and frame valid
        → (frame_dict, None, [])

    - mode is actionable but build failed or invariants failed
        → (None, "risk_frame_invalid", [TR caveat, *failure codes])
        Orchestrator MUST downgrade `mode` to TOPLANIYOR per §10.
        The TR caveat is user-facing; the failure codes are diagnostic
        and should be logged but not surfaced verbatim to end users.

    Spec §10 mandate: "if any invariant fails, downgrade mode to
    TOPLANIYOR (not SAKİN, not hidden)."
    """
    if mode not in ACTIONABLE_MODES:
        return None, None, []

    frame = build_risk_frame(price=price, atr14=atr14, mode=mode)
    ok, failures = validate_risk_frame(frame)
    if ok:
        return frame, None, []

    return None, DOWNGRADE_REASON_INVALID, [DOWNGRADE_CAVEAT_TR, *failures]


# Surface the BULLALFA_PARAMS reference here for callers wanting the
# raw config (e.g. for diagnostics/logging) without re-importing.
RISK_FRAME_PARAMS = BULLALFA_PARAMS["risk_frame"]

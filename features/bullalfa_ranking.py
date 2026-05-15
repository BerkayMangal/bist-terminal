# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# features/bullalfa_ranking.py
#
# §17 — Unified opportunity score that powers the default scan sort.
#
#   HIZLI / SWING / POZİSYON   → confidence.final  (full credit, 0–100)
#   TOPLANIYOR                  → min(70, accumulation_strength)
#   UZAK DUR                    → 5  (visible but obviously not a buy)
#   SAKİN                       → min(20, quality_score × 0.20)
#
# Default scan sort is `opportunity_score DESC`; an integer return
# preserves stable tie-breaking under repeated recomputes.
#
# All caps + multipliers are sourced from BULLALFA_PARAMS — never
# inlined. v2 calibration may rebalance the SAKİN multiplier and the
# TOPLANIYOR cap; logic untouched.
# ================================================================

from __future__ import annotations

from typing import Optional

from engine.bullalfa_params import (
    OPPORTUNITY_SAKIN_CAP,
    OPPORTUNITY_SAKIN_MULT,
    OPPORTUNITY_TOPLANIYOR_CAP,
    OPPORTUNITY_UZAK_DUR_FIXED,
    SECTOR_CONCENTRATION_THRESHOLD,
)

__all__ = [
    "opportunity_score",
    "ALL_MODES",
    "ACTIONABLE_MODES",
    "sector_concentration_alert",
]


ACTIONABLE_MODES: frozenset[str] = frozenset({"HIZLI", "SWING", "POZİSYON"})
ALL_MODES: frozenset[str] = ACTIONABLE_MODES | frozenset(
    {"TOPLANIYOR", "SAKİN", "UZAK DUR"}
)


# ----------------------------------------------------------------
# Opportunity score
# ----------------------------------------------------------------

def opportunity_score(
    *,
    mode: str,
    confidence_final: Optional[float] = None,
    accumulation_strength: Optional[int] = None,
    quality_score: Optional[float] = None,
) -> int:
    """Compute the §17 opportunity_score for a single signal.

    The function is total: every mode in `ALL_MODES` produces an
    integer ∈ [0, 100]. Missing inputs for a given mode collapse to
    0 with no exception (per §17 the scan must show ALL stocks; a
    missing primitive cannot remove a stock from the result set).

    Args:
      mode: one of HIZLI/SWING/POZİSYON/TOPLANIYOR/SAKİN/UZAK DUR.
        Unknown modes return 0 (not an exception — defensive default
        for a public scan that must never crash on a single signal).
      confidence_final: 0–100, used for actionable modes only.
      accumulation_strength: 0–100, used for TOPLANIYOR only.
      quality_score: 0–100, used for SAKİN only.

    Returns:
      int ∈ [0, 100]. Round-half-to-even via Python int(round(...)).
    """
    if mode in ACTIONABLE_MODES:
        if confidence_final is None:
            return 0
        return _clamp_int(confidence_final)

    if mode == "TOPLANIYOR":
        if accumulation_strength is None:
            return 0
        return min(OPPORTUNITY_TOPLANIYOR_CAP, _clamp_int(accumulation_strength))

    if mode == "UZAK DUR":
        return int(OPPORTUNITY_UZAK_DUR_FIXED)

    if mode == "SAKİN":
        if quality_score is None:
            return 0
        derived = float(quality_score) * OPPORTUNITY_SAKIN_MULT
        return min(OPPORTUNITY_SAKIN_CAP, _clamp_int(derived))

    # Unknown mode — fail closed.
    return 0


def _clamp_int(x: float) -> int:
    """Round to int, clamp to [0, 100]."""
    try:
        v = int(round(float(x)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, v))


# ----------------------------------------------------------------
# Sector concentration banner (§17)
# ----------------------------------------------------------------

def sector_concentration_alert(
    sector_concentration: dict[str, int],
) -> Optional[tuple[str, int]]:
    """Return (sector_group, count) if any sector clears the §17 threshold.

    Returns None when no sector is concentrated. When multiple sectors
    cross the threshold simultaneously, the highest count wins; ties
    break alphabetically on the sector name for deterministic output.

    The orchestrator uses this to render the §17 banner: e.g.
    "Bugün banka sektöründe 6 sinyal var — yoğun korelasyon, dikkat."
    """
    if not sector_concentration:
        return None
    above = [
        (sector, count)
        for sector, count in sector_concentration.items()
        if count >= SECTOR_CONCENTRATION_THRESHOLD
    ]
    if not above:
        return None
    above.sort(key=lambda kv: (-kv[1], kv[0]))
    return above[0]

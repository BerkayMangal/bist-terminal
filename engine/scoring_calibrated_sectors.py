"""Phase 9 — Sector-conditional fits scaffold.

Phase 4.7 fits used a single isotonic curve per metric across all
sectors. This is a reasonable starting assumption but masks
genuine sector heterogeneity:

  HOLDINGS (KCHOL, SAHOL): debt/equity routinely > 100 because of
  consolidated debt; an industrial firm with d/e=100 is in distress,
  but for a holding it's normal. Single-eğri model conflates these.

  PERAKENDE (MGROS, BIM, SOK, ULKR): high stock turnover, low net
  margins (3-5%), inventory-heavy balance sheet. ROIC distribution
  is offset from heavy-industry firms.

  ENERJI (AKSEN, ENKAI, AYGAZ): commodity-price-driven margins, very
  cyclical. PE varies wildly by oil/gas cycle stage; calibration
  needs sector-specific knot density.

  SANAYI (KRDMD, EREGL, ASELS): traditional industrials, the closest
  to 'textbook' fundamentals. Phase 4.7's general fit is most
  applicable here.

  ULASIM (THYAO, PGSUS, TAVHL): airline / airport — highly leveraged,
  sensitive to fuel + FX, fundamentally different than industrials.

  TEKNOLOJI (KONTR, LOGO, PAPIL): software-style margins, asset-light,
  ROIC distribution shifted right.

  HOLDING + PERAKENDE + ENERJI + SANAYI + ULASIM + TEKNOLOJI +
  GAYRIMENKUL + SAVUNMA = 8 sector groups (matching engine/turkey_realities.py)

Phase 9 produces per-sector fits artifacts:
  reports/fa_isotonic_fits_sector_holding.json
  reports/fa_isotonic_fits_sector_perakende.json
  reports/fa_isotonic_fits_sector_enerji.json
  ... (one per sector group)

Dispatcher routes by sector_group: a holding symbol uses holding
fits, a perakende symbol uses perakende fits, etc.

THIS TURN: scaffold (sector list, path resolver, dispatcher
extension). Calibration of per-sector fits requires Phase 9 deploy
Colab notebook with broader symbol set per sector — needs more
than Phase 5's BIST30 non-bank, since each sector needs ≥10
symbols × 31 quarters ≈ 310 samples to fit reasonably.

DEPENDENCY: Phase 9 should run AFTER Phase 5 (broader sample),
so per-sector fits have enough data per sector.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("bistbull.scoring_calibrated_sectors")


# ==========================================================================
# Sector groups (matches engine/turkey_realities.py)
# ==========================================================================

SUPPORTED_SECTORS: frozenset[str] = frozenset({
    "gayrimenkul", "enerji", "ulasim", "sanayi", "savunma",
    "holding", "perakende", "banka",
    # Note: 'banka' is included for completeness but bank symbols
    # are routed via Phase 6 bank-specific fits, not sector fits.
    # 'teknoloji' may be added if/when a meaningful set of tech
    # symbols is calibrated separately; for now teknoloji symbols
    # fall under default 'sanayi' fits.
})


# ==========================================================================
# Per-sector artifact paths
# ==========================================================================

_REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def resolve_sector_fits_path(sector_group: Optional[str]) -> Optional[Path]:
    """Map sector_group → per-sector fits artifact path.

    Returns None if sector is missing or unsupported (caller falls
    back to general calibrated path).

    Path pattern:
      reports/fa_isotonic_fits_sector_<sector>.json

    e.g.  reports/fa_isotonic_fits_sector_holding.json
    """
    if not sector_group:
        return None
    sg = sector_group.strip().lower()
    if sg not in SUPPORTED_SECTORS:
        return None
    return _REPORTS_DIR / f"fa_isotonic_fits_sector_{sg}.json"


def normalize_sector(sector_group: Optional[str]) -> Optional[str]:
    """Defensive sector normalization.

    Returns the canonical sector_group string if recognized, None
    otherwise. None signals 'use general fits'.
    """
    if not sector_group:
        return None
    sg = sector_group.strip().lower()
    if sg in SUPPORTED_SECTORS:
        return sg
    return None


# ==========================================================================
# Per-sector fits loader
# ==========================================================================

def get_sector_fits(
    sector_group: Optional[str],
    force_reload: bool = False,
):
    """Load per-sector fits artifact.

    Returns dict[metric, IsotonicFit] or None if:
      - sector is unsupported
      - artifact file doesn't exist (Phase 9 deploy not done)

    Caller (sector dispatcher) falls back to general fits in either
    case.
    """
    sg = normalize_sector(sector_group)
    if sg is None:
        return None

    path = resolve_sector_fits_path(sg)
    if path is None or not path.exists():
        return None

    from engine.scoring_calibrated import _get_fits, CALIBRATED_VERSION
    cache_version = f"{CALIBRATED_VERSION}_sector_{sg}"
    return _get_fits(
        fits_path=path,
        force_reload=force_reload,
        scoring_version=cache_version,
    )


# ==========================================================================
# Sector-conditional dispatcher
# ==========================================================================

def score_dispatch_sector_aware(
    m: dict,
    sector_group: Optional[str] = None,
    scoring_version: Optional[str] = None,
    fits: Optional[dict] = None,
    symbol: Optional[str] = None,
) -> dict:
    """Phase 9: sector-aware score dispatcher.

    Tries per-sector fits first. Falls back to general path if:
      - sector unrecognized
      - per-sector artifact not committed yet
      - explicit fits kwarg passed (caller knows what they want)

    Returns same shape as score_dispatch with optional 'sector_fits'
    field marking when per-sector fits were used (for telemetry).
    """
    from engine.scoring_calibrated import (
        score_dispatch,
        score_value_calibrated, score_quality_calibrated,
        score_growth_calibrated, score_balance_calibrated,
        SUPPORTED_CALIBRATED_VERSIONS, HANDPICKED_VERSION,
        CALIBRATED_VERSION,
    )

    # Only attempt sector routing for calibrated versions
    sv = scoring_version or HANDPICKED_VERSION
    if sv not in SUPPORTED_CALIBRATED_VERSIONS:
        # V13 path — sector routing not relevant
        result = score_dispatch(
            m, sector_group=sector_group,
            scoring_version=sv,
            fits=fits, symbol=symbol,
        )
        result["sector_fits"] = False
        return result

    # If caller passed explicit fits, respect that
    if fits is not None:
        result = score_dispatch(
            m, sector_group=sector_group,
            scoring_version=sv,
            fits=fits, symbol=symbol,
        )
        result["sector_fits"] = False
        return result

    # Try per-sector fits
    sector_fits = get_sector_fits(sector_group)
    if sector_fits is not None:
        result = {
            "value":   score_value_calibrated(m, sector_fits),
            "quality": score_quality_calibrated(m, sector_fits),
            "growth":  score_growth_calibrated(m, sector_fits),
            "balance": score_balance_calibrated(m, sector_fits),
            "scoring_version": sv,
            "scoring_version_effective": f"{sv}_sector_{normalize_sector(sector_group)}",
            "sector_fits": True,
        }
        return result

    # Fall back to general dispatcher
    result = score_dispatch(
        m, sector_group=sector_group,
        scoring_version=sv,
        fits=fits, symbol=symbol,
    )
    result["sector_fits"] = False
    return result


# ==========================================================================
# Diagnostic: which sectors have fits committed?
# ==========================================================================

def get_calibrated_sectors() -> list[str]:
    """Return list of sectors that have a fits artifact on disk.

    Used by health checks and admin endpoints to report on
    Phase 9 deployment progress.
    """
    out = []
    for sg in SUPPORTED_SECTORS:
        path = resolve_sector_fits_path(sg)
        if path and path.exists():
            out.append(sg)
    return sorted(out)

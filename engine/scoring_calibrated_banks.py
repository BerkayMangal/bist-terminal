"""Phase 6 — Bank metric registry + calibrated scoring scaffold.

Banks have a structurally different financial statement schema:
the asset side is dominated by 'Krediler' (loans), 'Bankalar
Bakiyeleri' (interbank balances), 'Menkul Değerler Portföyü'
(securities portfolio); the liability side by 'Mevduatlar' (deposits).

The standard FA metrics (debt/equity, current ratio, ROIC) are
either meaningless or wildly off-scale for banks because of the
inherent leverage profile (banks are deliberately ~10x leveraged
versus ~0.5x for industrials).

This module defines bank-specific metrics, their direction
(higher is better / lower is better), and routes calibrated
scoring requests for bank symbols to bank fits.

ACTUAL FITS GENERATION: this is scaffold. The fits artifact
(reports/fa_isotonic_fits_banks.json) is produced later by a
Phase 6 Colab notebook running ingest on the 9 BIST banks with
bank-specific KAP schema parsing. Until then, bank symbols fall
back to V13 handpicked.

BANK METRICS:

  Profitability (↑ better):
    nim                Net Interest Margin (net interest income /
                       average earning assets) — bank's bread + butter
    roa_bank           Return on Assets — leveraged differently than
                       industrials, sector-specific calibration needed
    roe_bank           Return on Equity — banks target ~15%; > 20% is
                       suspicious (high leverage hiding losses)
    cost_to_income     Operating expenses / total revenue. ↓ better;
                       BIST banks typically 35-45%, > 60% problematic

  Asset Quality (↓ better):
    npl_ratio          Non-performing loans / total loans. Critical
                       indicator. Turkish banks 2018-2024 range 3-7%.
    loan_loss_provisions / total loans
                       How aggressively bank reserves for losses

  Capital Adequacy (↑ better):
    car                Capital Adequacy Ratio (Sermaye Yeterlilik Rasyosu)
                       BDDK minimum 12%, target 16%+
    tier1_ratio        Tier 1 capital ratio — pure equity buffer
    leverage_ratio     Tier 1 / total exposure (Basel III)

  Liquidity (↑ better):
    loan_to_deposit    Loans / deposits. ~95-105% is healthy. > 110%
                       indicates funding strain.
    lcr                Liquidity Coverage Ratio — Basel III metric

  Valuation (↓ better for PB-like, ↑ for div-yield-like):
    pb_bank            Price/Book — banks usually 0.5-2x, distinct
                       distribution from industrials
    pe_bank            Price/Earnings
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

log = logging.getLogger("bistbull.scoring_calibrated_banks")


# ==========================================================================
# Bank-specific metric directions
# ==========================================================================

# Direction True = higher is better (calibrated fit will be increasing)
# Direction False = lower is better (calibrated fit will be decreasing)
BANK_METRIC_DIRECTIONS: dict[str, bool] = {
    # Profitability
    "nim":                True,
    "roa_bank":           True,
    "roe_bank":           True,
    "cost_to_income":     False,  # opex/revenue ratio
    # Asset quality
    "npl_ratio":          False,
    "loan_loss_coverage": True,   # provisions / NPLs (higher = more conservative)
    # Capital adequacy
    "car":                True,
    "tier1_ratio":        True,
    "leverage_ratio":     True,
    # Liquidity
    "loan_to_deposit":    None,   # bell-curve: too low = idle capital,
                                  # too high = funding strain. Will need
                                  # special handling in Phase 6 deploy.
    "lcr":                True,
    # Valuation
    "pb_bank":            False,
    "pe_bank":            False,
}


# Subset of bank metrics suitable for direct isotonic regression.
# loan_to_deposit excluded because it's bell-shaped, not monotonic.
BANK_METRIC_KEYS_ISOTONIC: list[str] = [
    k for k, v in BANK_METRIC_DIRECTIONS.items() if v is not None
]


# ==========================================================================
# Bank fits artifact path
# ==========================================================================

DEFAULT_BANK_FITS_PATH = (
    Path(__file__).resolve().parent.parent
    / "reports" / "fa_isotonic_fits_banks.json"
)


CALIBRATED_BANK_VERSION = "calibrated_2026Q1_banks"


# ==========================================================================
# Scoring entrypoints (scaffold — pulls from generic isotonic loader)
# ==========================================================================

def get_bank_fits(force_reload: bool = False):
    """Load bank-specific fits from disk.

    Returns dict[metric, IsotonicFit] or None if not yet committed.
    Phase 6 Colab notebook produces this file. Until then, callers
    fall back to V13 handpicked for bank symbols (same as before
    Phase 6).
    """
    from engine.scoring_calibrated import _get_fits

    # Reuse the generic loader by passing an explicit path
    return _get_fits(
        fits_path=DEFAULT_BANK_FITS_PATH,
        force_reload=force_reload,
        scoring_version=CALIBRATED_BANK_VERSION,
    )


def score_bank_metric_calibrated(
    metric_key: str,
    value: Optional[float],
    fits=None,
) -> Optional[float]:
    """Score a single bank metric via its calibrated fit.

    Returns score in [5, 100] matching V13's scale, None if metric
    not in fits or value is None.
    """
    from engine.scoring_calibrated import score_metric_calibrated

    # Bank metrics may have direction flipped vs general metrics, but
    # the underlying isotonic curve handles that — caller passes the
    # bank-specific fits dict.
    if fits is None:
        fits = get_bank_fits()
        if fits is None:
            return None

    return score_metric_calibrated(metric_key, value, fits)


def is_bank_metrics_dict(m: dict) -> bool:
    """Heuristic: does this metrics dict look like bank metrics?

    Returns True if any of the bank-specific keys are present.
    Used by the dispatcher to decide whether to route to bank fits
    even when the symbol routing isn't direct.
    """
    return any(k in m for k in BANK_METRIC_KEYS_ISOTONIC)

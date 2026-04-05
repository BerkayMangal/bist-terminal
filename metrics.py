# ================================================================
# BISTBULL TERMINAL — CANONICAL METRIC MODEL
# engine/metrics.py
#
# Single source of truth for the metric pipeline output shape.
# Both providers (borsapy, yfinance) MUST produce dicts that
# conform to this schema after passing through normalize_metrics().
#
# DESIGN PRINCIPLES:
# 1. The canonical field list lives HERE, nowhere else.
# 2. normalize_metrics() fills missing keys with None — never invents values.
# 3. compute_score_coverage() tracks which FA dimensions have real data.
# 4. No consumer code changes needed — metrics remain plain dicts.
# ================================================================

from __future__ import annotations

from typing import Optional


# ================================================================
# CANONICAL FIELD REGISTRY
#
# Every field that a metric dict can contain, grouped by category.
# "required" fields must always be present (even if None).
# "optional" fields may be absent in older data but are normalized to None.
#
# This is the contract between data providers and the scoring engine.
# ================================================================

# --- Identity (always present, never None) ---
IDENTITY_FIELDS: dict[str, type] = {
    "symbol": str,
    "ticker": str,
    "name": str,
    "currency": str,
    "sector": str,
    "industry": str,
    "data_source": str,
}

# --- Market data ---
MARKET_FIELDS: list[str] = [
    "price", "market_cap",
    "pe", "pb", "ev_ebitda",
    "dividend_yield", "beta",
]

# --- Income statement (current + prev period) ---
INCOME_FIELDS: list[str] = [
    "revenue", "revenue_prev",
    "gross_profit", "gross_profit_prev",
    "operating_income",
    "ebit", "ebitda", "ebitda_prev",
    "net_income", "net_income_prev",
    "sga", "sga_prev",
]

# --- Cash flow ---
CASHFLOW_FIELDS: list[str] = [
    "operating_cf", "free_cf",
    "depreciation", "depreciation_prev",
]

# --- Balance sheet (current + prev period) ---
BALANCE_FIELDS: list[str] = [
    "total_assets", "total_assets_prev",
    "total_liabilities",
    "total_debt", "total_debt_prev",
    "cash",
    "current_assets", "current_assets_prev",
    "current_liabilities", "current_liabilities_prev",
    "working_capital",
    "retained_earnings",
    "equity",
    "receivables", "receivables_prev",
    "ppe", "ppe_prev",
]

# --- Per-share ---
PERSHARE_FIELDS: list[str] = [
    "trailing_eps", "book_value_ps",
]

# --- Computed ratios ---
RATIO_FIELDS: list[str] = [
    "roe", "roa", "roa_prev", "roic",
    "gross_margin", "gross_margin_prev",
    "operating_margin", "net_margin",
    "current_ratio", "current_ratio_prev",
    "debt_equity", "net_debt_ebitda",
    "interest_coverage",
    "fcf_yield", "fcf_margin", "cfo_to_ni",
    "revenue_growth", "eps_growth", "ebitda_growth",
    "peg", "graham_fv", "margin_safety",
    "share_change",
    "asset_turnover", "asset_turnover_prev",
    "inst_holders_pct",
    "ciro_pd",
]

# --- Legendary models (computed after metric construction) ---
LEGENDARY_FIELDS: list[str] = [
    "piotroski_f", "altman_z", "beneish_m",
]

# --- Provider-specific optional fields (not required but preserved) ---
OPTIONAL_FIELDS: list[str] = [
    "foreign_ratio", "free_float",
]

# --- Full canonical set (all numeric/optional fields) ---
ALL_METRIC_FIELDS: list[str] = (
    MARKET_FIELDS
    + INCOME_FIELDS
    + CASHFLOW_FIELDS
    + BALANCE_FIELDS
    + PERSHARE_FIELDS
    + RATIO_FIELDS
    + LEGENDARY_FIELDS
)


# ================================================================
# NORMALIZE — ensure both providers produce the same shape
# ================================================================
def normalize_metrics(m: dict) -> dict:
    """
    Ensure a metric dict has all canonical fields.

    Rules:
    - Missing numeric fields → set to None (never invent values)
    - Identity fields must already be present (not filled in here)
    - Extra provider-specific fields are preserved (not removed)
    - Original dict is NOT mutated; a new dict is returned.

    This is the single chokepoint where provider output becomes canonical.
    """
    result = dict(m)  # shallow copy — preserves extra fields

    # Fill missing canonical numeric fields with None
    for field in ALL_METRIC_FIELDS:
        if field not in result:
            result[field] = None

    return result


# ================================================================
# SCORE COVERAGE — track which FA dimensions have real data
# ================================================================

# Which raw scoring function inputs feed each FA dimension.
# If ALL listed metric keys are None, that dimension returns None
# and gets imputed to 50 in analyze_symbol.
FA_DIMENSION_INPUTS: dict[str, list[str]] = {
    "value": ["pe", "pb", "ev_ebitda", "fcf_yield", "margin_safety", "revenue", "market_cap"],
    "quality": ["roe", "roic", "net_margin"],
    "growth": ["revenue_growth", "eps_growth", "ebitda_growth", "peg"],
    "balance": ["net_debt_ebitda", "debt_equity", "current_ratio", "interest_coverage", "altman_z"],
    "earnings": ["cfo_to_ni", "fcf_margin", "beneish_m"],
    "moat": ["gross_margin", "gross_margin_prev", "roa", "roa_prev", "operating_margin", "asset_turnover", "asset_turnover_prev"],
    "capital": ["dividend_yield", "fcf_yield", "share_change", "roic", "operating_cf", "free_cf", "net_income"],
}


def compute_score_coverage(m: dict) -> dict:
    """
    For each FA scoring dimension, compute the fraction of input
    metrics that have real (non-None) data.

    Returns:
        {
            "value": {"available": 5, "total": 7, "pct": 71.4},
            "quality": {"available": 3, "total": 3, "pct": 100.0},
            ...
            "summary": {"dimensions_with_data": 6, "total_dimensions": 7,
                        "imputed_dimensions": ["growth"]},
        }
    """
    coverage: dict = {}
    imputed: list[str] = []

    for dim, keys in FA_DIMENSION_INPUTS.items():
        available = sum(1 for k in keys if m.get(k) is not None)
        total = len(keys)
        pct = round(100 * available / total, 1) if total > 0 else 0.0
        coverage[dim] = {
            "available": available,
            "total": total,
            "pct": pct,
        }
        if available == 0:
            imputed.append(dim)

    coverage["summary"] = {
        "dimensions_with_data": 7 - len(imputed),
        "total_dimensions": 7,
        "imputed_dimensions": imputed,
    }

    return coverage


def confidence_penalty_for_imputed_scores(imputed_dimensions: list[str]) -> float:
    """
    Compute a confidence reduction for imputed FA dimensions.

    Each imputed dimension reduces confidence by its FA weight × 100.
    E.g., imputed growth (weight=0.15) → -15 points.

    This is added to the existing confidence_score to ensure stocks
    with missing scoring dimensions are penalized proportionally.
    """
    from config import FA_WEIGHTS

    penalty = 0.0
    for dim in imputed_dimensions:
        weight = FA_WEIGHTS.get(dim, 0)
        penalty += weight * 100  # each imputed dim costs its full weight in confidence

    return round(penalty, 1)


# ================================================================
# FIELD PARITY CHECK — for testing and validation
# ================================================================
def check_field_parity(m: dict) -> dict:
    """
    Check which canonical fields are present/missing in a metric dict.

    Returns:
        {
            "present": ["pe", "pb", ...],
            "missing": ["share_change", ...],
            "extra": ["foreign_ratio", ...],
            "pct_present": 92.3,
        }
    """
    canonical = set(ALL_METRIC_FIELDS)
    present_canonical = [f for f in ALL_METRIC_FIELDS if f in m]
    missing = [f for f in ALL_METRIC_FIELDS if f not in m]
    extra = [f for f in m if f not in canonical and f not in IDENTITY_FIELDS]
    pct = round(100 * len(present_canonical) / len(canonical), 1) if canonical else 0.0

    return {
        "present": present_canonical,
        "missing": missing,
        "extra": extra,
        "pct_present": pct,
    }

# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# engine/bullalfa_params.py
#
# Single source of truth for every numeric heuristic in BullAlfa.
# Spec §9 mandate:
#   "All heuristic params live in a single BULLALFA_PARAMS dict
#    so v2 can override without editing logic."
#
# v1 = launch defaults (educated guesses).
# v2 = walk-forward fits, written into this dict (or its on-disk
#      JSON twin) by `research/bullalfa_walkforward.py`.
# v3 = ML refinement (optional).
#
# Every value tagged `# heuristic v1` is an educated default — NOT
# data-derived. Do NOT inline these numbers in business logic. If a
# new heuristic is needed, add it here first, then read it from here.
# ================================================================

from __future__ import annotations

from typing import Any

# ----------------------------------------------------------------
# 1. Macro-gate multipliers (spec §6)
# ----------------------------------------------------------------
# Indexed by (regime, tl_vol_bucket) → mode → multiplier.
# tl_vol_bucket: "low"  ↔ tl_vol_pct <  80
#                "high" ↔ tl_vol_pct >= 80
# A multiplier of 0.0 means the mode is disabled in that environment;
# the orchestrator must drop the signal to TOPLANIYOR or SAKİN.
_MACRO_MULT: dict[str, dict[str, dict[str, float]]] = {
    "risk_on": {
        "low":  {"HIZLI": 1.00, "SWING": 1.00, "POZİSYON": 1.00},  # heuristic v1
        "high": {"HIZLI": 0.70, "SWING": 1.00, "POZİSYON": 1.00},  # heuristic v1
    },
    "neutral": {
        "low":  {"HIZLI": 0.80, "SWING": 1.00, "POZİSYON": 1.00},  # heuristic v1
        "high": {"HIZLI": 0.80, "SWING": 1.00, "POZİSYON": 1.00},  # heuristic v1
    },
    "risk_off": {
        "low":  {"HIZLI": 0.00, "SWING": 0.60, "POZİSYON": 1.00},  # heuristic v1
        "high": {"HIZLI": 0.00, "SWING": 0.60, "POZİSYON": 1.00},  # heuristic v1
    },
}

TL_VOL_HIGH_PCT = 80.0  # heuristic v1 — boundary between low/high TL-vol buckets

# ----------------------------------------------------------------
# 2. Quality grade boundaries (spec §7)
# ----------------------------------------------------------------
# Score → grade. Inclusive lower bound.
_GRADE_BOUNDARIES: list[tuple[int, str]] = [
    (90, "A+"),
    (80, "A"),
    (70, "B"),
    (60, "C"),
    (0,  "D"),
]

# When freshness_pct < FRESHNESS_GRADE_CAP_PCT, grade is capped at "B".
FRESHNESS_GRADE_CAP_PCT = 80.0  # heuristic v1

# Mode-specific quality minimums (spec §11).
_MODE_QUALITY_MIN: dict[str, int] = {
    "POZİSYON":   70,
    "SWING":      60,
    "HIZLI":       0,
    "TOPLANIYOR":  0,
    "SAKİN":       0,
    # UZAK DUR has no quality min — it's a defensive label.
}

# ----------------------------------------------------------------
# 3. Engine 3 — volume thresholds (spec §8)
# ----------------------------------------------------------------
_E3_RVOL_THRESHOLD: dict[str, float] = {
    "HIZLI":    1.8,  # heuristic v1
    "SWING":    1.3,  # heuristic v1
    "POZİSYON": 1.0,  # heuristic v1
}

# ----------------------------------------------------------------
# 4. Engine 4 — breakout window per mode (spec §8)
# ----------------------------------------------------------------
_E4_BREAKOUT_BARS: dict[str, int] = {
    "HIZLI":    20,
    "SWING":    55,
    "POZİSYON": 126,  # ≈ 6 trading months
}

# E5 (compression): tightness as percentile of 60-bar BB-width window.
E5_BB_WIDTH_PCTILE_COMPRESS = 25       # heuristic v1
E5_ATR_TIGHTNESS_RATIO      = 0.85     # atr_today < atr_avg_20d × this; heuristic v1
E5_EXPANSION_RANGE_MULT     = 1.5      # range > atr_avg_20d × this; heuristic v1

# Sectors where E5 (compression → expansion) is skipped (spec §14).
_E5_SKIPPED_SECTORS: set[str] = {"banka", "holding", "gyo", "newly_listed", "halted"}

# E6 (pullback) parameters.
E6_EMA20_TOLERANCE_PCT = 0.02  # price within 2% of EMA20
E6_PANIC_BAR_ATR_MULT  = 2.0   # any bar with intraday range > 2×ATR disqualifies

# E7 (exhaustion dampener) penalty schedule (spec §8).
_E7_RSI_HIGH_PENALTY        = 0.15  # rsi > 70; heuristic v1
_E7_RSI_VERY_HIGH_PENALTY   = 0.20  # rsi > 80; heuristic v1
_E7_RUNUP_PENALTY           = 0.15  # return_5d > 20%; heuristic v1
_E7_VOL_FADE_PENALTY        = 0.20  # rvol_now < rvol_3d_ago × 0.7; heuristic v1
E7_RSI_HIGH_THRESHOLD       = 70.0
E7_RSI_VERY_HIGH_THRESHOLD  = 80.0
E7_RUNUP_5D_THRESHOLD       = 0.20
E7_VOL_FADE_RATIO           = 0.7
E7_PENALTY_CAP              = 0.7

# Tie-breaker between Engine 4 (breakout) and Engine 6 (pullback).
PULLBACK_TO_BREAKOUT_LOOKBACK_BARS = 3   # E4 fired ≤ this many bars ago
PULLBACK_TO_BREAKOUT_BONUS         = 5   # +5 confidence points; heuristic v1
PULLBACK_TO_BREAKOUT_CAP           = 95  # cap; heuristic v1

# ----------------------------------------------------------------
# 5. Engine score weights (spec §8)
# ----------------------------------------------------------------
# Edge score weights (sum to 1.0).
_EDGE_WEIGHTS: dict[str, float] = {
    "e2_relstr":     0.30,  # heuristic v1
    "e3_volume":     0.25,  # heuristic v1
    "e4_or_e6":      0.20,  # heuristic v1
    "e5_compress":   0.15,  # heuristic v1
    "e1_trend":      0.10,  # heuristic v1
}

# Per-mode technical-score weights. Each row sums to 1.0.
_TECH_WEIGHTS: dict[str, dict[str, float]] = {
    "HIZLI": {
        "e1_trend":   0.40,
        "e3_volume":  0.25,
        "e4_break":   0.25,
        "e5_compress":0.10,
    },  # heuristic v1
    "SWING": {
        "e1_trend":   0.30,
        "e2_relstr":  0.20,
        "e3_volume":  0.20,
        "e4_or_e6":   0.20,
        "e5_compress":0.10,
    },  # heuristic v1
    "POZİSYON": {
        "e1_trend":   0.50,
        "e2_relstr":  0.30,
        "e3_volume":  0.20,
    },  # heuristic v1
}

# ----------------------------------------------------------------
# 6. Layer 3 — combination weights (spec §9)
# ----------------------------------------------------------------
# Mode-conditional Quality × Technical × Edge mix. Each row sums to 1.0.
_COMBO_WEIGHTS: dict[str, dict[str, float]] = {
    "HIZLI":    {"quality": 0.20, "technical": 0.55, "edge": 0.25},  # heuristic v1
    "SWING":    {"quality": 0.35, "technical": 0.40, "edge": 0.25},  # heuristic v1
    "POZİSYON": {"quality": 0.55, "technical": 0.20, "edge": 0.25},  # heuristic v1
}

# Sigmoid squash for v1 confidence calibration.
SIGMOID_MIDPOINT  = 55.0   # heuristic v1
SIGMOID_STEEPNESS = 0.08   # heuristic v1

# ----------------------------------------------------------------
# 7. Layer 4 — risk frame (spec §10)
# ----------------------------------------------------------------
# ATR-multiple stop distance per mode.
_STOP_ATR_MULT: dict[str, float] = {
    "HIZLI":    1.2,  # heuristic v1
    "SWING":    1.8,  # heuristic v1
    "POZİSYON": 2.5,  # heuristic v1
}

# Maximum holding window (bars = trading days) per mode.
_MAX_HOLD_BARS: dict[str, int] = {
    "HIZLI":    5,    # 1–5 day swing
    "SWING":    20,   # ~4 weeks
    "POZİSYON": 126,  # ~6 months
}

# Trail rules per mode (Turkish UI strings).
_TRAIL_RULES: dict[str, str] = {
    "HIZLI":    "1R'da yarıyı sat, kalan için EMA10",
    "SWING":    "1R'da yarıyı sat, kalan için EMA20",
    "POZİSYON": "EMA50 altında günlük kapanış",
}

# Entry zone sized as a tight band around the latest close.
ENTRY_ZONE_LOW_MULT  = 0.995   # 50 bp below close; heuristic v1
ENTRY_ZONE_HIGH_MULT = 1.010   # 100 bp above close; heuristic v1

# Risk-frame R-targets are derived (1R, 2R, 3R) — no separate constants.
# Tolerance for invariant 5 (`target_2r ≈ entry + 2 × R` within rounding):
RISK_FRAME_R_TOLERANCE_PCT = 0.01  # 1%, heuristic v1

# ----------------------------------------------------------------
# 8. Liquidity gates (spec §11)
# ----------------------------------------------------------------
# All ADV thresholds in TL.
ADV_HARD_FLOOR_TRY      = 1_000_000     # below this → TOPLANIYOR for all actionable modes
ADV_HIZLI_FLOOR_TRY     = 5_000_000     # below this → HIZLI not allowed
ADV_LOW_LIQUIDITY_TRY   = 10_000_000    # below this → confidence × LIQUIDITY_PENALTY
LIQUIDITY_PENALTY       = 0.85          # heuristic v1

# ----------------------------------------------------------------
# 9. Session gates (spec §11)
# ----------------------------------------------------------------
SESSION_END_HIZLI_CUTOFF_MIN = 30  # mins before close at which HIZLI auto-downgrades

# ----------------------------------------------------------------
# 10. UZAK DUR forced detection (spec §11)
# ----------------------------------------------------------------
UZAK_DUR_EXHAUSTION_MIN = 0.6  # heuristic v1 — exhaustion strictly above this
# AND current bar closes below prior bar's low → forced UZAK DUR.

# ----------------------------------------------------------------
# 11. TOPLANIYOR criteria (spec §12)
# ----------------------------------------------------------------
TOPLANIYOR_BB_PCTILE         = 35     # bb_width_today < pct(60d, this)
TOPLANIYOR_RVOL_5D_LOW       = 1.05   # heuristic v1 — soft volume rise floor
TOPLANIYOR_RVOL_5D_HIGH      = 1.50   # heuristic v1 — soft volume rise ceiling
TOPLANIYOR_HIGHER_LOWS_MIN   = 3      # spec §17 example — used by accumulation_strength
TOPLANIYOR_UD_VOL_RATIO_MIN  = 1.4    # up-day vol / down-day vol over last 10 bars
TOPLANIYOR_LOOKBACK_BARS     = 10     # used for ADX-rise / higher-lows / UD-vol

# accumulation_strength coefficients (handoff §4 — flag for v2 calibration).
_ACC_STRENGTH_W: dict[str, float] = {
    "adx_rise":         25.0,  # heuristic v1
    "tightness":        30.0,  # heuristic v1
    "buying_pressure":  25.0,  # heuristic v1
    "structure":        20.0,  # heuristic v1
}
ACC_STRENGTH_BUYING_PRESSURE_NORMALISER = 0.5   # divisor — heuristic v1
ACC_STRENGTH_ADX_FLOOR                  = 5.0   # divisor floor — heuristic v1

# ----------------------------------------------------------------
# 12. Opportunity score (spec §17)
# ----------------------------------------------------------------
OPPORTUNITY_TOPLANIYOR_CAP   = 70   # capped at this for non-actionable mode
OPPORTUNITY_UZAK_DUR_FIXED   = 5    # near-bottom; visible but obviously not a buy
OPPORTUNITY_SAKIN_CAP        = 20   # quality-driven; cap of 20% × quality_score
OPPORTUNITY_SAKIN_MULT       = 0.20 # min(20, quality_score × 0.20)

# Sector-concentration banner threshold (spec §17).
SECTOR_CONCENTRATION_THRESHOLD = 5   # ≥ this many actionable signals from same sector → banner

# ----------------------------------------------------------------
# 13. Sector & universe branching (spec §14)
# ----------------------------------------------------------------
NEWLY_LISTED_THRESHOLD_DAYS = 180     # < 180 trading days history → newly_listed
NEWLY_LISTED_GRADE_CAP      = "B"     # cap quality grade
_NEWLY_LISTED_ALLOWED_MODES: set[str] = {"HIZLI", "TOPLANIYOR", "SAKİN"}
_HALTED_FORCED_MODE         = "UZAK DUR"

# Sector → benchmark index mapping (spec §14).
_SECTOR_BENCHMARK: dict[str, str] = {
    "banka":         "XBANK",
    "holding":       "XHOLD",
    "gyo":           "XGMYO",
    "sanayi":        "XU100",
    "savunma":       "XU100",
    "enerji":        "XU100",
    "perakende":     "XU100",
    "ulasim":        "XU100",
    "newly_listed":  "XU100",
    "halted":        "XU100",
}
DEFAULT_BENCHMARK = "XU100"

# ----------------------------------------------------------------
# 13b. Signal schema version (spec §19)
# ----------------------------------------------------------------
SCHEMA_VERSION = "1.4"

# Keywords used to override yfinance sector → REIT (gyo). Lowercase, substring.
_GYO_KEYWORDS: tuple[str, ...] = ("reit", "real estate", "gayrimenkul")

# ----------------------------------------------------------------
# 14. Engine-2 (relative strength) thresholds (spec §8)
# ----------------------------------------------------------------
# Score: 1.0 (rising RS), 0.5 (partial), 0.0 (failing).
# rs_short = stock_return_20d - bench_return_20d
# rs_long  = stock_return_60d - bench_return_60d
# Both > 0 → 1.0 ; one > 0 → 0.5 ; otherwise 0.0.
# These are structural rules from the spec, not numeric heuristics.

# ----------------------------------------------------------------
# 15. Data-integrity floor (spec §8 + §15)
# ----------------------------------------------------------------
MIN_TRADING_DAYS = 60   # below this → SAKİN with caveat (also informs short_history)

# Freshness floor below which we degrade to SAKİN (spec §15).
FRESHNESS_FORCE_SAKIN_PCT = 60.0

# ----------------------------------------------------------------
# 16. Cache TTLs (spec §21) — seconds
# ----------------------------------------------------------------
CACHE_TTL_TECHNICAL_SEC = 5 * 60       # 5 minutes
CACHE_TTL_QUALITY_SEC   = 24 * 60 * 60 # 1 day
CACHE_TTL_MACRO_SEC     = 15 * 60      # 15 minutes
SCAN_BATCH_REFRESH_SEC  = 5 * 60       # background batch every 5 minutes

# Default pagination for /bullalfa/scan.
SCAN_DEFAULT_PAGE_SIZE = 50

# Circuit-breaker: freeze scan after this many consecutive external failures.
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5

# ----------------------------------------------------------------
# Master export
# ----------------------------------------------------------------
BULLALFA_PARAMS: dict[str, Any] = {
    "version": "1.4",
    "phase":   "v1_heuristic",

    "macro": {
        "multipliers":      _MACRO_MULT,
        "tl_vol_high_pct":  TL_VOL_HIGH_PCT,
    },

    "quality": {
        "grade_boundaries":     _GRADE_BOUNDARIES,
        "freshness_grade_cap":  FRESHNESS_GRADE_CAP_PCT,
        "mode_quality_min":     _MODE_QUALITY_MIN,
    },

    "engines": {
        "e3_rvol_threshold":     _E3_RVOL_THRESHOLD,
        "e4_breakout_bars":      _E4_BREAKOUT_BARS,
        "e5_bb_pctile":          E5_BB_WIDTH_PCTILE_COMPRESS,
        "e5_atr_tightness":      E5_ATR_TIGHTNESS_RATIO,
        "e5_expansion_mult":     E5_EXPANSION_RANGE_MULT,
        "e5_skipped_sectors":    sorted(_E5_SKIPPED_SECTORS),
        "e6_ema20_tolerance":    E6_EMA20_TOLERANCE_PCT,
        "e6_panic_atr_mult":     E6_PANIC_BAR_ATR_MULT,
        "e7": {
            "rsi_high_threshold":      E7_RSI_HIGH_THRESHOLD,
            "rsi_very_high_threshold": E7_RSI_VERY_HIGH_THRESHOLD,
            "rsi_high_penalty":        _E7_RSI_HIGH_PENALTY,
            "rsi_very_high_penalty":   _E7_RSI_VERY_HIGH_PENALTY,
            "runup_5d_threshold":      E7_RUNUP_5D_THRESHOLD,
            "runup_penalty":           _E7_RUNUP_PENALTY,
            "vol_fade_ratio":          E7_VOL_FADE_RATIO,
            "vol_fade_penalty":        _E7_VOL_FADE_PENALTY,
            "cap":                     E7_PENALTY_CAP,
        },
        "pullback_to_breakout": {
            "lookback_bars": PULLBACK_TO_BREAKOUT_LOOKBACK_BARS,
            "bonus":         PULLBACK_TO_BREAKOUT_BONUS,
            "cap":           PULLBACK_TO_BREAKOUT_CAP,
        },
        "edge_weights":   _EDGE_WEIGHTS,
        "tech_weights":   _TECH_WEIGHTS,
    },

    "calibration": {
        "combo_weights":     _COMBO_WEIGHTS,
        "sigmoid_midpoint":  SIGMOID_MIDPOINT,
        "sigmoid_steepness": SIGMOID_STEEPNESS,
    },

    "risk_frame": {
        "stop_atr_mult":         _STOP_ATR_MULT,
        "max_hold_bars":         _MAX_HOLD_BARS,
        "trail_rules":           _TRAIL_RULES,
        "entry_zone_low_mult":   ENTRY_ZONE_LOW_MULT,
        "entry_zone_high_mult":  ENTRY_ZONE_HIGH_MULT,
        "r_tolerance_pct":       RISK_FRAME_R_TOLERANCE_PCT,
    },

    "liquidity": {
        "adv_hard_floor_try":   ADV_HARD_FLOOR_TRY,
        "adv_hizli_floor_try":  ADV_HIZLI_FLOOR_TRY,
        "adv_low_floor_try":    ADV_LOW_LIQUIDITY_TRY,
        "low_liquidity_penalty": LIQUIDITY_PENALTY,
    },

    "session": {
        "hizli_cutoff_min": SESSION_END_HIZLI_CUTOFF_MIN,
    },

    "uzak_dur": {
        "exhaustion_min": UZAK_DUR_EXHAUSTION_MIN,
    },

    "toplaniyor": {
        "bb_pctile":           TOPLANIYOR_BB_PCTILE,
        "rvol_5d_low":         TOPLANIYOR_RVOL_5D_LOW,
        "rvol_5d_high":        TOPLANIYOR_RVOL_5D_HIGH,
        "higher_lows_min":     TOPLANIYOR_HIGHER_LOWS_MIN,
        "ud_vol_ratio_min":    TOPLANIYOR_UD_VOL_RATIO_MIN,
        "lookback_bars":       TOPLANIYOR_LOOKBACK_BARS,
        "accumulation_strength_weights": _ACC_STRENGTH_W,
        "buying_pressure_normaliser":    ACC_STRENGTH_BUYING_PRESSURE_NORMALISER,
        "adx_floor":                     ACC_STRENGTH_ADX_FLOOR,
    },

    "opportunity": {
        "toplaniyor_cap":   OPPORTUNITY_TOPLANIYOR_CAP,
        "uzak_dur_fixed":   OPPORTUNITY_UZAK_DUR_FIXED,
        "sakin_cap":        OPPORTUNITY_SAKIN_CAP,
        "sakin_mult":       OPPORTUNITY_SAKIN_MULT,
        "concentration_threshold": SECTOR_CONCENTRATION_THRESHOLD,
    },

    "sector": {
        "newly_listed_days":         NEWLY_LISTED_THRESHOLD_DAYS,
        "newly_listed_grade_cap":    NEWLY_LISTED_GRADE_CAP,
        "newly_listed_allowed_modes": sorted(_NEWLY_LISTED_ALLOWED_MODES),
        "halted_forced_mode":        _HALTED_FORCED_MODE,
        "benchmark_map":             dict(_SECTOR_BENCHMARK),
        "default_benchmark":         DEFAULT_BENCHMARK,
        "gyo_keywords":              list(_GYO_KEYWORDS),
    },

    "data_integrity": {
        "min_trading_days":          MIN_TRADING_DAYS,
        "freshness_force_sakin_pct": FRESHNESS_FORCE_SAKIN_PCT,
    },

    "cache": {
        "technical_ttl_sec":  CACHE_TTL_TECHNICAL_SEC,
        "quality_ttl_sec":    CACHE_TTL_QUALITY_SEC,
        "macro_ttl_sec":      CACHE_TTL_MACRO_SEC,
        "scan_refresh_sec":   SCAN_BATCH_REFRESH_SEC,
        "scan_page_size":     SCAN_DEFAULT_PAGE_SIZE,
        "circuit_breaker_threshold": CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    },
}


# ================================================================
# Typed accessors — use these in business logic
# ================================================================

def macro_multiplier(regime: str, tl_vol_pct: float, mode: str) -> float:
    """Return the macro multiplier for (regime, tl_vol_pct, mode).

    Returns 1.0 for non-actionable modes (TOPLANIYOR / SAKİN / UZAK DUR);
    returns 0.0 if the actionable mode is disabled in this regime.
    """
    if mode in {"TOPLANIYOR", "SAKİN", "UZAK DUR"}:
        return 1.0
    bucket = "high" if tl_vol_pct >= TL_VOL_HIGH_PCT else "low"
    regime_table = _MACRO_MULT.get(regime) or _MACRO_MULT["neutral"]
    bucket_table = regime_table.get(bucket, {})
    return float(bucket_table.get(mode, 1.0))


def grade_from_score(score: float) -> str:
    """Map a 0–100 quality score to its letter grade.

    `_GRADE_BOUNDARIES` is sorted high → low; first hit wins.
    """
    s = max(0.0, min(100.0, float(score)))
    for floor, grade in _GRADE_BOUNDARIES:
        if s >= floor:
            return grade
    return "D"


def quality_min_for_mode(mode: str) -> int:
    """Minimum quality score required to qualify for `mode`. 0 means no gate."""
    return _MODE_QUALITY_MIN.get(mode, 0)


def rvol_threshold(mode: str) -> float | None:
    """E3 — required relative-volume floor for an actionable mode."""
    return _E3_RVOL_THRESHOLD.get(mode)


def breakout_bars(mode: str) -> int | None:
    """E4 — lookback window for the n-day high check, per mode."""
    return _E4_BREAKOUT_BARS.get(mode)


def is_e5_skipped(sector_group: str) -> bool:
    """E5 (compression → expansion) is skipped for these sector groups."""
    return sector_group in _E5_SKIPPED_SECTORS


def stop_atr_mult(mode: str) -> float | None:
    """ATR-multiple stop distance for an actionable mode."""
    return _STOP_ATR_MULT.get(mode)


def max_hold_bars(mode: str) -> int | None:
    """Maximum holding window (trading bars) for an actionable mode."""
    return _MAX_HOLD_BARS.get(mode)


def trail_rule(mode: str) -> str | None:
    """Trail-stop rule (Turkish UI string) for an actionable mode."""
    return _TRAIL_RULES.get(mode)


def benchmark_for_sector(sector_group: str) -> str:
    """Default benchmark index symbol for `sector_group`. Falls back to XU100."""
    return _SECTOR_BENCHMARK.get(sector_group, DEFAULT_BENCHMARK)


def gyo_keywords() -> tuple[str, ...]:
    """Lowercase substrings used to override yfinance sector → 'gyo'."""
    return _GYO_KEYWORDS


def newly_listed_allowed_modes() -> frozenset[str]:
    """Modes available for stocks with < NEWLY_LISTED_THRESHOLD_DAYS history."""
    return frozenset(_NEWLY_LISTED_ALLOWED_MODES)


def halted_forced_mode() -> str:
    """Mode forced when a stock is halted today."""
    return _HALTED_FORCED_MODE


# ================================================================
# Self-check at import time — fail fast on weight tables that
# don't sum to 1.0 (one of the §22 calibration tests). This is
# a defensive guard, not a substitute for the unit test.
# ================================================================

def _validate_weight_tables() -> None:
    eps = 1e-9
    for mode, weights in _COMBO_WEIGHTS.items():
        s = sum(weights.values())
        if abs(s - 1.0) > eps:
            raise ValueError(f"BULLALFA_PARAMS combo_weights[{mode}] sums to {s}, not 1.0")
    for mode, weights in _TECH_WEIGHTS.items():
        s = sum(weights.values())
        if abs(s - 1.0) > eps:
            raise ValueError(f"BULLALFA_PARAMS tech_weights[{mode}] sums to {s}, not 1.0")
    edge_sum = sum(_EDGE_WEIGHTS.values())
    if abs(edge_sum - 1.0) > eps:
        raise ValueError(f"BULLALFA_PARAMS edge_weights sums to {edge_sum}, not 1.0")
    acc_sum = sum(_ACC_STRENGTH_W.values())
    if abs(acc_sum - 100.0) > eps:
        raise ValueError(
            f"BULLALFA_PARAMS accumulation_strength weights sum to {acc_sum}, not 100.0"
        )


_validate_weight_tables()


__all__ = [
    "BULLALFA_PARAMS",
    "macro_multiplier",
    "grade_from_score",
    "quality_min_for_mode",
    "rvol_threshold",
    "breakout_bars",
    "is_e5_skipped",
    "stop_atr_mult",
    "max_hold_bars",
    "trail_rule",
    "benchmark_for_sector",
    "gyo_keywords",
    "newly_listed_allowed_modes",
    "halted_forced_mode",
    # Module-level constants useful for tests / sibling modules:
    "TL_VOL_HIGH_PCT",
    "FRESHNESS_GRADE_CAP_PCT",
    "FRESHNESS_FORCE_SAKIN_PCT",
    "MIN_TRADING_DAYS",
    "SCHEMA_VERSION",
    "ENTRY_ZONE_LOW_MULT",
    "ENTRY_ZONE_HIGH_MULT",
    "RISK_FRAME_R_TOLERANCE_PCT",
    "ADV_HARD_FLOOR_TRY",
    "ADV_HIZLI_FLOOR_TRY",
    "ADV_LOW_LIQUIDITY_TRY",
    "LIQUIDITY_PENALTY",
    "SESSION_END_HIZLI_CUTOFF_MIN",
    "UZAK_DUR_EXHAUSTION_MIN",
    "TOPLANIYOR_BB_PCTILE",
    "TOPLANIYOR_RVOL_5D_LOW",
    "TOPLANIYOR_RVOL_5D_HIGH",
    "TOPLANIYOR_HIGHER_LOWS_MIN",
    "TOPLANIYOR_UD_VOL_RATIO_MIN",
    "TOPLANIYOR_LOOKBACK_BARS",
    "OPPORTUNITY_TOPLANIYOR_CAP",
    "OPPORTUNITY_UZAK_DUR_FIXED",
    "OPPORTUNITY_SAKIN_CAP",
    "OPPORTUNITY_SAKIN_MULT",
    "SECTOR_CONCENTRATION_THRESHOLD",
    "NEWLY_LISTED_THRESHOLD_DAYS",
    "NEWLY_LISTED_GRADE_CAP",
    "DEFAULT_BENCHMARK",
    "PULLBACK_TO_BREAKOUT_LOOKBACK_BARS",
    "PULLBACK_TO_BREAKOUT_BONUS",
    "PULLBACK_TO_BREAKOUT_CAP",
    "SIGMOID_MIDPOINT",
    "SIGMOID_STEEPNESS",
    "E5_BB_WIDTH_PCTILE_COMPRESS",
    "E5_ATR_TIGHTNESS_RATIO",
    "E5_EXPANSION_RANGE_MULT",
    "E6_EMA20_TOLERANCE_PCT",
    "E6_PANIC_BAR_ATR_MULT",
    "E7_RSI_HIGH_THRESHOLD",
    "E7_RSI_VERY_HIGH_THRESHOLD",
    "E7_RUNUP_5D_THRESHOLD",
    "E7_VOL_FADE_RATIO",
    "E7_PENALTY_CAP",
    "ACC_STRENGTH_BUYING_PRESSURE_NORMALISER",
    "ACC_STRENGTH_ADX_FLOOR",
    "CACHE_TTL_TECHNICAL_SEC",
    "CACHE_TTL_QUALITY_SEC",
    "CACHE_TTL_MACRO_SEC",
    "SCAN_BATCH_REFRESH_SEC",
    "SCAN_DEFAULT_PAGE_SIZE",
    "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
]

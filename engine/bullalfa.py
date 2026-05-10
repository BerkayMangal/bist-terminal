# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# engine/bullalfa.py
#
# Orchestrator. ALL CALLS GO HERE.
#
# Composes the 5 layers + 2 cross-cutting concerns + ranking layer
# defined in spec §3:
#
#   Layer 0 — Macro gate            (§6)
#   Layer 1 — Quality Surface       (§7)
#   Layer 2 — Technical Engines     (§8)
#   Layer 3 — Calibration           (§9)
#   Layer 4 — Risk Frame            (§10)
#   Cross-cutting — Sector / Universe Branching (§14)
#   Cross-cutting — Degradation     (§15)
#   Ranking — Opportunity Score     (§17)
#
# Public entry point:
#   build_bullalfa_signal(...) → dict matching §19 BullAlfaSignal schema
#
# Out-of-scope modules (per handoff §2):
#   engine/verdict.py, engine/scoring.py, engine/scoring_calibrated.py,
#   engine/scoring_v11.py, engine/aggregation.py, engine/labels.py,
#   engine/bullwatch.py, api/bullwatch.py, engine/technical.py
# This module CALLS them but does NOT modify them.
# ================================================================

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import pandas as pd

# --- in-scope BullAlfa modules -----------------------------------
from engine.bullalfa_degrade import (
    DegradationLog,
    DegradeAction,
    DegradeCode,
    rule_for,
)
from engine.bullalfa_params import (
    ADV_HARD_FLOOR_TRY,
    ADV_HIZLI_FLOOR_TRY,
    ADV_LOW_LIQUIDITY_TRY,
    BULLALFA_PARAMS,
    FRESHNESS_FORCE_SAKIN_PCT,
    FRESHNESS_GRADE_CAP_PCT,
    LIQUIDITY_PENALTY,
    MIN_TRADING_DAYS,
    SCHEMA_VERSION,
    SESSION_END_HIZLI_CUTOFF_MIN,
    TL_VOL_HIGH_PCT,
    UZAK_DUR_EXHAUSTION_MIN,
    grade_from_score,
    macro_multiplier,
    quality_min_for_mode,
)
from features.bullalfa_calibration import (
    calibration_phase,
    compute_confidence,
)
from features.bullalfa_features import (
    EngineInputs,
    build_engine_inputs,
    compute_engines,
    detect_pullback_to_breakout,
)
from features.bullalfa_ranking import opportunity_score
from features.bullalfa_risk import try_build_risk_frame
from features.bullalfa_risk import DOWNGRADE_CAVEAT_TR as _RISK_FRAME_INVALID_CAVEAT_TR
from features.bullalfa_sector import (
    SectorContext,
    is_newly_listed,
    resolve_sector_context,
)
from features.bullalfa_toplaniyor import (
    ToplaniyorAssessment,
    compute_accumulation_strength,
    evaluate_toplaniyor,
)
from features.bullalfa_why_now import why_now

# --- out-of-scope modules — CALL ONLY, NEVER MODIFY ---------------
# Wrapped in try/except at every callsite per §15.
try:
    from engine.scoring import (  # type: ignore
        compute_fa_pure,
        score_balance,
        score_capital,
        score_earnings,
        score_growth,
        score_moat,
        score_quality,
        score_value,
    )
except Exception:  # pragma: no cover — imports must succeed in v1.4
    compute_fa_pure = None  # type: ignore[assignment]

try:
    from engine.data_quality import assess_data_quality  # type: ignore
except Exception:  # pragma: no cover
    assess_data_quality = None  # type: ignore[assignment]

try:
    from engine.macro_decision import compute_regime  # type: ignore
except Exception:  # pragma: no cover
    compute_regime = None  # type: ignore[assignment]

try:
    from utils.market_status import get_market_status  # type: ignore
except Exception:  # pragma: no cover
    get_market_status = None  # type: ignore[assignment]

log = logging.getLogger("bistbull.bullalfa")


# ----------------------------------------------------------------
# Constants used across the orchestrator
# ----------------------------------------------------------------

_ACTIONABLE_MODES: frozenset[str] = frozenset({"HIZLI", "SWING", "POZİSYON"})

# Mode priority per §11 — UZAK DUR > HIZLI > SWING > POZİSYON > TOPLANIYOR > SAKİN.
_MODE_PRIORITY: tuple[str, ...] = (
    "UZAK DUR", "HIZLI", "SWING", "POZİSYON", "TOPLANIYOR", "SAKİN",
)

# Critical-fields proxy used to derive a freshness percentage from
# `assess_data_quality` until proper timestamp-based freshness lands.
# The 5 fields below mirror data_quality._MISSING_CRITICAL.
_CRITICAL_FIELD_COUNT = 5


# ================================================================
# Layer 0 — Macro gate
# ================================================================

@dataclass(frozen=True)
class _MacroState:
    regime: str          # "risk_on" | "neutral" | "risk_off"
    tl_vol_pct: float    # 0–100; 50.0 used as neutral fallback
    available: bool      # False → orchestrator records macro_unavailable


def _resolve_macro_state(
    macro_result: Optional[Mapping[str, Any]],
    log_obj: DegradationLog,
) -> _MacroState:
    """Extract regime + tl_vol_pct from a macro_result dict.

    Spec §6 calls `current_regime()` and `tl_volatility_percentile(252)`.
    Neither exists in the codebase — what exists is
    `engine.macro_decision.compute_regime(inputs)` returning a
    `RegimeResult` dataclass with uppercase regime strings. Callers
    pass either:
      - a RegimeResult.to_dict() result, OR
      - a manually-constructed dict with keys {"regime", "tl_vol_pct"}.

    Missing or unparseable input → records `macro_unavailable` and
    returns the §15 "assume_neutral" default (regime=neutral,
    tl_vol_pct=50, multipliers all 1.0 except the documented neutral
    HIZLI penalty).
    """
    if not macro_result:
        log_obj.record(DegradeCode.MACRO_UNAVAILABLE)
        return _MacroState(regime="neutral", tl_vol_pct=50.0, available=False)

    raw_regime = macro_result.get("regime") or macro_result.get("Regime")
    if not isinstance(raw_regime, str):
        log_obj.record(DegradeCode.MACRO_UNAVAILABLE)
        return _MacroState(regime="neutral", tl_vol_pct=50.0, available=False)

    # Normalize: spec uses lowercase, RegimeResult uses uppercase.
    norm = raw_regime.strip().lower().replace(" ", "_").replace("-", "_")
    if norm not in {"risk_on", "neutral", "risk_off"}:
        log_obj.record(DegradeCode.MACRO_UNAVAILABLE)
        return _MacroState(regime="neutral", tl_vol_pct=50.0, available=False)

    tl_vol = macro_result.get("tl_vol_pct")
    if tl_vol is None:
        # Soft-fail: we have a regime but not a TL-vol percentile.
        # Default to 50 (low bucket) and DO NOT log macro_unavailable —
        # the regime alone is informative enough for v1.
        tl_vol_val = 50.0
    else:
        try:
            tl_vol_val = float(tl_vol)
        except (TypeError, ValueError):
            tl_vol_val = 50.0

    return _MacroState(regime=norm, tl_vol_pct=tl_vol_val, available=True)


def _macro_block(state: _MacroState) -> dict[str, Any]:
    """Produce the §19 `macro` block dict."""
    # Use HIZLI to surface the most-aggressive mode multiplier in the UI.
    mult = macro_multiplier(state.regime, state.tl_vol_pct, "HIZLI")
    return {
        "regime":           state.regime,
        "tl_vol_pct":       round(state.tl_vol_pct, 2),
        "multiplier":       round(mult, 4),
        "hizli_disabled":   mult == 0.0,
    }


# ================================================================
# Layer 1 — Quality surface
# ================================================================

@dataclass(frozen=True)
class _QualitySurface:
    score:          int                 # 0–100
    grade:          str                 # "A+" | "A" | "B" | "C" | "D"
    grade_capped:   bool
    freshness_pct:  float               # 0–100
    tags:           dict[str, Any]


def _scores_dict_from_metrics(metrics: Mapping[str, Any], sector_group: str) -> dict[str, float]:
    """Compose the `scores` dict that engine.scoring.compute_fa_pure expects.

    Defensive: any individual score that throws is replaced with 50
    (neutral). This honors §15 "macro_unavailable / aggregation_failed"
    pattern but at finer granularity — a single broken dimension
    shouldn't sink the whole quality calculation.
    """
    scores: dict[str, float] = {}
    pairs: tuple[tuple[str, Any], ...] = (
        ("quality",  score_quality),
        ("value",    score_value),
        ("growth",   score_growth),
        ("balance",  score_balance),
        ("earnings", score_earnings),
        ("moat",     score_moat),
        ("capital",  score_capital),
    )
    for key, fn in pairs:
        if fn is None:
            scores[key] = 50.0
            continue
        try:
            v = fn(dict(metrics), sector_group) if key in {"quality", "value", "growth", "balance"} \
                else fn(dict(metrics))
            scores[key] = float(v) if v is not None else 50.0
        except Exception:
            scores[key] = 50.0
    return scores


def _derive_freshness_pct(metrics: Mapping[str, Any]) -> float:
    """Derive a freshness percentage from `assess_data_quality`.

    Spec §7 calls `freshness_pct(metrics)` directly; that function
    doesn't exist in the codebase (Q2 from the Milestone A report).
    Until proper timestamp-based freshness lands, we proxy via the
    completeness of the critical-fields set used by data_quality:
    a fresher metrics dict has fewer missing critical fields.

    Returns a value in [0, 100]. On exception, returns 100.0
    (best-case) — failure here must NOT pull a stock below the
    `FRESHNESS_FORCE_SAKIN_PCT` floor by accident; that has to be
    surfaced via the explicit `aggregation_failed` code instead.
    """
    if assess_data_quality is None:
        return 100.0
    try:
        info = assess_data_quality(dict(metrics)) or {}
    except Exception:
        return 100.0
    missing = int(info.get("missing_count", 0) or 0)
    pct = max(0.0, (_CRITICAL_FIELD_COUNT - missing) / _CRITICAL_FIELD_COUNT * 100.0)
    return round(pct, 2)


def _quality_tags(metrics: Mapping[str, Any], score: int) -> dict[str, Any]:
    """Compose the carry-over tag dict per §19 `quality.tags`.

    All tags are NULLABLE in the schema — a metrics dict without the
    Buffett/Graham fields just gets `null` for those slots.
    """
    # Kalite tag — bucketed off the score (matches §7 "Kalite: GÜÇLÜ/ORTA/ZAYIF").
    if score >= 75:
        kalite = "GÜÇLÜ"
    elif score >= 55:
        kalite = "ORTA"
    elif score > 0:
        kalite = "ZAYIF"
    else:
        kalite = None

    # Value bucket — read straight from metrics if the upstream pipeline
    # has already computed it; otherwise leave null and let v2 fill in.
    val = metrics.get("value_label") if isinstance(metrics.get("value_label"), str) else None

    buffett = metrics.get("buffett_label") if isinstance(metrics.get("buffett_label"), str) else None
    graham  = metrics.get("graham_label")  if isinstance(metrics.get("graham_label"),  str) else None

    return {"kalite": kalite, "value": val, "buffett": buffett, "graham": graham}


def _compute_quality_surface(
    metrics: Optional[Mapping[str, Any]],
    sector_group: str,
    log_obj: DegradationLog,
) -> Optional[_QualitySurface]:
    """Build the §7 quality surface or record an `aggregation_failed`.

    Returns None when computation fails; the orchestrator forces
    SAKİN per §15 in that case.
    """
    if metrics is None or compute_fa_pure is None:
        log_obj.record(DegradeCode.AGGREGATION_FAILED)
        return None

    try:
        scores = _scores_dict_from_metrics(metrics, sector_group)
        fa_pure = float(compute_fa_pure(scores))           # 1–99
    except Exception as exc:
        log.warning("compute_fa_pure failed: %s", exc)
        log_obj.record(DegradeCode.AGGREGATION_FAILED)
        return None

    fresh = _derive_freshness_pct(metrics)

    # Apply freshness penalty per §7. Spec calls this the
    # `freshness_penalty(metrics)` factor; we proxy it as fresh/100
    # so a 100%-complete metrics dict gets no penalty.
    quality_score_raw = fa_pure * (fresh / 100.0)
    quality_score = int(round(max(0.0, min(100.0, quality_score_raw))))

    grade = grade_from_score(quality_score)
    grade_capped = False

    # §7: "if fresh < 80 and grade in ('A+', 'A') → grade='B', capped"
    if fresh < FRESHNESS_GRADE_CAP_PCT and grade in {"A+", "A"}:
        grade = "B"
        grade_capped = True

    # Force SAKİN if freshness is below the hard floor.
    if fresh < FRESHNESS_FORCE_SAKIN_PCT:
        log_obj.record(DegradeCode.FRESHNESS_BELOW_60)

    return _QualitySurface(
        score=quality_score,
        grade=grade,
        grade_capped=grade_capped,
        freshness_pct=round(fresh, 2),
        tags=_quality_tags(metrics, quality_score),
    )


def _quality_block(q: Optional[_QualitySurface]) -> dict[str, Any]:
    if q is None:
        # Degraded — emit a 0-score, D-grade placeholder. Schema §19
        # requires the keys to be present even on failure paths.
        return {
            "score":         0,
            "grade":         "D",
            "grade_capped":  False,
            "freshness_pct": 0.0,
            "tags":          {"kalite": None, "value": None, "buffett": None, "graham": None},
        }
    return {
        "score":         q.score,
        "grade":         q.grade,
        "grade_capped":  q.grade_capped,
        "freshness_pct": q.freshness_pct,
        "tags":          dict(q.tags),
    }


# ================================================================
# Layer 2 — Technical engines (composed via features.bullalfa_features)
# ================================================================

def _compute_engine_results_per_mode(
    inp: EngineInputs,
    short_history: bool,
) -> dict[str, dict[str, Any]]:
    """Run engines for each candidate mode that the trading-history
    floor (`MIN_TRADING_DAYS`) and short_history flag allow.

    Returns: {"HIZLI": {...}, "SWING": {...}, "POZİSYON": {...}}.
    Modes excluded by short_history get an empty dict so the caller
    can short-circuit.
    """
    out: dict[str, dict[str, Any]] = {}
    for mode in ("HIZLI", "SWING", "POZİSYON"):
        if short_history and mode in {"SWING", "POZİSYON"}:
            out[mode] = {}
            continue
        out[mode] = compute_engines(inp, mode)
    return out


# ----------------------------------------------------------------
# Edge / Tech score helpers
# ----------------------------------------------------------------

def _edge_score(engines: Mapping[str, Any]) -> float:
    """§8 edge score, scaled to 0–100. Per spec:
        0.30·E2 + 0.25·E3 + 0.20·(E4 OR E6) + 0.15·E5 + 0.10·E1
        × (1 − exhaustion)
    All E_i in 0..1.
    """
    w = BULLALFA_PARAMS["engines"]["edge_weights"]
    e1 = float(engines.get("e1_trend", 0))
    e2 = float(engines.get("e2_relstr", {}).get("score", 0.0))
    e3 = 1.0 if engines.get("e3_volume", {}).get("passed") else 0.0
    e4_type = engines.get("e4_breakout", {}).get("type")
    e4 = 1.0 if e4_type else 0.0
    e6 = 1.0 if engines.get("e6_pullback") else 0.0
    e4_or_e6 = max(e4, e6)
    e5 = 1.0 if engines.get("e5_compression", {}).get("expanded") else 0.0
    exh = float(engines.get("e7_exhaustion", 0.0))

    raw = (
        w["e2_relstr"]   * e2
        + w["e3_volume"]   * e3
        + w["e4_or_e6"]    * e4_or_e6
        + w["e5_compress"] * e5
        + w["e1_trend"]    * e1
    )
    return max(0.0, min(100.0, 100.0 * raw * (1.0 - exh)))


def _tech_score(engines: Mapping[str, Any], mode: str) -> float:
    """§8 mode-specific technical score, 0–100."""
    w = BULLALFA_PARAMS["engines"]["tech_weights"].get(mode)
    if not w:
        return 0.0
    e1 = float(engines.get("e1_trend", 0))
    e2 = float(engines.get("e2_relstr", {}).get("score", 0.0))
    e3 = 1.0 if engines.get("e3_volume", {}).get("passed") else 0.0
    e4 = 1.0 if engines.get("e4_breakout", {}).get("type") else 0.0
    e6 = 1.0 if engines.get("e6_pullback") else 0.0
    e4_or_e6 = max(e4, e6)
    e5 = 1.0 if engines.get("e5_compression", {}).get("expanded") else 0.0

    raw = (
        w.get("e1_trend",     0) * e1
        + w.get("e2_relstr",    0) * e2
        + w.get("e3_volume",    0) * e3
        + w.get("e4_break",     0) * e4
        + w.get("e4_or_e6",     0) * e4_or_e6
        + w.get("e5_compress",  0) * e5
    )
    return max(0.0, min(100.0, 100.0 * raw))


# ----------------------------------------------------------------
# Mode-condition predicates per §11
# ----------------------------------------------------------------

def _hizli_conditions_met(
    eng: Mapping[str, Any],
    macro: _MacroState,
    adv_20d: Optional[float],
) -> bool:
    """Macro allows + E1[HIZLI] + E3[>1.8] + (E4[20d] OR E5 expansion)
    + exhaustion ≤ 0.5 + ADV ≥ 5M."""
    if macro_multiplier(macro.regime, macro.tl_vol_pct, "HIZLI") <= 0.0:
        return False
    if not eng.get("e1_trend"):
        return False
    if not eng.get("e3_volume", {}).get("passed"):
        return False
    breakout_ok = eng.get("e4_breakout", {}).get("type") == "20d"
    expansion_ok = bool(eng.get("e5_compression", {}).get("expanded"))
    if not (breakout_ok or expansion_ok):
        return False
    if float(eng.get("e7_exhaustion", 0.0)) > 0.5:
        return False
    if adv_20d is None or float(adv_20d) < ADV_HIZLI_FLOOR_TRY:
        return False
    return True


def _swing_conditions_met(
    eng: Mapping[str, Any],
    quality_score: int,
    adv_20d: Optional[float],
) -> bool:
    """E1[SWING stack] + E2 + E3[>1.3] + (E4[55d] OR E6 pullback) + ADV ≥ 5M
    + quality ≥ 60.
    """
    if quality_score < quality_min_for_mode("SWING"):
        return False
    if not eng.get("e1_trend"):
        return False
    if float(eng.get("e2_relstr", {}).get("score", 0.0)) <= 0.0:
        return False
    if not eng.get("e3_volume", {}).get("passed"):
        return False
    breakout_55 = eng.get("e4_breakout", {}).get("type") == "55d"
    pullback = bool(eng.get("e6_pullback"))
    if not (breakout_55 or pullback):
        return False
    if adv_20d is None or float(adv_20d) < ADV_HIZLI_FLOOR_TRY:
        return False
    return True


def _pozisyon_conditions_met(
    eng: Mapping[str, Any],
    quality_score: int,
    inp: EngineInputs,
    adv_20d: Optional[float],
) -> bool:
    """E1[POZİSYON] + E2[60d positive] + ADV ≥ 5M + quality ≥ 70."""
    if quality_score < quality_min_for_mode("POZİSYON"):
        return False
    if not eng.get("e1_trend"):
        return False
    rs_long = inp.return_60d
    bench_60 = inp.bench_return_60d
    if rs_long is None:
        return False
    # If bench is missing, "60d positive" reduces to stock 60d positive.
    bench_val = bench_60 if bench_60 is not None else 0.0
    if (rs_long - bench_val) <= 0.0:
        return False
    if adv_20d is None or float(adv_20d) < ADV_HIZLI_FLOOR_TRY:
        return False
    return True


# ----------------------------------------------------------------
# UZAK DUR (§11 — exhaustion > 0.6 AND close < prior_low)
# ----------------------------------------------------------------

def _uzak_dur_forced(eng: Mapping[str, Any], inp: EngineInputs) -> bool:
    """Forced UZAK DUR when exhaustion strictly above the floor AND
    the current bar closes below the prior bar's low."""
    exh = float(eng.get("e7_exhaustion", 0.0))
    if exh <= UZAK_DUR_EXHAUSTION_MIN:
        return False
    if inp.price is None or inp.prior_low is None:
        return False
    return float(inp.price) < float(inp.prior_low)


# ================================================================
# Liquidity gates (§11)
# ================================================================

@dataclass(frozen=True)
class _LiquidityGateResult:
    mode:               str
    confidence_mult:    float
    downgrade_reason:   Optional[str]
    penalty_applied:    bool


def _apply_liquidity_gates(
    *,
    mode: str,
    adv_20d_try: Optional[float],
    swing_eligible: bool,
) -> _LiquidityGateResult:
    """Run the §11 liquidity gates AFTER mode resolution.

    Order matters:
      1. ADV < 1M → all actionable modes → TOPLANIYOR
      2. ADV < 5M and HIZLI → SWING (if eligible) else TOPLANIYOR
      3. ADV < 10M → confidence × LIQUIDITY_PENALTY
    """
    if adv_20d_try is None:
        return _LiquidityGateResult(
            mode=mode, confidence_mult=1.0,
            downgrade_reason=None, penalty_applied=False,
        )

    adv = float(adv_20d_try)

    if adv < ADV_HARD_FLOOR_TRY and mode in _ACTIONABLE_MODES:
        return _LiquidityGateResult(
            mode="TOPLANIYOR", confidence_mult=1.0,
            downgrade_reason="günlük hacim 1M TL altı",
            penalty_applied=False,
        )

    if adv < ADV_HIZLI_FLOOR_TRY and mode == "HIZLI":
        new_mode = "SWING" if swing_eligible else "TOPLANIYOR"
        return _LiquidityGateResult(
            mode=new_mode, confidence_mult=1.0,
            downgrade_reason="günlük hacim 5M TL altı, HIZLI uygun değil",
            penalty_applied=False,
        )

    if adv < ADV_LOW_LIQUIDITY_TRY:
        return _LiquidityGateResult(
            mode=mode, confidence_mult=LIQUIDITY_PENALTY,
            downgrade_reason=None, penalty_applied=True,
        )

    return _LiquidityGateResult(
        mode=mode, confidence_mult=1.0,
        downgrade_reason=None, penalty_applied=False,
    )


# ================================================================
# Session gate (§11)
# ================================================================

def _resolve_session_state(
    market_status: Optional[Mapping[str, Any]],
) -> tuple[bool, Optional[int]]:
    """Return (is_open, minutes_to_close_or_None).

    Spec §11 imports `is_market_open()` and `minutes_to_close()` from
    `utils.market_status`; neither exists. What exists is
    `get_market_status()` returning a status-dict. We adapt:
    - `is_open ← (status == "open")`
    - `minutes_to_close` derived from BIST 18:00 close time + IST hour
      string in the dict; if unavailable, return None and the gate
      will be conservatively skipped (HIZLI not auto-downgraded).
    """
    if not market_status:
        return False, None
    status = str(market_status.get("status", "")).lower()
    is_open = status == "open"
    if not is_open:
        return is_open, None

    ist_time = str(market_status.get("ist_time", ""))
    if ":" not in ist_time:
        return is_open, None
    try:
        hh, mm = ist_time.split(":")[:2]
        h = int(hh); m = int(mm)
    except Exception:
        return is_open, None

    # BIST closes at 18:00 IST on full days.
    minutes_now = h * 60 + m
    if market_status.get("half_day"):
        minutes_close = 12 * 60 + 30
    else:
        minutes_close = 18 * 60
    return is_open, max(0, minutes_close - minutes_now)


def _apply_session_gate(
    *,
    mode: str,
    market_status: Optional[Mapping[str, Any]],
) -> tuple[str, Optional[str]]:
    """If HIZLI within `SESSION_END_HIZLI_CUTOFF_MIN` of close → TOPLANIYOR."""
    if mode != "HIZLI":
        return mode, None
    is_open, mtc = _resolve_session_state(market_status)
    if not is_open or mtc is None:
        return mode, None
    if mtc < SESSION_END_HIZLI_CUTOFF_MIN:
        return "TOPLANIYOR", "seans bitimine 30dk'dan az"
    return mode, None


# ================================================================
# §16 warnings hygiene
# ================================================================

def _warnings(
    *,
    mode: str,
    quality_grade: str,
    freshness_pct: float,
    adv_20d_try: Optional[float],
    benchmark_fallback: bool,
    phase: str,
) -> list[str]:
    msgs: list[str] = []

    if mode == "HIZLI" and quality_grade == "D":
        msgs.append("Spekülatif yapı — teknik hareket ön planda")

    if mode in {"HIZLI", "SWING"} and quality_grade in {"C", "D"}:
        msgs.append("Kalite zayıf — trade fırsatı olabilir, yatırım kalitesi sınırlı")

    if freshness_pct < 80:
        msgs.append("Veri eski — kalite tahminine güven düşük")

    if adv_20d_try is not None and float(adv_20d_try) < ADV_LOW_LIQUIDITY_TRY:
        msgs.append("Düşük likidite — slipaj riski yüksek")

    if benchmark_fallback:
        msgs.append("Sektör endeksi yok, XU100 referansı")

    if phase == "v1_heuristic":
        msgs.append("Kalibrasyon: ön-aşama")

    return msgs


# ================================================================
# Mode classification
# ================================================================

def _classify_mode(
    *,
    inp: EngineInputs,
    engines_per_mode: Mapping[str, Mapping[str, Any]],
    quality_score: int,
    macro: _MacroState,
    adv_20d_try: Optional[float],
    sector_ctx: SectorContext,
    log_obj: DegradationLog,
) -> tuple[str, dict[str, list[str]]]:
    """Resolve mode per §11 priority. Returns (mode, why_tracking) where
    `why_tracking = {"why_this_mode": [...], "why_not_higher_mode": [...]}`.
    """
    # First priority: forced overrides via degradation
    if log_obj.any_force_uzak_dur():
        return "UZAK DUR", {
            "why_this_mode":       ["Forced via degradation"],
            "why_not_higher_mode": [],
        }
    if log_obj.any_force_sakin():
        return "SAKİN", {
            "why_this_mode":       ["Forced via degradation"],
            "why_not_higher_mode": [],
        }

    # Spec §11 priority: UZAK DUR > HIZLI > SWING > POZİSYON > TOPLANIYOR > SAKİN.
    # UZAK DUR is detected from engines+inputs (exhaustion + reversal bar);
    # any actionable engine-result satisfies the same predicate, so we
    # check off the SWING engines as a representative set.
    swing_eng = engines_per_mode.get("SWING") or engines_per_mode.get("HIZLI") or {}
    if swing_eng and _uzak_dur_forced(swing_eng, inp):
        return "UZAK DUR", {
            "why_this_mode":       ["Yorgun + ters dönüş barı"],
            "why_not_higher_mode": [],
        }

    why_not: list[str] = []
    allowed = sector_ctx.allowed_modes
    limited = log_obj.limited_mode_set()
    if limited is not None:
        allowed = frozenset(allowed) & limited

    # HIZLI?
    hizli_eng = engines_per_mode.get("HIZLI") or {}
    if "HIZLI" in allowed and hizli_eng and _hizli_conditions_met(hizli_eng, macro, adv_20d_try):
        return "HIZLI", {
            "why_this_mode": ["E1+E3+breakout/expansion satisfied", "Macro permits"],
            "why_not_higher_mode": [],
        }
    elif "HIZLI" not in allowed:
        why_not.append("HIZLI: sektör/branş kuralı bu modu kısıtladı")
    elif not hizli_eng:
        why_not.append("HIZLI: engines unavailable")

    # SWING?
    swing_eng_full = engines_per_mode.get("SWING") or {}
    swing_eligible = bool(
        swing_eng_full and
        "SWING" in allowed and
        _swing_conditions_met(swing_eng_full, quality_score, adv_20d_try)
    )
    if swing_eligible:
        return "SWING", {
            "why_this_mode": ["E1 stack + E2 + E3 + (E4-55d or E6) satisfied"],
            "why_not_higher_mode": why_not,
        }
    elif "SWING" not in allowed:
        why_not.append("SWING: sektör/branş kuralı bu modu kısıtladı")

    # POZİSYON?
    poz_eng = engines_per_mode.get("POZİSYON") or {}
    if "POZİSYON" in allowed and poz_eng and \
            _pozisyon_conditions_met(poz_eng, quality_score, inp, adv_20d_try):
        return "POZİSYON", {
            "why_this_mode": ["E1 + 60d RS positive + quality ≥ 70"],
            "why_not_higher_mode": why_not,
        }
    elif "POZİSYON" not in allowed:
        why_not.append("POZİSYON: sektör/branş kuralı bu modu kısıtladı")

    # TOPLANIYOR? (only when no actionable mode fired)
    return "PENDING_TOPLANIYOR", {
        "why_this_mode": [],
        "why_not_higher_mode": why_not,
    }


# ================================================================
# Toplaniyor / SAKİN routing
# ================================================================

def _resolve_non_actionable_mode(
    *,
    inp: EngineInputs,
    quality_grade: str,
    actionable_fired: bool,
) -> tuple[str, ToplaniyorAssessment]:
    """Decide TOPLANIYOR vs SAKİN. Returns (mode, assessment)."""
    assessment = evaluate_toplaniyor(
        inp=inp,
        quality_grade=quality_grade,
        actionable_mode_already_fired=actionable_fired,
    )
    if assessment.eligible:
        return "TOPLANIYOR", assessment
    return "SAKİN", assessment


# ================================================================
# Public entry point
# ================================================================

def build_bullalfa_signal(
    *,
    ticker: str,
    hist_df: pd.DataFrame,
    bench_df: Optional[pd.DataFrame],
    metrics: Optional[Mapping[str, Any]],
    sector_raw: Optional[str] = None,
    industry_raw: Optional[str] = None,
    short_history: Optional[bool] = None,
    halted_today: bool = False,
    macro_result: Optional[Mapping[str, Any]] = None,
    market_status: Optional[Mapping[str, Any]] = None,
    isotonic_fits: Optional[Mapping[str, Any]] = None,
    tech_pre: Optional[Mapping[str, Any]] = None,
    days_listed: Optional[int] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Build a §19 BullAlfaSignal dict for one ticker.

    All external dependencies (macro, market_status, metrics, hist_df,
    bench_df, technical primitives) are passed in as arguments — the
    orchestrator does NOT fetch data. This makes the function pure
    given its inputs and trivially testable. The scan-level batch loop
    is responsible for orchestrating the data fetches and calling this
    once per ticker.

    Args:
      ticker:        BIST ticker symbol.
      hist_df:       OHLCV daily bars, oldest→newest. Used for engine
                     primitives. >= MIN_TRADING_DAYS bars unless
                     `short_history=True` is explicitly passed.
      bench_df:      Sector benchmark OHLCV (Close column at minimum).
                     Pass None to use XU100 fallback semantics.
      metrics:       Per-ticker fundamental metrics for the quality
                     surface. Pass None or {} to force aggregation_failed.
      sector_raw:    yfinance / source-of-truth sector string.
      industry_raw:  yfinance / source-of-truth industry string (used
                     to detect REITs).
      short_history: Override the bars-available flag. None → derived
                     from hist_df length (< MIN_TRADING_DAYS).
      halted_today:  True if the ticker did not trade today (forces
                     UZAK DUR per §15).
      macro_result:  Output of `engine.macro_decision.compute_regime`
                     converted to_dict, or any dict containing
                     {"regime", "tl_vol_pct"}. Pass None to force
                     macro_unavailable per §15.
      market_status: Output of `utils.market_status.get_market_status`,
                     used for the §11 session gate. Pass None to skip
                     the gate.
      isotonic_fits: v2 calibration fits. None → use_sigmoid_v1 (v1
                     phase). Logged as `isotonic_unavailable` to
                     surface the §16 "Kalibrasyon: ön-aşama" warning.
      tech_pre:      Optional pre-computed `engine.technical.compute_technical`
                     output. Saves a call when the orchestrator has it
                     cached.
      days_listed:   For newly-listed detection (§14). Pass None to
                     skip the check.
      now_iso:       Override "now" for deterministic testing. None
                     → current UTC time as ISO 8601 with trailing 'Z'.

    Returns:
      A dict matching §19 BullAlfaSignal. Always returns a valid dict;
      every failure mode degrades to SAKİN with caveats, never raises.
    """
    log_obj = DegradationLog()

    # Normalize timestamp.
    now = now_iso or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ----------------------------------------------------------------
    # Layer 0 — macro
    # ----------------------------------------------------------------
    macro_state = _resolve_macro_state(macro_result, log_obj)

    # ----------------------------------------------------------------
    # Sector context (§14)
    # ----------------------------------------------------------------
    if short_history is None:
        short_history = (hist_df is None) or (len(hist_df) < MIN_TRADING_DAYS)
    if short_history:
        log_obj.record(DegradeCode.SHORT_HISTORY)

    if halted_today:
        log_obj.record(DegradeCode.HALTED_TODAY)

    newly = is_newly_listed(days_listed)

    sector_ctx = resolve_sector_context(
        yf_sector=sector_raw,
        yf_industry=industry_raw,
        history_length_days=days_listed,
        is_halted=halted_today,
    )

    if sector_ctx.benchmark_fallback:
        log_obj.record(DegradeCode.BENCHMARK_INDEX_MISSING)

    # ----------------------------------------------------------------
    # Layer 1 — quality surface
    # ----------------------------------------------------------------
    quality = _compute_quality_surface(metrics, sector_ctx.sector_group, log_obj)

    # ----------------------------------------------------------------
    # Layer 2 — engines (only when we have enough history)
    # ----------------------------------------------------------------
    inp: Optional[EngineInputs] = None
    engines_per_mode: dict[str, dict[str, Any]] = {}
    try:
        if hist_df is None or len(hist_df) == 0:
            log_obj.record(DegradeCode.PIT_MISSING)
        else:
            inp = build_engine_inputs(
                hist_df=hist_df,
                tech=dict(tech_pre or {}),
                bench_df=bench_df,
                sector_group=sector_ctx.sector_group,
                benchmark=sector_ctx.benchmark,
                short_history=bool(short_history),
            )
            engines_per_mode = _compute_engine_results_per_mode(inp, bool(short_history))
    except Exception as exc:
        log.warning("[%s] technical engines failed: %s", ticker, exc)
        log_obj.record(DegradeCode.TECHNICAL_FAILED)
        engines_per_mode = {}
        inp = None

    # ----------------------------------------------------------------
    # ADV calculation (used by liquidity gates and POZİSYON test)
    # ----------------------------------------------------------------
    adv_20d_try: Optional[float] = None
    try:
        if hist_df is not None and len(hist_df) >= 20 and "Volume" in hist_df.columns:
            last_price = float(hist_df["Close"].iloc[-1])
            vol_avg_20d = float(hist_df["Volume"].iloc[-20:].mean())
            adv_20d_try = vol_avg_20d * last_price
    except Exception:
        adv_20d_try = None

    # ----------------------------------------------------------------
    # Mode classification (priority resolution)
    # ----------------------------------------------------------------
    quality_score = quality.score if quality is not None else 0
    quality_grade = quality.grade if quality is not None else "D"

    if inp is None:
        # No engine inputs at all → SAKİN unless something forced earlier.
        if log_obj.any_force_uzak_dur():
            mode = "UZAK DUR"
        elif log_obj.has(DegradeCode.HALTED_TODAY):
            mode = "UZAK DUR"
        else:
            mode = "SAKİN"
        why_tracking = {"why_this_mode": ["No engine inputs"], "why_not_higher_mode": []}
    else:
        mode, why_tracking = _classify_mode(
            inp=inp,
            engines_per_mode=engines_per_mode,
            quality_score=quality_score,
            macro=macro_state,
            adv_20d_try=adv_20d_try,
            sector_ctx=sector_ctx,
            log_obj=log_obj,
        )

    actionable_fired = mode in _ACTIONABLE_MODES

    # ----------------------------------------------------------------
    # Pending TOPLANIYOR/SAKİN routing
    # ----------------------------------------------------------------
    toplaniyor_assessment: Optional[ToplaniyorAssessment] = None
    if mode == "PENDING_TOPLANIYOR":
        if inp is None:
            mode = "SAKİN"
        else:
            mode, toplaniyor_assessment = _resolve_non_actionable_mode(
                inp=inp,
                quality_grade=quality_grade,
                actionable_fired=False,
            )
            # Restrict to allowed modes per sector_ctx.
            if mode not in sector_ctx.allowed_modes:
                mode = "SAKİN"

    # ----------------------------------------------------------------
    # Liquidity gates (post mode resolution, §11)
    # ----------------------------------------------------------------
    swing_eng = engines_per_mode.get("SWING") or {}
    swing_eligible = bool(
        swing_eng and inp is not None and
        _swing_conditions_met(swing_eng, quality_score, adv_20d_try)
        and "SWING" in sector_ctx.allowed_modes
    )
    liq = _apply_liquidity_gates(
        mode=mode, adv_20d_try=adv_20d_try, swing_eligible=swing_eligible,
    )
    mode = liq.mode

    # ----------------------------------------------------------------
    # Session gate (§11 — within 30min of close → HIZLI to TOPLANIYOR)
    # ----------------------------------------------------------------
    mode, session_downgrade_reason = _apply_session_gate(
        mode=mode, market_status=market_status,
    )

    final_downgrade_reason = liq.downgrade_reason or session_downgrade_reason

    # ----------------------------------------------------------------
    # Layer 3 — calibration (only for actionable modes)
    # ----------------------------------------------------------------
    if isotonic_fits is None:
        log_obj.record(DegradeCode.ISOTONIC_UNAVAILABLE)
    phase_label = calibration_phase(isotonic_fits_loaded=isotonic_fits is not None)

    confidence_block: dict[str, Any] = {
        "raw_combined": 0.0, "final": 0.0, "phase": phase_label,
    }
    accumulation_strength_val = 0
    chosen_engines: dict[str, Any] = {}

    if inp is not None:
        # Engine block selected for the final mode (or SWING as fallback
        # when the final mode is non-actionable, since the §19 schema
        # always includes the engines block for transparency).
        chosen_engines = (
            engines_per_mode.get(mode) if mode in _ACTIONABLE_MODES else None
        ) or engines_per_mode.get("SWING") or engines_per_mode.get("HIZLI") or {}

        if mode in _ACTIONABLE_MODES and chosen_engines:
            try:
                tech_score = _tech_score(chosen_engines, mode)
                edge_score = _edge_score(chosen_engines)
                exhaustion = float(chosen_engines.get("e7_exhaustion", 0.0))
                macro_mult = macro_multiplier(macro_state.regime, macro_state.tl_vol_pct, mode)
                age_mult = 1.0  # v1 — no age-based decay yet
                conf = compute_confidence(
                    quality_score=float(quality_score),
                    technical_score=float(tech_score),
                    edge_score=float(edge_score),
                    mode=mode,
                    exhaustion=exhaustion,
                    macro_mult=macro_mult,
                    age_mult=age_mult,
                )
                # Liquidity penalty applied multiplicatively per §11.
                final_with_liq = float(conf["final"]) * liq.confidence_mult
                conf["final"] = round(max(0.0, min(100.0, final_with_liq)), 2)
                confidence_block = {
                    "raw_combined": conf["raw_combined"],
                    "final":        conf["final"],
                    # Phase is pinned by the orchestrator (it knows
                    # whether v2 fits were loaded); compute_confidence
                    # only sees BULLALFA_PARAMS and would otherwise
                    # always report v1.
                    "phase":        phase_label,
                }
            except Exception as exc:
                log.warning("[%s] calibration failed: %s", ticker, exc)
                confidence_block = {"raw_combined": 0.0, "final": 0.0, "phase": phase_label}

        # accumulation_strength always populated (used by §17 ranking).
        try:
            accumulation_strength_val = compute_accumulation_strength(inp)
        except Exception:
            accumulation_strength_val = 0

    # ----------------------------------------------------------------
    # Layer 4 — risk frame (only for actionable modes)
    # ----------------------------------------------------------------
    risk_frame = None
    risk_frame_downgraded = False
    if mode in _ACTIONABLE_MODES and inp is not None:
        atr14 = inp.atr14
        rf, dr_reason, rf_caveats = try_build_risk_frame(
            price=inp.price, atr14=atr14, mode=mode,
        )
        if rf is None and dr_reason is not None:
            # Spec §10: invariant failure → downgrade to TOPLANIYOR
            # (NOT SAKİN, NOT hidden).
            new_mode, top_assessment = _resolve_non_actionable_mode(
                inp=inp,
                quality_grade=quality_grade,
                actionable_fired=True,  # actionable just failed risk-frame
            )
            mode = new_mode if new_mode in sector_ctx.allowed_modes else "SAKİN"
            toplaniyor_assessment = top_assessment
            confidence_block = {"raw_combined": 0.0, "final": 0.0, "phase": phase_label}
            risk_frame_downgraded = (mode == "TOPLANIYOR")
        else:
            risk_frame = rf

    # ----------------------------------------------------------------
    # Apply grade cap from sector context (newly_listed)
    # ----------------------------------------------------------------
    final_grade = quality_grade
    grade_capped = quality.grade_capped if quality else False
    if sector_ctx.grade_cap is not None and quality is not None:
        capped = sector_ctx.grade_cap
        # Cap means "max of capped_grade"; only downgrade letters above the cap.
        order = ["A+", "A", "B", "C", "D"]
        if final_grade in order and capped in order:
            if order.index(final_grade) < order.index(capped):
                final_grade = capped
                grade_capped = True

    quality_block = _quality_block(quality)
    quality_block["grade"] = final_grade
    quality_block["grade_capped"] = grade_capped

    # ----------------------------------------------------------------
    # Ranking — opportunity_score (§17)
    # ----------------------------------------------------------------
    opp = opportunity_score(
        mode=mode,
        confidence_final=confidence_block.get("final"),
        accumulation_strength=accumulation_strength_val,
        quality_score=quality_score,
    )

    # ----------------------------------------------------------------
    # Engine block per §19 (always populated — transparency)
    # ----------------------------------------------------------------
    e2 = (chosen_engines or {}).get("e2_relstr", {}) or {}
    e3 = (chosen_engines or {}).get("e3_volume", {}) or {}
    e4 = (chosen_engines or {}).get("e4_breakout", {}) or {}
    e5 = (chosen_engines or {}).get("e5_compression", {}) or {}
    pullback_to_breakout_flag = False
    if inp is not None and chosen_engines:
        try:
            # Determine which mode the chosen_engines were computed for.
            # `compute_engines` doesn't tag itself, so re-derive against
            # the final mode (or SWING as a representative).
            tag_mode = mode if mode in _ACTIONABLE_MODES else "SWING"
            pullback_to_breakout_flag = detect_pullback_to_breakout(inp, tag_mode)
        except Exception:
            pullback_to_breakout_flag = False

    engines_block = {
        "e1_trend":      int((chosen_engines or {}).get("e1_trend", 0)),
        "e2_relstr":     {
            "score":     float(e2.get("score", 0.0)),
            "benchmark": str(e2.get("benchmark", sector_ctx.benchmark)),
        },
        "e3_volume":     {
            "rvol":   float(e3.get("rvol", 0.0)) if e3.get("rvol") is not None else 0.0,
            "passed": bool(e3.get("passed", False)),
        },
        "e4_breakout":   {
            "type":     e4.get("type"),
            "bars_ago": e4.get("bars_ago"),
        },
        "e5_compression": {
            "compressed":      bool(e5.get("compressed", False)),
            "expanded":        bool(e5.get("expanded", False)),
            **({"skipped_reason": e5["skipped_reason"]} if e5.get("skipped_reason") else {}),
        },
        "e6_pullback":   bool((chosen_engines or {}).get("e6_pullback", False)),
        "e7_exhaustion": float((chosen_engines or {}).get("e7_exhaustion", 0.0)),
        "pullback_to_breakout":  bool(pullback_to_breakout_flag),
        "accumulation_strength": int(accumulation_strength_val),
    }

    # ----------------------------------------------------------------
    # Why-now (§18)
    # ----------------------------------------------------------------
    rs_short = e2.get("rs_short")
    why = why_now(
        mode=mode,
        engines={
            "e1_pass":           bool(engines_block["e1_trend"]),
            "e3_rvol":           engines_block["e3_volume"]["rvol"],
            "rs_short":          rs_short if isinstance(rs_short, (int, float)) else None,
            "e4_breakout_type":  engines_block["e4_breakout"]["type"],
            "e4_bars_ago":       engines_block["e4_breakout"]["bars_ago"],
            "e5_expansion":      engines_block["e5_compression"]["expanded"],
            "e7_exhaustion":     engines_block["e7_exhaustion"],
        },
        quality={"temel_score": quality_score} if quality is not None else None,
        valuation={"fk_oran": metrics.get("pe") if metrics else None},
        technicals={
            "ema200_above":      bool(inp and inp.price and inp.ema200 and inp.price > inp.ema200),
            "rs_60d_positive":   bool(
                inp and inp.return_60d is not None and inp.bench_return_60d is not None and
                (inp.return_60d - inp.bench_return_60d) > 0
            ),
            "rsi":               (inp.rsi if inp else None),
            "ret_5d":            (inp.return_5d if inp else None),
            "rvol_today_drop":   False,
            "reversal_bar":      bool(inp and inp.price and inp.prior_low and inp.price < inp.prior_low),
        },
        toplaniyor={
            "rvol_5d_avg":  inp.rvol_5d_avg if inp else None,
            "bb_pctile":    (inp.bb_width_60d_pctile if inp is not None else None),
            "adx_rising":   bool(
                inp and inp.adx_today is not None and inp.adx_10d_ago is not None
                and inp.adx_today > inp.adx_10d_ago
            ),
            "higher_lows":  bool(inp and (inp.higher_lows_count_10d or 0) >= 3),
        } if mode == "TOPLANIYOR" else None,
    )

    # ----------------------------------------------------------------
    # Caveats — degradation log + sector ctx caveats + risk-frame TR
    # ----------------------------------------------------------------
    caveats: list[str] = []
    for c in log_obj.caveats():
        if c not in caveats:
            caveats.append(c)
    for c in (sector_ctx.caveats or ()):
        if c not in caveats:
            caveats.append(c)
    # Risk-frame caveat is only user-meaningful when the cascade
    # actually landed in TOPLANIYOR (the spec §10 fallback). When
    # it cascaded further to SAKİN the caveat would mislead.
    if risk_frame_downgraded and _RISK_FRAME_INVALID_CAVEAT_TR not in caveats:
        caveats.append(_RISK_FRAME_INVALID_CAVEAT_TR)

    # ----------------------------------------------------------------
    # Warnings (§16)
    # ----------------------------------------------------------------
    warnings = _warnings(
        mode=mode,
        quality_grade=final_grade,
        freshness_pct=quality.freshness_pct if quality else 0.0,
        adv_20d_try=adv_20d_try,
        benchmark_fallback=sector_ctx.benchmark_fallback,
        phase=phase_label,
    )

    # ----------------------------------------------------------------
    # Liquidity block per §19
    # ----------------------------------------------------------------
    liquidity_block = {
        "adv_20d_try":      float(adv_20d_try) if adv_20d_try is not None else 0.0,
        "penalty_applied":  bool(liq.penalty_applied),
        "downgrade_reason": final_downgrade_reason,
    }

    # ----------------------------------------------------------------
    # Lifecycle — placeholder, full tracking is post-v1.4.
    # ----------------------------------------------------------------
    horizon_bars: Optional[int] = None
    horizon_label: Optional[str] = None
    if mode == "HIZLI":
        horizon_bars, horizon_label = 5, "1–5 gün"
    elif mode == "SWING":
        horizon_bars, horizon_label = 20, "~4 hafta"
    elif mode == "POZİSYON":
        horizon_bars, horizon_label = 126, "~6 ay"

    lifecycle_block = {
        "signal_id":    f"{ticker}-{now}",
        "triggered_at": now,
        "bars_since":   0,
        "status":       "TAZE" if mode in _ACTIONABLE_MODES else None,
        "outcome":      None,
        "mode_history": [{"mode": mode, "entered_at": now}],
    }

    # ----------------------------------------------------------------
    # Assemble per §19 schema
    # ----------------------------------------------------------------
    return {
        "ticker":         ticker,
        "sector_group":   sector_ctx.sector_group,
        "generated_at":   now,
        "schema_version": SCHEMA_VERSION,

        "quality":  quality_block,
        "macro":    _macro_block(macro_state),

        "mode":          mode,
        "horizon_bars":  horizon_bars,
        "horizon_label": horizon_label,
        "why_now":       list(why),

        "engines":   engines_block,
        "confidence": confidence_block,
        "opportunity_score": int(opp),
        "risk_frame": risk_frame,

        "lifecycle": lifecycle_block,
        "liquidity": liquidity_block,

        "explainer": {
            "why_this_mode":       list(why_tracking.get("why_this_mode", [])),
            "why_not_higher_mode": list(why_tracking.get("why_not_higher_mode", [])),
            "caveats":             caveats,
            "warnings":            warnings,
        },
    }


# ================================================================
# Scan-level assembly (§19 ScanResponse)
# ================================================================

def build_scan_response(
    signals: list[dict],
    *,
    page: int = 1,
    per_page: Optional[int] = None,
    extra_warnings: Optional[list[str]] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the §19 ScanResponse from a list of BullAlfaSignal dicts.

    Sorts signals by `opportunity_score DESC`. Computes `meta.by_mode`,
    `meta.sector_concentration`, `meta.universe_size`, and pagination.

    Args:
      signals:        Signal dicts produced by `build_bullalfa_signal`.
                      The function does not mutate its input.
      page:           1-indexed page number for pagination.
      per_page:       Page size; None → no pagination (returns all).
      extra_warnings: Scan-level warnings (concentration banners,
                      circuit-breaker notices, etc.).
      now_iso:        Override "now" for deterministic tests.
    """
    now = now_iso or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Stable sort: opportunity DESC, then ticker ASC for tie-break.
    sorted_signals = sorted(
        signals,
        key=lambda s: (-int(s.get("opportunity_score", 0)), str(s.get("ticker", ""))),
    )

    by_mode: dict[str, int] = {}
    sector_concentration: dict[str, int] = {}
    for s in sorted_signals:
        m = str(s.get("mode", "SAKİN"))
        by_mode[m] = by_mode.get(m, 0) + 1
        # Spec §17 banner is for actionable-mode concentration.
        if m in _ACTIONABLE_MODES:
            sg = str(s.get("sector_group", "unknown"))
            sector_concentration[sg] = sector_concentration.get(sg, 0) + 1

    universe_size = len(sorted_signals)

    pagination = None
    page_signals = sorted_signals
    if per_page is not None and per_page > 0:
        page = max(1, int(page))
        start = (page - 1) * per_page
        end = start + per_page
        page_signals = sorted_signals[start:end]
        pagination = {
            "page":      page,
            "per_page":  per_page,
            "total":     universe_size,
        }

    meta: dict[str, Any] = {
        "generated_at":         now,
        "universe_size":        universe_size,
        "by_mode":              by_mode,
        "sector_concentration": sector_concentration,
        "warnings":             list(extra_warnings or []),
    }
    if pagination is not None:
        meta["pagination"] = pagination

    return {"signals": page_signals, "meta": meta}

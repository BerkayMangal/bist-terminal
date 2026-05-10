# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# api/bullalfa_schemas.py
#
# Pydantic v2 models mirroring the §19 schema verbatim. Used for:
#
#   1. Generating `bullalfa_jsonschema.json` (artifact for consumers)
#   2. Optional runtime validation in tests
#   3. Future: enforce as `response_model=` on the route handlers
#      once we're confident no field-level surprises remain
#
# These models DO NOT change the API contract at runtime — the
# orchestrator returns plain dicts and the route handlers don't
# enforce these models. They're documentation + validation tools.
# ================================================================

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Mode + grade enums (§11, §7)
ModeLiteral  = Literal["HIZLI", "SWING", "POZİSYON",
                       "TOPLANIYOR", "SAKİN", "UZAK DUR"]
GradeLiteral = Literal["A+", "A", "B", "C", "D"]
RegimeLiteral = Literal["risk_on", "neutral", "risk_off"]
PhaseLiteral  = Literal["v1_heuristic", "v2_isotonic", "v3_ml"]
SectorGroupLiteral = Literal[
    "banka", "holding", "gyo", "savunma", "enerji",
    "perakende", "ulasim", "sanayi",
    "newly_listed", "halted",
]
KaliteLiteral = Literal["GÜÇLÜ", "ORTA", "ZAYIF"]
ValueLiteral  = Literal["KELEPİR", "UCUZ", "NORMAL", "PAHALI"]
LabelPassLiteral = Literal["Geçti", "Kaldı"]
BreakoutTypeLiteral = Literal["20d", "55d", "6m"]
LifecycleStatusLiteral = Literal["TAZE", "GELİŞMEKTE", "GEÇ KALDI"]
LifecycleOutcomeLiteral = Literal["1R_VURDU", "STOP_OLDU", "SÜRESİ_DOLDU"]


# Common config — allow extra fields so the orchestrator can grow
# the dict shape (e.g. add a new engine) without immediately breaking
# v1.4 schema validation.
class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ----------------------------------------------------------------
# §19 sub-blocks
# ----------------------------------------------------------------

class QualityTags(_Base):
    kalite:  Optional[KaliteLiteral]    = None
    value:   Optional[ValueLiteral]     = None
    buffett: Optional[LabelPassLiteral] = None
    graham:  Optional[LabelPassLiteral] = None


class Quality(_Base):
    score:         int                    = Field(ge=0, le=100)
    grade:         GradeLiteral
    grade_capped:  bool
    freshness_pct: float                  = Field(ge=0, le=100)
    tags:          QualityTags


class Macro(_Base):
    regime:         RegimeLiteral
    tl_vol_pct:     float                 = Field(ge=0, le=100)
    multiplier:     float                 = Field(ge=0)
    hizli_disabled: bool


class E2RelStr(_Base):
    score:     float
    benchmark: str


class E3Volume(_Base):
    rvol:   float = Field(ge=0)
    passed: bool


class E4Breakout(_Base):
    type:     Optional[BreakoutTypeLiteral] = None
    bars_ago: Optional[int]                  = Field(default=None, ge=0)


class E5Compression(_Base):
    compressed:     bool
    expanded:       bool
    skipped_reason: Optional[str] = None


class Engines(_Base):
    e1_trend:               int           = Field(ge=0, le=1)
    e2_relstr:              E2RelStr
    e3_volume:              E3Volume
    e4_breakout:            E4Breakout
    e5_compression:         E5Compression
    e6_pullback:            bool
    e7_exhaustion:          float         = Field(ge=0, le=1)
    pullback_to_breakout:   bool
    accumulation_strength:  int           = Field(ge=0, le=100)


class Confidence(_Base):
    raw_combined: float
    final:        float = Field(ge=0, le=100)
    phase:        PhaseLiteral


class RiskFrame(_Base):
    # Spec §19 declares entry_low / entry_high as separate fields, but
    # the orchestrator emits a 2-tuple `entry_zone: [low, high]` which
    # the frontend expects. Both shapes are valid — we accept either
    # here so the schema reflects production behaviour.
    entry_zone:    Optional[list[float]]  = Field(default=None, min_length=2, max_length=2)
    entry_low:     Optional[float]        = None
    entry_high:    Optional[float]        = None
    stop:          float
    stop_pct:      float
    target_1r:     float
    target_2r:     float
    target_3r:     float
    invalidation:  str
    max_hold_bars: int                    = Field(ge=1)
    trail_rule:    str


class ModeHistoryEntry(_Base):
    mode:       ModeLiteral
    entered_at: str                       # ISO 8601


class Lifecycle(_Base):
    signal_id:    str
    triggered_at: str
    bars_since:   int                     = Field(ge=0)
    status:       Optional[LifecycleStatusLiteral]  = None
    outcome:      Optional[LifecycleOutcomeLiteral] = None
    mode_history: list[ModeHistoryEntry]


class Liquidity(_Base):
    adv_20d_try:      float                = Field(ge=0)
    penalty_applied:  bool
    downgrade_reason: Optional[str]        = None


class Explainer(_Base):
    why_this_mode:       list[str]
    why_not_higher_mode: list[str]
    caveats:             list[str]
    warnings:            list[str]


# ----------------------------------------------------------------
# Top-level signal (§19)
# ----------------------------------------------------------------

class BullAlfaSignal(_Base):
    ticker:         str
    sector_group:   SectorGroupLiteral
    generated_at:   str                   # ISO 8601
    schema_version: Literal["1.4"]

    quality:           Quality
    macro:             Macro

    mode:              ModeLiteral
    horizon_bars:      Optional[int]      = Field(default=None, ge=0)
    horizon_label:     Optional[str]      = None
    why_now:           list[str]

    engines:           Engines
    confidence:        Confidence
    opportunity_score: int                = Field(ge=0, le=100)
    risk_frame:        Optional[RiskFrame] = None

    lifecycle:         Lifecycle
    liquidity:         Liquidity
    explainer:         Explainer


# ----------------------------------------------------------------
# Scan response wrapping (§19 ScanResponse)
# ----------------------------------------------------------------

class Pagination(_Base):
    page:     int = Field(ge=1)
    per_page: int = Field(ge=1)
    total:    int = Field(ge=0)


class CircuitBreaker(_Base):
    frozen:               bool
    consecutive_failures: int = Field(ge=0)


class ScanMeta(_Base):
    generated_at:          str
    universe_size:         int                     = Field(ge=0)
    by_mode:               dict[str, int]
    sector_concentration:  dict[str, int]
    warnings:              list[str]
    pagination:            Optional[Pagination]    = None
    schema_version:        Optional[Literal["1.4"]] = None
    cache_as_of:           Optional[str]            = None
    provider:              Optional[str]            = None
    circuit_breaker:       Optional[CircuitBreaker] = None


class ScanResponse(_Base):
    signals: list[BullAlfaSignal]
    meta:    ScanMeta


class TickerResponse(_Base):
    """Wrapper returned by `GET /api/bullalfa/{ticker}`."""
    schema_version: Literal["1.4"]
    signal:         BullAlfaSignal


# ----------------------------------------------------------------
# JSON Schema export
# ----------------------------------------------------------------

def export_json_schema() -> dict:
    """Return a single JSON Schema covering the §19 surface."""
    return {
        "$schema":     "https://json-schema.org/draft/2020-12/schema",
        "title":       "BullAlfa v1.4 API contract",
        "description": "Spec §19 schema for /api/bullalfa/scan and "
                       "/api/bullalfa/{ticker} responses.",
        "$defs": {
            "BullAlfaSignal": BullAlfaSignal.model_json_schema(),
            "ScanResponse":   ScanResponse.model_json_schema(),
            "TickerResponse": TickerResponse.model_json_schema(),
        },
    }


__all__ = [
    "BullAlfaSignal", "ScanResponse", "ScanMeta", "TickerResponse",
    "Quality", "QualityTags", "Macro",
    "Engines", "E2RelStr", "E3Volume", "E4Breakout", "E5Compression",
    "Confidence", "RiskFrame", "Lifecycle", "ModeHistoryEntry",
    "Liquidity", "Explainer",
    "Pagination", "CircuitBreaker",
    "ModeLiteral", "GradeLiteral", "RegimeLiteral", "PhaseLiteral",
    "SectorGroupLiteral",
    "export_json_schema",
]

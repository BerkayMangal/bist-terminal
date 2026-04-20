# ================================================================
# BISTBULL TERMINAL — MACRO DECISION ENGINE
# engine/macro_decision.py
#
# Rule-based, explainable regime detection.
# No black boxes. Every score traceable.
# ================================================================

from __future__ import annotations

import logging
import datetime as dt
from typing import Optional, Any
from dataclasses import dataclass, field, asdict

log = logging.getLogger("bistbull.macro_decision")

# ================================================================
# SCORING THRESHOLDS — all visible, no magic numbers
# ================================================================

THRESHOLDS = {
    "cds": {"bull": 250, "bear": 350},           # bps
    "usdtry_5d_pct": {"bull": 1.0, "bear": 3.0}, # %
    "vix": {"bull": 18, "bear": 25},
    "dxy_20d_pct": {"bull": -1.0, "bear": 1.0},   # % change — negative = weaker dollar = good for EM
    "yield_spread": {"bull": 0.0, "bear": -1.0},   # 10Y - 2Y in pct points; negative = inverted
    "global_idx_5d_pct": {"bull": 0.5, "bear": -1.0},  # S&P 500 5d %
}

# 6 signals → range [-6, +6]. ±3 = 50% directional agreement.
# Deliberately conservative: NEUTRAL is honest when signals are mixed.
# 2 of 6 signals (CDS, yield_spread) are "tahmini" — regime is primarily
# driven by the 4 live signals (USDTRY, VIX, DXY, S&P500).
REGIME_THRESHOLDS = {"risk_on": 3, "risk_off": -3}

# Contradiction detection
CONTRADICTION_BIST_THRESHOLD = 3.0   # BIST 5d % move that contradicts regime


# ================================================================
# SAFETY GUARDS (Phase 7)
# ================================================================
def _safe_float(val: Any, default: float = 0.0,
                lo: float = -9999, hi: float = 9999) -> float:
    """Safely convert to float, clamp to [lo, hi]. Never returns None."""
    if val is None:
        return default
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN check
        return default
    return max(lo, min(hi, v))


def _safe_pct(val: Any, default: float = 0.0) -> float:
    return _safe_float(val, default, lo=-100.0, hi=500.0)


# ================================================================
# SIGNAL SCORING
# ================================================================
@dataclass
class SignalScore:
    name: str
    value: float
    score: int          # -1, 0, +1
    label: str          # "Olumlu", "Nötr", "Olumsuz"
    source: str         # "canlı", "günlük", "haftalık", "tahmini"
    fetched_at: Optional[str] = None
    note: str = ""


@dataclass
class Contradiction:
    type: str           # "bist_vs_macro", "cds_vs_fx", "global_vs_local"
    message: str        # plain Turkish


@dataclass
class RegimeResult:
    regime: str         # "RISK_ON", "NEUTRAL", "RISK_OFF"
    score: float        # weighted score (-5.0 to +5.0 typical)
    confidence: str     # "HIGH", "MEDIUM", "LOW"
    explanation: str    # plain Turkish
    signals: list[SignalScore] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    computed_at: str = ""
    regime_since: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signals"] = [asdict(s) for s in self.signals]
        d["contradictions"] = [asdict(c) for c in self.contradictions]
        return d


# ================================================================
# CORE: score one signal
# ================================================================
def _score_signal(name: str, value: float, thresholds: dict) -> int:
    bull = thresholds["bull"]
    bear = thresholds["bear"]

    if bull < bear:
        # lower is better (CDS, VIX, USDTRY change, DXY)
        if value < bull:
            return 1
        elif value > bear:
            return -1
        return 0
    else:
        # higher is better (yield_spread, foreign_flow, global_idx)
        if value > bull:
            return 1
        elif value < bear:
            return -1
        return 0


SCORE_LABELS = {1: "Olumlu", 0: "Nötr", -1: "Olumsuz"}


# ================================================================
# MAIN: compute regime
# ================================================================
def compute_regime(inputs: dict[str, Any]) -> RegimeResult:
    """
    6-signal regime engine with weighted scoring.
    Trusted signals = full weight (1.0), estimated = half weight (0.5).
    """
    signals: list[SignalScore] = []
    weighted_score = 0.0

    # (key, display_name, unit, default_source)
    signal_defs = [
        ("cds",               "Türkiye CDS",        "bps",  "tahmini"),
        ("usdtry_5d_pct",     "USD/TRY (5 gün)",    "%",    "günlük"),
        ("vix",               "VIX",                "",     "günlük"),
        ("dxy_20d_pct",       "Dolar Endeksi (20g)", "%",   "günlük"),
        ("yield_spread",      "Verim Eğrisi",       "puan", "tahmini"),
        ("global_idx_5d_pct", "S&P 500 (5 gün)",    "%",    "günlük"),
    ]

    # Weight by source quality
    WEIGHT_MAP = {"günlük": 1.0, "tahmini": 0.5, "eski": 0.25, "yok": 0.0}

    for key, display, unit, default_source in signal_defs:
        raw = inputs.get(key)
        missing = raw is None
        val = _safe_float(raw, default=0.0)
        thresh = THRESHOLDS.get(key)
        if thresh is None:
            continue
        source = inputs.get(f"{key}_source", "yok" if missing else default_source)
        sc = 0 if missing else _score_signal(key, val, thresh)
        weight = WEIGHT_MAP.get(source, 0.5)
        weighted_score += sc * weight
        fetched = inputs.get(f"{key}_fetched_at")
        note = "veri yok" if missing else (f"{val:+.1f}{unit}" if unit else f"{val:.1f}")
        signals.append(SignalScore(
            name=display, value=round(val, 2), score=sc,
            label=SCORE_LABELS[sc], source=source,
            fetched_at=fetched, note=note,
        ))

    # --- Regime (using weighted score) ---
    # Max possible = 6.0 (all trusted bullish), min = -6.0
    # With 2 estimated signals at 0.5 weight: max = 4*1.0 + 2*0.5 = 5.0
    # Thresholds: ±2.5 gives ~50% weighted agreement for regime change
    if weighted_score >= 2.5:
        regime = "RISK_ON"
    elif weighted_score <= -2.5:
        regime = "RISK_OFF"
    else:
        regime = "NEUTRAL"

    # --- Confidence ---
    # CRITICAL: Confidence = data quality, NOT signal agreement.
    # Mixed signals with good data = MEDIUM (AI should comment on the mix).
    # Poor data with any signals = LOW (AI should stay quiet).
    n_signals = len(signals)
    n_trusted = sum(1 for s in signals if s.source in ("günlük",))
    n_estimated = sum(1 for s in signals if s.source in ("tahmini", "eski"))
    n_missing = sum(1 for s in signals if s.note == "veri yok")
    abs_ws = abs(weighted_score)

    # Data quality tier (floor)
    if n_signals == 0 or n_missing >= 4:
        data_quality = "LOW"
    elif n_trusted >= 4 and n_missing == 0:
        data_quality = "HIGH" if n_estimated <= 1 else "MEDIUM"
    elif n_trusted >= 3:
        data_quality = "MEDIUM"
    else:
        data_quality = "LOW"

    # Final confidence: data quality is the floor, strong signal can lift
    if data_quality == "LOW":
        confidence = "LOW"
    elif data_quality == "HIGH" and abs_ws >= 3.5:
        confidence = "HIGH"
    else:
        confidence = "MEDIUM"

    # --- Explanation (plain Turkish) ---
    explanation = _build_explanation(regime, weighted_score, signals, confidence)

    # --- Contradiction detection ---
    contradictions = _detect_contradictions(inputs, regime, signals)

    return RegimeResult(
        regime=regime,
        score=round(weighted_score, 1),
        confidence=confidence,
        explanation=explanation,
        signals=signals,
        contradictions=contradictions,
        computed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )


# ================================================================
# EXPLANATION BUILDER
# ================================================================
def _build_explanation(regime: str, score: float,
                       signals: list[SignalScore], confidence: str) -> str:
    neg = [s for s in signals if s.score == -1]
    pos = [s for s in signals if s.score == 1]

    if regime == "RISK_OFF":
        drivers = ", ".join(s.name for s in neg[:3]) or "birden fazla sinyal"
        return (f"{drivers} olumsuz yönde. "
                f"Piyasa ortamı temkinli olmayı gerektiriyor.")
    elif regime == "RISK_ON":
        drivers = ", ".join(s.name for s in pos[:3]) or "birden fazla sinyal"
        return (f"{drivers} olumlu yönde. "
                f"Risk iştahı destekli bir ortam var.")
    else:
        return ("Sinyaller karışık. "
                "Net bir yön yok — bekle-gör stratejisi mantıklı.")


# ================================================================
# CONTRADICTION DETECTION
# ================================================================
def _detect_contradictions(inputs: dict, regime: str,
                           signals: list[SignalScore]) -> list[Contradiction]:
    conds: list[Contradiction] = []

    bist_5d = _safe_pct(inputs.get("bist_5d_pct"), 0.0)

    # 1. BIST vs Regime
    if regime == "RISK_OFF" and bist_5d > CONTRADICTION_BIST_THRESHOLD:
        conds.append(Contradiction(
            type="bist_vs_macro",
            message=f"BIST 5 günde %{bist_5d:.1f} yükseldi ama makro ortam olumsuz. "
                    "Bu yükselişin arkasını sorgula.",
        ))
    elif regime == "RISK_ON" and bist_5d < -CONTRADICTION_BIST_THRESHOLD:
        conds.append(Contradiction(
            type="bist_vs_macro",
            message=f"BIST 5 günde %{bist_5d:.1f} düştü ama makro ortam olumlu. "
                    "Yerel bir sorun olabilir.",
        ))

    # 2. CDS vs FX
    cds = _safe_float(inputs.get("cds"), 0)
    usdtry_5d = _safe_pct(inputs.get("usdtry_5d_pct"), 0)
    if cds > 300 and usdtry_5d < -1.0:
        conds.append(Contradiction(
            type="cds_vs_fx",
            message="CDS yüksek ama TL güçleniyor — bu ayrışma sürdürülebilir olmayabilir.",
        ))

    # 3. Global vs Local
    global_5d = _safe_pct(inputs.get("global_idx_5d_pct"), 0)
    if global_5d > 1.0 and bist_5d < -1.0:
        conds.append(Contradiction(
            type="global_vs_local",
            message="Küresel endeksler yükseliyor ama BIST geride kalıyor. "
                    "Türkiye'ye özgü bir baskı olabilir.",
        ))
    elif global_5d < -2.0 and bist_5d > 1.0:
        conds.append(Contradiction(
            type="global_vs_local",
            message="Küresel piyasalar düşerken BIST yükseliyor — "
                    "ayrışma genellikle kısa ömürlü olur.",
        ))

    return conds


# ================================================================
# SECTOR ROTATION MAP
# ================================================================
SECTOR_ROTATION = {
    "RISK_ON": {
        "strong": ["Bankacılık", "Sanayi", "Holding"],
        "weak":   ["Gıda", "Telekom"],
    },
    "NEUTRAL": {
        "strong": ["Gıda", "Teknoloji", "Enerji"],
        "weak":   ["Holding", "İnşaat"],
    },
    "RISK_OFF": {
        "strong": ["Gıda", "Telekom", "Enerji", "Savunma"],
        "weak":   ["Bankacılık", "Sanayi", "İnşaat"],
    },
}


def get_sector_rotation(regime: str) -> dict:
    return SECTOR_ROTATION.get(regime, SECTOR_ROTATION["NEUTRAL"])

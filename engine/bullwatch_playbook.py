"""
BullWatch v2 — Playbook Sequence Engine (Addendum Module 1).

This is the NARRATIVE LAYER. The 8 motors produce evidence; this engine
reads them as sequences and tells the story:

    "Bu hisse döngünün neresinde?"

Sequence templates encode the canonical group-behavior playbooks:
  - ACCUMULATION_SEQUENCE: Float Lock → Absorption → Sanction event →
                           Spring recovery → Breakout
  - DISTRIBUTION_SEQUENCE: Ceiling series → Break → Retail Heat →
                           Gap trap → Volume inversion
  - MARKUP_SEQUENCE:       Absorption complete → Breakout →
                           Follow-through → Ceiling acceleration

For each template, we walk the steps in order and check whether each
trigger condition holds, with a per-step time window. The output is
the playbook with the highest confidence (steps_completed / total_steps).

Phase A note
------------
Some sequence steps depend on motors that don't exist yet in Phase A
(CeilingBreak, RetailHeat, GapTrap, VBTS feed). For those, the engine
substitutes proxies derived from existing OHLCV signals or marks the
step "optional/unknown". This keeps the engine functional from day 1
while progressively gaining confidence as Phase B/C motors come online.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import pandas as pd
    _PANDAS = True
except Exception:
    _PANDAS = False


@dataclass
class PlaybookResult:
    playbook: str  # "ACCUMULATION_SEQUENCE" | "DISTRIBUTION_SEQUENCE" |
                   # "MARKUP_SEQUENCE" | "UNCLEAR"
    confidence: int  # 0-100, fraction of completed steps
    sequence_events: list = field(default_factory=list)
    missing_next_confirmation: list = field(default_factory=list)
    candidates: list = field(default_factory=list)  # all evaluated playbooks

    def to_dict(self) -> dict:
        return {
            "playbook": self.playbook,
            "confidence": self.confidence,
            "sequence_events": self.sequence_events,
            "missing_next_confirmation": self.missing_next_confirmation,
            "candidates": self.candidates,
        }


# ────────────────────────────────────────────────────────────────────
# Sequence step definitions.
#
# Each step is detected by walking the trailing OHLCV window with
# the trigger function. The trigger receives a SymbolState bundle
# of all available signals (from existing motors + Phase A modules)
# and returns a date if the step occurred recently, else None.
# ────────────────────────────────────────────────────────────────────
@dataclass
class SymbolState:
    """Bundle of signals available at scoring time."""
    df: Any  # OHLCV DataFrame
    sub_scores: dict  # existing motor sub-scores (0..1)
    metrics: dict  # metrics_dict from score_symbol
    pinning: Optional[dict] = None  # PricePinningResult.to_dict()
    # Phase B/C motors (optional — use proxies if absent)
    ceiling_break: Optional[dict] = None  # {ceiling_count, days_since_break, ...}
    retail_heat: Optional[float] = None  # 0..100
    gap_trap: Optional[float] = None  # 0..100
    sanction_events: list = field(default_factory=list)


# ── Trigger detection helpers ──────────────────────────────────────
def _date_n_days_ago(df, n: int) -> Optional[Any]:
    """Best-effort date for 'n days ago' from the last bar."""
    if not _PANDAS or df is None or len(df) <= n:
        return None
    try:
        return df.index[-1 - n]
    except Exception:
        return None


def _compression_present(state: SymbolState) -> bool:
    """ATR or BB compression sub-score >= 0.55"""
    cm = state.sub_scores.get("compression")
    return cm is not None and cm >= 0.55


def _patterns_normalized(state: SymbolState) -> list[str]:
    """A.8: pattern labels in metrics are title-case ('Absorption',
    'Walk-Up Accumulation'). Lowercase once so each detector below is
    robust against label case/format changes.
    """
    raw = state.metrics.get("patterns", []) or []
    return [str(p).lower() for p in raw]


def _float_lock_pattern(state: SymbolState) -> bool:
    """
    Float Lock proxy: float_pressure motor sub-score >= 0.55 OR
    20d turnover/floating heuristic high. Phase A doesn't have a
    proper Float Lock motor yet, so we use float_pressure + compression.
    """
    fp = state.sub_scores.get("float_pressure")
    return (fp is not None and fp >= 0.55) or _compression_present(state)


def _absorption_pattern(state: SymbolState) -> bool:
    """
    Absorption proxy: existing absorption pattern label OR
    silent_volume + recent down days with limited price damage.
    """
    patterns_lc = _patterns_normalized(state)
    if any("absorption" in p for p in patterns_lc):
        return True
    sv = state.sub_scores.get("silent_volume")
    return sv is not None and sv >= 0.55


def _shakeout_pattern(state: SymbolState) -> bool:
    """Spring/shakeout pattern from existing price-action engine."""
    patterns_lc = _patterns_normalized(state)
    return any("shakeout" in p for p in patterns_lc)


def _walk_up_pattern(state: SymbolState) -> bool:
    """Walk-up = early markup confirmation."""
    patterns_lc = _patterns_normalized(state)
    return any("walk" in p and "up" in p for p in patterns_lc)


def _tight_closes_pattern(state: SymbolState) -> bool:
    patterns_lc = _patterns_normalized(state)
    return any("tight" in p and "close" in p for p in patterns_lc)


def _pinning_present(state: SymbolState) -> bool:
    if not state.pinning:
        return False
    score = state.pinning.get("price_pinning_score") or 0
    return score >= 55


def _ceiling_series(state: SymbolState) -> bool:
    if state.ceiling_break:
        return state.ceiling_break.get("ceiling_count", 0) >= 2
    # Phase A proxy: 5d return > 20% suggests rapid up-moves (proxy for ceilings)
    df = state.df
    if df is None or len(df) < 6:
        return False
    try:
        ret_5d = (df["Close"].iloc[-1] - df["Close"].iloc[-6]) / df["Close"].iloc[-6]
        return float(ret_5d) > 0.20
    except Exception:
        return False


def _ceiling_break_recent(state: SymbolState) -> bool:
    if state.ceiling_break:
        dsb = state.ceiling_break.get("days_since_break")
        return dsb is not None and dsb <= 5
    return False  # no proxy available


def _retail_heat_high(state: SymbolState) -> bool:
    return (state.retail_heat or 0) >= 60


def _gap_trap_recurring(state: SymbolState) -> bool:
    return (state.gap_trap or 0) >= 50


def _down_volume_inversion(state: SymbolState) -> bool:
    """Down-day vol > up-day vol over last 20 days."""
    df = state.df
    if not _PANDAS or df is None or len(df) < 20:
        return False
    try:
        win = df.iloc[-20:]
        up_days = win[win["Close"] > win["Open"]]
        down_days = win[win["Close"] < win["Open"]]
        if len(up_days) == 0 or len(down_days) == 0:
            return False
        avg_up_vol = float(up_days["Volume"].mean())
        avg_down_vol = float(down_days["Volume"].mean())
        return avg_up_vol > 0 and (avg_down_vol / avg_up_vol) > 1.2
    except Exception:
        return False


def _sanction_event_recent(state: SymbolState) -> bool:
    return len(state.sanction_events) > 0  # Phase B will populate


def _follow_through_present(state: SymbolState) -> bool:
    """5d return > 10% with volume expansion."""
    df = state.df
    if not _PANDAS or df is None or len(df) < 21:
        return False
    try:
        ret_5d = (df["Close"].iloc[-1] - df["Close"].iloc[-6]) / df["Close"].iloc[-6]
        recent_vol = float(df["Volume"].iloc[-5:].mean())
        prior_vol = float(df["Volume"].iloc[-25:-5].mean())
        return float(ret_5d) > 0.10 and prior_vol > 0 and (recent_vol / prior_vol) > 1.3
    except Exception:
        return False


# ────────────────────────────────────────────────────────────────────
# Sequence templates
# ────────────────────────────────────────────────────────────────────
ACCUMULATION_SEQUENCE = [
    {"step": 1, "name": "float_lock_or_compression",
     "trigger": _float_lock_pattern,
     "label": "Float kilidi / sıkışma"},
    {"step": 2, "name": "absorption_pattern",
     "trigger": _absorption_pattern,
     "label": "Alıcı emilmesi"},
    {"step": 3, "name": "sanction_event",
     "trigger": _sanction_event_recent,
     "optional": True,
     "label": "Tedbir / brüt takas (opsiyonel)"},
    {"step": 4, "name": "spring_recovery",
     "trigger": _shakeout_pattern,
     "label": "Spring / shakeout recovery"},
    {"step": 5, "name": "breakout",
     "trigger": _walk_up_pattern,
     "label": "Hacim eşliğinde breakout"},
]

DISTRIBUTION_SEQUENCE = [
    {"step": 1, "name": "ceiling_series",
     "trigger": _ceiling_series,
     "label": "Tavan / hızlı yükseliş serisi"},
    {"step": 2, "name": "ceiling_break",
     "trigger": _ceiling_break_recent,
     "optional": True,  # may not have ceiling motor yet
     "label": "Tavan bozma"},
    {"step": 3, "name": "retail_heat_spike",
     "trigger": _retail_heat_high,
     "optional": True,  # may not have retail motor yet
     "label": "Retail ilgi patlaması"},
    {"step": 4, "name": "gap_trap_recurrence",
     "trigger": _gap_trap_recurring,
     "optional": True,  # may not have gap trap motor yet
     "label": "Tekrarlayan gap trap"},
    {"step": 5, "name": "volume_inversion",
     "trigger": _down_volume_inversion,
     "label": "Down-volume > up-volume"},
]

MARKUP_SEQUENCE = [
    {"step": 1, "name": "tight_closes_or_compression",
     "trigger": lambda s: _tight_closes_pattern(s) or _compression_present(s),
     "label": "Önceki sıkışma / dar kapanışlar"},
    {"step": 2, "name": "breakout",
     "trigger": _walk_up_pattern,
     "label": "Walk-up breakout"},
    {"step": 3, "name": "follow_through",
     "trigger": _follow_through_present,
     "label": "Hacim ile follow-through"},
    {"step": 4, "name": "ceiling_acceleration",
     "trigger": _ceiling_series,
     "optional": True,
     "label": "Tavan ivmelenmesi"},
]


def _evaluate_template(state: SymbolState, template: list,
                       template_name: str) -> dict:
    """Walk the template, count completed (and optional-completed) steps."""
    completed = []
    missing = []
    today_iso = dt.date.today().isoformat()

    for step in template:
        triggered = False
        try:
            triggered = bool(step["trigger"](state))
        except Exception:
            triggered = False

        if triggered:
            completed.append({
                "step": step["step"],
                "name": step["name"],
                "label": step["label"],
                "date": today_iso,  # we don't track exact event date in Phase A
            })
        else:
            missing.append({
                "step": step["step"],
                "name": step["name"],
                "label": step["label"],
                "optional": step.get("optional", False),
            })

    # Confidence = completed / non-optional steps
    required_steps = [s for s in template if not s.get("optional")]
    completed_required = [c for c in completed
                          if next((s for s in template
                                  if s["step"] == c["step"] and not s.get("optional")), None)]
    if not required_steps:
        confidence = 100 if completed else 0
    else:
        confidence = int(len(completed_required) / len(required_steps) * 100)

    # Bonus: optional steps that did fire add small confidence
    optional_completed = [c for c in completed
                          if next((s for s in template
                                  if s["step"] == c["step"] and s.get("optional")), None)]
    confidence = min(100, confidence + 5 * len(optional_completed))

    return {
        "playbook": template_name,
        "confidence": confidence,
        "sequence_events": completed,
        "missing_next_confirmation": [
            m["label"] for m in missing if not m["optional"]
        ][:2],
    }


def detect_playbook(state: SymbolState) -> PlaybookResult:
    """
    Evaluate all sequence templates; return the highest-confidence one.

    If max confidence < 30, returns UNCLEAR — random isolated signals,
    no coherent narrative.
    """
    candidates = [
        _evaluate_template(state, ACCUMULATION_SEQUENCE, "ACCUMULATION_SEQUENCE"),
        _evaluate_template(state, DISTRIBUTION_SEQUENCE, "DISTRIBUTION_SEQUENCE"),
        _evaluate_template(state, MARKUP_SEQUENCE, "MARKUP_SEQUENCE"),
    ]
    candidates.sort(key=lambda c: c["confidence"], reverse=True)

    best = candidates[0]
    if best["confidence"] < 30:
        return PlaybookResult(
            playbook="UNCLEAR",
            confidence=0,
            sequence_events=[],
            missing_next_confirmation=[],
            candidates=candidates,
        )

    return PlaybookResult(
        playbook=best["playbook"],
        confidence=best["confidence"],
        sequence_events=best["sequence_events"],
        missing_next_confirmation=best["missing_next_confirmation"],
        candidates=candidates,
    )

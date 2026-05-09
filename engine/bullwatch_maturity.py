"""
BullWatch v2 — Move Maturity Score (Addendum Module 6).

Concept
-------
Distinguishes EARLY accumulation candidates from LATE/EXHAUSTED pumps
that might look like "opportunity" by raw score but are actually risk.

Classes:
  EARLY     — accumulation phase, low retail attention, low position
  MID       — markup active, volume expanding, healthy advance
  LATE      — ceiling series, retail heat, high RSI, position high
  EXHAUSTED — ceiling broken + gap traps + weak close + retail peak

This module is critical because v1 BullWatch can't tell the difference
between a stock starting accumulation and a stock that has already been
pumped 50% — both can score high on individual motors. Move Maturity
is the temporal context.

Inputs (some optional):
  - df: OHLCV daily
  - retail_heat_score: from RetailHeat motor (Module 8 of engines spec)
  - gap_trap_score: from GapTrap motor
  - ceiling_break_result: from CeilingBreak motor (with ceiling_count etc.)

If optional inputs are None, the module degrades gracefully — uses
only the OHLCV-derivable signals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import numpy as np
    import pandas as pd
    _PANDAS = True
except Exception:
    _PANDAS = False


@dataclass
class MoveMaturityResult:
    maturity: str  # "EARLY" | "MID" | "LATE" | "EXHAUSTED" | "UNCLEAR"
    move_maturity_score: Optional[int]
    all_scores: dict = field(default_factory=dict)
    indicators: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "maturity": self.maturity,
            "score": self.move_maturity_score,
            "all_scores": self.all_scores,
            "indicators": self.indicators,
            "evidence": self.evidence,
        }


def _compute_rsi(closes, period: int = 14):
    """Simple RSI without external deps."""
    if len(closes) < period + 1:
        return 50.0
    try:
        deltas = pd.Series(closes).diff().dropna()
        gains = deltas.where(deltas > 0, 0.0)
        losses = -deltas.where(deltas < 0, 0.0)
        avg_gain = gains.rolling(period, min_periods=period).mean().iloc[-1]
        avg_loss = losses.rolling(period, min_periods=period).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - (100 / (1 + rs)))
    except Exception:
        return 50.0


def compute_move_maturity_score(
    df: Any,
    retail_heat_score: Optional[float] = None,
    gap_trap_score: Optional[float] = None,
    ceiling_break_result: Optional[dict] = None,
) -> MoveMaturityResult:
    """
    Classify a move's maturity stage.

    All inputs except df are optional — graceful degradation if motors
    haven't been run yet (Phase A doesn't include CeilingBreak/GapTrap/
    RetailHeat — those come in Phase B/C of the engines spec).
    """
    if not _PANDAS or df is None or len(df) < 60:
        return MoveMaturityResult(maturity="UNCLEAR", move_maturity_score=None)

    try:
        # Inputs from OHLCV
        ret_5d = float((df["Close"].iloc[-1] - df["Close"].iloc[-6]) / df["Close"].iloc[-6])
        ret_20d = float((df["Close"].iloc[-1] - df["Close"].iloc[-21]) / df["Close"].iloc[-21])

        lookback = min(252, len(df))
        high_12m = float(df["High"].iloc[-lookback:].max())
        low_12m = float(df["Low"].iloc[-lookback:].min())
        current = float(df["Close"].iloc[-1])
        if high_12m > low_12m:
            position_in_range = (current - low_12m) / (high_12m - low_12m)
        else:
            position_in_range = 0.5

        rsi = _compute_rsi(df["Close"].values)

        # Upper wick cluster (last 5 days) — bearish exhaustion signal
        last5 = df.iloc[-5:]
        upper_wicks = []
        for _, row in last5.iterrows():
            try:
                w = (float(row["High"]) - float(row["Close"])) / float(row["Close"])
                upper_wicks.append(w)
            except Exception:
                pass
        upper_wick_pct = sum(upper_wicks) / len(upper_wicks) if upper_wicks else 0.0
        has_wick_cluster = upper_wick_pct > 0.02

        # Volume climax (last 5d max vs prior 30d avg)
        recent_max_vol = float(df["Volume"].iloc[-5:].max())
        prior_window = df["Volume"].iloc[-35:-5] if len(df) >= 35 else df["Volume"].iloc[:-5]
        prior_avg_vol = float(prior_window.mean()) if len(prior_window) > 0 else 1.0
        vol_climax = recent_max_vol / prior_avg_vol if prior_avg_vol > 0 else 1.0

        # Optional inputs — None-safe
        retail_heat = float(retail_heat_score) if retail_heat_score is not None else 0.0
        gap_trap = float(gap_trap_score) if gap_trap_score is not None else 0.0
        cb = ceiling_break_result or {}
        ceilings = int(cb.get("ceiling_count", 0))
        days_since_break = cb.get("days_since_break")

        indicators = {
            "ret_5d": round(ret_5d, 3),
            "ret_20d": round(ret_20d, 3),
            "position_in_range": round(position_in_range, 2),
            "rsi": round(rsi, 1),
            "ceiling_count": ceilings,
            "wick_cluster": has_wick_cluster,
            "vol_climax_ratio": round(vol_climax, 2),
            "retail_heat": retail_heat,
            "gap_trap": gap_trap,
        }

        # ── Class scores ──────────────────────────────────────────────
        # EXHAUSTED: ceiling break + gap trap + weak close + retail peak
        exhausted = 0
        if days_since_break is not None and days_since_break <= 2:
            exhausted += 25
        if gap_trap >= 50:
            exhausted += 20
        if has_wick_cluster:
            exhausted += 15
        if retail_heat >= 60:
            exhausted += 20
        if rsi > 75:
            exhausted += 10
        if position_in_range > 0.85:
            exhausted += 10

        # LATE: tavan series + retail heat + high RSI + position high
        late = 0
        if ceilings >= 2:
            late += 25
        if retail_heat >= 50:
            late += 20
        if rsi > 70:
            late += 15
        if position_in_range > 0.7:
            late += 20
        if ret_20d > 0.30:
            late += 10
        if vol_climax > 3:
            late += 10

        # MID: markup active, healthy expansion
        mid = 0
        if 0.05 < ret_20d < 0.30:
            mid += 25
        if 0.4 < position_in_range < 0.7:
            mid += 25
        if vol_climax > 1.5:
            mid += 20
        if 50 < rsi < 70:
            mid += 20
        if retail_heat < 40:
            mid += 10

        # EARLY: accumulation indicators
        early = 0
        if abs(ret_20d) < 0.05:
            early += 25
        if position_in_range < 0.4:
            early += 25
        if retail_heat < 30:
            early += 20
        if 40 < rsi < 60:
            early += 20
        if ceilings == 0:
            early += 10

        scores = {"EARLY": early, "MID": mid, "LATE": late, "EXHAUSTED": exhausted}
        # Pick the highest. Ties: prefer riskier read (EXHAUSTED > LATE > MID > EARLY)
        # — safer default for users who might otherwise read EARLY into a late move.
        risk_order = ["EXHAUSTED", "LATE", "MID", "EARLY"]
        max_score = max(scores.values())
        if max_score == 0:
            return MoveMaturityResult(
                maturity="UNCLEAR",
                move_maturity_score=0,
                all_scores=scores,
                indicators=indicators,
            )

        winners = [k for k in risk_order if scores[k] == max_score]
        maturity = winners[0]

        # ── Build evidence list (Turkish, observation-only) ──
        evidence = []
        if maturity == "EXHAUSTED":
            if days_since_break is not None and days_since_break <= 2:
                evidence.append(f"Tavan {days_since_break} gün önce kırıldı")
            if gap_trap >= 50:
                evidence.append(f"Gap trap skoru {int(gap_trap)}")
            if has_wick_cluster:
                evidence.append("Son 5 günde üst gölge kümesi")
            if retail_heat >= 60:
                evidence.append(f"Retail ilgisi {int(retail_heat)}")
            if rsi > 75:
                evidence.append(f"RSI {rsi:.0f} — overbought zone")
        elif maturity == "LATE":
            if ceilings >= 2:
                evidence.append(f"{ceilings} tavan günü son 10g")
            if retail_heat >= 50:
                evidence.append(f"Retail ilgisi {int(retail_heat)}")
            if rsi > 70:
                evidence.append(f"RSI {rsi:.0f} — overbought")
            if position_in_range > 0.7:
                evidence.append(f"12-aylık range'in %{int(position_in_range*100)} dilimi")
            if ret_20d > 0.30:
                evidence.append(f"Son 20 günde %{ret_20d*100:.0f}")
        elif maturity == "MID":
            evidence.append(f"Son 20 günde %{ret_20d*100:.0f} (sağlıklı ilerleme)")
            evidence.append(f"Range'in %{int(position_in_range*100)} dilimi (orta)")
            if vol_climax > 1.5:
                evidence.append(f"Hacim {vol_climax:.1f}x normal")
            if 50 < rsi < 70:
                evidence.append(f"RSI {rsi:.0f} (sağlıklı)")
        else:  # EARLY
            if abs(ret_20d) < 0.05:
                evidence.append(f"Son 20 günde %{ret_20d*100:.1f} (yatay)")
            if position_in_range < 0.4:
                evidence.append(f"Range'in %{int(position_in_range*100)} alt dilimi")
            if retail_heat < 30:
                evidence.append("Retail ilgisi düşük")
            if ceilings == 0:
                evidence.append("Tavan yok")

        return MoveMaturityResult(
            maturity=maturity,
            move_maturity_score=int(max_score),
            all_scores=scores,
            indicators=indicators,
            evidence=evidence,
        )
    except Exception:
        return MoveMaturityResult(maturity="UNCLEAR", move_maturity_score=None)

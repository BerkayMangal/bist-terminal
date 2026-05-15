"""
BullWatch v2 — Price Pinning / Control Band Score (Addendum Module 2).

Concept
-------
Different from Float Lock:
  Float Lock asks: is a large portion of float turning over?
  Price Pinning asks: is the price being deliberately held in a narrow band?

Together they're powerful: high Float Lock + high Price Pinning means
"organized lot transfer happening AND price is being controlled" — the
classic accumulation-with-suppression signature in Turkish micro-caps.

Signals computed:
  - close_band_width — narrowest band that contains 70%+ of last 20 closes
  - closes_inside_band_pct — what % of closes fall inside that band
  - wick_reversion_ratio — how often did intraday push outside the band
                          but the daily close revert back inside?
  - vol_above_avg_during_band — volume context: is suspicious when above-
                                avg volume persists while price is pinned

Output is a 0-100 score plus the detected control band [low, high].

Note on philosophy: the module reports observation, never recommendation.
"Fiyat kontrol ediliyor olabilir" — only language used.
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
class PricePinningResult:
    score: Optional[int]
    control_band: Optional[list[float]] = None  # [low, high]
    closes_inside_band_pct: Optional[int] = None
    band_width_pct: Optional[float] = None
    wick_reversion_ratio: Optional[float] = None
    vol_ratio: Optional[float] = None
    components: dict = field(default_factory=dict)
    interpretation: str = "yok"

    def to_dict(self) -> dict:
        return {
            "price_pinning_score": self.score,
            "control_band": self.control_band,
            "closes_inside_band_pct": self.closes_inside_band_pct,
            "band_width_pct": self.band_width_pct,
            "wick_reversion_ratio": self.wick_reversion_ratio,
            "vol_ratio": self.vol_ratio,
            "components": self.components,
            "interpretation": self.interpretation,
        }


# Candidate band widths to try (fraction of price). Best = narrowest that
# still contains the inclusion threshold.
_BAND_WIDTHS = (0.015, 0.02, 0.025, 0.03, 0.04, 0.05)
# Minimum % of closes that must fall inside the band for it to qualify.
_MIN_INCLUSION = 0.70


def _find_best_band(closes: list[float]) -> Optional[tuple[float, float, float, float]]:
    """
    Find the narrowest band centered on median that contains >= 70% of closes.
    Returns (low, high, band_width_pct, inclusion_pct) or None.
    """
    if not closes:
        return None
    median = float(sorted(closes)[len(closes) // 2])
    best = None
    for w in _BAND_WIDTHS:
        low = median * (1 - w)
        high = median * (1 + w)
        inside = sum(1 for c in closes if low <= c <= high)
        inclusion = inside / len(closes)
        if inclusion >= _MIN_INCLUSION:
            # Narrowest qualifying band wins (we iterate ascending width)
            best = (low, high, w, inclusion)
            break
    return best


def compute_price_pinning_score(df: Any, lookback_days: int = 20) -> PricePinningResult:
    """
    Compute Price Pinning score on the trailing window.

    Returns a PricePinningResult; score=None if data is insufficient.
    Never raises — degrades gracefully.
    """
    if not _PANDAS or df is None or len(df) < lookback_days:
        return PricePinningResult(score=None)

    try:
        window = df.iloc[-lookback_days:]
        baseline = df.iloc[-(lookback_days + 30):-lookback_days] if len(df) >= lookback_days + 30 else None

        closes = [float(c) for c in window["Close"].values if c == c]  # filter NaN
        if len(closes) < lookback_days:
            return PricePinningResult(score=None)

        best_band = _find_best_band(closes)
        if best_band is None:
            # No narrow band qualifies → no pinning
            return PricePinningResult(
                score=10,
                control_band=None,
                closes_inside_band_pct=0,
                band_width_pct=None,
                wick_reversion_ratio=None,
                vol_ratio=None,
                interpretation="yok",
            )

        low, high, band_width, inclusion = best_band

        # ── Wick reversion: did intraday push outside, then close revert?
        excursions = 0
        reversions = 0
        for _, row in window.iterrows():
            try:
                h = float(row["High"])
                lo = float(row["Low"])
                c = float(row["Close"])
            except (TypeError, ValueError):
                continue
            broke_high = h > high * 1.005
            broke_low = lo < low * 0.995
            closed_inside = low <= c <= high
            if broke_high or broke_low:
                excursions += 1
                if closed_inside:
                    reversions += 1
        wick_reversion = (reversions / excursions) if excursions > 0 else 0.0

        # ── Volume context: is above-avg vol persisting during pinning?
        vol_ratio = 1.0
        if baseline is not None and len(baseline) >= 10:
            try:
                avg_window = float(window["Volume"].mean())
                avg_baseline = float(baseline["Volume"].mean())
                if avg_baseline > 0:
                    vol_ratio = avg_window / avg_baseline
            except Exception:
                pass

        # ── Score components
        components: dict = {}

        # Band tightness (0-40)
        if band_width <= 0.02:
            components["tightness"] = 40
        elif band_width <= 0.03:
            components["tightness"] = 30
        elif band_width <= 0.04:
            components["tightness"] = 15
        else:
            components["tightness"] = 0

        # Inclusion (0-30)
        if inclusion >= 0.85:
            components["inclusion"] = 30
        elif inclusion >= 0.75:
            components["inclusion"] = 20
        else:
            components["inclusion"] = 10

        # Wick reversion (0-15)
        if wick_reversion >= 0.7:
            components["wick_reversion"] = 15
        elif wick_reversion >= 0.5:
            components["wick_reversion"] = 8
        else:
            components["wick_reversion"] = 0

        # Volume context (0-15) — high vol while pinned is suspicious
        if vol_ratio >= 1.3:
            components["volume_context"] = 15
        elif vol_ratio >= 1.1:
            components["volume_context"] = 8
        else:
            components["volume_context"] = 0

        score = sum(components.values())

        # Interpretation (Turkish, observation-only)
        if score >= 75:
            interp = "fiyat_kontrol_ediliyor_olabilir"
        elif score >= 55:
            interp = "orta_seviye_pinning"
        elif score >= 35:
            interp = "zayıf_işaret"
        else:
            interp = "yok"

        return PricePinningResult(
            score=int(score),
            control_band=[round(low, 2), round(high, 2)],
            closes_inside_band_pct=int(round(inclusion * 100)),
            band_width_pct=round(band_width * 100, 2),
            wick_reversion_ratio=round(wick_reversion, 2),
            vol_ratio=round(vol_ratio, 2),
            components=components,
            interpretation=interp,
        )
    except Exception:
        return PricePinningResult(score=None)

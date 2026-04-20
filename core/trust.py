# ================================================================
# BISTBULL TERMINAL — CORE TRUST MODEL
# core/trust.py
#
# Every data point in the system must carry truth metadata.
# This is the contract between Truth, Decision, and Narrative layers.
#
# Classifications:
#   trusted_delayed   — real market data, EOD delayed (yfinance)
#   trusted_periodic  — official source, updated per meeting/quarter
#   derived           — calculated from trusted inputs (scores, regimes)
#   estimated         — manually entered, not verified live
#   editorial         — human opinion, not algorithmic
#   ai_generated      — LLM output, never treated as fact
#   fake_placeholder  — hardcoded zero/dummy — MUST BE REMOVED
# ================================================================

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ================================================================
# CLASSIFICATIONS
# ================================================================
class Classification:
    TRUSTED_DELAYED = "trusted_delayed"
    TRUSTED_PERIODIC = "trusted_periodic"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    EDITORIAL = "editorial"
    AI_GENERATED = "ai_generated"
    FAKE_PLACEHOLDER = "fake_placeholder"


# What can feed the decision engine
DECISION_ELIGIBLE = {
    Classification.TRUSTED_DELAYED,
    Classification.TRUSTED_PERIODIC,
    Classification.DERIVED,
    Classification.ESTIMATED,      # allowed but with reduced confidence
}

# What can feed AI prompts
AI_ELIGIBLE = {
    Classification.TRUSTED_DELAYED,
    Classification.TRUSTED_PERIODIC,
    Classification.DERIVED,
    Classification.ESTIMATED,      # with explicit "tahmini" note
    Classification.EDITORIAL,      # with explicit "görüş" note
}

# What can appear in hero / prominent UI
HERO_ELIGIBLE = {
    Classification.TRUSTED_DELAYED,
    Classification.TRUSTED_PERIODIC,
    Classification.DERIVED,
    Classification.ESTIMATED,      # with visible warning
}

# NEVER enters any pipeline
BANNED = {
    Classification.FAKE_PLACEHOLDER,
}


# ================================================================
# DATA POINT
# ================================================================
@dataclass
class DataPoint:
    """Every important data value in the system."""
    value: Any
    source: str                     # "yfinance", "borsapy", "TCMB", "manuel", "hesaplama", "AI"
    classification: str             # one of Classification.*
    timestamp: Optional[str] = None # ISO format
    freshness_label: str = ""       # "Günlük", "Manuel · 11 Nis", "Tahmini", etc.
    stale: bool = False
    notes: str = ""                 # any caveats

    # Computed properties
    @property
    def safe_for_decision(self) -> bool:
        return self.classification in DECISION_ELIGIBLE and not self.stale_critical

    @property
    def safe_for_ai(self) -> bool:
        return self.classification in AI_ELIGIBLE

    @property
    def safe_for_hero(self) -> bool:
        return self.classification in HERO_ELIGIBLE

    @property
    def is_estimated(self) -> bool:
        return self.classification == Classification.ESTIMATED

    @property
    def is_editorial(self) -> bool:
        return self.classification == Classification.EDITORIAL

    @property
    def is_fake(self) -> bool:
        return self.classification == Classification.FAKE_PLACEHOLDER

    @property
    def stale_critical(self) -> bool:
        """Stale beyond usability — 30+ days for estimated, 3+ days for delayed."""
        if not self.timestamp:
            return self.classification == Classification.ESTIMATED
        try:
            ts = dt.datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
            age_days = (dt.datetime.now(dt.timezone.utc) - ts).days
            if self.classification == Classification.TRUSTED_DELAYED:
                return age_days > 3
            elif self.classification in (Classification.ESTIMATED, Classification.TRUSTED_PERIODIC):
                return age_days > 30
            return False
        except Exception:
            return False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["safe_for_decision"] = self.safe_for_decision
        d["safe_for_ai"] = self.safe_for_ai
        d["safe_for_hero"] = self.safe_for_hero
        d["is_estimated"] = self.is_estimated
        return d


# ================================================================
# GUARD FUNCTIONS — fail-safe, never crash production
# ================================================================
import logging as _log
_guard_log = _log.getLogger("bistbull.trust.guard")


def guard_decision(dp: DataPoint, field_name: str = "") -> bool:
    """Check if a DataPoint is eligible for the decision engine.
    Returns False and logs for ineligible data. Never raises."""
    if dp.is_fake:
        _guard_log.warning(f"BLOCKED fake/placeholder: '{field_name}'")
        return False
    if dp.classification == Classification.AI_GENERATED:
        _guard_log.warning(f"BLOCKED AI output from decision: '{field_name}'")
        return False
    if dp.classification == Classification.EDITORIAL:
        _guard_log.warning(f"BLOCKED editorial from decision: '{field_name}'")
        return False
    return dp.safe_for_decision


def guard_ai(dp: DataPoint, field_name: str = "") -> bool:
    """Check if a DataPoint is eligible for AI prompts."""
    if dp.is_fake:
        return False
    return dp.safe_for_ai


def filter_decision_inputs(raw: dict[str, Any], classifications: dict[str, str]) -> tuple[dict, int]:
    """Filter raw inputs, excluding ineligible fields.
    Returns (clean_inputs, excluded_count) — never crashes.
    classifications maps field_name → Classification constant."""
    clean = {}
    excluded = 0
    for key, value in raw.items():
        cls = classifications.get(key, Classification.TRUSTED_DELAYED)
        dp = DataPoint(value=value, source="auto", classification=cls)
        if guard_decision(dp, key):
            clean[key] = value
        else:
            excluded += 1
    return clean, excluded


def check_minimum_data(datapoints: dict[str, DataPoint], min_trusted: int = 3) -> bool:
    """Check if enough trusted data exists to generate AI commentary."""
    trusted_count = sum(
        1 for dp in datapoints.values()
        if dp.classification in (Classification.TRUSTED_DELAYED, Classification.TRUSTED_PERIODIC, Classification.DERIVED)
        and not dp.stale_critical
    )
    return trusted_count >= min_trusted


# ================================================================
# FRESHNESS LABEL BUILDER
# ================================================================
def build_freshness_label(classification: str, source: str, timestamp: Optional[str] = None) -> str:
    """Human-readable Turkish freshness label."""
    if classification == Classification.FAKE_PLACEHOLDER:
        return "Veri yok"
    if classification == Classification.AI_GENERATED:
        return "AI Yorum"
    if classification == Classification.EDITORIAL:
        return "Editöryal görüş"
    if classification == Classification.ESTIMATED:
        date_part = ""
        if timestamp:
            try:
                d = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                date_part = f" · {d.strftime('%d %b')}"
            except Exception:
                pass
        return f"Tahmini{date_part}"

    # Trusted delayed/periodic/derived
    if not timestamp:
        return f"Günlük · {source}"

    try:
        ts = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        age = dt.datetime.now(dt.timezone.utc) - ts
        if age.total_seconds() < 3600:
            return f"Güncel · {source}"
        elif age.days == 0:
            return f"Günlük · {source}"
        elif age.days <= 7:
            return f"Bu hafta · {source}"
        elif age.days <= 14:
            return f"Geçen hafta · {source}"
        else:
            return f"Eski · {source}"
    except Exception:
        return f"Günlük · {source}"

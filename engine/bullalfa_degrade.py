# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# engine/bullalfa_degrade.py
#
# §15 — degradation & failure behavior.
#
# Every external call in `engine/bullalfa.py` is wrapped in
# try/except. On failure, the orchestrator records a degradation
# code from this module and applies the prescribed action.
#
# The full v1.4 matrix (spec §15):
#
#   macro_unavailable        → assume_neutral
#                              caveat "Rejim tespit edilemedi"
#   pit_missing              → force_mode_sakin
#                              caveat "Geçmiş veri eksik"
#   aggregation_failed       → force_mode_sakin
#                              caveat "Temel veri hesaplanamadı"
#   technical_failed         → force_mode_sakin
#                              caveat "Teknik veri eksik"
#   freshness_below_60       → force_mode_sakin
#                              caveat "Veri çok eski"
#   short_history            → limit_modes_hizli_toplaniyor_sakin
#                              caveat "Kısa geçmiş — POZİSYON/SWING devre dışı"
#   halted_today             → force_mode_uzak_dur
#                              caveat "İşlem durdurulmuş"
#   out_of_session           → freeze_existing
#                              caveat "Kapalı seans"
#   benchmark_index_missing  → fallback_to_xu100
#                              caveat "Sektör endeksi yok, XU100 referansı"
#   isotonic_unavailable     → use_sigmoid_v1
#                              caveat "Kalibrasyon: ön-aşama"
#
# Key change from v1.3 (§15 last paragraph): failure modes now
# degrade to SAKİN (visible) rather than excluding from universe.
# Show every BIST stock — quality informs but never gates.
# ================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

__all__ = [
    "DegradeCode",
    "DegradeAction",
    "DEGRADATION_RULES",
    "DegradationOutcome",
    "DegradationLog",
    "rule_for",
    "caveat_for",
    "action_for",
]


# ----------------------------------------------------------------
# Code + action enums (string constants — keep stable across versions)
# ----------------------------------------------------------------

class DegradeCode:
    """All degradation codes the orchestrator may emit. String values
    are stable identifiers used in tests and logs; do not rename."""

    MACRO_UNAVAILABLE       = "macro_unavailable"
    PIT_MISSING             = "pit_missing"
    AGGREGATION_FAILED      = "aggregation_failed"
    TECHNICAL_FAILED        = "technical_failed"
    FRESHNESS_BELOW_60      = "freshness_below_60"
    SHORT_HISTORY           = "short_history"
    HALTED_TODAY            = "halted_today"
    OUT_OF_SESSION          = "out_of_session"
    BENCHMARK_INDEX_MISSING = "benchmark_index_missing"
    ISOTONIC_UNAVAILABLE    = "isotonic_unavailable"


class DegradeAction:
    """Stable action names. The orchestrator dispatches on these."""

    ASSUME_NEUTRAL                       = "assume_neutral"
    FORCE_MODE_SAKIN                     = "force_mode_sakin"
    LIMIT_MODES_HIZLI_TOPLANIYOR_SAKIN   = "limit_modes_hizli_toplaniyor_sakin"
    FORCE_MODE_UZAK_DUR                  = "force_mode_uzak_dur"
    FREEZE_EXISTING                      = "freeze_existing"
    FALLBACK_TO_XU100                    = "fallback_to_xu100"
    USE_SIGMOID_V1                       = "use_sigmoid_v1"


# ----------------------------------------------------------------
# Rules table (mirrors spec §15 verbatim)
# ----------------------------------------------------------------

DEGRADATION_RULES: dict[str, dict[str, str]] = {
    DegradeCode.MACRO_UNAVAILABLE: {
        "action": DegradeAction.ASSUME_NEUTRAL,
        "caveat": "Rejim tespit edilemedi",
    },
    DegradeCode.PIT_MISSING: {
        "action": DegradeAction.FORCE_MODE_SAKIN,
        "caveat": "Geçmiş veri eksik",
    },
    DegradeCode.AGGREGATION_FAILED: {
        "action": DegradeAction.FORCE_MODE_SAKIN,
        "caveat": "Temel veri hesaplanamadı",
    },
    DegradeCode.TECHNICAL_FAILED: {
        "action": DegradeAction.FORCE_MODE_SAKIN,
        "caveat": "Teknik veri eksik",
    },
    DegradeCode.FRESHNESS_BELOW_60: {
        "action": DegradeAction.FORCE_MODE_SAKIN,
        "caveat": "Veri çok eski",
    },
    DegradeCode.SHORT_HISTORY: {
        "action": DegradeAction.LIMIT_MODES_HIZLI_TOPLANIYOR_SAKIN,
        "caveat": "Kısa geçmiş — POZİSYON/SWING devre dışı",
    },
    DegradeCode.HALTED_TODAY: {
        "action": DegradeAction.FORCE_MODE_UZAK_DUR,
        "caveat": "İşlem durdurulmuş",
    },
    DegradeCode.OUT_OF_SESSION: {
        "action": DegradeAction.FREEZE_EXISTING,
        "caveat": "Kapalı seans",
    },
    DegradeCode.BENCHMARK_INDEX_MISSING: {
        "action": DegradeAction.FALLBACK_TO_XU100,
        "caveat": "Sektör endeksi yok, XU100 referansı",
    },
    DegradeCode.ISOTONIC_UNAVAILABLE: {
        "action": DegradeAction.USE_SIGMOID_V1,
        "caveat": "Kalibrasyon: ön-aşama",
    },
}


# ----------------------------------------------------------------
# Outcome dataclasses
# ----------------------------------------------------------------

@dataclass(frozen=True)
class DegradationOutcome:
    """The shape returned by `rule_for(code)` — convenient typed access."""

    code:   str
    action: str
    caveat: str


@dataclass
class DegradationLog:
    """Mutable per-signal log of degradations encountered in this pipeline.

    Pattern: orchestrator owns one log per BullAlfaSignal. Each external
    call (macro, aggregation, technical, etc.) wrapped in try/except;
    on failure, log.record(code) is called. The log accumulates codes
    in order, and the assembler reads it to populate caveats and to
    decide forced-mode/limited-mode routing.

    The log is intentionally append-only and order-preserving — a
    signal can hit `short_history` AND `benchmark_index_missing` AND
    `isotonic_unavailable` all on the same bar (e.g. a newly-listed
    REIT pre-v2). The assembler must surface all three caveats.
    """

    codes: list[str] = field(default_factory=list)

    def record(self, code: str) -> None:
        """Append a degradation code if it isn't already present."""
        if code not in DEGRADATION_RULES:
            raise ValueError(f"unknown degradation code: {code!r}")
        if code not in self.codes:
            self.codes.append(code)

    # -- queries ----------------------------------------------------

    def has(self, code: str) -> bool:
        return code in self.codes

    def any_force_sakin(self) -> bool:
        """True if any logged code's action is FORCE_MODE_SAKIN."""
        return any(
            DEGRADATION_RULES[c]["action"] == DegradeAction.FORCE_MODE_SAKIN
            for c in self.codes
        )

    def any_force_uzak_dur(self) -> bool:
        return any(
            DEGRADATION_RULES[c]["action"] == DegradeAction.FORCE_MODE_UZAK_DUR
            for c in self.codes
        )

    def any_freeze(self) -> bool:
        return any(
            DEGRADATION_RULES[c]["action"] == DegradeAction.FREEZE_EXISTING
            for c in self.codes
        )

    def limited_mode_set(self) -> Optional[frozenset[str]]:
        """Mode set the orchestrator is restricted to.

        Returns None when no limiting code has been recorded; otherwise
        the intersection of every limiting code's allowed-set. v1.4
        defines exactly one limiting action so this currently returns
        the SHORT_HISTORY allowed-set, but the structure tolerates
        future limiters.
        """
        limited: Optional[frozenset[str]] = None
        for c in self.codes:
            action = DEGRADATION_RULES[c]["action"]
            if action == DegradeAction.LIMIT_MODES_HIZLI_TOPLANIYOR_SAKIN:
                allowed = frozenset({"HIZLI", "TOPLANIYOR", "SAKİN"})
                limited = allowed if limited is None else (limited & allowed)
        return limited

    def caveats(self) -> list[str]:
        """User-facing TR caveats in the order the codes were recorded."""
        return [DEGRADATION_RULES[c]["caveat"] for c in self.codes]


# ----------------------------------------------------------------
# Convenience accessors
# ----------------------------------------------------------------

def rule_for(code: str) -> DegradationOutcome:
    """Return the typed (action, caveat) for a code. Raises on unknown code."""
    if code not in DEGRADATION_RULES:
        raise KeyError(f"unknown degradation code: {code!r}")
    rule = DEGRADATION_RULES[code]
    return DegradationOutcome(code=code, action=rule["action"], caveat=rule["caveat"])


def action_for(code: str) -> str:
    """Return just the action string for a code."""
    return rule_for(code).action


def caveat_for(code: str) -> str:
    """Return just the Turkish caveat for a code."""
    return rule_for(code).caveat


def caveats_for(codes: Iterable[str]) -> list[str]:
    """Vectorised caveat lookup — preserves input order."""
    return [caveat_for(c) for c in codes]

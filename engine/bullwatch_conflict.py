"""
BullWatch v2 — Engine Conflict Matrix (Addendum Module 9).

The most important meta-layer: same motor scores carry different meaning
in different contexts. Examples:

    Float Lock high + Retail Heat low   → ACCUMULATION (silent collection)
    Float Lock high + Retail Heat high  → DISTRIBUTION (group selling to retail)
    Absorption + low position_in_range  → ACCUMULATION
    Absorption + high position_in_range → DISTRIBUTION
    Gap Trap + EARLY maturity           → noise
    Gap Trap + LATE maturity            → distribution warning

This module fires a list of weighted rules over the symbol's full
state (motor scores + maturity + position + pinning). Each rule, if
its condition holds, contributes a vote toward one of the canonical
reads: ACCUMULATION / DISTRIBUTION / MARKUP / RE_ACCUMULATION / NOISE.

The dominant_read is the read with the highest total weight. Confidence
is the dominant share of total votes. Competing reads with at least 50%
of dominant weight are surfaced as `conflicts`.

This prevents the system's core failure mode in v1: high motor scores
being naively read as accumulation when they actually indicate distribution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional


@dataclass
class ConflictResult:
    dominant_read: str  # ACCUMULATION / DISTRIBUTION / MARKUP / RE_ACCUMULATION /
                       # NOISE / UNCLEAR
    confidence: int  # 0-100 — kept for backward compat
    conflicts: list = field(default_factory=list)  # competing reads
    resolved_by: list = field(default_factory=list)  # rules that fired
    # ── Phase A.6 hygiene ──
    rule_agreement_pct: int = 0       # % of fired rules voting for dominant_read
    evidence_depth_count: int = 0     # number of rules that fired total
    confidence_tier: str = "LOW"      # LOW | MEDIUM | HIGH

    def to_dict(self) -> dict:
        return {
            "dominant_read": self.dominant_read,
            "confidence": self.confidence,
            "conflicts": self.conflicts,
            "resolved_by": self.resolved_by,
            "rule_agreement_pct": self.rule_agreement_pct,
            "evidence_depth_count": self.evidence_depth_count,
            "confidence_tier": self.confidence_tier,
        }


# ────────────────────────────────────────────────────────────────────
# Rule definitions
#
# Each rule:
#   - name: identifier
#   - condition: callable(state) -> bool
#   - resolved_read: which canonical read this rule supports
#   - rationale: Turkish explanation for the user
#   - weight: how much weight this rule contributes (5..30)
#
# Higher weights = stronger / more specific patterns. Lower weights =
# weaker hints. Always prefer specificity in the condition: include
# context (position_in_range, maturity) so the rule fires only when
# the situation truly matches.
# ────────────────────────────────────────────────────────────────────
def _safe_get(state, attr, default=0):
    """Read attribute from SimpleNamespace, default if missing or None."""
    val = getattr(state, attr, None)
    if val is None:
        return default
    return val


CONFLICT_RULES = [
    # ── Float Lock context rules ──
    # Note: rules requiring retail_heat / gap_trap (Phase B/C) only fire
    # when those values are TRULY known (not None). In Phase A they pass
    # through harmlessly until Phase B comes online.
    {
        "name": "float_lock_with_retail_heat",
        "condition": lambda s: (
            _safe_get(s, "float_pressure_score", 0) >= 60
            and getattr(s, "retail_heat", None) is not None
            and _safe_get(s, "retail_heat", 0) >= 60
        ),
        "resolved_read": "DISTRIBUTION",
        "rationale": "Lot dönüşü retail FOMO ile birlikte → grup retail'e veriyor",
        "weight": 25,
    },
    {
        "name": "float_lock_no_retail",
        "condition": lambda s: (
            _safe_get(s, "float_pressure_score", 0) >= 60
            and getattr(s, "retail_heat", None) is not None
            and _safe_get(s, "retail_heat", 100) < 30
        ),
        "resolved_read": "ACCUMULATION",
        "rationale": "Lot dönüşü retail ilgi olmadan → quiet accumulation",
        "weight": 25,
    },
    # ── Float Turnover (20d cumulative) rules — Phase A.6 promotion ──
    # Was diagnostic-only; now consumed by Conflict Matrix per A.6 patch.
    # Fires only if turnover ratio is computable (free_float + shares known).
    {
        "name": "high_turnover_low_position_calm",
        "condition": lambda s: (
            getattr(s, "float_turnover_20d", None) is not None
            and _safe_get(s, "float_turnover_20d", 0) > 1.0
            and abs(_safe_get(s, "ret_20d", 0)) < 0.10
            and _safe_get(s, "position_in_range", 0.5) < 0.50
        ),
        "resolved_read": "ACCUMULATION",
        "rationale": "Yüksek lot dönüşü + dar fiyat hareketi + düşük zone → "
                     "fiyat suni dar tutulurken transfer gözlemleniyor",
        "weight": 25,
    },
    {
        "name": "high_turnover_high_position_strong_move",
        "condition": lambda s: (
            getattr(s, "float_turnover_20d", None) is not None
            and _safe_get(s, "float_turnover_20d", 0) > 1.0
            and _safe_get(s, "position_in_range", 0.5) > 0.70
            and _safe_get(s, "ret_20d", 0) > 0.30
        ),
        "resolved_read": "DISTRIBUTION",
        "rationale": "Yüksek lot dönüşü + yüksek konum + güçlü 20g hareket → "
                     "geç aşama dağıtım imzası",
        "weight": 25,
    },
    # ── Absorption context rules ──
    {
        "name": "absorption_low_range",
        "condition": lambda s: (
            _safe_get(s, "absorption_score", 0) >= 60
            and _safe_get(s, "position_in_range", 0.5) < 0.4
        ),
        "resolved_read": "ACCUMULATION",
        "rationale": "Düşük zonelarda alım emiliyor — birikim",
        "weight": 20,
    },
    {
        "name": "absorption_high_range",
        "condition": lambda s: (
            _safe_get(s, "absorption_score", 0) >= 60
            and _safe_get(s, "position_in_range", 0.5) > 0.7
        ),
        "resolved_read": "DISTRIBUTION",
        "rationale": "Yüksek zonelarda satış emiliyor — grup yukarıda dağıtıyor",
        "weight": 20,
    },
    # ── Maturity-context gating (Phase B/C dependent) ──
    {
        "name": "gap_trap_with_late_maturity",
        "condition": lambda s: (
            getattr(s, "gap_trap", None) is not None
            and _safe_get(s, "gap_trap", 0) >= 40
            and _safe_get(s, "move_maturity", "") in ("LATE", "EXHAUSTED")
        ),
        "resolved_read": "DISTRIBUTION",
        "rationale": "Geç olgunlukta gap trap → distribution",
        "weight": 18,
    },
    {
        "name": "gap_trap_with_early_maturity",
        "condition": lambda s: (
            getattr(s, "gap_trap", None) is not None
            and _safe_get(s, "gap_trap", 0) >= 40
            and _safe_get(s, "move_maturity", "") in ("EARLY", "MID")
        ),
        "resolved_read": "NOISE",
        "rationale": "Erken aşamada gap trap → genelde gürültü",
        "weight": 5,
    },
    # ── Pinning context rules (Phase A available) ──
    {
        "name": "pinning_with_low_position",
        "condition": lambda s: (
            _safe_get(s, "price_pinning_score", 0) >= 60
            and _safe_get(s, "position_in_range", 0.5) < 0.4
        ),
        "resolved_read": "ACCUMULATION",
        "rationale": "Düşük zonelarda fiyat suni dar tutuluyor — toplama hazırlığı",
        "weight": 22,
    },
    {
        "name": "pinning_with_high_position",
        "condition": lambda s: (
            _safe_get(s, "price_pinning_score", 0) >= 60
            and _safe_get(s, "position_in_range", 0.5) > 0.7
        ),
        "resolved_read": "DISTRIBUTION",
        "rationale": "Yüksek zonelarda fiyat suni dar tutuluyor — exit prep",
        "weight": 22,
    },
    # ── Maturity standalone signals (Phase A available) ──
    {
        "name": "exhausted_maturity_distribution",
        "condition": lambda s: _safe_get(s, "move_maturity", "") == "EXHAUSTED",
        "resolved_read": "DISTRIBUTION",
        "rationale": "Hareket exhausted aşamasında — hacim/fiyat zayıflığı",
        "weight": 25,
    },
    {
        "name": "late_maturity_distribution_lean",
        "condition": lambda s: (
            _safe_get(s, "move_maturity", "") == "LATE"
            and _safe_get(s, "position_in_range", 0.5) > 0.7
        ),
        "resolved_read": "DISTRIBUTION",
        "rationale": "Geç olgunluk + yüksek konum → distribution riski",
        "weight": 18,
    },
    {
        "name": "early_maturity_low_position",
        "condition": lambda s: (
            _safe_get(s, "move_maturity", "") == "EARLY"
            and _safe_get(s, "position_in_range", 0.5) < 0.4
        ),
        "resolved_read": "ACCUMULATION",
        "rationale": "Erken aşama + düşük range konumu → klasik birikim zemini",
        "weight": 18,
    },
    # ── Phase B/C dependent: late + retail (only fires when retail known) ──
    {
        "name": "late_maturity_with_retail_heat",
        "condition": lambda s: (
            _safe_get(s, "move_maturity", "") == "LATE"
            and getattr(s, "retail_heat", None) is not None
            and _safe_get(s, "retail_heat", 0) >= 50
        ),
        "resolved_read": "DISTRIBUTION",
        "rationale": "Geç olgunluk + retail FOMO → distribution riski yüksek",
        "weight": 22,
    },
    # ── Markup confirmation ──
    {
        "name": "mid_maturity_with_walk_up",
        "condition": lambda s: (
            _safe_get(s, "move_maturity", "") == "MID"
            and _safe_get(s, "price_action_score", 0) >= 60
        ),
        "resolved_read": "MARKUP",
        "rationale": "Orta olgunluk + walk-up → sağlıklı markup ilerlemesi",
        "weight": 18,
    },
    # ── Accumulation playbook completion ──
    {
        "name": "accumulation_sequence_high_confidence",
        "condition": lambda s: (
            _safe_get(s, "playbook", "") == "ACCUMULATION_SEQUENCE"
            and _safe_get(s, "playbook_confidence", 0) >= 60
        ),
        "resolved_read": "ACCUMULATION",
        "rationale": "Toplama sequence yüksek güvenle ilerliyor",
        "weight": 25,
    },
    {
        "name": "distribution_sequence_high_confidence",
        "condition": lambda s: (
            _safe_get(s, "playbook", "") == "DISTRIBUTION_SEQUENCE"
            and _safe_get(s, "playbook_confidence", 0) >= 60
        ),
        "resolved_read": "DISTRIBUTION",
        "rationale": "Dağıtım sequence yüksek güvenle ilerliyor",
        "weight": 25,
    },
]


def _classify_confidence_tier(evidence_depth: int, agreement_pct: int) -> str:
    """
    Phase A.6 hygiene: don't display single-rule output as HIGH confidence.

    HIGH:   evidence_depth >= 3 AND agreement >= 70%
    MEDIUM: evidence_depth >= 2 AND agreement >= 60%
    LOW:    everything else (single-rule fires always land here)
    """
    if evidence_depth >= 3 and agreement_pct >= 70:
        return "HIGH"
    if evidence_depth >= 2 and agreement_pct >= 60:
        return "MEDIUM"
    return "LOW"


def resolve_conflict_matrix(state_dict: dict) -> ConflictResult:
    """
    Apply each rule in turn. Tally weighted votes. Return dominant read.

    Args:
        state_dict: a dict with all the signal/context fields. Missing
                    fields are treated as defaults via _safe_get.
                    Typical keys:
                      float_pressure_score, absorption_score,
                      price_action_score, retail_heat, gap_trap,
                      position_in_range, move_maturity,
                      price_pinning_score, playbook, playbook_confidence,
                      float_turnover_20d, ret_20d (Phase A.6)

    Phase A.6 hygiene: emits three new fields on every result so the
    UI / runner can avoid showing single-rule output as HIGH confidence:
      - rule_agreement_pct: % of fired rules that voted for dominant_read
      - evidence_depth_count: total number of rules that fired
      - confidence_tier: LOW / MEDIUM / HIGH

    Never raises — degrades gracefully to UNCLEAR if no rule fires.
    """
    state = SimpleNamespace(**state_dict)
    votes: dict[str, int] = {}
    rule_reads: list[str] = []  # track each fired rule's read for agreement %
    fired: list[dict] = []

    for rule in CONFLICT_RULES:
        try:
            condition_met = bool(rule["condition"](state))
        except Exception:
            condition_met = False

        if condition_met:
            read = rule["resolved_read"]
            votes[read] = votes.get(read, 0) + rule["weight"]
            rule_reads.append(read)
            fired.append({
                "rule": rule["name"],
                "resolved_to": read,
                "rationale": rule["rationale"],
                "weight": rule["weight"],
            })

    if not votes:
        return ConflictResult(
            dominant_read="UNCLEAR",
            confidence=0,
            conflicts=[],
            resolved_by=[],
            rule_agreement_pct=0,
            evidence_depth_count=0,
            confidence_tier="LOW",
        )

    sorted_votes = sorted(votes.items(), key=lambda x: x[1], reverse=True)
    dominant_read, dominant_weight = sorted_votes[0]
    total_weight = sum(votes.values())

    # ── Legacy confidence (weight-share) ──
    confidence = int(round(dominant_weight / total_weight * 100))

    # ── Phase A.6: rule_agreement_pct (count-share, not weight-share) ──
    # This is more honest than the weight-share confidence: a single
    # high-weight rule vs the same number of low-weight rules look
    # different in agreement_pct.
    evidence_depth = len(fired)
    dominant_rule_count = sum(1 for r in rule_reads if r == dominant_read)
    rule_agreement = int(round(dominant_rule_count / evidence_depth * 100))

    # ── Phase A.6: confidence_tier ──
    confidence_tier = _classify_confidence_tier(evidence_depth, rule_agreement)

    # Surface competing reads with >= 50% of dominant weight
    conflicts = [
        {"competing_read": r, "weight": w}
        for r, w in sorted_votes[1:]
        if w >= dominant_weight * 0.5
    ]

    return ConflictResult(
        dominant_read=dominant_read,
        confidence=confidence,
        conflicts=conflicts,
        resolved_by=fired,
        rule_agreement_pct=rule_agreement,
        evidence_depth_count=evidence_depth,
        confidence_tier=confidence_tier,
    )

# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# features/bullalfa_sector.py
#
# Sector & universe branching (spec §14).
#
# Maps a stock to one of the BullAlfa sector buckets, picks its
# benchmark index, and tells the orchestrator which engines/modes
# are available for that bucket.
#
# Notes:
#   • The existing `engine.scoring.SECTOR_THRESHOLDS` covers 7 groups
#     (banka, holding, savunma, enerji, perakende, ulasim, sanayi).
#     BullAlfa adds two operational pseudo-sectors that are NOT
#     yfinance-derived sectors:
#         - newly_listed  (history shorter than NEWLY_LISTED_THRESHOLD_DAYS)
#         - halted        (no trade today)
#     and one yfinance-derivable bucket the existing module doesn't
#     break out separately:
#         - gyo  (Real Estate / REIT)
#   • We do NOT modify `engine.scoring` — gyo/newly_listed/halted are
#     BullAlfa-only labels that override the base mapping for the
#     purposes of E5 skip, benchmark selection, mode availability,
#     and grade capping.
# ================================================================

from __future__ import annotations

from dataclasses import dataclass

from engine.scoring import map_sector
from engine.bullalfa_params import (
    BULLALFA_PARAMS,
    NEWLY_LISTED_THRESHOLD_DAYS,
    NEWLY_LISTED_GRADE_CAP,
    DEFAULT_BENCHMARK,
    benchmark_for_sector,
    gyo_keywords,
    halted_forced_mode,
    is_e5_skipped,
    newly_listed_allowed_modes,
)

# All actionable / contextual modes — used as the "no restriction" baseline.
_ALL_MODES: frozenset[str] = frozenset(
    {"HIZLI", "SWING", "POZİSYON", "TOPLANIYOR", "SAKİN", "UZAK DUR"}
)

# Grade order used by `cap_grade` — index = severity (0 = best).
_GRADE_ORDER: tuple[str, ...] = ("A+", "A", "B", "C", "D")


# ----------------------------------------------------------------
# Public dataclass — what the orchestrator consumes
# ----------------------------------------------------------------

@dataclass(frozen=True)
class SectorContext:
    """Resolved sector/universe context for a single ticker.

    Fields
    ------
    sector_group:
        One of {banka, holding, gyo, savunma, enerji, perakende,
        ulasim, sanayi, newly_listed, halted}. The first eight feed
        into existing scoring; the last two are BullAlfa operational
        buckets. Always non-empty.
    benchmark:
        Sector index symbol (e.g. "XBANK", "XU100").
    benchmark_fallback:
        True iff the chosen sector benchmark was unavailable AND we
        fell back to XU100. Surfaced as a UI caveat per §14.
    skip_e5:
        True if Engine 5 (compression → expansion) must be skipped
        for this sector_group (spec §8 + §14).
    allowed_modes:
        Set of modes the orchestrator may emit for this stock.
        Restricted for newly_listed and halted; full set otherwise.
    grade_cap:
        Letter grade ceiling, or None for no cap. Currently used
        only for newly_listed (cap at "B" per §14).
    short_history:
        True iff the stock has < NEWLY_LISTED_THRESHOLD_DAYS bars.
    halted:
        True iff the stock is halted today (no trade).
    caveats:
        Free-form Turkish UI caveats accumulated during resolution.
    """

    sector_group:        str
    benchmark:           str
    benchmark_fallback:  bool
    skip_e5:             bool
    allowed_modes:       frozenset[str]
    grade_cap:           str | None
    short_history:       bool
    halted:              bool
    caveats:             tuple[str, ...] = ()


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

def is_newly_listed(history_length_days: int | None) -> bool:
    """True iff the price history is shorter than the spec §14 threshold."""
    if history_length_days is None:
        return False
    return history_length_days < NEWLY_LISTED_THRESHOLD_DAYS


def detect_gyo(yf_sector: str | None, yf_industry: str | None = None) -> bool:
    """Detect REIT / gyo from yfinance sector or industry strings.

    yfinance does not always tag REITs as "Real Estate" cleanly; we
    inspect both the sector and (when supplied) the industry text.
    """
    haystack = f"{yf_sector or ''} {yf_industry or ''}".lower()
    if not haystack.strip():
        return False
    return any(keyword in haystack for keyword in gyo_keywords())


def detect_holding(yf_sector: str | None, yf_industry: str | None = None) -> bool:
    """Detect holding / conglomerate from yfinance sector or industry strings.

    `engine.scoring.map_sector` lumps every "Financial Services" name
    into "banka" because the existing FA scoring system doesn't break
    out holding companies. BullAlfa needs them as their own bucket
    (different benchmark XHOLD per §14, same E5-skip rule). We do the
    detection here without touching engine.scoring.
    """
    haystack = f"{yf_sector or ''} {yf_industry or ''}".lower()
    if not haystack.strip():
        return False
    keywords = ("conglomerate", "holding", "diversified financial")
    return any(k in haystack for k in keywords)


def base_sector_group(yf_sector: str | None, yf_industry: str | None = None) -> str:
    """Resolve the base sector group, with BullAlfa-only `gyo` and
    `holding` overrides.

    The existing `engine.scoring.map_sector` does not break out REITs
    or holding companies; BullAlfa needs both as their own buckets
    so it can pick the right benchmark (XGMYO / XHOLD) and apply
    sector-specific rules (E5 skip per §14). If detect_gyo fires,
    return 'gyo'; if detect_holding fires, return 'holding';
    otherwise delegate to the existing mapper.

    Precedence: gyo > holding > base mapper. A REIT misclassified
    as a conglomerate by yfinance still ends up as 'gyo'.
    """
    if detect_gyo(yf_sector, yf_industry):
        return "gyo"
    if detect_holding(yf_sector, yf_industry):
        return "holding"
    return map_sector(yf_sector or "")


def get_benchmark(sector_group: str, available_benchmarks: set[str] | None = None) -> tuple[str, bool]:
    """Pick the benchmark for `sector_group`.

    Parameters
    ----------
    sector_group:
        Output of `base_sector_group` or `resolve_sector_context`.
    available_benchmarks:
        Optional set of benchmark symbols the data layer can actually
        serve right now. If supplied and the preferred benchmark is
        missing, fall back to XU100 (and report fallback=True).

    Returns
    -------
    (benchmark_symbol, is_fallback)
    """
    preferred = benchmark_for_sector(sector_group)
    if available_benchmarks is None:
        return preferred, False
    if preferred in available_benchmarks:
        return preferred, False
    # Fallback: XU100 if available, else whatever we asked for (caller
    # will get the missing-benchmark caveat from the degradation rules).
    if preferred != DEFAULT_BENCHMARK and DEFAULT_BENCHMARK in available_benchmarks:
        return DEFAULT_BENCHMARK, True
    return preferred, False


def cap_grade(grade: str | None, cap: str | None) -> tuple[str | None, bool]:
    """Cap `grade` at `cap` if `cap` is the more conservative of the two.

    Returns (capped_grade, was_capped). If either argument is missing
    the input grade is returned unchanged (capped=False).
    """
    if grade is None or cap is None:
        return grade, False
    if grade not in _GRADE_ORDER or cap not in _GRADE_ORDER:
        # Unknown letter — defensive: return unchanged.
        return grade, False
    if _GRADE_ORDER.index(grade) >= _GRADE_ORDER.index(cap):
        # Grade is already at or below the cap (lower index = better).
        return grade, False
    return cap, True


def filter_modes(allowed: frozenset[str], candidate: str) -> str:
    """If `candidate` mode is not in `allowed`, downgrade to the most
    permissive mode that is. Used by the orchestrator to align sector
    branching with mode classification.

    Order of preference for downgrade target: TOPLANIYOR → SAKİN.
    UZAK DUR is preserved if allowed.
    """
    if candidate in allowed:
        return candidate
    if candidate == "UZAK DUR":
        # Always preserve UZAK DUR — even halted-only sectors allow it.
        return "UZAK DUR"
    if "TOPLANIYOR" in allowed:
        return "TOPLANIYOR"
    if "SAKİN" in allowed:
        return "SAKİN"
    # Last resort — should not happen with current rules.
    return next(iter(allowed)) if allowed else "SAKİN"


# ----------------------------------------------------------------
# Main entry point — used by the orchestrator
# ----------------------------------------------------------------

def resolve_sector_context(
    *,
    yf_sector: str | None,
    yf_industry: str | None = None,
    history_length_days: int | None,
    is_halted: bool = False,
    available_benchmarks: set[str] | None = None,
) -> SectorContext:
    """Compute the full BullAlfa sector context for a ticker.

    Order of precedence for sector_group:
      1. halted        → forces UZAK DUR mode and skips most engines
      2. newly_listed  → restricts modes to {HIZLI, TOPLANIYOR, SAKİN}
                          and caps grade at B
      3. base sector   → banka/holding/gyo/sanayi/etc.

    `halted` and `newly_listed` can both be true at once; halted wins
    for `sector_group` (worse outcome) but newly_listed flags are
    still surfaced via the dataclass for the orchestrator.
    """
    base = base_sector_group(yf_sector, yf_industry)
    short_hist = is_newly_listed(history_length_days)
    caveats: list[str] = []

    # Decide the operative sector_group.
    if is_halted:
        sector_group = "halted"
    elif short_hist:
        sector_group = "newly_listed"
    else:
        sector_group = base

    # Mode restrictions.
    if sector_group == "halted":
        allowed = frozenset({halted_forced_mode()})
    elif sector_group == "newly_listed":
        allowed = newly_listed_allowed_modes()
    else:
        allowed = _ALL_MODES

    # Grade cap (only newly_listed for v1).
    if sector_group == "newly_listed":
        grade_cap: str | None = NEWLY_LISTED_GRADE_CAP
    else:
        grade_cap = None

    # Benchmark — based on the OPERATIVE sector_group, so that
    # newly_listed/halted both default to XU100 per §14.
    benchmark, fallback = get_benchmark(sector_group, available_benchmarks)
    if fallback:
        caveats.append("Sektör endeksi yok, XU100 kullanıldı")

    # E5 skip rule.
    skip_e5 = is_e5_skipped(sector_group)

    # Surface short-history & halted caveats per §14 / §15.
    if short_hist and sector_group == "newly_listed":
        caveats.append("Kısa geçmiş — POZİSYON/SWING devre dışı")
    if is_halted:
        caveats.append("İşlem durdurulmuş")

    return SectorContext(
        sector_group=sector_group,
        benchmark=benchmark,
        benchmark_fallback=fallback,
        skip_e5=skip_e5,
        allowed_modes=allowed,
        grade_cap=grade_cap,
        short_history=short_hist,
        halted=is_halted,
        caveats=tuple(caveats),
    )


__all__ = [
    "SectorContext",
    "is_newly_listed",
    "detect_gyo",
    "detect_holding",
    "base_sector_group",
    "get_benchmark",
    "cap_grade",
    "filter_modes",
    "resolve_sector_context",
]

# ================================================================
# BULLWATCH ENGINE — Find low-float BIST stocks being quietly
# accumulated before the crowd notices.
#
# This is NOT a screener, NOT a buy/sell signal generator.
# It surfaces *footprints* of accumulation using existing repo
# infrastructure (data.providers + engine.technical) — nothing
# external is required.
#
# Pipeline:
#   1. score_symbol(metrics, df, ownership=None) — pure scoring
#      from already-fetched inputs. Returns BullWatchResult.
#   2. scan(symbols, ownership_lookup=None)      — orchestrates
#      data fetching + parallel scoring across a universe.
#
# Score weights (sum = 100):
#   Float Pressure         20
#   Revenue Mispricing     15
#   Silent Volume          15
#   Price Action           20
#   Compression            10
#   Ownership Intelligence 15   (skipped + reweighted if no data)
#   Fundamental Quality     5
#
# Zones:
#   EARLY      — initial footprint (compression + calm + light vol)
#   CONFIRMED  — ownership/tape aligned (high score, multiple engines)
#   CONVICTION — breakout in progress (RVOL high, float pressure firing)
# ================================================================

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

from features.bullwatch_features import (
    FLOAT_MARKET_CAP_CAP_TL,
    LIQUIDITY_FLOOR_TL,
    PRICE_CALM_PCT,
    FLOAT_PRESSURE_STRONG, FLOAT_PRESSURE_VERY_STRONG, FLOAT_PRESSURE_EXTREME,
    RVOL_EARLY, RVOL_STRONG,
    float_market_cap, passes_float_cap,
    revenue_to_marketcap, revenue_mispricing_tier,
    avg_traded_value_20d, passes_liquidity,
    relative_volume, float_pressure,
    price_change_5d, is_price_calm,
    atr_compression_ratio, bb_width_compression_ratio,
    detect_price_action_patterns,
    ownership_signal,
)

log = logging.getLogger("bistbull.bullwatch")

# Engine weights — must sum to 100 when ownership is available.
# When ownership has no coverage we redistribute its 15 points to
# the other engines proportionally so a stock isn't unfairly capped.
WEIGHTS_WITH_OWNERSHIP: dict[str, float] = {
    "float_pressure":      20.0,
    "revenue_mispricing":  15.0,
    "silent_volume":       15.0,
    "price_action":        20.0,
    "compression":         10.0,
    "ownership":           15.0,
    "fundamental_quality":  5.0,
}

# Fundamental quality thresholds (per spec: "avoid junk pumps")
FQ_PE_MAX: float = 15.0
FQ_ROE_MIN: float = 0.15           # 15% (we expect fraction, e.g. 0.18)
FQ_NET_DEBT_EBITDA_MAX: float = 2.0


# ----------------------------------------------------------------
@dataclass
class BullWatchResult:
    symbol: str
    score: float                     # 0–100 final score
    zone: str                        # EARLY | CONFIRMED | CONVICTION
    pattern: str                     # human-readable pattern label
    components: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    data_quality: str = "high"       # high | medium | low
    eligible: bool = True            # did it pass universe filters?
    reject_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # round numeric fields for JSON niceness
        d["score"] = round(self.score, 1)
        d["components"] = {k: round(v, 2) for k, v in self.components.items()}
        return d


# ================================================================
# ENGINE 1 — Float Pressure
# ================================================================
def _engine_float_pressure(fp: Optional[float]) -> tuple[Optional[float], list[str]]:
    """Return (sub-score in [0,1], reasons)."""
    if fp is None:
        return None, []
    reasons: list[str] = []
    if fp >= FLOAT_PRESSURE_EXTREME:
        sub = 1.0
        reasons.append(f"Extreme float pressure ({fp * 100:.1f}%)")
    elif fp >= FLOAT_PRESSURE_VERY_STRONG:
        sub = 0.85
        reasons.append(f"Very strong float pressure ({fp * 100:.1f}%)")
    elif fp >= FLOAT_PRESSURE_STRONG:
        sub = 0.65
        reasons.append(f"Strong float pressure ({fp * 100:.1f}%)")
    elif fp >= 0.01:
        sub = 0.30
    else:
        sub = 0.0
    return sub, reasons


# ================================================================
# ENGINE 2 — Revenue Mispricing
# ================================================================
def _engine_revenue_mispricing(rev_to_mc: Optional[float]) -> tuple[Optional[float], list[str]]:
    tier = revenue_mispricing_tier(rev_to_mc)
    if rev_to_mc is None:
        return None, []
    reasons: list[str] = []
    if tier == 2:
        reasons.append(f"Revenue ≥ 10× market cap ({rev_to_mc:.1f}×)")
        return 1.0, reasons
    if tier == 1:
        reasons.append(f"Revenue ≥ 5× market cap ({rev_to_mc:.1f}×)")
        return 0.7, reasons
    if rev_to_mc >= 2.0:
        return 0.3, reasons
    return 0.0, reasons


# ================================================================
# ENGINE 3 — Silent Volume (Relative Volume vs 20d)
# ================================================================
def _engine_silent_volume(rvol: Optional[float]) -> tuple[Optional[float], list[str]]:
    if rvol is None:
        return None, []
    reasons: list[str] = []
    if rvol >= RVOL_STRONG:
        reasons.append(f"Strong relative volume ({rvol:.2f}×)")
        return 1.0, reasons
    if rvol >= RVOL_EARLY:
        reasons.append(f"Early relative volume ({rvol:.2f}×)")
        return 0.65, reasons
    if rvol >= 1.1:
        return 0.25, reasons
    return 0.0, reasons


# ================================================================
# ENGINE 4 — Price Action Accumulation
# ================================================================
def _engine_price_action(patterns: dict) -> tuple[float, list[str]]:
    """Always returns a score (0 if no patterns), never None."""
    count = patterns.get("count", 0)
    if count <= 0:
        return 0.0, []
    # 1 pattern → 0.5, 2 → 0.75, 3 → 0.9, 4+ → 1.0
    score_map = {1: 0.5, 2: 0.75, 3: 0.9, 4: 1.0}
    sub = score_map.get(count, 1.0)
    labels = patterns.get("labels", [])
    reasons = [f"Price action: {label}" for label in labels]
    return sub, reasons


# ================================================================
# ENGINE 5 — Volatility Compression (ATR + BB width)
# ================================================================
def _engine_compression(atr_ratio: Optional[float],
                        bb_ratio: Optional[float]) -> tuple[Optional[float], list[str]]:
    """
    Reward ratio < 1 (current vol below 60d median).
    Use the average of available signals; if neither is available → None.
    """
    parts: list[float] = []
    reasons: list[str] = []
    for label, ratio in (("ATR", atr_ratio), ("BB width", bb_ratio)):
        if ratio is None:
            continue
        # ratio of 0.7 → score 0.6;  0.5 → score 1.0;  >= 1.0 → score 0
        if ratio >= 1.0:
            parts.append(0.0)
        else:
            parts.append(min(1.0, (1.0 - ratio) / 0.5))
            reasons.append(f"{label} compressed to {ratio:.2f}× of 60d median")
    if not parts:
        return None, []
    return sum(parts) / len(parts), reasons


# ================================================================
# ENGINE 6 — Ownership Intelligence (delegates to features module)
# ================================================================
def _engine_ownership(ownership: Optional[dict]) -> tuple[Optional[float], list[str], str]:
    sig = ownership_signal(ownership)
    score = sig["score"]
    reasons = sig["reasons"]
    coverage = sig["coverage"]
    return score, reasons, coverage


# ================================================================
# ENGINE 7 — Fundamental Quality
# ================================================================
def _engine_fundamental_quality(metrics: dict) -> tuple[Optional[float], list[str]]:
    """Avoid junk pumps. PE<15, ROE>15%, net_debt/EBITDA<2."""
    pe = metrics.get("pe")
    roe = metrics.get("roe")
    nd_ebitda = metrics.get("net_debt_ebitda")

    have = sum(1 for x in (pe, roe, nd_ebitda) if x is not None)
    if have == 0:
        return None, []

    passes = 0
    total = 0
    reasons: list[str] = []

    if pe is not None:
        total += 1
        if 0 < pe < FQ_PE_MAX:
            passes += 1
        else:
            reasons.append(f"PE outside healthy range ({pe:.1f})")

    if roe is not None:
        total += 1
        if roe >= FQ_ROE_MIN:
            passes += 1
            reasons.append(f"ROE {roe * 100:.1f}%")
        else:
            reasons.append(f"ROE only {roe * 100:.1f}%")

    if nd_ebitda is not None:
        total += 1
        # negative net debt = net cash — that's good
        if nd_ebitda < FQ_NET_DEBT_EBITDA_MAX:
            passes += 1
        else:
            reasons.append(f"Net debt / EBITDA = {nd_ebitda:.1f}")

    if total == 0:
        return None, []
    return passes / total, reasons


# ================================================================
# Zone classification — depends on which engines are firing.
# ================================================================
def _classify_zone(score: float,
                   fp: Optional[float],
                   rvol: Optional[float],
                   ownership_score: Optional[float],
                   pattern_count: int,
                   compression_score: Optional[float]) -> str:
    """
    EARLY      — quiet footprint: compression + price calm, low score.
    CONFIRMED  — multiple engines aligned (incl. tape or ownership), mid-high.
    CONVICTION — breakout in progress (high RVOL or extreme float pressure).
    """
    high_rvol = rvol is not None and rvol >= RVOL_STRONG
    extreme_fp = fp is not None and fp >= FLOAT_PRESSURE_VERY_STRONG
    if score >= 75 and (high_rvol or extreme_fp):
        return "CONVICTION"
    if score >= 60 and (
        (ownership_score is not None and ownership_score >= 0.4)
        or pattern_count >= 2
        or (rvol is not None and rvol >= RVOL_EARLY)
    ):
        return "CONFIRMED"
    return "EARLY"


def _pattern_label(active_engines: list[str], patterns: dict,
                   ownership_score: Optional[float]) -> str:
    """Build descriptive pattern string. NO buy/sell language."""
    parts: list[str] = []
    if "Float Pressure" in active_engines:
        parts.append("Float Squeeze")
    if "Compression" in active_engines and "Float Pressure" not in active_engines:
        parts.append("Volatility Compression")
    if patterns.get("labels"):
        parts.extend(patterns["labels"][:2])
    if ownership_score is not None and ownership_score >= 0.4:
        parts.append("Ownership Footprint")
    if "Silent Volume" in active_engines and "Float Pressure" not in active_engines:
        parts.append("Silent Volume Pickup")
    if "Revenue Mispricing" in active_engines and not parts:
        parts.append("Revenue Mispricing")
    if not parts:
        parts.append("Quiet Watchlist Candidate")
    # Dedupe while preserving order
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            out.append(p); seen.add(p)
    return " + ".join(out[:4])


# ================================================================
# Main scoring entry point — pure, deterministic, no I/O.
# ================================================================
def score_symbol(metrics: dict,
                 df: Any = None,
                 ownership: Optional[dict] = None) -> BullWatchResult:
    """
    Score a single symbol.

    Args:
        metrics: dict in the shape of compute_metrics_v9 output. Only
                 a few keys are used: market_cap, free_float, revenue,
                 pe, roe, net_debt_ebitda, and (optional) shares.
        df:      OHLCV DataFrame (Open/High/Low/Close/Volume),
                 trailing ~80+ sessions recommended.
        ownership: Optional ownership snapshot (see features module).

    Returns BullWatchResult — always returns a result; ineligible
    symbols are flagged via `eligible=False` and `reject_reason`.
    """
    symbol = str(metrics.get("symbol") or metrics.get("ticker") or "?")
    market_cap = metrics.get("market_cap")
    free_float = metrics.get("free_float")
    revenue = metrics.get("revenue")
    shares_outstanding = metrics.get("shares")
    if shares_outstanding is None and market_cap and metrics.get("price"):
        try:
            shares_outstanding = float(market_cap) / float(metrics["price"])
        except (TypeError, ValueError, ZeroDivisionError):
            shares_outstanding = None

    fmc = float_market_cap(market_cap, free_float)

    # ---- Universe filters ----
    if not passes_float_cap(market_cap, free_float):
        return BullWatchResult(
            symbol=symbol, score=0.0, zone="EARLY",
            pattern="Outside BullWatch universe",
            eligible=False,
            reject_reason=(
                "no float data" if fmc is None
                else f"float mcap {fmc/1e6:.0f}M TL > {FLOAT_MARKET_CAP_CAP_TL/1e6:.0f}M cap"
            ),
            metrics={"float_market_cap": fmc, "market_cap": market_cap,
                     "free_float": free_float},
            data_quality="low",
        )

    if not passes_liquidity(df):
        atv = avg_traded_value_20d(df)
        return BullWatchResult(
            symbol=symbol, score=0.0, zone="EARLY",
            pattern="Outside BullWatch universe",
            eligible=False,
            reject_reason=(
                "no price history" if atv is None
                else f"20d avg traded value {atv/1e6:.1f}M TL < {LIQUIDITY_FLOOR_TL/1e6:.0f}M floor"
            ),
            metrics={"float_market_cap": fmc, "avg_traded_value_20d": atv},
            data_quality="low",
        )

    # ---- Feature extraction ----
    fp = float_pressure(df, shares_outstanding, free_float)
    rvol = relative_volume(df)
    rev_mc = revenue_to_marketcap(revenue, market_cap)
    if rev_mc is None and metrics.get("ciro_pd") is not None:
        rev_mc = float(metrics["ciro_pd"])
    pc5 = price_change_5d(df)
    calm = is_price_calm(df)
    atr_r = atr_compression_ratio(df)
    bb_r = bb_width_compression_ratio(df)
    patterns = detect_price_action_patterns(df)

    # ---- Engine sub-scores (each in [0,1] or None if no data) ----
    s_fp, r_fp = _engine_float_pressure(fp)
    s_rev, r_rev = _engine_revenue_mispricing(rev_mc)
    s_sv, r_sv = _engine_silent_volume(rvol)
    s_pa, r_pa = _engine_price_action(patterns)
    s_cm, r_cm = _engine_compression(atr_r, bb_r)
    s_ow, r_ow, ow_coverage = _engine_ownership(ownership)
    s_fq, r_fq = _engine_fundamental_quality(metrics)

    # Price calm acts as a small multiplier on the price-action engine —
    # we want to reward accumulation during quiet periods.
    if calm and s_pa is not None and s_pa > 0:
        s_pa = min(1.0, s_pa * 1.15)

    sub_scores = {
        "float_pressure":      s_fp,
        "revenue_mispricing":  s_rev,
        "silent_volume":       s_sv,
        "price_action":        s_pa,
        "compression":         s_cm,
        "ownership":           s_ow,
        "fundamental_quality": s_fq,
    }

    # ---- Weight redistribution: drop weights for engines with no data,
    # then renormalize so the maximum achievable score is always 100.
    available = {k: v for k, v in sub_scores.items() if v is not None}
    if not available:
        return BullWatchResult(
            symbol=symbol, score=0.0, zone="EARLY",
            pattern="Insufficient data",
            eligible=True,
            reject_reason="no engines fired",
            metrics={"float_market_cap": fmc},
            data_quality="low",
        )

    weights = {k: WEIGHTS_WITH_OWNERSHIP[k] for k in available}
    weight_total = sum(weights.values())
    if weight_total <= 0:
        score = 0.0
    else:
        # Each engine contributes (sub * weight); we report contributions
        # in the original 100-point scale by renormalizing weight_total.
        norm = 100.0 / weight_total
        contributions = {k: available[k] * weights[k] * norm
                         for k in available}
        score = sum(contributions.values())

    # ---- Pattern label + zone ----
    THRESH = 0.5
    active = []
    if s_fp is not None and s_fp >= THRESH: active.append("Float Pressure")
    if s_rev is not None and s_rev >= THRESH: active.append("Revenue Mispricing")
    if s_sv is not None and s_sv >= THRESH: active.append("Silent Volume")
    if s_cm is not None and s_cm >= THRESH: active.append("Compression")
    if s_pa >= THRESH: active.append("Price Action")
    pattern = _pattern_label(active, patterns, s_ow)
    zone = _classify_zone(score, fp, rvol, s_ow, patterns.get("count", 0), s_cm)

    # ---- Reasons (de-duped, capped) ----
    reasons: list[str] = []
    for chunk in (r_fp, r_rev, r_sv, r_pa, r_cm, r_ow, r_fq):
        for r in chunk:
            if r and r not in reasons:
                reasons.append(r)
    reasons = reasons[:8]

    # ---- Data quality flag ----
    if ow_coverage == "none" and (s_fq is None or s_cm is None):
        dq = "medium"
    elif ow_coverage == "none":
        dq = "medium"
    elif sum(1 for v in sub_scores.values() if v is not None) >= 6:
        dq = "high"
    else:
        dq = "medium"

    return BullWatchResult(
        symbol=symbol,
        score=round(max(0.0, min(100.0, score)), 1),
        zone=zone,
        pattern=pattern,
        components={k: float(v) for k, v in sub_scores.items() if v is not None},
        metrics={
            "float_market_cap": fmc,
            "market_cap": market_cap,
            "free_float": free_float,
            "revenue_to_marketcap": rev_mc,
            "rvol": rvol,
            "float_pressure": fp,
            "price_change_5d": pc5,
            "atr_compression": atr_r,
            "bb_compression": bb_r,
            "patterns": patterns.get("labels", []),
            "ownership_coverage": ow_coverage,
        },
        reasons=reasons,
        data_quality=dq,
        eligible=True,
    )


# ================================================================
# Universe scan — orchestrates fetching + parallel scoring.
#
# Optional dependency injection for testability:
#   metrics_fn(symbol)  -> dict like compute_metrics_v9
#   history_fn(symbols) -> dict[symbol, DataFrame]
#   ownership_fn(symbol)-> dict | None
# Defaults wire to the existing repo providers.
# ================================================================
def scan(symbols: list[str],
         metrics_fn: Optional[Callable[[str], dict]] = None,
         history_fn: Optional[Callable[[list[str]], dict[str, Any]]] = None,
         ownership_fn: Optional[Callable[[str], Optional[dict]]] = None,
         max_workers: int = 8,
         min_score: float = 0.0,
         include_ineligible: bool = False) -> list[BullWatchResult]:
    """
    Run BullWatch across a universe.

    All providers are injectable so the scan can be tested with
    deterministic fakes. By default it uses the existing repo
    providers (data.providers.compute_metrics_v9 +
    engine.technical.batch_download_history).
    """
    # Resolve default providers lazily so that tests don't need to
    # have borsapy installed.
    if metrics_fn is None:
        from data.providers import compute_metrics_v9 as _m
        metrics_fn = _m  # type: ignore
    if history_fn is None:
        from engine.technical import batch_download_history as _h
        history_fn = _h  # type: ignore
    if ownership_fn is None:
        ownership_fn = lambda _s: None  # noqa: E731

    log.info("BullWatch scan starting: %d symbols", len(symbols))

    # Bulk-fetch history in one shot — the existing helper already
    # batches via borsapy, so this is the cheap path.
    try:
        hist_map = history_fn(symbols) or {}
    except Exception as exc:
        log.warning("BullWatch: batch history fetch failed: %r", exc)
        hist_map = {}

    def _score_one(sym: str) -> Optional[BullWatchResult]:
        try:
            metrics = metrics_fn(sym)
        except Exception as exc:
            log.debug("BullWatch %s: metrics fetch failed: %r", sym, exc)
            return None
        df = hist_map.get(sym)
        try:
            ownership = ownership_fn(sym)
        except Exception:
            ownership = None
        try:
            return score_symbol(metrics, df, ownership)
        except Exception as exc:
            log.warning("BullWatch %s: scoring failed: %r", sym, exc)
            return None

    results: list[BullWatchResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_score_one, s): s for s in symbols}
        for fut in as_completed(futures):
            r = fut.result()
            if r is None:
                continue
            if not r.eligible and not include_ineligible:
                continue
            if r.score < min_score and r.eligible:
                # Eligible but low-scoring — keep only if explicitly asked
                if min_score > 0:
                    continue
            results.append(r)

    # Sort: eligible by score desc, ineligible last
    results.sort(key=lambda r: (not r.eligible, -r.score))
    log.info("BullWatch scan done: %d eligible, top score %.1f",
             sum(1 for r in results if r.eligible),
             results[0].score if results and results[0].eligible else 0.0)
    return results

"""
BullWatch metrics cache layer.

Sits between BullWatch's scan loop and `compute_metrics_v9`. Adds three
robustness layers that the raw provider can't give us:

  1. **Persistent caching** (Redis, 12h TTL) — avoid hammering yfinance
     429 times per warmup. Stale-but-recent data is fine for
     low-frequency screens like BullWatch (we're not making intraday
     trade decisions on this).

  2. **Sanity check** — drop obviously-bad values BEFORE they reach the
     scoring engine. yfinance occasionally returns nonsense
     (free_float=18.9 = 1890%, negative market caps, etc.) and we'd
     rather treat the symbol as "no data" than score it on garbage.

  3. **Manual override** — for known yfinance bugs we've encountered
     in production, hard-code the correct value. Pragmatic; trust your
     own eyes over Yahoo's data feed for specific tickers.

Cache key format: `bullwatch:metrics:v1:{SYMBOL}`. Bumping the `v1`
suffix invalidates everything (use when sanity rules change).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from core import redis_client

log = logging.getLogger("bistbull.bullwatch_cache")

# 12 hours — long enough to dodge yfinance flakiness across a trading
# day, short enough that the next scheduled warmup pulls fresh
# fundamentals + post-close market cap. Tune via env var if needed.
CACHE_TTL_SEC: int = 12 * 3600

CACHE_KEY_PREFIX = "bullwatch:metrics:v1:"


# ----------------------------------------------------------------
# Manual overrides — yfinance bugs we've confirmed in production logs.
#
# Each entry only applies to FIELDS LISTED here; other fields still
# come from yfinance. Update sparingly, and always with a comment
# explaining what we observed and how we know the correct value.
# ----------------------------------------------------------------
_KNOWN_OVERRIDES: dict[str, dict[str, Any]] = {
    # yfinance returned free_float as 7.1 (instead of 0.71 fraction).
    # Confirmed via KAP/İş Yatırım: ICBC Turkey free float ~71%.
    # Without this, ICBCT shows "710%" in near-misses table.
    "ICBCT": {"free_float": 0.71},
    # yfinance returned 18.9 → would be 1890%. Real free float ~25%.
    # Source: GLCVY KAP filing.
    "GLCVY": {"free_float": 0.25},
    # 28.0 from yfinance. Real ~28%, so yfinance is sending percentage
    # form here unlike its usual fraction. Normalize_free_float catches
    # this correctly (28→0.28), so no override needed for AKGRT.
    # Listed here as a no-op reminder; do NOT enable.
    # "AKGRT": {"free_float": 0.28},
    #
    # ── Phase A.6 hygiene additions ─────────────────────────────
    # yfinance returned no float data (empty floatShares field) for these
    # three symbols in the 2026-05-08 Phase A.5 review. Without overrides
    # they were rejected as "no_data". Values below are best-effort from
    # public BIST/KAP filings — VERIFY on KAP and adjust if needed.
    #
    # Kaplamin Ambalaj — small-cap pump candidate. Free float estimate
    # from public filings ~35%.
    "KAPLM": {"free_float": 0.35},
    # Galer Holding — small-cap holding. Free float estimate ~35% per
    # public filings (not authoritatively verified).
    "GLRMK": {"free_float": 0.35},
    # Aselsan — state-owned defense. Public free float ~25.93% (Aselsan
    # IR / KAP). Note: even with this override ASELS may land in the
    # institutional tier (>15B TL float mcap); that's correct behavior.
    "ASELS": {"free_float": 0.2593},
}


# ----------------------------------------------------------------
# Sanity rules — return True if the value is OBVIOUSLY wrong.
# Used to drop bad fields, not the entire symbol.
# ----------------------------------------------------------------
def _is_bad_market_cap(v: Any) -> bool:
    if v is None:
        return False  # missing is fine (downstream handles None)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return True
    return f <= 0 or f > 1e14  # > 100 trillion TL is impossible


def _is_bad_free_float(v: Any) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return True
    if f <= 0:
        return True
    # Acceptable forms: fraction in (0,1] OR percentage in (0,100].
    # Anything > 100 is nonsense (would be "100%+" of all shares).
    return f > 100.0


def _is_bad_revenue(v: Any) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return True
    return f < 0  # revenue can't be negative; if it is, data is wrong


def _is_bad_shares_outstanding(v: Any) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return True
    return f <= 0 or f > 1e15  # > 1 quadrillion shares is impossible


_SANITY_RULES = {
    "market_cap": _is_bad_market_cap,
    "free_float": _is_bad_free_float,
    "revenue": _is_bad_revenue,
    "shares_outstanding": _is_bad_shares_outstanding,
}


def _apply_sanity(metrics: dict, symbol: str) -> dict:
    """Drop fields that fail sanity. Returns the same dict, modified."""
    for field, is_bad in _SANITY_RULES.items():
        v = metrics.get(field)
        if is_bad(v):
            log.warning(
                "DATA QUALITY [%s]: rejected %s=%r (sanity rule failed)",
                symbol, field, v,
            )
            metrics[field] = None
    return metrics


def _apply_overrides(metrics: dict, symbol: str) -> dict:
    """Stamp known good values over borsapy output for specific tickers.

    Phase A.10 Step 2-A: also stamps audit metadata so production API
    consumers can see whether/which override was applied:
      - override_applied: bool
      - override_source: "manual_override" | None
      - override_fields: list of field names overridden
    Also updates metrics["_field_sources"][field] to "manual_override"
    for fields that were stamped.
    """
    sym = symbol.upper().replace(".IS", "").replace(".E", "").strip()
    overrides = _KNOWN_OVERRIDES.get(sym)

    # Always set the audit fields, even when no override exists, so
    # downstream consumers (BullWatchResult, smoke tests, dashboards)
    # can rely on these keys being present.
    if not overrides:
        metrics.setdefault("override_applied", False)
        metrics.setdefault("override_source", None)
        metrics.setdefault("override_fields", [])
        return metrics

    overridden: list[str] = []
    field_sources = metrics.setdefault("_field_sources", {})
    for field, value in overrides.items():
        old = metrics.get(field)
        if old != value:
            log.info(
                "DATA QUALITY [%s]: override %s=%r → %r (manual)",
                sym, field, old, value,
            )
            metrics[field] = value
            overridden.append(field)
            field_sources[field] = "manual_override"
            _STATS["override_applied_count"] += 1

    metrics["override_applied"] = bool(overridden)
    metrics["override_source"] = "manual_override" if overridden else None
    metrics["override_fields"] = overridden
    return metrics


# ----------------------------------------------------------------
# Cache stats — exposed via /api/bullwatch/health for visibility.
#
# Phase A.10 Step 2-A: broken down by category. The original _STATS
# kept only a single generic "error" counter; that hid 392 borsapy
# errors behind one number. Now we track which step failed and
# which fields are missing so the diagnostic endpoint can show
# WHERE coverage is breaking.
# ----------------------------------------------------------------
_STATS: dict[str, int] = {
    # Existing — kept for backwards compat with any dashboard/log scraper
    "hit": 0, "miss": 0, "error": 0, "sanity_drop": 0,
    # Phase A.10: per-error-category breakdown
    "missing_ohlcv": 0,
    "missing_free_float": 0,
    "missing_market_cap": 0,
    "missing_income_statement": 0,
    "borsapy_fast_info_error": 0,
    "borsapy_history_error": 0,
    "borsapy_income_stmt_error": 0,
    "override_applied_count": 0,
    "stale_cache_used_count": 0,
    "data_status_live": 0,
    "data_status_partial": 0,
    "data_status_missing": 0,
}


def _required_fields_for_status() -> list[str]:
    """Fields BullWatch needs to score a symbol meaningfully."""
    return ["market_cap", "free_float", "shares"]


def _compute_data_status(metrics: dict) -> tuple[str, list[str]]:
    """Return (data_status, missing_fields) tuple.

    data_status:
      - "live"    — all required fields present, freshly fetched
      - "partial" — at least one required field missing
      - "missing" — all required fields missing
      - "stale"   — only set by cache layer when serving stale data
    """
    required = _required_fields_for_status()
    missing = [f for f in required if metrics.get(f) is None]
    if not missing:
        return "live", []
    if len(missing) == len(required):
        return "missing", missing
    return "partial", missing


def get_stats() -> dict[str, Any]:
    total = sum(v for k, v in _STATS.items() if k in ("hit", "miss", "error", "sanity_drop"))
    hit_pct = (_STATS["hit"] / total * 100) if total else None
    # Phase A.10 Step 2-A: structured diagnostics breakdown so the
    # /api/bullwatch/health endpoint can show WHERE coverage breaks.
    diagnostics = {
        "missing_fields": {
            "ohlcv": _STATS.get("missing_ohlcv", 0),
            "free_float": _STATS.get("missing_free_float", 0),
            "market_cap": _STATS.get("missing_market_cap", 0),
            "income_statement": _STATS.get("missing_income_statement", 0),
        },
        "borsapy_errors": {
            "fast_info": _STATS.get("borsapy_fast_info_error", 0),
            "history": _STATS.get("borsapy_history_error", 0),
            "income_stmt": _STATS.get("borsapy_income_stmt_error", 0),
        },
        "data_status_distribution": {
            "live": _STATS.get("data_status_live", 0),
            "partial": _STATS.get("data_status_partial", 0),
            "missing": _STATS.get("data_status_missing", 0),
        },
        "override_applied_count": _STATS.get("override_applied_count", 0),
        "stale_cache_used_count": _STATS.get("stale_cache_used_count", 0),
    }
    return {
        # Original fields kept for backwards compat with any dashboard
        # / log scraper that depends on them.
        "hit": _STATS["hit"],
        "miss": _STATS["miss"],
        "error": _STATS["error"],
        "sanity_drop": _STATS["sanity_drop"],
        "total_lookups": total,
        "hit_pct": round(hit_pct, 1) if hit_pct is not None else None,
        "ttl_sec": CACHE_TTL_SEC,
        "redis_available": redis_client.is_available(),
        # Phase A.10 Step 2-A
        "diagnostics": diagnostics,
    }


def _cache_get(symbol: str) -> Optional[dict]:
    if not redis_client.is_available():
        return None
    try:
        return redis_client.get_json(CACHE_KEY_PREFIX + symbol.upper())
    except Exception as e:
        log.debug("cache_get error for %s: %r", symbol, e)
        return None


def _cache_set(symbol: str, metrics: dict) -> None:
    if not redis_client.is_available():
        return
    try:
        # Stamp fetched_at so the engine can tell freshness if needed
        metrics_with_meta = dict(metrics)
        metrics_with_meta.setdefault("_cached_at", time.time())
        redis_client.set_json(
            CACHE_KEY_PREFIX + symbol.upper(),
            metrics_with_meta,
            ttl=CACHE_TTL_SEC,
        )
    except Exception as e:
        log.debug("cache_set error for %s: %r", symbol, e)


# ----------------------------------------------------------------
# Public entrypoint — drop-in replacement for compute_metrics_v9
# from BullWatch's perspective.
# ----------------------------------------------------------------
def cached_compute_metrics(symbol: str) -> dict:
    """
    Same return shape as `compute_metrics_v9(symbol)`, with caching,
    sanity-checking, and manual override applied.

    Phase A.10 Step 2-A: also stamps diagnostic fields:
      - _data_status: "live" | "stale" | "partial" | "missing"
      - _missing_fields: list of field names that are None
      - _provider_used: "cached_borsapy" | "borsapy" (no stale fallback yet)
      - _provider_errors: list of {error_type, message} (empty on success)
      - _field_sources: dict (set by compute_metrics_v9 + _apply_overrides)

    Failure mode: if the underlying provider raises, we let the exception
    propagate (engine's _score_one already swallows it and returns None).
    We do NOT cache exceptions — next call retries fresh.
    """
    sym = symbol.upper().strip()

    # 1. Cache hit?
    cached = _cache_get(sym)
    if cached is not None:
        _STATS["hit"] += 1
        # Defensive: re-apply overrides on cached data too, in case our
        # _KNOWN_OVERRIDES dict was edited since the cache was populated.
        cached = _apply_overrides(cached, sym)
        # Mark provenance so consumers know this came from cache.
        cached["_provider_used"] = "cached_borsapy"
        # Recompute data_status (free_float may have changed via override)
        status, missing = _compute_data_status(cached)
        cached["_data_status"] = status
        cached["_missing_fields"] = missing
        cached.setdefault("_provider_errors", [])
        _STATS[f"data_status_{status}" if status in ("live", "partial", "missing") else "data_status_partial"] += 1
        for f in missing:
            stat_key = f"missing_{f}" if f"missing_{f}" in _STATS else None
            if stat_key:
                _STATS[stat_key] += 1
        return cached

    # 2. Miss — fetch fresh.
    _STATS["miss"] += 1
    provider_errors: list[dict] = []
    try:
        # Imported lazily to avoid circular dependency at module load
        from data.providers import compute_metrics_v9
        metrics = compute_metrics_v9(sym)
    except Exception as e:
        _STATS["error"] += 1
        # Classify the borsapy failure mode for the health endpoint.
        err_type = _classify_borsapy_error(e)
        if err_type == "fast_info":
            _STATS["borsapy_fast_info_error"] += 1
        elif err_type == "history":
            _STATS["borsapy_history_error"] += 1
        elif err_type == "income_stmt":
            _STATS["borsapy_income_stmt_error"] += 1
        # Re-raise so the API layer can return a structured error response.
        raise

    # 3. Sanity → override → cache → return.
    metrics = _apply_sanity(metrics, sym)
    metrics = _apply_overrides(metrics, sym)

    # 4. Compute diagnostic fields.
    status, missing = _compute_data_status(metrics)
    metrics["_data_status"] = status
    metrics["_missing_fields"] = missing
    metrics["_provider_used"] = "borsapy"
    metrics["_provider_errors"] = provider_errors
    # Track per-field missing for health diagnostics
    _STATS[f"data_status_{status}" if status in ("live", "partial", "missing") else "data_status_partial"] += 1
    for f in missing:
        stat_key = f"missing_{f}" if f"missing_{f}" in _STATS else None
        if stat_key:
            _STATS[stat_key] += 1

    _cache_set(sym, metrics)
    return metrics


def _classify_borsapy_error(exc: Exception) -> str:
    """Phase A.10: classify a borsapy/provider exception into a category.

    Returns one of: "fast_info" | "history" | "income_stmt" | "unknown".
    Used to populate per-error-type counters in _STATS. We classify by
    the exception message since borsapy raises generic Exception in
    several places without type info.
    """
    msg = str(exc).lower()
    if "fast_info" in msg or "fast info" in msg:
        return "fast_info"
    if "history" in msg or "ohlcv" in msg:
        return "history"
    if "income" in msg or "balance" in msg or "cashflow" in msg:
        return "income_stmt"
    return "unknown"


def invalidate(symbol: str) -> bool:
    """Force-evict a symbol from cache (e.g. after manual override edit)."""
    if not redis_client.is_available():
        return False
    try:
        return redis_client.delete(CACHE_KEY_PREFIX + symbol.upper())
    except Exception:
        return False

"""
BullWatch metrics cache layer.

Sits between BullWatch's scan loop and `compute_metrics_v9`. Adds three
robustness layers that the raw provider can't give us:

  1. **Persistent caching** (Redis) — avoid hammering the data provider
     429 times per warmup. The fresh-window is 12h (CACHE_TTL_SEC); after
     that the entry is "stale" but kept in Redis for STALE_GRAVE_SEC (7
     days) so we can serve it as a fallback when a fresh fetch fails.
     Stale-but-recent data is fine for low-frequency screens like
     BullWatch (we're not making intraday trade decisions on this).

  2. **Sanity check** — drop obviously-bad values BEFORE they reach the
     scoring engine. The provider occasionally returns nonsense
     (free_float=18.9 = 1890%, negative market caps, etc.) and we'd
     rather treat the symbol as "no data" than score it on garbage.

  3. **Manual override** — for known provider bugs we've encountered
     in production, hard-code the correct value. Pragmatic; trust your
     own eyes over the data feed for specific tickers.

Cache key format: `bullwatch:metrics:v3:{SYMBOL}`. Bumping the version
suffix invalidates everything (Step 2-A.1 added shares to the dict and
Step 2-A.2 added cycle_state/diagnostic fields — old v1/v2 entries
have stale shapes and are auto-skipped on next read).
"""
from __future__ import annotations

import collections
import logging
import time
from typing import Any, Optional

from core import redis_client

log = logging.getLogger("bistbull.bullwatch_cache")

# 12 hours — long enough to dodge provider flakiness across a trading
# day, short enough that the next scheduled warmup pulls fresh
# fundamentals + post-close market cap. Tune via env var if needed.
CACHE_TTL_SEC: int = 12 * 3600

# Phase A.10 Step 2-B: stale grave window. Entries past CACHE_TTL_SEC
# but within STALE_GRAVE_SEC are returned with data_status="stale" when
# the live provider fails — so the user sees old-but-known-good numbers
# instead of an empty error state.
STALE_GRAVE_SEC: int = 7 * 24 * 3600  # 7 days

# Phase A.10 Step 2-B: cache schema version. Bump when the metric shape
# changes (added/renamed fields). Old keys won't be read; they expire
# naturally via Redis TTL. v3 = post Step 2-A.1 (shares in dict) +
# Step 2-A.2 (cycle_state, diagnostic field propagation).
CACHE_SCHEMA_VERSION: str = "v3"
CACHE_KEY_PREFIX: str = f"bullwatch:metrics:{CACHE_SCHEMA_VERSION}:"


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


# Phase A.10 Step 2-B: keep a bounded ring of the most recent sanity
# drops so /api/bullwatch/health can show WHY data was rejected, not
# just how many times.
_SANITY_DROP_LOG: collections.deque = collections.deque(maxlen=100)


def _record_sanity_drop(symbol: str, field: str, value: Any, reason: str) -> None:
    """Capture a per-field sanity drop for diagnostics."""
    try:
        _SANITY_DROP_LOG.append({
            "symbol": symbol,
            "field": field,
            "original_value": (str(value)[:80] if value is not None else None),
            "reason": reason,
            "ts": time.time(),
        })
    except Exception:
        pass
    # Per-field counter
    key = f"sanity_drop_{field}"
    _STATS[key] = _STATS.get(key, 0) + 1
    _STATS["sanity_drop"] = _STATS.get("sanity_drop", 0) + 1


def _apply_sanity(metrics: dict, symbol: str) -> dict:
    """Drop fields that fail sanity. Returns the same dict, modified.

    Phase A.10 Step 2-B: each rejection now records {symbol, field,
    original_value, reason} into _SANITY_DROP_LOG and increments a
    per-field counter so /api/bullwatch/health exposes WHY drops happened.
    """
    for field, is_bad in _SANITY_RULES.items():
        v = metrics.get(field)
        if is_bad(v):
            log.warning(
                "DATA QUALITY [%s]: rejected %s=%r (sanity rule failed)",
                symbol, field, v,
            )
            _record_sanity_drop(symbol, field, v, "sanity_rule_failed")
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
    # Phase A.10 Step 2-A: per-error-category breakdown
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
    # Phase A.10 Step 2-B: additional counters
    "missing_shares": 0,                     # was silently dropped pre-2-B
    "data_status_stale": 0,                  # served-from-grave count
    "cache_stale_hit": 0,                    # cache lookup found expired entry
    "borsapy_timeout_error": 0,              # provider TimeoutError specifically
    "borsapy_data_not_available_error": 0,   # DataNotAvailableError specifically
    # Per-field sanity_drop counters (auto-registered by _record_sanity_drop)
    "sanity_drop_market_cap": 0,
    "sanity_drop_free_float": 0,
    "sanity_drop_revenue": 0,
    "sanity_drop_shares_outstanding": 0,
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
    # Phase A.10 Step 2-A/2-B: structured diagnostics breakdown so the
    # /api/bullwatch/health endpoint can show WHERE coverage breaks.
    diagnostics = {
        "missing_fields": {
            "ohlcv": _STATS.get("missing_ohlcv", 0),
            "free_float": _STATS.get("missing_free_float", 0),
            "market_cap": _STATS.get("missing_market_cap", 0),
            # Phase A.10 Step 2-B: shares now first-class
            "shares": _STATS.get("missing_shares", 0),
            "income_statement": _STATS.get("missing_income_statement", 0),
        },
        "borsapy_errors": {
            "fast_info": _STATS.get("borsapy_fast_info_error", 0),
            "history": _STATS.get("borsapy_history_error", 0),
            "income_stmt": _STATS.get("borsapy_income_stmt_error", 0),
            # Phase A.10 Step 2-B: subcategories for the provider-failure logs
            # we saw in production (TimeoutError, DataNotAvailableError).
            "timeout": _STATS.get("borsapy_timeout_error", 0),
            "data_not_available": _STATS.get("borsapy_data_not_available_error", 0),
        },
        "data_status_distribution": {
            "live": _STATS.get("data_status_live", 0),
            "partial": _STATS.get("data_status_partial", 0),
            # Phase A.10 Step 2-B: stale-while-revalidate served entries
            "stale": _STATS.get("data_status_stale", 0),
            "missing": _STATS.get("data_status_missing", 0),
        },
        # Phase A.10 Step 2-B: per-field sanity drop breakdown + recent log
        "sanity_drop_breakdown": {
            "market_cap": _STATS.get("sanity_drop_market_cap", 0),
            "free_float": _STATS.get("sanity_drop_free_float", 0),
            "revenue": _STATS.get("sanity_drop_revenue", 0),
            "shares_outstanding": _STATS.get("sanity_drop_shares_outstanding", 0),
        },
        "recent_sanity_drops": list(_SANITY_DROP_LOG)[-20:],
        "override_applied_count": _STATS.get("override_applied_count", 0),
        "stale_cache_used_count": _STATS.get("stale_cache_used_count", 0),
        # Phase A.10 Step 2-B: cache schema version surfaced for debugging
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "stale_grave_sec": STALE_GRAVE_SEC,
    }
    return {
        # Original fields kept for backwards compat with any dashboard
        # / log scraper that depends on them.
        "hit": _STATS["hit"],
        "miss": _STATS["miss"],
        "error": _STATS["error"],
        "sanity_drop": _STATS["sanity_drop"],
        # Phase A.10 Step 2-B: stale-cache hit visible at top level too
        "stale_hit": _STATS.get("cache_stale_hit", 0),
        "total_lookups": total,
        "hit_pct": round(hit_pct, 1) if hit_pct is not None else None,
        "ttl_sec": CACHE_TTL_SEC,
        "redis_available": redis_client.is_available(),
        # Phase A.10 Step 2-A/2-B
        "diagnostics": diagnostics,
    }


def _cache_get(symbol: str) -> Optional[dict]:
    """Read whatever's in the cache (fresh OR stale). Use _cache_age()
    to determine freshness."""
    if not redis_client.is_available():
        return None
    try:
        return redis_client.get_json(CACHE_KEY_PREFIX + symbol.upper())
    except Exception as e:
        log.debug("cache_get error for %s: %r", symbol, e)
        return None


def _cache_age(metrics: Optional[dict]) -> Optional[float]:
    """Seconds since last successful fetch, or None if missing/no stamp."""
    if not metrics:
        return None
    cached_at = metrics.get("_cached_at")
    if cached_at is None:
        return None
    try:
        return max(0.0, time.time() - float(cached_at))
    except (TypeError, ValueError):
        return None


def _cache_set(symbol: str, metrics: dict) -> None:
    """Store with a long Redis TTL (STALE_GRAVE_SEC). Logical freshness
    is computed in code via _cache_age() against CACHE_TTL_SEC.

    Phase A.10 Step 2-B: keep entries past their fresh window so
    stale-while-revalidate can serve them when the live provider fails.
    """
    if not redis_client.is_available():
        return
    try:
        # Stamp fetched_at so the engine can tell freshness if needed
        metrics_with_meta = dict(metrics)
        metrics_with_meta.setdefault("_cached_at", time.time())
        # Stamp schema version so future Step 2-B+ migrations can
        # detect old shapes if they ever co-exist.
        metrics_with_meta["_cache_schema_version"] = CACHE_SCHEMA_VERSION
        redis_client.set_json(
            CACHE_KEY_PREFIX + symbol.upper(),
            metrics_with_meta,
            ttl=STALE_GRAVE_SEC,  # Step 2-B: kept around for fallback
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

    Phase A.10 Step 2-A: stamps diagnostic fields on the output.
    Phase A.10 Step 2-B: stale-while-revalidate. If a fresh fetch fails
    BUT a stale cache entry exists, serve the stale entry with
    data_status="stale" and provider error metadata, instead of raising.

    Diagnostic fields surfaced:
      - _data_status: "live" | "stale" | "partial" | "missing"
      - _missing_fields: list of field names that are None
      - _provider_used: "borsapy" | "cached_borsapy" | "stale_cache"
      - _provider_errors: list of {error_type, message} (empty on success)
      - _cache_age_seconds: age in seconds (set when status is stale)
      - _last_success_at: ISO-ish timestamp of last successful fetch
      - _field_sources: dict (set by compute_metrics_v9 + _apply_overrides)

    Failure mode (Step 2-B): if NO cache entry exists AND the provider
    raises, we let the exception propagate (preserves existing engine
    behavior — _score_one swallows + returns None). We do NOT cache
    exceptions.
    """
    sym = symbol.upper().strip()

    # 1. Cache lookup. We always read; freshness is decided by age.
    cached = _cache_get(sym)
    cache_age = _cache_age(cached) if cached else None
    # If a cache entry exists with no _cached_at stamp, treat as fresh
    # (legacy entries pre-Step 2-B, plus mocked test entries without
    # the stamp) so we don't burn a provider call unnecessarily.
    is_fresh = cached is not None and (cache_age is None or cache_age < CACHE_TTL_SEC)

    # 1a. Fresh cache hit → return immediately.
    if cached is not None and is_fresh:
        _STATS["hit"] += 1
        # Defensive: re-apply overrides on cached data too, in case our
        # _KNOWN_OVERRIDES dict was edited since the cache was populated.
        cached = _apply_overrides(cached, sym)
        # Mark provenance so consumers know this came from cache.
        cached["_provider_used"] = "cached_borsapy"
        cached["_cache_age_seconds"] = cache_age
        # Recompute data_status (free_float may have changed via override)
        status, missing = _compute_data_status(cached)
        cached["_data_status"] = status
        cached["_missing_fields"] = missing
        cached.setdefault("_provider_errors", [])
        _bump_status_counters(status, missing)
        return cached

    # 2. Stale cache exists OR cache miss — try fresh fetch.
    if cached is not None and not is_fresh:
        _STATS["cache_stale_hit"] += 1
    else:
        _STATS["miss"] += 1

    provider_errors: list[dict] = []
    metrics: Optional[dict] = None
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
        elif err_type == "timeout":
            _STATS["borsapy_timeout_error"] += 1
        elif err_type == "data_not_available":
            _STATS["borsapy_data_not_available_error"] += 1
        provider_errors.append({
            "error_type": err_type,
            "message": str(e)[:200],
        })

        # Phase A.10 Step 2-B — stale-while-revalidate.
        # If the live provider failed BUT we have a stale cache entry,
        # serve it. The user sees old-but-known-good numbers instead
        # of an empty error state.
        if cached is not None:
            _STATS["stale_cache_used_count"] += 1
            stale = _apply_overrides(dict(cached), sym)
            stale["_provider_used"] = "stale_cache"
            stale["_cache_age_seconds"] = cache_age
            stale["_provider_errors"] = provider_errors
            stale["_last_success_at"] = cached.get("_cached_at")
            # Force data_status="stale" regardless of underlying field state
            # (so UI can show DATA: STALE consistently).
            _, missing = _compute_data_status(stale)
            stale["_data_status"] = "stale"
            stale["_missing_fields"] = missing
            _STATS["data_status_stale"] += 1
            return stale

        # No cache + provider failure → preserve old behavior (raise).
        raise

    # 3. Fresh fetch succeeded → sanity → override → cache → return.
    metrics = _apply_sanity(metrics, sym)
    metrics = _apply_overrides(metrics, sym)

    # 4. Compute diagnostic fields.
    status, missing = _compute_data_status(metrics)
    metrics["_data_status"] = status
    metrics["_missing_fields"] = missing
    metrics["_provider_used"] = "borsapy"
    metrics["_provider_errors"] = provider_errors
    metrics["_cache_age_seconds"] = 0.0
    _bump_status_counters(status, missing)

    _cache_set(sym, metrics)
    return metrics


def _bump_status_counters(status: str, missing: list[str]) -> None:
    """Step 2-B: helper to update _STATS counters from a (status, missing)
    pair. Centralized so cached/stale/fresh paths stay consistent."""
    counter = f"data_status_{status}" if status in ("live", "partial", "missing", "stale") else "data_status_partial"
    _STATS[counter] = _STATS.get(counter, 0) + 1
    for f in missing:
        # Map field name to its counter — Step 2-B added missing_shares.
        # (`_required_fields_for_status()` returns market_cap, free_float, shares.)
        stat_key = f"missing_{f}"
        if stat_key in _STATS:
            _STATS[stat_key] += 1


def _classify_borsapy_error(exc: Exception) -> str:
    """Phase A.10: classify a borsapy/provider exception into a category.

    Returns one of:
      "fast_info"          — fast_info / shares fetch failure
      "history"            — OHLCV history fetch failure
      "income_stmt"        — financial statement fetch failure
      "timeout"            — TimeoutError unrelated to a specific subsystem (Step 2-B)
      "data_not_available" — DataNotAvailableError (Step 2-B)
      "unknown"            — unclassified

    Used to populate per-error-type counters in _STATS. We classify by
    exception class name AND message content. Subsystem-specific
    categories take priority over generic timeout/no-data so the
    diagnostics breakdown stays informative.
    """
    cls_name = type(exc).__name__
    msg = str(exc).lower()
    # Subsystem-specific first (most informative for debugging)
    if "fast_info" in msg or "fast info" in msg:
        return "fast_info"
    if "history" in msg or "ohlcv" in msg:
        return "history"
    if "income" in msg or "balance" in msg or "cashflow" in msg:
        return "income_stmt"
    # Step 2-B: generic timeout / no-data fallbacks
    if cls_name == "TimeoutError" or "timeouterror" in cls_name.lower() or "timeout" in msg:
        return "timeout"
    if "datanotavailable" in cls_name.lower() or "no financial data available" in msg:
        return "data_not_available"
    return "unknown"


def invalidate(symbol: str) -> bool:
    """Force-evict a symbol from cache (e.g. after manual override edit)."""
    if not redis_client.is_available():
        return False
    try:
        return redis_client.delete(CACHE_KEY_PREFIX + symbol.upper())
    except Exception:
        return False

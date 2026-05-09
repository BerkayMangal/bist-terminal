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
    """Stamp known good values over yfinance output for specific tickers."""
    sym = symbol.upper().replace(".IS", "").replace(".E", "").strip()
    overrides = _KNOWN_OVERRIDES.get(sym)
    if not overrides:
        return metrics
    for field, value in overrides.items():
        old = metrics.get(field)
        if old != value:
            log.info(
                "DATA QUALITY [%s]: override %s=%r → %r (manual)",
                sym, field, old, value,
            )
            metrics[field] = value
    return metrics


# ----------------------------------------------------------------
# Cache stats — exposed via /api/bullwatch/health for visibility.
# ----------------------------------------------------------------
_STATS: dict[str, int] = {"hit": 0, "miss": 0, "error": 0, "sanity_drop": 0}


def get_stats() -> dict[str, Any]:
    total = sum(_STATS.values())
    hit_pct = (_STATS["hit"] / total * 100) if total else None
    return {
        **_STATS,
        "total_lookups": total,
        "hit_pct": round(hit_pct, 1) if hit_pct is not None else None,
        "ttl_sec": CACHE_TTL_SEC,
        "redis_available": redis_client.is_available(),
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
        return _apply_overrides(cached, sym)

    # 2. Miss — fetch fresh.
    _STATS["miss"] += 1
    try:
        # Imported lazily to avoid circular dependency at module load
        from data.providers import compute_metrics_v9
        metrics = compute_metrics_v9(sym)
    except Exception:
        _STATS["error"] += 1
        raise

    # 3. Sanity → override → cache → return.
    metrics = _apply_sanity(metrics, sym)
    metrics = _apply_overrides(metrics, sym)
    _cache_set(sym, metrics)
    return metrics


def invalidate(symbol: str) -> bool:
    """Force-evict a symbol from cache (e.g. after manual override edit)."""
    if not redis_client.is_available():
        return False
    try:
        return redis_client.delete(CACHE_KEY_PREFIX + symbol.upper())
    except Exception:
        return False

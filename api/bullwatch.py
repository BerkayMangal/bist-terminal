# ================================================================
# BULLWATCH API — GET /api/bullwatch
#
# Returns ranked low-float BIST candidates with scores, zones, and
# descriptive pattern labels. Cached for 5 minutes (scan is ~30-60s
# end-to-end so we don't want to re-run on every page load).
#
# Backward-compat rule: this module ONLY adds new endpoints. It
# does not import or modify any existing endpoint logic.
# ================================================================

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Query, Body
from fastapi.responses import JSONResponse

from core.response_envelope import success, error
from core.snapshot_store import get_default_store, SnapshotLockHeld

log = logging.getLogger("bistbull.bullwatch_api")
router = APIRouter()

# Snapshot store module key — also used by tests and the background loop.
SNAPSHOT_MODULE = "bullwatch"

# In-memory cache — write-through mirror of the snapshot store. Kept so
# /api/bullwatch/watchlist/state (and any future module needing fast in-proc
# access) doesn't pay a Redis roundtrip on every request. Snapshot store is
# the source of truth; this is purely an L1 mirror.
_CACHE: dict[str, Any] = {
    "items": None,
    "as_of": None,
    "stale_after": 0.0,
    "running": False,
    "progress": 0,        # symbols processed so far (during in-flight scan)
    "total": 0,           # total symbols in the in-flight scan
    "scan_started_at": 0.0,
}
# Thread-safe lock for _CACHE writes. Multiple threads can write to this
# dict simultaneously: the scan executor thread updates progress, the
# refresh routine swaps items, the async health handler reads everything.
# Without this lock, readers can see partial state during writes.
# (Audit fix, Stage 1.)
import threading as _threading
_CACHE_LOCK = _threading.RLock()


def _cache_set(key: str, value: Any) -> None:
    """Thread-safe single-key setter for _CACHE."""
    with _CACHE_LOCK:
        _CACHE[key] = value


def _cache_get(key: str, default: Any = None) -> Any:
    """Thread-safe single-key getter for _CACHE."""
    with _CACHE_LOCK:
        return _CACHE.get(key, default)


def _cache_update(**kwargs) -> None:
    """Thread-safe batch updater — keeps writes atomic so readers see
    either the old state or the new, never half."""
    with _CACHE_LOCK:
        _CACHE.update(kwargs)


def _cache_snapshot() -> dict[str, Any]:
    """Return a deep-enough copy of _CACHE for safe iteration outside
    the lock. Items list is intentionally referenced (not deep-copied)
    because callers don't mutate it."""
    with _CACHE_LOCK:
        return dict(_CACHE)
_CACHE_TTL_SEC = 300  # 5 minutes — used for in-mem mirror freshness
_SCAN_DONE: Optional[asyncio.Event] = None  # set by the runner, awaited by waiters
_SCAN_MAX_WAIT_SEC = 300  # hard ceiling — even slow scans should fit (yfinance can be flaky)
# Snapshot age beyond which a refresh=false request schedules a background
# refresh anyway (still serves the stale data). 30 min matches the planned
# refresh loop cadence so we never deliver data older than ~one cycle.
_SNAPSHOT_SOFT_MAX_AGE_SEC = 1800


def _resolve_universe() -> list[str]:
    """BullWatch universe = FULL_BIST (deduped BIST30 ∪ EXTRA ∪ EXTENDED).

    All 437 unique BIST tickers we know about. BIST30 large-caps will
    almost always come back ineligible (float-mcap above the cap), but
    they are scanned and surfaced as ineligible rather than silently
    skipped — the user asked to "scan every stock". Power users can
    drill into any name via /api/bullwatch/{symbol}.
    """
    try:
        from config import FULL_BIST
        out = list(FULL_BIST)
    except ImportError:
        out = []
    if not out:
        # Fallbacks if FULL_BIST isn't exported (older config layouts)
        for const_name in ("UNIVERSE_EXTRA", "UNIVERSE_EXTENDED", "UNIVERSE_BIST30", "UNIVERSE"):
            try:
                mod = __import__("config", fromlist=[const_name])
                out.extend(getattr(mod, const_name, []))
            except Exception:
                continue
    return list(dict.fromkeys(out))


def _run_scan(min_score: float = 0.0,
              limit: Optional[int] = None,
              cap_tl: Optional[float] = None,
              diagnostic: bool = False) -> dict[str, Any]:
    """Synchronous scan — called from a thread executor.

    Always includes a `near_misses` array in the response: the 20
    ineligible-but-closest-to-passing symbols sorted by float mcap
    ascending. This guarantees the empty state is never empty —
    user sees what JUST missed and why, no second request needed.
    The `diagnostic` flag is kept for backward-compat but no longer
    changes the response shape (always full).
    """
    from engine.bullwatch import scan
    from features.bullwatch_features import FLOAT_MARKET_CAP_CAP_TL
    universe = _resolve_universe()
    t0 = time.time()
    # Thread-safe atomic init — the async health handler reads these
    # concurrently with the scan thread that writes them.
    _cache_update(
        scan_started_at=t0,
        progress=0,
        total=len(universe),
    )

    def _on_progress(done: int, total: int) -> None:
        # Atomic — readers see either old or new pair, never partial.
        _cache_update(progress=done, total=total)

    # Overhaul Stage 2: pull previous-scan zones for hysteresis.
    # Once a ticker earned a zone, the exit threshold is HIGHER than the
    # entry threshold (2pt buffer) — prevents run-to-run flap at the
    # 75/60 boundary. First scan or new ticker → no previous zone → no
    # hysteresis applied (back-compat).
    previous_zones = _read_previous_zone_map()

    # Always include_ineligible=True — it's just a filter on already-computed
    # results, no extra fetches. Lets us surface near_misses for free.
    # max_workers=16 — yfinance fetches are I/O-bound, GIL doesn't apply.
    results = scan(universe, min_score=min_score,
                   include_ineligible=True, cap_tl=cap_tl,
                   max_workers=16, progress_callback=_on_progress,
                   previous_zones=previous_zones)
    eligible = [r for r in results if r.eligible]
    if limit:
        eligible = eligible[:limit]

    # Near-misses: ineligible with KNOWN float mcap, sorted ascending so
    # the smallest (= closest to passing) come first.
    from features.bullwatch_features import normalize_free_float
    ineligible = [
        r for r in results
        if not r.eligible and r.metrics.get("float_market_cap") is not None
    ]
    ineligible.sort(key=lambda r: r.metrics.get("float_market_cap") or 0)
    near_misses = [
        {
            "symbol": r.symbol,
            "float_market_cap": r.metrics.get("float_market_cap"),
            "market_cap": r.metrics.get("market_cap"),
            "free_float": normalize_free_float(r.metrics.get("free_float")),
            "avg_traded_value_20d": r.metrics.get("avg_traded_value_20d"),
            "reject_reason": r.reject_reason,
        }
        for r in ineligible[:20]
    ]

    items = [r.to_dict() for r in eligible]

    # Annotate each item with a `delta` field (vs yesterday's snapshot).
    # Fail-soft: if Redis is down or no history, items get a stable/no-op
    # delta and the rest of the response is unaffected.
    try:
        from data.bullwatch_history import annotate_with_deltas, save_snapshot
        annotate_with_deltas(items)
        # Persist this scan's results so tomorrow's scan can compute delta.
        # Save AFTER annotation so we don't compare today against itself.
        save_snapshot(items)
    except Exception as e:
        log.warning("BullWatch history annotate/save failed: %r", e)

    return {
        "items": items,
        "scanned": len(universe),
        "eligible_count": sum(1 for r in results if r.eligible),
        "ineligible_count": sum(1 for r in results if not r.eligible),
        "cap_tl": cap_tl or FLOAT_MARKET_CAP_CAP_TL,
        "near_misses": near_misses,
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
        "duration_ms": round((time.time() - t0) * 1000, 0),
    }


# ================================================================
# SNAPSHOT STORE INTEGRATION
#
# The previous warmup_cache_loop lived here and wrote to an in-memory
# _CACHE only. It has been replaced by:
#   - engine.background_tasks.bullwatch_refresh_loop (the periodic loop)
#   - _refresh_and_persist (one scan + snapshot write + _CACHE mirror)
#   - _schedule_background_refresh (fire-and-forget refresh from request)
#   - _read_snapshot_payload (snapshot → response payload)
#
# Snapshot store is the source of truth; _CACHE is a write-through mirror
# kept for in-proc lookups (watchlist enrichment, single-symbol endpoint).
# ================================================================


def _persist_snapshot(payload: dict) -> Optional[str]:
    """Write a _run_scan output to the shared snapshot store.

    Returns the new scan_id on success, None if the store is unavailable
    or rejected the write. Caller continues normally — snapshot persistence
    is best-effort, never blocks the response path.

    Side effect — Alarm dispatch (immutable high-conviction history).
    The BullWatch list itself is volatile (re-ranks every scan); the
    alarm record persists across scans so the user can answer
    "system gave a strong call N days ago — where is the ticker now?"
    """
    items = payload.get("items") or []
    # Dispatch alarms — best-effort, never blocks the snapshot write
    try:
        from engine.bullwatch_alerts import dispatch_scan_alerts
        dispatch_scan_alerts(items)
    except Exception as exc:
        log.warning("BullWatch alarm dispatch failed: %r", exc)
    if not items:
        return None
    try:
        scored = [
            (it.get("symbol", ""), float(it.get("score") or 0.0), it)
            for it in items
            if it.get("symbol")
        ]
        meta = {
            "scanned": payload.get("scanned"),
            "eligible_count": payload.get("eligible_count"),
            "ineligible_count": payload.get("ineligible_count"),
            "cap_tl": payload.get("cap_tl"),
            "duration_ms": payload.get("duration_ms"),
            "as_of": payload.get("as_of"),
            "near_misses": payload.get("near_misses", []),
        }
        store = get_default_store()
        return store.write_snapshot(SNAPSHOT_MODULE, scored, meta=meta)
    except Exception as exc:
        log.warning("BullWatch snapshot persist failed: %r", exc)
        return None


def _read_previous_zone_map(
    module: str = "bullwatch",
) -> dict[str, str]:
    """Return {ticker: zone} from the latest BullWatch snapshot.

    Used by Stage 2 hysteresis — score_symbol() consults this map per
    ticker so that boundary-zone tickers don't flap between
    CONVICTION/CONFIRMED/EARLY across scans.

    Falls back to empty dict if snapshot store is unavailable or no
    snapshot exists yet (first scan after startup, or fresh install).
    """
    out: dict[str, str] = {}
    try:
        store = get_default_store()
        scan_id = store.read_latest_scan_id(module)
        if scan_id is None:
            return out
        # Read the top items (we keep up to 500); each item already has
        # a `zone` field from the previous scoring pass.
        top = store.read_top(module, 500, scan_id=scan_id)
        if not top:
            return out
        tickers = [t for t, _ in top]
        items_map = store.read_items(module, tickers, scan_id=scan_id)
        for t, it in (items_map or {}).items():
            z = (it or {}).get("zone")
            if z:
                out[t] = z
    except Exception as exc:
        log.debug("read_previous_zone_map failed: %r", exc)
    return out


def _read_snapshot_payload(
    limit: int = 200,
    module: str = SNAPSHOT_MODULE,
) -> Optional[dict]:
    """Reconstruct a _run_scan-shaped payload from the latest snapshot.

    Returns None when no healthy snapshot is available. If the latest is
    corrupted, attempts a one-time fallback to `previous` automatically.

    `module` defaults to SNAPSHOT_MODULE (`bullwatch`) — the canonical
    cold snapshot. Pass `bullwatch_hot` to read the D.3 hot-tier subset.
    """
    try:
        store = get_default_store()
        scan_id = store.read_latest_scan_id(module)
        if scan_id is None:
            return None
        if not store.is_healthy(module, scan_id=scan_id):
            log.warning("BullWatch snapshot %s corrupted — trying previous", scan_id)
            if not store.fallback_to_previous(module):
                return None
            scan_id = store.read_latest_scan_id(module)
            if scan_id is None:
                return None
        meta = store.read_meta(module, scan_id=scan_id)
        if meta is None:
            return None
        top = store.read_top(module, limit, scan_id=scan_id)
        if not top:
            return None
        items_map = store.read_items(
            module, [t for t, _ in top], scan_id=scan_id,
        )
        items = [items_map[t] for t, _ in top if t in items_map]
        if not items:
            return None
        return {
            "items": items,
            "scanned": meta.get("scanned"),
            "eligible_count": meta.get("eligible_count"),
            "ineligible_count": meta.get("ineligible_count"),
            "cap_tl": meta.get("cap_tl"),
            "duration_ms": meta.get("duration_ms"),
            "as_of": meta.get("as_of"),
            "near_misses": meta.get("near_misses", []),
            "_snapshot_scan_id": meta.get("scan_id"),
            "_snapshot_asof_unix": meta.get("asof_unix"),
        }
    except Exception as exc:
        log.warning("BullWatch snapshot read failed: %r", exc)
        return None


# Watchdog: scan'ler bazen borsapy timeout'ları yüzünden 427/437'de
# takılı kalabiliyor — production'da 491s sonucu olmadan asılı kaldığı
# gözlemlendi. Bu cap'i geçen scan'ı zorla bitir ve partial sonuç publish et.
_SCAN_WATCHDOG_SEC = 8 * 60        # 8 min hard cap


async def _refresh_and_persist(min_score: float = 0.0) -> Optional[dict]:
    """Run one full scan and persist it. Idempotent — returns None if a
    scan is already in flight. Used by the background refresh loop and by
    the request-triggered `_schedule_background_refresh`."""
    global _SCAN_DONE
    # Read-test-act under lock so concurrent refresh requests don't
    # both pass the `not running` check and start two scans.
    with _CACHE_LOCK:
        if _CACHE["running"]:
            # Audit fix: if scan has been "running" for too long, it's
            # almost certainly hung on borsapy stragglers. Force-reset
            # the flag so a new scan can run.
            started_at = _CACHE.get("scan_started_at") or 0
            if started_at and (time.time() - started_at) > _SCAN_WATCHDOG_SEC:
                log.warning(
                    "BullWatch scan watchdog: %.0fs elapsed (cap %ds), "
                    "force-resetting",
                    time.time() - started_at, _SCAN_WATCHDOG_SEC,
                )
                _CACHE["running"] = False
                if _SCAN_DONE is not None:
                    _SCAN_DONE.set()
            else:
                log.debug("BullWatch refresh: scan already in flight, skipping")
                return None
        _CACHE["running"] = True
        _CACHE["scan_started_at"] = time.time()
    _SCAN_DONE = asyncio.Event()
    try:
        t0 = time.time()
        # Snapshot the previous in-mem items BEFORE we overwrite them,
        # so the membership detector can diff old-vs-new.
        prev_items_for_diff = list(((_CACHE.get("items") or {}).get("items")) or [])
        # Wrap _run_scan in an asyncio timeout so a hung scan can't pin
        # the running flag indefinitely. SCAN_TIMEOUT_SEC (1200) is the
        # inner hard cap; we add our own watchdog at 8 min.
        try:
            payload = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, _run_scan, min_score, None, None, False,
                ),
                timeout=_SCAN_WATCHDOG_SEC,
            )
        except asyncio.TimeoutError:
            log.warning(
                "BullWatch scan exceeded %ds watchdog — publishing partial "
                "from previous snapshot",
                _SCAN_WATCHDOG_SEC,
            )
            # Best-effort: keep the user's UI working with the LAST good
            # snapshot. Don't write a new partial — we don't trust it.
            return None
        scan_id = _persist_snapshot(payload)
        # Atomic publish — readers waiting for new items see all three
        # fields update at once, never a half-populated state.
        _cache_update(
            items=payload,
            as_of=payload["as_of"],
            stale_after=time.time() + _CACHE_TTL_SEC,
        )
        # Membership events — detect entries/exits/zone changes vs prev.
        # Only when we actually had a previous list to diff against.
        if prev_items_for_diff:
            try:
                from engine.bullwatch_membership import detect_and_persist
                await asyncio.to_thread(
                    detect_and_persist,
                    prev_items_for_diff,
                    payload.get("items") or [],
                    scan_id,
                )
            except Exception as _mexc:
                log.warning("membership detect failed: %r", _mexc)
        log.info(
            "BullWatch refresh complete in %.1fs — %d eligible / %d scanned, snapshot=%s",
            time.time() - t0,
            payload.get("eligible_count", 0),
            payload.get("scanned", 0),
            scan_id or "(not persisted)",
        )
        return payload
    except asyncio.CancelledError:
        log.info("BullWatch refresh: cancelled")
        raise
    except Exception as exc:
        log.exception("BullWatch refresh failed: %r", exc)
        return None
    finally:
        if _SCAN_DONE is not None:
            _SCAN_DONE.set()
        _cache_set("running", False)


def _schedule_background_refresh() -> bool:
    """Fire-and-forget background refresh. Returns True if a new scan
    was scheduled, False if one was already in flight (idempotent)."""
    if _CACHE["running"]:
        return False
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return False
    loop.create_task(_refresh_and_persist())
    return True


async def _cold_start_scan() -> Optional[dict]:
    """Blocking scan path, used only when no snapshot and no in-mem cache
    exists. If another scan is already in flight, awaits its completion
    rather than starting a duplicate."""
    global _SCAN_DONE
    if _CACHE["running"]:
        evt = _SCAN_DONE
        if evt is None:
            return None
        try:
            await asyncio.wait_for(evt.wait(), timeout=_SCAN_MAX_WAIT_SEC)
        except asyncio.TimeoutError:
            return None
        return _CACHE.get("items")
    return await _refresh_and_persist()


# Fields kept in `lite` mode — the BullWatch list view only ever
# renders these. Heavy fields (metrics, components, reasons, narrative)
# are fetched lazily via /api/bullwatch/{ticker} when a card opens.
_LITE_FIELDS = (
    "symbol", "score", "zone", "pattern",
    "data_quality", "sector", "sector_group", "sector_tr",
    "industry", "delta", "is_late",
    # KAP boost meta surfaced by the new engine — small + actionable
    "data_status",
)


def _trim_item_for_lite(item: dict) -> dict:
    """Drop heavy fields from a BullWatchResult dict to shrink payload."""
    return {k: item.get(k) for k in _LITE_FIELDS if k in item}


def _apply_filters_and_slice(
    payload: dict,
    zone: Optional[str],
    min_score: float,
    limit: int,
    lite: bool = False,
) -> list[dict]:
    """Per-request filter on the cached/snapshot items. Filters happen on
    read, never on write — caching every parameter combo isn't worth it.

    When `lite=True`, heavy fields (metrics, components, reasons,
    narrative, …) are stripped. The list view never uses them; the
    detail panel re-fetches via /api/bullwatch/{ticker}. Cuts ~70% of
    response size on a 50-item list.
    """
    items = list(payload.get("items") or [])
    if zone:
        zone_norm = zone.strip().upper()
        items = [it for it in items if it.get("zone") == zone_norm]
    if min_score > 0:
        items = [it for it in items if (it.get("score") or 0) >= min_score]
    items = items[:limit]
    if lite:
        items = [_trim_item_for_lite(it) for it in items]
    return items


@router.get("/api/bullwatch")
async def api_bullwatch(
    refresh: bool = Query(False, description="Force a fresh scan, bypassing cache"),
    min_score: float = Query(0.0, ge=0, le=100,
                             description="Minimum BullWatch score to include"),
    zone: Optional[str] = Query(None,
                                description="Filter by zone: EARLY, CONFIRMED, CONVICTION"),
    limit: int = Query(50, ge=1, le=200,
                       description="Max results to return"),
    cap_tl: Optional[float] = Query(None, ge=10_000_000, le=10_000_000_000,
                                     description="Override float-mcap cap (TL). Default 250M."),
    diagnostic: bool = Query(False,
                             description="Include top 20 near-miss ineligible stocks"),
    tier: Optional[str] = Query(None,
                                description=(
                                    "Snapshot tier: `hot` reads the 5-min "
                                    "refreshed top-50 subset. Default reads "
                                    "the 30-min full-universe snapshot."
                                )),
    lite: bool = Query(False,
                       description=(
                           "Strip heavy item fields (metrics, components, "
                           "reasons, narrative). Cuts ~70% of payload — "
                           "the list view uses this; detail panel re-fetches "
                           "via /api/bullwatch/{ticker}."
                       )),
):
    """
    Return ranked BullWatch candidates.

    Response shape (flat, V9.1-compatible — see core.response_envelope):
        {
          "items": [
            {"symbol": "...", "score": 92.0, "zone": "CONFIRMED",
             "pattern": "Float Squeeze + Insider Activity",
             "data_quality": "high",
             "components": {...}, "metrics": {...}, "reasons": [...]}
          ],
          "scanned": 238,
          "eligible_count": 12,
          "as_of": "...",
          "_meta": {...}
        }
    """
    now = time.time()
    is_experimental = (cap_tl is not None) or diagnostic

    # ── Experimental requests bypass snapshot+cache (ad-hoc tuning) ──
    if is_experimental:
        try:
            payload = await asyncio.get_event_loop().run_in_executor(
                None, _run_scan, min_score, None, cap_tl, diagnostic,
            )
        except Exception as exc:
            log.exception("BullWatch experimental scan failed: %r", exc)
            return error(f"BullWatch scan failed: {exc}", status_code=500)
        return _build_response(
            payload, "experimental", zone, min_score, limit,
            from_snapshot=False, lite=lite,
        )

    # ── Pick the snapshot module based on the requested tier ───────
    tier_norm = (tier or "").strip().lower()
    if tier_norm == "hot":
        # Try the hot tier first; fall back to cold so the user doesn't
        # see an empty page during the first 8 min after boot when the
        # hot loop hasn't run yet.
        view = _read_snapshot_payload(limit=200, module="bullwatch_hot")
        if view is None:
            view = _read_snapshot_payload(limit=200, module=SNAPSHOT_MODULE)
        view_source = "snapshot"
    else:
        view = _read_snapshot_payload(limit=200, module=SNAPSHOT_MODULE)
        view_source: Optional[str] = "snapshot" if view is not None else None
    if view is None and _CACHE.get("items") is not None:
        view = _CACHE["items"]
        view_source = "memory"

    view_age_sec: Optional[float] = None
    if view is not None:
        asof_unix = view.get("_snapshot_asof_unix")
        if asof_unix is None and _CACHE.get("stale_after"):
            asof_unix = _CACHE["stale_after"] - _CACHE_TTL_SEC
        if asof_unix is not None:
            view_age_sec = max(0.0, now - float(asof_unix))

    snapshot_scan_id = view.get("_snapshot_scan_id") if view else None
    from_snapshot = view_source == "snapshot"

    # ── refresh=true: never block when a view exists ────────────────
    if refresh:
        if view is not None:
            scheduled = _schedule_background_refresh()
            return _build_response(
                view, "snapshot_with_refresh" if from_snapshot else "memory_with_refresh",
                zone, min_score, limit,
                from_snapshot=from_snapshot,
                scan_id=snapshot_scan_id,
                refresh_scheduled=scheduled,
                lite=lite,
            )
        # No view at all → cold-start (blocks; spec-allowed only here)
        payload = await _cold_start_scan()
        if payload is None:
            return error("BullWatch cold-start scan failed", status_code=500)
        return _build_response(
            payload, "cold_start", zone, min_score, limit,
            from_snapshot=False, lite=lite,
        )

    # ── refresh=false: serve view if fresh; schedule refresh if stale ──
    if view is not None:
        is_fresh = view_age_sec is None or view_age_sec < _SNAPSHOT_SOFT_MAX_AGE_SEC
        if is_fresh:
            return _build_response(
                view, "snapshot_hit" if from_snapshot else "memory_hit",
                zone, min_score, limit,
                from_snapshot=from_snapshot,
                scan_id=snapshot_scan_id,
                lite=lite,
            )
        # Stale → schedule refresh and serve stale view in the meantime
        scheduled = _schedule_background_refresh()
        return _build_response(
            view, "stale_with_refresh", zone, min_score, limit,
            from_snapshot=from_snapshot,
            scan_id=snapshot_scan_id,
            refresh_scheduled=scheduled,
            stale=True,
            lite=lite,
        )

    # ── No view exists at all → cold-start (blocking) ───────────────
    payload = await _cold_start_scan()
    if payload is None:
        return error("BullWatch cold-start scan failed", status_code=500)
    return _build_response(
        payload, "cold_start", zone, min_score, limit,
        from_snapshot=False, lite=lite,
    )


def _build_response(
    payload: dict,
    cache_status: str,
    zone: Optional[str],
    min_score: float,
    limit: int,
    from_snapshot: bool = False,
    scan_id: Optional[str] = None,
    refresh_scheduled: bool = False,
    stale: bool = False,
    lite: bool = False,
) -> JSONResponse:
    """Apply filters, build response envelope, attach snapshot meta."""
    items = _apply_filters_and_slice(payload, zone, min_score, limit, lite=lite)
    extra_meta: dict[str, Any] = {
        "engine": "bullwatch_v1",
        "from_snapshot": from_snapshot,
    }
    if scan_id:
        extra_meta["scan_id"] = scan_id
    if refresh_scheduled:
        extra_meta["refresh_scheduled"] = True
    if stale:
        extra_meta["stale"] = True
    return success(
        {
            "items": items,
            "scanned": payload.get("scanned", 0),
            "eligible_count": payload.get("eligible_count", 0),
            "ineligible_count": payload.get("ineligible_count", 0),
            "cap_tl": payload.get("cap_tl"),
            "near_misses": payload.get("near_misses", []),
            "duration_ms": payload.get("duration_ms"),
        },
        as_of=payload.get("as_of"),
        cache_status=cache_status,
        extra_meta=extra_meta,
    )


@router.get("/api/bullwatch/health")
async def api_bullwatch_health():
    """Lightweight health check — does NOT trigger a scan.

    Frontend polls this every few seconds while a scan is in flight to
    show real-time progress instead of a blank spinner.
    """
    cached = _CACHE["items"] is not None
    running = _CACHE["running"]
    progress = _CACHE.get("progress", 0)
    total = _CACHE.get("total", 0)
    scan_started = _CACHE.get("scan_started_at", 0)
    elapsed_sec = (time.time() - scan_started) if (running and scan_started) else None

    return success({
        "ok": True,
        "engine": "bullwatch_v1",
        "cache_populated": cached,
        "cache_as_of": _CACHE.get("as_of"),
        "cache_age_sec": (time.time() - (_CACHE["stale_after"] - _CACHE_TTL_SEC))
                         if cached else None,
        "scan_running": running,
        "scan_progress": progress,
        "scan_total": total,
        "scan_progress_pct": round(progress / total * 100, 1) if total else None,
        "scan_elapsed_sec": round(elapsed_sec, 1) if elapsed_sec is not None else None,
        "cache": _cache_stats_safe(),
        "history": _history_stats_safe(),
        # Phase A.10 Step 2-B.1: scan runtime diagnostics
        "scan_diagnostics": _scan_diagnostics_safe(),
    })


def _scan_diagnostics_safe() -> dict:
    """Phase A.10 Step 2-B.1: per-scan runtime stats (which symbols hit
    timeout / cancellation, average + p95 timing, total duration). Never
    raises from the health check path."""
    try:
        from engine.bullwatch import get_scan_stats
        return get_scan_stats()
    except Exception:
        return {"error": "scan_stats_unavailable"}


def _cache_stats_safe() -> dict:
    """Safely fetch BullWatch metrics-cache stats — never raise from health check."""
    try:
        from data.bullwatch_cache import get_stats
        return get_stats()
    except Exception:
        return {"error": "stats_unavailable"}


def _history_stats_safe() -> dict:
    """Safely fetch BullWatch snapshot history stats."""
    try:
        from data.bullwatch_history import get_history_stats
        return get_history_stats()
    except Exception:
        return {"error": "stats_unavailable"}


@router.post("/api/bullwatch/watchlist/state")
async def api_bullwatch_watchlist_state(
    payload: dict = Body(...),
):
    """
    Stateless watchlist enrichment.

    The watchlist itself lives in the user's browser (localStorage —
    keeps it private to the device, no server-side personal data).
    The frontend POSTs `{symbols: ["KAPLM", "KARTN", ...]}` and gets
    back the latest known state of each: current eligibility, score,
    zone, delta vs yesterday, plus a 7-day score history for the
    sparkline.

    Items the user is watching but that aren't currently eligible get
    a `cooled_off` flag so the UI can mark them — important signal
    for tape readers ("the absorption setup I was watching has
    dissipated"). NOT a sell recommendation; just a state change.
    """
    symbols = payload.get("symbols") or []
    if not isinstance(symbols, list):
        return error("symbols must be a list")

    # Cap the request size — defensive limit for a tool with no auth.
    symbols = [str(s).upper().strip() for s in symbols if s][:100]

    # Pull current scan results (cache hit if warmup ran recently)
    items_index: dict[str, dict] = {}
    if _CACHE.get("items"):
        for it in _CACHE["items"].get("items", []):
            items_index[it["symbol"]] = it

    # Yesterday's snapshot for delta computation when symbol isn't in
    # today's eligible set
    try:
        from data.bullwatch_history import (
            get_yesterday_snapshot, get_score_history,
            compute_delta_for_item,
        )
        yesterday = get_yesterday_snapshot()
    except Exception:
        yesterday = None
        get_score_history = lambda *_a, **_kw: [None] * 7  # type: ignore

    out = []
    for sym in symbols:
        current = items_index.get(sym)
        if current:
            # Eligible right now
            try:
                history = get_score_history(sym, days=7)
            except Exception:
                history = [None] * 7
            out.append({
                "symbol": sym,
                "eligible": True,
                "score": current.get("score"),
                "zone": current.get("zone"),
                "pattern": current.get("pattern"),
                "sector_tr": current.get("sector_tr"),
                "delta": current.get("delta"),
                "score_history_7d": history,
                "cooled_off": False,
            })
        else:
            # Not in today's eligible set. Was it in yesterday's?
            prior = (yesterday or {}).get(sym)
            try:
                history = get_score_history(sym, days=7)
            except Exception:
                history = [None] * 7
            out.append({
                "symbol": sym,
                "eligible": False,
                "score": None,
                "zone": None,
                "pattern": None,
                "sector_tr": None,
                "delta": None,
                "prev_score": prior.get("score") if prior else None,
                "prev_zone": prior.get("zone") if prior else None,
                "score_history_7d": history,
                "cooled_off": prior is not None,  # was eligible yesterday
            })

    return success({
        "items": out,
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
    })


@router.get("/api/bullwatch/sector-rotation")
async def api_bullwatch_sector_rotation(
    window_days: int = 7,
    include_alarms: bool = True,
    include_membership: bool = True,
):
    """Tahtacı sektör rotasyonu — hangi sektör ısınıyor, hangisi soğuyor.

    Mevcut alarm storage + membership events üzerinden per-sektör net
    aktivite skoru hesaplar. Read-only — yan etki yok.

    Trend bands:
      ≥6  → 🔥 hot      (sektör ısınıyor)
      ≥2  → ⚡ warm
      -2..2 → ➡️ neutral
      ≤-2 → ❄️ cooling
    """
    from engine.bullwatch_sector_rotation import compute_rotation
    if window_days < 1 or window_days > 90:
        window_days = 7
    data = await asyncio.get_event_loop().run_in_executor(
        None, compute_rotation,
        window_days, include_alarms, include_membership,
    )
    return success(data, extra_meta={"endpoint": "bullwatch.sector_rotation"})


@router.get("/api/bullwatch/sector-rotation/summary")
async def api_bullwatch_sector_rotation_summary(window_days: int = 7):
    """Banner aggregate — kaç sektör ısınıyor / soğuyor."""
    from engine.bullwatch_sector_rotation import get_rotation_summary
    if window_days < 1 or window_days > 90:
        window_days = 7
    data = await asyncio.get_event_loop().run_in_executor(
        None, get_rotation_summary, window_days,
    )
    return success(data, extra_meta={"endpoint": "bullwatch.sector_rotation.summary"})


@router.get("/api/bullwatch/pre-alarms")
async def api_bullwatch_pre_alarms(
    score_min: float = 70.0,
    score_max: float = 75.0,
    tahtaci_min: float = 0.30,
    limit: int = 20,
):
    """Pre-alarm candidates — "tahtacı yaklaşıyor".

    CONVICTION mantığını BOZMUYORUZ; mevcut alarmlar (score≥75 + zone +
    ≥2 motor + data_quality=high) aynen kalır. Bu endpoint sadece
    score 70-75 arasında olan AMA güçlü tahtacı sinyali (kap_activity +
    ownership + group_boost + walkup) taşıyan adayları surface eder.

    Read-only: bu adaylar alarm storage'a yazılmaz, sadece UI'de
    "yaklaşan" panel olarak gösterilir. Kullanıcı CONVICTION'a girmeden
    önce yakalayabilir.

    Literal path BEFORE /bullwatch/{symbol} (route order lesson).
    """
    from engine.bullwatch_prealarm import find_pre_alarm_candidates

    # Read live cache (or snapshot fallback) — same source as main list.
    items = ((_CACHE.get("items") or {}).get("items")) or []
    if not items:
        snap = _read_snapshot_payload(limit=500)
        items = (snap or {}).get("items") or []

    candidates = await asyncio.get_event_loop().run_in_executor(
        None,
        find_pre_alarm_candidates,
        items, score_min, score_max, tahtaci_min, ("CONFIRMED",), limit,
    )
    return success(
        {"items": candidates, "count": len(candidates),
         "score_min": score_min, "score_max": score_max,
         "tahtaci_min": tahtaci_min},
        extra_meta={"endpoint": "bullwatch.pre_alarms"},
    )


@router.get("/api/bullwatch/pre-alarms/summary")
async def api_bullwatch_pre_alarms_summary():
    """Banner aggregate — kaç hisse alarma yaklaşıyor."""
    from engine.bullwatch_prealarm import get_pre_alarm_summary
    items = ((_CACHE.get("items") or {}).get("items")) or []
    if not items:
        snap = _read_snapshot_payload(limit=500)
        items = (snap or {}).get("items") or []
    data = await asyncio.get_event_loop().run_in_executor(
        None, get_pre_alarm_summary, items,
    )
    return success(data, extra_meta={"endpoint": "bullwatch.pre_alarms.summary"})


@router.get("/api/bullwatch/explain/{symbol}")
async def api_bullwatch_explain(symbol: str):
    """Score explainability for one ticker — "Niye bu skor / niye
    tahtacı sinyali?". Pulls the symbol's current item from the live
    BullWatch cache (or snapshot fallback) and runs it through
    `engine.bullwatch_explainability.build_explanation`.

    Returns: Tahtacı Signal Strength + per-engine breakdown grouped
    into 🎯 Tahtacı / 📊 Teyit / 🏛️ Bağlam categories + previous
    snapshot delta when available.

    Literal path registered BEFORE variadic /{symbol} so FastAPI
    doesn't route 'explain' as a ticker symbol. (Lesson from PR #51.)
    """
    sym = (symbol or "").upper().strip().replace(".IS", "")
    if not sym:
        return error("empty symbol", status_code=400)

    def _go():
        # 1) Try live in-mem cache
        items = ((_CACHE.get("items") or {}).get("items")) or []
        for it in items:
            if (it.get("symbol") or "").upper() == sym:
                return it
        # 2) Fall back to snapshot store
        snap = _read_snapshot_payload(limit=500)
        if snap and snap.get("items"):
            for it in snap["items"]:
                if (it.get("symbol") or "").upper() == sym:
                    return it
        return None

    item = await asyncio.get_event_loop().run_in_executor(None, _go)
    if not item:
        return error(
            f"{sym} not currently in BullWatch list — only listed tickers "
            "can be explained. Run a scan first or check the ticker.",
            status_code=404,
        )

    from engine.bullwatch_explainability import build_explanation
    bundle = await asyncio.get_event_loop().run_in_executor(
        None, build_explanation, item,
    )
    return success(
        bundle,
        extra_meta={"endpoint": "bullwatch.explain"},
    )


@router.get("/api/bullwatch/{symbol}")
async def api_bullwatch_symbol(symbol: str):
    """
    Score a single symbol against BullWatch criteria. Useful for
    inspecting why a stock did or didn't qualify.
    """
    from utils.helpers import normalize_symbol
    from engine.bullwatch import score_symbol

    sym = normalize_symbol(symbol).replace(".IS", "")
    try:
        # Phase A.10 Step 2-A FIX: route through cached_compute_metrics
        # so that direct symbol calls go through the same sanity +
        # override + Redis cache pipeline as the warmup scan. Previously
        # this called compute_metrics_v9 directly, bypassing manual
        # overrides — which is why /api/bullwatch/KAPLM showed "no float
        # data" even though KAPLM has an override. cached_compute_metrics
        # also stamps diagnostic fields (_data_status, _field_sources,
        # override_applied, ...) which the score_symbol downstream then
        # surfaces in the BullWatchResult.
        from data.bullwatch_cache import cached_compute_metrics
        from engine.technical import batch_download_history
    except ImportError as exc:
        return error(f"data layer unavailable: {exc}", status_code=503)

    try:
        metrics = await asyncio.get_event_loop().run_in_executor(
            None, cached_compute_metrics, sym,
        )
    except Exception as exc:
        return error(f"metrics fetch failed: {exc}", status_code=502)

    try:
        hist = await asyncio.get_event_loop().run_in_executor(
            None, batch_download_history, [sym],
        )
        df = (hist or {}).get(sym)
    except Exception as exc:
        log.warning("BullWatch %s: history fetch failed: %r", sym, exc)
        df = None

    try:
        result = score_symbol(metrics, df, ownership=None)
    except Exception as exc:
        log.exception("BullWatch %s: scoring failed", sym)
        return error(f"scoring failed: {exc}", status_code=500)

    return success(result.to_dict(),
                   as_of=dt.datetime.now(dt.timezone.utc).isoformat(),
                   extra_meta={"engine": "bullwatch_v1"})

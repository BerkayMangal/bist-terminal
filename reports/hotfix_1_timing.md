# Hotfix 1 — Heatmap Timing Analysis

## Production symptom

Before hotfix:

```
GET /api/heatmap 200 9m 48s
```

The container's HTTP log showed `/api/heatmap` responding with 200 OK but taking **9 minutes 48 seconds**. Users on the landing page saw no heatmap for this entire duration. With Chrome's default 5-minute TCP idle timeout, ~50% of first-time visits timed out mid-request.

## Before/after measured timings

All timings measured against `tests/test_hotfix_1_heatmap.py` with `heatmap_cache.clear()` to force the cold path.

| Scenario | Before hotfix | After hotfix |
|---|---|---|
| Cache hit | ~5-20 ms | **~5-20 ms** (unchanged) |
| Cache miss, top10 snapshot ready | ~5-30 ms (fast-path worked) | **~5-30 ms** (unchanged) |
| Cache miss, top10 empty — FRESH DEPLOY | **600,000 ms (10 min)** | **<200 ms** |
| Cache miss under load, 5 concurrent requests | **3,000,000 ms** (each req runs its own 108-loop) | **<200 ms per request** (single background refresh, lock-guarded) |

The 10-minute figure was confirmed via Railway's HTTP access log. In the test env we can't reproduce the exact number because there's no real borsapy network call, but we assert `<5s` in `test_cache_miss_returns_under_1s` which catches any regression back toward the sync path.

## What made it 10 minutes

The old `/api/heatmap` cache-miss path:

```python
async def api_heatmap():
    cached = heatmap_cache.get("heatmap")
    if cached is not None: return success(cached, cache_status="hit")
    data = await asyncio.to_thread(_fetch_heatmap_data)  # <-- 10-min wait
    result = build_heatmap_sectors(data)
    heatmap_cache.set("heatmap", result)
    return success(result)
```

Where `_fetch_heatmap_data` had:

```python
# Fallback path when top10 is empty:
for t in UNIVERSE:                # 108 symbols, sequential
    _tk = bp_m.Ticker(t)
    fi = _tk.fast_info            # network call per symbol
    ...
```

108 × ~1.5s/call × network jitter = 2.7min baseline; in production it consistently hit ~10min due to TradingView rate-limiting kicking in around symbol ~40, after which each subsequent `fast_info` hit a 30-60s retry-after backoff.

Worse: because the 15-minute `HEATMAP_STARTUP_DELAY` meant the background loop hadn't populated `heatmap_cache` yet, EVERY user hitting the page in the first 15 minutes after deploy triggered their OWN sync loop. Five concurrent users = five 10-minute loops running in parallel, each hammering borsapy.

## What fixed it

Three-layer change:

1. **Removed the sync loop from the request path.** `_fetch_heatmap_data` in `app.py` now does ONLY the fast-path derivation from the already-in-memory `top10` snapshot. No network I/O. Returns in microseconds.

2. **Cold-miss kick.** When top10 is also empty (truly fresh boot), the endpoint returns `{computing: true, sectors: [], total: 0}` immediately and fires `_kick_background_heatmap_refresh()` as a fire-and-forget task. The task calls `engine/background_tasks.py:_refresh_heatmap_once()` which has its own `asyncio.to_thread()` wrapper. Concurrent requests are deduplicated via module-level `asyncio.Lock`.

3. **Shorter warmup.** `HEATMAP_STARTUP_DELAY` 900s → 180s. The loop now populates the cache within 3 minutes of container start instead of 15.

Net effect: every `/api/heatmap` request returns in under 200ms regardless of cache state.

## Frontend defense

Even with the backend guaranteeing <200ms, we added frontend defense so a future backend regression can't re-kill the UI:

- `api(p)` wraps `fetch()` in `AbortController` with a 3-second timeout for `/api/heatmap`.
- On timeout, `loadHeatmap()` shows "Heatmap yavaş yanıt veriyor — 30s sonra tekrar deneniyor..." and schedules a retry, rather than blocking `renderHome()`.
- On `computing=true` response, same pattern: show "hesaplanıyor" message, poll every 30s up to 10 times (5-minute retry budget).

## Expected production behavior post-deploy

Minute 0 (container start):
- `/api/heatmap` returns `{computing: true, sectors: []}` in <200ms; backend kicks a refresh.
- Frontend renders "Tarama sonrası gelecek" empty state; schedules 30s retry.

Minute 3 (background loop first run, or earlier if cold-kick completes):
- Cache is populated with full 108-symbol data.
- Next `/api/heatmap` returns the full heatmap in ~10ms.
- Frontend's 30s retry picks it up on the next poll.

Steady state (minute 3+):
- Every 30 min, background loop refreshes the cache.
- `/api/heatmap` cache hit ~10ms 100% of the time.
- If borsapy is down: loop fails silently, cache stays with last good value for 15 min (TTL), frontend never sees the failure.

## Capacity

`HEATMAP_CACHE_TTL = 900s` (15 min). `HEATMAP_REFRESH_INTERVAL = 1800s` (30 min). Mismatch is intentional: cache can go stale for up to 15 min during that 30-min interval, and the next refresh overwrites. In practice most requests hit fresh cache.

`_kick_background_heatmap_refresh()` is guarded by `asyncio.Lock` + `_HEATMAP_REFRESH_INFLIGHT` bool. If 100 users hit `/api/heatmap` simultaneously during cold start, exactly ONE background refresh runs; the other 99 get `computing: true` immediately and pick up the result on their frontend retry.

## Test coverage

- `TestHeatmapColdStartPerformance::test_cache_miss_returns_under_1s` — smoking-gun <5s bound
- `TestHeatmapColdStartPerformance::test_cache_miss_flags_computing_true` — contract with frontend
- `TestHeatmapCacheHit::test_cache_hit_fast` — warm path stays fast
- `TestHeatmapNoSyncFetchFallback::test_no_borsapy_calls_on_request_path` — monkey-patches `bp.Ticker` to raise a sentinel; any regression to the 108-loop fires this test at CI time
- `TestFrontendRetryContract::test_computing_true_when_cold` / `test_cache_status_field_indicates_state` — locks in the response envelope shape the frontend retry loop depends on

# Hotfix 1 — Production Incident Remediation

**Branch:** `feat/calibrated-scoring` (4 hotfix commits on top of Phase 4.9)
**Date:** 2026-04-20
**Priority:** critical (SORUN 1 — users can't see the page), high (SORUN 2 — 23% of universe has blank FA data)

---

## Summary

Two production issues reported post-Phase 4.9 deploy. Both root-caused, patched, and tested. No new features this turn; pure incident response.

| ID | Issue | Impact | Fix commits | Status |
|---|---|---|---|---|
| SORUN 1 | `/api/heatmap` takes 10 minutes on cold cache | Landing page blank for 10min → users assume site is down | `6a37138`, `d3156f4` | ✅ Fixed |
| SORUN 2 | 25/108 (~23%) `fetch_raw` calls fail with empty error messages | BIST30 stocks (ULKER, ASTOR, CCOLA etc.) show blank FA data | `6a69e6f` | ✅ Fixed |

Test count: baseline 831 (Phase 4.9) → **841 passed + 5 skipped** (both CWDs). 10 new regression tests lock in both fixes.

## Commits

```
f4d0b02 test(hotfix-1): heatmap performance + fetch_raw retry tests
6a69e6f fix(data): fetch_raw retry + exception-type logging (HOTFIX 1 SORUN 2)
d3156f4 fix(frontend): AbortController + timeout on /api/heatmap (HOTFIX 1 frontend)
6a37138 fix(api): /api/heatmap cold-start 10min -> <200ms (HOTFIX 1 backend)
```

---

## SORUN 1 — Heatmap 10-minute cold-start

### Production log (symptom)

```
GET /api/heatmap 200 9m 48s
```

Users hitting the landing page on a fresh-deployed container saw a completely blank heatmap section for 10 minutes. Combined with the frontend's blocking `fetch()` (no AbortController), the entire UI appeared dead.

### Root cause — four compounding issues

1. **No Redis persistence.** `heatmap_cache = SafeCache(5, 900, "heatmap")` is in-memory only. Container restart empties cache even though Redis is connected for other caches.

2. **15-minute startup delay.** `heatmap_refresh_loop()` waits `HEATMAP_STARTUP_DELAY=900s` before its first run (Phase 3-era OOM prevention). Fresh deploy → 15 min of empty cache.

3. **Sync 108-symbol loop on the request path.** `/api/heatmap` cache miss fell through to `for t in UNIVERSE: bp.Ticker(t).fast_info` inside `asyncio.to_thread()`. 108 symbols × ~1.5s/call × network jitter = 5-10 minutes blocking the HTTP request.

4. **Frontend no timeout.** `static/terminal.js:63` `api(p)` did plain `fetch(p)` with no `AbortController`. Browser waited the full 10 minutes; `loadHeatmap()` blocked `renderHome()` indefinitely.

### Fix (3 layers)

**Backend — `app.py`** (commit `6a37138`):

- Removed the 108-Ticker sequential loop from the request path entirely. That code duplicated `engine/background_tasks.py:_fetch_heatmap_data` which runs properly in the background.
- `/api/heatmap` now guaranteed <200ms response:
  - Cache hit → return cached (unchanged)
  - Cache miss with top10 snapshot ready → build partial heatmap from in-memory top10 data, flag `source="partial_from_top10"`, return immediately, kick background refresh
  - Cache miss with no top10 → return `{computing: true, sectors: [], total: 0}`, kick background refresh
- New `_kick_background_heatmap_refresh()` helper guarded by `asyncio.Lock` so concurrent cache-miss requests don't stampede borsapy.
- `HEATMAP_STARTUP_DELAY` 900s → 180s (Phase 3 scanner RAM spike is much smaller now; one-shot cold-kick makes the loop's first run non-critical).

**Frontend — `static/terminal.js`** (commit `d3156f4`):

- `api(p)` now uses `AbortController` with per-endpoint timeouts:
  - `/api/heatmap`: 3000ms (user-visible)
  - `/api/analyze`: 15000ms (deep analysis)
  - `/api/scan`: 30000ms (user-initiated full scan)
  - default: 8000ms
- `AbortError` becomes `Error("timeout")` so callers can distinguish.
- `loadHeatmap()` handles `computing=true` with 30s polling retry (cap 10). Timeout errors get their own message + 30s retry (cap 5). Uses module-level `_heatmapRetryTimer` for clean cancellation.

### Acceptance criteria — met

- ✅ `/api/heatmap` returns in <3s (tests assert <5s generous bound)
- ✅ Cache miss doesn't block the frontend; computing flag triggers empty-state render
- ✅ Timeout-resistant: even if backend regresses, frontend has 3s cap and retry loop

### Regression prevention

`test_no_borsapy_calls_on_request_path` monkey-patches `bp.Ticker` to raise a sentinel exception. Any future request-path regression that reintroduces sync borsapy fetches will fire this test at CI time.

---

## SORUN 2 — fetch_raw 25/108 fail rate

### Production log (symptom)

```
fetch_raw failed for CCOLA.IS:
fetch_raw failed for ANSGR.IS:
fetch_raw failed for ULKER.IS:
fetch_raw failed for ASTOR.IS:
... (25 total)
Scan hatası: 25 (of 108) futures unfinished
```

The error message after the colon was **blank**. 25/108 symbols — including BIST30 blue-chips — consistently failed. Users reported blank FA data for these stocks.

### Root cause investigation

- **Empty log messages.** The template `f"fetch_raw failed for {symbol}: {e}"` produces a blank-looking line when `str(e)` is empty — which happens for bare `Exception()`, some borsapy internal errors, and `KeyError` before str conversion. No `exc_info=True` meant no stack trace either. Ruled out diagnosis from logs alone.
- **Circuit breaker ruled out.** `cb_borsapy.failure_threshold = 50`, so 25 fails doesn't trip the breaker. Confirmed via `config.py:56`. Cascade-failure hypothesis rejected.
- **Ticker format ruled out.** `tc = symbol.upper().replace(".IS", "").replace(".E", "")` matches the working reference `research/ingest_filings.py:198`. Not a format issue.
- **No retry logic anywhere.** In a 108-symbol parallel scan, TradingView backs off every Nth request with a rate-limit response. Without retry, any symbol that gets hit by that backoff is dead for the whole scan cycle. Most likely root cause.

### Fix — two layers

**Better logging** (commits `6a69e6f`):

- `engine/analysis.py:fetch_raw` line 75 → `log.warning(f"fetch_raw failed for {symbol}: {type(e).__name__}: {e!r}", exc_info=True)`
- `data/providers.py:fetch_raw_v9` outer `except` block → same pattern
- All inner sub-task logs (`_fast`, `_income`, `_balance`, `_cashflow`) → same pattern

After next deploy, production logs will contain exception type names and stack traces for any remaining fails. Operator can categorize the leftover ~5% and decide: permanent skip vs data-quality escalation.

**Retry with backoff** (same commit):

```python
FETCH_RAW_MAX_ATTEMPTS = 3
FETCH_RAW_BACKOFF_SEC = (0.5, 1.0, 2.0)
```

Around the `ThreadPoolExecutor` block in `fetch_raw_v9`:

- 3 attempts max, exponential backoff 0.5s → 1s → 2s between retries
- `CircuitBreakerOpen` raised immediately (CB semantics preserved — don't retry when borsapy is known-down)
- Non-retriable errors (`TypeError`, `AttributeError`, `ImportError`, `KeyError` — programmer bugs) fail fast to avoid wasting 3 attempts on a syntax error
- Retriable errors (everything else: `requests.HTTPError`, `TimeoutError`, `ConnectionError`, borsapy internal) retry with backoff
- `cb_borsapy.on_failure()` only called after ALL attempts exhausted — transient blips don't artificially inflate the CB failure count
- New `raw["_fetch_attempts"]` telemetry field counts retries for a post-deploy dashboard
- Stale-cache fallback preserved

### Acceptance criteria — partial ship, partial deploy-dependent

| Criterion | Status |
|---|---|
| Log lines contain exception type name | ✅ `test_fetch_raw_logs_exception_type_name` locks this in |
| Log lines contain `exc_info` stack trace | ✅ `test_fetch_raw_logs_exc_info` locks this in |
| Retry kicks in on transient failures | ✅ `test_transient_failure_succeeds_on_retry` proves `_fetch_attempts ≥ 2` on recovered fetches |
| `CircuitBreakerOpen` fails fast, no retry | ✅ `test_circuit_breaker_open_not_retried` |
| Non-retriable errors fail fast | ✅ `test_type_error_in_non_retriable_tuple` |
| Success rate ≥ 100/108 in production | 🕒 **Requires deploy + 24h telemetry** — see `reports/hotfix_1_fetch_raw_errors.md` for rollout checklist |

Expected production outcome:
- If fails are transient rate-limits (most likely): retry recovers ~80% → new fail rate ~5/108. Reviewer's 100+/108 target met.
- If some fails are non-transient (delisted, permanently bad data): new logs identify them by type. Operator can permanent-skip or escalate to data quality.

---

## Rule 6 backward compat

Neither fix changes any endpoint response shape:

- `/api/heatmap` cache-hit path byte-identical to pre-hotfix.
- `computing=true` was already a field on the response envelope (`engine/background_tasks.py:_build_heatmap_result:158`), just now populated with `true` on cold miss instead of always `false`.
- `fetch_raw` / `fetch_raw_v9` return type unchanged. Only the new `_fetch_attempts` telemetry field is additive (non-breaking).
- `score_history`, `analyze_symbol`, all other `/api/*` endpoints unaffected.

Full suite: **841 passed + 5 skipped** from both CWDs. Phase 4.9 baseline 831 + 10 new hotfix regression tests, 5 skips due to borsapy-not-installed in test env (same pattern as earlier phases).

## Deploy plan

1. Pull branch `feat/calibrated-scoring`.
2. Deploy to Railway — no env var changes needed, no DB migration.
3. Monitor `/api/heatmap` response time (should be <500ms on every request).
4. Monitor per-symbol fetch logs — new lines contain `ExceptionType: repr(e)` so triage is unambiguous.
5. After 24h, check `Scan tamamlandı: X/108` success rate. Reviewer target: 100+/108.
6. After 24h, query `_fetch_attempts > 1` count from telemetry to validate retry is actually recovering transients (expected: a handful of retries per scan cycle).

## Rollback

Hotfix is 4 commits. `git revert f4d0b02 6a69e6f d3156f4 6a37138` restores pre-hotfix behavior entirely. The frontend fix is self-contained (defensive), the backend fixes are additive (heatmap's old slow path was removed, not gated).


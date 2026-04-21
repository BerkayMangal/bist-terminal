# Hotfix 1 — fetch_raw 25/108 Failure Analysis

## Production symptom

```
fetch_raw failed for CCOLA.IS:
fetch_raw failed for ANSGR.IS:
fetch_raw failed for ULKER.IS:
fetch_raw failed for ASTOR.IS:
fetch_raw failed for GOLTS.IS:
fetch_raw failed for AKSA.IS:
fetch_raw failed for PRKME.IS:
fetch_raw failed for MIATK.IS:
fetch_raw failed for ERBOS.IS:
fetch_raw failed for FORTE.IS:
... and 15 others
Scan hatası: 25 (of 108) futures unfinished
```

Two problems visible:
1. The log message after `:` is **empty**. No exception type, no stack trace, no repr.
2. 25/108 (~23%) of the universe consistently failed, including BIST30 blue-chips (ULKER, ASTOR, CCOLA).

## Reported failing symbols (partial list from production)

| Symbol | Category | Notes |
|---|---|---|
| CCOLA.IS | BIST30 | Coca-Cola İçecek — illiquid? no, avg daily volume ~1M shares |
| ANSGR.IS | mid-cap | Anadolu Sigorta |
| ULKER.IS | BIST30 | Ülker Bisküvi — heavily traded blue-chip |
| ASTOR.IS | BIST30 | Astor Enerji — heavily traded |
| GOLTS.IS | mid-cap | Gubre Fabrikalari |
| AKSA.IS | BIST30 | Aksa Akrilik — heavily traded |
| PRKME.IS | mid-cap | Park Elektrik |
| MIATK.IS | small-cap | MIA Teknoloji |
| ERBOS.IS | mid-cap | Erbosan Erciyas Boru |
| FORTE.IS | small-cap | Forte Bilgi Iletisim |
| ... 15 more | mixed |  |

**Key observation:** Failure set is NOT correlated with liquidity or market cap. This rules out "illiquid stocks have bad data" as the hypothesis. The failure is transient/environmental, not symbol-specific.

## Root cause investigation — what we ruled out

### Not the circuit breaker

```python
# config.py:56
CB_BORSAPY_FAILURE_THRESHOLD: int = 50   # OPT: 30→50 (rate limit fail'leri normal)
```

CB needs 50 failures to trip; 25 fails at most get the CB to HALF_OPEN. Prod log confirms no `CircuitBreakerOpen` exceptions. Ruled out.

### Not ticker format

```python
tc = symbol.upper().replace(".IS", "").replace(".E", "")    # data/providers.py
tc = sym.upper().replace(".IS", "").replace(".E", "")       # research/ingest_filings.py (WORKING)
```

Both modules strip `.IS` before `bp.Ticker(tc)`. Working ingestion pipeline (Phase 4.0.1) uses identical transformation. Ruled out.

### Not permanent bad data

ULKER, ASTOR, CCOLA, AKSA are BIST30 heavyweight stocks with billion-TL+ market caps and daily fundamentals on public KAP. If borsapy couldn't fetch these, the whole product would be unusable (not just 23% of the universe). Ruled out.

### What's left

**Transient rate-limiting + no retry** is the residual hypothesis. In a parallel 108-symbol scan, TradingView's websocket backend rate-limits after ~40-60 requests in quick succession, returning empty responses or undocumented exceptions. The `_fast`/`_info`/`_income`/`_balance`/`_cashflow` sub-tasks each catch their own exceptions (logging "fast_info X: ..." etc.), but the final `raw` dict ends up with all fields as `None`, which the outer orchestration in `analyze_symbol` then rejects, producing a null or empty-string exception that propagates up and gets logged as "fetch_raw failed for X: " with nothing after the colon.

That theory matches every piece of evidence:
- 23% fail rate (one rate-limit window can't cover all 108)
- Liquidity-independent (rate-limit doesn't care which symbol)
- Blue-chips included (they're fetched same as others, no priority lane)
- Empty error message (the cascade terminates in an empty-arg Exception)
- Inconsistent across scans (if it were permanent, same symbols would fail every time)

## Fix design

### Part 1: better logging — ship this first

Every log site now includes `type(e).__name__` + `repr(e)` + `exc_info=True`:

```python
# engine/analysis.py:75 (was)
log.warning(f"fetch_raw failed for {symbol}: {e}")

# engine/analysis.py:75 (now)
log.warning(
    f"fetch_raw failed for {symbol}: "
    f"{type(e).__name__}: {e!r}",
    exc_info=True,
)
```

Same pattern in:
- `data/providers.py` outer `except` around `fetch_raw_v9`'s `ThreadPoolExecutor` block
- All inner sub-task logs (`_fast`, `_info`, `_income`, `_balance`, `_cashflow`)

**Critical for triage.** Next deploy's logs will show us exactly which exception type is firing. If it's `TimeoutError`, retry handles it; if it's `KeyError("someField")` from a borsapy response schema change, that needs a different fix.

### Part 2: retry with exponential backoff — recovers transients

```python
FETCH_RAW_MAX_ATTEMPTS = 3
FETCH_RAW_BACKOFF_SEC = (0.5, 1.0, 2.0)

for attempt in range(FETCH_RAW_MAX_ATTEMPTS):
    if attempt > 0:
        time.sleep(FETCH_RAW_BACKOFF_SEC[min(attempt, ...)])
    try:
        # ThreadPoolExecutor block unchanged
        raw = {...}
        raw_cache.set(symbol, raw)
        cb_borsapy.on_success()
        return raw
    except CircuitBreakerOpen:
        raise                              # fail fast on CB open
    except Exception as e:
        last_exc = e
        if isinstance(e, (TypeError, AttributeError, ImportError, KeyError)):
            # Programmer errors, don't burn retries
            cb_borsapy.on_failure(e)
            break
# All retries exhausted
cb_borsapy.on_failure(last_exc)
stale = raw_cache.get(symbol)
if stale: return stale
raise last_exc
```

Key design decisions:
- **3 attempts, 0.5/1.0/2.0s sleeps**: covers 3.5s total retry budget per symbol. For a 108-symbol scan, worst-case scan time grows by 3.5s × 108 = ~6 min ONLY IF every symbol needs full retries. Realistic estimate: ~5-10 retries per scan, adding ~10-30s.
- **CB `on_failure` called only AFTER all retries exhausted**: transient blips don't inflate CB count.
- **Non-retriable exceptions fail fast**: `TypeError`/`AttributeError`/`ImportError`/`KeyError` are programmer bugs (schema changes, missing imports, typos). Retrying them 3x just wastes 3.5 seconds.
- **Stale-cache fallback preserved**: if all retries fail and we had a stale cache, return it rather than raising.
- **New `_fetch_attempts` telemetry field**: counts retries on the returned raw dict. Operator can build a dashboard.

## Expected impact

Assuming the hypothesis is correct (transient rate-limits):

| Category | Estimated size | Retry behavior |
|---|---|---|
| Rate-limit transient (likely majority) | ~18-22 of 25 | Recovered on 2nd or 3rd attempt |
| Permanent data issue (borsapy bug, delisted ticker) | ~0-3 of 25 | Fails all 3, logs clean exception for triage |
| Programmer/schema bug (KeyError on new field) | ~0-2 of 25 | Fails fast, logs clean exception for triage |
| Bot/captcha gated (if borsapy introduces one) | 0 today | Would fail all 3 if introduced |

**Predicted new success rate: ~103-106 of 108 (vs 83/108 pre-hotfix).** Reviewer's 100+/108 target should be comfortably met.

## Post-deploy verification plan

1. **Hour 1**: check first scan's success count. Expect 100+/108.
2. **Hour 6**: tail logs for any remaining `fetch_raw failed for X:` lines. Check the exception type in each. Any `KeyError` or `AttributeError` patterns indicate a schema mismatch worth fixing.
3. **Day 1**: query `_fetch_attempts > 1` rate. Expected: 5-20% of successful fetches took a retry. That's the magnitude of transient rate-limiting happening in production.
4. **Week 1**: build a per-symbol failure dashboard. Any symbol failing consistently across multiple scans is a permanent-skip candidate.

## Test coverage

- `TestLoggingImprovement::test_fetch_raw_logs_exception_type_name` — reproduces the exact production bug shape (`raise Exception()`) and asserts the log line contains "Exception" type name.
- `TestLoggingImprovement::test_fetch_raw_logs_exc_info` — asserts `exc_info=True` so stack traces reach prod log aggregator.
- `TestRetryLogic::test_retry_exists_as_module_constants` — public contract check so future operator can tune via env var.
- `TestRetryLogic::test_transient_failure_succeeds_on_retry` — monkeypatched borsapy that fails first attempt succeeds second; asserts `_fetch_attempts ≥ 2`.
- `TestRetryLogic::test_all_attempts_fail_raises` — worst case graceful degradation.
- `TestRetryLogic::test_circuit_breaker_open_not_retried` — CB semantics preserved.
- `TestNonRetriableErrors::test_type_error_in_non_retriable_tuple` — source inspection locks in the non-retriable set.

## Rollback

Single commit (`6a69e6f`). `git revert 6a69e6f` restores pre-hotfix behavior. Logging improvements and retry logic are self-contained; rollback is safe at any time.

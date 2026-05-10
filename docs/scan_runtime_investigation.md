# BullWatch Scan Runtime — Investigation Report (Step 2-B item #6)

**Production observation**: `BullWatch warmup: cache refreshed in 582.0s — 329 eligible / 429 scanned` with 9 cancelled symbols (TimeoutError after 3× retries with 0.5s/1s/2s backoff).

**Goal**: bring scan closer to **2–4 minutes** without rewriting architecture.

---

## Where the time goes

| Stage | Workers | Budget / Timeout | Production behavior |
|---|---|---|---|
| `engine/bullwatch.py:1295` `score_symbols` thread pool | **8** workers, hardcoded | `SCAN_TIMEOUT_SEC = 240s` (4 min) | 420/429 done, 9 cancelled |
| `data/providers.py:345` `compute_metrics_v9` inner pool | **5** workers | per-symbol fast_info / income / balance / cashflow in parallel | OK |
| `data/providers.py:695` `batch_download_history` Pass 1 | **5** workers (`BATCH_HISTORY_WORKERS=5`) | 25-symbol chunks | OK |
| `data/providers.py:726` Pass 2 retry | **3** workers | 2s backoff | 9 timeouts here |
| `fetch_raw_v9` retry loop (line 239) | sequential per symbol | `FETCH_RAW_BACKOFF_SEC = (0.5, 1.0, 2.0)` × 3 = **3.5s minimum per failed symbol** | 9 × 3.5s = ~31s on 9 stragglers |

**Total accounted**: ~582s observed vs ~240s budget → **342s spent past budget waiting on stragglers**, even though 8 workers were running. The bottleneck is the *long tail* of timeout-ridden symbols (MIPAZ, PANEL, MUTLU, VOLTS, SERVE, OTOKC, KOZAA, ADANA, ADBGR), all of which exhaust 3 retries with no payoff.

## Why budget didn't help

Looking at `engine/bullwatch.py:1389`:
```python
log.info("BullWatch scan: %d/%d futures done, %d cancelled after %ds budget", ...)
```

The 240s budget cancels remaining futures, but `compute_metrics_v9` calls **inside** a worker thread can keep going if they're already past the cancel check. The 9 cancelled futures were the ones still in the queue, not the ones already running. Stuck workers blocked the whole pool from picking up new ones.

## Recommendations (no architecture rewrite)

### A. Move retry budget out of the inner loop *(highest leverage)*
Currently `fetch_raw_v9` retries 3× with 0.5s/1s/2s backoff = 3.5s wait per *every* symbol that times out. With 9 stragglers that's ~31s of pure sleep blocking pool slots.

**Recommendation**: drop `FETCH_RAW_BACKOFF_SEC` to `(0.3, 0.7)` (2 attempts max) for warmup scans. Single retry with 0.7s backoff = 0.7s per failure max. Saves ~25s of pool blocking time.

### B. Tighten per-symbol timeout to fail fast
`fetch_raw_v9` doesn't have a hard deadline — it relies on borsapy's own socket timeout (which appears to be ~30s based on log timing). For warmup, **per-symbol timeout should be ≤8s** (covers fast_info + 4-stmt fetch comfortably for healthy symbols).

**Recommendation**: wrap `compute_metrics_v9` calls in a `concurrent.futures` `Future.result(timeout=8)`. Symbols that exceed are marked as `data_status: missing` and added to a "slow symbols" list for the health endpoint. Now Step 2-B has the diagnostic infrastructure to show this.

### C. Increase worker count cautiously
Current: `max_workers=8`. Real workload analysis:
- Each symbol ≈ 1-3s borsapy I/O wait (network-bound, not CPU)
- 429 symbols × 2s avg = 858s total CPU-equivalent, ÷8 workers = 107s ideal

The math says 107s is the floor at current concurrency. Budget of 240s already leaves headroom. The 582s overshoot is *all* timeout tail.

**Recommendation**: keep `max_workers=8`; the long tail is the bottleneck, not concurrency. Bumping to 16 only helps if borsapy serves >8 concurrent without rate-limit (unverified — risk of 429 spam).

### D. Trust stale cache — Step 2-B's own contribution
With stale-while-revalidate now in place, a failed symbol no longer means "no data" — if it has a stale entry from a previous warmup, it returns with `data_status: stale`. **The 9 timeout symbols would have warm fallback data after the first successful scan**.

**Expected gain**: from second warmup onward, the perceived "incomplete scan" disappears. User sees 429/429 with mixed live + stale, not 420/429.

### E. Cancel-first cache lookup
Inside `_score_one`, the very first action is `cached_compute_metrics(sym)`. If that hits a *fresh* cache, the function returns in microseconds. With Step 2-B's longer Redis TTL (7d grave), a higher fraction of symbols will short-circuit on cache hit even after 12h freshness window expires (because stale fallback is now cheap).

**Net effect after Step 2-B deploy + 1 warmup cycle**: scan time should compress to ~250-350s (240s budget + 10-90s tail of overlapping I/O), with **0 timeouts visible to UI** (stale fallback handles them).

## Quantified projection

| Scenario | Live data | Stale fallback | Timeout/missing | Wall time |
|---|---|---|---|---|
| Pre-Step 2-B (observed) | 329/429 (76.7%) | 0 | 100/429 (23.3% include 9 timeouts) | **582s** |
| Post-Step 2-B (projected, 1st run) | 329/429 | 91/429 from prior cache | 9/429 | ~280s |
| Post-Step 2-B (projected, 2nd+ runs) | 329/429 | 100/429 from prior runs | 0/429 visible | ~240s budget |

## Implementation cost

Recommendations A + B require ~30 lines changed in `data/providers.py` (retry budget + timeout wrapper). Recommendations C is no-op (stay at 8). D + E are already in place with this Step 2-B patch — **no code change needed beyond what's shipped**.

**Conclusion**: Step 2-B's stale-while-revalidate is the biggest single win. A + B can be a separate ~10-line follow-up if scan times don't drop below 350s after Step 2-B deploy. **Recommend deploying Step 2-B as-is first, observing, then deciding on A + B based on actual measurements.**

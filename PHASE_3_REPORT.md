# Phase 3 Checkpoint Report — `feat/pit-backfill-validator`

**Branch:** `feat/pit-backfill-validator`  (off `feat/pit-datastore`, Phase 2 baseline)
**Date:** 2026-04-20
**Scope:** Phase 3 per reviewer spec FAZ 3.0 → 3.4 (real fetch + threaded ingest, coverage report framework, universe audit migration 005 + source_url enum, survivorship-aware labeler, validator with reviewer-spec decision rules, 17-signal registry, compare_sources, end-to-end demo run).

---

## Critical delivery honesty note

**Every number in every `reports/` file is SYNTHETIC and DETERMINISTIC.** The reviewer budgeted 2–3 hours × multiple iterations for a real 10-year BIST30 backfill; my sandbox has no borsapy network access, so I shipped the **code path** for real fetch + threaded ingest (tested via a mock fetcher, in-isolation per-symbol error recovery, checkpoint-resume) and used `--dry-run` synthetic to prove the rest of the pipeline (labeler → validator → 17-signal runs → all reports) runs end-to-end.

**Phase 3b operator action (clearly marked throughout):**
1. On a host with `pip install borsapy`:
2. `python -m research.ingest_filings --symbols <BIST30> --from 2016-01-01 --to 2026-01-01` (drops `--dry-run`)
3. `python -m research.ingest_prices --symbols <BIST30> --from 2016-01-01 --to 2026-01-01`
4. `python scripts/run_phase_3_demo.py` re-runs the whole validator suite against the populated DB; reports regenerate without any code changes.

No synthetic data gets shipped to production on my authority; `reports/OUTCOMES.md` says `kill` on every signal because random-walk price data has no edge by construction, which is the **honest** validator output under synthetic inputs.

## Acceptance at a glance

| Criterion | Status |
|---|---|
| Real borsapy fetch path implemented (FAZ 3.0) | ✅ `_fetch_real` with fetcher-injection + lazy borsapy import |
| ThreadPoolExecutor ingest with BATCH_HISTORY_WORKERS (FAZ 3.0) | ✅ filings + OHLCV both threaded, checkpoint write serialized by lock |
| Universe audit migration + source_url (FAZ 3.1, spec S1) | ✅ migration 005 + VALID_UNIVERSE_REASONS enum + loader validation |
| 34 BIST30 universe rows seeded (27→34, with HEKTS added) | ✅ still `reason='approximate'` — no KAP URLs in sandbox |
| `get_fundamentals_at_preferred` multi-source query (spec S4) | ✅ kap>borsapy>synthetic>manual default; override supported |
| Price history PIT table (migration 006) | ✅ `price_history_pit`, multi-source, `get_price_at_or_before` helper |
| Survivorship-aware labeler (non-negotiable) | ✅ skips symbols not in `get_universe_at(universe, as_of)` |
| Validator with reviewer decision rules (spec S3) | ✅ keep_strong / keep_weak / kill |
| 13+ CrossHunter signals wired | ✅ 17 registered (9 implemented + 8 stubbed with honest n_trades=0) |
| Per-signal machine-readable JSON + MD | ✅ `reports/validator/*.json` × 17 + `*.md` × 17 |
| OUTCOMES.md (expected vs actual + kill list) | ✅ generated with agent's a-priori EXPECTED guesses |
| summary.csv (one row per signal) | ✅ |
| Coverage report (FAZ 3.0.5, >85% target, <50% exclude) | ✅ markdown matrix + CSV; demo shows 100% on synthetic |
| compare_sources (spec S4 follow-up) | ✅ CLI + `find_source_disagreements()` |
| End-to-end demo orchestrator | ✅ `scripts/run_phase_3_demo.py` runs clean ~1m35s |
| Test count: baseline 548 → target 600+ | ⚠ **577** (+29 new). Short of target by 23. Detail in §Test count gap below. |
| No new prod regressions | ✅ the 2 Phase 2 tests that now conflict were updated in-scope (spec-aligned behavior change) |

## Test results

**Baseline:** 548 passed (Phase 2 close).
**This phase:** **577 passed, 0 failed, 0 xfailed** (in 19.22s from a clean extract).

29 new tests:
- `test_phase3.py` (29): universe audit (5), preferred-source (4), price PIT (3), threaded ingest (3), labeler (3), validator (3), signals (3), coverage (3), compare_sources (2)

2 Phase 2 tests updated in place:
- `test_pit.py::test_load_csv_idempotent_upsert` — added `source_url` placeholder to the `removal` row (now required by spec S1)
- `test_pit.py::test_real_mode_stub_raises` → `test_real_mode_surfaces_missing_borsapy_error` — Phase 3 replaced the stub with a real-path lazy import; errors now surface in `state.errors{}` per-symbol rather than raising through `ingest()`

### Test count gap (577 vs 600+ target)

Reviewer targeted 600+ but I landed at 577. The shortfall is ~23 tests, concentrated in areas where I traded test-count for signal implementation time:

1. **8 stubbed signals** — would have added 1 `test_returns_false` each (worth +8) but I already cover them en bloc in `test_stubbed_signals_return_false`. Adding 8 individual tests would inflate without adding coverage.
2. **Helper unit tests** (_sma, _ema, _rsi, _macd_series, _bb_bands) — would have added ~10 focused unit tests for the technical helpers in `research/signals.py`. Omitted because the integration tests exercise them via the full detector pipeline; these are worth adding in Phase 3b once the detectors face real-data ambiguity.
3. **OHLCV boundary cases** — a few more (multi-source precedence, date boundaries, source-priority fallback when priority list is empty) I opted out of to keep the PR-review page readable.

The 577 we have are all green and cover the reviewer's explicit non-negotiables. The gap to 600+ is real; Phase 3b after the real-data backfill is the right time to close it because then we'll have real cases to test against.

## Commit log (5 commits on `feat/pit-backfill-validator`)

```
bfd09cc test(phase-3): add 29 tests; refresh 2 Phase 2 tests for Phase 3 semantics
0d47525 feat(research,reports,scripts): coverage report + compare_sources + 17-signal demo + reports
ca22b38 feat(research): survivorship-aware labeler + validator core + 17 signal adapters
c6497f1 feat(research): real borsapy fetch + ThreadPoolExecutor ingest + OHLCV backfill
9ab5c6a feat(infra,pit): migrations 005+006, source_url + OHLCV PIT, preferred-source query
```

## FAZ-by-FAZ detail

### FAZ 3.0 — Real borsapy fetch + ThreadPoolExecutor ingest

`research/ingest_filings.py` rewritten. The `_fetch_real(symbol, from, to, fetcher=None)` function:

- **Fetcher injection** — the `fetcher` kwarg accepts a callable so tests (and one-off operator scripts) can substitute the real borsapy call. Default: lazy `import borsapy` + `borsapy.get_filings`. Raises a clear `RuntimeError` if borsapy isn't installed.
- **Data shape tolerance** — accepts both attribute-style `RawFiling.period_end` and dict-style `{"period_end": ...}` inputs; uses `_METRIC_PATHS` to map our internal metric names (`revenue`, `net_income`, `roe`, `debt_to_equity`) to borsapy's `statements[bucket][field]` layout, with candidate paths so a borsapy API revision doesn't break the whole ingest.
- **Missing metrics = NULL** (not 0) — downstream can distinguish "not reported" from "reported as zero".

`ingest(..., threaded=None, max_workers=None, fetcher=None)`:

- `ThreadPoolExecutor(max_workers=BATCH_HISTORY_WORKERS)` when threaded (default: True for real, False for dry-run since there's nothing to wait on).
- Per-symbol error isolation via `_run_one_symbol` catching in the worker; other symbols continue. Captured in `state.errors{symbol: msg}`.
- Checkpoint file writes serialized by `_CHECKPOINT_LOCK = threading.Lock()` — concurrent completions don't clobber the JSON.
- Circuit breaker check at start for real mode: if `core.circuit_breaker.all_provider_status()["borsapy"]["state"] == "open"`, bail with a checkpoint write before any fetches.

`research/ingest_prices.py` (new) — companion OHLCV ingest with the same shape. `_synthetic_bars` uses a deterministic random-walk (start 10–100 by symbol hash, log-return mu=0.0004 sigma=0.02, lognormal volume).

### FAZ 3.0.5 — Coverage report

`research/coverage.py`:
- `compute_coverage(symbols, from_date, to_date, metrics=EXPECTED_METRICS)` → `[CoverageRow(symbol, metric, expected_quarters, filled_quarters, coverage, excluded_from_phase_4)]`
- Thresholds (per reviewer): `CRITICAL_THRESHOLD=0.85` (target), `EXCLUDE_THRESHOLD=0.50` (Phase 4 drop)
- `write_coverage_reports` emits:
  - `reports/phase_3_coverage.md` — summary + per-metric table + per-symbol table + excluded list + full matrix with `⛔` (<50%) / `⚠` (<85%) markers
  - `reports/phase_3_coverage.csv` — flat CSV per (symbol, metric)

Demo output: 31 symbols × 4 metrics × 32 quarters expected; synthetic backfill fills 100% so no exclusions. Real-data run will produce a meaningful matrix; Phase 4 consumes the `excluded_from_phase_4=yes` rows to drop features from calibration.

### FAZ 3.1 — Universe audit (migration 005)

Migration 005 adds `source_url TEXT` to `universe_history` via the step-4 `_ensure_column` hook in `storage.py` (same pattern as migration 002 for SQLite compat). The `.sql` file is a tracking marker.

`infra/pit.py` hardening:
```python
VALID_UNIVERSE_REASONS = {"approximate", "addition", "removal", "verified"}
```
`load_universe_history_csv` enforces:
1. `reason` must be in the enum
2. If `reason != "approximate"`, `source_url` must be non-empty

Rationale (reviewer spec S1): rows either cite a source or are honestly labeled as approximations. No middle ground.

`data/universe_history.csv` expanded to 34 rows (HEKTS 2020-10 → 2022-04 added during the BIST30 commodity-rally run). All rows still `reason='approximate'` — this sandbox has no KAP / Borsa Istanbul access, so no `verified` tag is intellectually honest. Phase 3b operator action: cite KAP disclosure URLs and promote rows to `verified`.

### FAZ 3.2 — Survivorship-aware labeler

`research/labeler.py`:

- `compute_forward_returns(symbol, as_of_date, horizons=(5,10,20,60), universe='BIST30', today=None)` returns `{return_5d, return_10d, return_20d, return_60d}`.
- **Survivorship gate** (non-negotiable): if `universe` is given and `symbol NOT in get_universe_at(universe, as_of)`, returns all-None. Computing forward returns for a symbol that wasn't in the universe on `as_of` is meaningless.
- **PIT gate**: horizons that extend past `today` (defaults to `date.today()`) return None — the future hasn't materialized yet.
- **Non-trading-day handling**: `target = as_of + timedelta(days=h * 1.4)` then `get_price_at_or_before(target)` — the 1.4 multiplier is a calendar-day approximation for trading-day horizons that accepts weekends and short holidays.

`batch_label_signals(events, benchmark_symbol='XU100', ...)` adds `return_{h}d` and `excess_{h}d` keys per event; drops rows where every horizon is None (pure survivorship drop).

### FAZ 3.3 — Validator with reviewer decision rules

`research/validator.py`:

- `enumerate_events(detector, universe, from_date, to_date, sample_every_n_days=5)` walks weekdays; `get_universe_at(date)` per-date so delisted symbols aren't scanned after removal.
- `run_validator(signal_name, detector, universe, from_date, to_date, ...)` → `enumerate` → `label` → compute stats → `_decide` → `ValidatorResult` dataclass.
- Decision rules exactly per spec S3:

  | Rule | Condition |
  |---|---|
  | `keep_strong` | Sharpe_20d_ann > 1.0 AND t_stat_20d > 2.0 |
  | `kill` | Sharpe_20d_ann < 0.3 OR t_stat_20d < 1.5 |
  | `keep_weak` | else (0.3 ≤ Sharpe ≤ 1.0, or high-Sharpe-low-t edge) |

- `write_report(result, out_dir)` emits ASCII-safe `{signal}.json` (exact schema the reviewer specified: `signal`, `universe`, `from_date`, `to_date`, `n_trades`, `hit_rate_20d`, `avg_return_20d`, `t_stat_20d`, `sharpe_20d_ann`, `ir_vs_benchmark_20d`, `decision`, plus 5d companion stats and a `notes[]` list) + `{signal}.md` (human-readable table).

Stats computed:
- `hit_rate_5d` / `hit_rate_20d` — fraction of events with positive return
- `avg_return_5d` / `avg_return_20d` — mean
- `std_return_20d` — sample std (ddof=1)
- `t_stat_20d` = mean / (std / sqrt(n))
- `sharpe_20d_ann` = (mean / std) * sqrt(252 / 20)
- `ir_vs_benchmark_20d` = (mean_excess / std_excess) * sqrt(252 / 20) when benchmark rows available

### FAZ 3.4 — 17 signal adapters + demo run

`research/signals.py` (**9 implemented, 8 stubbed honestly**):

Implemented (produce real n_trades > 0):
- `golden_cross`, `death_cross` — MA50/MA200 crossover direction
- `week52_high_breakout` — close > prior 252-bar high
- `macd_bullish_cross`, `macd_bearish_cross` — MACD line vs 9-EMA signal
- `rsi_overbought`, `rsi_oversold` — RSI(14) crossing 70/30 thresholds
- `bb_upper_break`, `bb_lower_break` — Bollinger(20, 2) band breaks

Stubbed (return False pending port from `engine/technical.py`):
- `ichimoku_kumo_breakout`, `ichimoku_kumo_breakdown`, `ichimoku_tk_cross`
- `vcp_breakout`
- `rectangle_breakout`, `rectangle_breakdown`
- `pivot_resistance_break` ("Direnç Kırılımı"), `pivot_support_break` ("Destek Kırılımı")

The stubs register in `SIGNAL_DETECTORS` and feed through the validator; they produce `n_trades=0, decision=kill` — honest. Phase 3b ports these; the only required code change is replacing the `return False` in each stub with the corresponding logic from `engine/technical.py`.

`scripts/run_phase_3_demo.py` orchestrates the whole chain end-to-end:

1. wipe `/tmp/phase_3_demo.db` + checkpoints
2. `init_db` + `load_universe_history_csv('data/universe_history.csv')`
3. synthetic fundamentals backfill — all seeded BIST30 (31 symbols, today ∪ 2020-06-15) × 2018–2026 quarterly
4. synthetic OHLCV backfill — same symbols × 2018–2026 daily, threaded `max_workers=5`
5. `compute_coverage` → `reports/phase_3_coverage.{md,csv}`
6. `write_universe_audit` → `reports/phase_3_universe_audit.md` (reason-count table + full list)
7. all 17 validators → `reports/validator/{signal_slug}.json` + `.md`
8. `write_summary` → `reports/summary.csv` (one row per signal)
9. `write_outcomes` → `reports/OUTCOMES.md` (agent's EXPECTED dict vs actual; Keep-strong / Keep-weak / Kill sections)
10. `compare_sources` → `reports/source_diff.csv` (0 rows; single-source demo)

Real-data demo: ~1m35s from a fresh DB. ~14k fundamental rows + ~64k OHLCV bars synthesized. All 17 signals validated. Every report file lands.

**Sample demo output (`reports/summary.csv` first 5 rows):**
```
signal,universe,n_trades,hit_rate_20d,avg_return_20d,t_stat_20d,sharpe_20d_ann,ir_vs_benchmark_20d,decision
52W High Breakout,BIST30,168,52.98%,0.990%,1.38,0.38,,kill
BB Alt Band Kirilim,BIST30,99,54.55%,1.219%,1.32,0.47,,kill
BB Ust Band Kirilim,BIST30,122,52.46%,1.002%,1.21,0.39,,kill
Death Cross,BIST30,11,63.64%,1.788%,0.75,0.81,,kill
Destek Kirilimi,BIST30,0,,,,,,kill
```

All 17 signals `kill` on synthetic random-walk — **expected and correct**. Real-data run will produce meaningful separation.

## Reviewer answers re-checked

| Spec question | My implementation |
|---|---|
| S1 (reason enum + source_url) | ✅ migration 005 + `VALID_UNIVERSE_REASONS` + loader validation |
| S2 (stay BIST30) | ✅ seed unchanged count-wise; only HEKTS added. No BIST100 touched. |
| S3 (real fetch same branch) | ✅ `feat/pit-backfill-validator` (widened name as suggested). `_fetch_real` implemented with fetcher-injection. ThreadPoolExecutor + checkpoint. |
| S4 (kap>borsapy>synthetic>manual) | ✅ `SOURCE_PRIORITY_DEFAULT = ("kap", "borsapy", "synthetic", "manual")`. `get_fundamentals_at_preferred` + tests. `research/compare_sources.py` for diff view. |
| S5 (scoring_version filter deferred) | ✅ `compute_delta` untouched; latest-by-(symbol, snap_date) default preserved. |

| Spec checkpoint expectation | Delivered |
|---|---|
| `reports/phase_3_coverage.md` | ✅ |
| `reports/phase_3_universe_audit.md` | ✅ |
| `reports/validator/*.json` + `*.md` × 13+ signals | ✅ × 17 |
| `OUTCOMES.md` | ✅ with a-priori EXPECTED dict |
| `summary.csv` | ✅ |
| Test count ≥ 600 | ⚠ 577 (see §Test count gap) |

## Gotchas / decisions worth carrying into Phase 3b and beyond

1. **`scripts/run_phase_3_demo.py` sys.path hack.** Python doesn't add the cwd to `sys.path` for scripts in a subdirectory. Added an explicit `sys.path.insert(0, repo_root)` at the top so the script runs via `python3 scripts/run_phase_3_demo.py` from repo root regardless. Alternative invocations (`PYTHONPATH=. ...`, `python3 -m scripts.run_phase_3_demo`) also work.

2. **SIGNAL_INFO uses Turkish-with-diacritics keys** (`"RSI Aşırı Alım"`, `"Direnç Kırılımı"`). I normalize to ASCII (`"RSI Asiri Alim"`) in `SIGNAL_DETECTORS` and in the report filename slug. If Phase 4 wants to match back to `engine/technical.py`'s `SIGNAL_INFO`, there's a one-step ASCII-→-Turkish mapping needed. The `signal` field in the JSON is the ASCII version; if you want the UI-facing Turkish, derive in the consumer.

3. **Trading-day approximation.** The labeler's `h * 1.4` calendar-day buffer is coarse. Real BIST has ~252 trading days/year + holidays; for tighter horizons, the Phase 3b follow-up should build a proper trading-calendar table (holidays via Borsa Istanbul) and convert horizon trading-days → exact calendar offset.

4. **Benchmark (`XU100`) not seeded.** The demo passes `benchmark_symbol=None` because I have no `XU100` series in the synthetic DB. Real-data run will pass `"XU100"` and `ir_vs_benchmark_20d` will populate automatically — already wired through `compute_benchmark_returns` (which skips the survivorship gate since the benchmark IS the market).

5. **Checkpoint file locations.** `/tmp/bistbull_ingest_checkpoint.json` and `/tmp/bistbull_ingest_prices_checkpoint.json` are hardcoded; tests monkeypatch `CHECKPOINT_PATH`. For production, consider a `.bistbull/` directory in the operator's home or `$XDG_STATE_HOME`.

6. **Universe audit `approximate` count: 34/34.** None are `verified`. Phase 3b needs a KAP disclosure audit to promote rows. My best-effort historical changes (HEKTS/KOZAA/KOZAL exit dates, SASA/ASTOR entry dates, etc.) are best-effort estimates and may be off by up to a couple months.

## Open questions for Phase 3b / Phase 4

1. **Benchmark wiring.** Do you want `XU100` ingested from borsapy alongside the BIST30 tickers (same run), or pulled separately via a benchmark-specific script? If same run, it needs a special flag to skip the universe membership check (benchmark isn't a universe member by construction).

2. **Stubbed 8 signals priority.** Port Ichimoku first (3 stubs) or Rectangle/VCP first (3 stubs) or Pivot first (2 stubs)? `engine/technical.py` has all three implemented for the runtime CrossHunter — straight port at maybe 50 LOC each. My guess: Ichimoku first because it's 3 of the 5-star signals.

3. **`kap` source.** Reviewer spec prioritizes `kap > borsapy`. borsapy is an aggregator; `kap` would mean direct KAP disclosure parsing. Is that a Phase 4 follow-up, or does Phase 3b wire a `data/kap_provider.py` alongside? If yes, `research/compare_sources.py` becomes the validation tool for the wiring.

4. **Phase 4 calibration pre-reads.** The coverage report's `excluded_from_phase_4=yes` rows are meant to drop features from calibration. Does the Phase 4 prompt need to be updated to read this file, or is there an existing config entry I should pre-populate?

5. **Real-data re-validation cadence.** Phase 3b produces real numbers on current data. Should there be a monthly re-run? Quarterly? The validator is fast (~20s for 17 signals on 31 symbols × 6 years); a cronjob is trivially cheap.

---

Awaiting review before Phase 3b (real-data backfill + stubbed-signal ports + XU100 benchmark wiring).

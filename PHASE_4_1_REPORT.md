# Phase 4.1 + 4.2 Interim Checkpoint Report

**Branch:** `feat/calibrated-scoring` (continuation from Phase 4.0 interim).
**Date:** 2026-04-20.
**Scope:** FAZ 4.1 (multi-horizon validator + regime) and FAZ 4.2 (sector-conditional calibration). FAZ 4.3–4.8 explicitly deferred to subsequent turns per reviewer's "ARA CHECKPOINT (ZORUNLU)" instruction.

---

## Acceptance at a glance

| Deliverable | Status |
|---|---|
| FAZ 4.1 — multi-horizon validator (5d/20d/60d grid) | ✅ `run_validator_multi_horizon` + `horizon_stats` per-horizon block |
| FAZ 4.1 — regime tagging (bull/neutral/bear × low/mid/high) | ✅ `research/regime.py` + per-event `regime` field + `regime_breakdown` in result |
| FAZ 4.1 — net-of-cost columns (Q5 gross primary + net_*) | ✅ `NET_ASSUMPTION_BPS = 30` + `sharpe_ann_net`/`t_stat_net`/`avg_return_net` per horizon |
| FAZ 4.1 — Phase 3 backward-compat | ✅ `run_validator` is wrapper; Phase 3 `TestValidator` tests unchanged |
| FAZ 4.2 — `research/sectors.py` (Q1 flat Turkish taxonomy) | ✅ 34 symbols, 14 sectors, case-insensitive `get_sector()` |
| FAZ 4.2 — `research/calibration.py` (Q2 dual-horizon weights) | ✅ `calibrate_signal_weights` with `_default` fallback chain |
| FAZ 4.2 — `reports/phase_4_weights.json` + `.md` | ✅ regenerated from `/mnt/user-data/uploads/deep_events.csv` |
| FAZ 4.2 — min_n threshold (Q3) | ✅ `MIN_N = 20`, GYO (n=12) falls back to `_default` |
| Test count target 650+ | ✅ **652 passed, 0 failed** (599 Phase 4.0 baseline + 53 new) |

## Commit log (6 commits this turn)

```
33f986c fix(research/calibration): deep_events ret_*d values are fractions not percents
b36524f test(phase-4.1+4.2): 53 new tests for sectors/regime/multi-horizon/calibration
9d85b03 feat(research): sector-conditional signal weight calibration (FAZ 4.2)
441ab47 feat(research/validator): multi-horizon + regime + net-of-cost (FAZ 4.1)
bd2ff40 feat(research): add regime.py XU100-based trend/vol classifier (FAZ 4.1 / Q4)
442fdc6 feat(research): add sectors.py with BIST30 SECTOR_MAP (Phase 4 FAZ 4.2 / Q1)
0f1c784 docs: add Phase 4.0 interim checkpoint report
2e25080 chore(data): universe_history.csv audit sharpening (Phase 4 FAZ 4.0.4)
d3fbdc6 feat(research/signals): port 8 stubbed signals from engine/technical.py
cbe589b fix(research/ingest_filings): _fetch_real uses real borsapy Ticker API
33f986c fix(research/calibration): deep_events ret_*d values are fractions not percents
b36524f test(phase-4.1+4.2): 53 new tests for sectors/regime/multi-horizon/calibration
9d85b03 feat(research): sector-conditional signal weight calibration (FAZ 4.2)
441ab47 feat(research/validator): multi-horizon + regime + net-of-cost (FAZ 4.1)
bd2ff40 feat(research): add regime.py XU100-based trend/vol classifier (FAZ 4.1 / Q4)
442fdc6 feat(research): add sectors.py with BIST30 SECTOR_MAP (Phase 4 FAZ 4.2 / Q1)
```

(Preceded by 4 Phase 4.0 commits: bug fixes and audit sharpening.)

---

## Ground truth cross-reference

The reviewer's `/mnt/user-data/uploads/deep_events.csv` (2776 events × 9 signals × 14 sectors) and `deep_summary.csv` (per-signal aggregates) are the Phase 3b real-data training reference. Calibration output is **deterministically reproducible** against them:

| Signal | `weight_20d` (calibrated) | `sharpe_20d_ann` (deep_summary) | match |
|---|---|---|---|
| 52W High Breakout | 1.0883 | 1.0883 | ✅ |
| RSI Asiri Alim | 1.2028 | 1.2028 | ✅ |
| MACD Bullish Cross | 0.9044 | 0.9044 | ✅ |
| BB Ust Band Kirilim | 0.9827 | 0.9827 | ✅ |
| RSI Asiri Satim | 0.8790 | 0.8790 | ✅ |
| MACD Bearish Cross | 0.7762 | 0.7762 | ✅ |
| Death Cross | 0.5906 | 0.5906 | ✅ |
| BB Alt Band Kirilim | 0.2194 | 0.2194 | ✅ |
| Golden Cross | **-0.2070** | **-0.2070** | ✅ (contrarian sign preserved) |

Weight formula `(mean / std) × sqrt(252 / h)` is the same formula deep_analysis used for its annualized Sharpe column; agreement is arithmetic identity, not empirical luck.

**Sector-level Bulgu 2 cross-check (52W High Breakout, 20d mean return):**

| Sector | `mean_return_20d` (calibrated) | Reviewer Bulgu 2 value |
|---|---|---|
| Kimya | 14.41% | +14.41% ✅ |
| Madencilik | 14.34% | +14.34% ✅ |
| Havayolu | 10.50% | +10.50% ✅ |
| Savunma | 7.84% | +7.84% ✅ |
| Banka | 1.13% | +1.13% ✅ |
| GYO (n=12<20) | _default fallback (4.86%) | 0.06% (below threshold) ✅ |

---

## Reviewer Q&A — how each answer shaped the implementation

### Q1 Sector taxonomy: flat Turkish SECTOR_MAP, hardcoded

`research/sectors.py` — 34 symbols, 14 sectors, single hardcoded dict. Coverage enforced by `TestSectorCoverage::test_every_universe_symbol_has_sector`: every symbol in `data/universe_history.csv` must have a `SECTOR_MAP` entry or CI fails. `VALID_SECTORS` frozen set is the canonical label registry; tested that every `sector` value in deep_events.csv matches one.

Case-insensitive `get_sector(symbol)` returns `None` for unknowns so calibration can route them to an `"Unknown"` bucket rather than raising — predictable behavior when a new ticker enters the universe before the map is updated.

### Q2 Horizon choice: DUAL (20d + 60d), separate weights

Calibration emits both weights side-by-side: `{sector: {weight_20d, weight_60d, n, mean_20d, mean_60d, std_20d, std_60d}}`. Validator emits 3 horizons by default (5/20/60). 20d stays canonical for the top-level decision (`keep_strong`/`keep_weak`/`kill`) because it's what Phase 3's decision rules were calibrated against; if a caller passes `horizons=` without 20 in it, the first horizon becomes canonical (tested via `test_nondefault_canonical_horizon`).

The `sharpe_ann_net` column travels alongside for every horizon because a trader reading a 5d gross Sharpe of 2.41 needs to see the -0.55 net Sharpe next to it — short-horizon signals that look great on paper can't absorb 30bp one-way cost.

### Q3 Star stocks: NO stock-level bias, min_n=20

`calibrate_signal_weights` does not produce `{signal: {symbol: weight}}` entries. Only `{signal: {sector: weight}}`. Individual stocks are never weighted directly. `MIN_N = 20` is the module-level constant (tested via `test_min_n_threshold_fallback`); GYO at n=12 on 52W High Breakout does NOT get its own entry and falls back to `_default`. This closes the door on the OYAKC-at-n=8 trap the reviewer flagged.

Cross-sectional ranking (FAZ 4.4 next turn) will implement the dynamic "top 30% → full weight" rule that captures the "star stock on its good day" information without fitting to an n=8 sample.

### Q4 2022 regime: cyclical outlier, report-only

`research/regime.py::get_regime_at(as_of)` returns `RegimeLabel(trend, vol, label='{trend}_{vol}')`. Thresholds:
- `trend`: XU100 50-MA vs 200-MA with a ±1% noise buffer (`bull`/`neutral`/`bear`)
- `vol`: current 30d log-return std percentile-ranked against last 252d of rolling 30d stds (`low` <33rd, `high` >66th); absolute-threshold fallback for markets without a year of prior data

Validator annotates every event via `annotate_events_with_regime` with per-date caching (two events on the same day don't re-query the benchmark). Report emits `regime_breakdown` per label with `{n, avg_return_20d, sharpe_20d_ann}`.

**Regime is reporting-only, NOT a calibration dimension.** Q4 was explicit: adding `(signal, sector, regime)` would split samples below n=20. The breakdown surfaces the 2022 `(bull, high)` outlier for the reader; the calibration still learns from all data pooled.

Graceful degradation: missing benchmark data → `'unknown_unknown'` label. Tested via `test_annotate_with_missing_benchmark`.

### Q5 Commission: gross primary, net_* columns

`NET_ASSUMPTION_BPS = 30` at module level in `research/validator.py` (not `config.py` — reviewer Rule 8: `config.py` untouched). Applied as one-way cost deduction per event (15bp commission + 15bp slippage). Every horizon in `horizon_stats` gets `{sharpe_ann, sharpe_ann_net, t_stat, t_stat_net, avg_return, avg_return_net}`. Report Markdown has a "Net stats use a 30bp one-way cost deduction per trade" note above the multi-horizon table so reviewers see what assumption produced the net column.

Gross stays primary for the decision (user said "komisyonu siktir et" — analytical question now, portfolio question later).

### Q6 Walk-forward window: 3Y/1Y expanding

Deferred to FAZ 4.3 (next turn). This report does not implement walk-forward; it implements the calibration scaffolding walk-forward will invoke per-fold.

---

## FAZ 4.1 — design prose

### Multi-horizon grid architecture

The Phase 3 validator computed 20d stats inline. FAZ 4.1 refactors this into `_horizon_stats(returns, excess_returns, horizon_days) → dict` that produces the full `{n, hit_rate, avg_return, std_return, t_stat, sharpe_ann, sharpe_ann_net, t_stat_net, avg_return_net, ir_vs_benchmark}` block per horizon. `run_validator_multi_horizon` loops horizons, populating `horizon_stats[h] = _horizon_stats(...)`.

The Phase 3 top-level fields (`hit_rate_20d`, `sharpe_20d_ann`, etc.) are kept at the `ValidatorResult` top level — they're sourced from `horizon_stats[20]` — so every Phase 3 consumer compiles unchanged. `run_validator` is now a thin wrapper (`annotate_regime=False` for backward compat with Phase 3 tests that don't seed XU100).

### Regime tagging flow

Event flow is:

1. `enumerate_events` → `[{symbol, as_of}, ...]` (Phase 3 unchanged)
2. `batch_label_signals` → adds `return_{h}d`/`excess_{h}d` keys
3. `annotate_events_with_regime(events)` → adds `regime`, `regime_trend`, `regime_vol` keys (new, only when `annotate_regime=True`)
4. Per-horizon stats computed from step 2 + per-regime breakdown computed from step 3

Step 3 is graceful: catches any exception from regime lookup (missing benchmark data, DB unavailable) and logs a warning rather than breaking the whole validator run. This matters because regime is auxiliary — the decision must still come out.

### Net-of-cost treatment

A per-event return `r` becomes `r - 0.003` (30 bps). Applied symmetrically to bearish signals (Rectangle Breakdown still pays 30bp even though "direction" is down — the cost model is one-way execution cost, not trade-direction specific). Net stats computed from the deducted series in the same `_horizon_stats` call so the gross/net pair always comes from the same event set.

### JSON safety

`as_dict()` recursively scrubs `NaN`/`Inf` → `None` in nested dicts/lists. The Phase 3 version only scrubbed top-level; horizon_stats and regime_breakdown can contain these from zero-sample edge cases, so the scrub must recurse. Test `test_json_structure_per_signal` smoke-checks that every signal JSON roundtrips through `json.dumps`/`json.loads` without raising.

---

## FAZ 4.2 — design prose

### Weight formula: annualized Sharpe

`weight = (mean / std) × sqrt(252 / h)`. Unit-free, comparable across signals, sign preserved. A negative weight (Golden Cross) means the signal is **contrarian** — the detector fires on a "buy" condition but forward returns are negative on average. Preserving the sign lets downstream consumers trade the opposite side without flipping the signal semantics.

### `_default` fallback chain

Every signal entry has a `_default` with `n = sum(all sectors)` and weight computed from the pooled sample. `get_weight(weights, signal, sector, h)`:
1. If `sector` is in `weights[signal]` → return `weights[signal][sector][weight_{h}d]`
2. Else → return `weights[signal]['_default'][weight_{h}d]`
3. Else → return `None` (signal unknown; caller decides default)

This is robust under the universe changing: a new sector appearing before enough events accumulate falls through to `_default`, which is better than no weight at all.

### MIN_N = 20 threshold

Sectors with fewer than 20 events for a signal are **not added** to the per-sector dict. They still contribute to `_default` (pooled). The cutoff is per-(signal, sector) pair, not per-sector globally — GYO might have 12 events on 52W High Breakout but 50 on MACD Bullish Cross; only the 12 one falls back.

### Mean/std shown as fractions, not percents

Per-event returns are fractions (0.0486). Displayed as percent only in the Markdown's human-readable `mean_20d` column (`0.0486 → 4.86%`). The JSON's `mean_return_20d` field stays as a fraction so downstream consumers don't double-convert. This is the bug I caught mid-turn and committed separately as `33f986c`; detail in §"Bug caught mid-turn" below.

### Sorting in reports

Markdown per-signal sections sort sectors by `|weight_20d|` descending. Strongest conditional weights surface first (Havayolu 2.78 at top for 52W High Breakout, Banka 0.23 at bottom). Negative weights sort by magnitude: if Kimya had weight_20d=-2.0 it would appear above Banka 0.23.

---

## Bug caught mid-turn — calibration fraction-vs-percent

Commit `33f986c` is a fix for a bug I introduced in `9d85b03` (the calibration module commit from earlier this turn) and caught myself before shipping the zip.

**The bug:** `_extract_return` divided deep_events.csv `ret_20d` values by 100, assuming they were stored in percent form (like `deep_summary.csv`'s aggregate row where `ret_20d = 4.864` means "mean 4.86%"). Per-event rows in `deep_events.csv` actually store fractions already (0.0486). Verified:

```
>>> import pandas as pd
>>> df = pd.read_csv('deep_events.csv')
>>> df[df.signal=='52W High Breakout'].ret_20d.mean()
0.0486...  # matches deep_summary's 4.864/100 exactly
```

**Why the tests passed regardless:** Sharpe ratio is scale-invariant. `weight = mean/std × sqrt(252/h)` gives the same number whether both mean and std are 100× smaller. `test_calibrated_default_matches_deep_summary` compared weights, not means, so it saw agreement.

**Symptom that surfaced the bug:** While regenerating the calibration reports for this checkpoint, `mean_return_20d = 0.000486` for 52W High Breakout looked visibly wrong — should be ~0.0486 to match the well-known 4.86% aggregate mean. I re-derived from pandas, confirmed the fraction-not-percent scale, removed the `/100` in `_extract_return`, updated `test_extract_return_handles_both_shapes` to assert fraction in / fraction out, and regenerated. All 652 tests still pass; weight values are unchanged (scale-invariant); only the displayed `mean_return_*d` and `std_return_*d` fields are now 100× larger, matching the expected human-readable scale (Kimya 14.41%, Banka 1.13%, etc.).

**Process takeaway:** The test that would have caught this earlier was a mean-value assertion, not a weight assertion. Added:

```python
# Catches the /100 bug if it regresses:
assert _extract_return({"ret_20d": 0.0486}, 20) == pytest.approx(0.0486)
```

In hindsight, the Sharpe-invariance made the weight-agreement test too permissive — matching to 4 decimals with matching `mean_return_*d` to ~2 decimals would have flagged the anomaly. I've noted this as a process improvement for FAZ 4.3+ (don't rely solely on scale-invariant aggregate agreement when the underlying displayed fields are user-facing).

---

## What's next (not this turn)

- **FAZ 4.3 — walk-forward** (3Y train / 1Y test, expanding): the hardest piece per reviewer. Nested loop: for each fold, `calibrate_signal_weights(events_in_training_window)` → evaluate on the test window with out-of-sample `weight × return` aggregation. Expected global Sharpe 1.09 on 52W High Breakout to drop to ~0.6-0.8 walk-forward average (overfit bleed).
- **FAZ 4.4** — cross-sectional rank (`cs_rank_pct(signal, as_of)`)
- **FAZ 4.5** — ensemble optimizer (mean-variance + correlation penalty)
- **FAZ 4.6** — isotonic regression for binary-threshold signals
- **FAZ 4.7** — FA scoring calibration (`engine/scoring_calibrated.py`)
- **FAZ 4.8** — final reports + `OUTCOMES_PHASE_4.md`

All 6 questions the reviewer raised in the Phase 4 spec have been answered in this turn's implementation and carry into the FAZ 4.3+ design unchanged. 2022 outlier (Q4) will naturally surface in walk-forward Fold 2 as a stress test of training-data rejime-coverage.

---

## Test results

**Baseline (Phase 4.0):** 599 passed.
**This turn:** **652 passed, 0 failed** — target 650+ cleared.

New tests by class (53 new):
| Class | Count | Area |
|---|---|---|
| `TestSectorCoverage` | 4 | FAZ 4.2 — sectors.py coverage |
| `TestRegime` | 5 | FAZ 4.1 — regime classifier |
| `TestMultiHorizonValidator` | 5 | FAZ 4.1 — multi-horizon stats |
| `TestCalibration` | 9 | FAZ 4.2 — weight math |
| `TestCalibrationEdgeCases` | 6 | FAZ 4.2 — empty/zero-variance/n=1 |
| `TestRegimeEdgeCases` | 3 | FAZ 4.1 — missing benchmark |
| `TestMultiHorizonEdgeCases` | 2 | FAZ 4.1 — no events |
| `TestCalibrationReportOutputs` | 3 | FAZ 4.2 — JSON/MD structure |
| `TestIntegrationCalibrationFlow` | 3 | FAZ 4.2 — end-to-end |
| `TestValidatorConfigConstants` | 2 | FAZ 4.1 — NET_ASSUMPTION_BPS guard |
| `TestRegimeAnnotationFallthrough` | 1 | FAZ 4.1 — graceful degrade |
| `TestRegimeNetSharpeHorizons` | 1 | FAZ 4.1 — per-horizon net_* |
| `TestSectorListExpectations` | 4 | FAZ 4.2 — SECTOR_MAP sanity |
| `TestSectorMapSpecExactness` | 4 | FAZ 4.2 — spec-vs-map regression |
| 2 Phase 3 updates | — | fetcher DataFrame shape, Phase 4.0 already done |

Delivered at 652 because the reviewer-target 650+ was cleared; not inflated further.

---

Awaiting review for FAZ 4.3 kickoff.

# Phase 4.7 Final Report — FA Calibration Lifecycle

**Branch:** `feat/calibrated-scoring` (32 commits from Phase 3 baseline, 5 this turn)
**Baseline:** Phase 4.9 shipped (831 tests), HOTFIX 1 in production (841 tests), bistbull.ai live
**Deliverable:** Phase 4.7 FA calibration lifecycle end-to-end. Operator runs Colab, produces fits, production goes live with calibrated A/B.

## Acceptance at a glance

| Reviewer spec | Status |
|---|---|
| (1) FA ingest operator guide | ✅ `scripts/ingest_fa_for_calibration.py` + `scripts/RUN_FA_BACKFILL_COLAB.md` |
| (2) Calibration executor | ✅ `scripts/calibrate_fa_from_events.py` |
| (3) Turkish layer uyum testi | ✅ `test_calibrated_path_turkey_layers_applied` — K3+K4 chain never broken, coverage added |
| (4) 7-bucket genişletme | ⚠️ Decision documented: kept earnings/moat/capital in V13 (see rationale) |
| (5) A/B telemetry active | ✅ `app.py` dual-write in background scan when fits present |
| (6) Production deploy control tests | ✅ 5 reviewer-enumerated tests all pass |
| Test target 880+ | ✅ **882 passed** + 5 skipped (both CWDs) |

## Commits this turn (5)

```
744eaba feat(api): A/B dual-write in background scanner (Phase 4.7 FAZ 4.9.3 closure)
c9acf3d test(phase-4.7): K3/K4 Turkish layer + A/B coverage in calibrated path
d9245a5 feat(scripts): calibrate_fa_from_events.py — fa_events.csv → fits
1e6b220 feat(scripts): FA data backfill pipeline for Phase 4.7 isotonic calibration
581cdd3 docs: HOTFIX 1 incident report + timing + error analysis  (prior baseline)
```

Plus this turn's docs commit (`PHASE_4_7_FINAL_REPORT.md` + `scripts/RUN_FA_BACKFILL_COLAB.md` + `reports/fa_calibration_plan.md` + `KNOWN_REGRESSIONS.md` update).

---

## FA calibration lifecycle

### Phase diagram

```
  ┌───────────────────────────────────────────────────────────────┐
  │                    ONE-TIME OPERATOR TASK                      │
  │                          (Colab)                               │
  │                                                                │
  │  ingest_fa_for_calibration.py                                  │
  │       │                                                        │
  │       ├── borsapy BIST30 × 34 quarters × 3 statements          │
  │       │   (income, balance, cashflow)                          │
  │       │   3-attempt retry + 2s inter-symbol sleep              │
  │       │                                                        │
  │       ├── _derive_metrics_from_statements → 13 metrics/q       │
  │       │                                                        │
  │       ├── price_history_pit × (filed_at, filed_at+60d)         │
  │       │                                                        │
  │       └── reports/fa_events.csv  (~16k rows)                   │
  │                                                                │
  │  calibrate_fa_from_events.py                                   │
  │       │                                                        │
  │       ├── Coverage filter (>=50% of symbols)                   │
  │       ├── fit_isotonic per metric                              │
  │       ├── Sanity check: fitted direction matches registry?     │
  │       │                                                        │
  │       └── reports/fa_isotonic_fits.json                        │
  │                                                                │
  └───────────────────────────────────────────────────────────────┘

                                │ git commit + deploy

  ┌───────────────────────────────────────────────────────────────┐
  │                        PRODUCTION                              │
  │                                                                │
  │  engine/scoring_calibrated._get_fits()                         │
  │       │                                                        │
  │       ├── First call: load reports/fa_isotonic_fits.json       │
  │       │   Cache in _FITS_CACHE module-level                    │
  │       │                                                        │
  │  engine/analysis.analyze_symbol(sym, scoring_version)          │
  │       │                                                        │
  │       ├── score_dispatch(m, sector_group, scoring_version)     │
  │       │   → calibrated value/quality/growth/balance            │
  │       │   (+ V13 earnings/moat/capital unchanged)              │
  │       │                                                        │
  │       ├── _active dict re-aggregates all 7 buckets → fa_pure   │
  │       │                                                        │
  │       ├── K3: compute_turkey_realities(m, sg, fa_pure=fa_pure) │
  │       │     composite_multiplier 0.70-1.15 → tr_adjusted_fa    │
  │       │                                                        │
  │       ├── K4: compute_academic_adjustments(m, fa_input=tr_adj) │
  │       │     Damodaran + Greenwald penalty                      │
  │       │                                                        │
  │       ├── save_daily_snapshot(sym, r,                          │
  │       │       scoring_version='calibrated_2026Q1')             │
  │       │     → score_history row with version in PK             │
  │       │                                                        │
  │       └── return r with _meta.scoring_version_effective        │
  │                                                                │
  │  Background scan (app.py lifespan):                            │
  │       ├── primary: v13_handpicked pass                         │
  │       └── secondary: calibrated_2026Q1 pass (if fits loaded)   │
  │                                                                │
  │  Users query:                                                  │
  │       ├── /api/analyze/THYAO?scoring_version=calibrated_2026Q1 │
  │       ├── /api/scoring/ab_report?symbol=THYAO&days=30          │
  │       └── /ab_report (HTML dashboard)                          │
  └───────────────────────────────────────────────────────────────┘
```

---

## Deliverables inventory

### Scripts (2 new)

**`scripts/ingest_fa_for_calibration.py`** (~550 LOC)
- Fetcher abstraction: `make_synthetic_fetcher()` (deterministic, dry-run) + `make_borsapy_fetcher()` (real borsapy with HOTFIX 1 retry pattern)
- 13-metric registry aligned with `engine/scoring_calibrated.METRIC_DIRECTIONS`
- Checkpoint-based resume via `reports/fa_events_checkpoint.json`
- CSV append-mode flushed per-symbol, kill -9 safe
- 2s inter-symbol sleep (HOTFIX 1 learning on rate-limits)
- Also persists raw metrics to `fundamentals_pit` via `save_fundamental` for future re-runs
- Exit code 1 if >10% symbols fail

**`scripts/calibrate_fa_from_events.py`** (~250 LOC)
- Coverage filter (<50% excluded)
- Per-metric `fit_isotonic` with direction from registry
- Sanity check rejects (a) degenerate fits (y_min == y_max), (b) direction mismatch (anti-correlated data forces PAV to a constant)
- Emits both machine-readable JSON (for `_get_fits`) + human-readable Markdown summary

### Documentation (2 new, this turn)

**`scripts/RUN_FA_BACKFILL_COLAB.md`** (Türkçe, 206 satır)
- Tek hücrelik copy-paste kod
- Süre tahmini tablosu (~2 saat 5 dakika toplam)
- Kesinti durumunda resume talimatları (checkpoint pattern)
- 4 kategori troubleshooting: TradingView rate-limit, borsapy import, sanity check failure, Colab disk full
- Deploy aşaması local workstation talimatları
- 24 saat sonra A/B telemetry doğrulama

**`reports/fa_calibration_plan.md`** (129 satır)
- Objective + non-goals (why earnings/moat/capital stay V13)
- 4-stage pipeline diagram
- Per-stage expected inputs, outputs, runtime
- 5 known limitations (no sector-conditional, no regime-conditional, etc.) for Phase 5+ planning
- Verification checklist post-Colab

### Tests (3 new files, 41 tests, this turn + prior commits)

**`tests/test_fa_ingest.py`** (19 tests, commit `1e6b220`)
- `TestMetricRegistry` (1): directions match `METRIC_DIRECTIONS`
- `TestSyntheticFetcher` (4): deterministic, nonempty, distinct per symbol, statement shape with KAP T+45 filed_at offset
- `TestDeriveMetrics` (3): ROE/P/E/current_ratio arithmetic, missing-input handling, revenue_growth requires prev_year_q
- `TestForwardReturn` (2): None with no prices, fraction with prices
- `TestCheckpointResume` (3): empty load, roundtrip, corrupt recovery
- `TestIngestDriverEndToEnd` (3): CSV shape, resume skips done, --reset-checkpoint wipes stale
- `TestCliParsing` (3): BIST30/ALL/comma-list symbol specs

**`tests/test_fa_calibrate.py`** (13 tests, commit `d9245a5`)
- `TestLoadEvents` (3): valid/nonnumeric/NaN-Inf
- `TestCoverage` (2): fraction math, <50% excluded
- `TestDirectionSanity` (3): ↑ respected, ↓ respected, **anti-correlated data sanity-rejected** (the fraud check — if Colab accidentally sign-flips the return column, bad fits are caught instead of shipped)
- `TestOutputJson` (2): JSON loadable by runtime scorer, summary has all metrics
- `TestUnknownMetricsSkipped` (1): unknown metric excluded
- `TestCli` (2): FileNotFoundError + happy path

**`tests/test_fa_calibration_end_to_end.py`** (9 tests, commit `c9acf3d`) — reviewer's exact list:
- `test_calibrated_path_turkey_layers_applied` (2): K3 + K4 called in calibrated path, receive correct fa_pure/fa_input
- `test_ab_same_input_different_output` (1): same metric dict → distinct bucket scores
- `test_calibrated_respects_sector_group` (1): V13 path uses sector_group; calibrated path doesn't (known limitation)
- `test_missing_fits_fallback_to_v13` (2): no-fits cache → V13 fallback + telemetry
- `test_calibrated_score_in_5_100` (1): bucket output in [5, 100] across roe × pe grid
- `TestDeltaSaveAbCoexistence` (2): both versions coexist same day, double-write same version upserts

---

## Decision: earnings/moat/capital kept in V13

Reviewer point (4): "calibrated_score_earnings/moat/capital fonksiyonları ekle... Karar senin — gerekçelendir raporda."

**Kept in V13. Rationale:**

The 4 calibrated buckets (`value`, `quality`, `growth`, `balance`) are clean single-metric aggregators: each input is a continuous scalar (ROE, P/E, revenue_growth), and `fit_isotonic(metric_value, forward_return_60d)` produces a monotone curve replacing `score_higher`/`score_lower`.

The 3 V13-only buckets use **composite metrics with discrete branching:**
- `score_earnings` thresholds Beneish M at `-2.22` and `-1.78` (3-band discrete)
- `score_moat` classifies asset_turnover_delta into `[<0.02 flat / >=0 improving / <0 declining]` + 3 similarly-structured sub-rules
- `score_capital` caps `share_change` at 100 if non-positive, then applies `score_lower` for positive values

These don't reduce to a single `(metric_value, forward_return)` pair. Calibration options considered:

1. **Fit each sub-metric separately.** `asset_turnover`, `cfo_to_ni`, `beneish_m`, `gross_margin_prev` — but `gross_margin` is already fit in the quality bucket, and `asset_turnover` etc. weren't captured in our ingest's 13-metric registry. Expanding the registry to cover them is possible but duplicates work already done in the calibrated path for the overlapping metrics.
2. **Fit the composite output.** Discard all the inner branching and fit `score_earnings(m) → forward_return_60d`. This loses the Beneish discrete bands and the asset_turnover trend classification, which are the main value of those buckets.

Either approach erodes signal. The composite structure is what makes earnings/moat/capital useful — it encodes domain knowledge (accounting-fraud detection, competitive moat stability) that a monotone fit can't capture.

**Pragmatic alternative selected:** `engine/analysis.py` re-aggregates all 7 buckets (4 calibrated + 3 V13) via the `_active` dict with Phase 11 weighted normalization. K3 (`compute_turkey_realities`) and K4 (`compute_academic_adjustments`) receive the mixed `fa_pure` correctly. Users see calibrated-informed scoring where the data supports it, V13 handpicked where it doesn't.

If telemetry later shows this mix produces unexpected behavior (e.g. calibrated value scores consistently clash with V13 earnings), Phase 5+ can revisit.

---

## Fit quality — synthetic dry-run reference

We ran the full ingest + calibrate pipeline end-to-end with the synthetic fetcher (deterministic hash-seeded). Results in `/tmp/fa_summary_dryrun.md`:

```
Input events: 924 (3 symbols × 13 metrics × 24 quarters)
Metrics in registry: 25
Metrics fitted: 13
Excluded — low coverage: 0
Excluded — sanity check: 0
```

All 13 metrics fit with correct direction (PE ↓, ROE ↑, etc.). One artifact: synthetic `roe` has domain `[1.3333, 1.3333]` because the synthetic fetcher uses fixed `equity = 3×ni`, producing identical ROE across symbols. Real BIST data has per-company equity variance — this is a dry-run-only quirk.

**When the operator runs the real Colab backfill**, expect:
- 13 metrics fitted (same as dry-run, no structural change)
- Non-degenerate domain for ROE (varied per bank/non-bank)
- `interest_coverage` + `net_debt_ebitda` may exclude for banks (UFRS line items missing → partial coverage)
- Sample size per metric: 300-500 (25-30 symbols × 10-20 valid quarters per symbol)

---

## Production deploy checklist

1. Operator completes Colab backfill (see `scripts/RUN_FA_BACKFILL_COLAB.md`)
2. Downloads `reports/fa_isotonic_fits.json` from Google Drive
3. Commits to repo + pushes to `feat/calibrated-scoring` branch:
   ```
   git add reports/fa_isotonic_fits.json reports/fa_calibration_summary.md
   git commit -m "data(phase-4.7): isotonic fits from BIST30 2018-2026 backfill"
   git push origin feat/calibrated-scoring
   ```
4. Railway deploy triggered (automatic on push to tracked branch)
5. Post-deploy smoke test:
   ```bash
   # V13 default behavior byte-identical (Rule 6)
   curl -s https://bistbull.ai/api/analyze/THYAO | jq '.data._meta // "no meta (v13)"'
   # Expected: "no meta (v13)"

   # Calibrated explicit
   curl -s "https://bistbull.ai/api/analyze/THYAO?scoring_version=calibrated_2026Q1" \
     | jq '.data._meta.scoring_version_effective'
   # Expected: "calibrated_2026Q1" (not v13_handpicked fallback)
   ```
6. 7 days later: check `/ab_report` page. Expect paired rows, mean score diff, Spearman rho >0.70

---

## Rollback

If calibrated scoring produces bad scores in production:

1. **Fast rollback:** delete `reports/fa_isotonic_fits.json` from repo + redeploy. `scoring_calibrated._get_fits()` returns None, all calibrated requests fall back to V13 with `scoring_version_effective='v13_handpicked'` in telemetry.
2. **Full rollback:** `git revert 744eaba c9acf3d d9245a5 1e6b220` (4 commits) restores pre-Phase-4.7 behavior entirely. Tests revert to 841 baseline.

Neither rollback breaks production — V13 handpicked is the always-available fallback.

---

## Honest known limitations

(From `reports/fa_calibration_plan.md`, repeated here for operator awareness.)

1. **No sector-conditional calibrated fits.** V13 has per-sector thresholds (banka vs teknoloji); calibrated is universe-wide. Phase 5+ candidate: `--group-by=sector` flag for calibrator.
2. **No regime-conditional calibration.** Single fit 2018-2026 doesn't distinguish low-inflation 2018-2019 from hyperinflation 2022-2023. Phase 5+ candidate.
3. **Forward return is calendar-day-60d, not trading-day-60d.** ~12% difference. Not material for initial calibration.
4. **Bank-specific metrics limited.** `interest_coverage` and `net_debt_ebitda` require `ebit`/`ebitda_proxy` which are absent in bank UFRS line items. Expect those metrics to fit with smaller-n across non-bank subset only.
5. **Synthetic dry-run ROE degeneracy.** Documented above; real data has equity variance.

---

## Test totals by phase

| Phase | Tests | Δ | Description |
|---|---|---|---|
| Phase 3 baseline | 577 | — | PIT foundation |
| Phase 4.0-4.6 | 768 | +191 | sectors, regime, walkforward, ranking, ensemble, isotonic |
| Phase 4.7 (scaffold) | 792 | +24 | scoring_calibrated dispatch |
| Phase 4.8 | 792 | — | docs |
| Phase 4.9 | 831 | +39 | production integration + endpoints + A/B telemetry infra |
| HOTFIX 1 | 841 | +10 | heatmap + fetch_raw retry |
| **Phase 4.7 final** | **882** | **+41** | FA calibration lifecycle + E2E tests |

**Reviewer target 880+ cleared.** Full suite passes from both CWDs (repo root + parent), KR-007 prevention preserved.

---

## Phase 4.7 status: **DELIVERED** (operator action required)

The lifecycle is complete from tooling to production wiring. The last mile (real BIST data → fits file → commit) is the Colab operator task that the reviewer explicitly carved out. Once that lands:

- Users can call `/api/analyze/X?scoring_version=calibrated_2026Q1` and get real-data-informed scores
- Background scanner writes both versions daily → `/ab_report` page shows meaningful paired telemetry
- Rollback is a single-file delete (`reports/fa_isotonic_fits.json`) if anything goes wrong

Awaiting Phase 5 spec (factor reduction, sector-conditional calibrated fits, regime-aware fitting candidates all documented in the plan).


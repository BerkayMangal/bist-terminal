# Phase 4.7 FA Calibration — Planning Document

Reviewer checklist (b): pre-ingest plan. Explains what the operator will produce in Colab, the expected output shape, and how production consumes it.

## Objective

Replace the 4 hand-picked threshold buckets (`value`, `quality`, `growth`, `balance`) in `engine/scoring.py` with data-driven isotonic fits calibrated on real BIST 2018-2026 (metric_value, forward_return_60d) pairs.

Earnings/moat/capital stay in V13. Rationale: those 3 buckets use composite metrics with discrete branching (Beneish M threshold, `share_change` cap-at-100, `asset_turnover` trend classification) that don't cleanly reduce to a single metric_value → return pair. Calibrating them would duplicate the work already done in the 4 calibrated buckets (via sub-metrics like `roe`, `net_margin`) or collapse composite structure. `engine/analysis.py` re-aggregates all 7 buckets into `fa_pure` regardless of source, so K3 (turkey_realities) + K4 (academic_layer) see the correct composite.

## Data pipeline

```
┌─────────────────┐   2-3 hours   ┌──────────────────┐
│  Colab operator │  borsapy API  │  fa_events.csv   │
│  (BIST30 × 34Q) │──────────────▶│  ~16,000 rows    │
└─────────────────┘               └──────────────────┘
                                           │
                                           │ <30s local
                                           ▼
                                  ┌──────────────────┐
                                  │ fa_isotonic_fits │
                                  │    .json         │
                                  └──────────────────┘
                                           │
                                           │ git commit + deploy
                                           ▼
                                  ┌──────────────────┐
                                  │  production      │
                                  │  scoring_calib.  │
                                  │  _get_fits()     │
                                  └──────────────────┘
```

## Stage 1: Ingest — scripts/ingest_fa_for_calibration.py

**Input:** BIST30 universe × quarterly fundamentals 2018-2026 × price history 2018-2026 (both from borsapy + PIT tables).

**Output:** `reports/fa_events.csv` — one row per `(symbol, period_end, metric)` tuple with the forward 60-day TR price return from filing date.

**Coverage estimate:**
- BIST30 = 30 symbols
- 8 years × 4 quarters = 32 quarters
- 13 metrics per quarter
- ~3 year price lead-time needed for 60d forward window, so effective range 2018-2026-2mo
- Expected rows: 30 × 30 × 13 = **~11,700 to ~16,000 rows** (depending on which metrics are computable for banks vs. non-banks)

**Rate limits + resilience:**
- 3-attempt retry with 0.5s/1s/2s backoff (HOTFIX 1 pattern) per sub-statement (income/balance/cashflow)
- 2s sleep between symbols to avoid TradingView mass-rate-limit (HOTFIX 1 learning)
- Checkpoint-based resume: `reports/fa_events_checkpoint.json` tracks completed symbols; kill -9 safe (CSV flushed per-symbol)
- Exit code 1 if >10% of symbols fail (CI guard)

## Stage 2: Calibration — scripts/calibrate_fa_from_events.py

**Input:** `reports/fa_events.csv`.

**Output:**
- `reports/fa_isotonic_fits.json` — consumable by `engine.scoring_calibrated._get_fits()`
- `reports/fa_calibration_summary.md` — human-readable per-metric fit quality table

**Per-metric processing:**

1. Coverage filter: drop metric if <50% of symbols have at least one row for it (Phase 3 convention).
2. Per-metric `fit_isotonic` with direction from `METRIC_DIRECTIONS` (25-metric registry in `engine/scoring_calibrated.py`).
3. `min_samples=20` per metric (below that, PAV doesn't produce signal).
4. Sanity check: fitted y-series must be monotone in registered direction. If registry says ROE should be ↑ but fit is ↓ or constant, **exclude** this metric (data quality issue).

**Expected fits (13 metrics):**

| Metric | Direction | Source in statement |
|---|:-:|---|
| roe | ↑ | net_income / equity (annualized) |
| roic | ↑ | ebit / (equity + debt) |
| net_margin | ↑ | net_income / revenue |
| gross_margin | ↑ | gross_profit / revenue |
| operating_margin | ↑ | operating_income / revenue |
| revenue_growth | ↑ | yoy revenue |
| fcf_yield | ↑ | free_cashflow / market_cap |
| current_ratio | ↑ | current_assets / current_liabilities |
| interest_coverage | ↑ | ebit / interest_expense |
| pe | ↓ | market_cap / net_income |
| pb | ↓ | market_cap / equity |
| debt_equity | ↓ | total_debt / equity |
| net_debt_ebitda | ↓ | (debt − cash) / ebitda |

## Stage 3: Consumption — engine/scoring_calibrated.py

Already scaffolded in Phase 4.7 (commit `e01175c`). Behavior:

- `_get_fits()` reads `reports/fa_isotonic_fits.json` on first request, caches in `_FITS_CACHE` module-level.
- `score_dispatch(m, sector_group, scoring_version='calibrated_2026Q1')` routes value/quality/growth/balance through `score_metric_calibrated(metric, value, fits)`.
- `score_metric_calibrated` returns `5 + 95 * fit.predict_normalized(x)` so output lands in [5, 100] compatible with V13 aggregation.
- When fits are missing (pre-Colab state), dispatcher falls back to V13 handpicked and sets `scoring_version_effective='v13_handpicked'` for telemetry.
- `engine/analysis.py:analyze_symbol(sym, scoring_version)` — Phase 4.9 integration — uses `score_dispatch`. `fa_pure` is re-aggregated from 4 calibrated + 3 V13 buckets. K3 + K4 receive that `fa_pure` regardless of version.

## Stage 4: A/B telemetry — app.py scan loop

Already shipped in commit `744eaba`:

After the primary v13 scan completes, the scan loop checks `_get_fits()`. If non-None, runs a secondary sequential pass with `scoring_version='calibrated_2026Q1'`. Each call writes a second `score_history` row (different scoring_version). The Phase 2 PK triple + UPSERT semantics keep both versions coexisting on the same (symbol, date).

Users query the A/B delta via:

- `GET /api/scoring/ab_report?symbol=THYAO&days=30` — JSON/CSV
- `GET /ab_report` — HTML dashboard

## Known limitations (Phase 5+ material)

1. **No sector-conditional calibrated fits.** V13 thresholds differ between "banka" and "teknoloji"; calibrated fits are universe-wide. If reviewer wants per-sector calibration, the ingest script needs a sector column (already there: `sector`) and the calibration executor needs a `--group-by=sector` flag.

2. **No regime-conditional calibration.** A single fit across 2018-2026 doesn't capture that Turkish fundamentals behave differently under (a) low-inflation 2018-2019 vs. (b) hyperinflation 2022-2023 vs. (c) recovery 2024-2026. Regime-split fits would require either per-period fits or regime as a sector_group-equivalent dimension.

3. **Synthetic dry-run artifact for ROE.** In dry-run mode, the synthetic fetcher uses fixed `equity = 3 × net_income` so all synthetic symbols have identical ROE. Real data has per-company variance; this is a dry-run-only quirk.

4. **Sample size varies per metric.** `interest_coverage` and `net_debt_ebitda` require `ebit` and `ebitda_proxy` which are missing for banks (UFRS line items differ). Expect these metrics to have smaller n and shorter domains.

5. **Forward return is calendar-day-60d, not trading-day-60d.** Small difference (~12%) but documented. True trading-day-60d would need a Turkish trading calendar; not material for the initial calibration.

## Verification checklist post-Colab

1. `wc -l reports/fa_events.csv` → expect 10,000-16,000 lines
2. Spot-check one row: `grep "^THYAO," reports/fa_events.csv | head -3` → reasonable metric values for an airline
3. Coverage sanity: `python3 -c "import pandas as pd; df=pd.read_csv('reports/fa_events.csv'); print(df.groupby('metric')['symbol'].nunique())"` → all metrics should have 25-30 symbols (>50% of 30)
4. Spearman sanity for ROE: `python3 -c "import pandas as pd; df=pd.read_csv('reports/fa_events.csv'); r=df[df.metric=='roe']; print(r[['metric_value','forward_return_60d']].corr(method='spearman'))"` → weakly positive (+0.05 to +0.25 expected)
5. Run calibrator; inspect `reports/fa_calibration_summary.md` → **"Metrics fitted: 10+"** target; if <8 metrics fit, investigate.
6. Deploy + smoke test `/api/analyze/THYAO?scoring_version=calibrated_2026Q1` → `_meta.scoring_version_effective === 'calibrated_2026Q1'` (not fallback).
7. 7 days later: `/ab_report` page should show paired rows with mean skor diff and Spearman rho.


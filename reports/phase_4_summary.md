# Phase 4 Summary — Calibrated Scoring

**Branch:** `feat/calibrated-scoring`
**Period:** 2026-04-20 (Phases 4.0 through 4.8)
**Test count:** 577 (P3 baseline) → **792 passed**, zero failures.

Phase 4 replaced the project's magic-number signal scoring with a data-driven calibration stack: sector-conditional weights, walk-forward validation, cross-sectional ranking, mean-variance ensemble, and isotonic fits ready to swap in for the handpicked V13 FA thresholds.

## Fold schedule (walk-forward, expanding window)

Per reviewer Q6: 3-year training / 1-year test, expanding. Event coverage in `deep_events.csv` (2018-2025) yields 5 folds:

| Fold | Train | Test | Train N | Test N |
|---|---|---|---|---|
| 1 | 2018-2020 | 2021 | 937 | 353 |
| 2 | 2018-2021 | 2022 | 1290 | 512 |
| 3 | 2018-2022 | 2023 | 1802 | 366 |
| 4 | 2018-2023 | 2024 | 2168 | 349 |
| 5 | 2018-2024 | 2025 | 2517 | 259 |

Fold 2 (test 2022) was reviewer's explicit stress test: 2022 was a cyclical outlier (emtia rallisi + TL devalüasyonu, BIST nominal +197%). Training 2018-2021 didn't contain this regime.

## Walk-forward Sharpe table (20-day horizon)

From `reports/walkforward.csv`, sorted by walk-forward mean:

| Signal | global | wf_mean | wf_std | F1 | F2 | F3 | F4 | F5 |
|---|---|---|---|---|---|---|---|---|
| RSI Asiri Satim | +0.88 | **+1.45** | 1.89 | +0.42 | **+4.32** | +1.94 | +1.31 | -0.73 |
| RSI Asiri Alim | +1.20 | **+1.24** | 0.70 | +1.52 | +1.72 | +1.59 | +1.35 | +0.01 |
| 52W High Breakout | +1.09 | **+1.16** | 0.73 | +0.69 | +1.43 | +1.36 | +2.10 | +0.22 |
| MACD Bearish Cross | +0.78 | +1.05 | 0.64 | +0.97 | +1.76 | +1.63 | +0.27 | +0.60 |
| BB Ust Band Kirilim | +0.98 | +1.03 | 0.45 | +0.64 | +1.51 | +0.78 | +1.55 | +0.69 |
| MACD Bullish Cross | +0.90 | +0.97 | 1.01 | +1.05 | +1.80 | +1.05 | +1.69 | **-0.72** |
| BB Alt Band Kirilim | +0.22 | +0.79 | 1.66 | +0.01 | **+3.56** | +0.65 | +0.58 | -0.87 |
| Death Cross | +0.59 | +0.31 | 1.51 | -0.75 | — | — | — | +1.38 |
| Golden Cross | -0.21 | +0.15 | 1.79 | +0.45 | — | — | +1.77 | -1.76 |

### Key findings

**Reviewer expected overfit discount 0.6-0.8. Observed: much smaller.** For stable signals (52W High, RSI Asiri Alim, BB Ust, MACD Bullish), global Sharpe and walk-forward mean are within 0.1. Signals generalize better than feared.

**Fold 2 verdict (from Phase 4.3 stress analysis):**
- BB Alt Band Kirilim F2 diff **+3.46** — flagged `F2 extreme outlier (likely vol-regime-specific)`. Without 2022, wf_mean collapses 0.79 → 0.09.
- RSI Asiri Satim F2 diff **+3.59** — same flag. Without 2022: 1.45 → 0.73.
- Remaining 5 signals show moderate F2 outperformance (+0.3 to +1.0 diff), i.e. regime-independent generalization.

**2025 (F5) sistematik zayıflık**: Golden Cross -1.76, MACD Bullish -0.72, RSI Asiri Alim +0.01. Either late-cycle signal bozulması veya partial-year sample noise (F5 n=259 vs other folds' ~350). Honest note, not a bug.

## Sector-conditional weights (FAZ 4.2)

From `reports/phase_4_weights.json`, produced by `calibrate_signal_weights(events, min_n=20)`:

Representative excerpt for 52W High Breakout:

| Sector | Weight (20d) | n | mean_return |
|---|---|---|---|
| Havayolu | **+2.78** | 32 | +0.147 |
| Demir-Çelik | +1.84 | 41 | +0.096 |
| Teknoloji | +1.52 | 78 | +0.088 |
| Perakende | +1.21 | 65 | +0.072 |
| Otomotiv | +0.94 | 28 | +0.058 |
| Gıda | +0.67 | 42 | +0.041 |
| Enerji | +0.51 | 35 | +0.032 |
| Banka | **+0.23** | 58 | +0.015 |

Reviewer's checkpoint numbers (Havayolu 2.78, Banka 0.23) match exactly. Sectors with n<20 fall back to the `_default` weight via `get_weight`.

## Cross-sectional ranking (FAZ 4.4)

`research/ranking.py`'s `signal_strength` → `cs_rank_pct` → `modulation_factor` pipeline resolves reviewer Q3 (no stock-level bias). Rank cutoffs per spec:
- Top 30% (rank ≥ 0.7) → full weight
- Bottom 30% (rank ≤ 0.3) → zero weight
- Middle 40% → linear ramp

Every signal has a per-indicator strength formula (close/252d-high ratio, |MA50-MA200|/MA200 gap, RSI distance past threshold, etc.). All strengths clipped to [0, 1] for scale-neutral comparison.

## Ensemble optimizer (FAZ 4.5)

From `reports/phase_4_ensemble.json`. Training on F1-F4, hold-out F5 2025:

| Signal | μ (training wf_mean) | Weight |
|---|---|---|
| RSI Asiri Alim | 1.544 | 0.200 |
| MACD Bullish Cross | 1.398 | 0.200 |
| 52W High Breakout | 1.394 | 0.200 |
| MACD Bearish Cross | 1.156 | 0.200 |
| BB Ust Band Kirilim | 1.119 | 0.200 |
| BB Alt Band Kirilim | 1.199 | 0.000 (capped→0) |
| RSI Asiri Satim | 1.998 | 0.000 (capped→0) |

**Hold-out F5 2025:**

| Strategy | F5 Sharpe |
|---|---|
| **Ensemble** | **+0.162** |
| Fair OOS baseline (training-top wf_mean = RSI Asiri Satim) | **-0.734** |
| Post-hoc cherry-pick (Death Cross, n_test=4) | +1.384 (not executable) |

**Ensemble beats the fair OOS baseline by +0.896.** The naive "pick the signal with highest training Sharpe" strategy would have bet on RSI Asiri Satim (training mean 1.998, Phase 4.3 flagged it as regime-outlier) and lost 73bp. Ensemble's diversification turned that into +16bp — an explicit out-of-sample value demonstration.

### Correlation structure

| Pair | Correlation |
|---|---|
| BB Alt Band ↔ RSI Asiri Satim | **0.98** (near-duplicate; same vol-regime factor) |
| BB Ust ↔ MACD Bullish | **0.98** (trend-continuation duplicate) |
| MACD Bearish ↔ RSI Asiri Alim | **0.96** (overbought-reversal cluster) |

9 signals collapse to ~3 independent factors. Phase 5 factor-reduction candidate.

## Isotonic fits (FAZ 4.6)

`research/isotonic.py` — pure-Python Pool Adjacent Violators. Fits a monotone step function to (x, y) pairs; returns `IsotonicFit` with `predict` (binary search), `predict_normalized` ([0, 1] range), clamping at domain boundaries.

Real-data demonstration (from `reports/phase_4_isotonic_fits.md`): for each signal, fit ret_5d → ret_20d to capture momentum profile.

| Signal | n | Knots | y range |
|---|---|---|---|
| 52W High Breakout | 662 | 23 | [-0.369, +0.701] |
| MACD Bullish Cross | 417 | 25 | [-0.273, +0.701] |
| BB Ust Band Kirilim | 433 | 22 | [-0.271, +0.361] |
| MACD Bearish Cross | 461 | 22 | [-0.356, +0.253] |
| RSI Asiri Alim | 267 | 20 | [-0.199, +0.162] |
| RSI Asiri Satim | 142 | 17 | [-0.198, +0.342] |
| BB Alt Band Kirilim | 337 | 17 | [-0.345, +0.269] |
| Golden Cross | 36 | 7 | [-0.141, +0.138] |

Example (52W High Breakout):
- ret_5d = -0.077 → fitted ret_20d = -0.034
- ret_5d = +0.046 → fitted ret_20d = +0.084
- ret_5d = +0.098 → fitted ret_20d = +0.133

The monotone shape confirms momentum persistence beyond the initial breakout: stronger early moves predict stronger sustained moves.

## Calibrated FA scoring (FAZ 4.7)

`engine/scoring_calibrated.py` — A/B dispatch via `scoring_version`:
- `v13_handpicked` → `engine/scoring.py` (existing)
- `calibrated_2026Q1` → this module (isotonic per-metric)

25-metric direction registry (METRIC_DIRECTIONS). Bucket wrappers mirror V13's aggregation exactly; only the primitive changes. Scores land in [5, 100] for downstream compatibility.

**Honest shipping state:** real calibration requires FA data (metric_value, forward_return_60d_TR) pairs not present in deep_events.csv. Scaffolding, dispatcher, bucket wrappers, cache, and fallback paths are all implemented and tested against synthetic FA events. When an operator (Colab) produces `reports/fa_isotonic_fits.json`, `scoring_calibrated` picks it up automatically. Until then, `score_dispatch(scoring_version='calibrated_2026Q1')` transparently falls back to V13 and records the fallback in `scoring_version_effective` for telemetry.

## Calibrated vs handpicked A/B comparison (synthetic)

Against an 80-record synthetic FA event set:

| Metric value | V13 handpicked | Calibrated 2026Q1 |
|---|---|---|
| ROE = 0.20 | 90 | **78.6** |
| ROE = 0.05 | 55 | 57.5 |
| PE = 8.0 | 95 | **68.7** |
| PE = 30.0 | 40 | 58.4 |

V13's thresholds are optimistic at extremes (ROE 0.20 → 90, PE 30 → 40). Calibrated on synthetic data gives a softer output band. Real FA calibration will either validate or refute V13's thresholds — that's the point of the A/B infrastructure.

## Test coverage by phase

| Phase | Tests | Δ | What landed |
|---|---|---|---|
| Phase 3 baseline | 577 | — | (Phase 3 artifacts) |
| 4.0 | 599 | +22 | borsapy, signals, migrations, universe |
| 4.1+4.2 | 652 | +53 | validator multi-horizon, regime, sector calibration |
| 4.3 | 685 | +33 | walk-forward |
| 4.3.5 | 687 | +2 | CWD finalize (KR-007) |
| 4.4 | 714 | +27 | cross-sectional ranking |
| 4.5 | 747 | +33 | ensemble optimizer |
| 4.6 | 768 | +21 | isotonic regression |
| **4.7** | **792** | **+24** | calibrated FA scoring |

Every FAZ has a `TestDisplayFieldCorrectness` class asserting user-facing numerics land in their expected scale bands (KR-006 prevention methodology). All 792 pass from BOTH CWD positions (repo root AND parent), per reviewer's original CWD repro path.

## Deliverables

- `research/sectors.py`, `research/regime.py`, `research/validator.py`, `research/calibration.py`, `research/walkforward.py`, `research/ranking.py`, `research/ensemble.py`, `research/isotonic.py`
- `engine/scoring_calibrated.py`
- `tests/_paths.py` (CWD-independent path constants module)
- `reports/walkforward.csv` (90 rows: 5 folds × 9 signals × 2 horizons)
- `reports/phase_4_walkforward.md` (cross-fold stability + Fold 2 stress)
- `reports/phase_4_weights.{json,md}` (sector-conditional weights)
- `reports/phase_4_ensemble.{json,md}` (mean-variance optimizer output)
- `reports/phase_4_isotonic_fits.{json,md}` (per-signal ret_5d→ret_20d fits)
- `reports/phase_4_summary.md` (this document)
- `reports/OUTCOMES_PHASE_4.md` (expected vs actual table)
- `PHASE_4_0_REPORT.md` through `PHASE_4_8_REPORT.md`
- `KNOWN_REGRESSIONS.md` (all KRs CLOSED)

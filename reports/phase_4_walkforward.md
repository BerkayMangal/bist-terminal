# Phase 4.3 Walk-Forward Validation

## Fold schedule (expanding window, 3Y minimum train)

| Fold | Train | Test | Train N | Test N |
|---|---|---|---|---|
| 1 | 2018-2020 | 2021 | 937 | 353 |
| 2 | 2018-2021 | 2022 | 1290 | 512 |
| 3 | 2018-2022 | 2023 | 1802 | 366 |
| 4 | 2018-2023 | 2024 | 2168 | 349 |
| 5 | 2018-2024 | 2025 | 2517 | 259 |

## Cross-fold stability — raw Sharpe_20d
Sorted by walk-forward mean descending. `global` column is the in-sample Phase 3b Sharpe (if provided); `wf_mean` is the mean across folds; `wf_std` measures stability.

| Signal | global | wf_mean | wf_std | wf_min | wf_max | F1 (2021) | F2 (2022) | F3 (2023) | F4 (2024) | F5 (2025) |
|---|---|---|---|---|---|---|---|---|---|---|
| RSI Asiri Satim | +0.88 | +1.45 | +1.89 | -0.73 | +4.32 | +0.42 | +4.32 | +1.94 | +1.31 | -0.73 |
| RSI Asiri Alim | +1.20 | +1.24 | +0.70 | +0.01 | +1.72 | +1.52 | +1.72 | +1.59 | +1.35 | +0.01 |
| 52W High Breakout | +1.09 | +1.16 | +0.73 | +0.22 | +2.10 | +0.69 | +1.43 | +1.36 | +2.10 | +0.22 |
| MACD Bearish Cross | +0.78 | +1.05 | +0.64 | +0.27 | +1.76 | +0.97 | +1.76 | +1.63 | +0.27 | +0.60 |
| BB Ust Band Kirilim | +0.98 | +1.03 | +0.45 | +0.64 | +1.55 | +0.64 | +1.51 | +0.78 | +1.55 | +0.69 |
| MACD Bullish Cross | +0.90 | +0.97 | +1.01 | -0.72 | +1.80 | +1.05 | +1.80 | +1.05 | +1.69 | -0.72 |
| BB Alt Band Kirilim | +0.22 | +0.79 | +1.66 | -0.87 | +3.56 | +0.01 | +3.56 | +0.65 | +0.58 | -0.87 |
| Death Cross | +0.59 | +0.31 | +1.51 | -0.75 | +1.38 | -0.75 | — | — | — | +1.38 |
| Golden Cross | -0.21 | +0.15 | +1.79 | -1.76 | +1.77 | +0.45 | — | — | +1.77 | -1.76 |

## Cross-fold stability — raw Sharpe_60d
Sorted by walk-forward mean descending. `global` column is the in-sample Phase 3b Sharpe (if provided); `wf_mean` is the mean across folds; `wf_std` measures stability.

| Signal | global | wf_mean | wf_std | wf_min | wf_max | F1 (2021) | F2 (2022) | F3 (2023) | F4 (2024) | F5 (2025) |
|---|---|---|---|---|---|---|---|---|---|---|
| RSI Asiri Alim | +1.20 | +1.15 | +0.39 | +0.55 | +1.49 | +0.97 | +1.49 | +1.37 | +1.37 | +0.55 |
| MACD Bullish Cross | +0.90 | +1.13 | +0.42 | +0.71 | +1.78 | +0.92 | +1.78 | +1.30 | +0.94 | +0.71 |
| Death Cross | +0.59 | +1.12 | +1.02 | +0.40 | +1.84 | +0.40 | — | — | — | +1.84 |
| RSI Asiri Satim | +0.88 | +1.07 | +0.79 | +0.47 | +2.44 | +0.71 | +2.44 | +0.47 | +0.95 | +0.77 |
| MACD Bearish Cross | +0.78 | +1.01 | +0.63 | +0.36 | +1.74 | +0.87 | +1.74 | +1.58 | +0.36 | +0.48 |
| 52W High Breakout | +1.09 | +0.97 | +0.41 | +0.48 | +1.46 | +0.48 | +1.46 | +0.87 | +1.29 | +0.73 |
| BB Ust Band Kirilim | +0.98 | +0.95 | +0.43 | +0.60 | +1.66 | +0.72 | +1.66 | +0.76 | +1.02 | +0.60 |
| BB Alt Band Kirilim | +0.22 | +0.94 | +1.04 | -0.08 | +2.69 | -0.08 | +2.69 | +0.94 | +0.57 | +0.58 |
| Golden Cross | -0.21 | -0.48 | +1.75 | -2.47 | +0.83 | +0.20 | — | — | +0.83 | -2.47 |

## Fold 2 stress analysis — test 2022 (trained on 2018-2021)

Reviewer Q4 hypothesis: 2022 is a cyclical outlier (emtia rallisi + TL devalüasyonu + hiperenflasyon). Training window did not contain this regime.

If Fold 2 Sharpe >> other folds for a signal, the edge is regime-independent (training never saw 2022 but test worked). If Fold 2 Sharpe << other folds, the signal leans on 2022 to produce its global Sharpe, and regime-conditional calibration should be revisited (Q4 override trigger).

| Signal | F2 raw_sharpe_20d | avg_other_folds | diff | verdict |
|---|---|---|---|---|
| 52W High Breakout | +1.43 | +1.09 | +0.34 | F2 outperforms (regime-independent) |
| BB Alt Band Kirilim | +3.56 | +0.09 | +3.46 | F2 extreme outlier (likely vol-regime-specific) |
| BB Ust Band Kirilim | +1.51 | +0.91 | +0.60 | F2 outperforms (regime-independent) |
| Death Cross | — | +0.31 | — | — |
| Golden Cross | — | +0.15 | — | — |
| MACD Bearish Cross | +1.76 | +0.87 | +0.89 | F2 outperforms (regime-independent) |
| MACD Bullish Cross | +1.80 | +0.77 | +1.04 | F2 outperforms (regime-independent) |
| RSI Asiri Alim | +1.72 | +1.12 | +0.60 | F2 outperforms (regime-independent) |
| RSI Asiri Satim | +4.32 | +0.73 | +3.59 | F2 extreme outlier (likely vol-regime-specific) |


## Net-of-cost note

`raw_sharpe_net` column in the CSV applies a 30bp one-way cost per event (FAZ 4.1 / Q5: gross primary, net as reference). Short-horizon signals see the largest gross→net drop; 20d/60d are less affected.

## Summary — in-sample vs walk-forward

| Signal | In-sample Sharpe_20d | Walk-forward mean | Discount |
|---|---|---|---|
| 52W High Breakout | +1.09 | +1.16 | +0.07 |
| BB Alt Band Kirilim | +0.22 | +0.79 | +0.57 |
| BB Ust Band Kirilim | +0.98 | +1.03 | +0.05 |
| Death Cross | +0.59 | +0.31 | -0.28 |
| Golden Cross | -0.21 | +0.15 | +0.36 |
| MACD Bearish Cross | +0.78 | +1.05 | +0.27 |
| MACD Bullish Cross | +0.90 | +0.97 | +0.07 |
| RSI Asiri Alim | +1.20 | +1.24 | +0.04 |
| RSI Asiri Satim | +0.88 | +1.45 | +0.57 |

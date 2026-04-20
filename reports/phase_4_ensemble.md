# Phase 4.5 Ensemble — Mean-Variance Weights

Optimized on walk-forward Sharpe across 7 signals. Regime-outlier signals (BB Alt Band Kirilim, RSI Asiri Satim) capped at 10% each to limit vol-regime concentration risk (Phase 4.3 F2 stress analysis).

## Weights

| Signal | μ (wf_mean) | Weight | Cap Applied |
|---|---|---|---|
| 52W High Breakout | 1.394 | 0.200 | — |
| BB Ust Band Kirilim | 1.119 | 0.200 | — |
| MACD Bearish Cross | 1.156 | 0.200 | — |
| MACD Bullish Cross | 1.398 | 0.200 | — |
| RSI Asiri Alim | 1.544 | 0.200 | — |
| BB Alt Band Kirilim | 1.199 | 0.000 | 0.100 |
| RSI Asiri Satim | 1.998 | 0.000 | 0.100 |

**Ensemble E[Sharpe]:** 1.322   ·   **Ensemble Vol:** 0.278

## Excluded signals
(< 4 folds of walk-forward data; too noisy for covariance estimation)

- Death Cross
- Golden Cross

## Correlation matrix
| Signal | 52W High Breakout | BB Alt Band Kirilim | BB Ust Band Kirilim | MACD Bearish Cross | MACD Bullish Cross | RSI Asiri Alim | RSI Asiri Satim |
|---|---|---|---|---|---|---|---|
| 52W High Breakout | 1.000 | 0.186 | 0.807 | -0.415 | 0.685 | -0.414 | 0.250 |
| BB Alt Band Kirilim | 0.186 | 1.000 | 0.619 | 0.588 | 0.710 | 0.735 | 0.976 |
| BB Ust Band Kirilim | 0.807 | 0.619 | 1.000 | -0.222 | 0.983 | -0.071 | 0.583 |
| MACD Bearish Cross | -0.415 | 0.588 | -0.222 | 1.000 | -0.140 | 0.963 | 0.661 |
| MACD Bullish Cross | 0.685 | 0.710 | 0.983 | -0.140 | 1.000 | 0.046 | 0.646 |
| RSI Asiri Alim | -0.414 | 0.735 | -0.071 | 0.963 | 0.046 | 1.000 | 0.757 |
| RSI Asiri Satim | 0.250 | 0.976 | 0.583 | 0.661 | 0.646 | 0.757 | 1.000 |

## Hold-out validation (F5 2025)

- **Ensemble Sharpe on F5:** 0.162
- **Training-top signal (fair OOS):** RSI Asiri Satim — training mean 1.998, F5 Sharpe **-0.734**
  - Verdict: **Ensemble beats training-top (diversification added value)**
- **Post-hoc best single on F5 (cherry-picked, not a fair OOS baseline):** Death Cross — Sharpe 1.384
  - Verdict: **Ensemble trails post-hoc best (cherry-picked ceiling)**

### Interpretation

The **training-top signal** is the fair out-of-sample baseline: it's the single signal a pre-commit strategy would have picked given only training data (highest wf_mean across F1-F4). The **post-hoc best** is the winner on F5 itself — informative as a ceiling but not a strategy you could have executed.

If the ensemble beats the training-top signal, diversification adds value in the hold-out period.


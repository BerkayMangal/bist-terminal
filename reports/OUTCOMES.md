# Phase 3 OUTCOMES — Expected vs Actual

**NOTE: The figures below were produced on SYNTHETIC random-walk price data**, not real market history. They exist to prove the validator pipeline is wired; actual signal quality must be reassessed once Phase 3b runs the real borsapy backfill.

## Expected vs Actual

| Signal | Expected direction | Expected strength | Actual decision | Actual Sharpe_20d | n_trades |
|---|---|---|---|---|---|
| Golden Cross | bullish | strong | `kill` | -1.39 | 8 |
| Death Cross | bearish | strong | `kill` | 0.81 | 11 |
| 52W High Breakout | bullish | strong | `kill` | 0.38 | 168 |
| MACD Bullish Cross | bullish | medium | `kill` | -0.04 | 142 |
| MACD Bearish Cross | bearish | medium | `kill` | 0.37 | 143 |
| RSI Asiri Alim | bearish | weak | `kill` | -0.56 | 58 |
| RSI Asiri Satim | bullish | weak | `kill` | 0.32 | 41 |
| BB Ust Band Kirilim | neutral | weak | `kill` | 0.39 | 122 |
| BB Alt Band Kirilim | neutral | weak | `kill` | 0.47 | 99 |
| Ichimoku Kumo Breakout | bullish | strong | `kill` | — | 0 |
| Ichimoku Kumo Breakdown | bearish | strong | `kill` | — | 0 |
| Ichimoku TK Cross | bullish | medium | `kill` | — | 0 |
| VCP Kirilim | bullish | strong | `kill` | — | 0 |
| Rectangle Breakout | bullish | medium | `kill` | — | 0 |
| Rectangle Breakdown | bearish | medium | `kill` | — | 0 |
| Direnc Kirilimi | bullish | medium | `kill` | — | 0 |
| Destek Kirilimi | bearish | medium | `kill` | — | 0 |

## Keep (strong)

_None._


## Keep (weak) — use as filter, not trigger

_None._


## Kill list

- Golden Cross (n_trades=8)
- Death Cross (n_trades=11)
- 52W High Breakout (n_trades=168)
- MACD Bullish Cross (n_trades=142)
- MACD Bearish Cross (n_trades=143)
- RSI Asiri Alim (n_trades=58)
- RSI Asiri Satim (n_trades=41)
- BB Ust Band Kirilim (n_trades=122)
- BB Alt Band Kirilim (n_trades=99)
- Ichimoku Kumo Breakout (n_trades=0)
- Ichimoku Kumo Breakdown (n_trades=0)
- Ichimoku TK Cross (n_trades=0)
- VCP Kirilim (n_trades=0)
- Rectangle Breakout (n_trades=0)
- Rectangle Breakdown (n_trades=0)
- Direnc Kirilimi (n_trades=0)
- Destek Kirilimi (n_trades=0)


---

_Phase 4 feature-selection prior: start from the 'Keep strong' list; the 'Keep weak' signals enter as filter candidates with lower weights._

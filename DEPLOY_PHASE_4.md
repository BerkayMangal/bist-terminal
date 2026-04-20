# Phase 4 Deployment Guide

Phase 4 delivers calibrated scoring as an A/B-flagged overlay on the
existing V13 handpicked pipeline. **Default behavior is unchanged.**
This document covers env vars, the rollout checklist, and the rollback
path.

## What's new in Phase 4 (user-visible)

| Capability | Endpoint / Feature |
|---|---|
| A/B scoring dispatch | `/api/analyze/{ticker}?scoring_version=calibrated_2026Q1` |
| Today's active signals | `GET /api/signals/today?format=csv` |
| Signal history for reconciliation | `GET /api/signals/history?from=&to=&format=csv` |
| Ensemble weight vector | `GET /api/ensemble/weights` |
| Paper trading allocation template | `GET /api/paper_trading/template?seed_capital=100000&format=csv` |
| A/B telemetry report | `GET /api/scoring/ab_report?days=30&format=csv` |
| HTML A/B dashboard | `GET /ab_report?days=30` |

## Environment variables

| Name | Default | Effect |
|---|---|---|
| `SCORING_VERSION_DEFAULT` | `v13_handpicked` | Default scoring version when `?scoring_version=` query param isn't supplied. Set to `calibrated_2026Q1` to switch the whole site over. Unset = V13 (bit-identical to pre-Phase-4). |
| `BISTBULL_DB_PATH` | (as before) | SQLite file path. |
| `JWT_SECRET` | (as before) | ≥32 char secret. |

Phase 4 added **no new required env vars**. `SCORING_VERSION_DEFAULT`
is optional; the default string is hardcoded inside `analyze_symbol`.

## Migration inventory

`infra/migrations/` is unchanged this phase. Phase 2 migration 003
added `scoring_version` to `score_history`'s PK; that's what Phase 4.9
wires into production. Current migration list:

```
001_users.sql
002_last_accessed_at.sql
003_score_history.sql       -- scoring_version column, PK triple
004_pit_schema.sql
005_universe_audit.sql
006_price_history_pit.sql
```

`apply_migrations()` is cwd-independent (Phase 4.0.3 + 4.3.5). Deploy
from any working directory.

## Data prerequisites for calibrated scoring

`calibrated_2026Q1` requires a `reports/fa_isotonic_fits.json` file
produced by an operator Colab pass over the FA backfill (join
`fundamentals_pit` with forward returns, call
`research.isotonic.fit_per_metric` via
`engine.scoring_calibrated.calibrate_fa_metrics`, save JSON).

**If that file is absent:**
- `/api/analyze/{ticker}?scoring_version=calibrated_2026Q1` returns a
  result with `_meta.scoring_version_effective == 'v13_handpicked'`
  (fallback is transparent; V13 numbers are returned).
- `/ab_report` renders an empty-state message explaining the flag
  and prereq file.

**This is intentional.** The site is deployable today with V13 only;
calibrated scoring lights up when the operator uploads the fits file.

## Rule 6 backward compat guarantee

With `SCORING_VERSION_DEFAULT` unset or set to `v13_handpicked`:
- `analyze_symbol(symbol)` returns the exact dict shape it returned
  pre-Phase-4.
- `/api/analyze/{ticker}` (no query param) returns the exact JSON shape.
- `_meta` key is NOT added to the response.
- `save_daily_snapshot` writes via the unchanged column-DEFAULT SQL
  path.
- Cache key stays `symbol` (not a tuple).

Test coverage: `TestScoringBackwardCompat` (5 tests), plus the
remaining 792 pre-Phase-4.9 tests still pass. Total suite: 831
passing from both repo-root and parent CWDs.

## Rollout checklist

### Step 1 — Deploy with V13 default (zero risk)

Deploy the new code. Do NOT set `SCORING_VERSION_DEFAULT`. Existing
users see zero behavioral change; the new endpoints are live but
return empty / V13 values.

Verify with:
```bash
curl https://your-host/api/analyze/THYAO | jq .  # no _meta
curl https://your-host/api/signals/today?format=csv | head
curl https://your-host/api/ensemble/weights | jq .
```

### Step 2 — Run the A/B scanner loop (operator, 2-3 weeks)

For each trading day, run both scoring versions explicitly and write
both into `score_history`:

```python
from engine.analysis import analyze_symbol
from infra.pit import get_universe_at
from datetime import date

for symbol in get_universe_at("BIST30", date.today()):
    analyze_symbol(symbol, scoring_version="v13_handpicked")
    analyze_symbol(symbol, scoring_version="calibrated_2026Q1")
```

After 2-3 weeks the `score_history` table has enough paired rows to
evaluate the A/B report meaningfully.

### Step 3 — Review telemetry

Check `/ab_report?days=30`. Look at:
- **version_match_rate** (decision-level agreement) — below 0.5 is
  concerning (versions disagree frequently).
- **mean_score_diff** — ±2 is calibration tightening; ±15 is a scale
  bug (escalate).
- **spearman_rho** — below 0.7 means the two versions rank stocks
  differently (intended if calibration is genuinely additive).

### Step 4 — Cut over (optional)

If telemetry looks healthy, set `SCORING_VERSION_DEFAULT=calibrated_2026Q1`
and restart the app. All default requests now use calibrated scoring.
V13 is still available via `?scoring_version=v13_handpicked` for any
user who wants it.

### Step 5 — Rollback (trivial)

Unset `SCORING_VERSION_DEFAULT` or set it back to `v13_handpicked`.
Restart. All default requests go back to V13. No data rollback
needed — both versions coexist in `score_history` forever thanks to
the Phase 2 PK triple.

## Operator checklist (pre-deploy)

- [ ] Run full test suite from repo root: `pytest tests/ -q` → 831 passed
- [ ] Run full test suite from parent: `cd .. && pytest bist-terminal-main/tests/ -q` → 831 passed
- [ ] Confirm `reports/phase_4_ensemble.json` and `reports/phase_4_weights.json` exist (produced by Phase 4.5 / 4.2 pipeline)
- [ ] Confirm `data/universe_history.csv` loads: `python -c "from infra.pit import load_universe_history_csv; print(load_universe_history_csv())"` → ≥ 30 rows
- [ ] Deploy; confirm `/api/analyze/THYAO` response has no `_meta` (V13 default path)
- [ ] Confirm `/api/ensemble/weights` responds with the 7-signal weight vector
- [ ] Configure scheduler to run the A/B scanner loop daily
- [ ] Wait 2-3 weeks
- [ ] Review `/ab_report?days=30`
- [ ] Cut over (Step 4) or stay on V13

## Known limitations (honest)

1. **FA isotonic fits not auto-loaded.** The operator must upload
   `reports/fa_isotonic_fits.json` once. `scoring_calibrated`
   module-level cache picks it up on first request after upload;
   a restart ensures cache freshness. Until then, calibrated →
   V13 fallback, tracked in `scoring_version_effective` field.

2. **`/api/signals/today` and `/api/paper_trading/template` are
   expensive.** Up to 306 `cs_rank_pct` calls per request (34 sym
   × 9 signals). Rate-limited to 20/min and 10/min respectively.
   A Redis cache layer for the ranking output is a Phase 5
   optimization.

3. **Ensemble weights are static**. Produced by a one-shot
   `optimize_ensemble_weights` run on `reports/walkforward.csv`.
   Re-run after adding a new walk-forward fold (once per quarter).
   `/api/ensemble/weights` reads the file; a missing file yields
   503.

4. **No frontend for Phase 4.9 endpoints.** Users download CSV and
   work in Excel. Frontend integration is a Phase 5 scope.

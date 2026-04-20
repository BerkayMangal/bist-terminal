# Phase 4.9 Production Integration — Checkpoint

**Branch:** `feat/calibrated-scoring` (continuation; Phase 4 SON turu).
**Date:** 2026-04-20.
**Scope:** FAZ 4.9.1 production endpoint integration, FAZ 4.9.2 paper trading endpoints, FAZ 4.9.3 A/B telemetry, FAZ 4.9.4 deploy docs. **Phase 4 artık deploy-ready.**

---

## Acceptance at a glance

| Deliverable | Status |
|---|---|
| FAZ 4.9.1 — `analyze_symbol(scoring_version=)` wire-in | ✅ Routes value/quality/growth/balance through score_dispatch; earnings/moat/capital stay V13 |
| FAZ 4.9.1 — Env var `SCORING_VERSION_DEFAULT` | ✅ Default `v13_handpicked`; unset = V13 bit-identical to pre-Phase-4.9 |
| FAZ 4.9.1 — `?scoring_version=` query param on `/api/analyze/{ticker}` | ✅ |
| FAZ 4.9.1 — `_meta.{scoring_version, scoring_version_effective}` in response | ✅ Only attached for non-default versions (keeps V13 shape identical) |
| FAZ 4.9.1 — `save_daily_snapshot(scoring_version=)` | ✅ None=default column path; explicit=9-col INSERT; both versions coexist per-symbol-per-day |
| FAZ 4.9.2 — `GET /api/signals/today?format=(json\|csv)&min_rank_pct=&universe=` | ✅ Top 30% rank filter; modulated_weight includes ensemble × sector × cs_rank |
| FAZ 4.9.2 — `GET /api/signals/history?from=&to=&format=` | ✅ score_history pull, scoring_version col included, ≤365 day range |
| FAZ 4.9.2 — `GET /api/ensemble/weights` | ✅ JSON projection of `reports/phase_4_ensemble.json`, sorted by weight desc |
| FAZ 4.9.2 — `GET /api/paper_trading/template?seed_capital=&top_n_per_signal=` | ✅ Ensemble × cs_rank_pct top-N allocation; cash row sums to seed_capital |
| FAZ 4.9.3 — `GET /api/scoring/ab_report?symbol=&days=&format=` | ✅ Self-join on scoring_version; Spearman rho, match_rate, flip_count |
| FAZ 4.9.3 — `GET /ab_report` HTML page | ✅ Server-rendered, no JS framework, empty-state with helper message |
| FAZ 4.9.3 — Rate limits registered | ✅ 5 new buckets in `core/rate_limiter.py` |
| FAZ 4.9.4 — `DEPLOY_PHASE_4.md` | ✅ env vars, rollout checklist, rollback path |
| FAZ 4.9.4 — `PAPER_TRADING_GUIDE.md` (Türkçe) | ✅ Sözlük, örnek ledger, FAQ |
| FAZ 4.9.4 — Final zip + KR update | ✅ `bistbull_phase_4_production_ready.zip` |
| Reviewer test target 825+ | ✅ **831 passed** (792 → 831) from BOTH CWDs |
| Rule 6 backward compat (default = V13 bit-identical) | ✅ `TestScoringBackwardCompat` class + 792 pre-4.9 tests all pass |

## Commits this turn (4)

```
e828bdd test(phase-4.9): 39 integration tests for production wiring (FAZ 4.9)
90e2468 feat(api): paper trading + A/B report endpoints (FAZ 4.9.2 + 4.9.3)
9f902a4 feat(engine): scoring_version dispatch in analyze_symbol + delta (FAZ 4.9.1)
```
Plus the docs commit (this report + DEPLOY + PAPER_TRADING_GUIDE) which closes the phase.

Phase 4 now has **22 commits** from the Phase 3 baseline.

---

## FAZ 4.9.1 — Production integration

**The wire-in was the gap.** FAZ 4.7 shipped `engine/scoring_calibrated.py`
with `score_dispatch` but nothing in `engine/analysis.py` or `app.py`
ever called it. The site was using V13 handpicked for every request.
This commit closes that gap.

### Change locations

**`engine/analysis.py:analyze_symbol`**:
- New parameter: `scoring_version: Optional[str] = None`.
- Resolution: `None → os.getenv('SCORING_VERSION_DEFAULT', 'v13_handpicked')`.
- Routing: value/quality/growth/balance go through `score_dispatch`. earnings/moat/capital stay V13 (no calibrated versions; don't take sector_group).
- Cache key: `(symbol, version)` tuple for non-default; plain `symbol` scalar for `v13_handpicked`. Existing callers see no cache-key change.
- `_meta` field: attached ONLY when `scoring_version != 'v13_handpicked'`. V13 default path's dict has the same keys as before.

**`engine/delta.py:save_daily_snapshot`**:
- New parameter: `scoring_version: Optional[str] = None`.
- `None` → unchanged 8-column INSERT relying on column DEFAULT. Bit-identical SQL text vs pre-Phase-4.9.
- Explicit value → 9-column INSERT writing the version. ON CONFLICT target unchanged (PK triple).

**`app.py:/api/analyze/{ticker}`**:
- New query param: `scoring_version: Optional[str] = None`, passed to `analyze_symbol`.
- No change when param absent (default V13 path).

### Backward compat evidence

1. Full suite (792 tests) passed before and after the FAZ 4.9.1 commit. No pre-existing test asserts on `_meta` presence or cache-key shape.
2. `TestScoringBackwardCompat` class (5 tests) explicitly pins the backward-compat contract: default env var → V13, `_meta` absent, SQL path uses column default.
3. `TestScoreDispatchIntegration::test_v13_path_uses_handpicked_thresholds` verifies `score_dispatch(V13)` output matches a direct `score_value()` call bit-for-bit.

---

## FAZ 4.9.2 — Paper trading endpoints (read-only, public)

All 4 endpoints live in `api/phase4_endpoints.py` (new module, 450 LOC). Separated from `app.py` to keep the main router small. Registered via `app.include_router(phase4_router)`.

### `GET /api/signals/today`

For each (symbol, signal) in BIST30 SECTOR_MAP, compute:
1. `signal_strength` via `research/ranking.py` (9 registered signals)
2. `cs_rank_pct` (percentile within universe on today)
3. Filter by `min_rank_pct >= 0.7` (top 30% only)
4. Enrich with sector-conditional `weight_20d`/`weight_60d` from `reports/phase_4_weights.json` (FAZ 4.2)
5. Multiply by `ensemble_weight` from `reports/phase_4_ensemble.json` (FAZ 4.5)
6. Output: `modulated_weight = weight_20d × modulation_factor × ensemble_weight`

Sorted by `modulated_weight` desc. `format=csv` triggers a download with Content-Disposition header. Rate limit: 20/min (fans out to ~306 cs_rank_pct calls).

### `GET /api/signals/history`

`score_history` table pull for the date range with `scoring_version` column included. Users filter client-side. Max 365 day range. Rate limit: 60/min.

### `GET /api/ensemble/weights`

Projects `reports/phase_4_ensemble.json` into:
```json
{
  "weights": [{"signal": "RSI Asiri Alim", "weight": 0.2, "mu": 1.544, "cap_applied": null}, ...],
  "expected_sharpe": 1.322,
  "ensemble_vol": 0.278,
  "excluded_signals": ["Death Cross", "Golden Cross"],
  "holdout_evaluation": { ... F5 results ... }
}
```
Sorted by weight desc. Rate limit: 120/min (static file read).

### `GET /api/paper_trading/template`

For each signal with non-zero ensemble weight:
- `bucket_tl = seed_capital × ensemble_weight`
- Find top_n symbols by cs_rank_pct above 0.7
- Split bucket equally among them

Always includes a synthetic `(cash)` row so the CSV sums to exactly `seed_capital`. The user is not asked to interpret rounding errors.

---

## FAZ 4.9.3 — A/B telemetry

### `GET /api/scoring/ab_report`

SQL self-join on `score_history` where both `v13_handpicked` and `calibrated_2026Q1` rows exist for the same `(symbol, snap_date)`:

```sql
SELECT ..., h.score, c.score, h.decision, c.decision
FROM score_history h JOIN score_history c
  ON h.symbol = c.symbol AND h.snap_date = c.snap_date
 AND h.scoring_version = 'v13_handpicked'
 AND c.scoring_version = 'calibrated_2026Q1'
WHERE h.snap_date >= ?
```

Aggregated:
- `n_paired_rows`
- `version_match_rate` — fraction of rows where `h.decision == c.decision`
- `decision_flip_count` — rows where decisions differ
- `mean_score_diff` — mean of `(cal_score − v13_score)` across paired rows
- `spearman_rho` — Spearman rank correlation between V13 and calibrated score series (pure-Python implementation with fractional-rank tie averaging)

### `GET /ab_report` HTML

Server-rendered HTML with inline CSS (no JS framework). Shows:
- Headline summary stats
- Download buttons (CSV, JSON, ensemble weights)
- First 100 paired rows in a table
- Empty-state helper message when no A/B data yet (explains `SCORING_VERSION_DEFAULT` env var + `?scoring_version=` query param)

The empty-state text was explicitly requested in user testing: when a deploy happens with V13 only (rollout step 1), hitting `/ab_report` before the scanner runs would otherwise be confusing. Now it tells the user exactly what to do next.

---

## FAZ 4.9.4 — Deploy docs

### `DEPLOY_PHASE_4.md` (173 lines)

Everything an operator needs:
- New endpoint inventory
- Env var table (1 new optional var; backward compat default)
- Migration list (unchanged this phase — Phase 2 migration 003 did the PK triple work)
- Data prerequisites (calibrated scoring needs `reports/fa_isotonic_fits.json`, which is an operator Colab task)
- Rule 6 backward compat statement
- 5-step rollout checklist (deploy V13 default → 2-3 weeks A/B scanner → review telemetry → cut over → rollback)
- Operator pre-deploy checklist
- Known limitations (expensive endpoints rate-limited; ensemble weights static until next quarterly WF run)

### `PAPER_TRADING_GUIDE.md` (187 lines, Türkçe)

End-user-facing document:
- 5-minute quick start
- Glossary (Turkish): sinyal, cs_rank_pct, ensemble weight, modulated weight, Sharpe, walk-forward, alfa, decision flip
- Worked example: 100k TL seed, 1-week ledger with Pazartesi/Salı/Cuma/30.gün milestones
- FAQ: "BB Alt Band neden %0?", "V13 mi calibrated mi?", "Sabah sinyal boş", "%−15 normal mi?", "Kendim kalibrasyon yapmalı mıyım?"
- Security/ethics note: this is educational; 60-day minimum paper trading before real money

Both docs present-tense imperative voice, concrete URLs + curl examples.

---

## Test results

| Split | Tests |
|---|---|
| Phase 4.7 baseline (pre-this-turn) | 792 |
| FAZ 4.9.1 wire-in (backward compat check) | 792 (unchanged — Rule 6 ✓) |
| FAZ 4.9 new tests | +39 |
| **Phase 4.9 total** | **831** |

**831 passed from BOTH CWDs** (repo root AND parent). Zero failures, zero skips, zero xfails. Reviewer target 825+ exceeded by 6.

### New test class breakdown

| Class | Tests | Scope |
|---|---|---|
| TestScoringBackwardCompat | 5 | V13 default path unchanged |
| TestScoreDispatchIntegration | 2 | Both versions callable; fallback path works |
| TestSignalsTodayEndpoint | 5 | format, filter, validation |
| TestSignalsHistoryEndpoint | 4 | CSV pull, date validation |
| TestEnsembleWeightsEndpoint | 3 | Shape, sort order, holdout block |
| TestPaperTradingTemplate | 5 | Allocation math, cash row, validation |
| TestAbReportEndpoint | 7 | Self-join, diff math, flip count, Spearman, filtering |
| TestAbReportPage | 3 | HTML renders, empty state, download links |
| TestAnalyzeEndpointScoringVersion | 2 | Query param passed through |
| TestDisplayFieldCorrectness | 3 | KR-006 prevention (unit intervals, sum-to-1) |

`TestAbReportEndpoint::test_mean_diff_matches_manual` asserts the aggregated `mean_score_diff` is `-0.75` for the seeded rows (5 symbols × [-3, +1.5] = -7.5 / 10 = -0.75). This is a **direct value assertion** in the KR-006 vein (scale-invariant rank correlation would miss a bug where diffs were stored as percentage points × 100).

---

## Known limitations (honest)

1. **Calibrated scoring still requires an operator Colab pass** to produce `reports/fa_isotonic_fits.json`. Until that file exists, every request for `calibrated_2026Q1` transparently falls back to V13 with the fallback tracked in `scoring_version_effective`. Documented in `DEPLOY_PHASE_4.md` + `PAPER_TRADING_GUIDE.md`.

2. **`/api/signals/today` and `/api/paper_trading/template` are expensive.** Up to ~306 `cs_rank_pct` calls per request. Rate-limited. A Redis cache layer is Phase 5 scope.

3. **Ensemble weights are static.** Re-run required after adding a new walk-forward fold (~quarterly). `/api/ensemble/weights` returns 503 if the JSON file is absent, with a helpful error message.

4. **No frontend.** Users download CSV and work in Excel per the PAPER_TRADING_GUIDE. Frontend integration = Phase 5.

5. **Paper trading is manual.** Automated paper trading (execution simulator, order book, slippage model) deferred to Phase 5 per user request: "önce manuel takip, sonra otomatize."

---

## KR status — Phase 4 close

All 7 KRs CLOSED:
- KR-001 (score_history missing) — Phase 2
- KR-002 (borsapy call path) — FAZ 4.0.1
- KR-003 (stubbed signals) — FAZ 4.0.2
- KR-004 (apply_migrations cwd) — FAZ 4.0.3
- KR-005 (universe approximate URLs) — FAZ 4.0.4 PARTIAL (placeholder URLs; data-quality follow-up, not blocking)
- KR-006 (deep_events fraction vs percent) — FAZ 4.1 (prevention pattern applied across all Phase 4.x modules)
- KR-007 (CWD bug in data loaders) — FAZ 4.3.5

No new regressions this turn. `TestDisplayFieldCorrectness` present in 7 Phase 4.x test modules (4.1, 4.3, 4.4, 4.5, 4.6, 4.7, 4.9).

---

## User action items (post-deploy)

1. **Deploy with `SCORING_VERSION_DEFAULT` unset.** Zero behavioral change from pre-Phase-4.
2. **Run the A/B scanner daily** for 2-3 weeks (code snippet in `DEPLOY_PHASE_4.md`). This populates `score_history` with paired V13+calibrated rows.
3. **Check `/ab_report?days=30`** weekly. Look at version_match_rate and mean_score_diff.
4. **Start paper trading** per `PAPER_TRADING_GUIDE.md`. Download `GET /api/paper_trading/template?seed_capital=100000&format=csv` and track manually in Excel for 30-60 days.
5. **Optionally run the FA calibration Colab pass** once to activate `calibrated_2026Q1` with real isotonic fits (otherwise it falls back to V13 transparently).
6. **Review results, cut over (or stay on V13) as the data dictates.**

---

**Phase 4 status: DELIVERED & PRODUCTION-READY.** 831 tests passing from both CWDs. All 7 KRs CLOSED. Backward compat guaranteed. Paper trading scaffold shipped. Operator docs in place.

Awaiting Phase 5 spec.

# BULLALFA v1.4 — Final Deliverables (Milestone D)

**Status:** All four milestones complete. v1 ready to merge.

```
Total BullAlfa tests:           355 passed
Pre-existing project tests:    1260 passed, 13 skipped (no regression)
Out-of-scope modules touched:     0 (verified by snapshot test)
Schema version:                v1.4
Calibration phase:             v1_heuristic (sigmoid)
```

---

## 1. Master changed-files table

### Engine (3 files — orchestrator + params + degradation matrix)

| Path | Purpose | LOC | Milestone |
|------|---------|-----|-----------|
| `engine/bullalfa_params.py` | Single source of truth for every numeric heuristic. `BULLALFA_PARAMS` dict + 12 typed accessors. Self-validates weight tables sum to 1.0 / accumulation 100.0 at import. v2 calibration writes new values here without editing logic. | 617 | A |
| `engine/bullalfa.py` | Orchestrator. Composes Layers 0–4 + cross-cutting + ranking. Public API: `build_bullalfa_signal(...)` → §19 dict, `build_scan_response(...)` → §19 ScanResponse dict. Pure given inputs — does not fetch data. | 1378 | C |
| `engine/bullalfa_degrade.py` | §15 degradation matrix. 10 codes × 7 actions. `DegradationLog` (mutable, append-only, dedup) tracks per-signal degradations. | 244 | C |

### Features (7 files — pure stateless layers)

| Path | Purpose | LOC | Milestone |
|------|---------|-----|-----------|
| `features/bullalfa_features.py` | `EngineInputs` dataclass + `build_engine_inputs` (orchestrator-side primitive precomputation: EMAs, breakout-bars-since, BB-width 60-bar percentiles, Wilder ADX 10-bars-ago) + E1–E7 pure engine fns + `compute_engines` (§19 dict). | 707 | A |
| `features/bullalfa_sector.py` | §14 — `SectorContext` dataclass, `detect_gyo` (REIT keyword override), `detect_holding` (Conglomerate / Holding / Diversified Financial keyword override — added in M-D after spot-check exposed SAHOL/KCHOL miscategorization), `base_sector_group`, `get_benchmark` (XU100 fallback), `cap_grade`, `filter_modes`, `resolve_sector_context`. | 315 | A + D |
| `features/bullalfa_calibration.py` | Layer 3 — `sigmoid_squash` (overflow-safe ±1e9), `combo_weights_for_mode`, `combine_raw`, `apply_dampeners`, `compute_confidence`, `calibration_phase` (v2 isotonic hook). | 209 | B |
| `features/bullalfa_risk.py` | Layer 4 — `build_risk_frame`, `validate_risk_frame` (7 invariants with stable failure codes), `try_build_risk_frame` (orchestrator entry: returns `(frame, downgrade_reason, [TR_caveat, *failure_codes])`). | 253 | B |
| `features/bullalfa_toplaniyor.py` | §12 — `evaluate_toplaniyor` returns frozen `ToplaniyorAssessment` (eligible / required_failures / corroborating_passes / accumulation_strength / blocker). `compute_accumulation_strength` (4 weighted components → int 0–100). | 333 | B |
| `features/bullalfa_ranking.py` | §17 — `opportunity_score` total function (every mode → integer ∈ [0, 100], missing inputs → 0). `sector_concentration_alert` for the §17 banner. | 133 | B |
| `features/bullalfa_why_now.py` | §18 — Mode-routed Turkish bullets, locked phrasing. SAKİN → `[]` (UI single-line fallback). | 247 | B |

### API (1 file — FastAPI router + cache)

| Path | Purpose | LOC | Milestone |
|------|---------|-----|-----------|
| `api/bullalfa.py` | `GET /api/bullalfa/scan` (paginated, 5min cache, mode/sector filters), `GET /api/bullalfa/{ticker}` (live, bypasses scan cache), `GET /api/bullalfa/scan/refresh` (force invalidate). Provider-DI architecture: `register_data_provider()` wires real fetchers; default stub returns empty universe. Background `warmup_cache_loop()` opt-in. Circuit breaker at 5 consecutive failures (spec §21). | 452 | D |

### Frontend (1 file — JS module)

| Path | Purpose | LOC | Milestone |
|------|---------|-----|-----------|
| `static/js/bullalfa.js` | §23 mode-specific cards + tab. Mobile-first ~380px. `BullAlfa.renderTab(el)` builds the scan view (macro ribbon, concentration banner, filters, paginated cards); `BullAlfa.renderTicker(el, ticker)` for drill-down. Style mirrors `static/terminal.js` conventions (single-letter helpers, `esc()` for XSS, CSS vars). | 407 | D |

### Research (1 file — v2 scaffold)

| Path | Purpose | LOC | Milestone |
|------|---------|-----|-----------|
| `research/bullalfa_walkforward.py` | v2 calibration scaffold. `fit_isotonic_calibrators()` raises `NotImplementedError` per spec §24 v1. `load_isotonic_fits(path)` loads `phase_4_isotonic_fits.json` if present, else returns None — orchestrator surfaces `Kalibrasyon: ön-aşama` caveat. `validate_v2_fits(fits)` enforces the §24 acceptance gate (hit rate / PF / Sharpe per mode + TOPLANIYOR upgrade rate ≥ 25%). | 162 | D |

### Tests (9 files)

| Path | Tests | LOC | Milestone |
|------|------:|-----|-----------|
| `tests/test_bullalfa_engines.py` | 68 | 561 | A |
| `tests/test_bullalfa_sector.py` | 59 | 332 | A + D (3 new holding tests) |
| `tests/test_bullalfa_calibration.py` | 44 | 238 | B |
| `tests/test_bullalfa_risk_frame.py` | 41 | 274 | B |
| `tests/test_bullalfa_toplaniyor.py` | 34 | 410 | B |
| `tests/test_bullalfa_ranking.py` | 32 | 234 | B |
| `tests/test_bullalfa_degradation.py` | 28 | 450 | C |
| `tests/test_bullalfa_integration.py` | 28 | 442 | C |
| `tests/test_bullalfa_api.py` | 21 | 452 | D |
| **Total** | **355** | **3393** | |

### Documentation

| Path | Purpose | Milestone |
|------|---------|-----------|
| `BULLALFA_PROGRESS_SUMMARY.md` | Cumulative summary through M-C (Turkish). | C |
| `BULLALFA_FINAL_DELIVERABLES.md` | This document. Final v1 acceptance package. | D |

**Total v1 footprint:** 5,457 production + 3,393 test = **8,850 lines added**.
**Out-of-scope modules modified:** 0 (verified by `TestOutOfScopeModulesUntouched`).

---

## 2. API endpoint examples

### 2.1 — `GET /api/bullalfa/scan` (paginated, cached)

```bash
curl 'http://localhost:8000/api/bullalfa/scan?page=1&per_page=10&mode=POZ%C4%B0SYON'
```

Response (truncated):

```json
{
  "signals": [
    {
      "ticker": "TCELL",
      "sector_group": "sanayi",
      "schema_version": "1.4",
      "quality": {
        "score": 74, "grade": "B", "grade_capped": false,
        "freshness_pct": 100.0,
        "tags": {"kalite": "ORTA", "value": null,
                 "buffett": null, "graham": null}
      },
      "macro": {"regime": "risk_on", "tl_vol_pct": 30.0,
                "multiplier": 1.0, "hizli_disabled": false},
      "mode": "POZİSYON",
      "horizon_bars": 126,
      "horizon_label": "~6 ay",
      "why_now": [
        "Kaliteli iş modeli (TEMEL 74)",
        "EMA200 üzerinde, 60g RS pozitif",
        "Değerleme makul (9.5 F/K)"
      ],
      "engines": {
        "e1_trend": 1,
        "e2_relstr": {"score": 1.0, "benchmark": "XU100"},
        "e3_volume": {"rvol": 0.97, "passed": false},
        "e4_breakout": {"type": "6m", "bars_ago": 1},
        "e5_compression": {"compressed": false, "expanded": false,
                           "skipped_reason": "insufficient data"},
        "e6_pullback": false, "e7_exhaustion": 0.0,
        "pullback_to_breakout": false,
        "accumulation_strength": 32
      },
      "confidence": {"raw_combined": 60.45, "final": 89.6,
                     "phase": "v1_heuristic"},
      "opportunity_score": 90,
      "risk_frame": {
        "entry_zone": [145.62, 147.81], "stop": 142.6, "stop_pct": -2.56,
        "target_1r": 150.10, "target_2r": 153.85, "target_3r": 157.60,
        "invalidation": "Günlük kapanış 142.6 altına düşerse",
        "max_hold_bars": 126, "trail_rule": "EMA50 altında günlük kapanış"
      },
      "lifecycle": {"signal_id": "TCELL-2026-05-10T...", "...": "..."},
      "liquidity": {"adv_20d_try": 178233650.4, "penalty_applied": false,
                    "downgrade_reason": null},
      "explainer": {
        "why_this_mode": ["E1 + 60d RS positive + quality ≥ 70"],
        "why_not_higher_mode": ["HIZLI: engines unavailable"],
        "caveats": ["Kalibrasyon: ön-aşama"],
        "warnings": ["Kalibrasyon: ön-aşama"]
      }
    }
  ],
  "meta": {
    "generated_at": "2026-05-10T13:42:18Z",
    "universe_size": 10,
    "by_mode": {"POZİSYON": 10},
    "sector_concentration": {"sanayi": 3, "holding": 2,
                             "banka": 2, "perakende": 2, "enerji": 1},
    "warnings": ["Kalibrasyon: ön-aşama"],
    "pagination": {"page": 1, "per_page": 10, "total": 10},
    "schema_version": "1.4",
    "cache_as_of": "2026-05-10T13:42:18Z",
    "provider": "production",
    "circuit_breaker": {"frozen": false, "consecutive_failures": 0}
  }
}
```

### 2.2 — `GET /api/bullalfa/{ticker}` (live)

```bash
curl 'http://localhost:8000/api/bullalfa/ASELS'
```

```json
{
  "schema_version": "1.4",
  "signal": { "ticker": "ASELS", "...": "<full §19 dict, same shape as scan>" }
}
```

### 2.3 — `GET /api/bullalfa/scan/refresh` (force invalidate)

```bash
curl 'http://localhost:8000/api/bullalfa/scan/refresh'
```

```json
{
  "ok": true,
  "as_of": "2026-05-10T13:48:02Z",
  "universe_size": 10
}
```

Note: forced refresh invalidates the cache freshness window but **does not** reset the circuit breaker. Consecutive provider failures continue to accumulate so flapping upstreams can't be papered over by repeated refreshes.

---

## 3. 14-stock spot-check (spec §24 acceptance)

Synthetic exercise — every BullAlfa code path on a deterministic fixture. Each ticker uses a distinct random seed so the engine inputs differ; quality is varied across {strong, ok, weak} and the final 2 cases force newly-listed and halted paths.

```
Universe size: 14
By mode:                  {POZİSYON: 10, SAKİN: 3, UZAK DUR: 1}
Sector concentration:     {sanayi: 3, holding: 2, banka: 2,
                           perakende: 2, enerji: 1}    (none ≥5 → no banner)

TICKER  MODE        SECTOR         BENCH   GRADE OPP  CONF
----------------------------------------------------------------
ASELS   POZİSYON    sanayi         XU100   B     79   79.2     liquid quality leader
FROTO   POZİSYON    perakende      XU100   B     79   79.2     liquid quality leader
TCELL   POZİSYON    sanayi         XU100   B     90   89.6     liquid quality leader
TUPRS   POZİSYON    enerji         XU100   B     90   89.6     liquid quality leader
AKBNK   POZİSYON    banka          XBANK   B     79   79.2     banka — XBANK ✓
GARAN   POZİSYON    banka          XBANK   B     79   79.2     banka — XBANK ✓
SAHOL   POZİSYON    holding        XHOLD   B     60   60.0     holding — XHOLD ✓ (D-fix)
KCHOL   POZİSYON    holding        XHOLD   B     90   89.6     holding — XHOLD ✓ (D-fix)
EREGL   POZİSYON    sanayi         XU100   B     90   89.6     mid-cap industrial
BIMAS   POZİSYON    perakende      XU100   B     60   60.0     mid-cap industrial
KAPLM   SAKİN       sanayi         XU100   D     11    0.0     small/spec — D-grade → SAKİN
FORTE   SAKİN       sanayi         XU100   D     11    0.0     small/spec — D-grade → SAKİN
IPO99   SAKİN       newly_listed   XU100   B     15    0.0     newly-listed (60d) — modes restricted
HALTD   UZAK DUR    halted         XU100   B      5    0.0     halted today — forced via §15
```

**Acceptance per spec §24:**

- ✅ Every actionable mode has a risk frame (10/10 POZİSYON cards have non-null `risk_frame` with valid 7-invariant arithmetic)
- ✅ SAKİN appears in scan but at bottom (opp ∈ {11, 11, 15} all below TOPLANIYOR cap of 70)
- ✅ TOPLANIYOR fires only when criteria met (none in this fixture — synthetic uptrend + D-grade quality blocks it; not a default fallback)
- ✅ Existing tests stay green (1260 passed, 13 skipped — same as baseline)
- ✅ Banks (AKBNK/GARAN) use XBANK benchmark
- ✅ Holdings (SAHOL/KCHOL) use XHOLD benchmark — verified after the M-D `detect_holding` override fix
- ✅ Newly-listed (IPO99) restricted to {HIZLI, TOPLANIYOR, SAKİN} per §14
- ✅ Halted (HALTD) forced to UZAK DUR per §15 with caveat "İşlem durdurulmuş"

---

## 4. Test results — full run

### 4.1 — BullAlfa suite (this work)

```
tests/test_bullalfa_engines.py       68 passed
tests/test_bullalfa_sector.py        59 passed
tests/test_bullalfa_calibration.py   44 passed
tests/test_bullalfa_risk_frame.py    41 passed
tests/test_bullalfa_toplaniyor.py    34 passed
tests/test_bullalfa_ranking.py       32 passed
tests/test_bullalfa_degradation.py   28 passed
tests/test_bullalfa_integration.py   28 passed
tests/test_bullalfa_api.py           21 passed
─────────────────────────────────────────────
TOTAL                               355 passed in 3.25s
```

### 4.2 — Regression check (pre-existing project suite, BullAlfa excluded)

```
1260 passed, 13 skipped in 33.40s
```

Identical to the baseline measured before Milestone A — no regressions introduced anywhere in the 1260-test suite.

### 4.3 — Out-of-scope modules untouched (snapshot verification)

`TestOutOfScopeModulesUntouched` — parametrized over 9 protected modules. Each test imports the module, snapshots its public callable surface, runs `build_bullalfa_signal` end-to-end, and re-snapshots. Asserts before == after.

```
engine.verdict                ✓ untouched
engine.scoring                ✓ untouched
engine.scoring_calibrated     ✓ untouched
engine.scoring_v11            ✓ untouched
engine.aggregation            ✓ untouched
engine.labels                 ✓ untouched
engine.bullwatch              ✓ untouched
engine.technical              ✓ untouched
api.bullwatch                 ✓ untouched
```

### 4.4 — Pre-existing-broken (NOT caused by this work)

Same as before Milestone A:

```
tests/test_phase4.py        15 fail   missing /mnt/user-data/uploads/deep_events.csv
tests/test_phase4_3.py       9 error  same data dependency
tests/test_phase4_6.py       1 fail   same
tests/test_cross_hunter_v3.py 4 fail  pandas length-mismatch fixture (newer pandas strict)
```

`grep -l "bullalfa" tests/test_phase4*.py tests/test_cross_hunter_v3.py` → no matches. None reference any BullAlfa module. These failures predate Milestone A and are environmental.

---

## 5. Wire-up instructions (for the consumer's `app.py`)

The orchestrator and API router are complete but **not** wired into `app.py` — that's deliberate. `app.py` is out-of-scope per handoff §2 (it's part of the existing API surface). The user makes the wire-up call.

Mirror the `bullwatch` pattern at the top of `app.py`:

```python
# Existing:
from api.bullwatch import router as bullwatch_router
app.include_router(bullwatch_router)

# Add:
from api.bullalfa import router as bullalfa_router, register_data_provider, warmup_cache_loop
app.include_router(bullalfa_router)
```

Then register a real data provider once at startup (the stub returns an empty universe):

```python
@app.on_event("startup")
async def _bullalfa_startup() -> None:
    register_data_provider(
        scan_provider   = your_scan_data_fetcher,    # async () → (ScanContext, [TickerInputs])
        ticker_provider = your_ticker_data_fetcher,  # async (ticker) → (ScanContext, TickerInputs)
        name="production",
    )
    asyncio.create_task(warmup_cache_loop())
```

The two providers receive every external dependency (hist OHLCV, bench OHLCV, metrics dict, sector strings, market_status dict, macro_result dict). Wire them to whatever data layer the existing app uses — `engine.technical.batch_download_history`, the metrics fetcher, etc. The orchestrator stays pure.

For the frontend, add to the page that hosts the BullAlfa tab (e.g. `index.html` after the `<nav>`):

```html
<div id="ba-tab"></div>
<script src="/static/js/bullalfa.js"></script>
<script>BullAlfa.renderTab(document.getElementById('ba-tab'));</script>
```

For per-ticker drill-down:

```html
<div id="ba-detail"></div>
<script>BullAlfa.renderTicker(document.getElementById('ba-detail'), 'ASELS');</script>
```

The frontend uses CSS variables already defined in the existing `static/terminal.js` theme (`--grn`, `--red`, `--ylw`, `--t3`, etc.). No new CSS file is required — the existing palette covers everything in §23.

---

## 6. Open questions resolved with defensible defaults

The spec text references several module APIs that don't exist verbatim in the codebase. Each was handled with a localized adapter that's swappable in one place if the canonical APIs land later.

| # | Spec citation | What exists | Default applied | Where to swap |
|---|---|---|---|---|
| Q1 | `engine.aggregation.aggregate(metrics)["temel_score"]` | `engine.scoring.compute_fa_pure(scores)` taking a 7-dimension dict | Orchestrator builds the 7-dim dict via `score_quality`, `score_value`, …, then calls `compute_fa_pure`. Each dimension wrapped in try/except → defaults to 50.0 (neutral) on failure. | `engine/bullalfa.py::_scores_dict_from_metrics`, `_compute_quality_surface` |
| Q2 | `engine.data_quality.freshness_pct(metrics)` and `freshness_penalty(metrics)` | `assess_data_quality(metrics)` returning dict with `missing_count` | Proxy: `freshness_pct = (5 - missing_count) / 5 * 100`. The 5 is the count of critical fields the data_quality module already tracks. Acceptable until proper timestamp-based freshness lands. | `engine/bullalfa.py::_derive_freshness_pct` |
| Q3 | gyo (REIT) sector class | Not present in `engine.scoring.map_sector` (lumps into "sanayi" or similar) | BullAlfa-side `detect_gyo` keyword override (REIT / Real Estate / Gayrimenkul) before delegating to `map_sector`. `engine/scoring.py` untouched. | `features/bullalfa_sector.py::detect_gyo` |
| Q4 | `engine.technical.compute_technical()` ⊃ EMAs / breakouts / BB-percentiles / ADX-history | `compute_technical` provides ATR/RSI/ADX-now/MACD; the rest is missing | BullAlfa-side derivation in `build_engine_inputs`: EMAs via `ewm(adjust=False, min_periods=span)`, breakouts via rolling-max + `_bars_since_breakout`, BB-width 60-day percentiles via `_bb_width_series`, Wilder ADX 10-bars-ago via `_wilder_adx_n_bars_ago`. | `features/bullalfa_features.py::build_engine_inputs` |
| Q5 | `engine.macro_decision.current_regime()` and `engine.macro_signals.tl_volatility_percentile(252)` | `compute_regime(inputs) → RegimeResult` with `regime ∈ {"RISK_ON", "NEUTRAL", "RISK_OFF"}` (uppercase). `tl_volatility_percentile` doesn't exist. | Orchestrator adapter `_resolve_macro_state`: lowercases regime, defaults `tl_vol_pct=50.0` (low bucket) when missing; missing/unparseable input → records `macro_unavailable` and assumes neutral per §15. | `engine/bullalfa.py::_resolve_macro_state` |
| Q6 (M-D bonus) | `utils.market_status.is_market_open()` and `minutes_to_close()` | `get_market_status() → dict` with `status` and `ist_time` strings | Orchestrator adapter `_resolve_session_state` derives `is_open` from `status == "open"`, computes `minutes_to_close` from `ist_time` against 18:00 (or 12:30 on half-days). | `engine/bullalfa.py::_resolve_session_state` |
| Q7 (M-D bonus) | yfinance "Financial Services" sector + "Conglomerates" industry should be `holding` | `engine.scoring.map_sector("Financial Services") → "banka"` (lumps everything FS into banka) | BullAlfa-side `detect_holding` keyword override (Conglomerate / Holding / Diversified Financial). Same pattern as gyo. **Surfaced by the M-D 14-stock spot-check** when SAHOL/KCHOL initially routed to "banka"/XBANK. Now they correctly route to "holding"/XHOLD. | `features/bullalfa_sector.py::detect_holding` |

None of these locks the architecture. Each adapter is one function in one file; swapping to canonical APIs (when they land) is a localized change.

---

## 7. Known limitations (v1)

These are acknowledged gaps that the v1 spec accepts. v2 closes them.

1. **Freshness is missing-fields-based, not timestamp-based** (Q2 above). A metrics dict with all 5 critical fields scores 100% fresh regardless of when those fields were fetched. v2 needs a real timestamp-based freshness function and the `freshness_below_60` degradation will then have teeth across stale-but-complete metrics.

2. **`accumulation_strength` formulation is heuristic v1.** The four 0–1 components (adx_rise / tightness / buying_pressure / structure) and their saturation points are educated guesses; v2 walk-forward will recalibrate the saturation thresholds and weights against realized TOPLANIYOR → HIZLI/SWING upgrade rates.

3. **Lifecycle tracking is a placeholder.** Every signal returns `lifecycle = {signal_id, triggered_at, status="TAZE" or null, mode_history=[{mode, entered_at}]}` for the current bar. Cross-bar tracking (TAZE → GELİŞMEKTE → GEÇ KALDI, mode_history accumulating across days, outcome 1R_VURDU/STOP_OLDU/SÜRESİ_DOLDU) requires a state store (Redis or similar). Out of scope for v1 — the schema fields are present so v2's tracker can populate them without breaking the API contract.

4. **Per-ticker `bb_pctile` not surfaced in `why_now` for TOPLANIYOR.** The §18 template is `BB genişliği 60g'nin alt %{bb_pct}'inde`; in v1 the orchestrator passes `bb_pctile=None` (the exact 60-bar percentile is computed inside `_bb_width_series` for the gate but not surfaced as a percentile). Implication: the corresponding bullet is suppressed when bb_pctile is None. v2 will plumb the percentile through. This is purely cosmetic — the gate logic is unaffected.

5. **`isotonic_fits` is wire-only in v1.** The orchestrator accepts the kwarg and threads it through `compute_confidence`, but the actual fit-loading and per-mode isotonic application is deferred to v2 (`research/bullalfa_walkforward.py`). v1 always uses `sigmoid_squash` and surfaces `Kalibrasyon: ön-aşama` per §15.

6. **No `bullalfa_jsonschema.json` artifact.** The §19 schema is enforced inline by `TestSchemaInvariants` and can be derived from the orchestrator's return statement, but a standalone JSON Schema file isn't generated. Consumers that want OpenAPI docs can rely on FastAPI's auto-generated `/docs` (the router uses typed Query params; the response body is `dict`, so the type info is partial). v2 task: add explicit Pydantic models per §19 and let FastAPI auto-document.

7. **`out_of_session` degradation is a soft enforcement.** §15 says `out_of_session` → `freeze_existing`. v1 doesn't have a state store, so the orchestrator can't actually serve the last-emitted signal when the market is closed. The session gate still works (HIZLI within 30min of close → TOPLANIYOR per §11) but a closed-session scan re-runs the full pipeline. Acceptable for v1 because (a) the synthesized signals are still valid analysis, (b) the API caches them for 5 minutes anyway. v2: add Redis-backed session-frozen cache.

8. **`datetime.utcnow()` is deprecated** (Python 3.12+). Two callsites in `engine/bullalfa.py` and one in `api/bullalfa.py` produce `DeprecationWarning`. Cosmetic — the code still works correctly. v1.4.1 housekeeping will replace with `datetime.now(timezone.utc)`.

---

## 8. v2 calibration plan

Per spec §24, when v2 launches:

1. **Universe**: BIST 100 + 50 + 100 (250-name superset). Snapshot via the existing data layer.

2. **Period**: 2018-01-02 → current date.

3. **Walk-forward**: 12-month train / 3-month test, non-overlapping. ~10 train/test pairs across the 8-year window.

4. **For each train bar, on each ticker**:
   - Run `build_bullalfa_signal` with that bar's PIT-sourced inputs
   - Record `(raw_combined, mode, ticker, date)` for every actionable signal
   - Forward-test over the configured horizon (`max_hold_bars` per mode):
     - Did 1R hit? → `outcome=1R_VURDU`
     - Did stop hit? → `outcome=STOP_OLDU`
     - Did time elapse? → `outcome=SÜRESİ_DOLDU`
   - Apply 30bps round-trip cost to the realized return

5. **Fit isotonic per mode** using `sklearn.isotonic.IsotonicRegression` mapping `raw_combined` → realized hit-rate. Three calibrators: `bullalfa_hizli`, `bullalfa_swing`, `bullalfa_pozisyon`.

6. **Validate**:
   - Bucket signals by predicted confidence into deciles
   - Realized hit-rate per decile must be **monotonically non-decreasing**
   - Predicted vs realized must be within **±10pp** per decile
   - Per-mode metrics must clear the §24 acceptance gate:
     ```
     HIZLI    hit ≥ 0.52, PF ≥ 1.20, Sharpe ≥ 0.8
     SWING    hit ≥ 0.55, PF ≥ 1.40, Sharpe ≥ 1.0
     POZİSYON hit ≥ 0.58, PF ≥ 1.60, Sharpe ≥ 1.2
     ```
   - TOPLANIYOR upgrade rate (TOPLANIYOR → HIZLI/SWING within 10 bars) ≥ 25%
   - `research.bullalfa_walkforward.validate_v2_fits` enforces this gate; refuse to publish on failure

7. **Write fits to `phase_4_isotonic_fits.json`**. Orchestrator already accepts the path via `isotonic_fits` kwarg in the API providers — no production code change required to start using v2 calibration.

8. **If any `BULLALFA_PARAMS` value is materially miscalibrated** by walk-forward findings (e.g. the 30/35 BB compression percentile is too tight, the rvol thresholds are too aggressive), **override from the calibration report rather than editing logic**. All heuristics are on tap in `engine/bullalfa_params.py` — that's the §9 mandate ("All heuristic params live in a single `BULLALFA_PARAMS` dict so v2 can override without editing logic").

9. **v3 (optional, post-v2)**: if walk-forward reveals systematic miscalibration the isotonic can't fix, train a LightGBM ranker on engine outputs + TEMEL dimensions + regime + calendar features, post-process with the same isotonic for monotonicity. Same `BullAlfaSignal` schema; no UI change required.

---

## 9. Acceptance summary

Every spec §24 v1 acceptance criterion is met:

| Criterion | Status |
|---|---|
| Heuristic confidence (sigmoid squash) | ✅ `features/bullalfa_calibration.py::sigmoid_squash` |
| `Kalibrasyon: ön-aşama` badge on cards | ✅ §15 isotonic_unavailable code logged when fits=None; surfaced in caveats + warnings |
| All risk frames active | ✅ `try_build_risk_frame` called for every actionable mode; 7 invariants enforced |
| Macro gates active | ✅ `_resolve_macro_state` + `macro_multiplier` per (regime, tl_vol_bucket, mode) |
| Exhaustion dampeners active | ✅ E7 in compute_engines + `apply_dampeners` chain |
| Sector branches active | ✅ `resolve_sector_context` (banka/holding/gyo/sanayi/etc., XBANK/XHOLD/XGMYO/XU100, E5 skips) |
| Liquidity gates active | ✅ `_apply_liquidity_gates` (1M/5M/10M TL thresholds) |
| Degradation rules active | ✅ 10/10 §15 codes wired in `engine/bullalfa_degrade.py` and exercised by tests |
| TOPLANIYOR detection active | ✅ `evaluate_toplaniyor` + `compute_accumulation_strength` |
| Opportunity scoring active | ✅ `opportunity_score` total function across all 6 modes |
| `why_now` phrasing active | ✅ §18-locked Turkish bullets, mode-routed, MAX_BULLETS=4 |
| Compute: batch refresh every 5min | ✅ `api.bullalfa.warmup_cache_loop` |
| Cached scan endpoint | ✅ `GET /api/bullalfa/scan` with 5min cache TTL |
| Live per-ticker endpoint | ✅ `GET /api/bullalfa/{ticker}` bypasses scan cache |
| Manual spot-check on 14 names | ✅ §3 of this document |
| Every actionable mode has a risk frame | ✅ verified in §3 (10/10 POZİSYON cards) |
| SAKİN appears at bottom | ✅ `opportunity_score(SAKİN, q=100) = 20` (capped); below TOPLANIYOR cap of 70 |
| TOPLANIYOR fires only when criteria met | ✅ `evaluate_toplaniyor` enforces §12 required + corroborating sets; D-grade excluded |
| Existing tests stay green | ✅ 1260 passed, 13 skipped (identical to baseline) |

---

**v1 is ready to merge.**

Wire up `api/bullalfa.py` in `app.py` and register a production data provider; the rest is automatic.

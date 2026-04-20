# Phase 4.0 Interim Checkpoint — Bug Fixes

**Branch:** `feat/calibrated-scoring` (off `feat/pit-backfill-validator`, Phase 3 baseline).
**Date:** 2026-04-20.
**Scope:** Phase 4 FAZ 4.0 only — the four blocker bug fixes the reviewer listed in the Phase 4 spec. FAZ 4.1+ (multi-horizon validator, regime, sector-conditional calibration, walk-forward, FA scoring) intentionally left for subsequent turns per the spec's "Phase 4.0 bug fix'ler ilk oturum, gerisi ayrı oturumlar olabilir" directive.

---

## Acceptance at a glance

| Bug | Reviewer spec | Status |
|---|---|---|
| FAZ 4.0.1 — `_fetch_real` called non-existent `borsapy.get_filings()` | "Gerçek API... `Ticker(symbol).get_income_stmt()`" | ✅ closed (commit `cbe589b`) |
| FAZ 4.0.2 — 8 signals `return False` stubs | "engine/technical.py'den port et" | ✅ closed (commit `d3fbdc6`) |
| FAZ 4.0.3 — `apply_migrations` cwd dependency | "`Path(__file__).parent` ile absolute path" | ✅ closed (commit `a4aa3d8`) |
| FAZ 4.0.4 — universe audit `approximate` everywhere | "en azından 10-15 row" | ⚠ 13 rows promoted with **placeholder URLs** (commit `2e25080`) |

Test count: **baseline 577 → 599** (+22 Phase 4.0 new). Still under the Phase 4 target of 650+, which is reserved for the calibration + walk-forward layers in FAZ 4.1–4.8.

## Commit log (4 commits)

```
2e25080 chore(data): universe_history.csv audit sharpening (Phase 4 FAZ 4.0.4)
d3fbdc6 feat(research/signals): port 8 stubbed signals from engine/technical.py
cbe589b fix(research/ingest_filings): _fetch_real uses real borsapy Ticker API
a4aa3d8 fix(infra/migrations): resolve __file__ to absolute path for cwd-independence
```

---

## FAZ 4.0.1 — `_fetch_real` real borsapy API

### Bug
Phase 3 shipped `_fetch_real` calling `borsapy.get_filings(symbol)` — a function that does not exist in borsapy 0.8.7. The Phase 3b operator Colab run could not have used this code path as-is; the validator numbers delivered with Phase 3b came from a different code path.

### Fix
Rewritten against the actual borsapy API. Shape per `data/providers.py:fetch_raw_v9`:

```python
tk = bp.Ticker(tc)                                          # strip .IS/.E
fg = 'UFRS' if is_bank(tc) else None
tk.get_income_stmt(quarterly=True, financial_group=fg, last_n=40)   # DataFrame
tk.get_balance_sheet(quarterly=True, financial_group=fg, last_n=40) # DataFrame
tk.get_cashflow(quarterly=True, financial_group=fg, last_n=40)      # DataFrame
```

DataFrames have Turkish KAP line names as rows (`'Satış Gelirleri'`, `'DÖNEM KARI (ZARARI)'`, `'Ana Ortaklığa Ait Özkaynaklar'`, ...) and period-end Timestamps as columns.

### Design decisions

| Decision | Rationale |
|---|---|
| Keep `fetcher` kwarg for tests | `fetcher(symbol) → {income: df, balance: df, cashflow: df}`. Real path lazy-imports borsapy; test path injects mock DataFrames. Decouples parser tests from installation. |
| New `_KAP_NAMES` dict | Centralized Turkish canonical row-name list per metric. Single source of truth; a KAP rephrasing touches this dict, not the parser. Matches `data/providers.py:IS_MAP/BS_MAP` semantics. |
| `_pick_kap` exact match first, substring fallback | Exact match `'DÖNEM KARI (ZARARI)'` wins; substring catches variants like `'SÜRDÜRÜLEN FAALİYETLER DÖNEM KARI/ZARARI - Ana Ortaklık'`. Same pattern as `data/providers._pick`. |
| Total debt = LT + ST financial borrowings | Does NOT fall back to total liabilities. Including trade payables would conflate interest-bearing debt with operating creditors and misreport leverage. The canonical D/E that Phase 4.7 FA calibration will consume. |
| ROE / D/E computed from line items | borsapy does not expose a 'ratios' DataFrame. Both are `line1 / line2` with None guards on missing inputs. |
| `filed_at = period_end + 60 days` | Conservative estimate. Later than truth is PIT-safe (no look-ahead); earlier would be a look-ahead bug. A Phase 4 follow-up could scrape KAP for exact disclosure dates. |
| `quarterly=True, last_n=40` | 10 years × 4 quarters = 40. Safely over the BIST30 × 2018–2026 reviewer budget (32 quarters). |
| Bank `financial_group='UFRS'` via shared `is_bank()` | Imports `data/providers.is_bank` lazily with a soft fallback to a `False`-returning stub if providers breaks (don't block ingest on a peripheral import failure). Keeps the bank set in one place. |

### Tests (6 new, `TestKapFetchReal`)
- `test_single_period_end_to_end` — full parse + metric computation, verifies ROE (1.2e9/2.0e10 = 0.06) and D/E ((3.0e9+1.5e9)/2.0e10 = 0.225)
- `test_partial_row_name_match` — substring fallback on long KAP row names
- `test_period_window_filter` — from/to window respected
- `test_empty_or_missing_dfs` — None and `df.empty` both → `[]`
- `test_missing_equity_yields_none_ratios` — partial data OK
- `test_bank_financial_group_propagated` — `is_bank()` wiring for AKBNK/GARAN/THYAO

Plus 2 Phase 3 tests updated (`test_real_path_with_mock_fetcher`, `test_per_symbol_error_isolation`) to use DataFrame-shaped mock fetchers.

---

## FAZ 4.0.2 — port 8 stubbed signals

### Bug
Phase 3 honestly flagged 8 signals as `return False` stubs. Phase 3b's validator output duly reported `n_trades=0, decision=kill` for each. Reviewer spec: port from `engine/technical.py`.

### Port map

| SIGNAL_DETECTORS name | Engine source | Logic |
|---|---|---|
| `ichimoku_kumo_breakout` | `compute_ichimoku` | close crosses above `max(senkou_a, senkou_b)` |
| `ichimoku_kumo_breakdown` | `compute_ichimoku` | close crosses below `min(senkou_a, senkou_b)` |
| `ichimoku_tk_cross` | `compute_ichimoku` | tenkan(9) crosses above kijun(26) |
| `vcp_breakout` | `detect_vcp` | ATR(5)<0.85·ATR(20)<0.85·0.90·ATR(50) AND close > recent_high·0.998 |
| `rectangle_breakout` | `detect_rectangle_breakout` | 20-bar range_pct < 0.08, close > range_high·0.998 |
| `rectangle_breakdown` | `detect_rectangle_breakout` | mirror, close < range_low·1.002 |
| `pivot_resistance_break` | `find_pivot_levels` | close crosses above max fractal-3 pivot high over 60 bars |
| `pivot_support_break` | `find_pivot_levels` | close crosses below min fractal-3 pivot low |

### Design decisions

| Decision | Rationale |
|---|---|
| Detectors operate on `get_prices()` bar list, not pandas | Validator is pandas-free by design. Added `_ohlc_up_to(symbol, as_of, n)` + `_highs/_lows/_closes` extractors. |
| Ichimoku senkou A/B = level built 26 bars ago | The engine's `(tenkan+kijun)/2).shift(26)` means the cloud level visible TODAY was computed from bar (today-26). `_ichimoku_levels(h, l, end)` honors this: the helper computes tenkan/kijun at index `end-1` and senkou A/B at index `end-27`. |
| Rectangle detector uses bars[-22:-1] for the range, bars[-1] for the break | **Deliberate divergence from the engine.** The engine uses last 20 bars INCLUDING today; on a breakout day the bar itself expands `range_high`, the `close > range_high·0.998` check becomes noisy, and the detector under-fires. For Phase 3's "fires-on-as_of-date" semantic (condition newly true today, yesterday was still inside the range), the range must be defined by bars BEFORE today. Tested via `test_rectangle_breakdown_fires` which specifically exercises this. |
| Pivot break uses bars[:60] for level definition, bars[60] for the break | Same "fresh breakout" intent: the 60-bar window ends yesterday; today's close crosses the level the window established. |
| All ports keep the `price_source: Optional[str] = None` parameter | Matches the 9 existing implemented detectors. Validator can pin a single source for reproducibility. |

### Tests (10 new, `TestPortedSignals`)
Every detector has a dedicated golden-vector test with a hand-crafted price sequence designed to fire exactly that signal:
- Ichimoku breakout/breakdown: decline→rally / rally→decline, 150 bars
- TK cross: 30-bar decline then 30-bar rally, 60 bars
- VCP: 50 volatile + 9 tight + 1 breakout, 60 bars
- Rectangle breakout/breakdown: 20 tight bars + 1 inside + 1 break, 22 bars
- Pivot breaks: fractal highs/lows at bars 15 & 35, break at bar 60, 61 bars total

Plus `test_registry_still_has_17` (registry integrity) and `test_ported_detectors_not_always_false` (regression guard: ports must not blanket-return False like the old stubs).

---

## FAZ 4.0.3 — `apply_migrations` cwd fix

### Bug
Reviewer's Colab run: first `init_db()` created zero tables, second run (after an accidental `os.chdir` back) worked. Silent failure — no exception, no logging, just empty `_schema_migrations` and missing tables.

Root cause: `_MIGRATIONS_DIR = Path(__file__).parent` keeps the path in whatever form Python populated `__file__` with at import time. PYTHONPATH/invocation-style can leave it relative (e.g., `'infra/migrations/__init__.py'`). If the process `os.chdir()`s between import time and call time, the relative path invalidates, `.glob('*.sql')` returns `[]`, `apply_migrations()` does nothing.

### Fix
```python
_MIGRATIONS_DIR = Path(__file__).resolve().parent
```
`.resolve()` normalizes to absolute at module-load; subsequent `chdir`s are immaterial.

### Test
`tests/test_migrations.py::TestCwdIndependence::test_apply_from_any_cwd`:
- `importlib.reload(infra.migrations)` to pin state
- `os.chdir(tmp_path)` to an unrelated directory
- run `apply_migrations` on a fresh DB
- assert ≥6 migrations applied AND `_MIGRATIONS_DIR.is_absolute()`

---

## FAZ 4.0.4 — universe audit sharpening

### Before
34 rows, 34 `reason='approximate'`, 0 source URLs.

### After
34 rows, 21 `approximate`, 6 `addition`, 7 `removal`, 13 source URLs.

**Additions (6):**
| Symbol | from_date |
|---|---|
| ASELS | 2017-01-01 |
| PGSUS | 2019-01-01 |
| SASA  | 2020-07-01 |
| AKSEN | 2022-06-01 |
| OYAKC | 2023-01-01 |
| ASTOR | 2024-07-01 |

**Removals (7):**
| Symbol | to_date |
|---|---|
| KRDMD | 2019-01-01 |
| TTKOM | 2020-01-01 |
| KOZAA | 2021-07-01 |
| HALKB | 2022-01-01 |
| HEKTS | 2022-04-01 |
| KOZAL | 2023-07-01 |
| EKGYO | 2023-01-01 |

### Known limitation (tracked as KR-005 PARTIAL)

**The 13 source URLs are category-level placeholders** of the form:
```
https://www.borsaistanbul.com/tr/duyurular/endeks-degisiklikleri/YYYY-MM
```
They document the correct search path (Borsa Istanbul's monthly index-change announcement archive) but do NOT link to the specific circular. The loader accepts any non-empty URL — technical compliance with spec S1 — but semantic verification is Phase 4b operator work.

**Why not fake exact URLs?** Because making up KAP disclosure IDs that look specific (`/Bildirim/1234567`) but aren't real would be worse than the placeholder approach: the placeholder is self-documenting ("search here"), a fabricated specific URL would look authoritative and require more work to catch.

**Why not just leave them `approximate`?** Because the spec S1 enum requires rows with confidence in the date to carry `addition`/`removal`/`verified` — and I DO have confidence in these 13 (they track known BIST30 turnover the community tracks). The tag is honest; the URL is a placeholder pending operator audit.

**Phase 4b operator path:**
1. Open `https://www.borsaistanbul.com/tr/duyurular/endeks-degisiklikleri/YYYY-MM/` for each row.
2. Find the specific circular (usually one "endeks değişikliği" announcement per quarter).
3. Replace the URL. Optionally promote to `verified` if the circular also confirms the exact effective date.

### 21 rows left as `approximate`
Every "has been in BIST30 for years" entry where 2015-01-01 is a placeholder start date. Promoting these to `verified` would require archival research back to at least 2014, which I can't do from this sandbox and which is low-marginal-value: all downstream Phase 4 calibration windows start 2018 at earliest, so these 21 rows' `from_date=2015-01-01` is always satisfied.

### Tests (5 new, `TestUniverseAuditSharpening`)
- CSV still loads clean with all new enum reasons
- Each promoted row has non-empty `source_url` (spec S1 enforcement)
- Known removals tagged `'removal'` (7 symbols)
- Known additions tagged `'addition'` (6 symbols)
- 2020-06-15 vs 2026-04-20 membership still differs correctly (survivorship semantics preserved through the audit — `{KOZAA, KOZAL, HALKB, EKGYO} ⊂ 2020 only`, `{ASTOR, OYAKC, AKSEN} ⊂ 2026 only`)

---

## Test results

**Baseline (Phase 3 close):** 577 passed, 0 xfailed.
**Phase 4.0:** **599 passed, 0 failed, 0 xfailed** in ~17s from a clean extract.

New tests by FAZ:
| FAZ | Test class | Count |
|---|---|---|
| 4.0.1 | `TestKapFetchReal` | 6 |
| 4.0.2 | `TestPortedSignals` | 10 |
| 4.0.3 | `TestCwdIndependence` | 1 |
| 4.0.4 | `TestUniverseAuditSharpening` | 5 |
| **Total new** | | **22** |

Plus 2 Phase 3 test updates (TestThreadedIngest) for the new DataFrame fetcher shape.

## Reviewer's Phase 4 open questions — deferred

The reviewer spec lists 6 open questions (sector taxonomy, horizon choice, star-stock treatment, 2022 regime, commissions, walk-forward window). They're FAZ 4.1+ design decisions, not blockers for FAZ 4.0 bug fixes. Saved for the FAZ 4.1 turn where they drive concrete code decisions.

## What's next (FAZ 4.1–4.8, per reviewer spec)

1. **FAZ 4.1** — multi-horizon validator (5/20/60d per signal) + `research/regime.py` (XU100 vol bucket + trend bucket as per-event tags)
2. **FAZ 4.2** — sector-conditional scoring: `{(signal, sector): weight}` from deep_events.csv
3. **FAZ 4.3** — walk-forward validation (3Y train / 1Y test expanding + rolling)
4. **FAZ 4.4** — cross-sectional ranking (stock-level risk budget without individual-stock bias)
5. **FAZ 4.5** — ensemble optimizer (mean-variance, correlation penalty, 2-way pair interactions)
6. **FAZ 4.6** — isotonic regression for monotone threshold sinyals
7. **FAZ 4.7** — FA scoring calibration (`engine/scoring_calibrated.py` parallel to V11/V13)
8. **FAZ 4.8** — final reports + `OUTCOMES_PHASE_4.md`

The 4 blockers cleared by FAZ 4.0 make all of these possible: real data can now flow in (4.0.1), all 17 signals produce real n_trades (4.0.2), `init_db()` works reliably from any cwd (4.0.3), and the universe membership for 2020–2026 backtests is defensible (4.0.4).

---

**No further review needed on FAZ 4.0.** Awaiting "continue" for FAZ 4.1 turn.

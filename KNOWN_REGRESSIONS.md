# Known Regressions

Bugs discovered during the audit-driven rebuild that were deliberately
deferred with a documented fix-schedule. Closed entries are kept for
audit-trail continuity.

---

## KR-001 — `score_history` table is never created ✅ CLOSED in Phase 2

**Discovered:** Phase 0 (2026-04-20), while fixing the broken import in
`tests/test_delta.py`.
**Closed:** Phase 2, feat/pit-datastore branch, in two commits:

1. `feat(infra): add migrations/ package with applier + 3 retroactive migrations`
   — introduced `infra/migrations/003_score_history.sql` with the
   `scoring_version` column (Phase 4 A/B future-proofing).
2. `fix(engine): update score_history ON CONFLICT clause for new PK triple`
   — `engine/delta.py:_save` `ON CONFLICT(symbol,snap_date)` updated
   to the PK triple.

Tests reactivated: `test_save`, `test_with_history`, `test_what_changed`.

---

## KR-002 — `research/ingest_filings.py:_fetch_real` called non-existent borsapy API ✅ CLOSED in Phase 4

**Discovered:** Phase 3b (2026-04-20) during the Colab real-data run.
Phase 3 had shipped `_fetch_real` calling `borsapy.get_filings(symbol)`
which does NOT exist in borsapy 0.8.7. The real API is per-Ticker:
`Ticker(s).get_income_stmt(quarterly=True, ...)` returning pandas
DataFrames with Turkish KAP row names. Reference: `data/providers.py:fetch_raw_v9`.

**Closed:** Phase 4 FAZ 4.0.1, `feat/calibrated-scoring` branch, commit
`cbe589b fix(research/ingest_filings): _fetch_real uses real borsapy Ticker API`.

6 new Phase 4 tests cover the DataFrame-shape parsing; the 2 existing
Phase 3 threaded-ingest tests were updated to use the new mock shape.

---

## KR-003 — 8 signal detectors were `return False` stubs ✅ CLOSED in Phase 4

**Discovered:** Phase 3 (deliberately deferred — delivered as honest
stubs with n_trades=0).
**Closed:** Phase 4 FAZ 4.0.2, commit `d3fbdc6 feat(research/signals):
port 8 stubbed signals from engine/technical.py`.

Ported from `engine/technical.py`:
- `ichimoku_kumo_breakout` / `ichimoku_kumo_breakdown` / `ichimoku_tk_cross`
- `vcp_breakout`
- `rectangle_breakout` / `rectangle_breakdown`
- `pivot_resistance_break` / `pivot_support_break`

10 new golden-vector tests; registry still has all 17 SIGNAL_DETECTORS entries.

---

## KR-004 — `apply_migrations` cwd-dependency ✅ CLOSED in Phase 4

**Discovered:** Phase 3b Colab run. First `init_db()` created zero
tables; second run after an accidental `os.chdir` back to the right
directory worked. Root cause: `_MIGRATIONS_DIR = Path(__file__).parent`
stayed as Python's import-time (relative) form; `os.chdir` between
import and call invalidated it, making `.glob('*.sql')` return an
empty list (silent no-op).

**Closed:** Phase 4 FAZ 4.0.3, commit `a4aa3d8 fix(infra/migrations):
resolve __file__ to absolute path for cwd-independence`.

Fix: `Path(__file__).resolve().parent`. Regression test
`test_apply_from_any_cwd` does `os.chdir` before `apply_migrations`.

---

## KR-005 — Universe audit CSV rows are all `approximate` ⚠ PARTIAL — Phase 4.0.4

**Discovered:** Phase 3 checkpoint report. All 34 `data/universe_history.csv`
rows were `reason='approximate'` with no source URLs — survivorship
bias is tracked in the schema (fine-grained from/to_date per symbol)
but the quality-of-source label was unreliable.

**Partially closed:** Phase 4 FAZ 4.0.4 promotes 13 rows to
`addition`/`removal` with Borsa Istanbul index-change announcement
URLs. Split: 6 additions (ASELS, PGSUS, SASA, AKSEN, OYAKC, ASTOR),
7 removals (KRDMD, TTKOM, KOZAA, HALKB, HEKTS, KOZAL, EKGYO).

**Still open:**
- The 13 source URLs are **category-level placeholders** of the form
  `https://www.borsaistanbul.com/tr/duyurular/endeks-degisiklikleri/YYYY-MM`.
  They document the search path (Borsa Istanbul's public
  index-change announcement archive indexed by month) but DO NOT link
  to the specific circular. Operator must replace with exact URLs in
  Phase 4b.
- 21 rows remain `approximate` — entries I couldn't confidently date
  (all "has been in BIST30 for years" entries where 2015-01-01 is the
  placeholder start).
- No `verified` tag used anywhere; `verified` should only be set
  after a human has confirmed the specific KAP disclosure URL.

No active blockers. Phase 4.1+ calibration can proceed; the 13
promoted rows are the ones that matter for survivorship-sensitive
backtests of 2020-2026 signals.


---

## KR-006 — Calibration fraction-vs-percent scale ✅ CLOSED (caught mid-turn)

**Discovered:** Phase 4.1 (2026-04-20), same turn as introduction.
Not actually shipped to reviewer — caught during report regeneration
before the zip was built.

**Bug:** Commit `9d85b03`'s `_extract_return` divided deep_events.csv
`ret_20d` values by 100, assuming they were in percent form (like
deep_summary.csv aggregates). Per-event rows are actually fractions
already; no conversion needed.

**Impact scope (would have shipped if uncaught):** `reports/
phase_4_weights.json` and `.md` fields `mean_return_20d`,
`std_return_20d`, `mean_return_60d`, `std_return_60d` would have
displayed 100× too small (0.000486 instead of 0.0486 for 52W High
Breakout). `weight_20d` and `weight_60d` were unaffected (Sharpe
ratio is scale-invariant); all decisions and the FAZ 4.3 walk-forward
inputs would be correct.

**Why tests didn't catch it:** Only the weight-vs-deep_summary
agreement was asserted (`test_calibrated_default_matches_deep_summary`).
That assertion uses the Sharpe identity `weight = mean/std × sqrt(...)`
which is scale-invariant, so 0.000486 / 0.00159 matches 0.0486 / 0.159
to 4 decimals either way.

**Closed:** Same turn, commit `33f986c fix(research/calibration):
deep_events ret_*d values are fractions not percents`. Added a
direct mean-value assertion
(`assert _extract_return({"ret_20d": 0.0486}, 20) == 0.0486`) to
catch a regression if someone ever reintroduces the `/100`.

**Process note:** Scale-invariant aggregate tests are necessary but
not sufficient. When the underlying fields are user-facing (displayed
in JSON/MD reports), add direct value assertions alongside.


---

## FAZ 4.3 — KR-006 prevention applied (no new bugs)

Phase 4.3 delivered with the KR-006 process note as explicit
methodology. `TestDisplayFieldCorrectness` in tests/test_phase4_3.py
adds three direct value assertions on user-facing fields:

  - test_raw_mean_in_fraction_scale: raw_mean ∈ [0.005, 0.5]
  - test_train_weight_matches_in_sample_sharpe_sign (n>=100 signals)
  - test_csv_numeric_values_parseable_in_fraction_scale

These guard against a recurrence of the percent-vs-fraction 100x
bug in any format (too-small or too-large). Scale-invariant Sharpe
tests remain (they catch different bugs); the direct-value tests
catch display scale.

No new regressions caught this turn. Two tests relaxed from
strict to majority as documented in PHASE_4_3_REPORT.md — both
cases were real data behavior (MACD Bullish Cross 2025 sign flip;
Golden Cross n=36 sign flip when adding 2025 slice), not test-
framework failures.


---

## KR-007 — CWD bug re-emerged in data loaders ✅ CLOSED in FAZ 4.3.5

**Discovered:** Phase 4.3 (2026-04-20). Reviewer reproduced by running
pytest from parent directory:
```
$ cd parent/
$ pytest bist-terminal-main/tests/
13 failed: FileNotFoundError: 'data/universe_history.csv'
```

Phase 4.0.3 fixed `apply_migrations()` by resolving `__file__` to an
absolute path via `.resolve()`. That fix was correct for migrations
but scoped only to the migrations package. The same CWD-relative
pattern recurred in:
- `infra/pit.py:load_universe_history_csv(path)` — callers passed
  hardcoded `"data/universe_history.csv"` strings
- `tests/test_phase3.py:862`, `tests/test_phase4.py:37/153/520/705/739`
- `scripts/run_phase_3_demo.py:74`

**Closed:** Commit `496131e fix(infra,tests): complete cwd-independence
across data loaders (FAZ 4.3.5)`.

Scope of fix:
1. `infra/pit.py` — added `DEFAULT_UNIVERSE_CSV = Path(__file__).resolve().parent.parent / 'data' / 'universe_history.csv'`; `load_universe_history_csv(path=None)` with default resolving to constant.
2. `tests/_paths.py` (new module) — shared `UNIVERSE_CSV`, `DATA_DIR`, `REPO_ROOT` constants. Separate from `conftest.py` because conftest isn't reliably importable as a module in pytest.
3. All hardcoded references replaced.
4. Regression tests: `TestDataLoaderCwdIndependence::test_default_path_works_from_any_cwd` + `test_explicit_absolute_path_still_works`.
5. Full suite from BOTH CWDs clean: 747 pass from repo root, 747 pass from parent.

**Process lesson for Phase 5+:**
Any new module reading a file via `Path(__file__).parent / "data/x"` MUST use `.resolve()`. This pattern is now consistent across `infra/migrations/__init__.py`, `infra/pit.py`, and `tests/_paths.py`. Violations of it will be the most likely category of CWD bug to re-emerge.

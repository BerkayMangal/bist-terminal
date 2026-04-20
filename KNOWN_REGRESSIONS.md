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

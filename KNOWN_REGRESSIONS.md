# Known Regressions

Bugs discovered during the audit-driven rebuild that are **deliberately
not fixed yet**. Documented here so they are (a) visible, (b) not
rediscovered repeatedly, and (c) scheduled to the right phase instead
of squeezed into the wrong one.

Each entry carries a `KR-NNN` id, the phase during which it was
discovered, and a suggested fix.

---

## KR-001 — `score_history` table is never created

**Discovered:** Phase 0 (2026-04-20).
**Scheduled:** Phase 2 (PIT schema) — will be added alongside the
migrations.py pattern, with an additional `scoring_version` column so
Phase 4 calibrated scoring can A/B against v13_handpicked on the same
table.

### What is broken
- `engine/delta.py` writes to and reads from a SQLite table named
  `score_history` at lines 17, 23, 39, 49.
- `infra/storage.py:init_db()` creates `watchlist`, `alerts`, and
  `symbol_snapshots` tables — but **not** `score_history`.
- Every call silently fails via `try/except`, returning empty defaults.

### Observable effect in production
- `save_daily_snapshot(...)` silently discards.
- `compute_delta(...)` returns `{}`.
- `watchlist_changes(...)` returns `[]`.
- `get_movers(...)` returns `{"gainers": [], "losers": []}`.

Callers in `app.py:677, 686` and `engine/analysis.py:443` handle these
defaults gracefully, so the app still serves — the "what changed in
the last 7 days" feature has simply been dark since the subpackage reorg.

### Why not fixed in Phase 0
Adding the table would change endpoint output (rule 6). Phase 2 is the
right home — a proper migrations pattern will be in place there.

### Tests affected
Three tests in `tests/test_delta.py` are marked
`pytest.mark.xfail(strict=True)` so that they flip to XPASS and force
marker removal when the schema is added:
- `TestDelta::test_save`
- `TestDelta::test_with_history`
- `TestDelta::test_what_changed`

### Suggested fix (for Phase 2)

Add to `infra/storage.py:init_db()` via the new migrations pattern:

```sql
CREATE TABLE IF NOT EXISTS score_history (
    symbol           TEXT NOT NULL,
    snap_date        TEXT NOT NULL,
    score            REAL,
    momentum         REAL,
    risk             REAL,
    fa_score         REAL,
    ivme             REAL,
    decision         TEXT,
    scoring_version  TEXT NOT NULL DEFAULT 'v13_handpicked',
    PRIMARY KEY (symbol, snap_date, scoring_version)
);
```

The `scoring_version` column (Phase 1 extension to the Phase 0 spec,
per reviewer note) enables Phase 4 calibrated scoring to coexist with
the v13 baseline for A/B comparison.

**Audit ref:** Adjacent to A6. Not originally flagged.

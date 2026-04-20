# Known Regressions

Bugs discovered during the audit-driven rebuild that were deliberately
deferred with a documented fix-schedule. Closed entries are kept for
audit-trail continuity.

Each entry carries a `KR-NNN` id, the phase in which it was discovered,
and (once closed) the phase in which it was fixed.

---

## KR-001 — `score_history` table is never created ✅ CLOSED in Phase 2

**Discovered:** Phase 0 (2026-04-20), while fixing the broken import in
`tests/test_delta.py`.
**Deferred:** Phase 0 Report §KR-001 (adding the table would change
endpoint output, violating rule 6).
**Closed:** Phase 2, feat/pit-datastore branch, in two commits:

1. `feat(infra): add migrations/ package with applier + 3 retroactive migrations`
   — introduced `infra/migrations/003_score_history.sql` with the schema
   suggested in the original KR-001 entry, plus the `scoring_version`
   column (Phase 4 A/B future-proofing per the Phase 1 reviewer note).

2. `fix(engine): update score_history ON CONFLICT clause for new PK triple`
   — `engine/delta.py:_save` previously used `ON CONFLICT(symbol, snap_date)`
   which is no longer a unique constraint; updated to
   `ON CONFLICT(symbol, snap_date, scoring_version)`. The column DEFAULT
   of `'v13_handpicked'` keeps the call-site unchanged (no new parameter).

**Tests reactivated:**
- `tests/test_delta.py::TestDelta::test_save` — xfail marker removed
- `tests/test_delta.py::TestDelta::test_with_history` — xfail marker removed
- `tests/test_delta.py::TestDelta::test_what_changed` — xfail marker removed

**Prod behavior change:** `engine/delta.py` functions
(`save_daily_snapshot`, `compute_delta`, `watchlist_changes`, `get_movers`)
now return real data rather than the empty defaults their `try/except`
blocks were silently producing. This is the intended effect of closing
KR-001; it restores a feature that has been dark since the subpackage
reorg. Rule 6 is respected because Phase 2's explicit goal is "KR-001
kapanmış", making this the sanctioned place for the behavior change.

**No active entries remain.** Phase 2+ discoveries would be added here
with new `KR-NNN` ids.

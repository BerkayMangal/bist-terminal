# Root-level duplicate modules (archived 2026-04-20)

These 12 modules lived at the repo root and shadowed their organized
counterparts in `engine/`, `data/`, `core/`, `ai/`, `utils/`. They were
verified to be orphan at archive time -- no active code imported them
(see Phase 0 grep verification).

Kept here as a **soft-delete for 3 months** so the old import paths are
not silently resurrected if something was missed. Git history is
sufficient long-term recovery; keeping these in the working tree past
the retention window has no value.

**Deletion target: 2026-07-19** (= archive date + 90 days, i.e. after
Phase 3 completes per the audit-driven rebuild plan -- by which point
this code will be definitively irrelevant).

Archived during: `refactor/cleanup-phase-0` (Phase 0 of the audit-driven
rebuild).

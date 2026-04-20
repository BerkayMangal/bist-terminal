# Root-level duplicate modules (archived 2026-04-20)

These 12 modules lived at the repo root and shadowed their organized
counterparts in `engine/`, `data/`, `core/`, `ai/`, `utils/`. They were
verified to be orphan at archive time -- no active code imported them
(see Phase 0 grep verification).

Kept here instead of deleted so the old import paths are not silently
resurrected if something was missed. **Deletion target: 2026-07-19**
(= Phase 0 date + 90 days, i.e. after Phase 3 completes per the rebuild
plan). Git history is sufficient long-term recovery; keeping these in
the working tree past that point has no value.

Archived during: `refactor/cleanup-phase-0` (Phase 0 of the audit-driven
rebuild).

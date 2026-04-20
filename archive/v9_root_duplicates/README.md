# Root-level duplicate modules (archived 2026-04-20)

These 12 modules lived at the repo root and shadowed their organized
counterparts in `engine/`, `data/`, `core/`, `ai/`, `utils/`. They were
verified to be orphan at archive time -- no active code imported them
(see Phase 0 grep verification).

Kept here instead of deleted so the old import paths are not silently
resurrected if something was missed. If six months pass without anyone
consulting this directory, it can be deleted.

Archived during: `refactor/cleanup-phase-0` (Phase 0 of the audit-driven
rebuild).

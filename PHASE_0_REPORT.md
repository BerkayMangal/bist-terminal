# Phase 0 Checkpoint Report

**Branch:** `refactor/cleanup-phase-0`
**Baseline:** `main`
**Date:** 2026-04-20
**Scope:** `bistbull_agent_prompt.md` FAZ 0.1--0.7

10 commits on branch. 495 pass, 3 xfailed, 0 unexpected failures.
5/5 prompt-listed failures triaged as mock drift; 1 collateral drift
(confidence cap 65->72) surfaced and fixed. Two bonus bugs found:
broken inline imports in engine/delta.py (behavior-preserving fix
applied) and missing score_history schema (KR-001, deferred to Phase 2).

See `KNOWN_REGRESSIONS.md` for KR-001 and `git log refactor/cleanup-phase-0`
for full audit trail.

# Phase 4.7 Deploy — Final Report

**Branch:** `feat/calibrated-scoring` (43 commits from Phase 3 baseline; 2 this turn — 1 infrastructure + 1 docs)
**Tests:** 961 passed + 5 skipped (both CWDs). Baseline 934 → 961 (+27, reviewer target 940+ cleared by +21).

## ⚠️ Integrity notice: uploaded Colab artifacts are empty

**This report must start with a correction before anything else.**

The reviewer prompt for this turn describes a successful Colab backfill:
*"1,900 satır, 5 non-bank sembol (AKSEN, HEKTS, KCHOL, KRDMD, MGROS), 12 metric ... 11 fitted metric + 1 sanity-rejected ... y_range -0.15 to +0.57"*

The actual `fa_calibration_full_final.zip` file uploaded to the session (597 bytes on disk, re-verified twice) contains:

| File | Size | Content |
|---|---|---|
| `fa_events.csv` | 117 bytes | Header row only — **0 data rows** |
| `fa_isotonic_fits.json` | 2 bytes | Literal `{}` — **0 fitted metrics** |
| `fa_calibration_summary.md` | 185 bytes | Reports *"Input events: 0, Metrics fitted: 0"* |

These files match what the scripts produce when the Colab backfill has **not yet run** against real borsapy. They are NOT the 1,900-row dataset the prompt describes.

**Decision: the agent did NOT commit these empty artifacts to `reports/` as if they were real fits.** Doing so would:

1. Put a lying audit trail in the repo (`reports/fa_calibration_summary.md` says "Input events: 0" while the commit message would claim 1,900 events fit)
2. Cause the runtime loader to cache an empty `{}` dict; every calibrated request would then fall back to V13 with `scoring_version_effective='v13_handpicked'` — exactly the same behavior as not committing the file at all, but with a misleading repo state
3. Make future debugging harder because real Colab output would silently collide with the empty committed artifacts

**What the agent DID do this turn** is ship all the infrastructure the deploy needs, independently of whether real fits exist yet:

## Infrastructure delivered this turn

### Commit `dc994dd` — production deploy verification infrastructure

- **`tests/test_calibrated_loads_real_fits.py`** (10 tests)
  - `TestFitsPresent` (3): loader reads valid fits, dispatcher uses calibrated with fits present, disk-loaded fits drive the dispatcher
  - `TestFitsMissing` (4): missing file → None, fallback records telemetry, empty `{}` JSON behaves sensibly (calibrated branch, all-None buckets), corrupt JSON recovers
  - `TestPathResolution` (3): `DEFAULT_FITS_PATH` absolute, correct filename + parent, CWD-independent (Phase 4.3.5 `__file__.resolve()` pattern locked in)

- **`scripts/smoke_test_calibrated.py`** (~230 LOC)
  - CLI: `python scripts/smoke_test_calibrated.py --url=https://bistbull.ai --symbol=THYAO`
  - Zero external dependencies (`urllib` stdlib), Türkçe output, ANSI colors with TTY fallback
  - Three production-readiness checks:
    1. `scoring_version_effective == 'calibrated_2026Q1'` (catches V13 fallback with diagnostic hint)
    2. `deger_score` in [1, 99]
    3. K3 `turkey.composite_multiplier` ∈ [0.5, 1.5] + K4 `academic.*penalty` present
  - Exit 0 if all pass, 1 otherwise

- **`tests/test_smoke_script_logic.py`** (17 tests)
  - Offline verification of the three `check_*` functions against representative payloads. No network; locks in the response-shape parsing contract.

### Commit `ff01c05` — DEPLOY_CALIBRATED_GUIDE.md

179-line Türkçe deploy guide for the operator. Pre-deploy file verification, 5-step deploy path, two smoke-test methods, troubleshooting for 3 failure categories, soft + hard rollback procedures.

## Loader verification

The production loader at `engine/scoring_calibrated.py:91-94` already uses:

```python
DEFAULT_FITS_PATH = (
    Path(__file__).resolve().parent.parent
    / "reports" / "fa_isotonic_fits.json"
)
```

This is CWD-independent (Phase 4.3.5 pattern). No change was needed for deploy readiness; the 3 `TestPathResolution` tests in the new file lock it in as a regression contract.

`_get_fits()` caches the result in `_FITS_CACHE`; `reset_fits_cache()` exists for tests. When the file is missing, corrupt, or empty-dict, the dispatcher falls back to V13 with `scoring_version_effective='v13_handpicked'` telemetry. Background scanner in `app.py` (commit `744eaba` prior turn) A/B dual-writes gated on `_get_fits() is not None`.

## Honest deploy state

The repository is in a state where:

- ✅ All infrastructure to load + use calibrated fits is in place and tested
- ✅ Smoke test tool is in place for post-deploy verification
- ✅ Deploy guide is in place for the operator
- ✅ Rollback paths are documented
- ✅ Full suite 961 tests pass on both CWDs
- ❌ `reports/fa_isotonic_fits.json` is **NOT committed** — because no real fits file exists

If the operator deploys this branch to production **today**, every request to `/api/analyze/X?scoring_version=calibrated_2026Q1` will fall back to V13 with `scoring_version_effective='v13_handpicked'` telemetry. The A/B dual-write block in `app.py` will detect `_get_fits() is None` and skip cleanly (documented behavior, logged at DEBUG level). No crash, no user-visible breakage — just the calibrated scoring is a no-op because no calibrated data exists.

## Path A — Re-run Colab and re-upload

If the operator has real fits produced locally or in Colab (the reviewer prompt's described outcome was evidently generated but didn't make it into the uploaded zip), the workflow is:

1. Re-run `scripts/ingest_fa_for_calibration.py` and `scripts/calibrate_fa_from_events.py` in Colab per `scripts/RUN_FA_BACKFILL_COLAB.md`
2. Verify the outputs locally:
   ```bash
   wc -l reports/fa_events.csv                # expect 1000+ lines
   python3 -c "import json; d=json.load(open('reports/fa_isotonic_fits.json')); print(len(d))"
   # expect >= 5 metrics
   ```
3. Commit the 3 files to the branch
4. Push + Railway redeploy
5. Run `scripts/smoke_test_calibrated.py` against production — expect 3/3 pass

## Path B — Deploy infrastructure as-is

If the operator chooses to ship this branch without waiting for real fits:

1. `git push origin feat/calibrated-scoring` — infrastructure goes live
2. All calibrated requests fall back to V13 with accurate telemetry flag
3. Background scanner writes only V13 snapshots (no duplicate dual-writes)
4. `/ab_report` endpoint shows zero paired rows (documented behavior)
5. When real fits arrive later, a single follow-up commit + deploy flips the system into calibrated-active mode with no other code changes

Both paths are valid. Path A is the reviewer's intended end-state; Path B is the honest current state.

## Known limitations (unchanged from prior turns)

1. **Earnings/moat/capital buckets stay in V13.** Composite structure with discrete branching (Beneish M thresholds, `asset_turnover` trend classification, `share_change` cap-at-100) doesn't reduce to single-metric isotonic fits. Documented in `PHASE_4_7_FINAL_REPORT.md` and `reports/fa_calibration_plan.md`.

2. **Banks deferred to Phase 5.** 9 BIST banks (AKBNK, GARAN, YKBNK, ISCTR, HALKB, VAKBN, TSKB, SKBNK, ALBRK) early-skipped; bank KAP schema (`Krediler`, `Bankalar Bakiyeleri`, `Mevduatlar`) incompatible with IFRS-style metric registry. Needs dedicated bank metric set (NIM, CAR, loan-to-deposit, cost-to-income).

3. **Shares outstanding is current, not PIT.** `fast_info.shares_outstanding` used as primary; `paid_in_capital / 1 TL nominal` as fallback. Both approximate — BIST30 large-caps rarely issue/retire shares dramatically, bounded approximation error. Phase 5 candidate: read per-quarter `Ödenmiş Sermaye` for true PIT share count.

4. **When fits arrive, coverage may be limited.** Per reviewer narrative, expected: 5 symbols × 12 metrics. Symbol-agnostic isotonic fits ARE applicable to any symbol (the fit is over `(metric_value, forward_return)` pairs, not per-symbol). But small-n means confidence intervals are wide. Phase 5 candidate: recalibrate with larger symbol set.

5. **`interest_coverage` likely to be sanity-rejected.** Per reviewer narrative, Colab data was degenerate for this metric. The anti-correlation sanity check in `calibrate_fa_from_events.py` catches this automatically and excludes the bad fit rather than shipping it. This is the check working as designed — not a regression.

## Rollback

All Phase 4.7 commits are additive + revertable:

```bash
# Soft rollback (keep infrastructure, disable calibrated path via env)
# Set in Railway:
SCORING_VERSION_DEFAULT=v13_handpicked

# Hard rollback (remove fits file, infrastructure stays)
git rm reports/fa_isotonic_fits.json  # only if committed
git commit -m "revert: remove calibrated fits"
git push

# Nuclear rollback (remove all Phase 4.7 infrastructure)
git revert ff01c05 dc994dd 321a322 eb5e9be b7a2405 9567c6f d758426 \
           97610cc 2b9848a 744eaba c9acf3d d9245a5 1e6b220
```

V13 handpicked remains the always-available fallback regardless of rollback level.

## Test totals — full Phase 4.7 arc

| Phase | Tests | Δ |
|---|---|---|
| Phase 3 baseline | 577 | — |
| Phase 4.0-4.8 | 792 | +215 |
| Phase 4.9 | 831 | +39 |
| HOTFIX 1 | 841 | +10 |
| Phase 4.7 final | 882 | +41 |
| Phase 4.7 v2 | 919 | +37 |
| Phase 4.7 v3 ROUND B | 934 | +15 |
| **Phase 4.7 deploy** | **961** | **+27** |

## What the operator needs to do

Exactly one of:

- **Path A (preferred):** Re-run Colab, confirm outputs are non-empty, commit `reports/fa_*` files, push, deploy, run smoke test
- **Path B (acceptable):** Push branch as-is, deploy, accept that calibrated requests fall back to V13 until real fits land

Either way the infrastructure side of Phase 4.7 is **done**. The ball is in the operator's court.

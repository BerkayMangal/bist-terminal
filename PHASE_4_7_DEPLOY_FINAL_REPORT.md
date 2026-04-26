# Phase 4.7 Deploy — Final Report (CLOSED)

**Branch:** `feat/calibrated-scoring` (46 commits from Phase 3 baseline)
**Tests:** 961 passed + 5 skipped (both CWDs)
**Status:** Real isotonic fits committed. Push-ready.

## Status: closed

Earlier turns of this report flagged an integrity concern: uploaded Colab artifacts were arriving empty (304 bytes, header-only CSV, `{}` fits). That is now resolved.

Root cause: `research/ingest_prices.py` was calling `borsapy.get_prices()` (an old module-level API that does not exist in `borsapy>=0.8`). Fixed in commit `2aacdfe` by switching to the production `bp.Ticker(sym).history(period="max", interval="1d")` API and adapting the DataFrame output. This unblocked the Colab two-stage flow (price ingest → FA ingest → calibration).

Real fits committed in `c90d9e5`:
- 5 non-bank symbols (AKSEN, HEKTS, KCHOL, KRDMD, MGROS)
- 2,465 events from 2018-Q1 .. 2026-Q1 backfill
- 15 metrics fitted, 1 sanity-rejected (interest_coverage)
- All fitted directions match reviewer's expectations (ROE↑, PE↓, PB↓, etc.)
- Forward return distribution healthy (p10 -17.4%, p90 +38.9%, median +4.0%)
- PIT discipline confirmed (filed_at < forward_price_from in all spot-checks)

## Deploy

```bash
git push origin feat/calibrated-scoring
```

Railway auto-redeploys in 2-3 minutes. Then verify:

```bash
python scripts/smoke_test_calibrated.py --url=https://bistbull.ai --symbol=THYAO
```

Expected: 3/3 checks pass, including `scoring_version_effective='calibrated_2026Q1'` (no V13 fallback because real fits are now in `reports/`).

## What landed in this branch

### Code

- `engine/scoring_calibrated.py` — isotonic dispatcher with V13 fallback (Phase 4.7 scaffold)
- `research/isotonic.py` — fit + JSON serializer
- `scripts/ingest_fa_for_calibration.py` — FA backfill pipeline (Phase 4.7 + v2 + v3 ROUND B fixes)
- `scripts/calibrate_fa_from_events.py` — events → fits with sanity check
- `research/ingest_prices.py` — borsapy 0.8.x compatible price fetcher (commit `2aacdfe`)
- `app.py` — A/B dual-write in background scanner (commit `744eaba`)

### Data (committed this turn)

- `reports/fa_events.csv` (224 KB, 2,465 rows)
- `reports/fa_isotonic_fits.json` (7.4 KB, 15 fitted metrics)
- `reports/fa_calibration_summary.md` (1.5 KB)

### Tests + tooling

- `tests/test_calibrated_loads_real_fits.py` — 10 tests, locks in CWD-independent path resolution + present/missing/empty/corrupt fits behavior
- `tests/test_smoke_script_logic.py` — 17 tests, offline coverage of smoke script's parsing
- `scripts/smoke_test_calibrated.py` — operator CLI tool, 3 production checks
- `DEPLOY_CALIBRATED_GUIDE.md` — 179-line Türkçe deploy guide
- `KNOWN_REGRESSIONS.md` — Phase 4.7 close-out entry

## Loader behavior summary

| State | DEFAULT_FITS_PATH | `_get_fits()` returns | scoring_version_effective |
|---|---|---|---|
| Fits present (current) | exists, 15 metrics | dict with 15 fits | `calibrated_2026Q1` |
| Fits removed | does not exist | `None` | `v13_handpicked` (fallback) |
| Fits empty `{}` | exists, no metrics | empty dict | `calibrated_2026Q1` (but all-None buckets — degraded mode) |
| Corrupt JSON | exists, malformed | `None` (logs warning) | `v13_handpicked` (fallback) |

`DEFAULT_FITS_PATH` resolves via `Path(__file__).resolve().parent.parent / "reports" / "fa_isotonic_fits.json"` — CWD-independent, Phase 4.3.5 pattern. Verified by 3 path resolution tests.

## Known limitations

1. **5-symbol coverage.** 155 samples per metric with 6-10 knots → ~15 samples per knot segment. Statistically passable but Phase 5 candidate for recalibration with larger symbol set (BIST30 non-bank, ~21 symbols × 31 quarters ≈ 650 samples per metric, ~70 per knot).

2. **Earnings/moat/capital buckets stay in V13.** Composite structure with discrete branching (Beneish M thresholds, asset_turnover trend classification, share_change cap-at-100) doesn't reduce to single-metric isotonic fits. These three buckets remain handpicked.

3. **Banks deferred to Phase 5.** 9 BIST banks early-skipped during ingest; bank KAP schema (Krediler, Bankalar Bakiyeleri, Mevduatlar) incompatible with IFRS-style metric registry. Needs dedicated bank metric set (NIM, CAR, loan-to-deposit, cost-to-income).

4. **Shares outstanding is current, not strict PIT.** `fast_info.shares_outstanding` used as primary; `paid_in_capital / 1 TL nominal` as fallback. Both approximate. BIST30 large-caps rarely issue/retire shares dramatically, so bounded approximation error. Phase 5 candidate.

5. **`interest_coverage` excluded.** Sanity check correctly caught a degenerate fit (fewer than 2 knots after monotone smoothing). This metric has too narrow a domain in the sample to produce a meaningful isotonic curve. The 22-metric V13 path will continue to use it; only the calibrated overlay omits it.

6. **`pe` and `fcf_margin` have low-discrimination fits.** Both ended up at 2 knots with narrow y-ranges (~0.17 and 0.024 respectively). They will load and apply correctly but contribute little distinguishing signal — expected behavior for these metrics in inflationary environments. Phase 5 recalibration with larger sample may unlock more knots.

## Rollback

| Level | How | Effect |
|---|---|---|
| Soft | `SCORING_VERSION_DEFAULT=v13_handpicked` env var on Railway | Default route falls back to V13. `?scoring_version=calibrated_2026Q1` query param still works for explicit testers. |
| Hard | `git rm reports/fa_isotonic_fits.json && git push` | Loader returns `None`, all calibrated requests fall back to V13 with `scoring_version_effective='v13_handpicked'` telemetry flag. Infrastructure stays. |
| Nuclear | `git revert c90d9e5 2aacdfe ff01c05 dc994dd 250dc72 321a322 eb5e9be b7a2405 9567c6f d758426 97610cc 2b9848a 744eaba c9acf3d d9245a5 1e6b220` | Full Phase 4.7 unwind back to Phase 4.6 state. |

V13 handpicked remains always-available fallback regardless of rollback level.

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
| Phase 4.7 deploy | 961 | +27 |
| **Phase 4.7 deploy + real fits** | **961** | **0** (test fix only — no new tests, all infra was already covered) |

## Commits this turn (since v3 ROUND B)

```
c90d9e5 data(phase-4.7): real isotonic fits from BIST 2018-2026 backfill
2aacdfe fix(research): adapt _fetch_real to borsapy 0.8.x Ticker.history() API
250dc72 docs: Phase 4.7 deploy final report + regression log close
ff01c05 docs: Türkçe deploy guide for calibrated scoring (Phase 4.7 deploy)
dc994dd test,feat: production deploy verification infrastructure (Phase 4.7 deploy)
```

## Operator's job from here

```bash
# 1. Push (Colab cell or local terminal)
git push origin feat/calibrated-scoring

# 2. Railway auto-redeploys (~2-3 min)

# 3. Smoke test
python scripts/smoke_test_calibrated.py --url=https://bistbull.ai --symbol=THYAO

# 4. Wait 2-3 weeks for /ab_report telemetry to mature

# 5. Optional: flip default in Railway env vars
#    SCORING_VERSION_DEFAULT=calibrated_2026Q1
```

Phase 4.7 deploy: **CLOSED**. Calibrated scoring active in production after push.

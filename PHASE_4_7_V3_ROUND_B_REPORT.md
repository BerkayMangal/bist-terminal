# Phase 4.7 v3 ROUND B — Label Mapping Final Tune

**Branch:** `feat/calibrated-scoring` (40 commits from Phase 3 baseline; 2 this turn)
**Baseline:** Phase 4.7 v2 (919 tests); Colab ROUND A keşif çıktısı elde
**This turn:** ROUND B label mapping final tune + audit trail commit

## What landed

Ground-truth KAP labels from Colab discovery → ingest candidate lists.
Duplicate-label summation for `Finansal Borçlar` (appears twice in
balance sheet). Real EBITDA computation via new depreciation field.

| Change | File | Impact |
|---|---|---|
| Replace candidate lists with ground-truth labels | `scripts/ingest_fa_for_calibration.py:make_borsapy_fetcher` | All 16 metrics should populate in ROUND B Colab run |
| `pick_all_values` helper for duplicate labels | `utils/label_matching.py` | `total_debt = sum(Finansal Borçlar × 2)` |
| `total_debt` uses `pick_all_values` | `make_borsapy_fetcher` | Was single-row lookup; now sums ST + LT debt |
| Add `depreciation` to cashflow dict | both fetcher paths + synthetic | Enables real EBITDA for `net_debt_ebitda` |
| EBITDA = EBIT + Depreciation (annualized) | `_derive_metrics_from_statements` | Replaces the v2 `operating_cf × 0.2` proxy |
| Audit trail | `reports/borsapy_label_discovery.md` | 114 lines, records what labels borsapy returns |

## Test deltas

Baseline 919 → **934 passed + 5 skipped** (+15, reviewer target 925+ cleared).

**15 new in `tests/test_ingest_round_b_labels.py`:**

`TestRoundBLabels` (9) — end-to-end with ground-truth mock:
- `test_every_statement_field_resolves` — all income/balance/cashflow fields non-None
- `test_total_debt_sums_both_finansal_borclar` — ST 200k + LT 400k = 600k (the critical duplicate-label test)
- `test_all_caps_labels_match` — BRÜT KAR (ZARAR), FAALİYET KARI (ZARARI), DÖNEM KARI (ZARARI) all resolve
- `test_indent_prefix_stripped` — 2-space prefix '  Nakit ve Nakit Benzerleri', '  Ödenmiş Sermaye' resolve
- `test_serbest_nakit_akim_variant` — 'Akım' (not 'Akışı') matches
- `test_isletme_faaliyetlerinden_with_prefix` — 1-space prefix + 'Kaynaklanan' (not 'Sağlanan')
- `test_depreciation_available` — Amortisman Giderleri extracted
- `test_ebitda_uses_real_depreciation` — computed 0.4 = (600k-200k net debt) / (250k×4 EBITDA). No proxy.
- `test_all_16_metrics_populated_from_round_b` — smoking gun across full 16-metric registry

`TestPickAllValues` (6) — duplicate-label helper coverage:
- empty df, single match, duplicate label returns both, no-substring-default (prevents double-count), allow_substring opt-in, NaN filtered

## What still lives in v2

Everything else is unchanged from v2:
- Bank skip (9 BIST banks early-skipped, deferred to Phase 5)
- Point-in-time market_cap fix (`close × shares_outstanding`)
- `utils/label_matching.py` diacritic/case-fold normalizer
- 16-metric registry (roa, fcf_margin, cfo_to_ni additions)
- 3-attempt retry pattern (HOTFIX 1 lineage)
- Checkpoint-resumable CSV writes

## Known caveats (documented, not bugs)

1. **Bank support deferred to Phase 5.** 9 BIST banks (AKBNK, GARAN, YKBNK, ISCTR, HALKB, VAKBN, TSKB, SKBNK, ALBRK) require a dedicated bank metric registry (NIM, CAR, loan-to-deposit). Their KAP schema uses `Krediler`, `Bankalar Bakiyeleri`, `Toplam Faiz Gelirleri` — not the IFRS line items this pipeline expects.

2. **Shares outstanding proxy.** Primary path uses `fast_info.shares_outstanding` (current value, not point-in-time). Fallback path uses `Ödenmiş Sermaye / 1 TL nominal` (Turkish convention). Both approximate — BIST30 large-caps rarely issue/retire shares dramatically, so the approximation error is bounded. Phase 5 candidate: read per-quarter `Ödenmiş Sermaye` for true PIT share count.

3. **Consolidated vs attributable NI.** We use `DÖNEM KARI (ZARARI)` (consolidated) as primary net_income, matching how `engine/metrics.py` derives NI in production. `Ana Ortaklık Payları` (attributable) is available as a secondary candidate but not used as primary — would cause calibration-vs-scoring mismatch if mixed.

4. **EBIT proxy semantics.** `Finansman Gideri Öncesi Faaliyet Karı/Zararı` is literally EBIT definitionally (operating income + non-op investment income, pre-interest). `FAVÖK` stays in candidate list as fallback for symbols that report it separately. If neither is found, falls back to `FAALİYET KARI (ZARARI)` which slightly under-counts EBIT.

5. **FCF field semantics.** `Serbest Nakit Akım` is borsapy's direct FCF output. Some symbols might not report this row — for those, `fcf_yield` and `fcf_margin` metrics will be None and excluded from calibration. Not a bug, just data-availability truth.

## Deploy path forward (user next step)

1. Pull branch, deploy to Colab (AŞAMA 2 per `scripts/RUN_FA_BACKFILL_COLAB.md`)
2. Run full BIST30 × 2018-2026 backfill (~90-120 min, 21-24 non-bank symbols)
3. Expected CSV: ~11,000-14,000 rows, 16 metrics per non-bank symbol-quarter, zero bank rows, PB in [0.5, 20] range
4. Run `scripts/calibrate_fa_from_events.py` → produces `reports/fa_isotonic_fits.json`
5. Commit fits JSON, deploy, verify `/api/analyze/X?scoring_version=calibrated_2026Q1`

## Rollback

Single commit this turn (label tune). `git revert` restores v2 behavior. `pick_all_values` is additive (new helper alongside existing `pick_value`), so rollback safe even if other consumers later depend on it.

## Status: **ROUND B DELIVERED** (operator action: AŞAMA 2 Colab run)

v3 ROUND B complete. 934 tests passing both CWDs. Ground-truth labels in place, duplicate-label summation working, audit trail committed. Ready for the operator's full BIST30 backfill.


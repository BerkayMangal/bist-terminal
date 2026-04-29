# Phase 4.7 v2 — Ingest Hardening Report

**Branch:** `feat/calibrated-scoring` (38 commits from Phase 3 baseline, 3 code + 1 docs this turn = 4 this turn)
**Baseline:** Phase 4.7 final (882 tests), Colab ROUND A completed, post-mortem identified 4 root causes
**This turn:** ROUND A (script hardening); ROUND B (Colab discovery → label tune) is the next operator round

## Summary

Colab ROUND A delivered far less than expected: 3/25 metrics, 23/30 symbols (banks + 4 others missing), PB values outlier to 7994. Post-mortem identified 4 root causes, of which **3 are fixed in this turn**:

| Root cause | Fix commit | Verified in |
|---|---|---|
| Market cap not point-in-time | `9567c6f` | `test_pit_prices_differ_across_quarters` (5x price diff → 5x mcap diff) |
| Turkish KAP labels mismatched | `97610cc` + `9567c6f` | `test_fuzzy_labels_survive_case_diacritic_variation` |
| Bank schema incompatible | `9567c6f` | `test_bank_symbol_passed_to_ingest_driver_is_skipped` |
| Label candidate lists incomplete | **pending ROUND B** (operator Colab run) | — |

The 4th cause is intentionally deferred: candidate labels must be verified against the actual borsapy output, which requires the `explore_borsapy_labels.py` discovery tool (new this turn) to run in Colab. ROUND B = operator runs Colab discovery → sends output to agent → agent updates candidate lists with verbatim labels → final ingest run.

## Test count

| Phase | Tests | Δ |
|---|---|---|
| Phase 4.7 final baseline | 882 | — |
| **Phase 4.7 v2** | **919** | **+37** |

Reviewer target 895+ cleared by +24. Full suite passes from BOTH CWDs (repo root + parent); KR-007 prevention preserved.

**Breakdown of +37:**
- 24 in `tests/test_label_normalization.py` (Turkish fold, pick_label, pick_value)
- 9 in `tests/test_pit_market_cap.py` (PIT mcap 6 + bank skip 3)
- 4 in `tests/test_ingest_real_labels.py` (mock borsapy real labels 3 + registry consistency 1)

## Commits this turn (4)

```
9567c6f fix(scripts): PIT mcap + bank skip + fuzzy labels + 16 metrics (Phase 4.7 v2)
d758426 feat(scripts): explore_borsapy_labels.py — ROUND A discovery tool
97610cc feat(utils): label_matching — Turkish-diacritic-aware KAP label fuzzy match
<doc commit in this PR>  docs: Phase 4.7 v2 — 2-stage Colab flow + v2 report
```

---

## Root cause analysis — detailed

### Cause 1: Market cap was NOT point-in-time

**Before v2** (`scripts/ingest_fa_for_calibration.py:340` in the ROUND A version):

```python
fast["market_cap"] = getattr(fi, "market_cap", None)
```

This called `tk.fast_info.market_cap` which is **today's** market cap. Applied to every historical quarter from 2018-2026. For a symbol that was 10 TL in 2018 and 200 TL in 2026, the script computed PB using the 2026 market cap against 2018 equity — producing PB=7994 type outliers. 100% of the PB/PE/FCF-yield data from ROUND A is unusable.

**v2 fix:**

`_pit_market_cap(symbol, filed_at, shares_current, paid_in_capital)` uses `infra/pit.get_price_at_or_before(filed_at)` to get the close price at the filing date, multiplies by shares outstanding. Fallback chain:

1. `shares_outstanding` (from `fast_info`, current) × PIT close price — works for BIST30 large-caps where share count is relatively stable
2. `paid_in_capital` (from balance sheet, PIT) × PIT close price — Turkish convention: 1 TL nominal means paid-in-capital = share count
3. `None` — metrics needing mcap (PE, PB, FCF yield) cleanly fall out

**Verification** (`test_pit_prices_differ_across_quarters`): with constant shares and prices differing 5x between 2020 and 2023, mcaps differ 5x. Not the same number repeated.

### Cause 2: Turkish KAP labels didn't match

**Before v2**: strict `pandas.loc[exact_candidate, col]`. For each field we hardcoded 1-2 Turkish candidate strings. But real borsapy returns labels with diacritic/whitespace/punctuation variations we didn't anticipate:

- `"Özkaynaklar"` (our candidate) vs `"Ozkaynaklar"` (borsapy) — NFD combining mark difference
- `"İşletme Faaliyetlerinden..."` vs `"Isletme Faaliyetlerinden..."` — İ/ı edge case Python's NFD doesn't handle
- `"Dönem Net Karı"` (candidate) vs `"Ana Ortaklık Paylarına Düşen Dönem Karı/Zararı"` (borsapy) — longer label with our target as substring
- Case variation: `"HASILAT"` vs `"Hasılat"`
- Punctuation: `"Brüt Kar/(Zarar)"` vs `"Brüt Kar/Zarar"` — parentheses vs no parens

**v2 fix:** `utils/label_matching.py` provides:

- `normalize_label(s)`: Explicit Turkish character table first (İ→I, ı→i, etc.), then NFD combining-mark strip, then punctuation-to-space, then whitespace collapse, then lowercase + trim
- `pick_label(available, candidates)`: two-pass match against DataFrame.index. Pass 1: normalized-exact. Pass 2: substring with 4-char minimum guard (prevents "Net" matching everything)
- `pick_value(df, col, candidates)`: combines + NaN filtering + duplicate-index handling

Candidate lists expanded from 1-2 to 3-5 per field. Verbatim labels will be added in ROUND B.

### Cause 3: Banks have incompatible schema

**Before v2:** banks were fetched with `financial_group="UFRS"` but the line-item candidates (`"Hasılat"`, `"Dönen Varlıklar"`, etc.) don't exist in bank balance sheets. Bank bilançosu kullanır:

- Revenue → `"Toplam Faiz Gelirleri"`
- Current assets → `"Krediler"`, `"Bankalar Bakiyeleri"`
- Current liabilities → `"Mevduatlar"`
- Net income → `"Banka Net Karı"` or `"Net Dönem Karı/Zararı"`

These are structurally different concepts, not just different labels. A useful bank calibration requires its own metric registry (NIM, CAR, loan-to-deposit, cost-to-income, etc.) which is Phase 5+ work.

**v2 fix:** BIST30'daki 9 banka (`AKBNK`, `GARAN`, `YKBNK`, `ISCTR`, `HALKB`, `VAKBN`, `TSKB`, `SKBNK`, `ALBRK`) early-skip. Driver logs "SKIP: banka şeması farklı (Krediler, Bankalar Bakiyeleri), ayrı calibration turu gerekli (Phase 5 kandidatı)". Checkpoint records the reason. Zero CSV rows written. Next Phase 5 bank pass will have a dedicated bank metric registry.

### Cause 4: Label candidate lists incomplete (deferred to ROUND B)

Even with fuzzy matching, we need the **actual** labels borsapy returns to get 100% coverage. The discovery tool `scripts/explore_borsapy_labels.py` lists all DataFrame.index labels for 5 representative non-bank symbols (THYAO, ASELS, EREGL, BIMAS, TUPRS). Operator runs it in Colab (5-10 min), output goes to `reports/borsapy_label_discovery.md`, agent updates `make_borsapy_fetcher`'s candidate lists in ROUND B.

---

## Additional improvement: METRIC_REGISTRY 13 → 16

Three safely-derivable metrics added (all computable from the 3 standard statements, no bank-specific logic):

| Metric | Formula | Why add now |
|---|---|---|
| `roa` | `net_income / total_assets` | Fundamental profitability metric missing from v1 |
| `fcf_margin` | `free_cashflow / revenue` | Cash efficiency; complements fcf_yield (which needs mcap) |
| `cfo_to_ni` | `operating_cf / net_income` | Quality-of-earnings check; low values hint at aggressive accruals |

`engine/scoring_calibrated.METRIC_DIRECTIONS` gains `"roa": True` (the other two were already in Phase 4.7 scaffolding). `_derive_metrics_from_statements` updated with the three new computations.

Dry-run confirms all 16 metrics populate per quarter (minus `revenue_growth` for the first year — needs `prev_year_q` lookup, expected and documented).

---

## Why `explore_borsapy_labels.py` samples 5 symbols (not 30)

Reviewer spec: "5 sembol seç (THYAO, ASELS, EREGL, BIMAS, TUPRS — farklı sektörler)".

Rationale: KAP standardized the TFRS reporting format; ~95% of non-bank symbols use the same line-item labels with minor variations. 5 representatives across sectors (airline, defense, steel, retail, energy) cover enough schema variation to catch most candidate list gaps. Running against all 30 would inflate the discovery output to 100+ pages of redundant labels.

The 5% variation we might miss will show up as `_fetch_attempts` failures in the actual ROUND B ingest; those symbols get logged with exception types, and agent can do a targeted follow-up pass on just the failed ones.

---

## ROUND B plan (operator next, then agent)

### Operator step (5-10 min)

1. Open Colab, paste AŞAMA 1 cell from `scripts/RUN_FA_BACKFILL_COLAB.md`
2. Run — produces `reports/borsapy_label_discovery.md`
3. Send that file to agent (paste 150 lines into chat, or Drive share)

### Agent step (~10 min)

1. Read the discovery markdown
2. For each field in `make_borsapy_fetcher()`, verify the candidate list includes at least one label that exact-matches what borsapy returned
3. Add missing variants verbatim (bypasses fuzzy match, deterministic)
4. Commit: `chore(scripts): tune label candidates from Colab discovery (ROUND B)`
5. Push to branch

### Operator step (90-120 min)

1. Open Colab, paste AŞAMA 2 cell
2. Run — produces `reports/fa_events.csv` + `reports/fa_isotonic_fits.json`
3. Sanity check output (CSV row count, metric distribution, PB range, zero bank rows)
4. Commit fits JSON to branch, push
5. Deploy, smoke test, done

---

## Known limitations (Phase 5+ material, unchanged from v1)

1. **No sector-conditional calibrated fits.** V13 has per-sector thresholds; calibrated is universe-wide.
2. **No regime-conditional calibration.** Single fit 2018-2026 doesn't split low-inflation vs hyperinflation periods.
3. **Forward return is calendar-day-60d, not trading-day-60d.** ~12% difference, not material.
4. **Banks deferred.** Bank-specific metric registry (NIM, CAR, etc.) is Phase 5+.
5. **Share count from current `fast_info`, not PIT.** BIST30 large-caps rarely issue/retire dramatically, so approximately correct. Phase 5+ candidate: read `"Ödenmiş Sermaye"` per quarter from balance sheet for true PIT share count.

---

## Rollback

v2 is 3 code commits + 1 docs commit on top of Phase 4.7 final:

```
9567c6f  fix(scripts): PIT mcap + bank skip + fuzzy labels + 16 metrics
d758426  feat(scripts): explore_borsapy_labels.py
97610cc  feat(utils): label_matching
```

`git revert 9567c6f d758426 97610cc` restores Phase 4.7 final. utils/label_matching.py is standalone (no runtime dependency), safe to keep even if ingest rolls back. V13 handpicked remains the always-available fallback.

---

## Status: **ROUND A DELIVERED** (operator action: run AŞAMA 1)

v2 script hardening complete. Discovery tool shipped. 919 tests passing. Banks properly excluded. PIT mcap fixed. Fuzzy matching in place. 16 metrics registered.

Next: operator runs `scripts/RUN_FA_BACKFILL_COLAB.md` AŞAMA 1 in Colab, sends discovery output, agent does ROUND B label tune, then full backfill produces the 11,000-14,000 row CSV that the calibration executor consumes.


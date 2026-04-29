# borsapy Label Discovery — Phase 4.7 v2 ROUND B

**Generated:** Phase 4.7 v2 ROUND A (Colab operator run)
**Consumed:** Phase 4.7 v3 ROUND B (agent label tune, this turn)

This document records the ground-truth DataFrame.index labels that
borsapy returns for Turkish KAP quarterly statements. Audit trail
for why `scripts/ingest_fa_for_calibration.py` has its current
candidate lists.

Symbols sampled: THYAO, ASELS, EREGL, BIMAS, TUPRS (5 non-bank
sectors). All labels below were observed in at least 3 of the 5
symbols, making them stable across non-bank schemas.

## Income statement — `get_income_stmt(quarterly=True)`

```
'Satış Gelirleri'                                         ← revenue
'Satışların Maliyeti (-)'                                 ← COGS
'BRÜT KAR (ZARAR)'                                        ← gross profit (ALL CAPS)
'Pazarlama, Satış ve Dağıtım Giderleri (-)'
'Genel Yönetim Giderleri (-)'
'Araştırma ve Geliştirme Giderleri (-)'
'Diğer Faaliyet Gelirleri'
'Diğer Faaliyet Giderleri (-)'
'FAALİYET KARI (ZARARI)'                                  ← operating income (ALL CAPS)
'Net Faaliyet Kar/Zararı'
'Finansman Gideri Öncesi Faaliyet Karı/Zararı'            ← EBIT proxy (used)
'(Esas Faaliyet Dışı) Finansal Gelirler'
'(Esas Faaliyet Dışı) Finansal Giderler (-)'              ← interest expense (used)
'SÜRDÜRÜLEN FAALİYETLER VERGİ ÖNCESİ KARI (ZARARI)'
'SÜRDÜRÜLEN FAALİYETLER DÖNEM KARI/ZARARI'
'DÖNEM KARI (ZARARI)'                                     ← net income (ALL CAPS, used)
'Ana Ortaklık Payları'                                    ← attributable NI
```

## Balance sheet — `get_balance_sheet(quarterly=True)`

**Critical duplicate:** `Finansal Borçlar` appears TWICE, under
`Kısa Vadeli Yükümlülükler` (current) and `Uzun Vadeli Yükümlülükler`
(long-term). Total debt = SUM of both rows. Handled via
`utils.label_matching.pick_all_values`.

```
'Dönen Varlıklar'                                ← current assets
'  Nakit ve Nakit Benzerleri'                    ← cash (2-space indent)
'  Ticari Alacaklar'
'  Stoklar'
'Duran Varlıklar'                                ← non-current assets
'  Maddi Duran Varlıklar'                        ← PP&E
'TOPLAM VARLIKLAR'                               ← total assets (ALL CAPS)
'Kısa Vadeli Yükümlülükler'                      ← current liabilities
'  Finansal Borçlar'                             ← short-term debt (duplicate index!)
'Uzun Vadeli Yükümlülükler'                      ← long-term liabilities
'  Finansal Borçlar'                             ← long-term debt (same label as above!)
'Özkaynaklar'                                    ← equity
'  Ana Ortaklığa Ait Özkaynaklar'                ← attributable equity
'  Ödenmiş Sermaye'                              ← paid-in capital (used for shares proxy)
'  Dönem Net Kar/Zararı'                         ← current period NI inside equity
'TOPLAM KAYNAKLAR'                               ← total liab + equity
```

## Cash flow — `get_cashflow(quarterly=True)`

```
'Amortisman Giderleri'                                       ← depreciation (used for EBITDA)
' Düzeltme Öncesi Kar'                                       ← pre-adjustment income (1-space prefix)
' İşletme Faaliyetlerinden Kaynaklanan Net Nakit'            ← operating CF (used)
' Yatırım Faaliyetlerinden Kaynaklanan Nakit'                ← investing CF
'Serbest Nakit Akım'                                         ← FCF (note: 'Akım' not 'Akışı'!) (used)
'Finansman Faaliyetlerden Kaynaklanan Nakit'                 ← financing CF
```

## Key observations — differences from v2 candidate lists

| Field | v2 candidate (wrong) | v3 ground-truth | Cause |
|---|---|---|---|
| revenue | `Hasılat` (primary) | `Satış Gelirleri` | Most symbols use 'Satış Gelirleri'; 'Hasılat' is rare |
| gross_profit | `Brüt Kar` | `BRÜT KAR (ZARAR)` | All caps — normalize_label case-folds, matches now |
| operating_income | `Esas Faaliyet Karı` | `FAALİYET KARI (ZARARI)` | All caps, parentheses — punctuation strip handles |
| net_income | `Dönem Net Karı` | `DÖNEM KARI (ZARARI)` | All caps, parens — case-fold + punct strip |
| ebit | `FAVÖK` (primary) | `Finansman Gideri Öncesi Faaliyet Karı/Zararı` | KAP doesn't have FAVÖK row; this is literal EBIT |
| interest_expense | `Finansman Giderleri` | `(Esas Faaliyet Dışı) Finansal Giderler (-)` | Substring match: 'finansal giderler' inside longer label |
| free_cashflow | `Serbest Nakit Akışı` | `Serbest Nakit Akım` | 'Akım' not 'Akışı' — different word, needs explicit candidate |
| operating_cf | `İşletme Faaliyetlerinden Sağlanan Nakit Akışı` | ` İşletme Faaliyetlerinden Kaynaklanan Net Nakit` | 'Kaynaklanan' not 'Sağlanan', 1-space prefix |
| total_debt | `Toplam Finansal Borçlar` (single) | `Finansal Borçlar` × 2 (sum) | Duplicate-label summation required |

## Why `Ana Ortaklık Payları` isn't used as primary net_income

ROE purists prefer attributable (parent-only) net income. But:
- `DÖNEM KARI (ZARARI)` is the consolidated NI that matches how
  `engine/metrics.py` derives NI in the main product pipeline
- Phase 4.7 calibration must be consistent with production scoring
- Using attributable NI here + consolidated NI in production would
  produce calibration-vs-scoring mismatches at runtime
- If future Phase 5 work moves production to attributable NI, the
  candidate list swaps in one line

## Banks not sampled

BIST30 banks (AKBNK, GARAN, YKBNK, ISCTR, HALKB, VAKBN) use a
completely different KAP schema: `Krediler`, `Bankalar Bakiyeleri`,
`Mevduatlar`, `Toplam Faiz Gelirleri`, etc. These require a dedicated
bank metric registry (NIM, CAR, loan-to-deposit ratio, cost-to-income)
and are deferred to Phase 5+. Banks are early-skipped in
`scripts/ingest_fa_for_calibration.py:ingest_symbols` via the
`BANK_SYMBOLS` frozenset — log line "SKIP: banka şeması farklı".

## Rollback

If a future borsapy schema change invalidates these labels, revert
to a stricter exact-match lookup by editing `make_borsapy_fetcher`
candidate lists. The `utils.label_matching` fuzzy layer will still
fold diacritics/case so minor drifts don't break the pipeline.

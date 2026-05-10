# BULLALFA v1.4 — İlerleme Özeti

**Durum:** Milestone A (Foundation), B (Calibration / Risk / TOPLANIYOR / Ranking / Why-Now) ve C (Orchestration) **tamam**. Milestone D (API + final deliverables) **bekliyor**.

**Test özeti:**
```
BullAlfa testleri:    331 passed
Mevcut paket testler: 1260 passed, 13 skipped (regresyon yok)
```

---

## 1. Milestone A — Foundation (tamamlandı)

| Dosya | İçerik | Satır |
|------|---------|------|
| `engine/bullalfa_params.py` | Tüm sayısal heuristic'lerin tek kaynağı (`BULLALFA_PARAMS` dict + `macro_multiplier`, `grade_from_score`, `quality_min_for_mode`, `rvol_threshold`, `breakout_bars`, `is_e5_skipped`, `stop_atr_mult`, `max_hold_bars`, `trail_rule`, `benchmark_for_sector`, `gyo_keywords`, `newly_listed_allowed_modes`, `halted_forced_mode` accessors). Yükleme anında `_validate_weight_tables` ile combo/tech/edge ağırlıklarının 1.0'e, accumulation_strength ağırlıklarının 100.0'e toplandığı doğrulanır. `SCHEMA_VERSION = "1.4"`. | 622 |
| `features/bullalfa_features.py` | `EngineInputs` frozen dataclass; orchestrator-tarafı primitive üretimi (`build_engine_inputs`: EMA20/50/200, breakout-bars-since, BB-width 60-bar percentile'lar, Wilder ADX 10-bar geriye); E1–E7 saf engine fonksiyonları; `detect_pullback_to_breakout`; `compute_engines` (§19 schema dict). | 707 |
| `features/bullalfa_sector.py` | `SectorContext` dataclass; `detect_gyo` keyword override (engine/scoring.py'a dokunmadan); `base_sector_group`; `get_benchmark` XU100 fallback; `cap_grade`; `filter_modes`; `resolve_sector_context` (kwargs: `yf_sector=`, `yf_industry=`, `history_length_days=`, `is_halted=`, `available_benchmarks=`). Halted > newly_listed > base sector öncelik sırası. | 290 |
| `tests/test_bullalfa_engines.py` | 96 test — E1–E7 birim testleri, tie-breaker, `compute_engines` schema, `build_engine_inputs` entegrasyonu. Synthetic uptrend fixture: rng.normal(0.20, 0.30, 250) (~3:1 SNR) — EMA20>EMA50>EMA200 garantili. | 561 |
| `tests/test_bullalfa_sector.py` | 28 test — REIT detection, newly-listed sınırları, sector mapping, benchmark fallback, grade caps, mode filtering, full context resolution. | 314 |

**Toplam:** 124 test, hepsi yeşil.

---

## 2. Milestone B — Calibration · Risk · TOPLANIYOR · Ranking · Why-Now (tamamlandı)

| Dosya | İçerik | Satır |
|------|---------|------|
| `features/bullalfa_calibration.py` | Layer 3 — `sigmoid_squash` (overflow-safe ±1e9), `combo_weights_for_mode`, `combine_raw`, `apply_dampeners` (multiplicative chain with input clamping), `compute_confidence` (returns §19 confidence-block shape; raises on non-actionable mode), `calibration_phase` (v2 isotonic hook). | 209 |
| `features/bullalfa_risk.py` | Layer 4 — `build_risk_frame` (entry zone via params multipliers, ATR-multiple stop, 1R/2R/3R, Türkçe invalidation), `validate_risk_frame` (7 invariants with stable failure codes `inv1_entry_band` … `inv7_max_hold_positive` plus `missing_frame`, `missing_key:*`), `try_build_risk_frame` (returns `(frame, downgrade_reason, [TR_caveat, *failure_codes])`). `DOWNGRADE_CAVEAT_TR = "Kurulum şekilleniyor — risk çerçevesi henüz net değil."` | 253 |
| `features/bullalfa_toplaniyor.py` | §12 — `ToplaniyorAssessment` frozen dataclass (`eligible`, `required_failures`, `corroborating_passes`, `accumulation_strength`, `blocker`); `evaluate_toplaniyor` (D-grade exclusion, trend intact via price>ema50 OR ema20>ema50, BB compression, rvol band open-interval); `compute_accumulation_strength` (4 components — adx_rise/tightness/buying_pressure/structure — weighted 25/30/25/20, sum-validated to 100). | 333 |
| `features/bullalfa_ranking.py` | §17 — `opportunity_score` total function (her mode → integer ∈ [0, 100], missing input → 0; bir hisse universe'den asla çıkmaz). `sector_concentration_alert` (alpha tie-break, deterministic). | 133 |
| `features/bullalfa_why_now.py` | §18 — Mode-routed Türkçe bullets, locked phrasing. SAKİN → `[]` (UI'da `Şu an dikkat çekici bir kurulum yok` tek satır). `MAX_BULLETS=4`, `MIN_BULLETS=2`, dedup with order preservation. | 247 |
| `tests/test_bullalfa_calibration.py` | 44 test — sigmoid bounds + monotonicity + extreme-clamp, weights sum-to-1, defensive copy, dampener chain (her multiplier izole + tam zincir), compute_confidence schema, params mutation guards, phase v1/v2/forced. | 238 |
| `tests/test_bullalfa_risk_frame.py` | 41 test — Per-mode dict/None davranışı, ATR multipliers (1.2/1.8/2.5) + max_hold (5/20/126) sabitleri, 7 invariant her biri targeted-break, inv5 within-tolerance perturbation, `try_build_risk_frame` dört yol (valid/invalid-inputs/invariant-fail-via-monkeypatch/non-actionable). | 274 |
| `tests/test_bullalfa_toplaniyor.py` | 34 test — Required-set her predicate izole bloklar (D-grade, trend, BB, rvol band lower/upper open-interval); corroborating-set her named corroborator others-broken durumda; `no_upgrade` solo eligibility; upgrade-priority over TOPLANIYOR; accumulation_strength bounded/integer/monotone-in-each-component/saturates-at-100. | 410 |
| `tests/test_bullalfa_ranking.py` | 32 test — Spec örnekleri (HIZLI 80→80, SWING 70→70, TOPLANIYOR 90→70, SAKİN 90→18, UZAK DUR→5), missing-input→0, sort stability under random permutation (10 reshuffles), sector concentration banner: at-/above-threshold + multi-sector highest-wins + alphabetical tie-break, surface invariants (SAKİN_MULT × 100 == SAKİN_CAP). | 234 |

**Toplam:** 151 yeni test, hepsi yeşil. Bir tasarım hatası kaldırıldı: korroborating-set testleri `actionable_mode_already_fired=True` ile `no_upgrade` token'ını kapatmaya çalışıyordu, ama o flag aynı zamanda TOPLANIYOR'u priority bloğu ile dışlıyor — tutarsız iki durumun çakışması. Testler "X others-broken iken eligible" şekline yeniden çatıldı.

---

## 3. Milestone C — Orchestration (tamamlandı)

| Dosya | İçerik | Satır |
|------|---------|------|
| `engine/bullalfa_degrade.py` | §15 — `DegradeCode` (10 sabit string), `DegradeAction`, `DEGRADATION_RULES` dict (spec'i birebir mirror), `DegradationOutcome` frozen dataclass, `DegradationLog` (mutable, append-only, dedup, `has`/`any_force_sakin`/`any_force_uzak_dur`/`any_freeze`/`limited_mode_set`/`caveats` methods), `rule_for`/`action_for`/`caveat_for`/`caveats_for` accessors. | 244 |
| `engine/bullalfa.py` | Ana orchestrator. Public API: `build_bullalfa_signal(ticker, hist_df, bench_df, metrics, sector_raw, industry_raw, short_history, halted_today, macro_result, market_status, isotonic_fits, tech_pre, days_listed, now_iso) → §19 dict` ve `build_scan_response(signals, page, per_page, extra_warnings, now_iso) → §19 ScanResponse dict`. Inputs verildiğinde **saf** — veri çekmez. | 1378 |
| `tests/test_bullalfa_degradation.py` | 28 test — `DegradationLog` (12: append+dedup+caveats+limited-mode-set+`rule_for`+rules-table-completeness), her degradation kodu (macro_unavailable, pit_missing, aggregation_failed, freshness_below_60, short_history, halted_today, isotonic_unavailable v1/v2, benchmark_fallback), stacked degradations, schema invariants (degraded signal still has all §19 keys, SAKİN null risk_frame + null horizon, UZAK DUR null risk_frame). | 450 |
| `tests/test_bullalfa_integration.py` | 28 test — TestFullPipeline (4: actionable production, §19 schema match, deterministic regression, never-raises on pathological input); TestOutOfScopeModulesUntouched (parametrized over 8 protected modules — `engine/verdict.py`, `engine/scoring*.py`, `engine/aggregation.py`, `engine/labels.py`, `engine/bullwatch.py`, `engine/technical.py`, `api/bullwatch.py` — public surface snapshot before/after orchestrator run, must be identical); TestScanResponse (13: universe size, by_mode, sector_concentration only-actionable, sort DESC, alpha tie-break, no-mutation, pagination first/last/partial/beyond/none, universe includes all modes, warnings passthrough, empty universe); TestEndToEnd (1: 5 sinyal → scan response). | 442 |

**Toplam:** 56 yeni test, hepsi yeşil.

### Orchestrator mimarisi (engine/bullalfa.py)

| Katman | Sorumluluk | Spec Bölümü |
|------|------|------|
| Layer 0 | `_resolve_macro_state`: regime normalizasyonu (uppercase→lowercase), tl_vol_pct fallback (50.0 = neutral bucket), macro_unavailable kaydı | §6 |
| Sector ctx | `resolve_sector_context` çağrısı, newly_listed/halted/benchmark fallback | §14 |
| Layer 1 | `_compute_quality_surface`: `compute_fa_pure(scores)`, missing-fields → freshness_pct proxy, grade cap if fresh<80, force SAKİN if fresh<60, tags from metrics | §7 |
| Layer 2 | `build_engine_inputs` + `compute_engines` her mode için (HIZLI/SWING/POZİSYON), engine_per_mode dict | §8 |
| Mode classification | Forced override (UZAK DUR > halted-degraded > SAKİN-degraded), sonra priority HIZLI > SWING > POZİSYON > TOPLANIYOR > SAKİN. Predicates: `_hizli_conditions_met`, `_swing_conditions_met`, `_pozisyon_conditions_met`, `_uzak_dur_forced` | §11 |
| TOPLANIYOR routing | `evaluate_toplaniyor` → TOPLANIYOR or SAKİN (sector_ctx.allowed_modes ile filtre) | §12 |
| Liquidity gates | ADV<1M → TOPLANIYOR, ADV<5M+HIZLI → SWING(if eligible)/TOPLANIYOR, ADV<10M → confidence×0.85 | §11 |
| Session gate | HIZLI→TOPLANIYOR if minutes_to_close<30 (ist_time string'inden 18:00 / 12:30 half-day'e karşı türetilir) | §11 |
| Layer 3 | `compute_confidence` × liquidity multiplier, isotonic_unavailable kodu fits=None ise loglanır | §9 |
| Layer 4 | `try_build_risk_frame`; invariant fail → actionable→TOPLANIYOR (or SAKİN if not allowed). `risk_frame_downgraded` flag, "Kurulum şekilleniyor" caveat'ı yalnızca TOPLANIYOR fallback'inde surface eder, SAKİN cascade'inde değil. Failure codes user-facing caveat'lara sızdırılmaz — yalnızca DOWNGRADE_CAVEAT_TR | §10 |
| Grade cap | `sector_ctx.grade_cap` (e.g. newly_listed→"B") cap'in üstündeki grade'leri düşürür | §14 |
| Ranking | `opportunity_score` | §17 |
| Why-now | §18 phrasing, mode-routed | §18 |
| Caveats | `log_obj.caveats()` ∪ `sector_ctx.caveats` ∪ (risk-frame TR if downgraded to TOPLANIYOR) | §15 |
| Warnings | §16 hygiene — Spekülatif yapı (HIZLI+D), Kalite zayıf (HIZLI/SWING+C/D), Veri eski (fresh<80), Düşük likidite (ADV<10M), Sektör endeksi yok (benchmark fallback), Kalibrasyon: ön-aşama (v1) | §16 |
| Lifecycle | placeholder `signal_id`/`triggered_at`/`status`/`mode_history`. Full tracking post-v1.4 | §19 |
| Scan response | `build_scan_response`: stable sort (opp DESC, ticker ASC), by_mode counts, sector_concentration only counts actionable modes, pagination, no-mutation | §19 |

### Out-of-scope modüller dokunulmadı

Test ile doğrulandı (`TestOutOfScopeModulesUntouched`): `engine/verdict.py`, `engine/scoring.py`, `engine/scoring_calibrated.py`, `engine/scoring_v11.py`, `engine/aggregation.py`, `engine/labels.py`, `engine/bullwatch.py`, `engine/technical.py`, `api/bullwatch.py` — orchestrator çalışmadan önce ve sonra public surface snapshot'ları aynı.

---

## 4. Açık sorular (defensible defaults uygulandı)

Spec ile codebase arasında 5 mismatch tespit edildi. Sen `devam et` dediğin için her birinde defensible default uyguladım — her biri tek bir noktada yaşıyor, kararı geri çevirmek istersen kolay swap.

| # | Spec çağrısı | Codebase'de var mı? | Uygulanan default |
|---|---|---|---|
| Q1 | `engine.aggregation.aggregate(...)["temel_score"]` | Yok — yalnızca `compute_fa_pure(scores)` var | `engine.scoring.compute_fa_pure({"quality":…,"value":…,…})` ile 7-boyut weighted average. `engine/scoring.py`'a dokunulmadı |
| Q2 | `engine.data_quality.freshness_pct(metrics)` ve `freshness_penalty(metrics)` | Yok — yalnızca `assess_data_quality(metrics)` var | `assess_data_quality()["missing_count"]` 5 kritik field'a karşı oranlanarak `freshness_pct` türetildi (proxy) |
| Q3 | `engine.scoring.sector_group("gyo")` | gyo (REIT) sınıfı yok | `features/bullalfa_sector.py::detect_gyo` keyword override (REIT/Real Estate/Gayrimenkul). `engine/scoring.py`'a dokunulmadı |
| Q4 | `engine.technical.compute_technical()` ⊃ EMAs/breakouts/BB-percentiles/ADX-10-bar-geriye | Kısmen — `compute_technical` ATR/RSI/ADX-now/MACD veriyor; EMA'lar/breakouts/BB-percentiles/ADX-history yok | BullAlfa-tarafında `build_engine_inputs` OHLCV'den hepsini türetiyor (EMA `ewm(adjust=False, min_periods=span)`, breakouts via rolling-max, BB 60-bar percentiles, Wilder ADX `n_back=10`) |
| Q5 | `engine.macro_decision.current_regime()` ve `engine.macro_signals.tl_volatility_percentile(252)` | Yok — yalnızca `compute_regime(inputs)` var, `RegimeResult.regime ∈ {"RISK_ON","NEUTRAL","RISK_OFF"}` (uppercase). tl_volatility_percentile hiç yok | Orchestrator'da `_resolve_macro_state` adapter: regime lowercase'e normalize, tl_vol_pct yoksa 50.0 (neutral bucket) fallback, eksik macro_result → `macro_unavailable` kaydı |

**Bonus**: `utils.market_status` da spec ile uyuşmuyor — spec `is_market_open()` ve `minutes_to_close()` çağırıyor; var olan `get_market_status()` dict döndürüyor. `_resolve_session_state` adapter'ı dict'ten türetiyor (status=="open" + ist_time'dan 18:00 / 12:30 half-day'e fark).

Hiçbiri mimariyi kilitlemiyor. Senden doğru cevap geldikten sonra orchestrator'daki **bir** callsite değiştirilerek geri alınabilir.

---

## 5. Milestone D — sırada (bekliyor)

Handoff §6'ya göre kalan iş:

- API endpoints: `api/bullalfa.py` (`/scan`, `/signal/{ticker}`, scan caching, pagination)
- Frontend hookup (kart şablonları §23: actionable/TOPLANIYOR/SAKİN/UZAK DUR — mobile-first ~380px)
- Final deliverables raporu:
  - Master changed-files tablosu
  - 3 endpoint örneği (curl + JSON cevap)
  - 14-stock spot-check tablosu (ASELS, FROTO, TCELL, TUPRS, AKBNK, GARAN, SAHOL, KCHOL, EREGL, BIMAS, KAPLM, FORTE + 1 IPO + 1 halted)
  - Full test results
  - Regression check (out-of-scope modüller değişmedi doğrulaması)
  - Known limitations
  - v2 calibration plan (`research/bullalfa_walkforward.py`)

---

## 6. Birikmiş uyarılar (test çıktısından)

- **Pre-existing-broken testler** (Milestone A öncesi de kırıktı, bizim değişiklikler değil):
  - `tests/test_phase4.py` — 15 fail, `/mnt/user-data/uploads/deep_events.csv` data dosyası yok
  - `tests/test_phase4_3.py` — 9 error, aynı data dependency
  - `tests/test_phase4_6.py` — 1 fail, aynı
  - `tests/test_cross_hunter_v3.py` — 4 fail, pandas length-mismatch fixture hatası (newer pandas strict)

  `grep -l "bullalfa" tests/test_phase4*.py tests/test_cross_hunter_v3.py` boş döner — hiçbirinde BullAlfa modüllerine referans yok. Bunlar ortamsal, bizim değişikliklerden değil.

- **DeprecationWarning** (cosmetic): `datetime.datetime.utcnow()` Python 3.12+'da deprecated. Tek satır fix (Milestone D'de uygulanacak): `_dt.datetime.now(_dt.timezone.utc)` ile değiştir, isoformat'ı koru.

---

## 7. Dosya konumları (özet)

```
engine/
  bullalfa.py                         (NEW — Milestone C, 1378 LOC)
  bullalfa_degrade.py                 (NEW — Milestone C,  244 LOC)
  bullalfa_params.py                  (Milestone A, 622 LOC; Milestone C +SCHEMA_VERSION)

features/
  bullalfa_features.py                (Milestone A, 707 LOC)
  bullalfa_sector.py                  (Milestone A, 290 LOC)
  bullalfa_calibration.py             (Milestone B, 209 LOC)
  bullalfa_risk.py                    (Milestone B, 253 LOC)
  bullalfa_toplaniyor.py              (Milestone B, 333 LOC)
  bullalfa_ranking.py                 (Milestone B, 133 LOC)
  bullalfa_why_now.py                 (Milestone B, 247 LOC)

tests/
  test_bullalfa_engines.py            (Milestone A,  96 tests)
  test_bullalfa_sector.py             (Milestone A,  28 tests)
  test_bullalfa_calibration.py        (Milestone B,  44 tests)
  test_bullalfa_risk_frame.py         (Milestone B,  41 tests)
  test_bullalfa_toplaniyor.py         (Milestone B,  34 tests)
  test_bullalfa_ranking.py            (Milestone B,  32 tests)
  test_bullalfa_degradation.py        (NEW — Milestone C, 28 tests)
  test_bullalfa_integration.py        (NEW — Milestone C, 28 tests)

Toplam: 11 modül + 8 test dosyası, 331 yeşil test.
```

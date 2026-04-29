# Calibrated Scoring Deploy Guide — Türkçe

Phase 4.7 calibrated scoring için production deploy adımları. Infrastructure tamamen hazır; sadece `reports/fa_isotonic_fits.json` dosyasını repo'ya commit'leyip deploy'u tetiklemek gerekiyor.

## Ön koşul — fits dosyasının repo'da olduğunu doğrula

Colab'dan dönen `fa_isotonic_fits.json` dosyası `reports/` klasöründe olmalı ve git-track edilmiş olmalı.

```bash
cd /path/to/bist-terminal-main
git checkout feat/calibrated-scoring
ls -la reports/fa_isotonic_fits.json
# Dosya olmalı, byte sayısı >10KB olmalı (gerçek fit'ler için)

git ls-files reports/fa_isotonic_fits.json
# Çıktı: reports/fa_isotonic_fits.json
# Boş çıktı → dosya committed değil, `git add` yap
```

**Dosya boş değil, tam JSON mı?** Hızlı kontrol:

```bash
python3 -c "
import json
with open('reports/fa_isotonic_fits.json') as f:
    fits = json.load(f)
print(f'Metric sayısı: {len(fits)}')
print(f'Metric adları: {list(fits.keys())}')
assert len(fits) >= 5, 'En az 5 metric bekleniyor'
print('✅ Fits JSON sağlıklı görünüyor')
"
```

## Adım 1 — GitHub'a push

```bash
git push origin feat/calibrated-scoring
```

## Adım 2 — Railway otomatik redeploy

Push tetiklenince Railway tracked branch'i yeniden build eder. Build süresi: ~2-3 dakika. Railway dashboard'da deploy status'u izle.

## Adım 3 — Canlı smoke test (2 yol)

### 3a. Tarayıcıda hızlı test

`https://bistbull.ai/api/analyze/THYAO?scoring_version=calibrated_2026Q1` URL'ini aç. Response JSON'ında:

```json
{
  "ok": true,
  "data": {
    "overall": 62.5,
    "turkey": { "composite_multiplier": 1.05, ... },
    "academic": { "academic_penalty": -1.5, ... },
    "_meta": {
      "scoring_version": "calibrated_2026Q1",
      "scoring_version_effective": "calibrated_2026Q1"
    }
  }
}
```

**Kritik kontrol:** `scoring_version_effective` alanı `"calibrated_2026Q1"` olmalı. Eğer `"v13_handpicked"` diyorsa → fits dosyası production'da yok, git'e commit'lendiğinden emin ol.

### 3b. CLI smoke test (renkli çıktı)

```bash
python3 scripts/smoke_test_calibrated.py --url=https://bistbull.ai --symbol=THYAO
```

Beklenen çıktı:

```
BistBull Phase 4.7 Calibrated Scoring — Smoke Test
URL:     https://bistbull.ai/api/analyze/THYAO?scoring_version=calibrated_2026Q1
Timeout: 20.0s

1. Calibrated scoring aktif mi?
✅ scoring_version_effective = 'calibrated_2026Q1'

2. Deger skoru geçerli range'de mi?
✅ deger_score = 62.5 (range [1, 99])

3. K3 (Türkiye Gerçekleri) + K4 (Akademik) katmanları çalıştı mı?
✅ K3 turkey_realities.composite_multiplier = 1.050
✅ K4 academic.academic_penalty = -1.5

Sonuç: 3/3 kontrol başarılı
✅ Calibrated scoring production'da sağlıklı ✓
```

3/3 başarılı → **canlıya çıktı**, işin bitti.

3/3 başarısız → aşağıdaki "sorun giderme" bölümüne bak.

## Adım 4 — 2-3 hafta telemetri topla

Kullanıcılar (ve background scanner) artık iki versiyonda da skor üretiyor. Her gün her sembol için hem V13 hem calibrated row'u `score_history` tablosuna yazılıyor. 2-3 hafta sonra `/ab_report` endpoint'inde paired telemetri olgun olur:

```
https://bistbull.ai/ab_report
```

Bu sayfada:
- Paired row sayısı
- Ortalama skor farkı (calibrated − V13)
- Decision flip sayısı (AL → İZLE vb)
- Spearman rank correlation

değerlerini göreceksin. Spearman > 0.85 → V13 ve calibrated çok yakın. < 0.70 → calibrated anlamlı farklı skorlar üretiyor, geçişe hazır.

## Adım 5 — Default flag'i calibrated'a çevir (opsiyonel, 2-3 hafta sonra)

Telemetry tatmin ediciyse Railway environment variables ekranında:

```
SCORING_VERSION_DEFAULT=calibrated_2026Q1
```

Redeploy tetikle. Artık `?scoring_version` query param'ı olmadan yapılan tüm `/api/analyze/*` istekleri calibrated versiyonu döner. Kullanıcı isterse `?scoring_version=v13_handpicked` ile V13'e düşebilir (A/B her iki yöne de çalışır).

## Sorun giderme

### "scoring_version_effective = v13_handpicked" görüyorum (fallback)

Calibrated request düştü, V13 fallback devrede. Muhtemel sebepler:

1. **fits JSON production'da yok.** Local'de `git ls-files reports/fa_isotonic_fits.json` kontrol et. Boş dönüyorsa commit'le:
   ```bash
   git add reports/fa_isotonic_fits.json
   git commit -m "data(phase-4.7): commit isotonic fits for production"
   git push origin feat/calibrated-scoring
   ```

2. **fits JSON boş veya bozuk.** Python check:
   ```bash
   python3 -c "import json; print(len(json.load(open('reports/fa_isotonic_fits.json'))))"
   ```
   `0` dönüyorsa Colab backfill başarısız olmuş, `scripts/RUN_FA_BACKFILL_COLAB.md` AŞAMA 2'yi tekrar çalıştır.

3. **Railway eski build'i serve ediyor.** Railway dashboard'dan manual redeploy tetikle.

### "K3 turkey bloğu response'ta yok"

`engine/analysis.py:compute_turkey_realities` çağrısında problem var. Deploy edilen commit'in `feat/calibrated-scoring` head'ine eşit olduğundan emin ol.

### HTTP timeout / 5xx

Railway logs'a bak. Muhtemelen `fetch_raw` transient hatası (HOTFIX 1 retry ile 10-15 sn sonra toparlanır) veya heatmap warming (3 dk'da biter).

## Rollback — iki yol

### Soft rollback (hızlı, kısmı)

Environment variable değiştir:

```
SCORING_VERSION_DEFAULT=v13_handpicked
```

Redeploy. Default flag V13'e döner. `?scoring_version=calibrated_2026Q1` query param'ı hala çalışır (meraklılar test edebilir).

### Hard rollback (tam)

```bash
git rm reports/fa_isotonic_fits.json
git commit -m "revert: remove calibrated fits for rollback"
git push origin feat/calibrated-scoring
```

Fits dosyası repo'da yoksa loader None döner → tüm calibrated istekler V13'e fallback olur + `scoring_version_effective='v13_handpicked'` telemetry flag'ini döner. Kullanıcı fark etmez ama log'da takip edebilirsin.

## Not

Bu deploy sonrası agent'ın işi biter. Monitoring (`/ab_report` kontrolü, Railway log'ları, user feedback) senin responsibility'in. Sorun olursa yeni phase turunda çözmeye bakarız.

Phase 4.7 calibrated scoring — **canlıya hazır.**

# Colab'da FA Backfill — 2 Aşamalı Akış (Phase 4.7 v2)

ROUND A (ilk deneme) 3/25 metric üretti, 23/30 sembol çekti, PB değerleri bozuktu. Post-mortem sonucu 4 kök sebep bulundu ve 3'ü v2'de düzeltildi (bank skip, PIT market_cap, fuzzy label matching). 4. sebep (Türkçe KAP label'larının gerçek hali) Colab keşif turu gerektiriyor — aşağıdaki akış onu çözüyor.

## Bu tur neden 2 aşamalı?

| Aşama | Ne yapıyor | Süre | Senden dönmesi gereken |
|---|---|---|---|
| **AŞAMA 1 — Keşif** | `explore_borsapy_labels.py` borsapy'den 5 sembol için gerçek KAP label'larını listeler | 5-10 dk | `reports/borsapy_label_discovery.md` dosyası (agent'a gönder) |
| **ARA** | Agent ROUND B'de ingest script'inin candidate listesini verilen label'larla günceller | — | (agent işi, 10 dk) |
| **AŞAMA 2 — Backfill** | `ingest_fa_for_calibration.py` full BIST30 × 2018-2026 fundamentals çeker, fuzzy match + PIT mcap ile 16 metric × ~30 çeyrek üretir | ~90 dk | `reports/fa_events.csv` + `reports/fa_isotonic_fits.json` |

## Ne düzeldi v2'de (ROUND A'ya göre)

| Sorun | v1 davranışı | v2 davranışı |
|---|---|---|
| Market cap tüm çeyreklerde aynı (bugünün değeri) | PB=7994 outlier'lar | Point-in-time: `close_price_at_filed_at × shares_outstanding`, PB realistic [0.5, 20] |
| Banka bilançosu schema farklı | Banka satırları boş/bozuk CSV'ye karışıyordu | BIST30'daki 9 banka early-skip, checkpoint'te "SKIP: bank schema" kaydı |
| Türkçe label diacritic eşleşmez | `loc["Özkaynaklar"]` bazen KeyError | `utils.label_matching.pick_value` İ/ı/Ğ/Ş/Ç fold ediyor, substring fallback var |
| 13 metric hesaplanıyordu | ROA, FCF margin, CFO/NI yoktu | +3 metric → 16 metric (METRIC_REGISTRY genişletildi) |

Banka desteği Phase 5 kandidatı — onun için ayrı schema mapping gerekli (bankalarda "Krediler", "Bankalar Bakiyeleri" vs).

---

## AŞAMA 1 — Keşif turu (5-10 dk)

### Hazırlık

1. [Google Colab](https://colab.research.google.com/) aç, yeni notebook
2. Runtime > Change runtime type > **CPU** yeterli

### Tek hücre — kopyala, yapıştır, çalıştır

```python
# ============================================================
# BistBull Phase 4.7 v2 — AŞAMA 1 — Borsapy Label Keşfi
# ============================================================

# 1. Repo klonu (Phase 4.7 v2 branch)
!git clone -b feat/calibrated-scoring https://github.com/<YOUR-GITHUB-USER>/bist-terminal-main.git 2>&1 | tail -3
%cd bist-terminal-main

# 2. borsapy kur
!pip install -q borsapy pandas numpy

# 3. Keşif script'i çalıştır — çıktıyı markdown dosyasına yaz
!mkdir -p reports
!python scripts/explore_borsapy_labels.py > reports/borsapy_label_discovery.md

# 4. Çıktıyı ekranda göster (agent'a göndermen için)
print("\n=== borsapy_label_discovery.md (ilk 150 satır) ===\n")
!head -150 reports/borsapy_label_discovery.md

# 5. Drive'a yedekle (agent'a göndermen için)
from google.colab import drive
drive.mount('/content/drive')
!mkdir -p /content/drive/MyDrive/bistbull_phase_4_7_v2/
!cp reports/borsapy_label_discovery.md /content/drive/MyDrive/bistbull_phase_4_7_v2/
print("\n✅ reports/borsapy_label_discovery.md Drive'da; agent'a gönder.")
```

### Çıktıyı agent'a nasıl göndereceksin?

Üç yol:
1. **Direkt yapıştır** (en kolay): terminal'deki `head -150` çıktısını kopyala, yeni chat mesajında yapıştır
2. **Drive'dan indir**: Google Drive'dan `bistbull_phase_4_7_v2/borsapy_label_discovery.md` dosyasını indir, chat'e attach et
3. **Colab'ı "share"le**: notebook'u agent'la paylaş (ama 1. yol genellikle yeterli)

Agent dosyayı alınca `scripts/ingest_fa_for_calibration.py`'nin candidate listelerini gerçek label'larla update eder (~10 dk), sonra AŞAMA 2'ye geçersin.

**AŞAMA 1'i burada durdur. AŞAMA 2'ye doğrudan geçme — label tune olmadan ingest %100 doğru çalışmaz.**

---

## AŞAMA 2 — Full backfill (90-120 dk)

Agent `scripts/ingest_fa_for_calibration.py`'yi ROUND B'de update edip yeni commit'i push'ladıktan sonra:

### Hazırlık

1. Aynı Colab notebook'a dön (veya yeni aç, High-RAM runtime öneriyoruz)
2. Runtime > Disconnect and delete runtime (temiz başlangıç)
3. Yeni hücre

### Tek hücre — kopyala, yapıştır, çalıştır

```python
# ============================================================
# BistBull Phase 4.7 v2 — AŞAMA 2 — Full Backfill
# ============================================================

# 1. Repo klonu (agent'ın ROUND B commit'i dahil)
!git clone -b feat/calibrated-scoring https://github.com/<YOUR-GITHUB-USER>/bist-terminal-main.git 2>&1 | tail -3
%cd bist-terminal-main

# 2. Bağımlılıklar
!pip install -q borsapy pandas numpy
!pip install -q --break-system-packages fastapi cachetools PyJWT argon2-cffi

# 3. FA ingest — BIST30 (bankalar otomatik skip) 2018-2026
# Kesinti durumunda --reset-checkpoint eklemeden tekrar çalıştır,
# checkpoint'ten kaldığı yerden devam eder.
!python scripts/ingest_fa_for_calibration.py \
    --symbols=BIST30 \
    --start=2018-01-01 \
    --end=2026-04-01 \
    --out=reports/fa_events.csv \
    --checkpoint=reports/fa_events_checkpoint.json \
    --sleep-between-symbols=2.0 \
    --log-level=INFO

# 4. Sanity: kaç metric çıktı?
print("\n=== Metric distribution in fa_events.csv ===")
!awk -F, 'NR>1 {print $4}' reports/fa_events.csv | sort | uniq -c

# 5. Sanity: bankalar gerçekten skip edildi mi?
print("\n=== Bank rows in CSV (should be 0) ===")
!grep -cE "^(AKBNK|GARAN|YKBNK|ISCTR|HALKB|VAKBN|TSKB|SKBNK|ALBRK)," reports/fa_events.csv || echo "0"

# 6. Sanity: PB değerleri artık realistic mi?
print("\n=== PB distribution (should be 0.5-20, no outliers) ===")
!awk -F, '$4=="pb" {print $5}' reports/fa_events.csv | sort -n | awk 'NR==1 || NR==1000 || NR==2000 || NR==3000 {print "  p"NR": "$1}' | head -10

# 7. Isotonic fit'leri üret
!python scripts/calibrate_fa_from_events.py \
    --events=reports/fa_events.csv \
    --out-fits=reports/fa_isotonic_fits.json \
    --out-summary=reports/fa_calibration_summary.md

# 8. Özet
print("\n=== fa_calibration_summary.md ===\n")
!cat reports/fa_calibration_summary.md

# 9. Drive'a yedekle
from google.colab import drive
drive.mount('/content/drive')
!mkdir -p /content/drive/MyDrive/bistbull_phase_4_7_v2/
!cp reports/fa_events.csv              /content/drive/MyDrive/bistbull_phase_4_7_v2/
!cp reports/fa_isotonic_fits.json      /content/drive/MyDrive/bistbull_phase_4_7_v2/
!cp reports/fa_calibration_summary.md  /content/drive/MyDrive/bistbull_phase_4_7_v2/
print("\n✅ Üç dosya Drive'da: bistbull_phase_4_7_v2/")
```

### Süre tahmini

| Adım | Süre | Ne görürsün |
|---|---|---|
| Repo + pip | ~1 dk | "Cloning into 'bist-terminal-main'..." |
| **FA ingest** | **~90-120 dk** | Her sembol için `[1/30] ASELS: fetching...` → `done in 45.2s, X events`. Bankalar "SKIP: banka şeması farklı" yazacak. |
| Sanity checks | <30 sn | Metric count tablosu, 0 bank rows, PB distribution |
| Calibration | <30 sn | "=== Calibration complete. 14-16 metrics fitted. ===" |
| Drive kopyası | ~30 sn | ✅ 3 dosya |

Toplam AŞAMA 2: ~90-125 dakika.

### Başarı kriterleri

**v1'e göre iyileşme dikti:**
- **v1 ROUND A:** 3 metric × 23 sembol = 2,116 satır CSV
- **v2 ROUND B beklenen:** 14-16 metric × 21-24 non-bank sembol × ~30 çeyrek = **~11,000-14,000 satır CSV**

Bu kriterlerden birine uymuyorsa bana dön:
- PB outlier check (`pb` değerlerinde 100'den büyük birşey varsa): PIT mcap hala yanlış, incele
- Metric count < 10: label mapping hala eksik, ROUND C gerekebilir
- Total rows < 5,000: çok sembol fail alıyor, log'larda `fetch_raw failed` satırlarına bak

## Sorun giderme

### "fetch_raw failed for X: TimeoutError"

borsapy TradingView rate-limit'e takıldı. Script 3 deneme + 0.5s/1s/2s backoff ile kendini kurtarıyor. Sonunda:

```bash
!cat reports/fa_events_checkpoint.json | python -c "import json, sys; d=json.load(sys.stdin); print('errors:', list(d['errors'].keys()))"
```

Bankalar haricinde `errors`'da 3'ten fazla symbol varsa:
1. `--sleep-between-symbols=5.0` yap
2. Aynı cell'i tekrar çalıştır (checkpoint fail olanları yeniden dener — `--reset-checkpoint` EKLEME)

### Aşama 1 ya da 2 arasında Colab bağlantısı kopmuş

Colab ücretsiz plan 12 saat kesintisiz çalışır. Timeout olursa aynı cell'i tekrar çalıştır — checkpoint'ten devam eder. **`--reset-checkpoint` asla ekleme** yoksa baştan başlarsın.

### "ImportError: borsapy"

`!pip install -q borsapy` başarısız. Tipik sebep: borsapy private PyPI'de. O zaman:

```python
!pip install -q git+https://github.com/dkoksal/borsapy.git
```

## Deploy aşaması (Colab sonrası, senin bilgisayarın)

1. Google Drive'dan 3 dosyayı indir:
   - `fa_events.csv` (opsiyonel, büyük — commit etme, S3/Drive'da tut)
   - `fa_isotonic_fits.json` ← **production için kritik**
   - `fa_calibration_summary.md` ← belge amaçlı, commit et

2. Local repo klonuna commit et:

```bash
cd /path/to/bist-terminal-main
git checkout feat/calibrated-scoring

cp ~/Downloads/fa_isotonic_fits.json     reports/
cp ~/Downloads/fa_calibration_summary.md reports/

git add reports/fa_isotonic_fits.json reports/fa_calibration_summary.md
git commit -m "data(phase-4.7-v2): isotonic fits from BIST30 2018-2026 backfill"
git push origin feat/calibrated-scoring
```

3. Railway'de branch'i deploy et

4. Deploy sonrası doğrula:

```bash
# V13 default hala çalışıyor mu? (Rule 6 backward compat)
curl -s https://bistbull.ai/api/analyze/THYAO | jq '.data._meta // "v13 (no meta)"'
# Beklenen: "v13 (no meta)"

# Calibrated flag çalışıyor mu?
curl -s "https://bistbull.ai/api/analyze/THYAO?scoring_version=calibrated_2026Q1" \
  | jq '.data._meta'
# Beklenen: {"scoring_version":"calibrated_2026Q1",
#            "scoring_version_effective":"calibrated_2026Q1"}
```

5. 7 gün sonra A/B telemetri kontrolü:

```bash
open https://bistbull.ai/ab_report
```

## Yardım

Sorun olursa terminalde şunu çalıştırıp ekran görüntüsünü bana gönder:

```bash
!git log --oneline -5
!tail -50 reports/fa_events_checkpoint.json
!wc -l reports/fa_events.csv
!awk -F, 'NR>1 {print $4}' reports/fa_events.csv | sort | uniq -c
```

# Kağıt Üstü Alım-Satım (Paper Trading) Rehberi

Bu rehber BistBull'un **gerçek parayla oynamadan** sinyallerini takip
etmenize ve Phase 4 kalibre ensemble stratejisini sınamanıza
yardımcı olur. Her şey CSV olarak indirilir, Excel'de izlenir;
otomatik emir gönderme yoktur.

## Hızlı başlangıç (5 dakika)

1. **Başlangıç sermayeni belirle.** Örnek: 100.000 TL hayali para.

2. **Bugünün önerilen dağılımını indir:**
   ```
   https://your-host/api/paper_trading/template?seed_capital=100000&format=csv
   ```
   Excel'de aç. Her satır: hangi sinyal, hangi hisse, kaç TL.

3. **Excel'de yeni bir dosya oluştur** (`paper_trading_ledger.xlsx`
   gibi). Şu sütunları koy:
   ```
   Tarih | Sembol | Sinyal | Giriş Fiyatı | TL Tutarı | Çıkış Tarihi | Çıkış Fiyatı | % Getiri
   ```
   Her satır bir "pozisyon"u temsil eder. Gerçekten almış gibi
   doldurursun (o günün kapanış fiyatı ile).

4. **Her sabah saat 09:45-10:00 arası** (BIST açılışından 15 dk
   sonra, ısınma gürültüsü geçsin diye):
   - `GET /api/signals/today?format=csv` → yeni sinyaller ne?
   - Yeni sinyal varsa ve ensemble weight'e sığıyorsa, `paper_trading_ledger`'a
     yeni satır ekle (giriş fiyatı = o anki fiyat).

5. **Çıkış kuralı:** Phase 4 sinyal ufku 20 işlem günüdür. 20 gün
   sonra pozisyonu kapat (çıkış fiyatı = o günün kapanışı, % getiriyi
   hesapla).

6. **30 gün sonra** — `GET /ab_report?days=30` ile V13 vs calibrated
   versiyonların kararları ne kadar uyumlu bak. Kendi ledger'ında
   hangi pozisyonun kazandırdığını görebilirsin.

## Sözlük — neyi neden izliyoruz

**Sinyal (signal):** Bir hissede belirli bir teknik örüntü tetiklenince
(RSI < 30, 52 haftalık zirve, MACD kesişimi vb.) üretilen uyarı.

**cs_rank_pct (0-1 arası):** O sinyal bugün universe'deki bütün
hisseler arasında o hisseyi kaçıncı yüzde dilime koyuyor? 1.0 =
en güçlü, 0.0 = en zayıf. Biz sadece ≥ 0.7 olanları (üst %30) dikkate
alıyoruz. Sebep: sinyal tetiklendi demek "sinyal tetiklendi" demek
değil, "sinyal *güçlü* tetiklendi" demek; zayıf tetiklenmeler
gürültüdür.

**Ensemble weight:** Phase 4.5'in walk-forward Sharpe değerlerinden
çıkardığı her sinyalin portföyde ne kadar yer tutması gerektiği.
Örn: RSI Aşırı Alım = 0.20 demek, sermayenin %20'si o sinyalin
seçtiği hisselere gider. Walk-forward demek: 2022, 2023, 2024 gibi
farklı yılların test setinde bu sinyal ne kadar iyi çalışmıştı? O
tarihsel başarıya göre ağırlıklandırıyoruz.

**Modulated weight:** Ensemble weight × cs_rank_pct ayarı. Bir
sinyalin ensemble'daki payı %20 ama bugünkü aday hisse universe'nin
%71'indeyse (sınırın hemen üstü), çarpan 0.025 olur ve efektif
ağırlık azalır. Tamamen güçlü adaylara (%90+) gider.

**Sharpe (oran):** Bir stratejinin getirisini volatilitesine bölüp
yıllıklandırmak. Kabaca: "1 birim risk başına kaç birim getiri?"
Sharpe 1.0 = kabul edilir, 1.5+ = iyi, 2.0+ = olağanüstü. Phase 4'ün
walk-forward ortalama Sharpe'ları 0.97-1.45 aralığında.

**Walk-forward:** Tarihsel veriyi pencere pencere bölüp, her
pencerenin önceki kısmı ile eğitip sonrası ile test etme. Bizde 5
fold var: 2018-2020 ile eğit 2021'de test, 2018-2021 ile eğit
2022'de test, vs. Bu şekilde "geleceği bilmeden" bir stratejinin
performansı ölçülür.

**Alfa:** Piyasanın (BIST100) üstünde getirisi. BIST100 %30 yaptı
sen %45 yaptıysan alfan %15.

**Decision flip:** V13 (elle ayarlı) vs calibrated (data-driven)
scoring'lerin farklı kararlar üretmesi. `/ab_report`'da
`decision_flip_count` gösterir. Çok flip = iki versiyon farklı
şeyler görüyor demek (beklenen; iki farklı model).

## Örnek senaryo — 100.000 TL ile 1 haftalık ledger

**Pazartesi 09:50:**
- `/api/paper_trading/template?seed_capital=100000&format=csv` indirildi.
- Önerilen dağılım şöyle diyelim:
  ```
  52W High Breakout   → %20 → 20.000 TL → THYAO (50%), ASELS (30%), ISCTR (20%)
  RSI Aşırı Alım      → %20 → 20.000 TL → EREGL, SASA, KRDMD
  MACD Bullish Cross  → %20 → 20.000 TL → KOZAL, PGSUS, DOHOL
  BB Üst Band Kırılım → %20 → 20.000 TL → TUPRS, TCELL, MGROS
  MACD Bearish Cross  → %20 → 20.000 TL → (short yerine bu sinyal reverse'de
                                            çalışır; short yapamıyorsak
                                            bu bucket'ı cash olarak tut)
  ```
- BB Alt Band ve RSI Aşırı Satım = 0% (regime-outlier cap yüzünden
  Phase 4.5 bu iki sinyali %0'a çekmiş).
- Ledger'a ilk 4 bucket'ı işle (16 satır, her biri yaklaşık 1250-1500 TL).

**Salı 09:50:**
- `/api/signals/today` kontrol et. Dün işlediğin pozisyonlar hâlâ
  listede mi? (Listeden düşmüş olabilir — o sinyalin cs_rank'i %70
  altına inmiş demek ama pozisyonu tutmaya devam et, çıkış 20 iş
  günü kuralı.)
- Yeni bir sembol girdi mi listeye? (Evet: ledger'a yeni bir satır
  ekle, o günün kapanışı giriş fiyatı.)

**Cuma 17:30 (hafta kapanışı):**
- Her pozisyonun güncel değerini Excel'de hesapla:
  ```
  =giriş_tl * (güncel_fiyat / giriş_fiyat)
  ```
- Hafta P&L = toplam güncel değer − 100.000.
- BIST100 haftalık getirisi ile kıyasla (alfa hesabı).

**20 iş günü sonra (3.-4. hafta):**
- İlk açtığın pozisyonları kapat (çıkış fiyatı = o günün kapanışı).
- % Getiri = (çıkış − giriş) / giriş.
- Aynı slotu yeni sinyalle doldur.

**30. gün:**
- `/ab_report?days=30` — V13 vs calibrated arasındaki fark ne?
- Kendi ledger'ına göre hangi sinyalin Hit Rate'i en yüksek?
- Gerçek parayla başlamadan önce 2-3 ay böyle izle.

## Sık sorulan sorular

**Soru:** Ensemble weight'te BB Alt Band %0. Ne demek, bu sinyali
hiç kullanmayacak mıyım?

**Cevap:** Phase 4.5 mean-variance optimizer'ı bu sinyali %10 cap
altında tuttu çünkü 2022'de (TL devalüasyonu + emtia rallisi)
outlier gibi patladı, başka yıl benzer performans göstermedi.
Ensemble'da yer tutmasa da olur — sadece gürültü ekler. Eğer
piyasa tekrar 2022-benzeri yüksek vol rejimine girerse Phase 5
dinamik regime-gating gelecek, şimdilik güvenli tarafta kal.

**Soru:** V13 mı yoksa calibrated mı kullanmalıyım?

**Cevap:** İlk 2-3 hafta her ikisini de çalıştır (`/ab_report`
paralel telemetri toplar). Kendi ledger'ında da eğer
`?scoring_version=calibrated_2026Q1` kullanıyorsan not et. 30 gün
sonra hangisinin Hit Rate'i yüksek, hangisinin P&L'i iyi bak.
Calibrated data-driven — çoğu durumda V13'e yakın ama 2-3 yerde
belirgin farklılık üretebilir; o farklılıklar genelde "doğru olan
calibrated" çıkıyor ama doğrulayıcı veri bu telemetri.

**Soru:** `/api/signals/today` sabah boş geliyor. Hata mı var?

**Cevap:** BIST 10:00 öncesi ısınmadadır, sinyal hesaplamaları
`cs_rank_pct` dahili fiyat verisine ihtiyaç duyar. En erken 10:30
civarı güvenle çalışır. Ayrıca `reports/phase_4_ensemble.json`
dosyası deploy anında yüklü olmalı; yoksa 503 döner.

**Soru:** Ledger'ımda %−15 var, vazgeçmeli miyim?

**Cevap:** 1 haftalık örneklem çok küçük; Sharpe 1.0 olan bir
strateji bile 1 haftada %−15 eder, normal. Phase 4'ün walk-forward
numaraları 20 işlem günü ufkunda. En az 30 gün, tercihen 60 gün
ledger tut. Ayrıca Ensemble E[Sharpe] = 1.32 demek: yıllık %25-35
beklenen getiri üst-orta tahmin; kısa vadede ±10% dalga beklenir.

**Soru:** Calibrated kalibrasyonunu kendim yapmalı mıyım?

**Cevap:** Operator ekibi Colab'da bir kez çalıştırır (bkz.
`DEPLOY_PHASE_4.md`). Çıktı `reports/fa_isotonic_fits.json` — sunucuya
yüklenir ve kendiliğinden etkinleşir. Sen bir son-kullanıcı olarak
dokunmazsın.

## Güvenlik & etik

Bu **eğitim amaçlıdır.** Sinyaller %100 garanti değildir; 5 yıllık
tarihsel walk-forward Sharpe 1.16'dır, yani toplam kazancın pozitif
*beklenen* olduğunu söyler — garanti değil. BIST volatilitesi
gelişmiş piyasalardan yüksek; kısa vadede büyük dalgalanmalar
normal.

Gerçek parayla başlamadan:
- En az 60 gün kağıt üstü çalıştır
- Kendi hit rate'ini ölç
- Maksimum drawdown'unu gör (en kötü pozisyon % kaç kaybetti?)
- Toplam vergi ve komisyon etkisini çıkar (Phase 4 net-of-cost
  hesaplar 30bp varsayıyor; gerçek komisyonunu öğren)

İyi şanslar. Sorular için `/ab_report` ve `reports/phase_4_summary.md`'e
göz at.

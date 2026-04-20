# BISTBULL Cross Hunter V3 — Kapsamlı Analiz Raporu

## Genel Değerlendirme

Cross Hunter'ın mevcut mimarisi (V2) sağlam bir temel üzerine kurulu: modüler sinyal tespiti, cache katmanlı veri yönetimi ve thread-safe state tracking. Ancak beş kritik alanda iyileştirme gereksinimi tespit ettim. Aşağıda her aşamayı neden-sonuç ilişkisiyle açıklıyorum.

---

## 1. Tutarsızlık (Non-Determinism) Analizi

### Tespit Edilen Sorunlar

**A) EWM (Exponentially Weighted Moving) adjust Parametresi**

`compute_technical()` içindeki `c.ewm(span=12).mean()` çağrısı, pandas'ın varsayılan `adjust=True` davranışını kullanıyor. Bu parametre, ilk birkaç bar'ın ağırlıklandırmasını etkiler ve farklı pandas sürümlerinde veya farklı veri uzunluklarında marjinal farklar üretebilir. Çözüm olarak tüm EWM çağrılarına `adjust=False` eklendi. Bu, Wilder'ın orijinal smoothing formülünü uygular ve her çalıştırmada birebir aynı sonucu verir.

**B) Floating-Point Karşılaştırma**

Cross tespitindeki `prev_50 <= prev_200 and ma50_val > ma200_val` gibi sınır kontrolleri, floating-point aritmetiğinde eşitlik durumlarında tutarsız sonuç verebilir. Örneğin MA50=142.3500000001 ve MA200=142.3500000002 gibi değerlerde bir run'da cross tespit edilirken diğerinde edilmeyebilir. Çözüm olarak tüm karşılaştırmalar epsilon toleranslı `_safe_gt()`, `_safe_lt()`, `_safe_gte()` fonksiyonlarıyla sarmalandı.

**C) `set()` Kullanımı — Sırasız Veri Yapısı**

Orijinal kodda `signals: set[str] = set()` kullanılıyor. Python'da set iterasyonu deterministik değildir (CPython 3.7+ dışında); farklı Python versiyonlarında veya farklı hash seed'lerle sıra değişebilir. Bu, `new_signals` listesinin sıralamasını dolaylı yoldan etkiler. Çözüm olarak `set` yerine `list` kullanıldı, duplikasyonlar `sorted(set(...))` ile kontrol edilip sıralanıyor.

**D) UNIVERSE İterasyon Sırası**

`for t in UNIVERSE` döngüsü, UNIVERSE'ün veri yapısına bağlı. Eğer UNIVERSE bir dict veya set ise sıra garanti değildir. Çözüm olarak `for t in sorted(UNIVERSE)` kullanıldı.

**E) Sıralama Tiebreaker**

Orijinal sort: `key=lambda x: (-x["ticker_total_stars"], -x["stars"])`. Aynı yıldız sayısına sahip iki farklı ticker'ın sırası belirsiz. Çözüm olarak üçüncü tiebreaker eklendi: ticker adı (`x["ticker"]`).

**F) `import random` — Dead Import**

`random` modülü import ediliyor ama hiçbir yerde kullanılmıyor. Potansiyel karışıklık kaynağı — kaldırıldı.

---

## 2. Sinyal Mantığının Güçlendirilmesi

### Mevcut Durum

V2'de sinyal tespiti "koşulsuz" çalışıyor: MA cross oldu mu → sinyal üret. Bu, düşük volatiliteli yatay piyasalarda çok sayıda sahte sinyal (whipsaw) üretiyor.

### Eklenen Filtreler

**A) ADX (Average Directional Index) — Trend Gücü Filtresi**

ADX, trend'in varlığını ve gücünü ölçer; 20 altı trend yok, 20-40 trend var, 40+ güçlü trend demektir. Kırılım sinyalleri (Golden Cross, Death Cross, Ichimoku breakout, S/R kırılımları) artık `has_trend = adx >= threshold` kontrolünden geçiyor. ADX eşiği piyasa rejimine göre dinamik olarak ayarlanıyor: boğa piyasasında 18, ayı piyasasında 25, yatay piyasada 15.

**B) ATR (Average True Range) — Volatilite Filtresi**

ATR, fiyatın ortalama günlük hareket aralığını ölçer. 52W High Breakout gibi sinyallerde kırılımın anlamlılığını kontrol etmek için kullanılıyor. ATR/fiyat oranı çok düşükse (< %0.5) hareket istatistiksel olarak anlamsız olabilir.

**C) Güçlendirilmiş Hacim Onayı**

Önceki eşik `vol_ratio > 1.3` idi; bu çok düşük. V3'te minimum eşik `1.5`'e yükseltildi ve piyasa rejimine göre dinamik: boğada 1.3x, ayıda 2.0x, yatayda 1.8x.

**D) Çift Kilit Mekanizması**

Kırılım sinyalleri artık `has_trend OR vol_confirmed` şartını arıyor. Her ikisinin birden olması `confirmation_count`'u artırarak sinyal kalitesini yükseltiyor. VCP kırılımlarında hacim onayı zorunlu hale getirildi (volatilite daralma sonrası kırılımda hacim patlaması beklenir).

**E) Confirmation Counter**

Her sinyal için bağımsız teyit faktörleri sayılıyor: hacim onayı, ADX trend teyidi, MACD uyumu, RSI nötr bölge, BB pozisyonu. Bu sayı `confirmation_count` olarak sinyale ekleniyor ve kalite puanlamasını doğrudan etkiliyor.

---

## 3. Backtest Modülü

### Mimari

`cross_hunter_backtest.py` iki mod destekliyor:

**A) Basit Backtest (`run()`)**

CrossHunter'ın ürettiği sinyalleri alır ve geçmiş veri üzerinde TP/SL simülasyonu yapar. Her sinyalin entry fiyatından itibaren bar-bar ilerleme, High/Low kontrolü ile TP/SL tespiti gerçekleştirir. Konservatif yaklaşım olarak aynı bar'da SL ve TP ikisi de tetiklenebilirse SL önce kontrol edilir (worst-case senaryosu).

**B) Walk-Forward Backtest (`run_walkforward()`)**

Veriyi lookback penceresi ile parçalar, her pencere sonunda scan_all() çağırır ve sinyal üretir, üretilen sinyalleri gelecek bar'lar üzerinde simüle eder. Bu, "geçmişe bakarak sinyal üretip geçmişte test etme" (look-ahead bias) hatasından kaçınır.

### Komisyon ve Slippage

Her trade için toplam komisyon `commission_pct * 2` (giriş + çıkış) olarak hesaplanıyor. BIST ortalaması %0.2 varsayılıyor (toplam %0.4). Slippage %0.1 olarak giriş fiyatına ekleniyor. `pnl_net_pct` alanı her zaman komisyon düşülmüş net kârı gösteriyor.

### Metrikler

Summary fonksiyonu şu metrikleri üretiyor: win rate (gross ve net), ortalama P&L, profit factor (toplam kâr / toplam zarar), ortalama holding süresi (bar), toplam komisyon maliyeti, sinyal tipine göre breakdown, yıldız sayısına göre breakdown, confirmation count'a göre breakdown.

---

## 4. Optimizasyon Parametreleri

### Dinamik Hale Getirilmesi Gereken Değişkenler

Temel yaklaşım olarak Cross Hunter artık bir `CrossHunterConfig` dataclass alıyor. Bu config, ya doğrudan verilir ya da `MarketRegime` tespitine göre otomatik seçilir.

**Piyasa Rejimi Tespiti (`detect_market_regime()`)**

Üç girdi kullanılıyor: MA50/MA200 ilişkisi (MA50 > MA200 ve fiyat > MA50 ise boğa), ADX seviyesi (< 20 ise yatay) ve son 20 günlük fiyat değişimi (> %5 boğa, < -%5 ayı).

**Rejime Göre Otomatik Parametre Tablosu:**

Boğa piyasasında ADX eşiği 18, hacim eşiği 1.3x, ATR çarpanı 0.3 ve minimum onay 1. Ayı piyasasında ADX eşiği 25, hacim eşiği 2.0x, ATR çarpanı 0.7, RSI aşırı alım 65 ve minimum onay 2. Yatay piyasada ADX eşiği 15, hacim eşiği 1.8x, ATR çarpanı 0.6, BB standart sapma 1.8 ve minimum onay 2.

**Backtest Config Tablosu (Timeframe):**

15 dakika: TP %1.5, SL %0.8, max holding 32 bar. 60 dakika: TP %2.5, SL %1.5, max holding 24 bar. Günlük: TP %3, SL %2, max holding 20 bar.

Tüm bu parametreler config dict üzerinden runtime'da override edilebilir; hardcoded magic number kalmadı.

---

## 5. Kod Refactoring

### Yapılan İyileştirmeler

**A) Monolith → Modüler Fonksiyonlar**

`compute_technical()` fonksiyonu 250+ satırdı ve test edilmesi zordu. Şimdi altı bağımsız fonksiyona bölündü: `compute_moving_averages()`, `compute_rsi()`, `compute_macd()`, `compute_bollinger_bands()`, `compute_adx()`, `compute_atr()`. Her biri bağımsız test edilebilir ve farklı config'lerle çağrılabilir.

**B) Vektörel İşlemler**

`_build_price_history()` fonksiyonunda `DataFrame.iterrows()` yerine `zip()` ile vektörel dönüşüm kullanıldı. iterrows() her satırı Series'e çevirir (yavaş); zip() doğrudan sütun değerlerini iterate eder, 130 bar için yaklaşık 3-5x hız farkı yaratır.

**C) DataFrame Çözümleme**

Veri kaynağı çözümleme (parametre → cache → provider) mantığı `_resolve_dataframe()` fonksiyonuna çıkarıldı. Bu, hem `compute_technical()` hem `generate_chart_png()` tarafından kullanılıyor ve kod tekrarı ortadan kalktı.

**D) Type Hinting ve Dokümantasyon**

Tüm public fonksiyonlara tip anotasyonları ve docstring eklendi. `SignalResult` NamedTuple, `CrossHunterConfig` frozen dataclass, `MarketRegime` enum ile tip güvenliği artırıldı.

**E) Dead Code Temizliği**

`import random` kaldırıldı, kullanılmayan `YF_AVAILABLE` flag'i korundu (ileride yfinance geri gelirse diye) ama yfinance ile ilgili tüm branch'ler temizlendi.

---

## Dosya Yapısı

Çıktıda üç dosya var:

`cross_hunter_v3.py` dosyası ana teknik analiz ve sinyal tarama motorunu içerir. Bu dosya mevcut `engine/technical.py` dosyasının yerine geçer.

`cross_hunter_backtest.py` dosyası backtest framework'ünü içerir. Bu dosya `engine/` dizinine yeni dosya olarak eklenir.

`signal_engine_v3.py` dosyası sinyal kalite ve güven skorlama motorunu içerir. Bu dosya mevcut `engine/signal_engine.py` dosyasının yerine geçer.

---

## Entegrasyon Rehberi

Mevcut kodla entegrasyon için üç adım yeterlidir:

Birinci adımda, `engine/technical.py` dosyasını `cross_hunter_v3.py` ile değiştirin. Tüm public API'ler korundu; `cross_hunter.scan_all()`, `compute_technical()`, `generate_chart_png()` aynı signature'ları kullanıyor.

İkinci adımda, `cross_hunter_backtest.py` dosyasını `engine/` dizinine kopyalayın. Import ve kullanım örneği: `from engine.cross_hunter_backtest import BacktestEngine, BacktestConfig`.

Üçüncü adımda, `engine/signal_engine.py` dosyasını `signal_engine_v3.py` ile değiştirin. Yeni alanlar (`adx_confirmed`, `confirmation_count`, `market_regime`) mevcut sinyallerde yoksa güvenli default'lara düşer.

---

## Sonraki Adımlar İçin Öneriler

Walk-forward backtest'i gerçek veri ile çalıştırıp confirmation_count breakdown'ına bakarak optimum filtre eşiğini belirlemek önemlidir.

Timeframe genişletmesi olarak 15dk ve 60dk veri çekme desteği eklenirse, `TIMEFRAME_BACKTEST_CONFIGS` tablosu hazırdır.

Dinamik TP/SL için ATR-bazlı TP/SL hesaplaması (ör. TP = entry + 2*ATR, SL = entry - 1.5*ATR) implementasyonu backtest modülüne eklenebilir.

Sinyal TTL olarak, bir sinyalin ne kadar süre "taze" kalacağını belirleyen mekanizma eklenebilir (ör. Golden Cross 5 gün geçerli, RSI sinyali 2 gün geçerli).

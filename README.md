# 🐂 BistBull Terminal

**Borsa İstanbul için kurumsal seviye analiz terminali.**

108 BIST hissesini 7 temel + 3 momentum boyutunda analiz eder, her hisseye tek bir skor verir ve kararını açıklar. Veri kalitesini açıkça gösterir. AI opsiyoneldir — skorlama tamamen deterministiktir.

[![Live](https://img.shields.io/badge/Live-bistbull.ai-D4A844?style=flat-square)](https://bistbull.ai)
[![Tests](https://img.shields.io/badge/Tests-350%20passing-34D399?style=flat-square)]()

---

## BistBull Ne Yapar?

Bir hisse hakkında karar vermeden önce bilmeniz gereken her şeyi tek bir yerde toplar:

- **Ucuz mu, pahalı mı?** → Değerleme skoru (F/K, PD/DD, FD/FAVÖK, Graham güvenlik marjı)
- **Şirket kaliteli mi?** → Kalite skoru (ROE, ROIC, net marj — sektöre göre kalibre edilmiş)
- **Büyüyor mu?** → Büyüme skoru (gelir, HBK, FAVÖK büyümesi, PEG)
- **Bilançosu sağlam mı?** → Bilanço skoru (Altman Z, borç/özsermaye, cari oran, faiz karşılama)
- **Kârı gerçek mi?** → Kâr kalitesi skoru (nakit akışı/kâr oranı, Beneish M-Score, FCF marjı)
- **Rekabet avantajı var mı?** → Hendek skoru (marj stabilitesi, fiyatlama gücü)
- **Sermayeyi doğru kullanıyor mu?** → Sermaye skoru (temettü, FCF getirisi, seyreltme kontrolü)
- **Şu an giriş zamanı mı?** → Momentum skoru (RSI, hacim, MA pozisyonu, 14 teknik sinyal)

Her boyut 0–100 arası skorlanır. Boyutlar ağırlıklı olarak birleştirilir. Sonuç: **tek bir karar** — AL, İZLE, BEKLE veya KAÇIN.

---

## Neden Önemli?

BIST'te perakende yatırımcıların çoğu tüyo, sosyal medya veya teknik analiz ile karar veriyor. BistBull farklı bir soru soruyor:

> "Bu şirket gerçekten iyi mi, yoksa sadece fiyatı mı hareket ediyor?"

Sistem, iyi bir şirketi ucuzken yakalar (AL), pahalıyken uyarır (BEKLE), ve temeli zayıf olan hisseleri filtreler (KAÇIN) — fiyat ne yapıyor olursa olsun.

---

## Skorlama Nasıl Çalışır?

### Temel Analiz Skoru (FA) — "Şirket iyi mi?"

7 boyutun ağırlıklı ortalaması:

| Boyut | Ağırlık | Ne Ölçer |
|-------|:---:|---------|
| Kalite | %30 | ROE, ROIC, net marj (sektöre göre kalibre) |
| Değerleme | %18 | F/K, PD/DD, FD/FAVÖK, FCF getirisi, Graham güvenlik marjı |
| Büyüme | %15 | Gelir, HBK, FAVÖK büyümesi, PEG |
| Bilanço | %10 | Altman Z, borç/FAVÖK, cari oran, faiz karşılama |
| Kâr Kalitesi | %10 | Nakit akışı/kâr, Beneish M, FCF marjı |
| Sermaye | %9 | Temettü, FCF getirisi, seyreltme kontrolü |
| Hendek | %8 | Marj stabilitesi, ROA tutarlılığı |

### Momentum Skoru (İvme) — "Ne zaman girmeliyim?"

| Boyut | Ağırlık | Ne Ölçer |
|-------|:---:|---------|
| Momentum | %40 | RSI, MA50/200 pozisyonu, hacim oranı |
| Teknik Kırılım | %35 | Golden Cross, Ichimoku, VCP, destek/direnç |
| Kurumsal Akış | %25 | Yabancı oranı, hacim-fiyat korelasyonu |

### Genel Skor Formülü

```
Genel = FA × 0.55 + Momentum(gated) × 0.35 + Değerleme Streç + Risk × 0.3
```

**Momentum gating**: Temeli zayıf hisselerin momentum bonusu sınırlandırılır. FA skoru 40 olan bir hissenin momentumu ne kadar güçlü olursa olsun, genel skoru çok yükselmez.

**Risk penaltileri**: Net zarar, negatif nakit akışı, aşırı borç, muhasebe manipülasyonu riski, hisse seyreltme — her biri ayrı ayrı puanla cezalandırılır.

---

## Açıklanabilirlik

Her hisse için sistem kararını açıklar:

- **Özet**: Tek cümlelik sonuç ("Ucuz değerleme ve güçlü kârlılık sayesinde öne çıkıyor, ancak büyüme zayıf.")
- **Güçlü yönler**: En etkili pozitif faktörler (katkı büyüklüğüne göre sıralı)
- **Zayıf yönler**: En etkili negatif faktörler
- **Veri güvenilirliği**: Kaç boyut gerçek veriye, kaçı tahminine dayanıyor

Açıklamalar tamamen deterministiktir. AI kullanmaz. Aynı veriyle her zaman aynı açıklamayı üretir.

---

## Veri Kalitesi ve Güven

Sistem her hisse için veri kalitesini açıkça belirtir:

| Tier | Anlamı | Gösterge |
|------|--------|:---:|
| **full** | Tüm finansal tablolar mevcut, güven ≥%70 | — |
| **partial** | Bazı tablolar eksik, güven %40–70 | 🟡 |
| **market_only** | Sadece piyasa verisi, güven <%40 | 🔴 |

**Neden önemli**: Banka hisseleri (GARAN, ISCTR, HALKB) farklı mali tablo yapılarına sahiptir — birçok metrik doğal olarak eksiktir. Sistem bunları 🔴 ile işaretler ve güven skorunu düşürür. Eksik veri arkasına yüksek skor saklamaz.

**Imputation kuralı**: Eksik boyutlar 50'ye (nötr) sabitlenir. Bu ne ödüllendirir ne cezalandırır — sadece "veri yok, bilmiyoruz" der.

---

## Sinyaller — Cross Hunter

108 BIST hissesini 14 teknik sinyal için tarar:

**Kırılım sinyalleri** (orta-uzun vade): Golden Cross, Death Cross, Ichimoku Kumo Breakout, VCP Kırılım, Rectangle Breakout, 52W High Breakout, Destek/Direnç kırılımları.

**Momentum sinyalleri** (kısa vade): MACD Cross, RSI Aşırı Alım/Satım, Bollinger Band kırılımları.

Her sinyal kalite derecesi alır:

| Derece | Anlamı |
|:---:|---------|
| **A** | Güçlü momentum + çoklu teyit + düşük risk + FA desteği |
| **B** | Orta momentum veya tek teyit |
| **C** | Zayıf momentum veya yüksek risk — gürültü olabilir |

Sinyal kalitesi tamamen deterministiktir — FA skoru, momentum, hacim teyidi ve risk flaglerinden hesaplanır.

---

## Watchlist ve Uyarılar

- **Watchlist**: Hisse detayında ⭐ ile ekle, ana sayfada takip et
- **Uyarılar**: Watchlist'teki hisseler için otomatik kontrol:
  - Yeni sinyal oluştu
  - Sinyal kalitesi yükseldi (C→B veya B→A)
  - Genel skor ≥5 puan değişti
  - Güven skoru ≥10 puan düştü
  - Yeni risk faktörü veya güçlü yön tespit edildi

Uyarılar günlük deduplike edilir — aynı uyarı aynı gün tekrar etmez.

---

## AI Katmanı (Opsiyonel)

AI, skorlama veya karar mekanizmasının parçası **değildir**. Tüm skorlar, açıklamalar ve sinyaller deterministiktir.

AI şunları sağlar (API key varsa):
- Hisse başına yatırım tezi
- Günlük piyasa brifing
- Doğal dille soru-cevap (Q Asistan)
- Sinyal ve makro yorumu

AI sağlayıcıları: Grok (xAI) → OpenAI → Anthropic. İlk çalışan kullanılır. Hiçbiri yoksa sistem AI olmadan tam çalışır.

---

## Kurulum

### Gereksinimler
- Python 3.10+
- borsapy veya yfinance (en az biri)
- AI key (opsiyonel): `XAI_API_KEY`, `OPENAI_KEY`, `ANTHROPIC_API_KEY`

### Yerel Çalıştırma

```bash
git clone https://github.com/BerkayMangal/bist-terminal.git
cd bist-terminal
pip install -r requirements.txt
export XAI_API_KEY=your_key  # opsiyonel
uvicorn app:app --host 0.0.0.0 --port 8000
```

### Railway Deploy

Procfile hazır. Railway'de environment variables olarak API key'leri ekleyin.

Watchlist/uyarılar için Railway'de `/data` yoluna persistent volume mount edin.

---

## API

| Endpoint | Açıklama |
|----------|---------|
| `GET /api/analyze/{ticker}` | Tam 10 boyutlu analiz |
| `GET /api/top10` | Tarama sonuçları (sıralı) |
| `GET /api/cross` | Cross Hunter sinyalleri (kalite dereceli) |
| `GET /api/macro` | Makro göstergeler (25 sembol) |
| `GET /api/heatmap` | Sektör ısı haritası |
| `GET /api/watchlist` | Takip listesi |
| `GET /api/alerts` | Uyarılar |
| `POST /api/alerts/refresh` | Uyarı kontrolü tetikle |
| `GET /api/health` | Sistem sağlık kontrolü |

---

## Mimari

```
app.py                   → FastAPI router + background scanner
engine/scoring.py        → 10 boyutlu skorlama motoru
engine/explainability.py → Deterministik açıklama motoru
engine/signal_engine.py  → Sinyal kalite katmanı (A/B/C)
engine/technical.py      → Teknik göstergeler + Cross Hunter
engine/alerts.py         → Uyarı motoru
data/providers.py        → borsapy + yfinance veri katmanı
ai/                      → AI sağlayıcı zinciri (opsiyonel)
```

**350 test** — skorlama, açıklanabilirlik, sinyal kalitesi, watchlist, uyarılar, golden correctness doğrulaması.

---

<p align="center">
  <strong>🐂 BistBull Terminal</strong><br>
  <em>Veriyle karar ver, tüyoyla değil.</em><br>
  <a href="https://bistbull.ai">bistbull.ai</a>
</p>

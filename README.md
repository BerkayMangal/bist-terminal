# 🐂 BistBull Terminal

**BIST Intelligence Terminal — Yapay Zeka Destekli Yatırım Asistanı**

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app)

---

## 🎯 Nedir?

BistBull, Borsa İstanbul (BIST) yatırımcıları için geliştirilmiş yapay zeka destekli yatırım terminalidir. 40 hisselik BIST universe'ini gerçek zamanlı analiz eder, 7 boyutlu temel analiz + 6 teknik gösterge ile skorlar ve AI destekli yatırım tezleri üretir.

**Bu bir oyuncak dashboard değil. Gerçekten kullanılan, ciddi ama etkileyici bir yatırım terminali.**

---

## ✨ Özellikler

### 📊 Temel Analiz Motoru
- **7 Boyutlu Skorlama:** Value, Quality, Growth, Balance, Earnings, Moat, Capital
- **Legendary Metrikler:** Piotroski F-Score (9 test), Altman Z-Score, Beneish M-Score
- **Graham Değerleme:** Fair Value, Margin of Safety, Graham/Buffett filtreleri
- **BIST-Kalibreli Eşikler:** Emerging market ortamına özel ayarlanmış parametreler

### 📈 Teknik Analiz
- RSI, MACD, MA50/200, Bollinger Bantları
- Golden/Death Cross otomatik tespiti
- 52 Hafta Yüksek/Düşük, Hacim analizi
- Cross Hunter: 40 hissede teknik sinyal taraması

### 🤖 AI Destekli İçgörüler
- **Grok / OpenAI / Anthropic** — üçünü de destekler, fallback mekanizması
- Hisse bazlı AI yatırım tezi (2-3 cümle, Türkçe)
- Günlük piyasa brifing'i
- Makro AI yorumu (EM karşılaştırması dahil)
- **BORSADEDE** — AI yatırım asistanı chatbot

### 🌍 Makro Radar
- 20+ global gösterge: BIST 30/100, USD/TRY, EUR/TRY, Brent, Altın, VIX, DXY
- **Emerging Markets YTD Sıralaması:** Brezilya, Hindistan, G.Kore, Tayvan, Çin, Polonya, Meksika, Endonezya, G.Afrika + Türkiye
- Günlük, haftalık, aylık ve YTD performans karşılaştırması

### 📡 Dashboard Intelligence
- **"Bugün Ne Oluyor?"** hero katmanı: Piyasa Modu, Hikaye, Fırsat, Risk, Bot Yorumu
- Günün 3 Hissesi / 3 Fırsat / 3 Risk
- Sektör güç/zayıflık analizi
- Watchlist (localStorage)
- Son bakılan hisseler
- Canlı sistem aktivite sayaçları
- Günlük finans özlü sözleri

### ⚡ Cross Hunter
- 40 hissede otomatik teknik sinyal taraması
- Golden Cross, Death Cross, MACD Crossover
- RSI aşırı alım/satım, Bollinger kırılım
- Bullish/Bearish sınıflandırma

### 📊 Takas Analizi
- Yabancı yatırımcı oranları
- İş Yatırım API + yfinance fallback

---

## 🏗️ Teknik Mimari

```
FastAPI (Python) ──→ yfinance (gerçek veri)
       │              ├── Temel analiz
       │              ├── Teknik analiz
       │              └── Makro veriler
       │
       ├──→ Grok / OpenAI / Anthropic (AI)
       │              ├── Yatırım tezleri
       │              ├── Piyasa brifing
       │              ├── Makro yorum
       │              └── BORSADEDE chatbot
       │
       └──→ Single-page HTML frontend
                       ├── Dark premium terminal UI
                       ├── 6 sayfa (Home, Radar, Cross, Makro, Takas, Sosyal)
                       └── BORSADEDE floating chat
```

**22 API Endpoint** — tamamı RESTful:

| Endpoint | Açıklama |
|----------|----------|
| `/api/analyze/{ticker}` | Full temel analiz |
| `/api/technical/{ticker}` | Teknik analiz |
| `/api/chart/{ticker}` | Matplotlib PNG chart |
| `/api/ai-summary/{ticker}` | AI yatırım tezi |
| `/api/scan` | 40 hisse universe taraması |
| `/api/hero-summary` | Dashboard hero verisi |
| `/api/macro` | 20+ makro gösterge |
| `/api/macro/commentary` | AI makro yorum |
| `/api/cross` | Cross Hunter sinyalleri |
| `/api/dashboard` | Fırsat/risk/sektör özeti |
| `/api/briefing` | AI piyasa brifing |
| `/api/agent?q=...` | BORSADEDE chatbot |
| `/api/quote` | Günlük finans sözü |
| `/api/takas` | Yabancı oranları |
| `/api/social` | Sosyal medya sentiment |
| `/api/live/stats` | Sistem aktivitesi |
| `/api/briefings/history` | Brifing geçmişi |
| `/api/batch/{tickers}` | Toplu analiz |
| `/api/top10` | Cached scan sonuçları |
| `/api/universe` | Hisse universe listesi |
| `/api/health` | Sistem durumu |

---

## 🚀 Kurulum (Railway)

### 1. Repo'yu GitHub'a yükle

```
bist-terminal/
├── app.py           # Backend (FastAPI)
├── index.html       # Frontend (Single-page)
├── requirements.txt
├── Dockerfile
├── Procfile
└── README.md
```

### 2. Railway'de deploy et

Railway → New Project → Deploy from GitHub repo

### 3. Environment Variables

| Variable | Zorunlu | Açıklama |
|----------|---------|----------|
| `XAI_API_KEY` | Önerilen | Grok API key (ana AI motoru) |
| `OPENAI_KEY` | Alternatif | OpenAI API key (fallback) |
| `ANTHROPIC_API_KEY` | Alternatif | Anthropic key (fallback) |

**En az bir AI key gerekli.** Grok önerilir (ucuz + X/Twitter verisi).

### 4. Aç ve kullan

```
https://your-project.up.railway.app
```

---

## 📐 Skorlama Sistemi

### Ağırlıklar (V6 — EM-Adjusted)

| Boyut | Ağırlık | İçerik |
|-------|---------|--------|
| Value | %20 | P/E, P/B, EV/EBITDA, FCF Yield, Graham MoS |
| Quality | %22 | ROE, ROIC, Gross/Op/Net Margin |
| Growth | %15 | Revenue, EPS, EBITDA büyüme, PEG |
| Balance | %20 | Net Borç/EBITDA, D/E, Cari Oran, Altman Z |
| Earnings | %10 | CFO/NI, FCF Margin, Beneish M |
| Moat | %8 | Margin stabilite, pricing power |
| Capital | %5 | Temettü, FCF Yield, dilüsyon |

### Ceza/Bonus
- Negatif özsermaye: -12 puan
- Net zarar: -8 puan
- Negatif operasyonel nakit akış: -8 puan
- Faiz karşılama < 1.5: -5 puan
- Beneish manipülasyon riski: -5 puan
- Net nakit pozisyonu: +3 puan

---

## 🐂 BORSADEDE

Sağ alttaki 🐂 butonuna tıklayarak AI yatırım asistanına erişin.

Sorulabilecek örnek sorular:
- "BIST'te bugün ne oluyor?"
- "THYAO hakkında ne düşünüyorsun?"
- "Bankacılık sektörü nasıl?"
- "P/E oranı nedir?"
- "Piotroski F-Score ne işe yarar?"

---

## 📊 Universe (40 Hisse)

ASELS, THYAO, BIMAS, KCHOL, SISE, EREGL, TUPRS, AKBNK, ISCTR, YKBNK, GARAN, SAHOL, MGROS, FROTO, TOASO, TCELL, KRDMD, PETKM, ENKAI, TAVHL, PGSUS, EKGYO, INDES, TTKOM, ARCLK, VESTL, DOHOL, AYGAZ, LOGO, SOKM, TKFEN, KONTR, ODAS, GUBRF, SASA, ISMEN, OYAKC, CIMSA, MPARK, AKSEN

---

## ⚠️ Yasal Uyarı

Bu uygulama yatırım tavsiyesi niteliğinde değildir. Burada yer alan bilgiler, analizler ve yorumlar bilgilendirme amaçlıdır. Yatırım kararlarınızı kendi araştırmanıza ve profesyonel danışmanlığa dayandırınız.

Veriler yfinance üzerinden Yahoo Finance'den alınmaktadır. Verilerin doğruluğu ve güncelliği garanti edilmez.

---

## 📝 Lisans

MIT License

---

**Built with 🐂 by BistBull Team**

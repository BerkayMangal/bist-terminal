# 🐂 BistBull Terminal

**BIST Intelligence Terminal — Yapay Zeka Destekli Yatırım Asistanı**

---

## 🎯 Nedir?

BistBull, Borsa İstanbul (BIST) yatırımcıları için geliştirilmiş yapay zeka destekli yatırım terminalidir. 40 hisselik BIST universe'ini gerçek zamanlı analiz eder, 7 boyutlu temel analiz + 6 teknik gösterge ile skorlar ve AI destekli yatırım tezleri üretir.

---

## ☕ EYYAY DEDE — Anadolu Kurnazı Yatırım Asistanı

BistBull'un kalbi **Eyyay Dede** (H. Sayılgan). 59 yaşında, tombul, beyaz sakallı, hırkalı, Anadolu kurnazı bir yatırım dedesi.

> *"Eyyay, hoş geldin evladım! Bi çayını koy, dede anlatsın."*

- BIST hakkında her soruya kısa, net, babacan cevap
- Temel analiz, teknik sinyal, makro — hepsini bilir
- Asla direkt al-sat tavsiyesi vermez
- Her cevabı "bu dedenin görüşü, sen de araştır evladım" ile biter

**⚠️ EYYAY DEDE eğlence ve bilgi amaçlıdır. Yatırım tavsiyesi değildir.**

---

## ✨ Özellikler

### 📊 Temel Analiz Motoru
- **7 Boyutlu Skorlama:** Value, Quality, Growth, Balance, Earnings, Moat, Capital
- **Legendary Metrikler:** Piotroski F-Score, Altman Z-Score, Beneish M-Score
- **Graham Değerleme:** Fair Value, Margin of Safety, Graham/Buffett filtreleri

### 📈 Teknik Analiz
- RSI, MACD, MA50/200, Bollinger Bantları
- Golden/Death Cross, MACD Crossover otomatik tespiti
- Cross Hunter: 40 hissede sinyal taraması

### 🤖 AI (Grok / OpenAI / Anthropic)
- Hisse bazlı AI yatırım tezi
- Günlük piyasa brifing
- Makro AI yorumu
- EYYAY DEDE chatbot

### 📡 Dashboard
- Piyasa Modu, Hikaye, Fırsat, Risk, Bot Yorumu
- Günün 3 Hissesi / 3 Fırsat / 3 Risk
- Günlük finans özlü sözü + kitap önerisi
- Sektör analizi, Watchlist, Sinyaller

### 🌍 Makro Radar
- 22 global gösterge
- EM YTD sıralaması
- AI makro yorumu

---

## 🏗️ Mimari

```
FastAPI ──→ yfinance (veri) + Grok/OpenAI/Anthropic (AI)
   └──→ Single-page HTML (dark premium terminal UI)
         └──→ EYYAY DEDE floating chat
```

**23 API Endpoint** · **1800+ satır backend** · **6 sayfa frontend**

---

## 🚀 Kurulum

```
bist-terminal/
├── app.py          # Backend
├── index.html      # Frontend
├── requirements.txt
├── Dockerfile
├── Procfile
└── README.md
```

Railway Variables: `XAI_API_KEY` (Grok, önerilen) veya `OPENAI_KEY` veya `ANTHROPIC_API_KEY`

---

## 📊 Universe (40 Hisse)

ASELS, THYAO, BIMAS, KCHOL, SISE, EREGL, TUPRS, AKBNK, ISCTR, YKBNK, GARAN, SAHOL, MGROS, FROTO, TOASO, TCELL, KRDMD, PETKM, ENKAI, TAVHL, PGSUS, EKGYO, INDES, TTKOM, ARCLK, VESTL, DOHOL, AYGAZ, LOGO, SOKM, TKFEN, KONTR, ODAS, GUBRF, SASA, ISMEN, OYAKC, CIMSA, MPARK, AKSEN

---

## ⚠️ Yasal Uyarı

Bu uygulama yatırım tavsiyesi değildir. EYYAY DEDE dahil tüm içerikler eğlence ve bilgi amaçlıdır. Yatırım kararlarınızı kendi araştırmanıza dayandırınız.

---

**Tasarım & Geliştirme: Berkay Kangal**
**© 2026 BistBull Terminal. Tüm hakları saklıdır.**

*Eyyay, bi çayını koy, dede anlatsın!* ☕🐂

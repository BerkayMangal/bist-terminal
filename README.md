<div align="center">

# 🐂 BistBull Terminal

**AI-Powered BIST Investment Analysis Terminal**

[![Live](https://img.shields.io/badge/LIVE-bistbull.ai-FFB300?style=for-the-badge)](https://bistbull.ai)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104-009688?style=flat-square&logo=fastapi)]()

*108 BIST hissesini 10 boyutta analiz eden, yapay zeka destekli yatırım terminali.*

</div>

---

## Ne Yapıyor?

| Soru | Nasıl? | Sonuç |
|------|--------|-------|
| 🏛️ **Şirket sağlam mı?** | 7 boyutlu temel analiz | **Değer Skoru** |
| ⚡ **Zamanlama uygun mu?** | 3 boyutlu teknik analiz | **İvme Skoru** |
| 🎯 **Ne yapmalı?** | İkisini birleştir | **AL / İZLE / BEKLE / KAÇIN** |

## Özellikler

🏛️ 7 boyut temel analiz (Piotroski, Altman, Beneish, Graham, Buffett) · ⚡ Cross Hunter sinyalleri · 🤖 Q Asistanı (Grok/GPT) · 🌍 Makro Radar · 📒 Sanal Portföy · 🔔 Watchlist & Alerts · 🗺️ Sektör Heatmap · 📈 Teknik Grafikler

## Mimari

```
borsapy (KAP) ──▸ FastAPI + Engine ──▸ Terminal UI
yfinance (yedek) ──▸ L1 Cache + Redis ──▸ WebSocket
                       AI (Grok/GPT) ──▸ Q Asistanı
```

## Tech Stack

FastAPI · Python 3.11 · borsapy + yfinance · Redis (opsiyonel) · SQLite · Grok/GPT · Railway · Docker

## Kurulum

```bash
git clone https://github.com/BerkayMangal/bist-terminal.git
cd bist-terminal
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8080
```

## Güvenlik

🔒 Session auth · 🛡️ Security headers · ✅ Metric guards · 🚦 Rate limiting · 🔌 Circuit breakers · 🐳 Non-root Docker

---

> ⚠️ BistBull yatırım tavsiyesi değildir. Veriler gecikmeli olabilir. Tüm risk kullanıcıya aittir.

<div align="center"><b>Tasarım & Geliştirme:</b> Berkay Kangal · © 2026</div>

# 🐂 BistBull Terminal V9.1

**AI-Powered BIST Stock Analysis Terminal**

> 10-dimensional hybrid scoring engine · Piotroski · Altman Z · Graham · Beneish · 14 technical signals · 3 AI engines · 100 BIST stocks

[![Live](https://img.shields.io/badge/Live-bistbull.ai-D4A844?style=flat-square)](https://bistbull.ai)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688?style=flat-square)](https://fastapi.tiangolo.com)
[![Deploy](https://img.shields.io/badge/Deploy-Railway-0B0D0E?style=flat-square)](https://railway.app)

---

## What is BistBull?

BistBull is a **Bloomberg-grade BIST (Borsa Istanbul) analysis terminal** built for the modern investor. It combines deep fundamental analysis with technical momentum scoring and AI-powered investment thesis generation — all in a single, free web interface.

Every stock is analyzed across **10 dimensions**, scored by **9 financial models**, cross-referenced by **14 technical signals**, and interpreted by **3 AI engines** (Grok, GPT-4o, Claude).

**Live:** [bistbull.ai](https://bistbull.ai) · **Terminal:** [bistbull.ai/terminal](https://bistbull.ai/terminal)

---

## Core Features

### 🏛️ DEĞER Score — "Is this company good?"
7-dimensional fundamental analysis engine:

| Dimension | What It Measures | Key Metrics |
|-----------|-----------------|-------------|
| **Değerleme** | Cheap or expensive? | P/E, P/B, EV/EBITDA, EV/Sales, FCF Yield, Graham MoS |
| **Kalite** | Is the business strong? | ROE, ROIC, Net Margin (sector-calibrated) |
| **Büyüme** | Is it growing? | Revenue, EPS, EBITDA growth, PEG |
| **Bilanço** | Is the balance sheet safe? | Net Debt/EBITDA, Current Ratio, Interest Coverage, Altman Z |
| **Kârlılık** | Is cash backing profits? | CFO/NI, FCF Margin, Beneish M-Score |
| **Hendek** | Does it have a moat? | Gross margin stability, pricing power, ROA consistency |
| **Sermaye** | Is capital allocated well? | Dividend yield, FCF yield, ROIC quality, dilution |

### ⚡ İVME Score — "When should I enter?"
3-dimensional technical momentum analysis:

| Dimension | What It Measures | Key Signals |
|-----------|-----------------|-------------|
| **Momentum** | Price + volume trend | RSI, MA50/200 position, volume ratio, 20-day change |
| **Teknik Kırılım** | Breakout signals | 52W High, VCP, Ichimoku Kumo, Rectangle, S/R levels |
| **Kurum Akışı** | Institutional flow proxy | Foreign ownership %, volume-price correlation |

### 🎯 Cross Hunter V2 — 14 Technical Signals
Real-time signal scanner across the full BIST universe:

- **5-star signals:** Golden Cross, Death Cross, Ichimoku Kumo Breakout/Breakdown, VCP Kırılım, 52W High Breakout
- **4-star signals:** Ichimoku TK Cross, Rectangle Breakout/Breakdown, Support/Resistance breaks
- **3-star signals:** MACD Bullish/Bearish Cross
- **1-2 star signals:** RSI Overbought/Oversold, Bollinger Band breaks

Each signal includes star reliability rating and volume confirmation.

### 🤖 AI Engine — Triple Fallback
- **Grok** (primary) — X/Twitter data access for social sentiment
- **GPT-4o-mini** (fallback) — Investment thesis generation
- **Claude** (final fallback) — Rich context analysis

AI features: Q terminal assistant, structured investment thesis (ENTRY / THESIS / RISK / TIMING / TURKEY context), market briefings, cross signal commentary, macro analysis.

### 🛡️ Turkey-Specific Intelligence
- **Hype Detection:** Auto-flags stocks with weak fundamentals but rapid price increases
- **Fake Profit Filter:** Catches companies with reported profits but negative cash flow
- **FA-Gated Momentum:** Limits momentum boost for fundamentally weak stocks
- **Sector Calibration:** 7 sector groups with custom thresholds (banks, defense, energy, retail, transport, holding, industrial)

---

## Architecture

### Modular Design (SOLID Principles)

```
app.py              → FastAPI endpoints + background scanner (slim router)
config.py           → All constants, thresholds, weights (zero magic numbers)
helpers.py          → Pure utility functions (zero side effects)
cache.py            → Thread-safe TTLCache wrapper + global state management
scoring.py          → 10-dimension scoring engine + risk penalties + decision engine
analysis.py         → Metric computation + Piotroski/Altman/Beneish + analyze pipeline
technical.py        → Technical indicators + Ichimoku + CrossHunter + chart generation
ai_engine.py        → AI provider chain + rich context builder + trader summary
market_status.py    → BIST holidays, session hours, half-day detection
data_layer_v9.py    → borsapy (İsyatırım) data layer with SafeCache integration
```

### Key Engineering Decisions

| Problem | Solution | Impact |
|---------|----------|--------|
| TTLCache not thread-safe | `SafeCache` wrapper with `threading.Lock` | Zero race conditions under parallel scan |
| O(N²) ticker lookups in heatmap/hero | Dict-based O(1) lookups via `_items_by_ticker()` | 76× faster for full universe operations |
| Matplotlib memory leak in chart generation | `try/finally` with guaranteed `plt.close(fig)` | Zero memory growth over time |
| Duplicate batch history download | Single download, shared between scanner + CrossHunter | 50% reduction in API calls |
| 3,424-line monolithic app.py | 10 focused modules, single responsibility | Maintainable, testable, extensible |
| Magic numbers scattered everywhere | Centralized `config.py` with typed constants | One place to tune all thresholds |

### Data Flow

```
borsapy (İsyatırım KAP data)  ─┐
                                ├──→  fetch_raw()  ──→  compute_metrics()  ──→  analyze_symbol()
yfinance (Yahoo Finance)       ─┘                                                    │
                                                                                      ▼
                                                                              scoring engine
                                                                           (10 dimensions + risk)
                                                                                      │
                                                                                      ▼
                                                                          ┌─── fa_pure score
                                                                          ├─── ivme_score
                                                                          ├─── risk_penalty
                                                                          ├─── entry_label
                                                                          └─── decision (AL/İZLE/BEKLE/KAÇIN)
```

### Cache Architecture

| Cache | TTL | Purpose |
|-------|-----|---------|
| `raw_cache` | 24h | Raw financial data (borsapy/yfinance) |
| `analysis_cache` | 24h | Full analysis results |
| `tech_cache` | 1h | Technical indicators |
| `history_cache` | 1h | Price history DataFrames |
| `ai_cache` | 2h | AI-generated summaries |
| `macro_cache` | 10m | Global macro data |

All caches are thread-safe via `SafeCache` wrapper.

---

## Quick Start

### Prerequisites
- Python 3.10+
- borsapy (for İsyatırım data) or yfinance (fallback)
- At least one AI key: `XAI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`

### Local Development

```bash
# Clone
git clone https://github.com/BerkayMangal/bist-terminal.git
cd bist-terminal

# Install
pip install -r requirements.txt

# Set AI key (at least one)
export XAI_API_KEY=your_grok_key
export OPENAI_API_KEY=your_openai_key

# Run
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open [localhost:8000](http://localhost:8000) → Landing page
Open [localhost:8000/terminal](http://localhost:8000/terminal) → Terminal

### Railway Deployment

```bash
# Procfile is pre-configured:
# web: uvicorn app:app --host 0.0.0.0 --port $PORT

# Set environment variables in Railway dashboard:
# XAI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY (optional)

git push  # Railway auto-deploys on push
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/universe` | GET | List all tracked BIST stocks |
| `/api/analyze/{ticker}` | GET | Full 10-dimension analysis |
| `/api/technical/{ticker}` | GET | Technical indicators + price history |
| `/api/chart/{ticker}` | GET | Matplotlib PNG chart |
| `/api/ai-summary/{ticker}` | GET | AI-generated investment thesis |
| `/api/top10` | GET | Top stocks by overall score |
| `/api/scan` | GET | Trigger full universe scan |
| `/api/scan-status` | GET | Scan progress (polling) |
| `/api/cross` | GET | Cross Hunter signals (14 types) |
| `/api/macro` | GET | Global macro data (25 symbols) |
| `/api/macro/commentary` | GET | AI macro commentary |
| `/api/heatmap` | GET | Sector heatmap |
| `/api/dashboard` | GET | Aggregated dashboard data |
| `/api/briefing` | GET | AI market briefing |
| `/api/hero-summary` | GET | Hero section data with AI |
| `/api/agent?q=` | GET | Q assistant query |
| `/api/takas` | GET | Foreign ownership data |
| `/api/social` | GET | X/Twitter sentiment (via Grok) |
| `/api/market-status` | GET | BIST open/closed/holiday status |
| `/api/health` | GET | System health check |
| `/api/batch/{tickers}` | GET | Batch analysis (up to 5) |
| `/api/quote` | GET | Daily finance quote |
| `/api/book` | GET | Daily book recommendation |

---

## Scoring Models

### Piotroski F-Score (9 criteria)
Profitability (ROA, CFO, ΔROA, accruals) + Leverage (Δleverage, Δliquidity, dilution) + Efficiency (Δgross margin, Δasset turnover).

### Altman Z-Score (5 factors)
Z = 1.2×(WC/TA) + 1.4×(RE/TA) + 3.3×(EBIT/TA) + 0.6×(MVE/TL) + 1.0×(Sales/TA). Sector-calibrated thresholds.

### Graham Fair Value
FV = √(22.5 × EPS × Book Value). Margin of Safety = (FV - Price) / FV.

### Beneish M-Score (8 variables)
DSRI, GMI, AQI, SGI, DEPI, SGAI, TATA, LVGI. M > -1.78 = manipulation risk.

---

## Tech Stack

- **Backend:** Python 3.12, FastAPI, Uvicorn
- **Data:** borsapy (İsyatırım), yfinance (Yahoo Finance)
- **AI:** OpenAI SDK (Grok + GPT), Anthropic SDK (Claude)
- **Charts:** Matplotlib
- **Cache:** cachetools + custom thread-safe wrapper
- **Frontend:** Vanilla HTML/CSS/JS (zero frameworks, zero build step)
- **Deploy:** Railway (Docker + Procfile)

---

## License

This project is proprietary. All rights reserved.

---

<p align="center">
  <strong>🐂 BistBull Terminal</strong><br>
  <em>Built with 20 years of institutional finance experience.</em><br>
  <a href="https://bistbull.ai">bistbull.ai</a>
</p>

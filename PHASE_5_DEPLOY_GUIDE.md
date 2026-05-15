# Phase 5 Deploy Guide

## Pre-Deploy Checklist

- [ ] Local: `pytest tests/` — yeni 126 test yeşil (baseline 934 + 126 = 1060+)
- [ ] Local: `node -c static/terminal.js` — JS syntax valid
- [ ] Local: `node -c static/js/widgets/*.js` — widget JS valid
- [ ] `MOBILE_QA_CHECKLIST.md` 4 viewport'ta tamamlandı
- [ ] Heatmap cold-start <1s (`time curl /api/heatmap` cold cache)
- [ ] `/api/cross/{symbol}/explain` 200 dönüyor
- [ ] `/api/ai/{symbol}/consensus` 200 dönüyor (AI provider env var'ları set ise)

## Environment Variables — Phase 5

**Yeni env var GEREKMİYOR.** Phase 5 mevcut env var'ları kullanıyor:

| Var | Açıklama | Phase 5 etkisi |
|---|---|---|
| `PERPLEXITY_KEY` | Perplexity API key | AI consensus 4. provider olarak kullanır |
| `GROK_KEY` | Grok API key | AI consensus 1. provider |
| `OPENAI_KEY` | OpenAI API key | AI consensus 2. provider |
| `ANTHROPIC_KEY` | Anthropic API key | AI consensus 3. provider |
| `BISTBULL_DB_PATH` | SQLite path | Değişiklik yok |
| `JWT_SECRET` | Auth secret | Değişiklik yok |
| `REDIS_URL` | Redis L2 cache | Değişiklik yok |

**Önerilen:** AI consensus için en az 2 provider key'i set olsun. 1 provider varsa fallback/single-model olarak çalışır, consensus mantığı bozulmaz.

## Deploy Adımları (Railway / Heroku)

### 1. Branch merge

```bash
git checkout main
git merge feat/phase-5-redesign --no-ff -m "Phase 5: UI/UX redesign"
git push origin main
```

VEYA staged rollout için ayrı branch deploy:

```bash
# Railway: feat/phase-5-redesign branch'ini staging environment'e deploy
# Production'a manuel promote
```

### 2. Smoke test (production veya staging)

```bash
BASE=https://bistbull.ai

# Health
curl -s $BASE/api/health | jq .version
# Beklenen: "V10.0" (değişiklik yok)

# Phase 5.1.1 — heatmap
time curl -s $BASE/api/heatmap | jq '.computing, .sectors | length'
# Beklenen: <1s, computing field present

# Phase 5.2.2 — signal explain (yeni endpoint)
curl -s $BASE/api/cross/THYAO/explain | jq '.symbol, .count, .signals | length'
# Beklenen: 200, "THYAO.IS", count 0+

# Phase 5.2.3 — AI consensus (yeni endpoint)
curl -s $BASE/api/ai/THYAO/consensus | jq '.consensus.leader, .consensus.sentiment'
# Beklenen: 200, leader provider name (or null on no AI keys), sentiment string

# Landing — yeni positioning
curl -s $BASE/ | grep -o "Türkiye gerçeklerini" | head -1
# Beklenen: "Türkiye gerçeklerini"
```

### 3. Cache warm-up

```bash
curl -s $BASE/api/scan -X POST > /dev/null
# 1-2 dakika bekle, sonra:
curl -s $BASE/api/heatmap | jq '.computing'
# Beklenen: false (cached)
```

## Rollback Prosedürü

Phase 5 **fully additive** — hiçbir mevcut endpoint'in response shape'i değişmedi (Rule 6). Rollback için 2 yöntem:

### A. Git revert (full rollback)

```bash
# Phase 5'in 5 commit'ini revert et
git log --oneline | grep "Phase 5\|phase-5" | awk '{print $1}' | xargs -I{} git revert --no-edit {}
git push origin main
```

### B. Frontend-only rollback (hızlı)

Backend zaten geriye uyumlu — sorun frontend'deyse:

```bash
# Sadece frontend dosyalarını eski versiyona döndür
git checkout 321a322 -- static/terminal.js static/terminal.css landing.html
git commit -m "rollback: frontend to Phase 4.7 v3 baseline"
git push origin main
```

Yeni backend endpoint'leri (`/api/cross/{symbol}/explain`, `/api/ai/{symbol}/consensus`) açık kalmaya devam eder — başka frontend tüketicisi yoksa zararsız.

### C. Tek bir feature'ı disable et

Yeni endpoint'leri 503 dönecek şekilde gate'lemek için `app.py`'a env var koşulu eklenebilir:

```python
# app.py — minimal disable patch
PHASE_5_AI_CONSENSUS = os.getenv("PHASE_5_AI_CONSENSUS", "1") == "1"

@app.get("/api/ai/{symbol}/consensus")
async def api_ai_consensus(symbol: str, request: Request):
    if not PHASE_5_AI_CONSENSUS:
        return error("Feature disabled", status_code=503)
    # ... mevcut kod
```

Sonra Railway'de `PHASE_5_AI_CONSENSUS=0` set et.

## Monitoring

Phase 5 sonrası izlenecek metrikler:

| Metric | Beklenen | Alert Eşiği |
|---|---|---|
| `/api/heatmap` p95 latency | <500ms | >2s |
| `/api/cross/{}/explain` p95 latency | <300ms | >1s |
| `/api/ai/{}/consensus` p95 latency | 5-15s | >25s |
| `/api/ai/{}/consensus` error rate | <5% | >20% |
| Heatmap `computing=true` ratio | <%30 | >%80 (cache cold çok sürüyor) |

## Bilinen Limitler

1. **AI consensus rate limit:** 4 provider paralel = 4x rate limit hit. Provider başına dakikada ~30 çağrı yapıyorsanız, hisse başına ~10 detail panel açılışı kapasitenin üst sınırı. Production'da Redis cache TTL artırılabilir.
2. **TradingView widget'ları:** Kod hazır ama henüz HTML mount noktası yok. `/macro` ve hisse detay sayfasında manuel olarak `<div id="tv-overview"></div>` + `window.lazyRenderTvOverview({symbol: 'THYAO'})` çağrısı eklenmeli (Phase 6).
3. **Mobile bottom-nav:** CSS hazır, JS render kodu yok. Activate etmek için `index.html`'e `<nav class="mob-bnav">` block'u eklenmeli (Phase 6).
4. **Onboarding tour:** Phase 5 içeriği eklenmedi (eski onboarding korundu). Phase 6'da Türkiye filtresi + AI consensus + sinyal kartları için 3-step tour önerilir.

## Phase 6 Önerileri

`PHASE_5_REPORT.md` Section 10'a bakın.

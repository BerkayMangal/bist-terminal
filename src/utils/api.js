const API_URL = '/api/analyze';

async function callClaude(system, userMessage) {
  const res = await fetch(API_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      system,
      messages: [{ role: 'user', content: userMessage }],
    }),
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  const data = await res.json();
  const text = (data.content || [])
    .filter(b => b.type === 'text')
    .map(b => b.text)
    .join('\n');
  // Extract JSON from response (handle markdown fences)
  const clean = text.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
  return JSON.parse(clean);
}

// ─── MARKET BAR ───
export async function fetchMarketData() {
  const system = `Sen bir piyasa veri servisisin. Güncel BIST ve emtia fiyatlarını bul.
SADECE geçerli JSON döndür, başka hiçbir şey yazma. Markdown kullanma.`;
  const msg = `Şu anki güncel fiyatları bul:
1. BIST XU030 endeksi
2. USD/TRY kuru
3. EUR/TRY kuru
4. Brent petrol (USD)
5. Altın gram fiyatı (TRY)
6. BIST XBANK endeksi

JSON formatı:
{
  "xu030": {"price": 0, "change_pct": 0},
  "usdtry": {"price": 0, "change_pct": 0},
  "eurtry": {"price": 0, "change_pct": 0},
  "brent": {"price": 0, "change_pct": 0},
  "gold": {"price": 0, "change_pct": 0},
  "xbank": {"price": 0, "change_pct": 0},
  "timestamp": "HH:MM"
}`;
  return callClaude(system, msg);
}

// ─── DASHBOARD: FULL ANALYSIS ───
export async function fetchStockAnalysis(ticker) {
  const system = `Sen 20 yıllık deneyime sahip bir BIST uzmanı finansal analistsin. 
Web'den güncel verileri kullanarak kapsamlı hisse analizi yap.
SADECE geçerli JSON döndür, başka hiçbir şey yazma. Markdown kullanma.`;
  const msg = `${ticker} hissesi için kapsamlı analiz yap. Web'den güncel verileri ara.

JSON formatı:
{
  "ticker": "${ticker}",
  "sirket_adi": "",
  "guncel_fiyat": 0,
  "degisim_pct": 0,
  "piotroski": {
    "skor": 0, "maks": 9,
    "detaylar": [{"kriter": "", "sonuc": true, "aciklama": ""}],
    "yorum": ""
  },
  "altman": {
    "z_skor": 0, "risk_seviyesi": "Düşük|Orta|Yüksek",
    "yorum": ""
  },
  "graham": {
    "ic_deger": 0, "guncel_fiyat": 0, "marj_guvenlik_pct": 0,
    "yorum": ""
  },
  "teknik": {
    "rsi": 0, "macd_sinyal": "AL|SAT|NÖTR",
    "trend": "Yükseliş|Düşüş|Yatay",
    "destek": 0, "direnc": 0,
    "ema20": 0, "ema50": 0, "ema200": 0,
    "yorum": ""
  },
  "haber": {
    "sentiment": "Pozitif|Negatif|Nötr", "skor": 0,
    "son_haberler": [{"baslik": "", "kaynak": "", "etki": "Pozitif|Negatif|Nötr"}],
    "yorum": ""
  },
  "viop": {
    "acik_pozisyon": 0, "degisim": "",
    "baz_farki": 0, "yorum": ""
  },
  "rakipler": {
    "liste": [{"ticker": "", "fk": 0, "pd_dd": 0, "getiri_ytd": 0}],
    "konumlama": ""
  },
  "dcf": {
    "hedef_fiyat": 0, "potansiyel_getiri_pct": 0,
    "varsayimlar": "", "tavsiye": "AL|TUT|SAT"
  },
  "karar": {
    "tavsiye": "AL|TUT|SAT", "guc_skoru": 0,
    "ozet": "", "kritik_firsat": "", "kritik_risk": ""
  }
}`;
  return callClaude(system, msg);
}

// ─── CROSS HUNTER ───
export async function fetchCrossSignals(stockList) {
  const system = `Sen teknik analiz uzmanısın. Hisse senetlerinde çapraz sinyal taraması yap.
SADECE geçerli JSON döndür, Markdown kullanma.`;
  const tickers = stockList.map(s => s.ticker).join(', ');
  const msg = `Şu BIST hisseleri için güncel teknik çapraz sinyalleri tara: ${tickers}

Her hisse için kontrol et:
- EMA 5/20 kesişimi (kısa vade)
- EMA 20/50 kesişimi (orta vade)  
- EMA 50/200 kesişimi (golden/death cross)
- RSI 30 altı (aşırı satım) veya 70 üstü (aşırı alım)
- MACD sinyal hattı kesişimi

Sadece AKTİF sinyal olan hisseleri döndür (son 5 gün içinde sinyal üretenler).

JSON formatı:
{
  "signals": [
    {
      "ticker": "", "sinyal_tipi": "EMA5_20|EMA20_50|EMA50_200|RSI_ASIRI_SATIM|RSI_ASIRI_ALIM|MACD",
      "yon": "AL|SAT", "guc": 1-10, "fiyat": 0,
      "aciklama": "", "tarih": "YYYY-MM-DD"
    }
  ],
  "ozet": { "toplam_al": 0, "toplam_sat": 0, "en_guclu": "" },
  "timestamp": ""
}`;
  return callClaude(system, msg);
}

// ─── QUANTUM SCANNER ───
export async function fetchQuantumScan(stockList) {
  const system = `Sen kantitatif finans uzmanısın. Her hisse için çoklu skorlama yap.
SADECE geçerli JSON döndür, Markdown kullanma.`;
  const tickers = stockList.map(s => s.ticker).join(', ');
  const msg = `Şu BIST hisseleri için quantum tarama skoru hesapla: ${tickers}

Her hisse için 0-100 arası skor ver:
- value_score: Değer (F/K, PD/DD, temettü verimi)
- momentum_score: Momentum (fiyat trendi, hacim, güç)
- teknik_score: Teknik (RSI, MACD, trend)
- temel_score: Temel (büyüme, karlılık, bilanço)
- flow_score: Akış (yabancı, hacim anomali)
- kangal_score: Genel KANGAL skoru (tüm faktörlerin ağırlıklı ortalaması)
- rejim: "TREND|RANGE|BREAKOUT|VOLATILE"
- sinyal: "AL|SAT|NÖTR"

JSON formatı:
{
  "stocks": [
    {
      "ticker": "", "fiyat": 0, "degisim_pct": 0,
      "value_score": 0, "momentum_score": 0, "teknik_score": 0,
      "temel_score": 0, "flow_score": 0, "kangal_score": 0,
      "rejim": "", "sinyal": "", "sektor": ""
    }
  ],
  "market_rejim": "",
  "timestamp": ""
}`;
  return callClaude(system, msg);
}

// ─── TAKAS ANALİZİ ───
export async function fetchTakasAnalysis(ticker) {
  const system = `Sen BIST takas ve aracı kurum analiz uzmanısın.
SADECE geçerli JSON döndür, Markdown kullanma.`;
  const msg = `${ticker} hissesi için son takas verilerini ve aracı kurum analizini yap.

KAP ve piyasa verilerinden:
- Hangi aracı kurumlar net alıcı/satıcı
- Yabancı takas oranı ve trendi
- Blok satışlar varsa
- "Kim mal topluyor" analizi

JSON formatı:
{
  "ticker": "${ticker}",
  "yabanci_oran_pct": 0, "yabanci_trend": "Artış|Azalış|Sabit",
  "net_hacim_mn_tl": 0,
  "araci_kurumlar": [
    {"kurum": "", "net_lot": 0, "yon": "ALICI|SATICI", "yorum": ""}
  ],
  "blok_islemler": [{"tarih": "", "lot": 0, "fiyat": 0, "aciklama": ""}],
  "analiz": {
    "kim_topluyor": "", "kurumsal_ilgi": "Yüksek|Orta|Düşük",
    "yorum": ""
  },
  "timestamp": ""
}`;
  return callClaude(system, msg);
}

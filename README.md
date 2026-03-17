# BIST Terminal v2.0

Bloomberg Terminal estetiğinde, Claude AI destekli BIST hisse analiz platformu.

## Özellikler

### 📊 Ana Analiz (Dashboard)
- 8 modül: Piotroski, Altman Z, Graham, Teknik, Haber, VİOP, Rakip, DCF
- Tek hisse kodu gir → tam kapsamlı analiz

### ⚡ Cross Hunter
- 40 hissede 6 farklı çapraz sinyal tarama
- EMA 5/20, 20/50, 50/200 kesişimleri
- RSI aşırı satım/alım, MACD sinyal kesişimi
- Güce göre sıralama, filtre

### ◈ Quantum Tarayıcı
- 40 hisse × 6 skor boyutu (Value, Momentum, Teknik, Temel, Akış, KANGAL)
- Sektör, sinyal, rejim, minimum skor filtresi
- Sıralanabilir tablo
- KANGAL modülleri: Breakout/Momentum/Flow/Rejim

### ◎ Takas Analizi
- Aracı kurum bazlı net alım/satım
- Yabancı takas oranı ve trendi
- "Kim mal topluyor" analizi
- Blok işlemler

### 📈 Piyasa Barı
- XU030, USD/TRY, EUR/TRY, Brent, Altın, XBANK
- 5 dakikada bir otomatik güncelleme

## Kurulum

### Lokal Geliştirme

```bash
# Klonla
git clone https://github.com/YOUR_USER/bist-terminal.git
cd bist-terminal

# Bağımlılıkları yükle
npm install

# .env dosyasını ayarla
cp .env.example .env
# .env içine ANTHROPIC_API_KEY'i yaz

# Geliştirme modunda çalıştır (iki terminal gerekli)
# Terminal 1: API server
node server.js

# Terminal 2: Vite dev server
npm run dev
```

Tarayıcıda aç: http://localhost:3000

### Railway Deploy

1. GitHub'a pushla
2. Railway'de yeni proje oluştur → GitHub repo'yu bağla
3. Environment Variables'da `ANTHROPIC_API_KEY` ekle
4. Build Command: `npm install && npm run build`
5. Start Command: `npm start`

### Vercel Deploy

```bash
npm run build
# dist/ klasörünü Vercel'e deploy et
# API için ayrı serverless function gerekir
```

## Tech Stack

- **Frontend:** React 18 + Vite
- **Backend:** Express.js (API proxy)
- **AI:** Claude Sonnet (web search + analiz)
- **Styling:** Custom CSS (Bloomberg terminal tema)
- **Font:** IBM Plex Mono + Space Grotesk

## Dosya Yapısı

```
bist-terminal/
├── package.json
├── vite.config.js
├── server.js              # Express API proxy
├── index.html
├── .env.example
├── src/
│   ├── main.jsx           # React entry
│   ├── App.jsx            # Ana layout + routing
│   ├── App.css            # Global stiller
│   ├── config/
│   │   └── stocks.js      # 40 hisse + sektör + config
│   ├── utils/
│   │   └── api.js         # Claude API çağrıları
│   ├── components/
│   │   ├── MarketBar.jsx  # Üst piyasa barı
│   │   └── Navigation.jsx # Tab navigasyon
│   └── pages/
│       ├── Dashboard.jsx      # 8 modül analiz
│       ├── CrossHunter.jsx    # Sinyal tarayıcı
│       ├── QuantumScanner.jsx # 40 hisse quantum tablo
│       └── TakasAnalizi.jsx   # Aracı kurum flow
```

## Notlar

- Tüm veriler Claude AI + web search ile çekilir
- Yatırım tavsiyesi değildir
- ANTHROPIC_API_KEY gereklidir
- Rate limit: Claude API limitler geçerli

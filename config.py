# ================================================================
# BISTBULL TERMINAL V9.1 — CONFIG
# Tüm sabitler, universe, sektör eşikleri, cache TTL, ağırlıklar
# Diğer dosyalarda magic number SIFIR.
# ================================================================

import os

# ================================================================
# APP META
# ================================================================
BOT_VERSION = "V9.1"
APP_NAME = "BISTBULL TERMINAL"
CONFIDENCE_MIN = 50

# ================================================================
# AI PROVIDER CONFIG
# ================================================================
GROK_KEY: str = os.environ.get("XAI_API_KEY", "") or os.environ.get("GROK_API_KEY", "")
GROK_MODEL: str = os.environ.get("GROK_MODEL", "grok-3-mini-fast")
OPENAI_KEY: str = os.environ.get("OPENAI_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_KEY", "")
ANTHROPIC_MODEL: str = os.environ.get("AI_MODEL", "claude-sonnet-4-20250514")

# ================================================================
# CACHE TTL (seconds)
# ================================================================
RAW_CACHE_TTL = 86400
ANALYSIS_CACHE_TTL = 86400
TECH_CACHE_TTL = 3600
AI_CACHE_TTL = 7200
HISTORY_CACHE_TTL = 3600
MACRO_CACHE_TTL = 600
TAKAS_CACHE_TTL = 1800
SOCIAL_CACHE_TTL = 1800
BRIEFING_CACHE_TTL = 3600
HERO_CACHE_TTL = 1800
AGENT_CACHE_TTL = 600
HEATMAP_CACHE_TTL = 900
MACRO_AI_CACHE_TTL = 3600

# ================================================================
# CACHE MAX SIZES
# ================================================================
RAW_CACHE_SIZE = 5000
ANALYSIS_CACHE_SIZE = 5000
TECH_CACHE_SIZE = 500
AI_CACHE_SIZE = 200
HISTORY_CACHE_SIZE = 500

# ================================================================
# SCANNER / THREADING CONFIG
# ================================================================
SCAN_MAX_WORKERS = 25
RAW_PREFETCH_WORKERS = 25
BATCH_HISTORY_WORKERS = 25
BACKGROUND_SCAN_INTERVAL_OPEN = 3600
BACKGROUND_SCAN_INTERVAL_CLOSED = 10800
BACKGROUND_SCAN_STARTUP_DELAY = 1

# ================================================================
# UNIVERSE — BIST TOP 25
# ================================================================
UNIVERSE: list[str] = [
    # Mega-cap / en likit
    "THYAO", "ASELS", "GARAN", "AKBNK", "ISCTR", "YKBNK",
    # Holdingler
    "KCHOL", "SAHOL",
    # Sanayi / otomotiv / enerji
    "FROTO", "TOASO", "TUPRS", "EREGL", "SISE", "ARCLK", "PETKM",
    # Perakende / telco / havacılık
    "BIMAS", "MGROS", "TCELL", "TTKOM", "TAVHL", "PGSUS",
    # Savunma / inşaat / GYO
    "ENKAI", "EKGYO", "HEKTS", "SASA",
]

# ================================================================
# FA SCORE AĞIRLIKLARI
# ================================================================
FA_WEIGHTS: dict[str, float] = {
    "quality":  0.30,
    "value":    0.18,
    "growth":   0.15,
    "balance":  0.10,
    "earnings": 0.10,
    "moat":     0.08,
    "capital":  0.09,
}

IVME_WEIGHTS: dict[str, float] = {
    "momentum":   0.40,
    "tech_break": 0.35,
    "inst_flow":  0.25,
}

OVERALL_FA_WEIGHT = 0.55
OVERALL_MOMENTUM_WEIGHT = 0.35
OVERALL_RISK_CAP = -30
OVERALL_RISK_FACTOR = 0.3

# ================================================================
# VALUATION STRETCH TABLE
# ================================================================
VALUATION_STRETCH: list[tuple[float, float, int]] = [
    # (min_value, max_value, stretch_points)
    # Sıralama: ilk eşleşen uygulanır (yukarıdan aşağı)
]
# Fonksiyon olarak: config'den okuyan scoring.py kullanacak
VAL_STRETCH_MAP: list[tuple[int, int]] = [
    (80, 10), (65, 5), (55, 2), (45, 0), (35, -2), (25, -5), (15, -10), (0, -15),
]

# ================================================================
# SEKTÖR BAZLI EŞIK SİSTEMİ
# Format: (bad/great, ok/good, good/ok, great/bad) → score_higher/score_lower'a gider
# None = o metrik bu sektör için devre dışı
# ================================================================
SECTOR_THRESHOLDS: dict[str, dict] = {
    "banka": {
        "pe": (12, 8, 5, 3),
        "pb": (1.8, 1.2, 0.8, 0.5),
        "roe": (0.08, 0.14, 0.20, 0.28),
        "net_margin": (0.05, 0.12, 0.20, 0.30),
        "ev_ebitda": None,
        "debt_equity": None,
        "current_ratio": None,
        "altman_z": None,
    },
    "holding": {
        "pe": (18, 12, 8, 5),
        "pb": (1.8, 1.3, 0.9, 0.5),
        "roe": (0.04, 0.08, 0.14, 0.20),
        "net_margin": (0.03, 0.06, 0.12, 0.20),
    },
    "savunma": {
        "pe": (30, 20, 14, 8),
        "ev_ebitda": (16, 12, 8, 5),
        "roe": (0.06, 0.12, 0.18, 0.25),
        "revenue_growth": (-0.02, 0.08, 0.18, 0.30),
    },
    "enerji": {
        "pe": (10, 7, 4, 2),
        "ev_ebitda": (10, 7, 5, 3),
        "net_margin": (0.01, 0.04, 0.08, 0.15),
        "net_debt_ebitda": (3.5, 2.5, 1.5, 0.5),
        "debt_equity": (80, 150, 250, 400),
        "altman_z": (0.8, 1.5, 2.5, 3.5),
    },
    "perakende": {
        "pe": (22, 16, 10, 6),
        "net_margin": (0.01, 0.03, 0.06, 0.10),
        "revenue_growth": (-0.03, 0.08, 0.15, 0.25),
        "asset_turnover": (0.5, 0.9, 1.4, 2.0),
    },
    "ulasim": {
        "pe": (12, 8, 5, 3),
        "ev_ebitda": (8, 6, 4, 3),
        "roe": (0.05, 0.10, 0.16, 0.24),
        "net_debt_ebitda": (3.5, 2.5, 1.5, 0.5),
        "debt_equity": (150, 300, 500, 700),
        "current_ratio": (0.6, 0.9, 1.2, 1.8),
        "altman_z": (0.5, 1.0, 1.8, 2.8),
    },
    "sanayi": {
        "pe": (20, 14, 8, 5),
        "roe": (0.04, 0.10, 0.16, 0.22),
        "roic": (0.03, 0.08, 0.13, 0.18),
        "net_margin": (0.02, 0.06, 0.10, 0.16),
        "ev_ebitda": (12, 8, 5, 3),
    },
}

# Default eşikler (sektör override yoksa bunlar kullanılır)
DEFAULT_THRESHOLDS: dict[str, tuple] = {
    "pe": (25, 16, 10, 6),
    "pb": (4.5, 2.5, 1.5, 0.8),
    "ev_ebitda": (16, 11, 7, 4),
    "roe": (0.01, 0.06, 0.12, 0.20),
    "roic": (0.01, 0.06, 0.10, 0.16),
    "net_margin": (0.005, 0.03, 0.08, 0.15),
    "revenue_growth": (-0.05, 0.05, 0.15, 0.30),
    "net_debt_ebitda": (0.5, 1.5, 2.5, 4.0),
    "debt_equity": (30, 80, 150, 300),
    "current_ratio": (0.8, 1.1, 1.5, 2.2),
    "altman_z": (1.2, 1.8, 3.0, 4.5),
}

# Sektör mapping kuralları
SECTOR_KEYWORDS: dict[str, list[str]] = {
    "banka": ["bank", "financial serv"],
    "holding": ["holding", "conglomerate", "industrial conglomerate"],
    "savunma": ["defense", "aerospace"],
    "enerji": ["energy", "oil", "gas", "refin", "mining", "metals"],
    "perakende": ["retail", "consumer", "food", "beverage", "household"],
    "ulasim": ["airline", "transport", "logistic", "shipping"],
}
SECTOR_DEFAULT = "sanayi"

# ================================================================
# KADEMELİ RİSK PENALTI TABLOLARI
# ================================================================
PENALTY_ND_EBITDA_DEFAULT: list[tuple[float, int]] = [
    (5.0, -25), (4.0, -18), (3.0, -12), (2.5, -8), (2.0, -4),
]
PENALTY_ND_EBITDA_HIGH_DEBT: list[tuple[float, int]] = [
    (7.0, -25), (5.5, -18), (4.5, -12), (3.5, -8), (3.0, -4),
]
HIGH_DEBT_SECTORS: set[str] = {"ulasim", "enerji"}

PENALTY_DILUTION: list[tuple[float, int]] = [
    (0.20, -20), (0.10, -12), (0.05, -6), (0.02, -3),
]
PENALTY_BENEISH: list[tuple[float, int]] = [
    (-1.5, -18), (-1.78, -10), (-2.22, -3),
]

# Sabit penaltiler
PENALTY_NEGATIVE_EQUITY = -15
PENALTY_NET_LOSS = -10
PENALTY_NEGATIVE_CFO = -8
PENALTY_FAKE_PROFIT = -12
PENALTY_LOW_CASH_QUALITY = -6
BONUS_NET_CASH = 4
NET_CASH_THRESHOLD_MULTIPLIER = 1.2

# Interest coverage penaltileri
INT_COV_PENALTIES: list[tuple[float, int]] = [
    (1.0, -20), (1.5, -12), (2.0, -8), (3.0, -4),
]

# Hype detection eşikleri
HYPE_STRICT_PCT = 25
HYPE_STRICT_VOL = 2.5
HYPE_STRICT_FA = 40
HYPE_SOFT_PCT = 15
HYPE_SOFT_VOL = 2.0
HYPE_SOFT_FA = 35

# ================================================================
# CONFIDENCE KEYS — hangi metriklerin varlığına bakılacak
# ================================================================
CONFIDENCE_KEYS: list[str] = [
    "pe", "pb", "fcf_yield", "roe", "roic", "operating_margin",
    "revenue_growth", "eps_growth", "net_debt_ebitda", "interest_coverage",
    "cfo_to_ni", "piotroski_f", "altman_z", "peg", "margin_safety",
    "inst_holders_pct",
]

# ================================================================
# MACRO SYMBOLS
# ================================================================
MACRO_SYMBOLS: dict[str, dict] = {
    "XU030": {"symbol": "XU030.IS", "name": "BIST 30", "category": "turkiye", "flag": "🇹🇷"},
    "XU100": {"symbol": "XU100.IS", "name": "BIST 100", "category": "turkiye", "flag": "🇹🇷"},
    "USDTRY": {"symbol": "USDTRY=X", "name": "USD/TRY", "category": "turkiye", "flag": "🇹🇷"},
    "EURTRY": {"symbol": "EURTRY=X", "name": "EUR/TRY", "category": "turkiye", "flag": "🇹🇷"},
    "EEM": {"symbol": "EEM", "name": "iShares EM ETF", "category": "em", "flag": "🌍"},
    "IBOV": {"symbol": "^BVSP", "name": "Bovespa (Brezilya)", "category": "em", "flag": "🇧🇷"},
    "SENSEX": {"symbol": "^BSESN", "name": "Sensex (Hindistan)", "category": "em", "flag": "🇮🇳"},
    "MEXIPC": {"symbol": "^MXX", "name": "IPC (Meksika)", "category": "em", "flag": "🇲🇽"},
    "JCI": {"symbol": "^JKSE", "name": "JCI (Endonezya)", "category": "em", "flag": "🇮🇩"},
    "JSE": {"symbol": "^JN0U.JO", "name": "JSE Top40 (G.Afrika)", "category": "em", "flag": "🇿🇦"},
    "KOSPI": {"symbol": "^KS11", "name": "KOSPI (G.Kore)", "category": "em", "flag": "🇰🇷"},
    "TWSE": {"symbol": "^TWII", "name": "TAIEX (Tayvan)", "category": "em", "flag": "🇹🇼"},
    "WIG20": {"symbol": "WIG20.WA", "name": "WIG20 (Polonya)", "category": "em", "flag": "🇵🇱"},
    "CSI300": {"symbol": "000300.SS", "name": "CSI 300 (Çin)", "category": "em", "flag": "🇨🇳"},
    "SP500": {"symbol": "^GSPC", "name": "S&P 500", "category": "global", "flag": "🇺🇸"},
    "NASDAQ": {"symbol": "^IXIC", "name": "Nasdaq", "category": "global", "flag": "🇺🇸"},
    "DAX": {"symbol": "^GDAXI", "name": "DAX (Almanya)", "category": "global", "flag": "🇩🇪"},
    "FTSE": {"symbol": "^FTSE", "name": "FTSE 100 (UK)", "category": "global", "flag": "🇬🇧"},
    "NIKKEI": {"symbol": "^N225", "name": "Nikkei 225 (Japonya)", "category": "global", "flag": "🇯🇵"},
    "BRENT": {"symbol": "BZ=F", "name": "Brent Petrol", "category": "emtia", "flag": "🛢️"},
    "GOLD": {"symbol": "GC=F", "name": "Altın (oz)", "category": "emtia", "flag": "🥇"},
    "SILVER": {"symbol": "SI=F", "name": "Gümüş (oz)", "category": "emtia", "flag": "🥈"},
    "DXY": {"symbol": "DX-Y.NYB", "name": "Dolar Endeksi", "category": "emtia", "flag": "💵"},
    "VIX": {"symbol": "^VIX", "name": "VIX (Korku)", "category": "emtia", "flag": "😱"},
}

# ================================================================
# FİNANS SÖZLERI
# ================================================================
FINANCE_QUOTES: list[dict] = [
    {"text": "Fiyat ne ödediğinizdir, değer ne aldığınızdır.", "author": "Warren Buffett"},
    {"text": "Piyasa kısa vadede oylama makinesi, uzun vadede tartı makinesidir.", "author": "Benjamin Graham"},
    {"text": "En iyi yatırım kendinize yapacağınız yatırımdır.", "author": "Warren Buffett"},
    {"text": "Borsa sabırlıdan sabırsıza para transferi yapar.", "author": "Warren Buffett"},
    {"text": "Risk, ne yaptığınızı bilmemekten kaynaklanır.", "author": "Warren Buffett"},
    {"text": "Harika şirketi makul fiyata almak, makul şirketi harika fiyata almaktan iyidir.", "author": "Warren Buffett"},
    {"text": "Herkes açgözlü iken korkun, herkes korkak iken açgözlü olun.", "author": "Warren Buffett"},
    {"text": "Basitlik, sofistikeliğin nihai formudur.", "author": "Charlie Munger"},
    {"text": "Bildiğinizi alın, aldığınızı bilin.", "author": "Peter Lynch"},
    {"text": "En iyi zaman ağaç dikmek için 20 yıl önceydi. İkinci en iyi zaman bugün.", "author": "Çin Atasözü"},
    {"text": "Getiri peşinde koşmayın, riski yönetin. Getiri kendiliğinden gelir.", "author": "Benjamin Graham"},
    {"text": "Piyasadaki en tehlikeli dört kelime: Bu sefer farklı olacak.", "author": "Sir John Templeton"},
    {"text": "Sabır, yatırımcının en güçlü silahıdır.", "author": "Jesse Livermore"},
    {"text": "Trendin arkadaşındır, ta ki dönene kadar.", "author": "Ed Seykota"},
    {"text": "Bileşik faiz dünyanın sekizinci harikasıdır.", "author": "Albert Einstein"},
    {"text": "Bir hisseyi 10 yıl tutmayı düşünmüyorsanız, 10 dakika bile tutmayın.", "author": "Warren Buffett"},
    {"text": "Kazananları tut, kaybedenleri kes.", "author": "William O'Neil"},
    {"text": "Nakit pozisyon da bir pozisyondur.", "author": "Jesse Livermore"},
    {"text": "Enflasyon sessiz bir hırsızdır.", "author": "Milton Friedman"},
    {"text": "İyi şirketler kötü zamanlarda büyür.", "author": "Shelby Davis"},
    {"text": "Yatırımda en önemli kalite mizaçtır, zekâ değil.", "author": "Warren Buffett"},
    {"text": "Batmamak için çeşitlendir, zengin olmak için yoğunlaş.", "author": "Andrew Carnegie"},
    {"text": "Piyasa size ders verecekse en pahalısını verir.", "author": "Wall Street Atasözü"},
    {"text": "Yalnız kalabalığın tersine gitmeye hazır olan büyük kazançlar elde edebilir.", "author": "Sir John Templeton"},
]

# ================================================================
# FİNANS KİTAPLARI
# ================================================================
FINANCE_BOOKS: list[dict] = [
    {"title": "Akıllı Yatırımcı", "author": "Benjamin Graham", "description": "Değer yatırımının kutsal kitabı. Graham, hisse seçimi ve risk yönetimini basit ama derin anlatır. Buffett'ın 'hayatımı değiştiren kitap' dediği eser.", "level": "Başlangıç-Orta"},
    {"title": "Borsada Teknik Analiz", "author": "John J. Murphy", "description": "Teknik analizin ansiklopedisi. Grafik okuma, trend analizi, göstergeler — hepsi tek kitapta.", "level": "Orta"},
    {"title": "Bir Adım Önde", "author": "Peter Lynch", "description": "Efsanevi Magellan Fund yöneticisi, sıradan yatırımcının Wall Street'i nasıl yenebileceğini anlatıyor.", "level": "Başlangıç"},
    {"title": "Piyasa Büyücüleri", "author": "Jack D. Schwager", "description": "Dünyanın en başarılı trader'larıyla röportajlar. Ortak nokta: disiplin ve risk yönetimi.", "level": "Orta-İleri"},
    {"title": "Zengin Baba Yoksul Baba", "author": "Robert Kiyosaki", "description": "Para, yatırım ve finansal özgürlük hakkında temel bakış açısı.", "level": "Başlangıç"},
    {"title": "Kaybeden Trader'ın Günlüğü", "author": "Jim Paul", "description": "75 milyon dolar kaybeden bir trader'ın hikâyesi. Kazanmaktan çok kaybetmeyi anlamak için.", "level": "Herkes"},
    {"title": "Borsanın Sınırları", "author": "Nassim N. Taleb", "description": "Siyah Kuğu teorisinin babası, risk, belirsizlik ve piyasalardaki rastlantıyı gözler önüne seriyor.", "level": "İleri"},
    {"title": "Para Psikolojisi", "author": "Morgan Housel", "description": "Yatırım kararları mantık değil psikoloji ile alınır.", "level": "Başlangıç"},
    {"title": "Hisselerde Uzun Vadeli Yatırım", "author": "Jeremy Siegel", "description": "200 yıllık veriyle hisse senetlerinin neden uzun vadede en iyi yatırım aracı olduğunu kanıtlıyor.", "level": "Orta"},
    {"title": "Babil'in En Zengin Adamı", "author": "George S. Clason", "description": "5000 yıllık para bilgeliği modern hikâyelerle. Kısa ve etkili.", "level": "Başlangıç"},
    {"title": "Değer Yatırımının Küçük Kitabı", "author": "Christopher Browne", "description": "Graham-Buffett okulunun modern özeti.", "level": "Başlangıç-Orta"},
    {"title": "Flash Boys", "author": "Michael Lewis", "description": "Yüksek frekanslı trading dünyasının içerisinden nefes kesen anlatı.", "level": "Herkes"},
    {"title": "Trader Vic", "author": "Victor Sperandeo", "description": "40 yıllık tecrübesiyle trend takibi ve risk yönetimini pratikte öğretiyor.", "level": "Orta-İleri"},
    {"title": "Warren Buffett ve Finansal Tabloların Yorumu", "author": "Mary Buffett", "description": "Ustanın bilanço okuma yöntemini herkesin anlayacağı dilde aktarıyor.", "level": "Başlangıç"},
    {"title": "Kapital", "author": "Thomas Piketty", "description": "Servet eşitsizliği ve kapitalizmin dinamikleri. Makro düşünmeyi öğreten eser.", "level": "İleri"},
]

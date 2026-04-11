# ================================================================
# BISTBULL TERMINAL V10.0 — MASTER CONFIG
# Tüm sabitler, universe, sektör eşikleri, cache TTL, ağırlıklar,
# Redis, Circuit Breaker, Rate Limiter, Applicability kuralları.
# Diğer dosyalarda magic number SIFIR.
# ================================================================

import os

# ================================================================
# APP META
# ================================================================
BOT_VERSION = "V10.0"
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
# REDIS CONFIG — L2 persistent cache
# Railway sets REDIS_URL automatically when Redis add-on is attached.
# If empty, system falls back to RAM-only (L1) cache — V9.1 behavior.
# ================================================================
REDIS_URL: str = os.environ.get("REDIS_URL", "")
REDIS_SOCKET_TIMEOUT: int = 5
REDIS_SOCKET_CONNECT_TIMEOUT: int = 5
REDIS_RETRY_ON_TIMEOUT: bool = True
REDIS_MAX_CONNECTIONS: int = 20
REDIS_HEALTH_CHECK_INTERVAL: int = 30
REDIS_KEY_PREFIX: str = "bb:"
REDIS_SNAPSHOT_KEY: str = "bb:snapshot:top10"
REDIS_SCAN_LOCK_KEY: str = "bb:lock:scan"
REDIS_SCAN_LOCK_TTL: int = 600

# ================================================================
# CIRCUIT BREAKER CONFIG
# State machine: CLOSED → OPEN → HALF_OPEN → CLOSED
# Her dış kaynak (borsapy, yfinance, Grok, OpenAI, Anthropic) ayrı CB.
# ================================================================
CB_FAILURE_THRESHOLD: int = 5
CB_RECOVERY_TIMEOUT: int = 60
CB_HALF_OPEN_MAX_CALLS: int = 2
CB_SUCCESS_THRESHOLD: int = 2

# Provider-specific overrides
CB_BORSAPY_FAILURE_THRESHOLD: int = 15
CB_BORSAPY_RECOVERY_TIMEOUT: int = 120
CB_YFINANCE_FAILURE_THRESHOLD: int = 5
CB_YFINANCE_RECOVERY_TIMEOUT: int = 90
CB_AI_FAILURE_THRESHOLD: int = 3
CB_AI_RECOVERY_TIMEOUT: int = 45

# ================================================================
# RATE LIMITER CONFIG
# Sliding window IP-based rate limiting for expensive endpoints.
# ================================================================
RATE_LIMIT_ENABLED: bool = True
RATE_LIMIT_AI_SUMMARY: int = 10
RATE_LIMIT_AI_SUMMARY_WINDOW: int = 60
RATE_LIMIT_AGENT: int = 15
RATE_LIMIT_AGENT_WINDOW: int = 60
RATE_LIMIT_BRIEFING: int = 5
RATE_LIMIT_BRIEFING_WINDOW: int = 60
RATE_LIMIT_SCAN: int = 3
RATE_LIMIT_SCAN_WINDOW: int = 300

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

# Stale grace period — stale-while-revalidate: serve stale data while refreshing
# If data is older than TTL but younger than TTL + STALE_GRACE, serve it with stale=True
STALE_GRACE_SECONDS = 3600

# ================================================================
# CACHE MAX SIZES (L1 RAM layer)
# ================================================================
RAW_CACHE_SIZE = 5000
ANALYSIS_CACHE_SIZE = 5000
TECH_CACHE_SIZE = 500
AI_CACHE_SIZE = 200
HISTORY_CACHE_SIZE = 500

# ================================================================
# SCANNER / THREADING CONFIG
# ================================================================
SCAN_MAX_WORKERS = 15
RAW_PREFETCH_WORKERS = 10
BATCH_HISTORY_WORKERS = 10
BACKGROUND_SCAN_INTERVAL_OPEN = 3600
BACKGROUND_SCAN_INTERVAL_CLOSED = 10800
BACKGROUND_SCAN_STARTUP_DELAY = 1

# Scan phases (for progress tracking)
SCAN_PHASES: list[str] = [
    "prep",
    "raw_fetch",
    "history_fetch",
    "technical_compute",
    "scoring",
    "snapshot_publish",
    "ai_enrich",
    "done",
]

# ================================================================
# WEBSOCKET CONFIG
# ================================================================
WS_SCAN_PROGRESS_INTERVAL: float = 1.0
WS_MAX_CONNECTIONS: int = 50

# ================================================================
# RESPONSE ENVELOPE META DEFAULTS
# ================================================================
RESPONSE_BUILD_VERSION: str = BOT_VERSION

# ================================================================
# UNIVERSE — TÜM BİST (260+ hisse)
# ================================================================
UNIVERSE_BIST30: list[str] = [
    "ASELS", "THYAO", "BIMAS", "KCHOL", "SISE", "EREGL", "TUPRS", "AKBNK", "ISCTR", "YKBNK",
    "GARAN", "SAHOL", "MGROS", "FROTO", "TOASO", "TCELL", "KRDMD", "PETKM", "ENKAI", "TAVHL",
    "PGSUS", "EKGYO", "ARCLK", "TTKOM", "SOKM", "TKFEN", "KONTR", "AKSEN", "HEKTS", "SASA",
]

UNIVERSE_EXTRA: list[str] = [
    "VESTL", "DOHOL", "AYGAZ", "LOGO", "INDES", "ODAS", "GUBRF", "CIMSA", "MPARK",
    "OYAKC", "ISMEN", "TTRAK", "AEFES", "DOAS", "AGHOL", "OTKAR", "VESBE", "EGEEN", "TMSN",
    "GESAN", "ZOREN", "ENJSA", "AYDEM", "ISDMR",
    "HALKB", "VAKBN", "TSKB", "SKBNK", "ALBRK", "ANHYT", "AGESA", "TURSG", "ANSGR", "GLYHO", "BERA",
    "ULKER", "CCOLA", "PNSUT", "MAVI", "BIZIM", "YATAS", "ADEL",
    "NETAS", "KRONT", "ALARK", "ASTOR", "PAPIL",
    "ISGYO", "HLGYO", "KLGYO", "AKFGY", "BTCIM", "BAGFS",
    "CLEBI", "RYSAS",
    "CWENE", "SMRTG", "KCAER",
    "BRYAT", "EUPWR", "BRSAN", "SARKY", "GEDZA", "BUCIM", "KORDS", "KARTN", "DEVA",
    "CANTE", "CEMTS", "NUHCM", "PRKME", "AKSA", "GOLTS", "ERBOS", "MIATK", "QUAGR", "FORTE", "RGYAS",
]

UNIVERSE_EXTENDED: list[str] = [
    "AKENR", "AKFYE", "AKGRT", "ALCTL", "ALKIM", "ANACM", "ARDYZ", "ARENA", "ARMDA", "ASUZU",
    "ATAGY", "ATATP", "AVOD", "AVTUR", "BANVT", "BEYAZ", "BFREN", "BIENY", "BINHO", "BIOEN",
    "BMELK", "BMSTL", "BRMEN", "BURVA", "CEMAS", "CONSE", "CRFSA", "DAGI", "DENIZ", "DESA",
    "DESPC", "DGATE", "DOKTA", "DYOBY", "EDATA", "EGGUB", "EMNIS", "ESCOM", "ESEN", "ETILR",
    "FADE", "FMIZP", "FONET", "GEDIK", "GENIL", "GLCVY", "GOODY", "GWIND", "HATEK", "HDFGS",
    "HEDEF", "HTTBT", "HUNER", "ICBCT", "IHAAS", "INGRM", "INTEM", "IPEKE", "ISBIR", "ISFIN",
    "IZENR", "KAREL", "KARSN", "KATMR", "KENT", "KEREV", "KERVT", "KFEIN", "KGYO", "KLMSN",
    "KLNMA", "KONYA", "KOZAA", "KOZAL", "KRVGD", "KSTUR", "KUYAS", "LINK", "LMKDC", "LUKSK",
    "MAALT", "MAGEN", "MARTI", "MEGAP", "MERCN", "METUR", "MIGRS", "MOBTL", "MRGYO", "MRSHL",
    "MSGYO", "MTRKS", "MTRYO", "NATEN", "NTHOL", "NUGYO", "OLMIP", "OSMEN", "OYLUM", "OYYAT",
    "OZGYO", "OZKGY", "PAGYO", "PARSN", "PCILT", "PENGD", "PINSU", "PKART", "PRKAB", "PRZMA",
    "QNBFB", "RAYSG", "SAFKR", "SEKUR", "SELEC", "SILVR", "SMART", "SNGYO", "SNKRN", "SUNTK",
    "SUWEN", "TAIHL", "TATGD", "TBORG", "TEKTU", "TICR", "TKNSA", "TMPOL", "TNZTP", "TRGYO",
    "TRILC", "TRKCM", "TUCLK", "TUKAS", "TUREX", "UFUK", "ULUUN", "USAK", "UTPYA", "VAKKO",
    "VERTU", "VKGYO", "VKING", "YAPRK", "YEOTK", "YGYO", "YKSLN", "YONGA", "YUNSA", "YYAPI",
    "ZEDUR", "ZELOT",
]

UNIVERSE: list[str] = UNIVERSE_BIST30 + UNIVERSE_EXTRA + UNIVERSE_EXTENDED

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
VALUATION_STRETCH: list[tuple[float, float, int]] = []
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
# SEKTÖR APPLICABILITY MATRİSİ — V10 YENİ
# Hangi metrik hangi sektör için "uygulanabilir" / "düşük güven" / "uygulanamaz".
# Skor motoru bu matrisi okur ve N/A olan boyutları zorla puanlamaz.
#
# Değerler:
#   "full"    = tam uygulanabilir (normal puanlama)
#   "low"     = düşük güvenilirlik (puan üretilir ama UI'da uyarı gösterilir)
#   "na"      = uygulanamaz (puan üretilmez, ağırlık diğerlerine dağıtılır)
#
# Matris key: sektör grubu (map_sector çıktısı)
# Matris value: metrik → applicability
# Listede olmayan sektörler = tüm metrikler "full"
# Listede olmayan metrikler = "full"
# ================================================================
SECTOR_APPLICABILITY: dict[str, dict[str, str]] = {
    "banka": {
        "altman_z": "na",
        "beneish_m": "low",
        "graham_fair_value": "na",
        "ev_ebitda": "na",
        "debt_equity": "na",
        "current_ratio": "na",
        "net_debt_ebitda": "na",
        "fcf_yield": "low",
        "operating_margin": "low",
        "asset_turnover": "na",
        "roic": "low",
    },
    "holding": {
        "altman_z": "low",
        "beneish_m": "low",
        "graham_fair_value": "low",
        "ev_ebitda": "low",
        "operating_margin": "low",
        "fcf_yield": "low",
        "asset_turnover": "na",
    },
    "sigorta": {
        "altman_z": "na",
        "graham_fair_value": "na",
        "ev_ebitda": "na",
        "debt_equity": "na",
        "current_ratio": "na",
        "net_debt_ebitda": "na",
        "operating_margin": "low",
        "asset_turnover": "na",
        "roic": "low",
    },
    "gayrimenkul": {
        "altman_z": "low",
        "graham_fair_value": "low",
        "operating_margin": "low",
        "revenue_growth": "low",
    },
}

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
# LIQUIDITY GUARD — V10 YENİ
# Düşük hacimli hisselerde teknik sinyal güvenini düşür.
# ================================================================
LIQUIDITY_MIN_AVG_VOLUME: int = 500_000
LIQUIDITY_LOW_VOLUME_THRESHOLD: int = 1_000_000
LIQUIDITY_CONFIDENCE_HAIRCUT: float = 0.3
LIQUIDITY_MIN_TRADING_DAYS: int = 60

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
    "US10Y": {"symbol": "^TNX", "name": "ABD 10Y Tahvil", "category": "global", "flag": "🇺🇸"},
}

# ================================================================
# STATIK ORANLAR — Manuel güncelleme gerekir
# Son güncelleme: 25 Mart 2026
# ================================================================
STATIC_RATES: list[dict] = [
    {"key": "TCMB", "name": "TCMB Politika", "rate": 37.00, "prev": 38.00, "unit": "%", "flag": "🇹🇷", "updated": "2026-03-12", "note": "Sabit tutuldu — 22 Nisan kararı bekleniyor"},
    {"key": "FED", "name": "Fed Funds", "rate": 3.75, "prev": 3.75, "unit": "%", "flag": "🇺🇸", "updated": "2026-03-18", "note": "3.50-3.75 bant, sabit — İran savaşı belirsizliği"},
    {"key": "ECB", "name": "ECB Refi", "rate": 2.15, "prev": 2.15, "unit": "%", "flag": "🇪🇺", "updated": "2026-03-19", "note": "Sabit — bazı bankalar artırım bekliyor"},
    {"key": "CDS_TR", "name": "Türkiye 5Y CDS", "rate": 295, "prev": 280, "unit": "bps", "flag": "🇹🇷", "updated": "2026-04-11", "note": "Tahmini — MacroMicro W14 referans"},
    {"key": "TR10Y", "name": "TR 10Y Tahvil", "rate": 30.5, "prev": 29.8, "unit": "%", "flag": "🇹🇷", "updated": "2026-04-11", "note": "Tahmini — güncel teyit gerekli"},
    {"key": "TR2Y", "name": "TR 2Y Tahvil", "rate": 34.0, "prev": 33.5, "unit": "%", "flag": "🇹🇷", "updated": "2026-04-11", "note": "Tahmini — güncel teyit gerekli"},
]

# Statik oranların yaşını kontrol etmek için (gün cinsinden)
STATIC_RATES_STALE_DAYS: int = 14

# ================================================================
# V11 UPGRADE — SCORING ENGINE CONSTANTS
# Üç kaynağın sentezi: Citadel Quant + Forensic Professor + Berkay
# V10 değerleri KORUNUYOR — V11 sidecar modüller bu sabitleri kullanır.
# ================================================================

# --- V11 FA Ağırlıkları (downside protection focus) ---
V11_FA_WEIGHTS: dict[str, float] = {
    "quality":  0.25,   # was 0.30 — slight reduction to distribute
    "value":    0.18,   # kept — Ciro/PD goes INSIDE this dimension
    "growth":   0.12,   # was 0.15 — nominal growth misleading in high-inflation
    "balance":  0.15,   # was 0.10 — CRITICAL in 37% rate environment
    "earnings": 0.13,   # was 0.10 — forensic upgrade (Beneish, CFO/NI)
    "capital":  0.10,   # was 0.09 — compounder discipline
    "moat":     0.07,   # was 0.08 — data still noisy
}

# --- V11 Overall Formül ---
V11_OVERALL_FA_WEIGHT: float = 0.58       # was 0.55 — FA daha baskın
V11_OVERALL_MOMENTUM_WEIGHT: float = 0.28  # was 0.35 — momentum azaltıldı
V11_OVERALL_RISK_FACTOR: float = 0.38      # was 0.30 — risk daha çok konuşuyor

# --- V11 Risk Caps ---
V11_RISK_CAP_NORMAL: int = -42             # was -30
V11_RISK_CAP_FATAL: int = -55              # YENİ — fatal red flag durumları

# --- V11 Non-Linear Momentum Gate (piecewise) ---
# FA < 35 → momentum neredeyse yok sayılır
# FA 35-45 → sadece timing etkisi
# FA 45-55 → kontrollü izin
# FA 55-65 → alpha üretmeye başlar
# FA >= 65 → tam teyit sinyali
V11_MOMENTUM_GATE: list[tuple[int, float]] = [
    (65, 0.95),   # FA >= 65 → tam kredi
    (55, 0.70),   # FA >= 55 → %70
    (45, 0.40),   # FA >= 45 → %40
    (35, 0.18),   # FA >= 35 → %18
    (0,  0.08),   # FA < 35  → neredeyse sıfır
]

# --- V11 Sector Thresholds (High-Rate Calibrated) ---
V11_SECTOR_THRESHOLDS: dict[str, dict[str, tuple]] = {
    "banka": {
        "pe": (10, 7, 4.5, 2.5),          # was (12, 8, 5, 3) — tighter
        "pb": (1.8, 1.2, 0.8, 0.5),       # unchanged
        "roe": (0.12, 0.18, 0.25, 0.35),   # was (0.08, 0.14, 0.20, 0.28) — raised
        "net_margin": (0.06, 0.14, 0.22, 0.32),  # raised
        "roic": None,                       # N/A for banks
        "ev_ebitda": None,
        "debt_equity": None,
        "current_ratio": None,
        "altman_z": None,
    },
    "holding": {
        "pe": (16, 10, 7, 4),              # tighter
        "pb": (1.6, 1.1, 0.8, 0.5),
        "roe": (0.06, 0.10, 0.16, 0.24),   # raised
        "net_margin": (0.04, 0.08, 0.14, 0.22),
    },
    "savunma": {
        "pe": (28, 18, 12, 7),             # tighter
        "ev_ebitda": (14, 10, 7, 4),
        "roe": (0.08, 0.14, 0.20, 0.28),   # raised
        "revenue_growth": (-0.02, 0.08, 0.18, 0.30),
    },
    "enerji": {
        "pe": (8, 5, 3, 2),               # tighter — compete with bonds
        "ev_ebitda": (8, 6, 4, 2.5),
        "roe": (0.10, 0.16, 0.22, 0.30),   # raised significantly
        "net_margin": (0.02, 0.06, 0.10, 0.18),
        "net_debt_ebitda": (3.0, 2.0, 1.2, 0.3),  # tighter
        "debt_equity": (70, 130, 220, 370),
        "altman_z": (0.8, 1.5, 2.5, 3.5),
    },
    "perakende": {
        "pe": (20, 14, 9, 5),
        "net_margin": (0.015, 0.04, 0.07, 0.12),
        "roe": (0.06, 0.12, 0.18, 0.26),   # raised
        "revenue_growth": (-0.03, 0.08, 0.15, 0.25),
    },
    "ulasim": {
        "pe": (10, 7, 4, 2.5),
        "ev_ebitda": (7, 5, 3.5, 2.5),
        "roe": (0.08, 0.14, 0.20, 0.28),   # raised
        "net_debt_ebitda": (3.0, 2.2, 1.2, 0.3),
        "debt_equity": (140, 280, 460, 650),
        "current_ratio": (0.6, 0.9, 1.2, 1.8),
        "altman_z": (0.5, 1.0, 1.8, 2.8),
    },
    "sanayi": {
        "pe": (18, 12, 7, 4),              # tighter
        "roe": (0.06, 0.12, 0.18, 0.25),   # was (0.04, 0.10, 0.16, 0.22)
        "roic": (0.04, 0.10, 0.15, 0.20),
        "net_margin": (0.03, 0.08, 0.13, 0.20),
        "ev_ebitda": (10, 7, 4, 2.5),
    },
}

# --- V11 Ciro/PD Eşikleri (Berkay Factor) ---
# score_higher ile kullanılır — yüksek = iyi (ucuz)
V11_CIRO_PD_THRESHOLDS: tuple[float, float, float, float] = (1.0, 3.0, 6.0, 10.0)

# --- V11 Ciro/PD Etiket Sistemi ---
V11_CIRO_PD_LABELS: list[tuple[float, str, str]] = [
    (10.0, "KELEPİR", "#FFD700"),       # Altın
    (6.0,  "ÇOK UCUZ", "#00e676"),      # Neon yeşil
    (4.0,  "UCUZ", "#66bb6a"),           # Yeşil
    (1.0,  "NORMAL", "#78909c"),         # Gri
    (0.0,  "PAHALI", "#ef5350"),         # Kırmızı
]

# --- V11 Fatal Risk Triggers ---
# Bu kombinasyonlardan biri varsa → risk_cap = V11_RISK_CAP_FATAL
V11_FATAL_TRIGGERS: list[str] = [
    "negative_equity",                    # equity < 0
    "fake_profit_critical",               # CFO < 0 & NI > 0 & interest_coverage < 1.5
    "debt_distress",                      # NB/FAVÖK > 4.5 & faiz karşılama < 2
    "manipulation_plus_fake",             # Beneish > -1.78 & CFO/NI < 0.5
    "dilution_plus_negative_fcf",         # dilution > %10 & FCF margin < 0
]

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

# ================================================================
# BULLWATCH ENGINE — Find low-float BIST stocks being quietly
# accumulated before the crowd notices.
#
# This is NOT a screener, NOT a buy/sell signal generator.
# It surfaces *footprints* of accumulation using existing repo
# infrastructure (data.providers + engine.technical) — nothing
# external is required.
#
# Pipeline:
#   1. score_symbol(metrics, df, ownership=None) — pure scoring
#      from already-fetched inputs. Returns BullWatchResult.
#   2. scan(symbols, ownership_lookup=None)      — orchestrates
#      data fetching + parallel scoring across a universe.
#
# Score weights (sum = 100):
#   Float Pressure         20
#   Revenue Mispricing     15
#   Silent Volume          15
#   Price Action           20
#   Compression            10
#   Ownership Intelligence 15   (skipped + reweighted if no data)
#   Fundamental Quality     5
#
# Zones:
#   EARLY      — initial footprint (compression + calm + light vol)
#   CONFIRMED  — ownership/tape aligned (high score, multiple engines)
#   CONVICTION — breakout in progress (RVOL high, float pressure firing)
# ================================================================

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

from features.bullwatch_features import (
    FLOAT_MARKET_CAP_CAP_TL,
    EXTENDED_WATCH_CAP_TL,
    LIQUIDITY_FLOOR_TL,
    PRICE_CALM_PCT,
    FLOAT_PRESSURE_STRONG, FLOAT_PRESSURE_VERY_STRONG, FLOAT_PRESSURE_EXTREME,
    RVOL_EARLY, RVOL_STRONG,
    float_market_cap, passes_float_cap, classify_universe_tier,
    revenue_to_marketcap, revenue_mispricing_tier,
    avg_traded_value_20d, passes_liquidity,
    relative_volume, float_pressure,
    price_change_5d, is_price_calm,
    atr_compression_ratio, bb_width_compression_ratio,
    detect_price_action_patterns,
    ownership_signal,
)

log = logging.getLogger("bistbull.bullwatch")

# Engine weights — must sum to 100 when ownership is available.
# When ownership has no coverage we redistribute its 15 points to
# the other engines proportionally so a stock isn't unfairly capped.
WEIGHTS_WITH_OWNERSHIP: dict[str, float] = {
    "float_pressure":      20.0,
    "revenue_mispricing":  15.0,
    "silent_volume":       15.0,
    "price_action":        20.0,
    "compression":         10.0,
    "ownership":           15.0,
    "fundamental_quality":  5.0,
}

# Fundamental quality thresholds (per spec: "avoid junk pumps")
FQ_PE_MAX: float = 15.0
FQ_ROE_MIN: float = 0.15           # 15% (we expect fraction, e.g. 0.18)
FQ_NET_DEBT_EBITDA_MAX: float = 2.0


# ----------------------------------------------------------------
# Sector mapping — yfinance returns English sector strings; we group
# them into 5 broad Turkish categories that drive the filter chips.
# yfinance "Industrials" / "Basic Materials" / "Energy" / "Utilities"
# all map to ENDÜSTRİ since they share the BullWatch "real economy"
# character. Banks/insurance/REITs collapse into FİNANSAL because the
# original BullWatch spec specifically targets non-financial micro-caps
# (Kaplamin, Kartonsan etc.).
# ----------------------------------------------------------------
_SECTOR_MAP_TR: dict[str, str] = {
    "Industrials": "Endüstri",
    "Basic Materials": "Madencilik",
    "Energy": "Endüstri",
    "Utilities": "Endüstri",
    "Technology": "Teknoloji",
    "Communication Services": "Teknoloji",
    "Healthcare": "Sağlık",
    "Consumer Cyclical": "Tüketim",
    "Consumer Defensive": "Tüketim",
    "Financial Services": "Finansal",
    "Real Estate": "Finansal",
}

# Industries that override the sector mapping — yfinance often labels
# Turkish mining/cement companies as "Basic Materials" but the more
# specific industry tells us they're really MADENCİLİK.
_INDUSTRY_HINTS_MADENCILIK: tuple[str, ...] = (
    "mining", "cement", "steel", "metals", "coal", "iron",
)


# ----------------------------------------------------------------
# Canonical BIST ticker → Turkish sector mapping.
#
# yfinance returns inconsistent sector data for BIST stocks (often empty
# or wrong). This authoritative dict, derived from KAP/Borsa İstanbul
# sector classifications, is the PRIMARY source. yfinance is fallback.
#
# Categories are intentionally coarse (7 buckets) to drive filter chips:
#   Endüstri, Madencilik, Tüketim, Teknoloji, Sağlık, Finansal, Diğer
#
# Coverage: ~340 tickers covering BullWatch universe + BIST 100/30.
# Stocks not in this dict fall back to yfinance, then to "Diğer".
# Update cadence: BIST market structure changes ~quarterly; refresh
# this dict alongside config.UNIVERSE_EXTENDED revisions.
# ----------------------------------------------------------------
_BIST_SECTOR_OVERRIDE: dict[str, str] = {
    # ── FİNANSAL ── Bankacılık
    "AKBNK": "Finansal", "GARAN": "Finansal", "ISCTR": "Finansal",
    "ISATR": "Finansal", "ISBTR": "Finansal", "HALKB": "Finansal",
    "VAKBN": "Finansal", "YKBNK": "Finansal", "TSKB": "Finansal",
    "SKBNK": "Finansal", "ICBCT": "Finansal", "ALBRK": "Finansal",
    "KLNMA": "Finansal", "QNBFB": "Finansal", "QNBTR": "Finansal",
    # ── FİNANSAL ── Sigorta
    "AKGRT": "Finansal", "AGESA": "Finansal", "ANSGR": "Finansal",
    "ANHYT": "Finansal", "RAYSG": "Finansal", "TURSG": "Finansal",
    # ── FİNANSAL ── Faktoring / Aracı / Yatırım
    "GARFA": "Finansal", "BURVA": "Finansal", "CRDFA": "Finansal",
    "GLBMD": "Finansal", "ISFIN": "Finansal", "LIDER": "Finansal",
    "LIDFA": "Finansal", "SEKFK": "Finansal", "GEDIK": "Finansal",
    "DSTKF": "Finansal", "GLCVY": "Finansal", "HEDEF": "Finansal",
    "INFO": "Finansal", "INVES": "Finansal", "ISGSY": "Finansal",
    "ISMEN": "Finansal", "OYAYO": "Finansal", "PASEU": "Finansal",
    "UNLU": "Finansal", "VBTYZ": "Finansal",
    # ── FİNANSAL ── GYO (Gayrimenkul Yatırım Ortaklıkları)
    "ALGYO": "Finansal", "AGYO": "Finansal", "ATAGY": "Finansal",
    "AKMGY": "Finansal", "DGGYO": "Finansal", "DZGYO": "Finansal",
    "EKGYO": "Finansal", "ESGYO": "Finansal", "EYGYO": "Finansal",
    "HLGYO": "Finansal", "IDGYO": "Finansal", "ISGYO": "Finansal",
    "KGYO": "Finansal", "KLGYO": "Finansal", "KRGYO": "Finansal",
    "MRGYO": "Finansal", "MSGYO": "Finansal", "NUGYO": "Finansal",
    "OZGYO": "Finansal", "PAGYO": "Finansal", "PEKGY": "Finansal",
    "RYGYO": "Finansal", "SNGYO": "Finansal", "TDGYO": "Finansal",
    "TRGYO": "Finansal", "TSGYO": "Finansal", "VKGYO": "Finansal",
    "YGYO": "Finansal", "MARTI": "Finansal", "AVHOL": "Finansal",
    # ── FİNANSAL ── Holdingler
    "SAHOL": "Finansal", "AGHOL": "Finansal", "GSDHO": "Finansal",
    "GSDDE": "Finansal", "IHLAS": "Finansal", "NTHOL": "Finansal",
    "DOHOL": "Finansal", "POLHO": "Finansal", "RALYH": "Finansal",
    "EUHOL": "Finansal", "GRTHO": "Finansal", "IEYHO": "Finansal",
    "LRSHO": "Finansal", "KLRHO": "Finansal", "VERUS": "Finansal",
    "MAGEN": "Finansal", "EUREN": "Finansal",

    # ── MADENCİLİK ── Çimento
    "ADANA": "Madencilik", "ADBGR": "Madencilik", "AFYON": "Madencilik",
    "AKCNS": "Madencilik", "BSOKE": "Madencilik", "BTCIM": "Madencilik",
    "BUCIM": "Madencilik", "CIMSA": "Madencilik", "GOLTS": "Madencilik",
    "KONYA": "Madencilik", "NUHCM": "Madencilik", "MRSHL": "Madencilik",
    "USAK": "Madencilik", "CMENT": "Madencilik", "TARKM": "Madencilik",
    # ── MADENCİLİK ── Demir/Çelik/Metal
    "BRSAN": "Madencilik", "CEMAS": "Madencilik", "CEMTS": "Madencilik",
    "EREGL": "Madencilik", "ISDMR": "Madencilik", "KRDMA": "Madencilik",
    "KRDMB": "Madencilik", "KRDMD": "Madencilik", "BMSTL": "Madencilik",
    "IZMDC": "Madencilik", "BORSK": "Madencilik", "DMSAS": "Madencilik",
    "TUCLK": "Madencilik", "BURCE": "Madencilik", "ERBOS": "Madencilik",
    # ── MADENCİLİK ── Madencilik
    "KOZAA": "Madencilik", "KOZAL": "Madencilik", "PRDGS": "Madencilik",
    "KAPLM": "Madencilik", "VISMD": "Madencilik", "IZINV": "Madencilik",
    # ── MADENCİLİK ── Kimya/Gübre/Boya
    "ALKIM": "Madencilik", "BAGFS": "Madencilik", "GUBRF": "Madencilik",
    "HEKTS": "Madencilik", "SODSN": "Madencilik", "AKSA": "Madencilik",
    "SASA": "Madencilik", "KORDS": "Madencilik", "EGEPO": "Madencilik",
    "EGGUB": "Madencilik", "DYOBY": "Madencilik", "BAYRK": "Madencilik",
    "RNPOL": "Madencilik", "POLTK": "Madencilik", "PCILT": "Madencilik",
    # ── MADENCİLİK ── Cam/Seramik
    "SISE": "Madencilik", "EGSER": "Madencilik", "KUTPO": "Madencilik",
    # ── MADENCİLİK ── Kağıt/Karton
    "KARTN": "Madencilik", "ALKA": "Madencilik", "OLMIP": "Madencilik",
    "TIRE": "Madencilik",

    # ── ENDÜSTRİ ── Otomotiv
    "FROTO": "Endüstri", "OTKAR": "Endüstri", "TMSN": "Endüstri",
    "TOASO": "Endüstri", "TTRAK": "Endüstri", "KARSN": "Endüstri",
    "DOAS": "Endüstri", "OTOKC": "Endüstri", "ASUZU": "Endüstri",
    "BRYAT": "Endüstri",
    # ── ENDÜSTRİ ── Otomotiv parça
    "PRKAB": "Endüstri", "DITAS": "Endüstri", "DOKTA": "Endüstri",
    "JANTS": "Endüstri", "BFREN": "Endüstri", "KATMR": "Endüstri",
    "PARSN": "Endüstri", "FMIZP": "Endüstri", "EGEEN": "Endüstri",
    # ── ENDÜSTRİ ── Beyaz Eşya / Dayanıklı tüketim
    "ARCLK": "Endüstri", "VESBE": "Endüstri", "VESTL": "Endüstri",
    "ALCAR": "Endüstri",
    # ── ENDÜSTRİ ── Enerji üretimi / Elektrik
    "AKENR": "Endüstri", "AKFYE": "Endüstri", "AYDEM": "Endüstri",
    "BIOEN": "Endüstri", "ENJSA": "Endüstri", "ENKAI": "Endüstri",
    "GWIND": "Endüstri", "NATEN": "Endüstri", "ODAS": "Endüstri",
    "ZOREN": "Endüstri", "IZENR": "Endüstri", "NTGAZ": "Endüstri",
    "SMRTG": "Endüstri", "ALFAS": "Endüstri", "ASTOR": "Endüstri",
    "ECOR": "Endüstri", "ENERY": "Endüstri",
    # ── ENDÜSTRİ ── Petrol/Gaz/Rafineri
    "PETKM": "Endüstri", "TUPRS": "Endüstri", "TRCAS": "Endüstri",
    "AKSA": "Endüstri",
    # ── ENDÜSTRİ ── Lojistik / Havacılık / Ulaşım
    "CLEBI": "Endüstri", "MNDRS": "Endüstri", "RYSAS": "Endüstri",
    "PGSUS": "Endüstri", "THYAO": "Endüstri", "TLMAN": "Endüstri",
    "RTALB": "Endüstri", "GRSEL": "Endüstri", "TGSAS": "Endüstri",
    "BJKAS": "Endüstri",  # spor değil; fitness sektörü için diğer altta
    # ── ENDÜSTRİ ── Telekom (çoğu rapor "Endüstri" sayar BIST'te)
    "TCELL": "Teknoloji", "TTKOM": "Teknoloji",
    # ── ENDÜSTRİ ── İnşaat / Taahhüt / Müteahhit
    "TKFEN": "Endüstri", "ANELE": "Endüstri", "DAPGM": "Endüstri",
    "EDIP": "Endüstri", "BERA": "Endüstri", "QUAGR": "Endüstri",
    # ── ENDÜSTRİ ── Makine / Üretim / Sınai
    "ALCTL": "Endüstri", "ORGE": "Endüstri", "FORMT": "Endüstri",
    "MAKIM": "Endüstri", "MAKTK": "Endüstri", "GENTS": "Endüstri",
    "ASELS": "Endüstri", "KCAER": "Endüstri", "KCHOL": "Endüstri",
    "BRLSM": "Endüstri", "SAYAS": "Endüstri", "KIMMR": "Endüstri",
    "OSTIM": "Endüstri", "IMASM": "Endüstri", "SANEL": "Endüstri",
    "MOBTL": "Endüstri", "GMTAS": "Endüstri", "GEREL": "Endüstri",
    "CUSAN": "Endüstri", "MEKAG": "Endüstri", "ARMDA": "Endüstri",
    "ALARK": "Endüstri", "DOBUR": "Endüstri", "ECILC": "Endüstri",

    # ── TÜKETİM ── Gıda / İçecek
    "BIMAS": "Tüketim", "CCOLA": "Tüketim", "KENT": "Tüketim",
    "ULKER": "Tüketim", "PNSUT": "Tüketim", "PETUN": "Tüketim",
    "TBORG": "Tüketim", "TUKAS": "Tüketim", "BANVT": "Tüketim",
    "ERSU": "Tüketim", "KRVGD": "Tüketim", "KNFRT": "Tüketim",
    "MERKO": "Tüketim", "OYLUM": "Tüketim", "PINSU": "Tüketim",
    "PNLSN": "Tüketim", "ULUUN": "Tüketim", "AVOD": "Tüketim",
    "YAYLA": "Tüketim", "TATGD": "Tüketim", "SELVA": "Tüketim",
    "BRMEN": "Tüketim", "FADE": "Tüketim", "ETILR": "Tüketim",
    "DARDL": "Tüketim", "GENIL": "Tüketim", "FRIGO": "Tüketim",
    "KRSTL": "Tüketim", "ULAS": "Tüketim", "AYCES": "Tüketim",
    # ── TÜKETİM ── Perakende / Mağazacılık
    "MGROS": "Tüketim", "CRFSA": "Tüketim", "MAVI": "Tüketim",
    "KOTON": "Tüketim", "SOKM": "Tüketim", "EBEBK": "Tüketim",
    "INGRM": "Tüketim", "VAKKO": "Tüketim", "DESA": "Tüketim",
    "TKNSA": "Tüketim", "DESPC": "Tüketim", "BIZIM": "Tüketim",
    "BIENY": "Tüketim", "BIGCH": "Tüketim",
    # ── TÜKETİM ── Tekstil / Konfeksiyon / Hazır Giyim
    "BLCYT": "Tüketim", "BOSSA": "Tüketim", "ARSAN": "Tüketim",
    "SANKO": "Tüketim", "SKTAS": "Tüketim", "ATEKS": "Tüketim",
    "YUNSA": "Tüketim", "HATEK": "Tüketim", "BRKO": "Tüketim",
    "SUNTK": "Tüketim", "DAGI": "Tüketim", "DGNMO": "Tüketim",
    "BANTL": "Tüketim", "MNDTR": "Tüketim", "ROYAL": "Tüketim",
    "SKBNK": "Tüketim",  # yo bu banka, override above wins
    # ── TÜKETİM ── Mobilya / Ev tekstili
    "YATAS": "Tüketim", "KLMSN": "Tüketim", "MARKA": "Tüketim",
    "DOCO": "Tüketim", "BERA": "Tüketim",  # bera holding
    # ── TÜKETİM ── Ayakkabı / Aksesuar
    "DESA": "Tüketim", "MEPET": "Tüketim",
    # ── TÜKETİM ── Turizm / Konaklama / Eğlence
    "MAALT": "Tüketim", "TEKTU": "Tüketim", "MARTI": "Tüketim",
    "AYCES": "Tüketim", "ULAS": "Tüketim", "PKENT": "Tüketim",
    "MEPET": "Tüketim",
    # ── TÜKETİM ── Spor & Eğlence kulüpleri (technically Communication
    # Services in yfinance, but TÜKETİM is closer for retail investors)
    "FENER": "Tüketim", "GSRAY": "Tüketim", "TSPOR": "Tüketim",
    "BJKAS": "Tüketim",

    # ── TEKNOLOJİ ── Yazılım / IT / Donanım / Bilişim
    "KAREL": "Teknoloji", "NETAS": "Teknoloji", "FORTE": "Teknoloji",
    "ARDYZ": "Teknoloji", "EDATA": "Teknoloji", "FONET": "Teknoloji",
    "KFEIN": "Teknoloji", "LINK": "Teknoloji", "PAPIL": "Teknoloji",
    "SMART": "Teknoloji", "ESCOM": "Teknoloji", "INDES": "Teknoloji",
    "KRONT": "Teknoloji", "ARENA": "Teknoloji", "MIATK": "Teknoloji",
    "PENTA": "Teknoloji", "HKTM": "Teknoloji", "FZLGY": "Teknoloji",
    "INVEO": "Teknoloji", "TERA": "Teknoloji", "DGATE": "Teknoloji",
    "HUBVC": "Teknoloji", "KCAER": "Teknoloji",  # might be industri
    "VBTYZ": "Teknoloji",  # was finansal earlier; correct is teknoloji

    # ── SAĞLIK ── İlaç / Sağlık servisi
    "ECZYT": "Sağlık", "DEVA": "Sağlık", "SELEC": "Sağlık",
    "RTALB": "Sağlık", "MPARK": "Sağlık", "MEDTR": "Sağlık",
    "LKMNH": "Sağlık", "INTEM": "Sağlık", "ALCAR": "Sağlık",
    "GENTS": "Sağlık",  # might be industri instead
}
# Resolve overrides: Some keys appear in conflicting buckets above due to
# editing by category. The LAST assignment wins (Python dict literal),
# so the explicit clean-up below pins the canonical category for
# ambiguous tickers. Keep this list short and reviewed.
_BIST_SECTOR_OVERRIDE.update({
    "TCELL": "Teknoloji",   # iletişim
    "TTKOM": "Teknoloji",   # iletişim
    "VBTYZ": "Teknoloji",   # bilişim
    "BJKAS": "Tüketim",     # spor kulübü
    "BERA": "Endüstri",     # holding
    "ALARK": "Endüstri",    # holding
    "ECILC": "Endüstri",    # holding
    "POLHO": "Finansal",    # holding
    "GENTS": "Endüstri",    # genis sınai
    "INTEM": "Endüstri",    # sınai
    "ALCAR": "Endüstri",    # eşya
    "KCAER": "Endüstri",    # makine
    "KCHOL": "Endüstri",    # holding ama sınai bazlı
    "SKBNK": "Finansal",    # banka kesin
    "DESA": "Tüketim",      # ayakkabı/giyim
    "AYCES": "Tüketim",     # turizm
    "ULAS": "Tüketim",      # turizm
    "MEPET": "Tüketim",
    "BERA": "Endüstri",
})


def map_sector_tr(sector: Optional[str], industry: Optional[str],
                  symbol: Optional[str] = None) -> str:
    """Map a yfinance sector+industry pair to a Turkish filter category.

    Resolution order (canonical first → yfinance fallback → unknown):
      1. _BIST_SECTOR_OVERRIDE[symbol]  if symbol is in our curated dict
      2. Industry-level hint (cement/steel/mining → Madencilik)
      3. yfinance sector → _SECTOR_MAP_TR
      4. "Diğer"

    Returns one of: ENDÜSTRİ, MADENCİLİK, FİNANSAL, TÜKETİM, TEKNOLOJİ,
    SAĞLIK, DİĞER. Always returns a string.
    """
    # Strip .IS / .E suffix if present (yfinance often passes "ASELS.IS")
    sym_clean = (symbol or "").upper().replace(".IS", "").replace(".E", "").strip()
    if sym_clean and sym_clean in _BIST_SECTOR_OVERRIDE:
        return _BIST_SECTOR_OVERRIDE[sym_clean]

    s = (sector or "").strip()
    ind = (industry or "").lower().strip()

    # Industry-level override: cement/mining/steel always go to MADENCİLİK
    # regardless of yfinance's broader sector tag.
    if any(h in ind for h in _INDUSTRY_HINTS_MADENCILIK):
        return "Madencilik"

    return _SECTOR_MAP_TR.get(s, "Diğer")


# ----------------------------------------------------------------
# Narrative generator — turns score+pattern+sector into 3 plain-Turkish
# sentences a non-quant retail investor can act on. Deterministic
# (template-based, no LLM): same inputs → same output every time.
# ----------------------------------------------------------------
def _build_narrative(
    score: float,
    zone: str,
    pattern: str,
    sector_tr: str,
    components: dict[str, float],
    metrics: dict[str, Any],
    data_quality: str,
) -> dict[str, str]:
    """Three short Turkish paragraphs explaining the signal in human terms.

    - whats_happening: current state, what the engines see right now
    - what_to_watch: leading indicators that confirm/invalidate the setup
    - caveats: data quality / sector mismatch / score-too-low warnings
    """
    fp = metrics.get("float_pressure")          # daily volume / floating shares
    rvol = metrics.get("rvol")                  # vs 20-day median
    atr_r = metrics.get("atr_compression")      # 1.0 = at median, <1 = compressed
    bb_r = metrics.get("bb_compression")
    pc5 = metrics.get("price_change_5d")
    patterns = metrics.get("patterns", []) or []
    # A.8: pattern labels are title-case ("Absorption", "Walk-Up Accumulation").
    # Normalize once so every downstream check is robust.
    patterns_lc = [str(p).lower() for p in patterns]
    has_absorption = any("absorption" in p for p in patterns_lc)
    has_walk_up = any("walk" in p and "up" in p for p in patterns_lc)
    has_shakeout = any("shakeout" in p for p in patterns_lc)

    # ── NE OLUYOR — describe the present state in plain Turkish ──
    parts: list[str] = []
    if pattern and pattern != "—":
        parts.append(f"Şu an **{pattern.lower()}** profili veriyor.")

    if fp is not None:
        if fp >= 0.04:
            parts.append(f"Float'ın {fp*100:.1f}%'i bugün el değiştirdi — sıkı bir alım baskısı.")
        elif fp >= 0.02:
            parts.append(f"Float'ın {fp*100:.1f}%'i el değiştirdi — orta düzey birikim.")

    if atr_r is not None and atr_r < 0.95:
        parts.append(f"Volatilite son 60 günün %{atr_r*100:.0f}'inde — sıkışma var, bir şeye hazırlanıyor olabilir.")

    if rvol is not None:
        if rvol < 0.7:
            parts.append(f"Hacim normalin {rvol:.1f}x'i — sessizce, dikkat çekmeden.")
        elif rvol > 1.5:
            parts.append(f"Hacim normalin {rvol:.1f}x'i — fark edilmeye başladı.")

    if not parts:
        parts.append("Mekanik olarak eligible ama net bir hikaye yok henüz.")

    whats_happening = " ".join(parts)

    # ── NE BEKLE — leading indicators ──
    watch_parts: list[str] = []
    if zone == "EARLY":
        watch_parts.append("Hacim patlaması (RVOL > 2x) → birikim bitti, yükseliş tetiklendi demek olur.")
        if pc5 is not None and abs(pc5) < 0.03:
            watch_parts.append("Son 5 günde fiyat %3 üzerinde kırılırsa → CONFIRMED zone'a geçer.")
    elif zone == "CONFIRMED":
        watch_parts.append("Hacim 3x'i geçer + fiyat 10 günlük yüksekten kırarsa → CONVICTION.")
    elif zone == "CONVICTION":
        watch_parts.append("Trend kırılım modunda — momentum sürerse pozisyon büyütme zamanı.")

    if has_shakeout:
        watch_parts.append("Shakeout candle yapıldı — 5 gün içinde toparlanma + hacim teyidi anahtar.")
    if has_absorption:
        watch_parts.append("Absorption pattern var — satıcı tükendiğinde fiyat yukarı sıçrayabilir.")
    if has_walk_up:
        watch_parts.append("Walk-up devam ediyor — günlük yüksekleri tutması lazım.")

    if not watch_parts:
        watch_parts.append("Şu an net bir tetik yok — hacim ve fiyat gelişimini izle.")

    what_to_watch = " ".join(watch_parts)

    # ── NEDEN ŞÜPHELİ — caveats ──
    caveat_parts: list[str] = []
    if score < 30:
        caveat_parts.append(f"Skor düşük ({score:.0f}/100) — sinyaller henüz zayıf.")
    if sector_tr == "Finansal":
        caveat_parts.append("Sigorta/finansal şirket — BullWatch'ın orjinal hedefi (endüstriyel mikro-kap) değil.")
    if data_quality == "low":
        caveat_parts.append("Veri kalitesi düşük — temel rakamlar eksik veya tutarsız.")
    elif data_quality == "medium":
        caveat_parts.append("Veri orta — bazı bilanço alanları eksik (sigorta şirketleri için yaygın).")
    if rvol is not None and rvol < 0.3:
        caveat_parts.append("Hacim çok ince — likidite sorunu olabilir, çıkış zorlaşır.")

    if not caveat_parts:
        caveat_parts.append("Açık bir kırmızı bayrak yok ama yine de pozisyon büyüklüğünü kontrollü tut.")

    caveats = " ".join(caveat_parts)

    return {
        "whats_happening": whats_happening,
        "what_to_watch": what_to_watch,
        "caveats": caveats,
    }


@dataclass
class BullWatchResult:
    symbol: str
    score: float                     # 0–100 final score
    zone: str                        # EARLY | CONFIRMED | CONVICTION
    pattern: str                     # human-readable pattern label
    components: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    data_quality: str = "high"       # high | medium | low
    eligible: bool = True            # did it pass universe filters?
    reject_reason: Optional[str] = None
    sector: Optional[str] = None     # yfinance sector (English)
    industry: Optional[str] = None   # yfinance industry (English)
    sector_tr: Optional[str] = None  # mapped Turkish category for filter chips
    narrative: dict[str, str] = field(default_factory=dict)  # {whats_happening, what_to_watch, caveats}
    # ── Phase A.6 hygiene ──
    universe_tier: Optional[str] = None  # "core" | "extended" | "institutional" | "no_data"
    # ── BullWatch v2 Addendum Phase A (optional, may be None) ──
    # All fields are dicts (or None) — backwards compatible with v1 clients
    # that don't know about them. Final narrative authority is shifted from
    # `narrative` (v1, kept for compatibility) to these structured outputs.
    playbook_sequence: Optional[dict] = None        # Module 1
    price_pinning: Optional[dict] = None            # Module 2
    move_maturity: Optional[dict] = None            # Module 6
    engine_conflict_matrix: Optional[dict] = None   # Module 9
    evidence_layer: Optional[dict] = None           # Module 10
    # ── Phase A.10 Step 2-A: data provider diagnostics (additive) ──
    # All optional, default None. Backwards compatible with v1 clients.
    data_status: Optional[str] = None        # "live"|"stale"|"partial"|"missing"
    provider_used: Optional[str] = None      # "borsapy"|"cached_borsapy"|...
    field_sources: Optional[dict] = None     # {"market_cap": "borsapy.fast_info", ...}
    missing_fields: Optional[list] = None    # ["free_float", ...]
    provider_errors: Optional[list] = None   # [{"error_type":..., "message":...}]
    override_applied: Optional[bool] = None
    override_source: Optional[str] = None
    override_fields: Optional[list] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # round numeric fields for JSON niceness
        d["score"] = round(self.score, 1)
        d["components"] = {k: round(v, 2) for k, v in self.components.items()}
        return d


# ================================================================
# ENGINE 1 — Float Pressure
# ================================================================
def _engine_float_pressure(fp: Optional[float]) -> tuple[Optional[float], list[str]]:
    """Return (sub-score in [0,1], reasons)."""
    if fp is None:
        return None, []
    reasons: list[str] = []
    if fp >= FLOAT_PRESSURE_EXTREME:
        sub = 1.0
        reasons.append(f"Extreme float pressure ({fp * 100:.1f}%)")
    elif fp >= FLOAT_PRESSURE_VERY_STRONG:
        sub = 0.85
        reasons.append(f"Very strong float pressure ({fp * 100:.1f}%)")
    elif fp >= FLOAT_PRESSURE_STRONG:
        sub = 0.65
        reasons.append(f"Strong float pressure ({fp * 100:.1f}%)")
    elif fp >= 0.01:
        sub = 0.30
    else:
        sub = 0.0
    return sub, reasons


# ================================================================
# ENGINE 2 — Revenue Mispricing
# ================================================================
def _engine_revenue_mispricing(rev_to_mc: Optional[float]) -> tuple[Optional[float], list[str]]:
    tier = revenue_mispricing_tier(rev_to_mc)
    if rev_to_mc is None:
        return None, []
    reasons: list[str] = []
    if tier == 2:
        reasons.append(f"Revenue ≥ 10× market cap ({rev_to_mc:.1f}×)")
        return 1.0, reasons
    if tier == 1:
        reasons.append(f"Revenue ≥ 5× market cap ({rev_to_mc:.1f}×)")
        return 0.7, reasons
    if rev_to_mc >= 2.0:
        return 0.3, reasons
    return 0.0, reasons


# ================================================================
# ENGINE 3 — Silent Volume (Relative Volume vs 20d)
# ================================================================
def _engine_silent_volume(rvol: Optional[float]) -> tuple[Optional[float], list[str]]:
    if rvol is None:
        return None, []
    reasons: list[str] = []
    if rvol >= RVOL_STRONG:
        reasons.append(f"Strong relative volume ({rvol:.2f}×)")
        return 1.0, reasons
    if rvol >= RVOL_EARLY:
        reasons.append(f"Early relative volume ({rvol:.2f}×)")
        return 0.65, reasons
    if rvol >= 1.1:
        return 0.25, reasons
    return 0.0, reasons


# ================================================================
# ENGINE 4 — Price Action Accumulation
# ================================================================
def _engine_price_action(patterns: dict) -> tuple[float, list[str]]:
    """Always returns a score (0 if no patterns), never None."""
    count = patterns.get("count", 0)
    if count <= 0:
        return 0.0, []
    # 1 pattern → 0.5, 2 → 0.75, 3 → 0.9, 4+ → 1.0
    score_map = {1: 0.5, 2: 0.75, 3: 0.9, 4: 1.0}
    sub = score_map.get(count, 1.0)
    labels = patterns.get("labels", [])
    reasons = [f"Price action: {label}" for label in labels]
    return sub, reasons


# ================================================================
# ENGINE 5 — Volatility Compression (ATR + BB width)
# ================================================================
def _engine_compression(atr_ratio: Optional[float],
                        bb_ratio: Optional[float]) -> tuple[Optional[float], list[str]]:
    """
    Reward ratio < 1 (current vol below 60d median).
    Use the average of available signals; if neither is available → None.
    """
    parts: list[float] = []
    reasons: list[str] = []
    for label, ratio in (("ATR", atr_ratio), ("BB width", bb_ratio)):
        if ratio is None:
            continue
        # ratio of 0.7 → score 0.6;  0.5 → score 1.0;  >= 1.0 → score 0
        if ratio >= 1.0:
            parts.append(0.0)
        else:
            parts.append(min(1.0, (1.0 - ratio) / 0.5))
            reasons.append(f"{label} compressed to {ratio:.2f}× of 60d median")
    if not parts:
        return None, []
    return sum(parts) / len(parts), reasons


# ================================================================
# ENGINE 6 — Ownership Intelligence (delegates to features module)
# ================================================================
def _engine_ownership(ownership: Optional[dict]) -> tuple[Optional[float], list[str], str]:
    sig = ownership_signal(ownership)
    score = sig["score"]
    reasons = sig["reasons"]
    coverage = sig["coverage"]
    return score, reasons, coverage


# ================================================================
# ENGINE 7 — Fundamental Quality
# ================================================================
def _engine_fundamental_quality(metrics: dict) -> tuple[Optional[float], list[str]]:
    """Avoid junk pumps. PE<15, ROE>15%, net_debt/EBITDA<2."""
    pe = metrics.get("pe")
    roe = metrics.get("roe")
    nd_ebitda = metrics.get("net_debt_ebitda")

    have = sum(1 for x in (pe, roe, nd_ebitda) if x is not None)
    if have == 0:
        return None, []

    passes = 0
    total = 0
    reasons: list[str] = []

    if pe is not None:
        total += 1
        if 0 < pe < FQ_PE_MAX:
            passes += 1
        else:
            reasons.append(f"PE outside healthy range ({pe:.1f})")

    if roe is not None:
        total += 1
        if roe >= FQ_ROE_MIN:
            passes += 1
            reasons.append(f"ROE {roe * 100:.1f}%")
        else:
            reasons.append(f"ROE only {roe * 100:.1f}%")

    if nd_ebitda is not None:
        total += 1
        # negative net debt = net cash — that's good
        if nd_ebitda < FQ_NET_DEBT_EBITDA_MAX:
            passes += 1
        else:
            reasons.append(f"Net debt / EBITDA = {nd_ebitda:.1f}")

    if total == 0:
        return None, []
    return passes / total, reasons


# ================================================================
# Zone classification — depends on which engines are firing.
# ================================================================
def _classify_zone(score: float,
                   fp: Optional[float],
                   rvol: Optional[float],
                   ownership_score: Optional[float],
                   pattern_count: int,
                   compression_score: Optional[float]) -> str:
    """
    EARLY      — quiet footprint: compression + price calm, low score.
    CONFIRMED  — multiple engines aligned (incl. tape or ownership), mid-high.
    CONVICTION — breakout in progress (high RVOL or extreme float pressure).
    """
    high_rvol = rvol is not None and rvol >= RVOL_STRONG
    extreme_fp = fp is not None and fp >= FLOAT_PRESSURE_VERY_STRONG
    if score >= 75 and (high_rvol or extreme_fp):
        return "CONVICTION"
    if score >= 60 and (
        (ownership_score is not None and ownership_score >= 0.4)
        or pattern_count >= 2
        or (rvol is not None and rvol >= RVOL_EARLY)
    ):
        return "CONFIRMED"
    return "EARLY"


def _pattern_label(active_engines: list[str], patterns: dict,
                   ownership_score: Optional[float]) -> str:
    """Build descriptive pattern string. NO buy/sell language."""
    parts: list[str] = []
    if "Float Pressure" in active_engines:
        parts.append("Float Squeeze")
    if "Compression" in active_engines and "Float Pressure" not in active_engines:
        parts.append("Volatility Compression")
    if patterns.get("labels"):
        parts.extend(patterns["labels"][:2])
    if ownership_score is not None and ownership_score >= 0.4:
        parts.append("Ownership Footprint")
    if "Silent Volume" in active_engines and "Float Pressure" not in active_engines:
        parts.append("Silent Volume Pickup")
    if "Revenue Mispricing" in active_engines and not parts:
        parts.append("Revenue Mispricing")
    if not parts:
        parts.append("Quiet Watchlist Candidate")
    # Dedupe while preserving order
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            out.append(p); seen.add(p)
    return " + ".join(out[:4])


def _diagnostic_fields(metrics: dict) -> dict:
    """Phase A.10 Step 2-A: extract diagnostic kwargs for BullWatchResult.

    Reads the `_*` and override fields stamped by data.bullwatch_cache and
    data.providers. Returns a kwargs dict ready to **-unpack into
    BullWatchResult(...). All values are optional (default None) so this
    is safe even when metrics doesn't have the diagnostic fields (e.g.
    legacy v1 callers that build metrics by hand).
    """
    return {
        "data_status": metrics.get("_data_status"),
        "provider_used": metrics.get("_provider_used"),
        "field_sources": metrics.get("_field_sources"),
        "missing_fields": metrics.get("_missing_fields"),
        "provider_errors": metrics.get("_provider_errors"),
        "override_applied": metrics.get("override_applied"),
        "override_source": metrics.get("override_source"),
        "override_fields": metrics.get("override_fields"),
    }


# ================================================================
# Main scoring entry point — pure, deterministic, no I/O.
# ================================================================
def score_symbol(metrics: dict,
                 df: Any = None,
                 ownership: Optional[dict] = None,
                 cap_tl: Optional[float] = None) -> BullWatchResult:
    """
    Score a single symbol.

    Args:
        metrics: dict in the shape of compute_metrics_v9 output. Only
                 a few keys are used: market_cap, free_float, revenue,
                 pe, roe, net_debt_ebitda, and (optional) shares.
        df:      OHLCV DataFrame (Open/High/Low/Close/Volume),
                 trailing ~80+ sessions recommended.
        ownership: Optional ownership snapshot (see features module).
        cap_tl:  Optional override for float-mcap cap (defaults to
                 FLOAT_MARKET_CAP_CAP_TL). Useful for live tuning.

    Returns BullWatchResult — always returns a result; ineligible
    symbols are flagged via `eligible=False` and `reject_reason`.
    """
    effective_cap = float(cap_tl) if cap_tl else FLOAT_MARKET_CAP_CAP_TL
    symbol = str(metrics.get("symbol") or metrics.get("ticker") or "?")
    market_cap = metrics.get("market_cap")
    free_float = metrics.get("free_float")
    revenue = metrics.get("revenue")
    shares_outstanding = metrics.get("shares")
    if shares_outstanding is None and market_cap and metrics.get("price"):
        try:
            shares_outstanding = float(market_cap) / float(metrics["price"])
        except (TypeError, ValueError, ZeroDivisionError):
            shares_outstanding = None

    fmc = float_market_cap(market_cap, free_float)
    universe_tier = classify_universe_tier(market_cap, free_float)

    # ---- Universe filters ----
    # Phase A.6: tiered visibility. Core (<3B) and Extended (3-15B) both
    # proceed to scoring; Institutional (>15B) and no_data are rejected
    # with a tier-aware reject_reason.
    if universe_tier in ("institutional", "no_data"):
        if universe_tier == "no_data":
            reject = "no float data"
        else:
            reject = (f"institutional tier — float mcap "
                      f"{fmc/1e6:.0f}M TL > {EXTENDED_WATCH_CAP_TL/1e6:.0f}M extended cap")
        return BullWatchResult(
            symbol=symbol, score=0.0, zone="EARLY",
            pattern="Outside BullWatch universe",
            eligible=False,
            reject_reason=reject,
            metrics={"float_market_cap": fmc, "market_cap": market_cap,
                     "free_float": free_float},
            data_quality="low",
            universe_tier=universe_tier,
            **_diagnostic_fields(metrics),
        )

    if not passes_liquidity(df):
        atv = avg_traded_value_20d(df)
        return BullWatchResult(
            symbol=symbol, score=0.0, zone="EARLY",
            pattern="Outside BullWatch universe",
            eligible=False,
            reject_reason=(
                "no price history" if atv is None
                else f"20d avg traded value {atv/1e6:.1f}M TL < {LIQUIDITY_FLOOR_TL/1e6:.0f}M floor"
            ),
            metrics={"float_market_cap": fmc, "avg_traded_value_20d": atv},
            data_quality="low",
            universe_tier=universe_tier,
            **_diagnostic_fields(metrics),
        )

    # ---- Feature extraction ----
    fp = float_pressure(df, shares_outstanding, free_float)
    rvol = relative_volume(df)
    rev_mc = revenue_to_marketcap(revenue, market_cap)
    if rev_mc is None and metrics.get("ciro_pd") is not None:
        rev_mc = float(metrics["ciro_pd"])
    pc5 = price_change_5d(df)
    calm = is_price_calm(df)
    atr_r = atr_compression_ratio(df)
    bb_r = bb_width_compression_ratio(df)
    patterns = detect_price_action_patterns(df)

    # ---- Engine sub-scores (each in [0,1] or None if no data) ----
    s_fp, r_fp = _engine_float_pressure(fp)
    s_rev, r_rev = _engine_revenue_mispricing(rev_mc)
    s_sv, r_sv = _engine_silent_volume(rvol)
    s_pa, r_pa = _engine_price_action(patterns)
    s_cm, r_cm = _engine_compression(atr_r, bb_r)
    s_ow, r_ow, ow_coverage = _engine_ownership(ownership)
    s_fq, r_fq = _engine_fundamental_quality(metrics)

    # Price calm acts as a small multiplier on the price-action engine —
    # we want to reward accumulation during quiet periods.
    if calm and s_pa is not None and s_pa > 0:
        s_pa = min(1.0, s_pa * 1.15)

    sub_scores = {
        "float_pressure":      s_fp,
        "revenue_mispricing":  s_rev,
        "silent_volume":       s_sv,
        "price_action":        s_pa,
        "compression":         s_cm,
        "ownership":           s_ow,
        "fundamental_quality": s_fq,
    }

    # ---- Weight redistribution: drop weights for engines with no data,
    # then renormalize so the maximum achievable score is always 100.
    available = {k: v for k, v in sub_scores.items() if v is not None}
    if not available:
        return BullWatchResult(
            symbol=symbol, score=0.0, zone="EARLY",
            pattern="Insufficient data",
            eligible=True,
            reject_reason="no engines fired",
            metrics={"float_market_cap": fmc},
            data_quality="low",
            universe_tier=universe_tier,
            **_diagnostic_fields(metrics),
        )

    weights = {k: WEIGHTS_WITH_OWNERSHIP[k] for k in available}
    weight_total = sum(weights.values())
    if weight_total <= 0:
        score = 0.0
    else:
        # Each engine contributes (sub * weight); we report contributions
        # in the original 100-point scale by renormalizing weight_total.
        norm = 100.0 / weight_total
        contributions = {k: available[k] * weights[k] * norm
                         for k in available}
        score = sum(contributions.values())

    # ---- Pattern label + zone ----
    THRESH = 0.5
    active = []
    if s_fp is not None and s_fp >= THRESH: active.append("Float Pressure")
    if s_rev is not None and s_rev >= THRESH: active.append("Revenue Mispricing")
    if s_sv is not None and s_sv >= THRESH: active.append("Silent Volume")
    if s_cm is not None and s_cm >= THRESH: active.append("Compression")
    if s_pa >= THRESH: active.append("Price Action")
    pattern = _pattern_label(active, patterns, s_ow)
    zone = _classify_zone(score, fp, rvol, s_ow, patterns.get("count", 0), s_cm)

    # ---- Reasons (de-duped, capped) ----
    reasons: list[str] = []
    for chunk in (r_fp, r_rev, r_sv, r_pa, r_cm, r_ow, r_fq):
        for r in chunk:
            if r and r not in reasons:
                reasons.append(r)
    reasons = reasons[:8]

    # ---- Data quality flag ----
    if ow_coverage == "none" and (s_fq is None or s_cm is None):
        dq = "medium"
    elif ow_coverage == "none":
        dq = "medium"
    elif sum(1 for v in sub_scores.values() if v is not None) >= 6:
        dq = "high"
    else:
        dq = "medium"

    sector = metrics.get("sector") or None
    industry = metrics.get("industry") or None
    sector_tr = map_sector_tr(sector, industry, symbol=symbol)

    metrics_dict = {
        "float_market_cap": fmc,
        "market_cap": market_cap,
        "free_float": free_float,
        "revenue_to_marketcap": rev_mc,
        "rvol": rvol,
        "float_pressure": fp,
        "price_change_5d": pc5,
        "atr_compression": atr_r,
        "bb_compression": bb_r,
        "patterns": patterns.get("labels", []),
        "ownership_coverage": ow_coverage,
    }

    score_final = round(max(0.0, min(100.0, score)), 1)

    narrative = _build_narrative(
        score=score_final,
        zone=zone,
        pattern=pattern,
        sector_tr=sector_tr,
        components={k: float(v) for k, v in sub_scores.items() if v is not None},
        metrics=metrics_dict,
        data_quality=dq,
    )

    # ────────────────────────────────────────────────────────────────
    # BullWatch v2 Addendum — Phase A modules
    #
    # Run after v1 scoring is complete. Each module is independent and
    # fail-safe: any exception is swallowed, that module's output is
    # left as None. The v1 fields (score/zone/pattern/narrative) are
    # NEVER modified — Phase A only ADDS new structured outputs.
    # ────────────────────────────────────────────────────────────────
    pinning_dict = None
    maturity_dict = None
    playbook_dict = None
    conflict_dict = None
    evidence_dict = None

    try:
        from engine.bullwatch_pinning import compute_price_pinning_score
        pinning = compute_price_pinning_score(df)
        pinning_dict = pinning.to_dict()
    except Exception as _e:
        log.debug("Phase A pinning failed for %s: %r", symbol, _e)

    try:
        from engine.bullwatch_maturity import compute_move_maturity_score
        # Phase A: ceiling/retail/gap motors don't exist yet — pass None
        maturity = compute_move_maturity_score(
            df,
            retail_heat_score=None,
            gap_trap_score=None,
            ceiling_break_result=None,
        )
        maturity_dict = maturity.to_dict()
    except Exception as _e:
        log.debug("Phase A maturity failed for %s: %r", symbol, _e)

    try:
        from engine.bullwatch_playbook import detect_playbook, SymbolState
        state = SymbolState(
            df=df,
            sub_scores={k: float(v) for k, v in sub_scores.items() if v is not None},
            metrics=metrics_dict,
            pinning=pinning_dict,
        )
        playbook = detect_playbook(state)
        playbook_dict = playbook.to_dict()
    except Exception as _e:
        log.debug("Phase A playbook failed for %s: %r", symbol, _e)

    try:
        from engine.bullwatch_conflict import resolve_conflict_matrix
        # Build the conflict matrix state from all available signals.
        # Convert sub_scores [0..1] → 0..100 scale for the rules.
        ind = (maturity_dict or {}).get("indicators") or {}

        # Phase A.7/A.8 fix: pattern labels in metrics_dict are title-case
        # (e.g. "Absorption", "Walk-Up Accumulation"). Normalize once and
        # use substring match consistent with playbook + narrative checks.
        patterns_lc = [str(p).lower()
                       for p in (metrics_dict.get("patterns") or [])]
        has_absorption = any("absorption" in p for p in patterns_lc)

        # Phase A.6: compute float_turnover_20d as a real signal (not just
        # diagnostic). cumulative 20d volume / floating shares.
        float_turnover_20d = None
        try:
            if shares_outstanding and free_float and df is not None and len(df) >= 20:
                from features.bullwatch_features import normalize_free_float
                ff_norm = normalize_free_float(free_float)
                if ff_norm:
                    floating = float(shares_outstanding) * ff_norm
                    if floating > 0:
                        cum_vol = float(df["Volume"].iloc[-20:].sum())
                        float_turnover_20d = cum_vol / floating
        except Exception:
            float_turnover_20d = None

        conflict_state = {
            "float_pressure_score": (sub_scores.get("float_pressure") or 0) * 100,
            "absorption_score": 100.0 if has_absorption
                                else (sub_scores.get("price_action") or 0) * 100,
            "price_action_score": (sub_scores.get("price_action") or 0) * 100,
            # Phase A: retail_heat/gap_trap motors don't exist yet. Pass None
            # (not 0) so conflict matrix rules requiring these signals
            # don't false-fire on default-zero values.
            "retail_heat": None,
            "gap_trap": None,
            "position_in_range": ind.get("position_in_range", 0.5),
            "move_maturity": (maturity_dict or {}).get("maturity", "UNCLEAR"),
            "price_pinning_score": (pinning_dict or {}).get("price_pinning_score") or 0,
            "playbook": (playbook_dict or {}).get("playbook", "UNCLEAR"),
            "playbook_confidence": (playbook_dict or {}).get("confidence", 0),
            # Phase A.6: turnover-based rules
            "float_turnover_20d": float_turnover_20d,
            "ret_20d": ind.get("ret_20d", 0.0),
        }
        conflict = resolve_conflict_matrix(conflict_state)
        conflict_dict = conflict.to_dict()
        # Also expose float_turnover_20d at the top of metrics for the runner
        metrics_dict["float_turnover_20d"] = float_turnover_20d
    except Exception as _e:
        log.debug("Phase A conflict matrix failed for %s: %r", symbol, _e)

    try:
        from engine.bullwatch_evidence import build_evidence_card
        evidence_dict = build_evidence_card(
            metrics=metrics_dict,
            sub_scores={k: float(v) for k, v in sub_scores.items() if v is not None},
            pinning_result=pinning_dict,
            maturity_result=maturity_dict,
            playbook_result=playbook_dict,
            conflict_result=conflict_dict,
        )
    except Exception as _e:
        log.debug("Phase A evidence failed for %s: %r", symbol, _e)

    return BullWatchResult(
        symbol=symbol,
        score=score_final,
        zone=zone,
        pattern=pattern,
        components={k: float(v) for k, v in sub_scores.items() if v is not None},
        metrics=metrics_dict,
        reasons=reasons,
        data_quality=dq,
        eligible=True,
        sector=sector,
        industry=industry,
        sector_tr=sector_tr,
        narrative=narrative,
        universe_tier=universe_tier,
        # Phase A additions (any may be None on failure)
        playbook_sequence=playbook_dict,
        price_pinning=pinning_dict,
        move_maturity=maturity_dict,
        engine_conflict_matrix=conflict_dict,
        evidence_layer=evidence_dict,
        # Phase A.10 Step 2-A: data provider diagnostics (additive)
        **_diagnostic_fields(metrics),
    )


# ================================================================
# Universe scan — orchestrates fetching + parallel scoring.
#
# Optional dependency injection for testability:
#   metrics_fn(symbol)  -> dict like compute_metrics_v9
#   history_fn(symbols) -> dict[symbol, DataFrame]
#   ownership_fn(symbol)-> dict | None
# Defaults wire to the existing repo providers.
# ================================================================
def scan(symbols: list[str],
         metrics_fn: Optional[Callable[[str], dict]] = None,
         history_fn: Optional[Callable[[list[str]], dict[str, Any]]] = None,
         ownership_fn: Optional[Callable[[str], Optional[dict]]] = None,
         max_workers: int = 8,
         min_score: float = 0.0,
         include_ineligible: bool = False,
         cap_tl: Optional[float] = None,
         progress_callback: Optional[Callable[[int, int], None]] = None,
         ) -> list[BullWatchResult]:
    """
    Run BullWatch across a universe.

    All providers are injectable so the scan can be tested with
    deterministic fakes. By default it uses the existing repo
    providers (data.providers.compute_metrics_v9 +
    engine.technical.batch_download_history).
    """
    # Resolve default providers lazily so that tests don't need to
    # have borsapy installed.
    if metrics_fn is None:
        # Use the BullWatch cache layer: Redis-backed + sanity-checked
        # + manual-override-aware. Falls through to compute_metrics_v9
        # on cache miss. Big win on warmup where most symbols are warm.
        from data.bullwatch_cache import cached_compute_metrics as _m
        metrics_fn = _m  # type: ignore
    if history_fn is None:
        from engine.technical import batch_download_history as _h
        history_fn = _h  # type: ignore
    if ownership_fn is None:
        ownership_fn = lambda _s: None  # noqa: E731

    log.info("BullWatch scan starting: %d symbols", len(symbols))

    # Bulk-fetch history in one shot — the existing helper already
    # batches via borsapy, so this is the cheap path.
    try:
        hist_map = history_fn(symbols) or {}
    except Exception as exc:
        log.warning("BullWatch: batch history fetch failed: %r", exc)
        hist_map = {}

    def _score_one(sym: str) -> Optional[BullWatchResult]:
        try:
            metrics = metrics_fn(sym)
        except Exception as exc:
            log.debug("BullWatch %s: metrics fetch failed: %r", sym, exc)
            return None
        df = hist_map.get(sym)
        try:
            ownership = ownership_fn(sym)
        except Exception:
            ownership = None
        try:
            return score_symbol(metrics, df, ownership, cap_tl=cap_tl)
        except Exception as exc:
            log.warning("BullWatch %s: scoring failed: %r", sym, exc)
            return None

    results: list[BullWatchResult] = []
    total = len(symbols)
    processed = 0
    # Per-symbol timeout: each future must complete within this budget.
    # yfinance occasionally returns 0-byte responses that hang for ~90s.
    # If 2-3 stragglers do this, the whole scan stalls forever via the
    # default `with ThreadPoolExecutor` block (which waits on shutdown).
    # We use as_completed(timeout=...) measured from start-of-loop, and
    # whichever futures haven't returned by then are cancelled.
    SCAN_TIMEOUT_SEC = 240  # 4 minutes total budget for the scan loop
    pool = ThreadPoolExecutor(max_workers=max_workers)
    futures = {pool.submit(_score_one, s): s for s in symbols}
    try:
        for fut in as_completed(futures, timeout=SCAN_TIMEOUT_SEC):
            processed += 1
            if progress_callback is not None:
                try:
                    progress_callback(processed, total)
                except Exception:
                    pass  # callback failure must never break the scan
            try:
                r = fut.result(timeout=1)  # already done — instant
            except Exception as exc:
                log.debug("BullWatch future failed: %r", exc)
                continue
            if r is None:
                continue
            if not r.eligible and not include_ineligible:
                continue
            if r.score < min_score and r.eligible:
                # Eligible but low-scoring — keep only if explicitly asked
                if min_score > 0:
                    continue
            results.append(r)
    except TimeoutError:
        # Scan budget exceeded — yfinance has stragglers. Take what we got
        # and cancel everything else. This is the critical guarantee:
        # scan() ALWAYS returns within SCAN_TIMEOUT_SEC, never hangs.
        unfinished = [f for f in futures if not f.done()]
        log.warning(
            "BullWatch scan: %d/%d futures done, %d cancelled after %ds budget",
            processed, total, len(unfinished), SCAN_TIMEOUT_SEC,
        )
        for f in unfinished:
            f.cancel()
    finally:
        # Don't wait for stragglers; cancel and move on.
        pool.shutdown(wait=False, cancel_futures=True)

    # Sort: eligible by score desc, ineligible last
    results.sort(key=lambda r: (not r.eligible, -r.score))
    log.info("BullWatch scan done: %d eligible, top score %.1f",
             sum(1 for r in results if r.eligible),
             results[0].score if results and results[0].eligible else 0.0)
    return results

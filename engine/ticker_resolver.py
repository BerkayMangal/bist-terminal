# ================================================================
# BISTBULL TERMINAL — TICKER RESOLVER
# engine/ticker_resolver.py
#
# Resolves natural Turkish company names to BIST ticker codes.
# Two layers: instant local lookup, then optional AI fallback.
# ================================================================
from __future__ import annotations
import logging
log = logging.getLogger("bistbull.ticker_resolver")

# ── Alias map: lowercase variant → ticker ────────────────────────
_ALIASES: dict[str, str] = {
    # Bankalar
    "garanti": "GARAN", "garanti bankası": "GARAN", "garanti bankasi": "GARAN",
    "akbank": "AKBNK", "iş bankası": "ISCTR", "is bankasi": "ISCTR", "isbank": "ISCTR",
    "yapı kredi": "YKBNK", "yapi kredi": "YKBNK", "yapıkredi": "YKBNK",
    "halkbank": "HALKB", "halk bankası": "HALKB", "vakıfbank": "VAKBN", "vakifbank": "VAKBN",
    "tskb": "TSKB", "şekerbank": "SKBNK", "albaraka": "ALBRK",
    # Havacılık / Savunma
    "thy": "THYAO", "türk hava yolları": "THYAO", "turk hava yollari": "THYAO",
    "pegasus": "PGSUS", "aselsan": "ASELS",
    # Otomotiv
    "tofaş": "TOASO", "tofas": "TOASO", "ford otosan": "FROTO", "ford": "FROTO", "froto": "FROTO",
    "otokar": "OTKAR",
    # Holding
    "koç holding": "KCHOL", "koc holding": "KCHOL", "koç": "KCHOL", "koc": "KCHOL",
    "sabancı": "SAHOL", "sabanci": "SAHOL", "sabancı holding": "SAHOL",
    "doğan holding": "DOHOL", "dogan holding": "DOHOL", "alarko": "ALARK",
    "anadolu grubu": "AGHOL",
    # Perakende / FMCG
    "bim": "BIMAS", "migros": "MGROS", "şok": "SOKM", "sok": "SOKM",
    "ülker": "ULKER", "ulker": "ULKER", "coca cola": "CCOLA", "ccola": "CCOLA",
    "mavi": "MAVI", "pınar süt": "PNSUT", "pinar sut": "PNSUT",
    # Sanayi
    "ereğli": "EREGL", "eregli": "EREGL", "ereğli demir": "EREGL",
    "kardemir": "KRDMD", "tüpraş": "TUPRS", "tupras": "TUPRS",
    "şişecam": "SISE", "sisecam": "SISE", "sise": "SISE",
    "arçelik": "ARCLK", "arcelik": "ARCLK", "vestel": "VESTL",
    "petkim": "PETKM", "enka": "ENKAI",
    "sasa": "SASA", "çimsa": "CIMSA", "cimsa": "CIMSA",
    "brisa": "BRSAN", "kordsa": "KORDS", "sarkuysan": "SARKY",
    # Telekom / Teknoloji
    "turkcell": "TCELL", "türk telekom": "TTKOM", "turk telekom": "TTKOM",
    "logo": "LOGO", "netaş": "NETAS", "netas": "NETAS", "kron": "KRONT", "indeks": "INDES",
    # Enerji
    "aksa enerji": "AKSEN", "enerjisa": "ENJSA", "zorlu enerji": "ZOREN",
    "aydem": "AYDEM", "odaş": "ODAS", "odas": "ODAS",
    # Gayrimenkul
    "emlak gyo": "EKGYO", "iş gyo": "ISGYO", "halk gyo": "HLGYO",
    # İnşaat
    "tekfen": "TKFEN", "kalyon": "KLGYO",
    # Gıda / Tarım
    "gübre fabrikaları": "GUBRF", "gubrf": "GUBRF",
    "hektaş": "HEKTS", "hektas": "HEKTS",
    # Diğer
    "tav": "TAVHL", "çelebi": "CLEBI", "pegasus": "PGSUS",
    "türk traktör": "TTRAK", "turk traktor": "TTRAK",
    "doğuş otomotiv": "DOAS", "dogus otomotiv": "DOAS",
    "aygaz": "AYGAZ", "bera": "BERA", "astor": "ASTOR",
}

# Also add self-references: ticker → ticker
from config import UNIVERSE
for _t in UNIVERSE:
    _ALIASES[_t.lower()] = _t
    _ALIASES[_t.lower().replace(".is", "")] = _t


def resolve_ticker(text: str) -> str | None:
    """Resolve a single natural text to a BIST ticker. Returns None if not found."""
    if not text:
        return None
    clean = text.strip().lower().replace(".is", "")
    
    # Direct match
    if clean in _ALIASES:
        return _ALIASES[clean]
    
    # Try uppercase (already a ticker?)
    upper = clean.upper()
    if upper in {t for t in UNIVERSE}:
        return upper
    
    # Fuzzy: check if text is substring of any alias
    for alias, ticker in _ALIASES.items():
        if clean in alias or alias in clean:
            return ticker
    
    return None


def resolve_multiple(text: str) -> list[str]:
    """Resolve multiple tickers from a text like 'tofaş ereğli' or 'TOASO vs FROTO'."""
    if not text:
        return []
    
    # Split on common separators
    parts = text.replace(" vs ", " ").replace(" ve ", " ").replace(",", " ").replace("/", " ").split()
    tickers = []
    seen = set()
    
    for part in parts:
        t = resolve_ticker(part)
        if t and t not in seen:
            tickers.append(t)
            seen.add(t)
    
    # If single-word didn't work, try the full text
    if not tickers:
        t = resolve_ticker(text)
        if t:
            tickers.append(t)
    
    return tickers


def search_suggestions(query: str, limit: int = 6) -> list[dict]:
    """Return matching tickers for autocomplete dropdown."""
    if not query or len(query) < 2:
        return []
    
    q = query.strip().lower()
    results = []
    seen = set()
    
    # First: exact ticker prefix
    for t in UNIVERSE:
        if t.lower().startswith(q) and t not in seen:
            results.append({"ticker": t, "match": "ticker"})
            seen.add(t)
    
    # Second: alias matches
    for alias, ticker in _ALIASES.items():
        if q in alias and ticker not in seen:
            results.append({"ticker": ticker, "match": alias})
            seen.add(ticker)
    
    return results[:limit]

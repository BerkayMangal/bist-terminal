"""Holding-group affiliations for BIST tickers.

When a "tahtacı" (operator) crew runs one stock in a holding family,
they often warm up adjacent names in the same family. Tracking group-
level CONVICTION density gives early signal on the next rotation.

This map is intentionally conservative — only well-known public
groupings where the family connection is liquid common knowledge.
"""
from __future__ import annotations
from typing import Dict, Iterable, Optional, Set

# Group name -> set of tickers (without .IS suffix).
HOLDING_GROUPS: Dict[str, Set[str]] = {
    "yildiz": {"BIMAS", "ULKER", "TBORG"},
    "koc": {"KCHOL", "ARCLK", "FROTO", "TUPRS", "TOASO", "AYGAZ",
            "MGROS", "OTKAR", "TATGD", "YKBNK"},
    "sabanci": {"SAHOL", "AKBNK", "AKSA", "AKSEN", "AKCNS", "BRSAN",
                "ENJSA", "CIMSA", "KORDS"},
    "eczacibasi": {"ECILC", "ESEN", "ECZYT", "IPEKE"},
    "dogan": {"DOHOL", "DGGYO", "DOAS", "HURGZ"},
    "anadolu": {"AGHOL", "AEFES", "CCOLA", "ANSGR", "ANHYT"},
    "bera": {"BERA", "BJKAS"},
    "cukurova": {"CUKUR", "BTCIM", "EDIP", "YATAS"},
    "yapikredi": {"YKBNK", "KCHOL"},
    "fiba": {"FIBAH", "AKFGY", "FIBAB"},
    "ihlas": {"IHLAS", "IHGZT", "IHEVA", "IHLGM"},
    "tav": {"TAVHL", "ASTOR"},
    "alarko": {"ALARK", "ALCAR"},
    "borusan": {"BRSAN", "BRYAT", "BORLS"},
    "akkok": {"AKSA", "AKMGY", "AKKIM"},
    "cengiz": {"EUREN", "MPARK"},
    "tekfen": {"TKFEN"},
    "kibar": {"KARSN", "KATMR"},
    "yasar": {"DYOBY", "PNSUT", "VKING"},
}

# Pre-build reverse index ticker -> group name for O(1) lookup.
_TICKER_TO_GROUP: Dict[str, str] = {}
for _group_name, _members in HOLDING_GROUPS.items():
    for _t in _members:
        _TICKER_TO_GROUP[_t.upper()] = _group_name


def _norm(ticker: str) -> str:
    if not ticker:
        return ""
    return ticker.upper().replace(".IS", "").strip()


def get_group(ticker: str) -> Optional[str]:
    """Return the holding-group name for `ticker`, or None."""
    return _TICKER_TO_GROUP.get(_norm(ticker))


def get_peers(ticker: str) -> Set[str]:
    """Return ticker's group peers (excluding the ticker itself).
    Empty set if ticker is not in any tracked group."""
    g = get_group(ticker)
    if not g:
        return set()
    peers = set(HOLDING_GROUPS[g]) - {_norm(ticker)}
    return peers


def all_group_tickers() -> Iterable[str]:
    """Flat iterable of every ticker that appears in any group."""
    return iter(_TICKER_TO_GROUP.keys())

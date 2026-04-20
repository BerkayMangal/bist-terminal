"""Sector taxonomy for BIST30 symbols (Phase 4 FAZ 4.2 / Q1).

Reviewer decision: flat Turkish-language sector mapping, NOT a GICS
hierarchy. 30 symbols × 14 sectors is granular enough for per-sector
calibration without splitting sample sizes below the n=20 threshold.

SECTOR_MAP is the single source of truth. New symbols must be added
here manually — tests/test_phase4.py::TestSectorCoverage enforces
that every symbol in data/universe_history.csv has a sector entry.

Sector values are free-form Turkish strings (not an enum); we keep
them that way so human operators reading the calibration reports can
read "Banka" directly without looking up an enum code. The 14 sectors
match the Phase 3b deep_events.csv field values, so calibration
output back-references the training data cleanly.
"""

from __future__ import annotations

from typing import Optional


# Canonical BIST30 sector mapping, sourced from reviewer spec
# (Phase 4.1 continuation doc) and cross-referenced against
# /mnt/user-data/uploads/deep_events.csv sector column.
SECTOR_MAP: dict[str, str] = {
    # Banka (6)
    "AKBNK": "Banka",
    "GARAN": "Banka",
    "ISCTR": "Banka",
    "YKBNK": "Banka",
    "HALKB": "Banka",
    "VAKBN": "Banka",
    # Holding (4)
    "KCHOL": "Holding",
    "SAHOL": "Holding",
    "ENKAI": "Holding",
    "OYAKC": "Holding",
    # Savunma (2)
    "ASELS": "Savunma",
    "ASTOR": "Savunma",
    # Kimya (2)
    "HEKTS": "Kimya",
    "SASA":  "Kimya",
    # Enerji (3)
    "TUPRS": "Enerji",
    "PETKM": "Enerji",
    "AKSEN": "Enerji",
    # Perakende (2)
    "BIMAS": "Perakende",
    "MGROS": "Perakende",
    # Gıda (1)
    "ULKER": "Gıda",
    # Sanayi (4)
    "ARCLK": "Sanayi",
    "FROTO": "Sanayi",
    "TOASO": "Sanayi",
    "SISE":  "Sanayi",
    # Demir-Çelik (2)
    "EREGL": "Demir-Çelik",
    "KRDMD": "Demir-Çelik",
    # Madencilik (2)
    "KOZAL": "Madencilik",
    "KOZAA": "Madencilik",
    # GYO (1)
    "EKGYO": "GYO",
    # Havayolu (2)
    "THYAO": "Havayolu",
    "PGSUS": "Havayolu",
    # Ulaşım (1)
    "TAVHL": "Ulaşım",
    # Telekom (2)
    "TCELL": "Telekom",
    "TTKOM": "Telekom",
}

# The set of 14 canonical sector labels -- also matches the deep_events.csv
# training data's `sector` column exactly.
VALID_SECTORS: frozenset[str] = frozenset(SECTOR_MAP.values())


def get_sector(symbol: str) -> Optional[str]:
    """Return the sector for a symbol, or None if unknown.

    Case-insensitive. Unknown symbols return None rather than raising so
    the labeler / validator / calibration pipeline can continue; callers
    that require a sector should check for None explicitly.
    """
    if not symbol:
        return None
    return SECTOR_MAP.get(symbol.upper())


def symbols_in_sector(sector: str) -> list[str]:
    """Return sorted list of symbols in the given sector. [] if unknown sector."""
    return sorted(sym for sym, sec in SECTOR_MAP.items() if sec == sector)

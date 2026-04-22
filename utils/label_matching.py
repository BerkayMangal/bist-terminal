"""Turkish-diacritic-aware fuzzy label matching for KAP financial statements.

Context (Phase 4.7 v2, post Colab ROUND-A diagnosis):
  Colab FA backfill produced only 3 of 25 expected metrics because
  borsapy's DataFrame.index labels didn't match the candidate strings
  hardcoded in scripts/ingest_fa_for_calibration.py. Many mismatches
  come from Turkish-locale differences:
    - 'İşletme Faaliyetlerinden...' vs 'Isletme Faaliyetlerinden...'
    - 'Özkaynaklar' vs 'Ozkaynaklar' vs 'OZKAYNAKLAR'
    - Extra whitespace, trailing punctuation, newline wraps

  This module provides a normalize_label() that strips all that so
  pandas index matching becomes robust, and pick_label() that tries
  an ordered candidate list with scoring fallback.

  Zero dependency on borsapy — purely string manipulation. Testable
  without any network or data package.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Iterable


# Turkish-specific character mapping. unicodedata NFD + strip-combining
# handles most diacritics, but Turkish 'ı' (dotless i) and 'İ' (dotted I)
# are edge cases that don't fold through NFD cleanly — we handle them
# explicitly.
_TURKISH_FOLD = str.maketrans({
    "İ": "I", "ı": "i", "Ğ": "G", "ğ": "g", "Ş": "S", "ş": "s",
    "Ç": "C", "ç": "c", "Ö": "O", "ö": "o", "Ü": "U", "ü": "u",
})


def normalize_label(s: object) -> str:
    """Canonicalize a label for comparison.

    1. None → empty string (so a missing index entry compares equal to
       a candidate only if the candidate is also empty, which never happens).
    2. Cast to str.
    3. Apply Turkish-specific character fold (İ→I, ı→i, etc.).
    4. Unicode NFD decomposition then strip combining marks (catches
       'é' type accents if they slip in).
    5. Strip all punctuation including parens and commas.
    6. Collapse all runs of whitespace to a single space.
    7. lowercase and trim.

    This is a ONE-WAY normalization for equality comparison only.
    """
    if s is None:
        return ""
    text = str(s)
    # Turkish explicit fold first (before NFD, since İ/ı need custom handling)
    text = text.translate(_TURKISH_FOLD)
    # General Unicode NFD + combining-mark strip
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Strip punctuation (keep letters, digits, whitespace only)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    # Collapse whitespace (incl. newlines, tabs)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def pick_label(
    available_labels: Iterable[str],
    candidates: list[str],
    allow_substring: bool = True,
) -> Optional[str]:
    """Find the first available label matching any of the candidates.

    Order of matching (first match wins):
      1. Exact normalized match (normalize_label(cand) == normalize_label(label))
      2. If allow_substring: normalized candidate is a substring of the
         normalized label (so 'Donem Net Kari' matches
         'Donem Net Kari Zarari' or 'Ana Ortakliga Ait Donem Net Kari').

    Returns the ORIGINAL (un-normalized) label from available_labels so
    the caller can look it up via df.loc[returned_label, col].

    Returns None if no candidate matches any available label.
    """
    # Normalize once
    avail_list = list(available_labels)
    avail_norm = [(original, normalize_label(original)) for original in avail_list]

    # Pass 1: exact normalized match, in candidate order
    for cand in candidates:
        cand_norm = normalize_label(cand)
        if not cand_norm:
            continue
        for original, norm in avail_norm:
            if norm == cand_norm:
                return original

    # Pass 2: substring match (candidate appears within a longer label)
    if allow_substring:
        for cand in candidates:
            cand_norm = normalize_label(cand)
            if not cand_norm:
                continue
            for original, norm in avail_norm:
                # Guard against trivial substring noise — require at least
                # 4 chars of cand to be present, so 'Net' doesn't match
                # every label containing that 3-letter word.
                if len(cand_norm) >= 4 and cand_norm in norm:
                    return original

    return None


def pick_value(
    df,
    col,
    candidates: list[str],
    allow_substring: bool = True,
) -> Optional[float]:
    """Look up a numeric cell in a pandas DataFrame by fuzzy row-label match.

    df     : pandas.DataFrame with string index (KAP statement shape)
    col    : column label (typically a quarter-end date string)
    candidates : ordered list of label candidates to try

    Returns a float, or None if no candidate matches OR the cell is
    NaN/None/non-numeric.
    """
    if df is None or getattr(df, "empty", True):
        return None
    try:
        label = pick_label(df.index, candidates, allow_substring=allow_substring)
    except Exception:
        return None
    if label is None:
        return None
    try:
        val = df.loc[label, col]
    except (KeyError, TypeError):
        return None
    # Handle pandas Series (happens if index has duplicates) — take first
    try:
        import pandas as _pd
        if isinstance(val, _pd.Series):
            val = val.iloc[0] if len(val) else None
    except ImportError:
        pass
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    # NaN check without importing pandas just for isna
    if f != f:
        return None
    return f

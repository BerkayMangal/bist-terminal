"""Tests for utils/label_matching.py — Turkish-diacritic-aware fuzzy
label matching for KAP financial statements.

Context (Phase 4.7 v2): Colab ROUND A produced only 3/25 metrics
because candidate labels had small diacritic/whitespace differences
from what borsapy returns. This module provides robust normalization.
"""

from __future__ import annotations

import pytest


class TestNormalizeLabel:
    """normalize_label() canonicalizes for comparison."""

    def test_turkish_diacritic_fold(self):
        from utils.label_matching import normalize_label
        assert normalize_label("Özkaynaklar") == "ozkaynaklar"
        assert normalize_label("İşletme") == "isletme"
        assert normalize_label("ÇEYREK") == "ceyrek"
        assert normalize_label("Ğğ") == "gg"
        assert normalize_label("Şş") == "ss"
        assert normalize_label("Üü") == "uu"

    def test_dotless_i_folded(self):
        """Turkish ı (dotless i) must fold to 'i', not stay as 'ı'."""
        from utils.label_matching import normalize_label
        assert normalize_label("Hasılat") == "hasilat"
        assert normalize_label("Karı") == "kari"
        assert normalize_label("ı" * 5) == "iiiii"

    def test_dotted_capital_i_folded(self):
        """Turkish İ (dotted capital I) must fold to 'I' then 'i'."""
        from utils.label_matching import normalize_label
        assert normalize_label("İşletme Faaliyetlerinden") == "isletme faaliyetlerinden"
        assert normalize_label("İSTANBUL") == "istanbul"

    def test_whitespace_collapse(self):
        from utils.label_matching import normalize_label
        assert normalize_label("Ana   Ortaklığa") == "ana ortakliga"
        assert normalize_label("\tAna\nOrtaklığa\r\n") == "ana ortakliga"
        assert normalize_label("  Özkaynaklar  ") == "ozkaynaklar"

    def test_punctuation_stripped(self):
        from utils.label_matching import normalize_label
        # Slash, comma, period, parens all go away
        assert normalize_label("Dönem Net Karı/Zararı") == "donem net kari zarari"
        assert normalize_label("Kar (Zarar)") == "kar zarar"
        assert normalize_label("Toplam Borç, Uzun Vade") == "toplam borc uzun vade"

    def test_none_returns_empty(self):
        from utils.label_matching import normalize_label
        assert normalize_label(None) == ""

    def test_non_string_coerced(self):
        from utils.label_matching import normalize_label
        # Float / int shouldn't crash
        assert normalize_label(42) == "42"
        assert normalize_label(3.14) == "3 14"  # dot becomes space

    def test_empty_string_returns_empty(self):
        from utils.label_matching import normalize_label
        assert normalize_label("") == ""
        assert normalize_label("   ") == ""

    def test_case_insensitive(self):
        from utils.label_matching import normalize_label
        assert normalize_label("HASILAT") == normalize_label("hasılat") \
            == normalize_label("Hasılat") == "hasilat"


class TestPickLabel:
    """pick_label() selects the first candidate matching any available
    index label, returning the ORIGINAL (un-normalized) label."""

    def test_exact_match_returns_original(self):
        from utils.label_matching import pick_label
        labels = ["Dönem Net Karı/Zararı", "Özkaynaklar"]
        # Normalized match, returns original
        r = pick_label(labels, ["Donem Net Kari Zarari"])
        assert r == "Dönem Net Karı/Zararı"

    def test_first_candidate_wins(self):
        """When multiple candidates would match, the FIRST candidate
        that matches something wins (candidate order is preference)."""
        from utils.label_matching import pick_label
        labels = ["Dönem Net Karı", "Ana Ortaklığa Ait Dönem Net Karı"]
        # 'Dönem Net Karı' is the first candidate; it exact-matches
        # labels[0] directly
        r = pick_label(labels, ["Dönem Net Karı", "Ana Ortaklığa Ait"])
        assert r == "Dönem Net Karı"

    def test_substring_match_fallback(self):
        """When no exact match, substring match should find labels
        containing the candidate."""
        from utils.label_matching import pick_label
        labels = ["Ana Ortaklığa Ait Dönem Net Karı"]
        r = pick_label(labels, ["Dönem Net Karı"])
        assert r == "Ana Ortaklığa Ait Dönem Net Karı"

    def test_substring_needs_min_length(self):
        """Very short candidates (<4 chars) must NOT match via substring
        to avoid accidentally matching 'Net' in every label containing
        that common word."""
        from utils.label_matching import pick_label
        labels = ["Net Dönem Karı", "Toplam Net Satışlar"]
        # 'Net' is only 3 chars; shouldn't substring-match
        assert pick_label(labels, ["Net"]) is None

    def test_substring_disabled(self):
        from utils.label_matching import pick_label
        labels = ["Ana Ortaklığa Ait Dönem Net Karı"]
        # With substring=False, 'Dönem Net Karı' shouldn't match the
        # longer label
        assert pick_label(labels, ["Dönem Net Karı"], allow_substring=False) is None

    def test_no_match_returns_none(self):
        from utils.label_matching import pick_label
        assert pick_label(["Özkaynaklar"], ["Nonexistent Label"]) is None

    def test_empty_candidates_returns_none(self):
        from utils.label_matching import pick_label
        assert pick_label(["Özkaynaklar"], []) is None

    def test_empty_available_returns_none(self):
        from utils.label_matching import pick_label
        assert pick_label([], ["Özkaynaklar"]) is None

    def test_whitespace_insensitive(self):
        """Leading/trailing whitespace in either direction shouldn't
        block a match."""
        from utils.label_matching import pick_label
        labels = ["  Özkaynaklar  "]
        assert pick_label(labels, ["Özkaynaklar"]) == "  Özkaynaklar  "

    def test_real_kap_examples(self):
        """Representative KAP label examples from THYAO's 2023Q3 filing."""
        from utils.label_matching import pick_label
        # These are the actual labels borsapy might return (with
        # variation across quarters / symbols)
        labels = [
            "Hasılat",
            "Satışların Maliyeti (-)",
            "Brüt Kar/(Zarar)",
            "Esas Faaliyet Karı/Zararı",
            "Finansman Giderleri (-)",
            "Dönem Net Karı/Zararı",
            "Ana Ortaklık Paylarına Düşen Dönem Karı/Zararı",
        ]
        assert pick_label(labels, ["Hasılat"]) == "Hasılat"
        assert pick_label(labels, ["Brüt Kar"]) is not None  # substring
        assert pick_label(labels, ["Esas Faaliyet Karı"]) is not None
        # 'Finansman Giderleri' matches via substring (the (-) suffix
        # is stripped by punctuation rule)
        assert pick_label(labels, ["Finansman Giderleri"]) == "Finansman Giderleri (-)"


class TestPickValue:
    """pick_value() combines pick_label with DataFrame cell lookup."""

    def test_pandas_unavailable_handled(self):
        """If pandas is unavailable OR df is None, returns None."""
        from utils.label_matching import pick_value
        assert pick_value(None, "2023-12-31", ["any"]) is None

    def test_real_dataframe_lookup(self):
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_value
        df = pd.DataFrame({
            "2023-12-31": [100.0, 200.0, 300.0],
            "2023-09-30": [90.0, 190.0, 290.0],
        }, index=["Hasılat", "Özkaynaklar", "Toplam Varlıklar"])
        # Exact match
        assert pick_value(df, "2023-12-31", ["Hasılat"]) == 100.0
        # Normalized match
        assert pick_value(df, "2023-12-31", ["HASILAT"]) == 100.0
        # Substring of a longer label
        df2 = pd.DataFrame({"2023-12-31": [500.0]},
                            index=["Ana Ortaklığa Ait Dönem Net Karı"])
        assert pick_value(df2, "2023-12-31", ["Dönem Net Karı"]) == 500.0

    def test_nan_returns_none(self):
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_value
        df = pd.DataFrame({"2023-12-31": [float("nan")]}, index=["Hasılat"])
        assert pick_value(df, "2023-12-31", ["Hasılat"]) is None

    def test_empty_dataframe_returns_none(self):
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_value
        df = pd.DataFrame()
        assert pick_value(df, "2023-12-31", ["Hasılat"]) is None

    def test_missing_column_returns_none(self):
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_value
        df = pd.DataFrame({"2023-12-31": [100.0]}, index=["Hasılat"])
        assert pick_value(df, "2020-01-01", ["Hasılat"]) is None

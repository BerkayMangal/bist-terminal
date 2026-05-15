# ================================================================
# tests/test_bullalfa_ranking.py
#
# Spec §22 coverage:
#   - HIZLI confidence 80 → opportunity 80
#   - SWING confidence 70 → opportunity 70
#   - TOPLANIYOR strength 90 → opportunity 70 (capped)
#   - SAKİN quality 90 → opportunity 18 (capped at 20% × quality)
#   - UZAK DUR → opportunity 5
#   - Sort stability across recomputes
# ================================================================

from __future__ import annotations

import random

import pytest

from engine.bullalfa_params import (
    OPPORTUNITY_SAKIN_CAP,
    OPPORTUNITY_SAKIN_MULT,
    OPPORTUNITY_TOPLANIYOR_CAP,
    OPPORTUNITY_UZAK_DUR_FIXED,
    SECTOR_CONCENTRATION_THRESHOLD,
)
from features.bullalfa_ranking import (
    ALL_MODES,
    opportunity_score,
    sector_concentration_alert,
)


# ================================================================
# Per-mode opportunity score
# ================================================================

class TestOpportunityScore:

    @pytest.mark.parametrize(
        "mode,confidence,expected",
        [
            ("HIZLI",    80, 80),
            ("HIZLI",    50, 50),
            ("HIZLI",   100, 100),
            ("SWING",    70, 70),
            ("SWING",    25, 25),
            ("POZİSYON", 65, 65),
        ],
    )
    def test_actionable_modes_pass_confidence_through(self, mode, confidence, expected):
        v = opportunity_score(mode=mode, confidence_final=confidence)
        assert v == expected

    @pytest.mark.parametrize("mode", ["HIZLI", "SWING", "POZİSYON"])
    def test_actionable_with_missing_confidence_returns_zero(self, mode):
        # Missing input cannot REMOVE the stock (§17 universe rule);
        # collapse to bottom of sort instead.
        assert opportunity_score(mode=mode, confidence_final=None) == 0

    def test_toplaniyor_capped_at_70(self):
        assert opportunity_score(
            mode="TOPLANIYOR", accumulation_strength=90,
        ) == OPPORTUNITY_TOPLANIYOR_CAP
        assert opportunity_score(
            mode="TOPLANIYOR", accumulation_strength=100,
        ) == OPPORTUNITY_TOPLANIYOR_CAP

    def test_toplaniyor_below_cap_passes_through(self):
        assert opportunity_score(
            mode="TOPLANIYOR", accumulation_strength=55,
        ) == 55
        assert opportunity_score(
            mode="TOPLANIYOR", accumulation_strength=20,
        ) == 20

    def test_toplaniyor_with_missing_strength_is_zero(self):
        assert opportunity_score(mode="TOPLANIYOR", accumulation_strength=None) == 0

    def test_uzak_dur_is_constant(self):
        assert opportunity_score(mode="UZAK DUR") == OPPORTUNITY_UZAK_DUR_FIXED

    def test_sakin_quality_90_yields_18(self):
        # 90 × 0.20 = 18 (under the 20 cap)
        assert opportunity_score(mode="SAKİN", quality_score=90) == 18

    def test_sakin_capped_at_20(self):
        # 100 × 0.20 = 20, equals cap
        assert opportunity_score(mode="SAKİN", quality_score=100) == OPPORTUNITY_SAKIN_CAP
        # 200 × 0.20 = 40, but cap = 20
        assert opportunity_score(mode="SAKİN", quality_score=200) == OPPORTUNITY_SAKIN_CAP

    def test_sakin_with_missing_quality_is_zero(self):
        assert opportunity_score(mode="SAKİN", quality_score=None) == 0

    def test_sakin_low_quality_floors_at_zero(self):
        assert opportunity_score(mode="SAKİN", quality_score=0) == 0
        assert opportunity_score(mode="SAKİN", quality_score=-50) == 0

    def test_unknown_mode_returns_zero(self):
        assert opportunity_score(mode="GIBBERISH") == 0

    def test_returns_integer(self):
        v = opportunity_score(mode="HIZLI", confidence_final=72.4)
        assert isinstance(v, int)
        assert v == 72
        v = opportunity_score(mode="HIZLI", confidence_final=72.6)
        assert v == 73


# ================================================================
# Sort stability under repeated recomputes
# ================================================================

class TestSortStability:

    def test_repeated_calls_yield_identical_ints(self):
        # The integer return + deterministic clamp/cap means re-scoring
        # the same inputs always produces the same sort key.
        for _ in range(50):
            assert opportunity_score(
                mode="HIZLI", confidence_final=72.4,
            ) == 72
            assert opportunity_score(
                mode="TOPLANIYOR", accumulation_strength=88,
            ) == 70

    def test_cross_mode_ordering_matches_spec_priority(self):
        # §17: HIZLI/SWING/POZİSYON top → strong TOPLANIYOR mid →
        # SAKİN lower → UZAK DUR bottom.
        scores = {
            "HIZLI_high":      opportunity_score(mode="HIZLI",      confidence_final=85),
            "SWING_mid":       opportunity_score(mode="SWING",      confidence_final=70),
            "POZİSYON_low":    opportunity_score(mode="POZİSYON",   confidence_final=55),
            "TOPLANIYOR_high": opportunity_score(mode="TOPLANIYOR", accumulation_strength=90),
            "SAKİN_high_q":    opportunity_score(mode="SAKİN",      quality_score=90),
            "UZAK_DUR":        opportunity_score(mode="UZAK DUR"),
        }
        # Strong actionable beats capped TOPLANIYOR.
        assert scores["HIZLI_high"]      > scores["TOPLANIYOR_high"]
        # TOPLANIYOR (capped 70) beats SAKİN (capped 20).
        assert scores["TOPLANIYOR_high"] > scores["SAKİN_high_q"]
        # SAKİN beats UZAK DUR (5).
        assert scores["SAKİN_high_q"]    > scores["UZAK_DUR"]

    def test_deterministic_ranking_under_random_permutation(self):
        # Build a fixed-data signal pool, then sort it under different
        # input orderings — the sorted outcome should be identical.
        signals = [
            ("AKBNK", "HIZLI",      {"confidence_final": 82}),
            ("ASELS", "SWING",      {"confidence_final": 71}),
            ("TUPRS", "POZİSYON",   {"confidence_final": 64}),
            ("KCHOL", "TOPLANIYOR", {"accumulation_strength": 80}),
            ("EREGL", "SAKİN",      {"quality_score": 85}),
            ("FROTO", "UZAK DUR",   {}),
        ]

        def _sort_key(sig):
            ticker, mode, kwargs = sig
            score = opportunity_score(mode=mode, **kwargs)
            return (-score, ticker)  # ticker as secondary key, ascending

        rng = random.Random(0)
        first  = sorted(signals, key=_sort_key)
        for _ in range(10):
            shuffled = signals.copy()
            rng.shuffle(shuffled)
            again = sorted(shuffled, key=_sort_key)
            assert again == first


# ================================================================
# Sector concentration banner
# ================================================================

class TestSectorConcentrationAlert:

    def test_below_threshold_returns_none(self):
        assert sector_concentration_alert({"banka": 3, "sanayi": 4}) is None

    def test_at_threshold_returns_alert(self):
        sector, count = sector_concentration_alert(
            {"banka": SECTOR_CONCENTRATION_THRESHOLD, "sanayi": 4}
        )
        assert sector == "banka"
        assert count == SECTOR_CONCENTRATION_THRESHOLD

    def test_above_threshold_returns_alert(self):
        sector, count = sector_concentration_alert({"banka": 7, "sanayi": 2})
        assert sector == "banka"
        assert count == 7

    def test_multiple_above_threshold_picks_highest(self):
        # Tie-break by alphabetical sector name when counts equal.
        sector, count = sector_concentration_alert(
            {"banka": 7, "sanayi": 9, "holding": 8}
        )
        assert sector == "sanayi"
        assert count == 9

    def test_alphabetical_tie_break(self):
        # Equal counts, both above threshold — alphabetical wins.
        sector, count = sector_concentration_alert(
            {"sanayi": 6, "banka": 6}
        )
        assert sector == "banka"
        assert count == 6

    def test_empty_dict_returns_none(self):
        assert sector_concentration_alert({}) is None


# ================================================================
# Surface invariants
# ================================================================

class TestSurfaceInvariants:

    def test_all_modes_set_complete(self):
        # Every mode the orchestrator can emit must be scoreable.
        expected = {"HIZLI", "SWING", "POZİSYON", "TOPLANIYOR", "SAKİN", "UZAK DUR"}
        assert ALL_MODES == frozenset(expected)

    def test_sakin_mult_param_consistency(self):
        # 0.20 × cap(20) means a quality_score of 100 just hits the cap.
        # If anyone retunes either constant, this test surfaces it.
        assert OPPORTUNITY_SAKIN_MULT * 100 == OPPORTUNITY_SAKIN_CAP

    def test_uzak_dur_fixed_below_sakin_cap(self):
        # §17 ordering invariant — UZAK DUR must rank below the worst
        # possible SAKİN.
        assert OPPORTUNITY_UZAK_DUR_FIXED < OPPORTUNITY_SAKIN_CAP

    def test_toplaniyor_cap_below_actionable_max(self):
        assert OPPORTUNITY_TOPLANIYOR_CAP < 100

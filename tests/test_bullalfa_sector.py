# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# tests/test_bullalfa_sector.py
#
# Sector & universe branching tests (spec §14, §22).
#
# Coverage targets:
#   - banka uses XBANK, skips E5
#   - holding uses XHOLD
#   - gyo uses XGMYO, skips E5
#   - newly_listed: max grade B, modes restricted to HIZLI/TOPLANIYOR/SAKİN
#   - Benchmark fallback to XU100 with caveat
#   - halted forces UZAK DUR
# ================================================================

from __future__ import annotations

import pytest

from features.bullalfa_sector import (
    SectorContext,
    base_sector_group,
    cap_grade,
    detect_gyo,
    filter_modes,
    get_benchmark,
    is_newly_listed,
    resolve_sector_context,
)
from engine.bullalfa_params import (
    NEWLY_LISTED_THRESHOLD_DAYS,
    NEWLY_LISTED_GRADE_CAP,
)


# ================================================================
# detect_gyo
# ================================================================

class TestDetectGyo:

    @pytest.mark.parametrize("yf_sector,yf_industry", [
        ("Real Estate",                 "REIT - Diversified"),
        ("Real Estate",                 None),
        ("REIT",                        ""),
        ("Financial Services",          "REIT - Office"),
        ("Gayrimenkul Yatırım Ortaklığı", None),
    ])
    def test_detects_reit_keywords(self, yf_sector, yf_industry):
        assert detect_gyo(yf_sector, yf_industry) is True

    @pytest.mark.parametrize("yf_sector", [
        "Banks - Diversified",
        "Industrials",
        "Consumer Cyclical",
        None,
        "",
    ])
    def test_does_not_match_non_reit_sectors(self, yf_sector):
        assert detect_gyo(yf_sector, None) is False


# ================================================================
# is_newly_listed
# ================================================================

class TestIsNewlyListed:

    def test_below_threshold_is_newly_listed(self):
        assert is_newly_listed(NEWLY_LISTED_THRESHOLD_DAYS - 1) is True

    def test_at_threshold_is_not_newly_listed(self):
        assert is_newly_listed(NEWLY_LISTED_THRESHOLD_DAYS) is False

    def test_long_history_is_not_newly_listed(self):
        assert is_newly_listed(1000) is False

    def test_none_is_not_newly_listed(self):
        assert is_newly_listed(None) is False


# ================================================================
# base_sector_group — gyo override over engine.scoring.map_sector
# ================================================================

class TestBaseSectorGroup:

    def test_bank_maps_to_banka(self):
        assert base_sector_group("Banks - Diversified") == "banka"

    def test_holding_maps_to_holding(self):
        assert base_sector_group("Industrial Conglomerate") == "holding"

    def test_conglomerates_in_financial_services_overrides_to_holding(self):
        # yfinance often tags Turkish holdings (SAHOL, KCHOL) as
        # sector="Financial Services" + industry="Conglomerates".
        # The existing engine.scoring.map_sector lumps Financial
        # Services into "banka"; the BullAlfa-side detect_holding
        # override breaks them out to "holding" so they get XHOLD.
        assert base_sector_group("Financial Services", "Conglomerates") == "holding"

    def test_banks_in_financial_services_stay_banka(self):
        # Negative case — make sure the holding override doesn't
        # overcatch genuine bank tickers.
        assert base_sector_group("Financial Services", "Banks—Regional") == "banka"
        assert base_sector_group("Financial Services", "Banks Diversified") == "banka"

    def test_diversified_financial_overrides_to_holding(self):
        # Some holdings show up as "Diversified Financial" industry.
        assert base_sector_group("Financial Services", "Diversified Financial Services") == "holding"

    def test_reit_overrides_to_gyo(self):
        # Without our override, this would map to sanayi via map_sector
        assert base_sector_group("Real Estate", "REIT - Diversified") == "gyo"

    def test_industrial_default(self):
        assert base_sector_group("Industrials") == "sanayi"

    def test_unknown_falls_to_default(self):
        assert base_sector_group("MysteryCategory") == "sanayi"

    def test_empty_string_falls_to_default(self):
        assert base_sector_group("") == "sanayi"


# ================================================================
# get_benchmark — sector → index mapping + fallback
# ================================================================

class TestGetBenchmark:

    @pytest.mark.parametrize("sector,expected", [
        ("banka",        "XBANK"),
        ("holding",      "XHOLD"),
        ("gyo",          "XGMYO"),
        ("sanayi",       "XU100"),
        ("savunma",      "XU100"),
        ("enerji",       "XU100"),
        ("perakende",    "XU100"),
        ("ulasim",       "XU100"),
        ("newly_listed", "XU100"),
        ("halted",       "XU100"),
    ])
    def test_default_mapping(self, sector, expected):
        bench, fallback = get_benchmark(sector)
        assert bench == expected
        assert fallback is False

    def test_fallback_when_preferred_unavailable(self):
        bench, fallback = get_benchmark("banka", available_benchmarks={"XU100"})
        assert bench == "XU100"
        assert fallback is True

    def test_no_fallback_when_preferred_available(self):
        bench, fallback = get_benchmark("banka", available_benchmarks={"XBANK", "XU100"})
        assert bench == "XBANK"
        assert fallback is False

    def test_fallback_xu100_to_xu100_is_not_fallback(self):
        # When sanayi's preferred IS XU100, that's not a fallback.
        bench, fallback = get_benchmark("sanayi", available_benchmarks={"XU100"})
        assert bench == "XU100"
        assert fallback is False

    def test_unknown_sector_falls_to_default(self):
        bench, _ = get_benchmark("unknown_sector_xyz")
        assert bench == "XU100"


# ================================================================
# cap_grade
# ================================================================

class TestCapGrade:

    @pytest.mark.parametrize("grade,cap,expected,was_capped", [
        ("A+", "B",  "B",  True),
        ("A",  "B",  "B",  True),
        ("B",  "B",  "B",  False),   # at the cap, no change
        ("C",  "B",  "C",  False),   # already worse than cap
        ("D",  "B",  "D",  False),
        ("A",  None, "A",  False),   # no cap
        (None, "B",  None, False),   # no grade
    ])
    def test_capping_behaviour(self, grade, cap, expected, was_capped):
        out, capped = cap_grade(grade, cap)
        assert out == expected
        assert capped is was_capped


# ================================================================
# filter_modes
# ================================================================

class TestFilterModes:

    def test_passes_through_when_allowed(self):
        allowed = frozenset({"HIZLI", "SWING", "POZİSYON", "TOPLANIYOR", "SAKİN", "UZAK DUR"})
        assert filter_modes(allowed, "HIZLI") == "HIZLI"

    def test_downgrades_to_toplaniyor_when_swing_disallowed(self):
        allowed = frozenset({"HIZLI", "TOPLANIYOR", "SAKİN"})
        assert filter_modes(allowed, "SWING") == "TOPLANIYOR"

    def test_downgrades_to_sakin_when_no_toplaniyor(self):
        allowed = frozenset({"SAKİN", "UZAK DUR"})
        assert filter_modes(allowed, "POZİSYON") == "SAKİN"

    def test_uzak_dur_is_preserved(self):
        # UZAK DUR is preserved even if the allowed set doesn't contain it
        allowed = frozenset({"SAKİN"})
        assert filter_modes(allowed, "UZAK DUR") == "UZAK DUR"


# ================================================================
# resolve_sector_context — main entry point integration tests
# ================================================================

class TestResolveSectorContext:

    def test_banka_context(self):
        ctx = resolve_sector_context(
            yf_sector="Banks - Diversified", history_length_days=1000,
        )
        assert ctx.sector_group == "banka"
        assert ctx.benchmark == "XBANK"
        assert ctx.skip_e5 is True
        assert ctx.allowed_modes == frozenset(
            {"HIZLI", "SWING", "POZİSYON", "TOPLANIYOR", "SAKİN", "UZAK DUR"}
        )
        assert ctx.grade_cap is None
        assert ctx.short_history is False
        assert ctx.halted is False

    def test_holding_context(self):
        ctx = resolve_sector_context(
            yf_sector="Industrial Conglomerate", history_length_days=1000,
        )
        assert ctx.sector_group == "holding"
        assert ctx.benchmark == "XHOLD"
        assert ctx.skip_e5 is True

    def test_gyo_context(self):
        ctx = resolve_sector_context(
            yf_sector="Real Estate",
            yf_industry="REIT - Diversified",
            history_length_days=1000,
        )
        assert ctx.sector_group == "gyo"
        assert ctx.benchmark == "XGMYO"
        assert ctx.skip_e5 is True

    def test_industrial_default(self):
        ctx = resolve_sector_context(
            yf_sector="Industrials", history_length_days=1000,
        )
        assert ctx.sector_group == "sanayi"
        assert ctx.benchmark == "XU100"
        assert ctx.skip_e5 is False
        assert ctx.grade_cap is None

    def test_newly_listed_restricts_modes_and_caps_grade(self):
        ctx = resolve_sector_context(
            yf_sector="Industrials",
            history_length_days=NEWLY_LISTED_THRESHOLD_DAYS - 1,
        )
        assert ctx.sector_group == "newly_listed"
        assert ctx.allowed_modes == frozenset({"HIZLI", "TOPLANIYOR", "SAKİN"})
        assert ctx.grade_cap == NEWLY_LISTED_GRADE_CAP
        assert ctx.short_history is True
        assert ctx.benchmark == "XU100"
        # newly_listed sector skips E5
        assert ctx.skip_e5 is True
        # Caveat surfaced
        assert any("Kısa geçmiş" in c for c in ctx.caveats)

    def test_halted_forces_uzak_dur(self):
        ctx = resolve_sector_context(
            yf_sector="Industrials",
            history_length_days=1000,
            is_halted=True,
        )
        assert ctx.sector_group == "halted"
        assert ctx.allowed_modes == frozenset({"UZAK DUR"})
        assert ctx.halted is True
        assert any("durdurulmuş" in c for c in ctx.caveats)

    def test_halted_takes_precedence_over_newly_listed(self):
        ctx = resolve_sector_context(
            yf_sector="Industrials",
            history_length_days=50,
            is_halted=True,
        )
        # halted wins for sector_group; newly_listed flag still surfaced
        assert ctx.sector_group == "halted"
        assert ctx.short_history is True
        assert ctx.allowed_modes == frozenset({"UZAK DUR"})

    def test_benchmark_fallback_emits_caveat(self):
        ctx = resolve_sector_context(
            yf_sector="Banks",
            history_length_days=1000,
            available_benchmarks={"XU100"},
        )
        assert ctx.benchmark == "XU100"
        assert ctx.benchmark_fallback is True
        assert any("XU100 kullanıldı" in c for c in ctx.caveats)

    def test_benchmark_fallback_not_triggered_when_index_available(self):
        ctx = resolve_sector_context(
            yf_sector="Banks",
            history_length_days=1000,
            available_benchmarks={"XU100", "XBANK"},
        )
        assert ctx.benchmark == "XBANK"
        assert ctx.benchmark_fallback is False
        assert not any("XU100 kullanıldı" in c for c in ctx.caveats)

    def test_caveats_is_tuple_immutable(self):
        ctx = resolve_sector_context(
            yf_sector="Industrials", history_length_days=50,
        )
        assert isinstance(ctx.caveats, tuple)
        with pytest.raises(AttributeError):
            ctx.caveats.append("oops")  # type: ignore[attr-defined]

    def test_dataclass_is_frozen(self):
        ctx = resolve_sector_context(
            yf_sector="Industrials", history_length_days=1000,
        )
        with pytest.raises(Exception):
            ctx.sector_group = "banka"  # type: ignore[misc]

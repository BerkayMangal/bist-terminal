# ================================================================
# BISTBULL TERMINAL — Unit Tests: Scoring Engine
# Tests: map_sector, compute_risk_penalties, compute_overall,
#        compute_fa_pure, compute_ivme, detect_hype
#
# All tests are deterministic, pure-logic, no I/O.
# ================================================================

import pytest

from engine.scoring import (
    map_sector,
    compute_risk_penalties,
    compute_overall,
    compute_fa_pure,
    compute_ivme,
    compute_valuation_stretch,
    detect_hype,
)
from config import (
    PENALTY_NEGATIVE_EQUITY,
    PENALTY_NET_LOSS,
    PENALTY_NEGATIVE_CFO,
    BONUS_NET_CASH,
    OVERALL_FA_WEIGHT,
    OVERALL_MOMENTUM_WEIGHT,
    OVERALL_RISK_CAP,
    OVERALL_RISK_FACTOR,
    FA_WEIGHTS,
    IVME_WEIGHTS,
    HYPE_STRICT_PCT,
    HYPE_STRICT_VOL,
    HYPE_STRICT_FA,
    HYPE_SOFT_PCT,
    HYPE_SOFT_VOL,
    HYPE_SOFT_FA,
)


# ================================================================
# map_sector
# ================================================================
class TestMapSector:
    """map_sector maps yfinance sector strings to BistBull sector groups."""

    def test_bank_mapping(self):
        assert map_sector("Financial Services") == "banka"
        assert map_sector("Banks—Regional") == "banka"

    def test_holding_mapping(self):
        assert map_sector("Industrial Conglomerates") == "holding"
        assert map_sector("Conglomerates") == "holding"

    def test_defense_mapping(self):
        assert map_sector("Aerospace & Defense") == "savunma"

    def test_energy_mapping(self):
        assert map_sector("Energy") == "enerji"
        assert map_sector("Oil & Gas Refining") == "enerji"

    def test_retail_mapping(self):
        assert map_sector("Consumer Defensive") == "perakende"
        assert map_sector("Food Products") == "perakende"

    def test_transport_mapping(self):
        assert map_sector("Airlines") == "ulasim"

    def test_default_sanayi(self):
        """Unknown sectors default to sanayi."""
        assert map_sector("Technology") == "sanayi"
        assert map_sector("") == "sanayi"
        assert map_sector("Something Random") == "sanayi"

    def test_none_input(self):
        assert map_sector(None) == "sanayi"

    def test_case_insensitive(self):
        assert map_sector("FINANCIAL SERVICES") == "banka"
        assert map_sector("airlines") == "ulasim"


# ================================================================
# compute_risk_penalties
# ================================================================
class TestComputeRiskPenalties:
    """compute_risk_penalties returns (total_penalty, reason_list)."""

    def test_healthy_company_no_penalties(self, healthy_industrial_metrics):
        """A healthy company should have zero or positive risk penalty."""
        penalty, reasons = compute_risk_penalties(healthy_industrial_metrics, "sanayi")
        # healthy_industrial has cash > debt * 1.2, so should get net cash bonus
        assert BONUS_NET_CASH in [BONUS_NET_CASH]  # just confirming constant exists
        # Net cash: cash=25B, debt=30B, 25 > 30*1.2=36 → NO net cash bonus
        # So penalty should be 0 (no negatives triggered)
        assert penalty == 0
        assert len(reasons) == 0

    def test_negative_equity_penalised(self, distressed_company_metrics):
        penalty, reasons = compute_risk_penalties(distressed_company_metrics, "sanayi")
        assert penalty <= PENALTY_NEGATIVE_EQUITY  # at least this much penalty
        assert any("özsermaye" in r.lower() or "equity" in r.lower() for r in reasons)

    def test_net_loss_penalised(self, distressed_company_metrics):
        penalty, reasons = compute_risk_penalties(distressed_company_metrics, "sanayi")
        assert penalty <= (PENALTY_NEGATIVE_EQUITY + PENALTY_NET_LOSS)
        assert any("zarar" in r.lower() for r in reasons)

    def test_negative_cfo_penalised(self, distressed_company_metrics):
        penalty, reasons = compute_risk_penalties(distressed_company_metrics, "sanayi")
        assert any("nakit" in r.lower() for r in reasons)

    def test_high_nd_ebitda_penalised(self):
        """Very high net_debt_ebitda should trigger graduated penalty."""
        m = {"net_debt_ebitda": 6.0}
        penalty, reasons = compute_risk_penalties(m, "sanayi")
        assert penalty < 0
        assert any("NB/FAVÖK" in r for r in reasons)

    def test_high_beneish_penalised(self):
        """Beneish M-score above -1.78 is a red flag."""
        m = {"beneish_m": -1.0}
        penalty, reasons = compute_risk_penalties(m, "sanayi")
        assert penalty < 0
        assert any("Beneish" in r for r in reasons)

    def test_dilution_penalised(self):
        """Share dilution > 2% triggers penalty."""
        m = {"share_change": 0.12}
        penalty, reasons = compute_risk_penalties(m, "sanayi")
        assert penalty < 0
        assert any("seyreltme" in r.lower() for r in reasons)

    def test_net_cash_bonus(self):
        """Cash > debt × 1.2 gives bonus."""
        m = {"total_debt": 10_000, "cash": 15_000}
        penalty, reasons = compute_risk_penalties(m)
        assert penalty == BONUS_NET_CASH
        assert any("nakit" in r.lower() for r in reasons)

    def test_empty_metrics_no_crash(self):
        """Empty dict should not crash."""
        penalty, reasons = compute_risk_penalties({})
        assert penalty == 0
        assert reasons == []

    def test_all_none_metrics(self, sparse_metrics):
        """All None metrics should produce zero penalty — nothing to penalise."""
        penalty, reasons = compute_risk_penalties(sparse_metrics)
        assert penalty == 0

    def test_low_interest_coverage_penalised(self):
        m = {"interest_coverage": 0.8}
        penalty, reasons = compute_risk_penalties(m, "sanayi")
        assert penalty < 0
        assert any("faiz" in r.lower() for r in reasons)

    def test_energy_sector_uses_high_debt_thresholds(self):
        """Energy sector should use more lenient NB/FAVÖK thresholds."""
        m = {"net_debt_ebitda": 4.0}
        penalty_sanayi, _ = compute_risk_penalties(m, "sanayi")
        penalty_enerji, _ = compute_risk_penalties(m, "enerji")
        # Energy is high-debt-tolerant, so should have less penalty
        assert penalty_enerji >= penalty_sanayi


# ================================================================
# compute_fa_pure
# ================================================================
class TestComputeFaPure:
    """FA pure is a weighted average of 7 fundamental dimensions."""

    def test_all_50_gives_50(self):
        """All dimensions at 50 → FA pure = 50."""
        scores = {k: 50.0 for k in FA_WEIGHTS}
        result = compute_fa_pure(scores)
        assert result == 50.0

    def test_all_100_near_99(self):
        """All dimensions at 100 → clipped to 99."""
        scores = {k: 100.0 for k in FA_WEIGHTS}
        result = compute_fa_pure(scores)
        assert result == 99.0

    def test_all_0_gives_1(self):
        """All dimensions at 0 → clipped to 1."""
        scores = {k: 0.0 for k in FA_WEIGHTS}
        result = compute_fa_pure(scores)
        assert result == 1.0

    def test_missing_keys_default_to_50(self):
        """Missing keys default to 50 in the formula."""
        scores = {"quality": 80.0}  # only quality provided
        result_partial = compute_fa_pure(scores)
        scores_full = {k: 50.0 for k in FA_WEIGHTS}
        scores_full["quality"] = 80.0
        result_full = compute_fa_pure(scores_full)
        assert result_partial == result_full

    def test_quality_has_highest_weight(self):
        """Quality has weight 0.30 — changing it should have the biggest impact."""
        base = {k: 50.0 for k in FA_WEIGHTS}
        scores_high_quality = {**base, "quality": 90.0}
        scores_high_value = {**base, "value": 90.0}
        # quality change of 40 pts × 0.30 = 12 pts
        # value change of 40 pts × 0.18 = 7.2 pts
        assert compute_fa_pure(scores_high_quality) > compute_fa_pure(scores_high_value)

    def test_weights_sum_to_one(self):
        """Sanity: FA_WEIGHTS should sum to 1.0."""
        assert abs(sum(FA_WEIGHTS.values()) - 1.0) < 0.001


# ================================================================
# compute_ivme
# ================================================================
class TestComputeIvme:
    """Ivme (momentum) score is weighted average of 3 technical dimensions."""

    def test_all_50_gives_50(self):
        scores = {k: 50.0 for k in IVME_WEIGHTS}
        assert compute_ivme(scores) == 50.0

    def test_momentum_has_highest_weight(self):
        """Momentum has weight 0.40 — biggest influence."""
        base = {k: 50.0 for k in IVME_WEIGHTS}
        hi_mom = {**base, "momentum": 90.0}
        hi_tb = {**base, "tech_break": 90.0}
        assert compute_ivme(hi_mom) > compute_ivme(hi_tb)

    def test_weights_sum_to_one(self):
        assert abs(sum(IVME_WEIGHTS.values()) - 1.0) < 0.001


# ================================================================
# compute_valuation_stretch
# ================================================================
class TestComputeValuationStretch:
    def test_very_high_value_score(self):
        assert compute_valuation_stretch(85) == 10

    def test_midrange_value_score(self):
        assert compute_valuation_stretch(50) == 0   # 50 >= 45 → stretch=0
        assert compute_valuation_stretch(55) == 2   # 55 >= 55 → stretch=2

    def test_low_value_score(self):
        assert compute_valuation_stretch(30) == -5

    def test_very_low_value_score(self):
        assert compute_valuation_stretch(10) == -15

    def test_monotonic(self):
        """Higher value score → higher or equal stretch."""
        stretches = [compute_valuation_stretch(v) for v in [5, 20, 40, 50, 60, 70, 85]]
        for i in range(len(stretches) - 1):
            assert stretches[i] <= stretches[i + 1]


# ================================================================
# compute_overall
# ================================================================
class TestComputeOverall:
    """Overall = FA*0.55 + momentum_effect*0.35 + val_stretch + risk*0.3"""

    def test_balanced_50_no_risk(self):
        """FA=50, Ivme=50, Value=50 (stretch=0), Risk=0."""
        result = compute_overall(50.0, 50.0, 50.0, 0)
        # momentum_effect = 50 * (50/100) = 25
        # val_stretch(50) = 0 (50 >= 45 threshold)
        # overall = 50*0.55 + 25*0.35 + 0 + 0 = 27.5 + 8.75 = 36.25
        assert abs(result - 36.2) < 0.2

    def test_strong_fundamentals(self):
        """FA=80, Ivme=70, Value=75 (stretch=5), Risk=0."""
        result = compute_overall(80.0, 70.0, 75.0, 0)
        # momentum_effect = 70 * (80/100) = 56
        # val_stretch(75) = 5 (75 >= 65 threshold)
        # overall = 80*0.55 + 56*0.35 + 5 + 0 = 44 + 19.6 + 5 = 68.6
        assert abs(result - 68.6) < 0.2

    def test_risk_penalty_reduces_score(self):
        """Same as above but with -20 risk penalty."""
        no_risk = compute_overall(80.0, 70.0, 75.0, 0)
        with_risk = compute_overall(80.0, 70.0, 75.0, -20)
        assert with_risk < no_risk
        # Expected diff: -20 * 0.3 = -6
        assert abs((no_risk - with_risk) - 6.0) < 0.1

    def test_risk_penalty_capped(self):
        """Risk penalty beyond -30 is capped at -30."""
        at_cap = compute_overall(50.0, 50.0, 50.0, -30)
        beyond_cap = compute_overall(50.0, 50.0, 50.0, -100)
        assert at_cap == beyond_cap

    def test_floor_at_1(self):
        """Score never goes below 1."""
        result = compute_overall(1.0, 1.0, 10.0, -30)
        assert result >= 1.0

    def test_ceiling_at_99(self):
        """Score never exceeds 99."""
        result = compute_overall(99.0, 99.0, 95.0, 10)
        assert result <= 99.0

    def test_momentum_gated_by_fa(self):
        """Momentum effect is multiplied by FA/100 — low FA dampens momentum."""
        # FA=20 → momentum gating = 0.2
        low_fa = compute_overall(20.0, 80.0, 50.0, 0)
        # FA=80 → momentum gating = 0.8
        high_fa = compute_overall(80.0, 80.0, 50.0, 0)
        # The difference should be substantial (FA drives both direct + gating)
        assert high_fa > low_fa + 20


# ================================================================
# detect_hype
# ================================================================
class TestDetectHype:
    """detect_hype flags stocks with fast price rise + high volume + low FA."""

    def test_no_tech_data(self):
        is_hype, reason = detect_hype(None, 30.0)
        assert is_hype is False
        assert reason is None

    def test_empty_tech_dict(self):
        is_hype, reason = detect_hype({}, 30.0)
        assert is_hype is False

    def test_strict_hype_detected(self):
        """All strict thresholds exceeded → hype."""
        tech = {"pct_20d": HYPE_STRICT_PCT + 5, "vol_ratio": HYPE_STRICT_VOL + 1}
        is_hype, reason = detect_hype(tech, HYPE_STRICT_FA - 5)
        assert is_hype is True
        assert reason is not None

    def test_soft_hype_detected(self):
        """Soft thresholds exceeded but not strict."""
        tech = {
            "pct_20d": HYPE_SOFT_PCT + 1,     # above soft, below strict
            "vol_ratio": HYPE_SOFT_VOL + 0.1,
        }
        fa = HYPE_SOFT_FA - 5  # below soft threshold
        # Verify we're below strict thresholds
        assert tech["pct_20d"] <= HYPE_STRICT_PCT
        is_hype, reason = detect_hype(tech, fa)
        assert is_hype is True

    def test_no_hype_good_fundamentals(self):
        """Even with high price and volume, strong FA prevents hype flag."""
        tech = {"pct_20d": 40, "vol_ratio": 4.0}
        is_hype, reason = detect_hype(tech, 70.0)  # FA well above both thresholds
        assert is_hype is False

    def test_no_hype_low_volume(self):
        """High price rise but low volume → not hype (could be legitimate breakout)."""
        tech = {"pct_20d": 30, "vol_ratio": 1.2}
        is_hype, reason = detect_hype(tech, 30.0)
        assert is_hype is False

    def test_no_hype_low_price_rise(self):
        """Low price rise even with high volume and low FA → not hype."""
        tech = {"pct_20d": 5, "vol_ratio": 3.0}
        is_hype, reason = detect_hype(tech, 25.0)
        assert is_hype is False

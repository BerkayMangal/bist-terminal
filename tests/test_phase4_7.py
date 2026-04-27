"""Phase 4.7 calibrated FA scoring tests."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest


@pytest.fixture
def synthetic_fa_events():
    """FA events with controlled (metric -> return) relationships."""
    random.seed(42)
    events = []
    for _ in range(60):
        roe = random.uniform(-0.1, 0.4)
        pe = random.uniform(5, 40)
        nm = random.uniform(-0.1, 0.3)
        rev_g = random.uniform(-0.2, 0.4)
        # ROE and net_margin positively linked to return; PE negatively
        ret = 0.1 * roe - 0.003 * pe + 0.05 * nm + 0.05 * rev_g + random.gauss(0, 0.02)
        events.append({
            "roe": roe, "pe": pe, "net_margin": nm,
            "revenue_growth": rev_g,
            "forward_return_60d": ret,
        })
    return events


@pytest.fixture
def synthetic_fits(synthetic_fa_events):
    from engine.scoring_calibrated import calibrate_fa_metrics
    return calibrate_fa_metrics(synthetic_fa_events)


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the module-level fits cache between tests."""
    from engine.scoring_calibrated import reset_fits_cache
    reset_fits_cache()
    yield
    reset_fits_cache()


# ========== calibrate_fa_metrics ==========

class TestCalibrateFaMetrics:
    def test_produces_fits_for_known_metrics(self, synthetic_fa_events):
        from engine.scoring_calibrated import calibrate_fa_metrics
        fits = calibrate_fa_metrics(synthetic_fa_events)
        assert "roe" in fits
        assert "pe" in fits
        assert "net_margin" in fits

    def test_direction_from_registry(self, synthetic_fa_events):
        """ROE increasing (higher = better); PE decreasing."""
        from engine.scoring_calibrated import calibrate_fa_metrics
        fits = calibrate_fa_metrics(synthetic_fa_events)
        assert fits["roe"].increasing is True
        assert fits["pe"].increasing is False

    def test_respects_excluded_metrics(self, synthetic_fa_events):
        from engine.scoring_calibrated import calibrate_fa_metrics
        fits = calibrate_fa_metrics(
            synthetic_fa_events,
            excluded_metrics=frozenset({"pe"}),
        )
        assert "pe" not in fits
        assert "roe" in fits

    def test_insufficient_samples_omits_metric(self):
        from engine.scoring_calibrated import calibrate_fa_metrics
        events = [{"roe": 0.1, "forward_return_60d": 0.05}] * 5  # only 5
        fits = calibrate_fa_metrics(events, min_samples=20)
        assert "roe" not in fits


# ========== score_metric_calibrated ==========

class TestScoreMetricCalibrated:
    def test_high_roe_high_score(self, synthetic_fits):
        from engine.scoring_calibrated import score_metric_calibrated
        high = score_metric_calibrated("roe", 0.30, fits=synthetic_fits)
        low = score_metric_calibrated("roe", -0.05, fits=synthetic_fits)
        assert high is not None and low is not None
        assert high > low

    def test_high_pe_low_score_decreasing(self, synthetic_fits):
        """PE: lower = better. High PE must score low, low PE high."""
        from engine.scoring_calibrated import score_metric_calibrated
        high_pe = score_metric_calibrated("pe", 35.0, fits=synthetic_fits)
        low_pe = score_metric_calibrated("pe", 7.0, fits=synthetic_fits)
        assert high_pe is not None and low_pe is not None
        assert low_pe > high_pe

    def test_score_in_5_to_100_range(self, synthetic_fits):
        from engine.scoring_calibrated import score_metric_calibrated
        for v in (-0.5, -0.1, 0.0, 0.1, 0.2, 0.5):
            s = score_metric_calibrated("roe", v, fits=synthetic_fits)
            if s is not None:
                assert 5.0 <= s <= 100.0

    def test_none_value_returns_none(self, synthetic_fits):
        from engine.scoring_calibrated import score_metric_calibrated
        assert score_metric_calibrated("roe", None, fits=synthetic_fits) is None

    def test_missing_fit_returns_none(self, synthetic_fits):
        """A metric not in fits returns None so caller can fall back."""
        from engine.scoring_calibrated import score_metric_calibrated
        assert score_metric_calibrated("no_such_metric", 1.0, fits=synthetic_fits) is None

    def test_no_fits_available_returns_none(self):
        """Without fits argument and no disk cache: None."""
        from engine.scoring_calibrated import score_metric_calibrated, reset_fits_cache
        reset_fits_cache()
        assert score_metric_calibrated("roe", 0.15, fits={}) is None


# ========== Bucket wrappers ==========

class TestScoreValueCalibrated:
    def test_returns_float_when_fits_available(self, synthetic_fits):
        from engine.scoring_calibrated import score_value_calibrated
        m = {"pe": 12.0, "pb": 1.5, "fcf_yield": 0.05,
             "margin_safety": 0.2, "ev_ebitda": 6.0,
             "market_cap": 1000, "total_debt": 100, "cash": 50, "revenue": 500}
        # synthetic_fits only has roe/pe/net_margin/revenue_growth,
        # so score_value parts mostly None. But pe is there and contributes.
        v = score_value_calibrated(m, fits=synthetic_fits)
        assert v is not None
        assert 5.0 <= v <= 100.0

    def test_returns_none_when_all_parts_none(self):
        from engine.scoring_calibrated import score_value_calibrated
        # Empty metrics + empty fits -> all parts None -> avg returns None
        assert score_value_calibrated({}, fits={}) is None


class TestScoreQualityCalibrated:
    def test_high_roe_yields_high_quality(self, synthetic_fits):
        from engine.scoring_calibrated import score_quality_calibrated
        high_m = {"roe": 0.35, "roic": 0.25, "net_margin": 0.25}
        low_m = {"roe": -0.05, "roic": 0.02, "net_margin": -0.01}
        hi = score_quality_calibrated(high_m, fits=synthetic_fits)
        lo = score_quality_calibrated(low_m, fits=synthetic_fits)
        # Only roe and net_margin are in synthetic_fits; roic missing
        if hi is not None and lo is not None:
            assert hi > lo


# ========== Dispatcher ==========

class TestScoreDispatch:
    def test_v13_handpicked_path(self):
        """Default scoring_version calls engine/scoring.py."""
        from engine.scoring_calibrated import score_dispatch, HANDPICKED_VERSION
        m = {"roe": 0.20, "pe": 8.0, "pb": 1.5, "market_cap": 5000,
             "total_debt": 100, "cash": 50, "revenue": 1000,
             "fcf_yield": 0.05, "margin_safety": 0.25, "ev_ebitda": 5.0,
             "roic": 0.18, "net_margin": 0.15,
             "debt_equity": 0.5, "current_ratio": 1.5, "altman_z": 3.5,
             "interest_coverage": 10, "net_debt_ebitda": 1.0,
             "revenue_growth": 0.15, "eps_growth": 0.20,
             "ebitda_growth": 0.18, "peg": 0.8}
        r = score_dispatch(m, sector_group="teknoloji",
                           scoring_version=HANDPICKED_VERSION)
        assert r["scoring_version"] == HANDPICKED_VERSION
        assert r["scoring_version_effective"] == HANDPICKED_VERSION
        for bucket in ("value", "quality", "growth", "balance"):
            assert bucket in r

    def test_calibrated_path_with_fits(self, synthetic_fits):
        from engine.scoring_calibrated import score_dispatch, CALIBRATED_VERSION
        m = {"roe": 0.20, "pe": 8.0, "net_margin": 0.15,
             "revenue_growth": 0.15}
        r = score_dispatch(m, scoring_version=CALIBRATED_VERSION,
                           fits=synthetic_fits)
        assert r["scoring_version"] == CALIBRATED_VERSION
        assert r["scoring_version_effective"] == CALIBRATED_VERSION
        # quality bucket uses roe + net_margin which are in fits
        assert r["quality"] is not None

    def test_calibrated_fallback_to_v13_when_no_fits(self, tmp_path, monkeypatch):
        """When calibrated requested but no fits available, fall back
        to V13 handpicked and record the fallback in version_effective.

        Phase 4.7 deploy: real fits live in reports/, so we monkeypatch
        DEFAULT_FITS_PATH to force missing-file branch.
        """
        from engine.scoring_calibrated import (
            score_dispatch, CALIBRATED_VERSION, HANDPICKED_VERSION,
            reset_fits_cache,
        )
        import engine.scoring_calibrated as scoring_mod
        monkeypatch.setattr(scoring_mod, "DEFAULT_FITS_PATH",
                            tmp_path / "no_fits.json")
        reset_fits_cache()
        m = {"roe": 0.20, "pe": 8.0, "pb": 1.5, "market_cap": 5000,
             "total_debt": 100, "cash": 50, "revenue": 1000,
             "fcf_yield": 0.05, "margin_safety": 0.25, "ev_ebitda": 5.0}
        r = score_dispatch(m, sector_group="teknoloji",
                           scoring_version=CALIBRATED_VERSION,
                           fits=None)
        # Requested calibrated, but got handpicked effective
        assert r["scoring_version"] == CALIBRATED_VERSION
        assert r["scoring_version_effective"] == HANDPICKED_VERSION


# ========== Load/save + cache ==========

class TestFitsCache:
    def test_load_from_disk(self, synthetic_fits, tmp_path, monkeypatch):
        from engine.scoring_calibrated import (
            _get_fits, reset_fits_cache,
        )
        from research.isotonic import write_isotonic_fits_json
        path = tmp_path / "fits.json"
        write_isotonic_fits_json(synthetic_fits, path)
        reset_fits_cache()
        loaded = _get_fits(fits_path=path)
        assert loaded is not None
        assert "roe" in loaded

    def test_cache_respected_on_second_call(self, synthetic_fits, tmp_path):
        from engine.scoring_calibrated import (
            _get_fits, reset_fits_cache,
        )
        from research.isotonic import write_isotonic_fits_json
        path = tmp_path / "fits.json"
        write_isotonic_fits_json(synthetic_fits, path)
        reset_fits_cache()
        first = _get_fits(fits_path=path)
        second = _get_fits()  # no path -> should hit cache
        assert first is second  # same object

    def test_force_reload_bypasses_cache(self, synthetic_fits, tmp_path):
        from engine.scoring_calibrated import (
            _get_fits, reset_fits_cache,
        )
        from research.isotonic import write_isotonic_fits_json
        path = tmp_path / "fits.json"
        write_isotonic_fits_json(synthetic_fits, path)
        reset_fits_cache()
        first = _get_fits(fits_path=path)
        # Force a reload -- should re-read from disk
        second = _get_fits(fits_path=path, force_reload=True)
        # Different objects (re-deserialized from JSON)
        assert first is not second
        # But same data
        assert set(first.keys()) == set(second.keys())

    def test_missing_file_returns_none(self, tmp_path):
        from engine.scoring_calibrated import _get_fits, reset_fits_cache
        reset_fits_cache()
        assert _get_fits(fits_path=tmp_path / "nope.json") is None


# ========== Integration ==========

class TestAbComparison:
    """A/B: same metrics through both scoring versions produce different
    outputs but both land in the [5, 100] band."""

    def test_both_versions_in_valid_range(self, synthetic_fits):
        from engine.scoring_calibrated import (
            score_dispatch, CALIBRATED_VERSION, HANDPICKED_VERSION,
        )
        m = {"roe": 0.20, "pe": 10.0, "pb": 1.5, "net_margin": 0.15,
             "market_cap": 5000, "total_debt": 100, "cash": 50,
             "revenue": 1000, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.18,
             "debt_equity": 0.5, "current_ratio": 1.5, "altman_z": 3.5,
             "interest_coverage": 10, "net_debt_ebitda": 1.0,
             "revenue_growth": 0.15, "eps_growth": 0.20,
             "ebitda_growth": 0.18, "peg": 0.8}
        v13 = score_dispatch(m, sector_group="teknoloji",
                              scoring_version=HANDPICKED_VERSION)
        cal = score_dispatch(m, scoring_version=CALIBRATED_VERSION,
                              fits=synthetic_fits)
        for r in (v13, cal):
            for k in ("value", "quality"):
                v = r.get(k)
                if v is not None:
                    assert 5.0 <= v <= 100.0, f"{k} = {v}"


# ========== KR-006 prevention ==========

class TestDisplayFieldCorrectness:
    def test_calibrated_score_in_5_100(self, synthetic_fits):
        """Every score_metric_calibrated output must land in [5, 100]."""
        from engine.scoring_calibrated import score_metric_calibrated
        for v in (-1.0, -0.1, 0.0, 0.05, 0.1, 0.2, 0.5, 1.0):
            s = score_metric_calibrated("roe", v, fits=synthetic_fits)
            if s is not None:
                assert 5.0 <= s <= 100.0

    def test_pe_lower_better_direction_preserved(self, synthetic_fits):
        """PE=5 should score HIGHER than PE=30 (since lower PE is better)."""
        from engine.scoring_calibrated import score_metric_calibrated
        low = score_metric_calibrated("pe", 5.0, fits=synthetic_fits)
        high = score_metric_calibrated("pe", 30.0, fits=synthetic_fits)
        if low is not None and high is not None:
            assert low > high, \
                f"PE lower-better violated: pe=5 -> {low}, pe=30 -> {high}"

    def test_no_scaling_error_between_versions(self, synthetic_fits):
        """Both versions' outputs must be in comparable magnitude.
        KR-006 bug pattern: calibrated giving 0.5 (fraction) while V13 gives 50 (percent)."""
        from engine.scoring_calibrated import (
            score_dispatch, CALIBRATED_VERSION, HANDPICKED_VERSION,
        )
        m = {"roe": 0.20, "pe": 10.0, "pb": 1.5, "net_margin": 0.15,
             "market_cap": 5000, "total_debt": 100, "cash": 50,
             "revenue": 1000, "fcf_yield": 0.05, "margin_safety": 0.25,
             "ev_ebitda": 5.0, "roic": 0.18,
             "debt_equity": 0.5, "current_ratio": 1.5, "altman_z": 3.5,
             "interest_coverage": 10, "net_debt_ebitda": 1.0,
             "revenue_growth": 0.15, "eps_growth": 0.20,
             "ebitda_growth": 0.18, "peg": 0.8}
        v13 = score_dispatch(m, sector_group="teknoloji",
                              scoring_version=HANDPICKED_VERSION)
        cal = score_dispatch(m, scoring_version=CALIBRATED_VERSION,
                              fits=synthetic_fits)
        v13_q = v13.get("quality"); cal_q = cal.get("quality")
        if v13_q is not None and cal_q is not None:
            # Both should be in the same order of magnitude (tens)
            ratio = v13_q / cal_q
            assert 0.2 <= ratio <= 5.0, \
                f"quality scale divergence: v13={v13_q}, cal={cal_q}"

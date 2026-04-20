"""Phase 4.6 isotonic regression tests."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest


# ========== Pool Adjacent Violators core ==========

class TestFitIsotonicCore:
    def test_monotone_increasing_on_noisy_linear(self):
        """Noisy linear data fits to monotone step with no violations."""
        from research.isotonic import fit_isotonic
        random.seed(1)
        xs = sorted([random.uniform(0, 100) for _ in range(60)])
        ys = [0.02 * x + random.gauss(0, 0.5) for x in xs]
        fit = fit_isotonic(xs, ys, increasing=True)
        assert fit is not None
        # Every consecutive y pair must be non-decreasing
        for i in range(1, len(fit.y_values)):
            assert fit.y_values[i] >= fit.y_values[i - 1], \
                f"violation at knot {i}: {fit.y_values[i-1]} -> {fit.y_values[i]}"

    def test_monotone_decreasing_direction(self):
        from research.isotonic import fit_isotonic
        random.seed(2)
        xs = sorted([random.uniform(0, 100) for _ in range(60)])
        ys = [-0.03 * x + random.gauss(0, 0.5) for x in xs]
        fit = fit_isotonic(xs, ys, increasing=False)
        assert fit is not None
        for i in range(1, len(fit.y_values)):
            assert fit.y_values[i] <= fit.y_values[i - 1]

    def test_perfect_staircase_preserved(self):
        """If input is already monotone, fit preserves it step-for-step."""
        from research.isotonic import fit_isotonic
        xs = [1.0, 2.0, 3.0, 4.0, 5.0] * 10  # 50 samples
        ys = [10.0, 20.0, 30.0, 40.0, 50.0] * 10
        fit = fit_isotonic(xs, ys, increasing=True)
        assert fit is not None
        # Each distinct x should map to its own y roughly
        assert fit.predict(1.0) == pytest.approx(10.0)
        assert fit.predict(5.0) == pytest.approx(50.0)

    def test_pool_violation_merged(self):
        """When y is anti-correlated with x in an increasing fit,
        PAV should merge to a constant."""
        from research.isotonic import fit_isotonic
        # Anti-correlated: fit forced increasing -> output constant mean
        xs = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
        ys = [50.0, 40.0, 30.0, 20.0, 10.0] * 10
        fit = fit_isotonic(xs, ys, increasing=True)
        assert fit is not None
        # All output y_values equal (mean of 10..50 = 30)
        assert all(abs(y - 30.0) < 1e-9 for y in fit.y_values)

    def test_insufficient_samples_returns_none(self):
        from research.isotonic import fit_isotonic
        xs = list(range(10))
        ys = list(range(10))
        # Default min_samples=20
        assert fit_isotonic(xs, ys) is None

    def test_all_none_returns_none(self):
        from research.isotonic import fit_isotonic
        xs = [None] * 50
        ys = [None] * 50
        assert fit_isotonic(xs, ys) is None

    def test_nan_inf_filtered(self):
        """NaN / Inf values should be dropped silently."""
        from research.isotonic import fit_isotonic
        xs = [1.0, 2.0, float('nan'), 3.0, float('inf')] + list(range(40))
        ys = [1.0, 2.0, 3.0, float('nan'), 5.0] + list(range(40))
        # Should still produce a fit; NaN/Inf rows skipped
        fit = fit_isotonic(xs, ys, increasing=True, min_samples=20)
        # Either a fit or None is OK; main assertion is no crash
        if fit is not None:
            for y in fit.y_values:
                assert not math.isnan(y) and not math.isinf(y)


# ========== predict ==========

class TestPredict:
    def test_predict_clamps_below_domain(self):
        from research.isotonic import fit_isotonic
        xs = list(range(20, 40))  # domain [20, 39]
        ys = [float(x) for x in xs]
        fit = fit_isotonic([float(x) for x in xs], ys, increasing=True)
        assert fit is not None
        # Below domain: returns y_values[0]
        assert fit.predict(5.0) == fit.y_values[0]

    def test_predict_clamps_above_domain(self):
        from research.isotonic import fit_isotonic
        xs = list(range(20, 40))
        ys = [float(x) for x in xs]
        fit = fit_isotonic([float(x) for x in xs], ys, increasing=True)
        # Above domain: returns y_values[-1]
        assert fit.predict(100.0) == fit.y_values[-1]

    def test_predict_monotonic_across_domain(self):
        from research.isotonic import fit_isotonic
        random.seed(3)
        xs = sorted([random.uniform(-10, 10) for _ in range(50)])
        ys = [0.5 * x + random.gauss(0, 0.3) for x in xs]
        fit = fit_isotonic(xs, ys, increasing=True)
        assert fit is not None
        # Predictions at evenly-spaced points must be non-decreasing
        span = fit.domain_max - fit.domain_min
        ys_pred = [fit.predict(fit.domain_min + i * span / 20) for i in range(21)]
        for i in range(1, len(ys_pred)):
            assert ys_pred[i] >= ys_pred[i - 1]

    def test_predict_normalized_in_unit_interval(self):
        from research.isotonic import fit_isotonic
        random.seed(4)
        xs = sorted([random.uniform(0, 100) for _ in range(50)])
        ys = [0.01 * x + random.gauss(0, 0.5) for x in xs]
        fit = fit_isotonic(xs, ys, increasing=True)
        for x in (-50, 0, 25, 50, 75, 100, 200):
            v = fit.predict_normalized(x)
            assert 0.0 <= v <= 1.0

    def test_predict_normalized_degenerate_returns_half(self):
        """If y_min == y_max (all outputs same), normalized returns 0.5."""
        from research.isotonic import fit_isotonic
        xs = list(range(30))
        ys = [5.0] * 30  # all same y
        fit = fit_isotonic([float(x) for x in xs], ys, increasing=True)
        assert fit is not None
        assert fit.predict_normalized(15.0) == 0.5


# ========== fit_per_metric ==========

class TestFitPerMetric:
    def test_multiple_metrics_fitted(self):
        from research.isotonic import fit_per_metric
        random.seed(5)
        events = [
            {"roe": random.uniform(0, 0.3),
             "pe":  random.uniform(5, 40),
             "ret": random.uniform(-0.2, 0.5)}
            for _ in range(40)
        ]
        fits = fit_per_metric(
            events, metric_keys=["roe", "pe"], return_key="ret",
            direction={"roe": True, "pe": False},
        )
        assert "roe" in fits
        assert "pe" in fits
        assert fits["roe"].increasing is True
        assert fits["pe"].increasing is False

    def test_metric_with_insufficient_samples_omitted(self):
        from research.isotonic import fit_per_metric
        events = [{"good": i, "bad": None, "ret": i * 0.1}
                  for i in range(40)]
        fits = fit_per_metric(events, metric_keys=["good", "bad"],
                              return_key="ret")
        assert "good" in fits
        assert "bad" not in fits  # all None


# ========== Serialization ==========

class TestSerialization:
    def test_to_dict_from_dict_roundtrip(self):
        from research.isotonic import fit_isotonic, IsotonicFit
        random.seed(6)
        xs = sorted([random.uniform(0, 100) for _ in range(30)])
        ys = [0.01 * x + random.gauss(0, 0.3) for x in xs]
        fit = fit_isotonic(xs, ys, increasing=True)
        d = fit.to_dict()
        rebuilt = IsotonicFit.from_dict(d)
        # predict at several points must match
        for x in (10, 50, 90):
            assert abs(fit.predict(x) - rebuilt.predict(x)) < 1e-9

    def test_write_and_load_json(self, tmp_path):
        from research.isotonic import (
            fit_isotonic, write_isotonic_fits_json, load_isotonic_fits_json,
        )
        random.seed(7)
        xs = sorted([random.uniform(0, 100) for _ in range(30)])
        ys = [0.01 * x + random.gauss(0, 0.3) for x in xs]
        fit = fit_isotonic(xs, ys, increasing=True)
        fits = {"roe": fit}
        path = tmp_path / "f.json"
        write_isotonic_fits_json(fits, path)
        loaded = load_isotonic_fits_json(path)
        assert "roe" in loaded
        assert abs(loaded["roe"].predict(50) - fit.predict(50)) < 1e-9

    def test_markdown_report_lists_all_metrics(self, tmp_path):
        from research.isotonic import (
            fit_isotonic, write_isotonic_fits_markdown,
        )
        random.seed(8)
        xs = sorted([random.uniform(0, 100) for _ in range(30)])
        ys = [0.01 * x + random.gauss(0, 0.3) for x in xs]
        fit = fit_isotonic(xs, ys, increasing=True)
        fits = {"roe": fit, "pe": fit}
        path = tmp_path / "fits.md"
        write_isotonic_fits_markdown(fits, path)
        md = path.read_text()
        assert "## roe" in md
        assert "## pe" in md


# ========== Real-data smoke ==========

class TestRealDataFit:
    """Sanity check: fit ret_5d -> ret_20d on 52W High Breakout events.
    A strong early move should predict a stronger sustained move (momentum)."""

    def test_52w_momentum_monotone(self):
        from research.calibration import load_events_csv
        from research.isotonic import fit_isotonic
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        evs = [e for e in events if e.get("signal") == "52W High Breakout"]
        xs = [e.get("ret_5d") for e in evs]
        ys = [e.get("ret_20d") for e in evs]
        fit = fit_isotonic(xs, ys, increasing=True)
        assert fit is not None
        assert fit.n_samples >= 500
        # Sanity: strong 5d return predicts positive 20d
        y_at_high_5d = fit.predict(0.05)  # +5% in 5 days
        y_at_low_5d = fit.predict(-0.05)  # -5% in 5 days
        assert y_at_high_5d > y_at_low_5d, \
            f"expected monotonicity: y({-0.05})={y_at_low_5d}, y({0.05})={y_at_high_5d}"


# ========== KR-006 prevention ==========

class TestDisplayFieldCorrectness:
    """All fit outputs should be in plausible fractional scale, not 100x off."""

    def test_predict_values_in_input_range(self):
        """Fitted y values must lie within the input y range.
        (Isotonic regression never extrapolates; it's a weighted average of inputs.)"""
        from research.isotonic import fit_isotonic
        random.seed(9)
        xs = sorted([random.uniform(0, 100) for _ in range(50)])
        ys = [0.01 * x + random.gauss(0, 0.3) for x in xs]
        fit = fit_isotonic(xs, ys, increasing=True)
        assert fit is not None
        y_min_in = min(ys); y_max_in = max(ys)
        assert fit.y_min >= y_min_in - 1e-9
        assert fit.y_max <= y_max_in + 1e-9

    def test_predict_normalized_always_unit_interval(self):
        from research.isotonic import fit_isotonic
        random.seed(10)
        xs = sorted([random.uniform(-5, 5) for _ in range(30)])
        ys = [0.3 * x + random.gauss(0, 0.1) for x in xs]
        fit = fit_isotonic(xs, ys, increasing=True)
        assert fit is not None
        for x in (-100, -5, 0, 5, 100):
            v = fit.predict_normalized(x)
            assert 0.0 <= v <= 1.0

    def test_domain_bounds_are_real_numbers(self):
        """domain_min/max should be actual x values from the input,
        not 100x scaled or sign-flipped."""
        from research.isotonic import fit_isotonic
        xs = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
        ys = [10.0, 20.0, 30.0, 40.0, 50.0] * 10
        fit = fit_isotonic(xs, ys, increasing=True)
        assert fit.domain_min == 1.0
        assert fit.domain_max == 5.0
        assert fit.y_min == 10.0
        assert fit.y_max == 50.0

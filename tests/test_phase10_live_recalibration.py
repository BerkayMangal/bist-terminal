"""Phase 10 — Live recalibration pipeline tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ==========================================================================
# Fixtures
# ==========================================================================

@pytest.fixture
def good_current_fit():
    return {
        "x_knots": [0.10, 0.20, 0.30],
        "y_values": [-0.05, 0.05, 0.10],
        "increasing": True, "n_samples": 200,
        "domain_min": 0.10, "domain_max": 0.30,
        "y_min": -0.05, "y_max": 0.10,
    }


@pytest.fixture
def good_candidate_fit():
    return {
        "x_knots": [0.10, 0.20, 0.30],
        "y_values": [-0.05, 0.06, 0.11],  # tiny shift
        "increasing": True, "n_samples": 220,  # more data
        "domain_min": 0.10, "domain_max": 0.30,
        "y_min": -0.05, "y_max": 0.11,
    }


@pytest.fixture
def fifteen_metrics_current(good_current_fit):
    return {f"m_{i}": dict(good_current_fit) for i in range(15)}


@pytest.fixture
def fifteen_metrics_candidate(good_candidate_fit):
    return {f"m_{i}": dict(good_candidate_fit) for i in range(15)}


# ==========================================================================
# PromotionVerdict basics
# ==========================================================================

class TestPromotionVerdict:
    def test_to_dict(self):
        from engine.live_recalibration import PromotionVerdict
        v = PromotionVerdict(promote=True, reason="ok", diagnostics={"x": 1})
        d = v.to_dict()
        assert d["promote"] is True
        assert d["reason"] == "ok"
        assert d["diagnostics"] == {"x": 1}


# ==========================================================================
# validate_promotion
# ==========================================================================

class TestValidatePromotion:
    def test_no_current_fits_promotes(self, good_candidate_fit):
        from engine.live_recalibration import validate_promotion
        v = validate_promotion({}, {"roe": good_candidate_fit})
        assert v.promote is True
        assert v.reason == "no_current_fits"

    def test_empty_candidate_blocks(self, good_current_fit):
        from engine.live_recalibration import validate_promotion
        v = validate_promotion({"roe": good_current_fit}, {})
        assert v.promote is False
        assert v.reason == "empty_candidate"

    def test_small_shifts_promote(
        self, fifteen_metrics_current, fifteen_metrics_candidate,
    ):
        from engine.live_recalibration import validate_promotion
        v = validate_promotion(fifteen_metrics_current, fifteen_metrics_candidate)
        assert v.promote is True

    def test_metric_count_explosion_blocks(
        self, fifteen_metrics_current, good_candidate_fit,
    ):
        """Candidate has 30 metrics vs current 15 → too much shift."""
        from engine.live_recalibration import validate_promotion
        candidate = {f"m_{i}": dict(good_candidate_fit) for i in range(30)}
        v = validate_promotion(fifteen_metrics_current, candidate)
        assert v.promote is False
        assert v.reason == "metric_count_shift_too_large"

    def test_low_metric_overlap_blocks(
        self, good_current_fit, good_candidate_fit,
    ):
        """Almost no shared metrics → blocked."""
        from engine.live_recalibration import validate_promotion
        current = {f"old_{i}": dict(good_current_fit) for i in range(10)}
        candidate = {f"new_{i}": dict(good_candidate_fit) for i in range(10)}
        v = validate_promotion(current, candidate)
        assert v.promote is False
        assert v.reason == "insufficient_metric_overlap"

    def test_sample_size_regression_blocks(self, good_current_fit):
        """Candidate has way fewer samples → blocked."""
        from engine.live_recalibration import validate_promotion
        current = {f"m_{i}": dict(good_current_fit) for i in range(15)}
        # Candidate with much smaller n_samples
        small_cand = dict(good_current_fit)
        small_cand["n_samples"] = 50
        candidate = {f"m_{i}": dict(small_cand) for i in range(15)}
        v = validate_promotion(current, candidate)
        assert v.promote is False
        assert v.reason == "sample_size_regression"

    def test_large_y_shift_blocks(self, good_current_fit):
        """Candidate y-values shifted a lot → blocked."""
        from engine.live_recalibration import validate_promotion
        current = {f"m_{i}": dict(good_current_fit) for i in range(15)}

        big_shift = dict(good_current_fit)
        # Shift median y by 0.30 (huge)
        big_shift["y_values"] = [0.20, 0.30, 0.40]
        candidate = {f"m_{i}": dict(big_shift) for i in range(15)}
        v = validate_promotion(current, candidate)
        assert v.promote is False
        assert v.reason in ("mean_y_shift_too_large",
                             "per_metric_y_range_shift_too_large")

    def test_per_metric_y_range_explosion_blocks(self, good_current_fit):
        """Candidate has wider y-range → blocked if too wide."""
        from engine.live_recalibration import validate_promotion
        current = {f"m_{i}": dict(good_current_fit) for i in range(15)}
        wide = dict(good_current_fit)
        wide["y_values"] = [-0.50, 0.0, 0.50]  # range of 1.0 vs 0.15 originally
        candidate = {f"m_{i}": dict(wide) for i in range(15)}
        v = validate_promotion(current, candidate)
        assert v.promote is False


# ==========================================================================
# Backup + restore
# ==========================================================================

class TestBackupAndRestore:
    def test_backup_creates_timestamped_copy(self, tmp_path):
        from engine.live_recalibration import backup_fits

        src = tmp_path / "fa_isotonic_fits.json"
        src.write_text('{"roe": {"x_knots": [0.1]}}')

        backup_path = backup_fits(src)
        assert backup_path is not None
        assert backup_path.exists()
        assert backup_path.parent.name == "backups"
        assert backup_path.stem.startswith("fa_isotonic_fits_")
        assert backup_path.read_text() == src.read_text()

    def test_backup_missing_source_returns_none(self, tmp_path):
        from engine.live_recalibration import backup_fits
        result = backup_fits(tmp_path / "nonexistent.json")
        assert result is None

    def test_restore_overwrites_target(self, tmp_path):
        from engine.live_recalibration import backup_fits, restore_fits

        src = tmp_path / "fa_isotonic_fits.json"
        original_content = '{"version": "v1"}'
        src.write_text(original_content)

        backup = backup_fits(src)

        # Modify original
        src.write_text('{"version": "v2_corrupted"}')

        # Restore
        ok = restore_fits(src, backup)
        assert ok is True
        assert src.read_text() == original_content

    def test_restore_missing_backup_returns_false(self, tmp_path):
        from engine.live_recalibration import restore_fits
        ok = restore_fits(
            tmp_path / "fits.json",
            tmp_path / "no_backup.json",
        )
        assert ok is False

    def test_list_backups_newest_first(self, tmp_path):
        from engine.live_recalibration import list_backups
        import time

        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        # Create 3 fake backup files with different mtimes
        b1 = backups_dir / "fa_isotonic_fits_20240101.json"
        b2 = backups_dir / "fa_isotonic_fits_20240601.json"
        b3 = backups_dir / "fa_isotonic_fits_20241201.json"
        for p in [b1, b2, b3]:
            p.write_text("{}")
            time.sleep(0.01)  # ensure mtime ordering

        result = list_backups(backups_dir)
        assert len(result) == 3
        # Newest (b3, last created) should be first
        assert result[0] == b3
        assert result[2] == b1

    def test_list_backups_empty_dir(self, tmp_path):
        from engine.live_recalibration import list_backups
        empty = tmp_path / "no_backups_yet"
        result = list_backups(empty)
        assert result == []


# ==========================================================================
# LiveRecalibrator
# ==========================================================================

class TestLiveRecalibrator:
    def test_load_current_fits_returns_empty_when_missing(self, tmp_path):
        from engine.live_recalibration import LiveRecalibrator
        r = LiveRecalibrator(fits_path=tmp_path / "missing.json")
        assert r.load_current_fits() == {}

    def test_load_current_fits_reads_valid_json(self, tmp_path):
        from engine.live_recalibration import LiveRecalibrator
        path = tmp_path / "fits.json"
        path.write_text('{"roe": {"x_knots": [0.1]}}')
        r = LiveRecalibrator(fits_path=path)
        loaded = r.load_current_fits()
        assert "roe" in loaded

    def test_load_current_fits_handles_invalid_json(self, tmp_path):
        from engine.live_recalibration import LiveRecalibrator
        path = tmp_path / "fits.json"
        path.write_text("not valid json")
        r = LiveRecalibrator(fits_path=path)
        assert r.load_current_fits() == {}

    def test_fetch_events_scaffold_returns_empty(self, tmp_path):
        """Phase 10 scaffold: fetch_events returns [] until deploy."""
        from engine.live_recalibration import LiveRecalibrator
        r = LiveRecalibrator(fits_path=tmp_path / "fits.json")
        assert r.fetch_events(since_days=90) == []

    def test_calibrate_empty_returns_empty(self, tmp_path):
        from engine.live_recalibration import LiveRecalibrator
        r = LiveRecalibrator(fits_path=tmp_path / "fits.json")
        assert r.calibrate([]) == {}

    def test_run_dry_run_does_not_modify_fits(self, tmp_path):
        from engine.live_recalibration import LiveRecalibrator
        path = tmp_path / "fits.json"
        original = '{"roe": {"x_knots": [0.1, 0.2]}}'
        path.write_text(original)

        r = LiveRecalibrator(fits_path=path)
        result = r.run(dry_run=True)
        assert result.promoted is False
        # File should be unchanged
        assert path.read_text() == original

    def test_run_with_no_events_blocks_promotion(self, tmp_path):
        from engine.live_recalibration import LiveRecalibrator
        path = tmp_path / "fits.json"
        # Write valid existing fits
        path.write_text(json.dumps({
            "roe": {
                "x_knots": [0.10, 0.20], "y_values": [-0.05, 0.10],
                "increasing": True, "n_samples": 100,
                "domain_min": 0.10, "domain_max": 0.20,
                "y_min": -0.05, "y_max": 0.10,
            }
        }))

        r = LiveRecalibrator(fits_path=path)
        result = r.run(dry_run=False)
        # No events → empty candidate → blocked
        assert result.promoted is False
        assert result.n_events == 0


# ==========================================================================
# Promotion config
# ==========================================================================

class TestPromotionConfig:
    def test_defaults_reasonable(self):
        from engine.live_recalibration import PromotionConfig
        c = PromotionConfig()
        # Sanity: defaults are conservative, not zero / infinite
        assert 0 < c.max_mean_y_shift < 0.10
        assert 0 < c.max_per_metric_y_shift < 0.30
        assert 0.5 < c.min_metric_overlap <= 1.0
        assert 0.7 <= c.min_n_samples_ratio <= 1.0

    def test_overrides_apply(self):
        from engine.live_recalibration import PromotionConfig
        c = PromotionConfig(
            max_metric_delta=10,
            max_mean_y_shift=0.05,
        )
        assert c.max_metric_delta == 10
        assert c.max_mean_y_shift == 0.05

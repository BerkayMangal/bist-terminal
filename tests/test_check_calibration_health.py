"""Phase 4.8.1 — calibration health check script tests.

Cover all the failure modes that *should* trip the script,
plus the happy path.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest


# Load script as a module
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "check_calibration_health.py"
)
_spec = importlib.util.spec_from_file_location("check_health", _SCRIPT_PATH)
check_health = importlib.util.module_from_spec(_spec)
sys.modules["check_health"] = check_health
_spec.loader.exec_module(check_health)


# ==========================================================================
# Fixtures
# ==========================================================================

@pytest.fixture
def good_fit():
    """A valid fit dict for one metric."""
    return {
        "x_knots": [-2.0, -1.0, 0.0, 1.0, 2.0],
        "y_values": [-0.05, -0.02, 0.0, 0.03, 0.08],
        "increasing": True,
        "n_samples": 150,
        "domain_min": -2.0,
        "domain_max": 2.0,
    }


@pytest.fixture
def healthy_repo(tmp_path, good_fit):
    """A repo dir with valid fits, events, summary."""
    reports = tmp_path / "reports"
    reports.mkdir()

    fits = {f"metric_{i}": good_fit for i in range(15)}
    (reports / "fa_isotonic_fits.json").write_text(json.dumps(fits))

    # CSV: 200 rows × 5 symbols
    with open(reports / "fa_events.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "period_end", "filed_at", "metric",
                    "metric_value", "forward_return_60d",
                    "forward_price_from", "forward_price_to",
                    "sector", "source"])
        for i in range(200):
            sym = ["AKSEN", "HEKTS", "KCHOL", "KRDMD", "MGROS"][i % 5]
            w.writerow([sym, "2020-01-01", "2020-02-15", "roe",
                        0.15, 0.05, 1.0, 1.05, "sanayi", "test"])

    (reports / "fa_calibration_summary.md").write_text(
        "# FA Calibration Summary\n\n"
        "**Input events:** 200\n\n"
        "**Metrics fitted:** 15\n\n"
    )

    return tmp_path


# ==========================================================================
# Happy path
# ==========================================================================

class TestHappyPath:
    def test_default_thresholds_pass(self, healthy_repo):
        ok, results = check_health.run_all_checks(
            repo_root=healthy_repo,
            min_metrics=5, min_rows=100, min_symbols=3, min_events=100,
        )
        assert ok, "\n".join(r.render() for r in results)
        assert all(r.ok for r in results)

    def test_strict_thresholds_with_real_quality_data(self, healthy_repo):
        # 15 metrics, 200 rows, 5 symbols, 200 events
        # Strict default: 10 metrics, 500 rows — this should partially fail
        ok, results = check_health.run_all_checks(
            repo_root=healthy_repo,
            min_metrics=10, min_rows=500, min_symbols=5, min_events=500,
        )
        # Will fail on row_count (200 < 500)
        assert not ok
        failed = [r for r in results if not r.ok]
        assert any("row_count" in r.name for r in failed)


# ==========================================================================
# fits.json failure modes
# ==========================================================================

class TestFitsJsonFailures:
    def test_missing_file(self, tmp_path):
        path = tmp_path / "fits.json"
        results = check_health.check_fits_json(path, min_metrics=5)
        assert not results[0].ok
        assert "not found" in results[0].message

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "fits.json"
        path.write_text("{ not valid json }")
        results = check_health.check_fits_json(path, min_metrics=5)
        assert any("parses" in r.name and not r.ok for r in results)

    def test_empty_object(self, tmp_path):
        # The disaster case: {} dict that loader silently falls back on
        path = tmp_path / "fits.json"
        path.write_text("{}")
        results = check_health.check_fits_json(path, min_metrics=5)
        # exists + parses + is_dict pass, metric_count fails
        assert any("metric_count" in r.name and not r.ok for r in results)
        msg = next(r.message for r in results if "metric_count" in r.name)
        assert "0 metrics" in msg or "expected >=" in msg

    def test_too_few_metrics(self, tmp_path, good_fit):
        path = tmp_path / "fits.json"
        path.write_text(json.dumps({"roe": good_fit, "pe": good_fit}))
        results = check_health.check_fits_json(path, min_metrics=5)
        assert any("metric_count" in r.name and not r.ok for r in results)

    def test_not_a_dict(self, tmp_path):
        path = tmp_path / "fits.json"
        path.write_text("[1, 2, 3]")
        results = check_health.check_fits_json(path, min_metrics=5)
        assert any("is_dict" in r.name and not r.ok for r in results)

    def test_metric_missing_keys(self, tmp_path):
        path = tmp_path / "fits.json"
        bad = {f"metric_{i}": {"x_knots": [1, 2]} for i in range(10)}
        path.write_text(json.dumps(bad))
        results = check_health.check_fits_json(path, min_metrics=5)
        assert any("per_metric_structure" in r.name and not r.ok for r in results)

    def test_x_knots_y_values_length_mismatch(self, tmp_path, good_fit):
        path = tmp_path / "fits.json"
        broken = dict(good_fit)
        broken["x_knots"] = [1.0, 2.0, 3.0]
        broken["y_values"] = [0.0, 0.5]  # length mismatch
        fits = {f"metric_{i}": broken for i in range(10)}
        path.write_text(json.dumps(fits))
        results = check_health.check_fits_json(path, min_metrics=5)
        assert any("per_metric_structure" in r.name and not r.ok for r in results)

    def test_one_knot_only(self, tmp_path, good_fit):
        path = tmp_path / "fits.json"
        broken = dict(good_fit)
        broken["x_knots"] = [1.0]
        broken["y_values"] = [0.5]
        fits = {f"metric_{i}": broken for i in range(10)}
        path.write_text(json.dumps(fits))
        results = check_health.check_fits_json(path, min_metrics=5)
        assert any("per_metric_structure" in r.name and not r.ok for r in results)


# ==========================================================================
# events.csv failure modes
# ==========================================================================

class TestEventsCsvFailures:
    def test_missing_file(self, tmp_path):
        results = check_health.check_events_csv(
            tmp_path / "events.csv", min_rows=100, min_symbols=3,
        )
        assert not results[0].ok

    def test_header_only(self, tmp_path):
        # The disaster case: only header, no data rows
        path = tmp_path / "events.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "period_end", "filed_at", "metric",
                        "metric_value", "forward_return_60d"])
        results = check_health.check_events_csv(path, min_rows=100, min_symbols=3)
        assert any("row_count" in r.name and not r.ok for r in results)

    def test_missing_columns(self, tmp_path):
        path = tmp_path / "events.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "metric"])  # missing required cols
            for _ in range(150):
                w.writerow(["AKSEN", "roe"])
        results = check_health.check_events_csv(path, min_rows=100, min_symbols=3)
        assert any("header" in r.name and not r.ok for r in results)

    def test_too_few_symbols(self, tmp_path):
        path = tmp_path / "events.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "period_end", "filed_at", "metric",
                        "metric_value", "forward_return_60d"])
            for _ in range(150):
                w.writerow(["AKSEN", "2020-01-01", "2020-02-15",
                            "roe", 0.15, 0.05])
        results = check_health.check_events_csv(path, min_rows=100, min_symbols=3)
        assert any("distinct_symbols" in r.name and not r.ok for r in results)


# ==========================================================================
# summary.md failure modes
# ==========================================================================

class TestSummaryMdFailures:
    def test_missing_file(self, tmp_path):
        results = check_health.check_summary_md(
            tmp_path / "summary.md", min_events=100, min_metrics=5,
        )
        assert not results[0].ok

    def test_missing_input_events_line(self, tmp_path):
        path = tmp_path / "summary.md"
        path.write_text("# FA Calibration Summary\n\n**Metrics fitted:** 15\n")
        results = check_health.check_summary_md(
            path, min_events=100, min_metrics=5,
        )
        assert any("input_events_line" in r.name and not r.ok for r in results)

    def test_zero_events_reported(self, tmp_path):
        # The disaster case from Phase 4.7: "Input events: 0"
        path = tmp_path / "summary.md"
        path.write_text(
            "# FA Calibration Summary\n\n"
            "**Input events:** 0\n\n"
            "**Metrics fitted:** 0\n"
        )
        results = check_health.check_summary_md(
            path, min_events=100, min_metrics=5,
        )
        assert any("input_events" in r.name and not r.ok for r in results)
        assert any("metrics_fitted" in r.name and not r.ok for r in results)

    def test_low_metrics_reported(self, tmp_path):
        path = tmp_path / "summary.md"
        path.write_text(
            "# FA Calibration Summary\n\n"
            "**Input events:** 200\n\n"
            "**Metrics fitted:** 2\n"
        )
        results = check_health.check_summary_md(
            path, min_events=100, min_metrics=5,
        )
        assert any("metrics_fitted" in r.name and not r.ok for r in results)


# ==========================================================================
# CLI integration
# ==========================================================================

class TestCli:
    def test_default_mode_on_healthy_repo(self, healthy_repo, capsys):
        rc = check_health.main(["--repo-root", str(healthy_repo)])
        assert rc == 0
        captured = capsys.readouterr()
        assert "All" in captured.out and "checks passed" in captured.out

    def test_strict_mode_on_healthy_repo_fails_on_row_count(self, healthy_repo, capsys):
        # Healthy repo has 200 rows, strict needs 500
        rc = check_health.main(["--repo-root", str(healthy_repo), "--strict"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "checks failed" in captured.out

    def test_quiet_flag_suppresses_per_check_output(self, healthy_repo, capsys):
        rc = check_health.main(["--repo-root", str(healthy_repo), "--quiet"])
        assert rc == 0
        captured = capsys.readouterr()
        # Quiet hides individual check lines; only summary remains
        assert "All" in captured.out
        # No per-check icons should appear in quiet mode
        assert "  ✅" not in captured.out

    def test_explicit_threshold_overrides(self, healthy_repo, capsys):
        # Override min-rows to 50 so 200-row repo passes strict-ish settings
        rc = check_health.main([
            "--repo-root", str(healthy_repo),
            "--min-metrics", "10",
            "--min-rows", "100",
            "--min-symbols", "5",
            "--min-events", "100",
        ])
        assert rc == 0

    def test_failing_repo_exits_with_1(self, tmp_path, capsys):
        # Empty repo — no reports/ at all
        rc = check_health.main(["--repo-root", str(tmp_path)])
        assert rc == 1


# ==========================================================================
# CWD-independence (Phase 4.3.5 pattern)
# ==========================================================================

class TestCwdIndependence:
    def test_repo_root_resolves_via_script_path(self):
        """_REPO_ROOT should be consistent regardless of where script is run."""
        assert check_health._REPO_ROOT.is_absolute()
        # Should point to the actual repo (parent of scripts/)
        assert (check_health._REPO_ROOT / "scripts" / "check_calibration_health.py").exists()

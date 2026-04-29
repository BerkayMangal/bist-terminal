"""Tests for scripts/calibrate_fa_from_events.py — the executor that
converts fa_events.csv into fa_isotonic_fits.json."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import random
import sys
from pathlib import Path

import pytest


_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "calibrate_fa_from_events.py"
)
_spec = importlib.util.spec_from_file_location("fa_calibrate", _SCRIPT_PATH)
fa_calibrate = importlib.util.module_from_spec(_spec)
sys.modules["fa_calibrate"] = fa_calibrate
_spec.loader.exec_module(fa_calibrate)


# ==========================================================================
# Fixtures
# ==========================================================================

def _write_events_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["symbol", "period_end", "filed_at", "metric",
            "metric_value", "forward_return_60d",
            "forward_price_from", "forward_price_to",
            "sector", "source"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@pytest.fixture
def good_events_csv(tmp_path):
    """40 symbols × 3 metrics (roe ↑, pe ↓, net_margin ↑) with
    clear monotone relationships to forward_return."""
    random.seed(42)
    rows = []
    for i in range(40):
        sym = f"SYM{i:03d}"
        for q in range(6):  # 6 quarters per symbol
            roe = random.uniform(-0.05, 0.40)
            pe = random.uniform(5, 40)
            nm = random.uniform(-0.05, 0.25)
            # Clean monotone: high roe → high return; high pe → low return
            ret = 0.20 * roe - 0.002 * pe + 0.15 * nm + random.gauss(0, 0.01)
            for mname, mval in (("roe", roe), ("pe", pe), ("net_margin", nm)):
                rows.append({
                    "symbol": sym, "period_end": f"2020-{(q*3)%12+1:02d}-01",
                    "filed_at": f"2020-{(q*3)%12+1:02d}-15",
                    "metric": mname, "metric_value": mval,
                    "forward_return_60d": ret,
                    "forward_price_from": 100, "forward_price_to": 100 * (1+ret),
                    "sector": "TestSector", "source": "test",
                })
    path = tmp_path / "events.csv"
    _write_events_csv(path, rows)
    return path


# ==========================================================================
# TestLoadEvents
# ==========================================================================

class TestLoadEvents:
    def test_loads_valid_rows(self, good_events_csv):
        rows = fa_calibrate._load_events(good_events_csv)
        assert len(rows) == 40 * 6 * 3

    def test_skips_nonnumeric_metric_value(self, tmp_path):
        p = tmp_path / "bad.csv"
        _write_events_csv(p, [
            {"symbol": "X", "period_end": "2020-01-01", "filed_at": "2020-01-15",
             "metric": "roe", "metric_value": "not-a-number",
             "forward_return_60d": 0.1,
             "forward_price_from": 100, "forward_price_to": 110,
             "sector": "T", "source": "s"},
            {"symbol": "Y", "period_end": "2020-01-01", "filed_at": "2020-01-15",
             "metric": "roe", "metric_value": 0.15,
             "forward_return_60d": 0.12,
             "forward_price_from": 100, "forward_price_to": 112,
             "sector": "T", "source": "s"},
        ])
        rows = fa_calibrate._load_events(p)
        assert len(rows) == 1  # only the valid Y row
        assert rows[0]["symbol"] == "Y"

    def test_skips_nan_inf(self, tmp_path):
        p = tmp_path / "nan.csv"
        _write_events_csv(p, [
            {"symbol": "X", "period_end": "2020-01-01", "filed_at": "2020-01-15",
             "metric": "roe", "metric_value": "nan",
             "forward_return_60d": 0.1,
             "forward_price_from": 100, "forward_price_to": 110,
             "sector": "T", "source": "s"},
            {"symbol": "Y", "period_end": "2020-01-01", "filed_at": "2020-01-15",
             "metric": "roe", "metric_value": "inf",
             "forward_return_60d": 0.1,
             "forward_price_from": 100, "forward_price_to": 110,
             "sector": "T", "source": "s"},
        ])
        rows = fa_calibrate._load_events(p)
        assert rows == []


# ==========================================================================
# TestCoverage
# ==========================================================================

class TestCoverage:
    def test_coverage_is_fraction_of_symbols(self):
        events = [
            {"metric": "roe", "symbol": "A"},
            {"metric": "roe", "symbol": "B"},
            {"metric": "pe",  "symbol": "A"},
            # pe coverage = 1/2 = 50%, roe = 2/2 = 100%
        ]
        cov = fa_calibrate._coverage_by_metric(events)
        assert cov["roe"] == 1.0
        assert cov["pe"] == 0.5

    def test_low_coverage_excluded(self, tmp_path):
        """Metrics with <min_coverage of symbols get excluded."""
        rows = []
        # 10 symbols have ROE; only 2 have PE
        for i in range(10):
            rows.append({
                "symbol": f"S{i}", "period_end": "2020-01-01",
                "filed_at": "2020-01-15",
                "metric": "roe", "metric_value": 0.1 + 0.01 * i,
                "forward_return_60d": 0.01 * i,
                "forward_price_from": 100, "forward_price_to": 100,
                "sector": "T", "source": "s",
            })
        for i in range(2):
            rows.append({
                "symbol": f"S{i}", "period_end": "2020-01-01",
                "filed_at": "2020-01-15",
                "metric": "pe", "metric_value": 10 + i,
                "forward_return_60d": 0.05,
                "forward_price_from": 100, "forward_price_to": 100,
                "sector": "T", "source": "s",
            })
        # Pad so we have enough ROE samples for fit
        for i in range(15):
            rows.append({
                "symbol": f"S{i % 10}", "period_end": "2021-01-01",
                "filed_at": "2021-01-15",
                "metric": "roe", "metric_value": 0.08 + 0.01 * i,
                "forward_return_60d": 0.01 * (i % 10),
                "forward_price_from": 100, "forward_price_to": 100,
                "sector": "T", "source": "s",
            })
        p = tmp_path / "lowcov.csv"
        _write_events_csv(p, rows)
        stats = fa_calibrate.calibrate(
            events_csv=p,
            out_fits=tmp_path / "fits.json",
            out_summary=tmp_path / "summary.md",
        )
        # PE has 2/10 = 20% coverage, < 50%, should be excluded
        assert "pe" in stats["excluded_low_coverage"]


# ==========================================================================
# TestDirectionSanity
# ==========================================================================

class TestDirectionSanity:
    def test_increasing_direction_respected(self, good_events_csv, tmp_path):
        """ROE with clean monotone-up data should fit as increasing."""
        stats = fa_calibrate.calibrate(
            events_csv=good_events_csv,
            out_fits=tmp_path / "fits.json",
            out_summary=tmp_path / "summary.md",
        )
        fitted = {f["metric"]: f for f in stats["fitted"]}
        assert "roe" in fitted
        assert fitted["roe"]["direction"] == "↑"

    def test_decreasing_direction_respected(self, good_events_csv, tmp_path):
        """PE direction should render as ↓ in summary."""
        stats = fa_calibrate.calibrate(
            events_csv=good_events_csv,
            out_fits=tmp_path / "fits.json",
            out_summary=tmp_path / "summary.md",
        )
        fitted = {f["metric"]: f for f in stats["fitted"]}
        assert "pe" in fitted
        assert fitted["pe"]["direction"] == "↓"

    def test_wrong_direction_excluded(self, tmp_path):
        """If data says 'higher ROE → LOWER return' (inverted), the
        fit is forced increasing; it'll become a constant and the
        sanity check should exclude it as degenerate."""
        random.seed(7)
        rows = []
        for i in range(40):
            sym = f"SYM{i:03d}"
            for q in range(5):
                # ANTI-correlated: higher ROE gives LOWER return
                roe = random.uniform(0, 0.4)
                ret = -roe + random.gauss(0, 0.001)
                rows.append({
                    "symbol": sym, "period_end": "2020-01-01",
                    "filed_at": "2020-01-15",
                    "metric": "roe", "metric_value": roe,
                    "forward_return_60d": ret,
                    "forward_price_from": 100, "forward_price_to": 100,
                    "sector": "T", "source": "s",
                })
        p = tmp_path / "anti.csv"
        _write_events_csv(p, rows)
        stats = fa_calibrate.calibrate(
            events_csv=p,
            out_fits=tmp_path / "fits.json",
            out_summary=tmp_path / "summary.md",
        )
        # Anti-correlated data forced-increasing PAV → all pooled to
        # one mean → degenerate fit → sanity excludes
        fitted_names = {f["metric"] for f in stats["fitted"]}
        assert "roe" not in fitted_names
        # Should be flagged in excluded_sanity
        sanity_excluded = {pair[0] for pair in stats["excluded_sanity"]}
        assert "roe" in sanity_excluded


# ==========================================================================
# TestOutputJson
# ==========================================================================

class TestOutputJson:
    def test_json_loadable_by_engine_scoring_calibrated(
        self, good_events_csv, tmp_path,
    ):
        """The fits JSON must be loadable by the runtime scorer."""
        out_fits = tmp_path / "fits.json"
        fa_calibrate.calibrate(
            events_csv=good_events_csv, out_fits=out_fits,
            out_summary=tmp_path / "summary.md",
        )
        from research.isotonic import load_isotonic_fits_json
        fits = load_isotonic_fits_json(out_fits)
        assert "roe" in fits
        assert "pe" in fits

        # And the scorer can actually use it
        from engine.scoring_calibrated import (
            score_metric_calibrated, reset_fits_cache,
        )
        reset_fits_cache()
        hi = score_metric_calibrated("roe", 0.30, fits=fits)
        lo = score_metric_calibrated("roe", -0.02, fits=fits)
        assert hi is not None and lo is not None
        assert hi > lo

    def test_summary_markdown_has_all_fitted(self, good_events_csv, tmp_path):
        fa_calibrate.calibrate(
            events_csv=good_events_csv,
            out_fits=tmp_path / "fits.json",
            out_summary=tmp_path / "summary.md",
        )
        md = (tmp_path / "summary.md").read_text()
        assert "`roe`" in md
        assert "`pe`" in md
        assert "`net_margin`" in md


# ==========================================================================
# TestUnknownMetricsSkipped
# ==========================================================================

class TestUnknownMetricsSkipped:
    def test_unknown_metric_in_stats_excluded_unknown(self, tmp_path):
        """A metric name not in METRIC_DIRECTIONS should be listed in
        stats['excluded_unknown'] and NOT fit."""
        rows = []
        # Enough samples for fit + registry-known metric
        for i in range(30):
            rows.append({
                "symbol": f"S{i}", "period_end": "2020-01-01",
                "filed_at": "2020-01-15",
                "metric": "roe", "metric_value": 0.05 + 0.01 * i,
                "forward_return_60d": 0.01 * i,
                "forward_price_from": 100, "forward_price_to": 100,
                "sector": "T", "source": "s",
            })
        # And 30 rows of a totally-unknown metric
        for i in range(30):
            rows.append({
                "symbol": f"S{i}", "period_end": "2020-01-01",
                "filed_at": "2020-01-15",
                "metric": "unicorn_factor", "metric_value": i * 0.1,
                "forward_return_60d": 0.001 * i,
                "forward_price_from": 100, "forward_price_to": 100,
                "sector": "T", "source": "s",
            })
        p = tmp_path / "mix.csv"
        _write_events_csv(p, rows)
        stats = fa_calibrate.calibrate(
            events_csv=p, out_fits=tmp_path / "fits.json",
            out_summary=tmp_path / "summary.md",
        )
        fitted_names = {f["metric"] for f in stats["fitted"]}
        assert "roe" in fitted_names
        assert "unicorn_factor" in stats["excluded_unknown"]
        assert "unicorn_factor" not in fitted_names


# ==========================================================================
# TestCli
# ==========================================================================

class TestCli:
    def test_missing_events_csv_fails(self, tmp_path):
        argv = [
            f"--events={tmp_path / 'does-not-exist.csv'}",
            f"--out-fits={tmp_path / 'fits.json'}",
            f"--out-summary={tmp_path / 'summary.md'}",
        ]
        with pytest.raises(FileNotFoundError):
            fa_calibrate.main(argv)

    def test_cli_end_to_end(self, good_events_csv, tmp_path):
        argv = [
            f"--events={good_events_csv}",
            f"--out-fits={tmp_path / 'fits.json'}",
            f"--out-summary={tmp_path / 'summary.md'}",
            "--log-level=WARNING",
        ]
        rc = fa_calibrate.main(argv)
        assert rc == 0
        assert (tmp_path / "fits.json").exists()
        assert (tmp_path / "summary.md").exists()

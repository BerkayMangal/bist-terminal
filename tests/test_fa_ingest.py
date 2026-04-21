"""Tests for scripts/ingest_fa_for_calibration.py.

The script is tested via its fetcher abstraction (synthetic path) —
the real borsapy path requires a network + module that isn't available
in CI. Core pipeline logic (metric derivation, forward-return lookup,
checkpoint resume, CSV shape) is exercised via the synthetic fetcher.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest


# Load scripts/ingest_fa_for_calibration.py as a module
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "ingest_fa_for_calibration.py"
)
_spec = importlib.util.spec_from_file_location("fa_ingest", _SCRIPT_PATH)
fa_ingest = importlib.util.module_from_spec(_spec)
sys.modules["fa_ingest"] = fa_ingest
_spec.loader.exec_module(fa_ingest)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Isolated DB per test."""
    db = tmp_path / "fa_ingest.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
    import infra.storage
    infra.storage._local = threading.local()
    infra.storage.DB_PATH = str(db)
    from infra.storage import init_db
    init_db()
    yield db


class TestMetricRegistry:
    """METRIC_REGISTRY must cover the directions registered in
    engine/scoring_calibrated.py:METRIC_DIRECTIONS."""

    def test_all_registered_metrics_have_directions(self):
        from engine.scoring_calibrated import METRIC_DIRECTIONS
        for mname, direction, _hint in fa_ingest.METRIC_REGISTRY:
            assert mname in METRIC_DIRECTIONS, \
                f"Registry metric '{mname}' missing from METRIC_DIRECTIONS"
            assert METRIC_DIRECTIONS[mname] == direction, \
                f"{mname} direction mismatch: ingest={direction} vs " \
                f"METRIC_DIRECTIONS={METRIC_DIRECTIONS[mname]}"


class TestSyntheticFetcher:
    """Dry-run fetcher produces deterministic, shape-correct output."""

    def test_returns_nonempty_for_valid_range(self):
        fetcher = fa_ingest.make_synthetic_fetcher()
        quarters = fetcher("THYAO", date(2020, 1, 1), date(2021, 12, 31))
        # 8 quarter-ends in 2020-2021
        assert len(quarters) == 8

    def test_deterministic(self):
        """Same (symbol, date range) must yield same numbers."""
        fetcher1 = fa_ingest.make_synthetic_fetcher()
        fetcher2 = fa_ingest.make_synthetic_fetcher()
        q1 = fetcher1("AKBNK", date(2021, 1, 1), date(2021, 12, 31))
        q2 = fetcher2("AKBNK", date(2021, 1, 1), date(2021, 12, 31))
        assert q1 == q2

    def test_different_symbols_give_different_values(self):
        fetcher = fa_ingest.make_synthetic_fetcher()
        q_a = fetcher("THYAO", date(2021, 1, 1), date(2021, 12, 31))
        q_b = fetcher("AKBNK", date(2021, 1, 1), date(2021, 12, 31))
        assert q_a[0]["income"]["revenue"] != q_b[0]["income"]["revenue"]

    def test_statement_shape(self):
        fetcher = fa_ingest.make_synthetic_fetcher()
        q = fetcher("THYAO", date(2021, 1, 1), date(2021, 12, 31))[0]
        assert "period_end" in q and isinstance(q["period_end"], date)
        assert "filed_at" in q and isinstance(q["filed_at"], date)
        # filed_at must be AFTER period_end (KAP T+45 rule)
        assert q["filed_at"] > q["period_end"]
        assert (q["filed_at"] - q["period_end"]).days >= 40
        for key in ("income", "balance", "cashflow", "fast"):
            assert key in q and isinstance(q[key], dict)


class TestDeriveMetrics:
    """_derive_metrics_from_statements produces valid metric dicts."""

    def test_roe_net_margin_computed(self):
        q = {
            "income": {"revenue": 1000, "net_income": 100, "ebit": 150,
                       "interest_expense": 30,
                       "gross_profit": 300, "operating_income": 150},
            "balance": {"equity": 500, "total_debt": 200, "cash": 50,
                        "current_assets": 400, "current_liabilities": 200,
                        "total_assets": 700},
            "cashflow": {"free_cashflow": 60, "operating_cf": 90},
            "fast": {"market_cap": 2000},
        }
        m = fa_ingest._derive_metrics_from_statements(q)
        # ROE = (net_income / equity) * 4 (annualized)
        assert m["roe"] == pytest.approx(0.8)  # 100/500 * 4
        # Net margin = 100/1000 = 0.1
        assert m["net_margin"] == pytest.approx(0.1)
        # Current ratio = 400/200 = 2.0
        assert m["current_ratio"] == pytest.approx(2.0)
        # P/E = market_cap / (net_income * 4) = 2000 / 400 = 5
        assert m["pe"] == pytest.approx(5.0)
        # P/B = market_cap / equity = 2000 / 500 = 4
        assert m["pb"] == pytest.approx(4.0)

    def test_missing_inputs_skipped(self):
        """Metrics missing required inputs should be absent (not NaN)."""
        q = {
            "income": {"revenue": 1000, "net_income": None},  # no NI
            "balance": {}, "cashflow": {}, "fast": {},
        }
        m = fa_ingest._derive_metrics_from_statements(q)
        # Without net_income, ROE/net_margin must be absent
        assert "roe" not in m
        assert "net_margin" not in m

    def test_revenue_growth_needs_prev_year_q(self):
        this_q = {
            "income": {"revenue": 1200, "net_income": 100},
            "balance": {"equity": 500}, "cashflow": {}, "fast": {},
        }
        prev_q = {
            "income": {"revenue": 1000, "net_income": 80},
            "balance": {"equity": 400}, "cashflow": {}, "fast": {},
        }
        m_no_prev = fa_ingest._derive_metrics_from_statements(this_q)
        m_with = fa_ingest._derive_metrics_from_statements(this_q, prev_year_q=prev_q)
        assert "revenue_growth" not in m_no_prev
        assert m_with["revenue_growth"] == pytest.approx(0.2)  # 20%


class TestForwardReturn:
    """_forward_return_60d uses PIT price_history correctly."""

    def test_returns_none_with_no_prices(self, fresh_db):
        """When price_history_pit is empty, function returns None
        (not a crash)."""
        r = fa_ingest._forward_return_60d("THYAO", date(2020, 6, 1))
        assert r is None

    def test_returns_fraction_when_prices_present(self, fresh_db):
        from infra.pit import save_price
        # Filing date 2020-06-01, prices either side
        save_price("THYAO", date(2020, 6, 1), "test", close=100.0)
        save_price("THYAO", date(2020, 6, 1) + timedelta(days=60),
                   "test", close=115.0)
        r = fa_ingest._forward_return_60d("THYAO", date(2020, 6, 1))
        assert r is not None
        ret, pf, pt = r
        assert ret == pytest.approx(0.15)
        assert pf == 100.0
        assert pt == 115.0


class TestCheckpointResume:
    """Script is idempotent + resumable via checkpoint JSON."""

    def test_load_missing_checkpoint_returns_empty(self, tmp_path):
        cp = fa_ingest._load_checkpoint(tmp_path / "nope.json")
        assert cp.completed_symbols == []
        assert cp.total_events == 0

    def test_load_write_roundtrip(self, tmp_path):
        cp = fa_ingest.Checkpoint(
            completed_symbols=["THYAO", "AKBNK"],
            total_events=500,
            errors={"BIMAS": "timeout"},
        )
        p = tmp_path / "cp.json"
        fa_ingest._write_checkpoint(p, cp)
        cp2 = fa_ingest._load_checkpoint(p)
        assert cp2.completed_symbols == ["THYAO", "AKBNK"]
        assert cp2.total_events == 500
        assert cp2.errors == {"BIMAS": "timeout"}

    def test_corrupt_checkpoint_starts_fresh(self, tmp_path):
        p = tmp_path / "cp.json"
        p.write_text("not valid json {")
        cp = fa_ingest._load_checkpoint(p)
        assert cp.completed_symbols == []


class TestIngestDriverEndToEnd:
    """Full ingest_symbols pipeline with synthetic fetcher + seeded
    prices produces a valid CSV."""

    def test_produces_events_csv(self, fresh_db, tmp_path):
        # Seed synthetic prices so forward_return_60d has data
        fa_ingest._seed_synthetic_prices(
            ["THYAO"], date(2020, 1, 1), date(2022, 6, 30),
        )
        out = tmp_path / "events.csv"
        cp = tmp_path / "cp.json"
        fetcher = fa_ingest.make_synthetic_fetcher()
        n_events, n_failed = fa_ingest.ingest_symbols(
            ["THYAO"], date(2020, 1, 1), date(2021, 12, 31),
            fetcher, out, cp, sleep_between_symbols=0,
        )
        assert n_failed == 0
        assert n_events > 0
        assert out.exists()

        with out.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == n_events
        # Column shape
        assert set(rows[0].keys()) == {
            "symbol", "period_end", "filed_at", "metric",
            "metric_value", "forward_return_60d",
            "forward_price_from", "forward_price_to",
            "sector", "source",
        }

    def test_checkpoint_resume_skips_done(self, fresh_db, tmp_path):
        fa_ingest._seed_synthetic_prices(
            ["THYAO", "AKBNK"], date(2020, 1, 1), date(2021, 12, 31),
        )
        out = tmp_path / "events.csv"
        cp = tmp_path / "cp.json"
        fetcher = fa_ingest.make_synthetic_fetcher()

        # First run: just THYAO
        fa_ingest.ingest_symbols(
            ["THYAO"], date(2020, 1, 1), date(2021, 6, 30),
            fetcher, out, cp, sleep_between_symbols=0,
        )
        n_after_first = sum(1 for _ in open(out))

        # Second run: both symbols, THYAO already in checkpoint -> skip
        n_events, _ = fa_ingest.ingest_symbols(
            ["THYAO", "AKBNK"], date(2020, 1, 1), date(2021, 6, 30),
            fetcher, out, cp, sleep_between_symbols=0,
        )
        n_after_second = sum(1 for _ in open(out))

        # Second run only added AKBNK's rows
        assert n_after_second > n_after_first
        # Checkpoint has both
        cp_data = json.loads(cp.read_text())
        assert set(cp_data["completed_symbols"]) == {"THYAO", "AKBNK"}

    def test_reset_checkpoint_cli_arg(self, fresh_db, tmp_path, monkeypatch):
        """--reset-checkpoint deletes both files before run."""
        out = tmp_path / "events.csv"
        cp = tmp_path / "cp.json"
        out.write_text("stale,csv,data\n")
        cp.write_text('{"completed_symbols":["STALE"],"total_events":9,"errors":{}}')

        argv = [
            "--dry-run", "--symbols=THYAO",
            "--start=2020-01-01", "--end=2020-06-30",
            f"--out={out}", f"--checkpoint={cp}",
            "--sleep-between-symbols=0", "--reset-checkpoint",
            "--log-level=WARNING",
        ]
        monkeypatch.setenv("BISTBULL_DB_PATH", str(fresh_db))
        rc = fa_ingest.main(argv)
        assert rc == 0
        # CSV should no longer have 'stale' line
        assert "stale" not in out.read_text()


class TestCliParsing:
    def test_bist30_resolves_to_30_symbols(self):
        syms = fa_ingest._parse_symbols_spec("BIST30")
        from config import UNIVERSE_BIST30
        assert syms == list(UNIVERSE_BIST30)

    def test_comma_list(self):
        syms = fa_ingest._parse_symbols_spec("THYAO,AKBNK, BIMAS ")
        assert syms == ["THYAO", "AKBNK", "BIMAS"]

    def test_all_resolves_to_universe(self):
        syms = fa_ingest._parse_symbols_spec("ALL")
        from config import UNIVERSE
        assert len(syms) == len(UNIVERSE)

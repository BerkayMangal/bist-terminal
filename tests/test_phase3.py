"""Phase 3 tests: labeler, validator, signals, coverage, compare_sources,
ingest (threaded + real-path with mocked fetcher), universe audit migration.
"""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def p3_db(tmp_path, monkeypatch):
    """Fresh DB; reset storage module-level thread-local."""
    db_path = tmp_path / "p3.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db_path))
    import infra.storage
    infra.storage._local = threading.local()
    infra.storage.DB_PATH = str(db_path)
    from infra.storage import init_db
    init_db()
    yield db_path


# ========== Universe audit (migration 005) ==========

class TestUniverseAudit:
    def test_source_url_column_present(self, p3_db):
        from infra.storage import _get_conn
        cols = {r[1] for r in _get_conn().execute(
            "PRAGMA table_info(universe_history)").fetchall()}
        assert "source_url" in cols

    def test_valid_reasons(self):
        from infra.pit import VALID_UNIVERSE_REASONS
        assert VALID_UNIVERSE_REASONS == {
            "approximate", "addition", "removal", "verified"}

    def test_loader_rejects_invalid_reason(self, p3_db, tmp_path):
        from infra.pit import load_universe_history_csv
        bad = tmp_path / "bad.csv"
        bad.write_text(
            "universe_name,symbol,from_date,to_date,reason,source_url\n"
            "BIST30,TEST,2020-01-01,,unknown_reason,\n"
        )
        with pytest.raises(ValueError, match="invalid reason"):
            load_universe_history_csv(bad)

    def test_loader_requires_source_url_for_non_approximate(self, p3_db, tmp_path):
        from infra.pit import load_universe_history_csv
        bad = tmp_path / "bad.csv"
        bad.write_text(
            "universe_name,symbol,from_date,to_date,reason,source_url\n"
            "BIST30,TEST,2020-01-01,,verified,\n"
        )
        with pytest.raises(ValueError, match="requires source_url"):
            load_universe_history_csv(bad)

    def test_loader_accepts_verified_with_source_url(self, p3_db, tmp_path):
        from infra.pit import load_universe_history_csv, get_universe_at
        ok = tmp_path / "ok.csv"
        ok.write_text(
            "universe_name,symbol,from_date,to_date,reason,source_url\n"
            "BIST30,TEST,2020-01-01,,verified,https://kap.org.tr/foo\n"
        )
        n = load_universe_history_csv(ok)
        assert n == 1
        assert "TEST" in get_universe_at("BIST30", "2023-01-01")


# ========== Preferred-source fundamentals (S4) ==========

class TestGetFundamentalsPreferred:
    def _seed_multi_source(self):
        from infra.pit import save_fundamental
        # Same period, three sources, different values
        save_fundamental("THYAO", "2022-12-31", "2023-02-10", "synthetic",
                         "net_income", 1.0e9)
        save_fundamental("THYAO", "2022-12-31", "2023-02-10", "borsapy",
                         "net_income", 2.0e9)
        save_fundamental("THYAO", "2022-12-31", "2023-02-10", "kap",
                         "net_income", 3.0e9)

    def test_default_priority_kap_wins(self, p3_db):
        from infra.pit import get_fundamentals_at_preferred
        self._seed_multi_source()
        r = get_fundamentals_at_preferred("THYAO", "2023-06-01")
        assert r["net_income"]["source"] == "kap"
        assert r["net_income"]["value"] == 3.0e9

    def test_custom_priority_synthetic_only(self, p3_db):
        from infra.pit import get_fundamentals_at_preferred
        self._seed_multi_source()
        r = get_fundamentals_at_preferred(
            "THYAO", "2023-06-01", source_priority=("synthetic",))
        assert r["net_income"]["source"] == "synthetic"
        assert r["net_income"]["value"] == 1.0e9

    def test_custom_priority_borsapy_before_kap(self, p3_db):
        from infra.pit import get_fundamentals_at_preferred
        self._seed_multi_source()
        r = get_fundamentals_at_preferred(
            "THYAO", "2023-06-01", source_priority=("borsapy", "kap"))
        assert r["net_income"]["source"] == "borsapy"
        assert r["net_income"]["value"] == 2.0e9

    def test_respects_look_ahead_guard(self, p3_db):
        from infra.pit import get_fundamentals_at_preferred
        self._seed_multi_source()
        # Query before any filings -- empty
        r = get_fundamentals_at_preferred("THYAO", "2023-02-09")
        assert r == {}


# ========== OHLCV PIT (migration 006) ==========

class TestPriceHistoryPIT:
    def test_save_and_get_prices(self, p3_db):
        from infra.pit import save_price, get_prices
        save_price("THYAO", "2024-01-02", "synthetic",
                   100.0, 102.0, 99.5, 101.0, 1_000_000, 101.0)
        save_price("THYAO", "2024-01-03", "synthetic",
                   101.0, 103.5, 100.0, 103.0, 1_200_000, 103.0)
        bars = get_prices("THYAO", "2024-01-01", "2024-01-05")
        assert len(bars) == 2
        assert bars[0]["close"] == 101.0
        assert bars[1]["close"] == 103.0

    def test_get_price_at_or_before_returns_most_recent(self, p3_db):
        from infra.pit import save_price, get_price_at_or_before
        save_price("THYAO", "2024-01-02", "synthetic", close=100.0)
        save_price("THYAO", "2024-01-05", "synthetic", close=105.0)
        # Weekend -- most recent trading day is Jan 5 (Friday)
        bar = get_price_at_or_before("THYAO", "2024-01-07")
        assert bar["trade_date"] == "2024-01-05"
        assert bar["close"] == 105.0

    def test_multi_source_coexist(self, p3_db):
        from infra.pit import save_price, get_prices
        save_price("THYAO", "2024-01-02", "synthetic", close=100.0)
        save_price("THYAO", "2024-01-02", "borsapy", close=101.5)
        all_bars = get_prices("THYAO", "2024-01-01", "2024-01-05")
        assert len(all_bars) == 2  # both sources
        syn = get_prices("THYAO", "2024-01-01", "2024-01-05",
                        source="synthetic")
        assert len(syn) == 1
        assert syn[0]["close"] == 100.0


# ========== Threaded + real-path ingest ==========

class TestThreadedIngest:
    def test_real_path_with_mock_fetcher(self, p3_db, tmp_path, monkeypatch):
        """Phase 4 FAZ 4.0.1: _fetch_real now consumes the real
        borsapy API shape, which is pandas DataFrames from
        Ticker.get_income_stmt() / get_balance_sheet() / get_cashflow().
        Columns are period-end Timestamps, rows are Turkish KAP line
        names matching data/providers.py:IS_MAP/BS_MAP."""
        import pandas as pd
        from research.ingest_filings import ingest
        monkeypatch.setattr(
            "research.ingest_filings.CHECKPOINT_PATH", tmp_path / "ck.json")

        # Build DataFrames in the real borsapy shape
        periods = [pd.Timestamp("2023-03-31"), pd.Timestamp("2023-06-30")]
        income = pd.DataFrame({
            periods[0]: [1.0e9, 1.0e8],
            periods[1]: [1.1e9, 1.2e8],
        }, index=["Satış Gelirleri", "DÖNEM KARI (ZARARI)"])
        balance = pd.DataFrame({
            periods[0]: [2.0e9, 3.0e8, 1.5e8],
            periods[1]: [2.1e9, 3.2e8, 1.6e8],
        }, index=["Ana Ortaklığa Ait Özkaynaklar",
                  "Uzun Vadeli Finansal Borçlar",
                  "Kısa Vadeli Finansal Borçlar"])

        def mock_fetcher(symbol):
            return {"income": income, "balance": balance, "cashflow": None}

        r = ingest(
            symbols=["THYAO", "AKBNK", "ISCTR"],
            from_date=date(2022, 1, 1), to_date=date(2024, 1, 1),
            dry_run=False, fetcher=mock_fetcher,
            threaded=True, max_workers=3,
        )
        assert r["totals"]["symbols"] == 3
        # 2 filings per symbol, 4 metrics each = 8 rows per symbol
        assert r["totals"]["filings"] == 3 * 2
        assert r["totals"]["rows"] == 3 * 2 * 4
        assert len(r["completed"]) == 3

    def test_per_symbol_error_isolation(self, p3_db, tmp_path, monkeypatch):
        """Error in one symbol's fetcher call must not stop other symbols."""
        import pandas as pd
        from research.ingest_filings import ingest
        monkeypatch.setattr(
            "research.ingest_filings.CHECKPOINT_PATH", tmp_path / "ck.json")

        call_count = {"n": 0}
        lock = threading.Lock()
        ok_income = pd.DataFrame({
            pd.Timestamp("2023-03-31"): [1.0e9],
        }, index=["Satış Gelirleri"])

        def flaky_fetcher(symbol):
            with lock:
                call_count["n"] += 1
            if symbol == "AKBNK":
                raise RuntimeError("simulated fetch error")
            return {"income": ok_income, "balance": None, "cashflow": None}

        r = ingest(
            symbols=["THYAO", "AKBNK", "ISCTR"],
            from_date=date(2022, 1, 1), to_date=date(2024, 1, 1),
            dry_run=False, fetcher=flaky_fetcher,
            threaded=True, max_workers=3,
        )
        # Other symbols should have completed
        assert "AKBNK" in r["errors"]
        assert "AKBNK" not in r["completed"]
        assert {"THYAO", "ISCTR"} <= set(r["completed"])

    def test_ohlcv_threaded_ingest(self, p3_db, tmp_path, monkeypatch):
        from research.ingest_prices import ingest as ingest_px
        monkeypatch.setattr(
            "research.ingest_prices.CHECKPOINT_PATH", tmp_path / "ckp.json")

        r = ingest_px(
            symbols=["A", "B", "C"],
            from_date=date(2024, 1, 1), to_date=date(2024, 1, 31),
            dry_run=True, threaded=True, max_workers=3,
        )
        # Jan 2024 has 23 weekdays
        assert r["totals"]["symbols"] == 3
        assert r["totals"]["bars"] >= 3 * 20


# ========== Labeler (survivorship-aware) ==========

class TestLabeler:
    def _seed_prices(self, symbols: list[str], start_close: float = 100.0):
        from infra.pit import save_price
        d = date(2023, 1, 2)
        while d <= date(2024, 6, 30):
            if d.weekday() < 5:
                for i, s in enumerate(symbols):
                    px = start_close * (1.0 + 0.001 * i + 0.0005 * (d - date(2023, 1, 2)).days)
                    save_price(s, d, "synthetic", close=px)
            d += timedelta(days=1)

    def test_forward_returns_basic(self, p3_db, tmp_path, monkeypatch):
        from infra.pit import load_universe_history_csv
        # seed a tiny universe
        csv = tmp_path / "u.csv"
        csv.write_text(
            "universe_name,symbol,from_date,to_date,reason,source_url\n"
            "BIST30,AAAAA,2022-01-01,,approximate,\n"
        )
        load_universe_history_csv(csv)
        self._seed_prices(["AAAAA"])

        from research.labeler import compute_forward_returns
        r = compute_forward_returns(
            "AAAAA", "2023-06-01", universe="BIST30", today=date(2024, 6, 30))
        # All horizons should resolve (drift is upward, so positive)
        assert all(v is not None for v in r.values())
        assert r["return_20d"] > 0
        assert r["return_60d"] > r["return_20d"]

    def test_survivorship_filter_skips_non_members(self, p3_db, tmp_path):
        from infra.pit import load_universe_history_csv
        from research.labeler import compute_forward_returns
        # Symbol MEMBR is in BIST30 but symbol OTHER is NOT
        csv = tmp_path / "u.csv"
        csv.write_text(
            "universe_name,symbol,from_date,to_date,reason,source_url\n"
            "BIST30,MEMBR,2022-01-01,,approximate,\n"
        )
        load_universe_history_csv(csv)
        self._seed_prices(["MEMBR", "OTHER"])

        r_member = compute_forward_returns(
            "MEMBR", "2023-06-01", universe="BIST30", today=date(2024, 6, 30))
        r_other = compute_forward_returns(
            "OTHER", "2023-06-01", universe="BIST30", today=date(2024, 6, 30))
        assert any(v is not None for v in r_member.values())
        assert all(v is None for v in r_other.values())

    def test_future_horizons_return_none(self, p3_db, tmp_path):
        from infra.pit import load_universe_history_csv
        from research.labeler import compute_forward_returns
        csv = tmp_path / "u.csv"
        csv.write_text(
            "universe_name,symbol,from_date,to_date,reason,source_url\n"
            "BIST30,AAA,2022-01-01,,approximate,\n"
        )
        load_universe_history_csv(csv)
        self._seed_prices(["AAA"])

        # as_of 10 days before today -- 60d horizon hasn't materialized yet
        today = date(2023, 6, 15)
        r = compute_forward_returns(
            "AAA", "2023-06-10", universe="BIST30", today=today)
        # 5d might be close to materialized, 60d definitely not
        assert r["return_60d"] is None


# ========== Validator ==========

class TestValidator:
    def test_decision_rules(self):
        from research.validator import _decide
        # keep_strong
        d, _ = _decide(sharpe=1.5, t_stat=2.5)
        assert d == "keep_strong"
        # keep_weak (marginal)
        d, _ = _decide(sharpe=0.5, t_stat=1.7)
        assert d == "keep_weak"
        # kill (low Sharpe)
        d, _ = _decide(sharpe=0.1, t_stat=2.5)
        assert d == "kill"
        # kill (low t)
        d, _ = _decide(sharpe=1.5, t_stat=1.0)
        assert d == "kill"
        # None -> kill
        d, _ = _decide(sharpe=None, t_stat=1.0)
        assert d == "kill"

    def test_enumerate_events_respects_universe(self, p3_db, tmp_path):
        from infra.pit import load_universe_history_csv
        from research.validator import enumerate_events
        csv = tmp_path / "u.csv"
        csv.write_text(
            "universe_name,symbol,from_date,to_date,reason,source_url\n"
            "BIST30,X,2023-01-01,,approximate,\n"
            "BIST30,Y,2023-01-01,2023-07-01,approximate,\n"
        )
        load_universe_history_csv(csv)

        # Detector fires for everything
        evs = enumerate_events(
            detector=lambda s, d: True, universe="BIST30",
            from_date=date(2023, 5, 1), to_date=date(2023, 9, 30),
            sample_every_n_days=30,
        )
        syms = {e["symbol"] for e in evs}
        # Y was removed 2023-07-01 so shouldn't appear in August/September events
        y_dates = sorted(e["as_of"] for e in evs if e["symbol"] == "Y")
        x_dates = sorted(e["as_of"] for e in evs if e["symbol"] == "X")
        assert all(d < "2023-07-01" for d in y_dates)
        assert any(d >= "2023-07-01" for d in x_dates)

    def test_write_report_emits_json_and_md(self, p3_db, tmp_path):
        from research.validator import ValidatorResult, write_report
        r = ValidatorResult(
            signal="Test Signal", universe="BIST30",
            from_date="2023-01-01", to_date="2024-01-01",
            n_trades=100, hit_rate_5d=0.55, hit_rate_20d=0.58,
            avg_return_5d=0.002, avg_return_20d=0.012,
            std_return_20d=0.05, t_stat_20d=2.4, sharpe_20d_ann=1.2,
            ir_vs_benchmark_20d=0.8, benchmark_symbol="XU100",
            decision="keep_strong", notes=["good signal"],
        )
        jp, mp = write_report(r, tmp_path)
        assert jp.exists() and mp.exists()
        data = json.loads(jp.read_text())
        assert data["signal"] == "Test Signal"
        assert data["decision"] == "keep_strong"
        assert "keep_strong" in mp.read_text()
        assert "100" in mp.read_text()  # n_trades


# ========== Signals (detector smoke) ==========

class TestSignals:
    def _seed_golden_cross_data(self, symbol: str):
        """Seed prices that trigger a Golden Cross late in the series.

        Design: 200 days of gentle decline (100→50) to push MA200 down,
        then 100 days of steady rally (50→150). MA50 overtakes MA200
        somewhere around day 260-280. Detector requires >=205 closes to
        compare, so the cross is detectable in the last ~35 days of
        the series.
        """
        from infra.pit import save_price
        d = date(2022, 9, 1)  # start far enough back to have 300 weekdays
        i = 0
        while i < 300:
            if d.weekday() < 5:
                if i < 200:
                    price = 100.0 - i * 0.25   # 100 -> 50
                else:
                    price = 50.0 + (i - 200) * 1.0  # 50 -> 150
                save_price(symbol, d, "synthetic", close=price)
                i += 1
            d += timedelta(days=1)

    def test_golden_cross_detector_fires_on_rally(self, p3_db):
        import research.signals as sigs
        self._seed_golden_cross_data("GCROSS")

        # 300 weekdays from 2022-09-01 ≈ late Oct 2023. The cross
        # happens around day 260-280 (about 4-5 weeks before the end).
        # Scan backwards from the series end looking for a fire.
        end_d = date(2023, 10, 27)
        fired_on_any = False
        d = end_d
        for _ in range(80):
            if d.weekday() < 5 and sigs.golden_cross("GCROSS", d,
                                                     price_source="synthetic"):
                fired_on_any = True
                break
            d -= timedelta(days=1)
        assert fired_on_any, "Golden Cross detector didn't fire on a seeded rally pattern"

    def test_stubbed_signals_return_false(self, p3_db):
        import research.signals as sigs
        # All stubs
        for name in ("ichimoku_kumo_breakout", "ichimoku_kumo_breakdown",
                    "ichimoku_tk_cross", "vcp_breakout",
                    "rectangle_breakout", "rectangle_breakdown",
                    "pivot_resistance_break", "pivot_support_break"):
            fn = getattr(sigs, name)
            assert fn("X", date(2023, 6, 1)) is False

    def test_signal_registry_has_17_entries(self):
        from research.signals import SIGNAL_DETECTORS
        assert len(SIGNAL_DETECTORS) == 17


# ========== Coverage report ==========

class TestCoverage:
    def test_compute_coverage_shape(self, p3_db):
        from infra.pit import save_fundamental
        from research.coverage import compute_coverage
        # Seed THYAO Q4 2022, Q1-Q4 2023, Q1 2024 = 6 filings × 4 metrics
        from datetime import date as D
        for q in [D(2022, 12, 31), D(2023, 3, 31), D(2023, 6, 30),
                  D(2023, 9, 30), D(2023, 12, 31), D(2024, 3, 31)]:
            for m in ("revenue", "net_income", "roe", "debt_to_equity"):
                save_fundamental("THYAO", q, q, "synthetic", m, 1.0)

        rows = compute_coverage(
            symbols=["THYAO"],
            from_date=date(2022, 10, 1), to_date=date(2024, 4, 1),
        )
        assert len(rows) == 4  # 1 symbol × 4 metrics
        for r in rows:
            assert r.coverage == 1.0  # all quarters filled
            assert not r.excluded_from_phase_4

    def test_exclude_threshold(self, p3_db):
        from infra.pit import save_fundamental
        from research.coverage import compute_coverage
        # Only fill 1 of 6 quarters -> 16% coverage -> excluded
        save_fundamental("SPARSE", date(2023, 3, 31),
                         date(2023, 5, 15), "synthetic", "revenue", 1.0)
        rows = compute_coverage(
            symbols=["SPARSE"],
            from_date=date(2022, 10, 1), to_date=date(2024, 4, 1),
        )
        rev_row = next(r for r in rows if r.metric == "revenue")
        assert rev_row.coverage < 0.5
        assert rev_row.excluded_from_phase_4

    def test_write_reports_emits_both(self, p3_db, tmp_path):
        from research.coverage import compute_coverage, write_coverage_reports
        rows = compute_coverage(
            symbols=["X"],
            from_date=date(2023, 1, 1), to_date=date(2024, 1, 1),
        )
        md, csvp = write_coverage_reports(
            rows, date(2023, 1, 1), date(2024, 1, 1),
            tmp_path, data_source="synthetic")
        assert md.exists() and csvp.exists()
        assert "synthetic" in md.read_text()


# ========== Compare sources ==========

class TestCompareSources:
    def test_detects_disagreement(self, p3_db):
        from infra.pit import save_fundamental
        from research.compare_sources import find_source_disagreements
        # Two sources, same period/metric, values differ by 20%
        save_fundamental("THYAO", "2022-12-31", "2023-02-10",
                         "borsapy", "net_income", 1.0e9)
        save_fundamental("THYAO", "2022-12-31", "2023-02-10",
                         "kap", "net_income", 1.2e9)
        diffs = find_source_disagreements(rel_tol=0.01)
        assert len(diffs) == 1
        assert diffs[0]["rel_diff"] > 0.15

    def test_below_tol_not_reported(self, p3_db):
        from infra.pit import save_fundamental
        from research.compare_sources import find_source_disagreements
        save_fundamental("THYAO", "2022-12-31", "2023-02-10",
                         "borsapy", "net_income", 1.000e9)
        save_fundamental("THYAO", "2022-12-31", "2023-02-10",
                         "kap", "net_income", 1.005e9)  # 0.5% diff
        diffs = find_source_disagreements(rel_tol=0.01)
        assert diffs == []


class TestKapFetchReal:
    """Phase 4 FAZ 4.0.1: _fetch_real parsing of real borsapy DataFrames
    (Turkish KAP line names, period-end Timestamp columns)."""

    def _df(self, rows: dict[str, list], periods: list[str]):
        import pandas as pd
        # rows: {line_name: [v_period0, v_period1, ...]}
        cols = [pd.Timestamp(p) for p in periods]
        return pd.DataFrame(rows, index=list(rows.keys())).T.set_index(
            pd.Index(list(rows.keys()))
        ) if False else pd.DataFrame(
            {col: [rows[name][i] for name in rows] for i, col in enumerate(cols)},
            index=list(rows.keys()),
        )

    def test_single_period_end_to_end(self, p3_db, tmp_path, monkeypatch):
        import pandas as pd
        from research.ingest_filings import _fetch_real
        income = pd.DataFrame({
            pd.Timestamp("2023-12-31"): [5.0e9, 1.2e9],
        }, index=["Satış Gelirleri", "DÖNEM KARI (ZARARI)"])
        balance = pd.DataFrame({
            pd.Timestamp("2023-12-31"): [2.0e10, 3.0e9, 1.5e9],
        }, index=[
            "Ana Ortaklığa Ait Özkaynaklar",
            "Uzun Vadeli Finansal Borçlar",
            "Kısa Vadeli Finansal Borçlar",
        ])

        def f(sym):
            return {"income": income, "balance": balance, "cashflow": None}

        fs = _fetch_real("THYAO", date(2023, 1, 1), date(2024, 1, 1), fetcher=f)
        assert len(fs) == 1
        m = fs[0]["metrics"]
        assert m["revenue"] == 5.0e9
        assert m["net_income"] == 1.2e9
        # ROE = 1.2e9 / 2.0e10 = 0.06
        assert abs(m["roe"] - 0.06) < 1e-9
        # D/E = (3.0e9 + 1.5e9) / 2.0e10 = 0.225
        assert abs(m["debt_to_equity"] - 0.225) < 1e-9
        # filed_at = period + 60 days
        assert fs[0]["filed_at"] == date(2023, 12, 31) + timedelta(days=60)

    def test_partial_row_name_match(self, p3_db):
        import pandas as pd
        from research.ingest_filings import _fetch_real
        # Row name contains the canonical substring but with extra tokens
        income = pd.DataFrame({
            pd.Timestamp("2023-12-31"): [8.0e8],
        }, index=["SÜRDÜRÜLEN FAALİYETLER DÖNEM KARI/ZARARI - Ana Ortaklık"])

        def f(sym):
            return {"income": income, "balance": None, "cashflow": None}

        fs = _fetch_real("X", date(2023, 1, 1), date(2024, 1, 1), fetcher=f)
        # Partial match on "SÜRDÜRÜLEN FAALİYETLER DÖNEM KARI/ZARARI"
        assert fs[0]["metrics"]["net_income"] == 8.0e8

    def test_period_window_filter(self, p3_db):
        import pandas as pd
        from research.ingest_filings import _fetch_real
        income = pd.DataFrame({
            pd.Timestamp("2019-12-31"): [1e9],  # outside window
            pd.Timestamp("2022-12-31"): [2e9],  # outside window
            pd.Timestamp("2023-06-30"): [3e9],  # inside
            pd.Timestamp("2024-12-31"): [4e9],  # outside
        }, index=["Satış Gelirleri"])

        def f(sym):
            return {"income": income, "balance": None, "cashflow": None}

        fs = _fetch_real("X", date(2023, 1, 1), date(2024, 1, 1), fetcher=f)
        assert len(fs) == 1
        assert fs[0]["period_end"] == date(2023, 6, 30)

    def test_empty_or_missing_dfs(self, p3_db):
        import pandas as pd
        from research.ingest_filings import _fetch_real

        # All None
        fs = _fetch_real("X", date(2023, 1, 1), date(2024, 1, 1),
                         fetcher=lambda s: {"income": None, "balance": None, "cashflow": None})
        assert fs == []

        # Empty DataFrame
        fs = _fetch_real("X", date(2023, 1, 1), date(2024, 1, 1),
                         fetcher=lambda s: {"income": pd.DataFrame(),
                                            "balance": None, "cashflow": None})
        assert fs == []

    def test_missing_equity_yields_none_ratios(self, p3_db):
        import pandas as pd
        from research.ingest_filings import _fetch_real
        # Only revenue + net_income; no balance sheet equity
        income = pd.DataFrame({
            pd.Timestamp("2023-12-31"): [5e9, 1e9],
        }, index=["Satış Gelirleri", "DÖNEM KARI (ZARARI)"])

        fs = _fetch_real("X", date(2023, 1, 1), date(2024, 1, 1),
                         fetcher=lambda s: {"income": income,
                                            "balance": None, "cashflow": None})
        assert fs[0]["metrics"]["revenue"] == 5e9
        assert fs[0]["metrics"]["net_income"] == 1e9
        assert fs[0]["metrics"]["roe"] is None
        assert fs[0]["metrics"]["debt_to_equity"] is None

    def test_bank_financial_group_propagated(self, p3_db, monkeypatch):
        """Sanity check: the default path would call get_income_stmt with
        financial_group='UFRS' for bank tickers. We can't exercise the
        real import path here, but we can verify the is_bank helper
        lookup reaches the right ticker."""
        from data.providers import is_bank
        assert is_bank("AKBNK") is True
        assert is_bank("GARAN") is True
        assert is_bank("THYAO") is False


class TestPortedSignals:
    """Phase 4 FAZ 4.0.2: golden-vector tests for the 8 signals ported
    from engine/technical.py. Each test seeds a price series designed
    to trigger exactly one signal and scans a short window for a fire."""

    def _seed_ohlc(self, symbol: str, bars: list[tuple[int, float, float, float, float]],
                   start: date = date(2022, 1, 3)):
        """Seed price bars. bars = list of (day_offset, open, high, low, close)."""
        from infra.pit import save_price
        for offset, o, h, l, c in bars:
            d = start
            count = 0
            while count < offset:
                d += timedelta(days=1)
                if d.weekday() < 5:
                    count += 1
            save_price(symbol, d, "synthetic",
                       open_=o, high=h, low=l, close=c, volume=1e6)

    def _seed_sequence(self, symbol: str, closes_and_hl: list[tuple[float, float, float]],
                       start: date = date(2022, 1, 3)):
        """Seed a sequence of weekday bars: [(close, high, low), ...]."""
        from infra.pit import save_price
        d = start
        i = 0
        while i < len(closes_and_hl):
            if d.weekday() < 5:
                c, h, l = closes_and_hl[i]
                save_price(symbol, d, "synthetic",
                           open_=c, high=h, low=l, close=c, volume=1e6)
                i += 1
            d += timedelta(days=1)

    def _scan_for_fire(self, detector, symbol: str, start: date,
                       days: int) -> list[str]:
        """Return list of trade_date ISO strings where detector fired
        during the first `days` weekdays starting at `start`."""
        import research.signals as sigs  # noqa
        fires = []
        d = start
        count = 0
        while count < days:
            if d.weekday() < 5:
                if detector(symbol, d, price_source="synthetic"):
                    fires.append(d.isoformat())
                count += 1
            d += timedelta(days=1)
        return fires

    def test_ichimoku_kumo_breakout_fires_on_rally(self, p3_db):
        """Decline from 100 to 50 over 80 bars, then rally to 150 over
        next 70 bars. Price crosses above the cloud during the rally."""
        import research.signals as sigs
        seq = []
        for i in range(150):
            if i < 80:
                c = 100.0 - i * 0.6
            else:
                c = 50.0 + (i - 80) * 1.4
            seq.append((c, c * 1.005, c * 0.995))
        self._seed_sequence("ICHIBO", seq)
        fires = self._scan_for_fire(sigs.ichimoku_kumo_breakout,
                                     "ICHIBO", date(2022, 1, 3), 150)
        assert fires, "ichimoku_kumo_breakout must fire on decline-then-rally"

    def test_ichimoku_kumo_breakdown_fires_on_reversal(self, p3_db):
        """Rally then decline; price crosses below the cloud."""
        import research.signals as sigs
        seq = []
        for i in range(150):
            if i < 80:
                c = 50.0 + i * 0.8
            else:
                c = 114.0 - (i - 80) * 1.0
            seq.append((c, c * 1.005, c * 0.995))
        self._seed_sequence("ICHIBD", seq)
        fires = self._scan_for_fire(sigs.ichimoku_kumo_breakdown,
                                     "ICHIBD", date(2022, 1, 3), 150)
        assert fires, "ichimoku_kumo_breakdown must fire on rally-then-decline"

    def test_ichimoku_tk_cross_fires(self, p3_db):
        """Downtrend then uptrend; tenkan(9) crosses kijun(26) upward."""
        import research.signals as sigs
        seq = []
        for i in range(60):
            if i < 30:
                c = 100.0 - i * 0.5
            else:
                c = 85.0 + (i - 30) * 0.8
            seq.append((c, c * 1.005, c * 0.995))
        self._seed_sequence("TKCROSS", seq)
        fires = self._scan_for_fire(sigs.ichimoku_tk_cross,
                                     "TKCROSS", date(2022, 1, 3), 60)
        assert fires, "ichimoku_tk_cross must fire on trend reversal"

    def test_vcp_breakout_fires_on_contraction_plus_break(self, p3_db):
        """50 bars volatile, 9 bars tight consolidation near 100, final
        bar breaks above."""
        import research.signals as sigs
        seq = []
        # Bars 0-49: high volatility around 100
        for i in range(50):
            sign = 1 if i % 4 < 2 else -1
            c = 100.0 + sign * 5
            seq.append((c, c + 3, c - 3))
        # Bars 50-58: tight consolidation 99-101
        for i in range(9):
            c = 100.0 + ((i % 2) * 0.3 - 0.15)
            seq.append((c, c + 0.2, c - 0.2))
        # Bar 59: breakout
        seq.append((103.0, 103.5, 100.5))
        self._seed_sequence("VCPSYM", seq)
        fires = self._scan_for_fire(sigs.vcp_breakout,
                                     "VCPSYM", date(2022, 1, 3), 60)
        assert fires, "vcp_breakout must fire when contraction + breakout present"

    def test_rectangle_breakout_fires(self, p3_db):
        """20 bars tight range 99-101, then close pushes above range_high * 0.998."""
        import research.signals as sigs
        seq = []
        # 20 tight bars (so range_high ≈ 100.6, range_low ≈ 99.4, range_pct < 0.08)
        for i in range(20):
            c = 100.0 + ((i % 2) * 0.3 - 0.15)
            seq.append((c, c + 0.2, c - 0.2))
        # One more tight bar (still inside range — this is the "yesterday"
        # that the detector compares against today)
        seq.append((100.0, 100.2, 99.8))
        # Breakout bar
        seq.append((102.0, 102.5, 100.5))
        self._seed_sequence("RECTUP", seq)
        fires = self._scan_for_fire(sigs.rectangle_breakout,
                                     "RECTUP", date(2022, 1, 3), 25)
        assert fires, "rectangle_breakout must fire"

    def test_rectangle_breakdown_fires(self, p3_db):
        """Mirror: tight range, then break below range_low * 1.002."""
        import research.signals as sigs
        seq = []
        for i in range(20):
            c = 100.0 + ((i % 2) * 0.3 - 0.15)
            seq.append((c, c + 0.2, c - 0.2))
        seq.append((100.0, 100.2, 99.8))
        seq.append((97.5, 99.5, 97.0))  # break below
        self._seed_sequence("RECTDN", seq)
        fires = self._scan_for_fire(sigs.rectangle_breakdown,
                                     "RECTDN", date(2022, 1, 3), 25)
        assert fires, "rectangle_breakdown must fire"

    def test_pivot_resistance_break_fires(self, p3_db):
        """Seed bars with a clear pivot-high at bar 15, then prices stay
        below until a break above in the bar AFTER the 60-bar lookback."""
        import research.signals as sigs
        seq = []
        for i in range(61):
            if i == 15:
                c, h, l = 118.0, 120.0, 117.0  # pivot high
            elif i == 60:
                c, h, l = 121.0, 122.0, 117.0  # breakout (today)
            else:
                c, h, l = 110.0 + ((i % 7) * 0.2), 111.0 + ((i % 7) * 0.2), 109.0
            seq.append((c, h, l))
        self._seed_sequence("PIVRES", seq)
        # The detector needs bar 60 to be "today"; scan around it
        fires = self._scan_for_fire(sigs.pivot_resistance_break,
                                     "PIVRES", date(2022, 1, 3), 70)
        assert fires, "pivot_resistance_break must fire above prior pivot high"

    def test_pivot_support_break_fires(self, p3_db):
        """Mirror: pivot low, then break below."""
        import research.signals as sigs
        seq = []
        for i in range(61):
            if i == 15:
                c, h, l = 82.0, 83.0, 80.0  # pivot low at 80
            elif i == 60:
                c, h, l = 78.0, 83.0, 77.0  # break below 80
            else:
                c, h, l = 90.0 - ((i % 7) * 0.2), 91.0, 89.0 - ((i % 7) * 0.2)
            seq.append((c, h, l))
        self._seed_sequence("PIVSUP", seq)
        fires = self._scan_for_fire(sigs.pivot_support_break,
                                     "PIVSUP", date(2022, 1, 3), 70)
        assert fires, "pivot_support_break must fire below prior pivot low"

    def test_registry_still_has_17(self):
        """After porting, SIGNAL_DETECTORS still has all 17 entries."""
        from research.signals import SIGNAL_DETECTORS
        assert len(SIGNAL_DETECTORS) == 17

    def test_ported_detectors_not_always_false(self, p3_db):
        """Regression guard: the ported detectors must NOT be the old
        'return False' stubs. Seed a reversal pattern (decline then
        rally) and verify the ported Ichimoku detectors fire at least
        once. Monotonic trends don't trigger crossover-based detectors
        because tenkan and kijun rise together -- we need a genuine
        reversal for a crossover to exist."""
        import research.signals as sigs
        # Decline + rally → TK cross and Kumo breakout both fire
        seq = []
        for i in range(150):
            if i < 80:
                c = 100.0 - i * 0.6
            else:
                c = 50.0 + (i - 80) * 1.4
            seq.append((c, c * 1.005, c * 0.995))
        self._seed_sequence("NOTSTUB", seq)
        # Both of these are ports from engine/technical.py -- the old
        # stubs returned False unconditionally.
        ported_names = ["Ichimoku Kumo Breakout", "Ichimoku TK Cross"]
        total_fires = 0
        for name in ported_names:
            fn = sigs.SIGNAL_DETECTORS[name]
            fires = self._scan_for_fire(fn, "NOTSTUB",
                                         date(2022, 1, 3), 150)
            total_fires += len(fires)
        assert total_fires > 0, \
            f"Ported detectors returned False everywhere; regression -- got {total_fires} fires"

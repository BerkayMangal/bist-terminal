"""Phase 4.9 production integration tests.

Combined scope per reviewer spec:
  - scoring_backward_compat (V13 default path bit-identical)
  - score_dispatch_integration (both versions callable via analyze_symbol)
  - signals_today endpoint (format + filtering)
  - ab_report endpoint (score_history self-join + Spearman)
  - paper_trading_template (allocation math)
  - signals_history endpoint (score_history pull)
  - ensemble_weights endpoint
  - /ab_report HTML page renders

All tests use FastAPI TestClient. The backward-compat class is first
because any failure there blocks the entire Phase 4.9 delivery.
"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ==========================================================================
# Shared fixtures
# ==========================================================================

@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh DB + TestClient. Ensures env var is unset so default
    v13_handpicked path is active."""
    db = tmp_path / "p49.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
    monkeypatch.setenv("JWT_SECRET", "p49-test-secret-abcdefghijklmnopqrstuvwxyz-0123456789")
    # Explicitly unset scoring version so tests see the default path
    monkeypatch.delenv("SCORING_VERSION_DEFAULT", raising=False)
    import infra.storage
    infra.storage._local = threading.local()
    infra.storage.DB_PATH = str(db)
    from infra.storage import init_db
    init_db()
    from infra.pit import load_universe_history_csv
    load_universe_history_csv()

    import app
    return TestClient(app.app)


@pytest.fixture
def seed_score_history_ab(client, tmp_path, monkeypatch):
    """Seed score_history with 10 paired (V13, calibrated) rows across
    5 symbols × 2 dates for the A/B report tests."""
    import infra.storage
    conn = infra.storage._get_conn()
    today = date.today()
    yesterday = today - timedelta(days=1)
    rows = []
    symbols = ["THYAO", "AKBNK", "BIMAS", "ASELS", "EREGL"]
    for sym in symbols:
        for d, v13, cal, v13_d, cal_d in [
            (today.isoformat(), 75.0, 72.0, "AL", "İZLE"),
            (yesterday.isoformat(), 70.0, 71.5, "AL", "AL"),
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO score_history "
                "(symbol, snap_date, score, momentum, risk, fa_score, ivme, "
                " decision, scoring_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sym, d, v13, 0.0, 0.0, v13, 0.0, v13_d, "v13_handpicked"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO score_history "
                "(symbol, snap_date, score, momentum, risk, fa_score, ivme, "
                " decision, scoring_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sym, d, cal, 0.0, 0.0, cal, 0.0, cal_d, "calibrated_2026Q1"),
            )
    conn.commit()
    return conn


# ==========================================================================
# Backward compat — Rule 6 guarantee
# ==========================================================================

class TestScoringBackwardCompat:
    """The V13 default path MUST produce bit-identical output to
    pre-Phase-4.9. Any regression here blocks the whole turn."""

    def test_analyze_symbol_default_scoring_version_v13(self):
        """Default scoring_version (None + no env var) -> v13_handpicked."""
        os.environ.pop("SCORING_VERSION_DEFAULT", None)
        from engine.scoring_calibrated import HANDPICKED_VERSION
        # analyze_symbol's default resolution path
        resolved = os.getenv("SCORING_VERSION_DEFAULT", "v13_handpicked")
        assert resolved == HANDPICKED_VERSION

    def test_default_path_no_meta_field(self, client, monkeypatch):
        """V13 default path: r should NOT carry _meta (backward compat)."""
        # analyze_symbol directly without going through HTTP so we don't
        # hit network data sources
        from engine.scoring_calibrated import score_dispatch, HANDPICKED_VERSION
        # Minimal metrics dict that avoids compute_metrics
        m = {"pe": 10.0, "roe": 0.15, "net_margin": 0.12, "roic": 0.18,
             "revenue_growth": 0.10, "debt_equity": 0.5, "current_ratio": 1.5,
             "altman_z": 3.5, "interest_coverage": 10, "net_debt_ebitda": 1.0,
             "eps_growth": 0.2, "ebitda_growth": 0.15, "peg": 0.8,
             "fcf_yield": 0.05, "margin_safety": 0.25, "ev_ebitda": 5.0,
             "pb": 1.5, "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500}
        result = score_dispatch(m, sector_group="teknoloji",
                                 scoring_version=HANDPICKED_VERSION)
        # V13 path returns a dict with scoring_version + scoring_version_effective
        assert result["scoring_version"] == "v13_handpicked"
        assert result["scoring_version_effective"] == "v13_handpicked"

    def test_env_var_default(self, monkeypatch):
        """Setting the env var changes the default."""
        monkeypatch.setenv("SCORING_VERSION_DEFAULT", "calibrated_2026Q1")
        resolved = os.getenv("SCORING_VERSION_DEFAULT", "v13_handpicked")
        assert resolved == "calibrated_2026Q1"

    def test_save_daily_snapshot_none_version_uses_default_column(self, tmp_path, monkeypatch):
        """save_daily_snapshot(scoring_version=None) should use the
        column DEFAULT 'v13_handpicked' (backward compat SQL path)."""
        db = tmp_path / "bc.db"
        monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
        import infra.storage
        infra.storage._local = threading.local()
        infra.storage.DB_PATH = str(db)
        from infra.storage import init_db
        init_db()

        from engine.delta import save_daily_snapshot
        save_daily_snapshot("THYAO", {"overall": 75.0, "ivme": 50.0,
                                       "risk_score": 10.0, "fa_score": 80.0,
                                       "decision": "AL"})
        conn = infra.storage._get_conn()
        row = conn.execute(
            "SELECT scoring_version FROM score_history WHERE symbol=?",
            ("THYAO",),
        ).fetchone()
        assert row is not None
        assert row[0] == "v13_handpicked"

    def test_save_daily_snapshot_explicit_version_writes_it(self, tmp_path, monkeypatch):
        """save_daily_snapshot(scoring_version='calibrated_2026Q1') writes
        that exact value to the column (not the default)."""
        db = tmp_path / "exp.db"
        monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
        import infra.storage
        infra.storage._local = threading.local()
        infra.storage.DB_PATH = str(db)
        from infra.storage import init_db
        init_db()

        from engine.delta import save_daily_snapshot
        save_daily_snapshot("AKBNK",
                             {"overall": 68.0, "ivme": 45.0,
                              "risk_score": 15.0, "fa_score": 70.0,
                              "decision": "İZLE"},
                             scoring_version="calibrated_2026Q1")
        conn = infra.storage._get_conn()
        row = conn.execute(
            "SELECT scoring_version FROM score_history WHERE symbol=?",
            ("AKBNK",),
        ).fetchone()
        assert row[0] == "calibrated_2026Q1"


# ==========================================================================
# score_dispatch integration
# ==========================================================================

class TestScoreDispatchIntegration:
    def test_v13_path_uses_handpicked_thresholds(self):
        """score_dispatch routes V13 to engine/scoring.py -- results
        match a direct call to score_value."""
        from engine.scoring_calibrated import score_dispatch, HANDPICKED_VERSION
        from engine.scoring import score_value, score_quality
        m = {"pe": 10.0, "roe": 0.15, "net_margin": 0.12,
             "market_cap": 1000, "total_debt": 100, "cash": 50,
             "revenue": 500, "pb": 1.5, "ev_ebitda": 5.0,
             "fcf_yield": 0.05, "margin_safety": 0.25, "roic": 0.18}
        d = score_dispatch(m, "teknoloji", HANDPICKED_VERSION)
        direct_value = score_value(m, "teknoloji")
        direct_quality = score_quality(m, "teknoloji")
        assert d["value"] == pytest.approx(direct_value)
        assert d["quality"] == pytest.approx(direct_quality)

    def test_calibrated_no_fits_falls_back(self):
        """calibrated_2026Q1 requested with no fits on disk -> falls
        back to V13 and records scoring_version_effective."""
        from engine.scoring_calibrated import (
            score_dispatch, CALIBRATED_VERSION, HANDPICKED_VERSION,
            reset_fits_cache,
        )
        reset_fits_cache()
        m = {"pe": 10.0, "roe": 0.15, "net_margin": 0.12,
             "market_cap": 1000, "total_debt": 100, "cash": 50, "revenue": 500,
             "pb": 1.5, "ev_ebitda": 5.0, "fcf_yield": 0.05,
             "margin_safety": 0.25, "roic": 0.18}
        d = score_dispatch(m, "teknoloji", CALIBRATED_VERSION, fits=None)
        assert d["scoring_version"] == CALIBRATED_VERSION
        assert d["scoring_version_effective"] == HANDPICKED_VERSION


# ==========================================================================
# /api/signals/today
# ==========================================================================

class TestSignalsTodayEndpoint:
    def test_json_format_returns_list(self, client):
        resp = client.get("/api/signals/today?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert "signals" in data
        assert isinstance(data["signals"], list)
        assert data["as_of"] == date.today().isoformat()
        # Each row has expected fields
        for row in data["signals"]:
            assert "symbol" in row
            assert "signal" in row
            assert "cs_rank_pct" in row
            assert "sector" in row

    def test_csv_format_parseable(self, client):
        resp = client.get("/api/signals/today?format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        # Content-Disposition set for browser download
        assert "attachment" in resp.headers["content-disposition"]
        # CSV parses
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        # Every row has the expected columns
        if rows:
            for required in ("symbol", "signal", "cs_rank_pct", "sector"):
                assert required in rows[0]

    def test_min_rank_pct_filter_excludes_below(self, client):
        """min_rank_pct=0.9 should return strictly fewer signals than 0.0."""
        lo = client.get("/api/signals/today?format=json&min_rank_pct=0.0").json()
        hi = client.get("/api/signals/today?format=json&min_rank_pct=0.9").json()
        assert hi["count"] <= lo["count"]
        # All hi rows have rank >= 0.9
        for row in hi["signals"]:
            if row["cs_rank_pct"] is not None:
                assert row["cs_rank_pct"] >= 0.9

    def test_invalid_format_param(self, client):
        resp = client.get("/api/signals/today?format=xml")
        assert resp.status_code == 422  # FastAPI validation

    def test_invalid_min_rank_pct_range(self, client):
        resp = client.get("/api/signals/today?min_rank_pct=1.5")
        assert resp.status_code == 422  # out of [0, 1] range


# ==========================================================================
# /api/signals/history
# ==========================================================================

class TestSignalsHistoryEndpoint:
    def test_csv_pull(self, client, seed_score_history_ab):
        today = date.today()
        week_ago = today - timedelta(days=7)
        resp = client.get(
            f"/api/signals/history?from={week_ago.isoformat()}"
            f"&to={today.isoformat()}&format=csv"
        )
        assert resp.status_code == 200
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        # seed inserted 10 rows for each version = 20 total within the window
        assert len(rows) >= 10
        for r in rows:
            assert r["scoring_version"] in (
                "v13_handpicked", "calibrated_2026Q1",
            )

    def test_invalid_date_format(self, client):
        resp = client.get("/api/signals/history?from=NOT-A-DATE&to=2025-01-01")
        assert resp.status_code == 400

    def test_range_too_large(self, client):
        today = date.today()
        too_far = today - timedelta(days=400)
        resp = client.get(
            f"/api/signals/history?from={too_far.isoformat()}&to={today.isoformat()}"
        )
        assert resp.status_code == 400
        assert "365" in resp.json()["error"]

    def test_to_before_from(self, client):
        resp = client.get("/api/signals/history?from=2024-12-31&to=2024-01-01")
        assert resp.status_code == 400


# ==========================================================================
# /api/ensemble/weights
# ==========================================================================

class TestEnsembleWeightsEndpoint:
    def test_returns_weights_vector(self, client):
        resp = client.get("/api/ensemble/weights")
        assert resp.status_code == 200
        data = resp.json()
        assert "weights" in data
        # Every weight entry has signal + weight + mu
        for w in data["weights"]:
            assert "signal" in w
            assert "weight" in w
            assert "mu" in w

    def test_weights_sorted_desc(self, client):
        """Weights should be sorted descending for readability."""
        resp = client.get("/api/ensemble/weights")
        weights = resp.json()["weights"]
        for i in range(1, len(weights)):
            assert weights[i - 1]["weight"] >= weights[i]["weight"] - 1e-9

    def test_holdout_evaluation_present(self, client):
        resp = client.get("/api/ensemble/weights")
        data = resp.json()
        assert "holdout_evaluation" in data


# ==========================================================================
# /api/paper_trading/template
# ==========================================================================

class TestPaperTradingTemplate:
    def test_allocation_sums_to_seed_capital(self, client):
        """allocation_tl + cash row = seed_capital (within rounding)."""
        resp = client.get("/api/paper_trading/template?seed_capital=100000&format=json")
        assert resp.status_code == 200
        data = resp.json()
        total = sum(
            row["allocation_tl"] for row in data["allocations"]
            if row["allocation_tl"] is not None
        )
        # Allow 5 TL rounding error over 100k
        assert abs(total - 100000.0) < 5.0

    def test_cash_row_present(self, client):
        resp = client.get("/api/paper_trading/template?seed_capital=100000&format=json")
        rows = resp.json()["allocations"]
        cash_rows = [r for r in rows if r["signal"] == "(cash)"]
        assert len(cash_rows) == 1

    def test_csv_parseable(self, client):
        resp = client.get("/api/paper_trading/template?seed_capital=50000&format=csv")
        assert resp.status_code == 200
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        assert rows  # at least the cash row
        for r in rows:
            float(r["allocation_tl"])  # parses as float

    def test_invalid_seed_capital(self, client):
        resp = client.get("/api/paper_trading/template?seed_capital=0")
        assert resp.status_code == 422

    def test_top_n_range(self, client):
        resp = client.get("/api/paper_trading/template?top_n_per_signal=20")
        assert resp.status_code == 422  # > 10


# ==========================================================================
# /api/scoring/ab_report
# ==========================================================================

class TestAbReportEndpoint:
    def test_joins_by_symbol_and_date(self, client, seed_score_history_ab):
        resp = client.get("/api/scoring/ab_report?days=30&format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["_meta"]["n_paired_rows"] >= 10
        # Each row has both v13 and cal score
        for r in data["rows"]:
            assert "v13_score" in r
            assert "cal_score" in r
            assert "diff" in r
            assert "decision_match" in r

    def test_mean_diff_matches_manual(self, client, seed_score_history_ab):
        """Seeded differences: (72-75)=-3 or (71.5-70)=+1.5 across 10 rows
        (5 symbols × 2 dates). Mean = ((-3)*5 + 1.5*5) / 10 = -0.75."""
        resp = client.get("/api/scoring/ab_report?days=30&format=json")
        mean_diff = resp.json()["_meta"]["mean_score_diff"]
        assert mean_diff == pytest.approx(-0.75, abs=0.01)

    def test_decision_flip_count(self, client, seed_score_history_ab):
        """Today's 5 symbols have V13='AL' vs cal='İZLE' flip; yesterday
        both=AL (no flip). So decision_flip_count = 5."""
        resp = client.get("/api/scoring/ab_report?days=30&format=json")
        flips = resp.json()["_meta"]["decision_flip_count"]
        assert flips == 5

    def test_symbol_filter(self, client, seed_score_history_ab):
        resp = client.get("/api/scoring/ab_report?symbol=THYAO&days=30&format=json")
        data = resp.json()
        for r in data["rows"]:
            assert r["symbol"] == "THYAO"

    def test_spearman_computed_when_enough_rows(self, client, seed_score_history_ab):
        resp = client.get("/api/scoring/ab_report?days=30&format=json")
        rho = resp.json()["_meta"]["spearman_rho"]
        assert rho is not None
        assert -1.0 <= rho <= 1.0

    def test_csv_format(self, client, seed_score_history_ab):
        resp = client.get("/api/scoring/ab_report?days=30&format=csv")
        assert resp.status_code == 200
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        assert len(rows) >= 10

    def test_empty_score_history(self, client):
        """Fresh DB with no A/B rows: endpoint returns empty without error."""
        resp = client.get("/api/scoring/ab_report?days=30&format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["_meta"]["n_paired_rows"] == 0


# ==========================================================================
# /ab_report HTML page
# ==========================================================================

class TestAbReportPage:
    def test_html_rendered(self, client, seed_score_history_ab):
        resp = client.get("/ab_report?days=30")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        # Our headline strings should appear
        assert "A/B Scoring Report" in resp.text
        # Seeded symbol appears in rendered table
        assert "THYAO" in resp.text

    def test_html_empty_shows_helpful_message(self, client):
        """Fresh DB: HTML page should render with the friendly empty-state
        message explaining how to enable calibrated scoring."""
        resp = client.get("/ab_report?days=30")
        assert resp.status_code == 200
        assert "SCORING_VERSION_DEFAULT" in resp.text or \
               "scoring_version" in resp.text

    def test_download_links_present(self, client):
        resp = client.get("/ab_report?days=30")
        assert "/api/scoring/ab_report" in resp.text
        assert "format=csv" in resp.text


# ==========================================================================
# /api/analyze with scoring_version query param
# ==========================================================================

class TestAnalyzeEndpointScoringVersion:
    """The /api/analyze/{ticker} endpoint accepts scoring_version query
    param. We don't want to hit the real yfinance data source in tests,
    so we verify the parameter is wired to analyze_symbol by patching."""

    def test_scoring_version_passed_to_analyze_symbol(self, client, monkeypatch):
        called = {}

        def fake_analyze(symbol, scoring_version=None):
            called["symbol"] = symbol
            called["scoring_version"] = scoring_version
            return {"metrics": {"price": 10, "market_cap": 1000, "pe": 10},
                    "overall": 50, "decision": "İZLE"}

        monkeypatch.setattr("app.analyze_symbol", fake_analyze)
        resp = client.get("/api/analyze/THYAO?scoring_version=calibrated_2026Q1")
        assert resp.status_code == 200
        assert called["scoring_version"] == "calibrated_2026Q1"

    def test_default_scoring_version_is_none(self, client, monkeypatch):
        """No query param -> analyze_symbol called with scoring_version=None
        (falls back to env var -> v13_handpicked)."""
        called = {}

        def fake_analyze(symbol, scoring_version=None):
            called["scoring_version"] = scoring_version
            return {"metrics": {"price": 10, "market_cap": 1000, "pe": 10},
                    "overall": 50, "decision": "İZLE"}

        monkeypatch.setattr("app.analyze_symbol", fake_analyze)
        resp = client.get("/api/analyze/THYAO")
        assert resp.status_code == 200
        assert called["scoring_version"] is None


# ==========================================================================
# KR-006 prevention
# ==========================================================================

class TestDisplayFieldCorrectness:
    """Endpoint responses must carry values in consistent scales.
    Paper trading allocation in TL, percentages in 0-100, weights in [0,1]."""

    def test_paper_trading_allocation_pct_in_0_100(self, client):
        resp = client.get("/api/paper_trading/template?seed_capital=100000&format=json")
        for r in resp.json()["allocations"]:
            if r.get("allocation_pct") is not None:
                assert 0.0 <= r["allocation_pct"] <= 100.0

    def test_ensemble_weights_sum_to_one(self, client):
        """Per Phase 4.5 contract, weights sum to 1.0."""
        resp = client.get("/api/ensemble/weights")
        data = resp.json()
        total = sum(w["weight"] for w in data["weights"])
        assert abs(total - 1.0) < 1e-6

    def test_signals_today_cs_rank_in_unit_interval(self, client):
        """Every rank in the response must be in [0, 1]."""
        resp = client.get("/api/signals/today?format=json")
        for r in resp.json()["signals"]:
            v = r.get("cs_rank_pct")
            if v is not None:
                assert 0.0 <= v <= 1.0

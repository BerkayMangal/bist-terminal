"""Phase 4.8 — A/B telemetry analysis script tests.

Tests are 100% offline: no network, no production DB. We build a
synthetic score_history table inline and exercise the script's
analysis path directly (functions are decoupled from CLI).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


# Load the script as a module
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "analyze_ab_telemetry.py"
)
_spec = importlib.util.spec_from_file_location("ab_analyze", _SCRIPT_PATH)
ab_analyze = importlib.util.module_from_spec(_spec)
sys.modules["ab_analyze"] = ab_analyze
_spec.loader.exec_module(ab_analyze)


# ==========================================================================
# Stats helpers
# ==========================================================================

class TestSpearmanRho:
    def test_perfect_positive(self):
        assert ab_analyze.spearman_rho([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == 1.0

    def test_perfect_negative(self):
        assert ab_analyze.spearman_rho([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == -1.0

    def test_uncorrelated(self):
        # Roughly zero correlation: y is intentionally non-monotonic in x
        rho = ab_analyze.spearman_rho([1, 2, 3, 4, 5, 6], [3, 1, 5, 2, 6, 4])
        assert rho is not None
        assert -0.5 < rho < 0.5

    def test_handles_ties(self):
        # All-tied y series → rank denominator is 0, returns None
        rho = ab_analyze.spearman_rho([1, 2, 3, 4], [5, 5, 5, 5])
        assert rho is None

    def test_too_few_returns_none(self):
        assert ab_analyze.spearman_rho([1, 2], [1, 2]) is None

    def test_length_mismatch(self):
        assert ab_analyze.spearman_rho([1, 2, 3], [1, 2]) is None


class TestRanks:
    def test_no_ties(self):
        # 1-based ranks
        assert ab_analyze._ranks([10, 20, 30]) == [1.0, 2.0, 3.0]
        assert ab_analyze._ranks([30, 20, 10]) == [3.0, 2.0, 1.0]

    def test_with_ties(self):
        # Average rank for tied values
        ranks = ab_analyze._ranks([10, 20, 20, 30])
        assert ranks[0] == 1.0
        assert ranks[1] == ranks[2] == 2.5
        assert ranks[3] == 4.0


class TestPercentile:
    def test_basic(self):
        vals = list(range(11))  # 0..10
        assert ab_analyze.percentile(vals, 50) == 5.0
        assert ab_analyze.percentile(vals, 10) == 1.0
        assert ab_analyze.percentile(vals, 90) == 9.0

    def test_empty(self):
        assert ab_analyze.percentile([], 50) is None

    def test_single(self):
        assert ab_analyze.percentile([42.0], 50) == 42.0


# ==========================================================================
# DB integration: synthetic score_history
# ==========================================================================

@pytest.fixture
def synthetic_db(tmp_path):
    """Build a tiny score_history with paired V13 and calibrated rows."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE score_history (
            symbol TEXT NOT NULL,
            snap_date TEXT NOT NULL,
            score REAL,
            momentum REAL,
            risk REAL,
            fa_score REAL,
            ivme REAL,
            decision TEXT,
            scoring_version TEXT NOT NULL DEFAULT 'v13_handpicked',
            PRIMARY KEY (symbol, snap_date, scoring_version)
        )
    """)

    # 5 symbols × 7 days × 2 versions = 70 rows
    today = date.today()
    rows = []
    test_data = [
        # (symbol, v13_base, cal_offset, v13_dec, cal_dec)
        ("THYAO", 65.0, +2.0, "AL", "AL"),
        ("TUPRS", 55.0, -3.0, "AL", "İZLE"),  # decision flip
        ("AKBNK", 45.0, +0.0, "İZLE", "İZLE"),  # bank, exact match
        ("ASELS", 70.0, +5.0, "AL", "AL"),
        ("EREGL", 30.0, -1.0, "SAT", "SAT"),
    ]
    for d_offset in range(7):
        snap = (today - timedelta(days=d_offset)).isoformat()
        for sym, v13, off, v13d, cald in test_data:
            rows.append((sym, snap, v13, 50, -5, v13 - 5, 60, v13d, "v13_handpicked"))
            rows.append((sym, snap, v13 + off, 50, -5, v13 - 5 + off, 60,
                         cald, "calibrated_2026Q1"))

    conn.executemany("""
        INSERT INTO score_history
        (symbol, snap_date, score, momentum, risk, fa_score, ivme, decision, scoring_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    conn.close()
    return str(db)


class TestFetchPairedSnapshots:
    def test_returns_paired_rows(self, synthetic_db):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        assert len(rows) == 5 * 7  # 5 symbols × 7 days
        assert all(isinstance(r, ab_analyze.PairedRow) for r in rows)

    def test_paired_row_diff(self, synthetic_db):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        thyao_rows = [r for r in rows if r.symbol == "THYAO"]
        assert len(thyao_rows) == 7
        for r in thyao_rows:
            assert r.diff == pytest.approx(2.0, abs=0.001)
            assert r.v13_decision == "AL"
            assert r.cal_decision == "AL"
            assert r.decision_match is True

    def test_decision_flip_detected(self, synthetic_db):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        tuprs_rows = [r for r in rows if r.symbol == "TUPRS"]
        for r in tuprs_rows:
            assert r.decision_match is False

    def test_empty_lookback(self, synthetic_db):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=0)
        # All rows are within 7 days; lookback_days=0 means "from today"
        # (lookback_days=0 → snap_date >= today; only today's row counts)
        assert len(rows) <= 5  # at most today's rows


# ==========================================================================
# Analysis
# ==========================================================================

class TestAnalyze:
    def test_empty_rows(self):
        a = ab_analyze.analyze([], {})
        assert a["n_paired_rows"] == 0
        assert "warning" in a

    def test_basic_aggregates(self, synthetic_db):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        sector_map = {
            "THYAO": "ulasim", "TUPRS": "enerji",
            "AKBNK": "banka", "ASELS": "savunma", "EREGL": "sanayi",
        }
        a = ab_analyze.analyze(rows, sector_map)

        assert a["n_paired_rows"] == 35
        assert a["n_symbols"] == 5
        assert a["n_sectors"] == 5

        o = a["overall"]
        assert o["spearman_rho_overall"] is not None
        # Calibrated mean offsets sum: (+2 -3 +0 +5 -1)/5 = +0.6
        # Each symbol contributes 7 rows, so overall mean is 0.6
        assert o["score_diff_mean"] == pytest.approx(0.6, abs=0.01)

    def test_decision_quadrant(self, synthetic_db):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        sector_map = {sym: "sanayi" for sym in
                      ["THYAO", "TUPRS", "AKBNK", "ASELS", "EREGL"]}
        a = ab_analyze.analyze(rows, sector_map)
        q = a["decision_quadrant"]
        # THYAO+ASELS = AL→AL = 14 rows
        assert q.get("AL->AL", 0) == 14
        # TUPRS = AL→İZLE = 7 rows (flips)
        assert q.get("AL->İZLE", 0) == 7
        # AKBNK = İZLE→İZLE = 7
        assert q.get("İZLE->İZLE", 0) == 7
        # EREGL = SAT→SAT = 7
        assert q.get("SAT->SAT", 0) == 7

    def test_per_sector_breakdown(self, synthetic_db):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        sector_map = {
            "THYAO": "ulasim", "TUPRS": "enerji",
            "AKBNK": "banka", "ASELS": "savunma", "EREGL": "sanayi",
        }
        a = ab_analyze.analyze(rows, sector_map)
        bs = a["by_sector"]
        assert "ulasim" in bs
        assert bs["ulasim"]["n_rows"] == 7
        assert bs["ulasim"]["n_symbols"] == 1
        assert bs["ulasim"]["mean_diff"] == pytest.approx(2.0, abs=0.01)
        assert bs["ulasim"]["decision_match_rate"] == 1.0

        # banka: AKBNK match=1.0
        assert bs["banka"]["decision_match_rate"] == 1.0
        # enerji (TUPRS) all flips
        assert bs["enerji"]["decision_match_rate"] == 0.0

    def test_per_symbol_ranking(self, synthetic_db):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        sector_map = {sym: "sanayi" for sym in
                      ["THYAO", "TUPRS", "AKBNK", "ASELS", "EREGL"]}
        a = ab_analyze.analyze(rows, sector_map)
        bs = a["by_symbol"]
        assert "THYAO" in bs and "ASELS" in bs

        # ASELS has biggest abs diff (+5), AKBNK smallest (0)
        assert bs["ASELS"]["max_abs_diff"] == pytest.approx(5.0, abs=0.01)
        assert bs["AKBNK"]["max_abs_diff"] == pytest.approx(0.0, abs=0.01)

        # Decision flips: TUPRS=7 (all rows flip), others=0
        assert bs["TUPRS"]["decision_flips"] == 7
        assert bs["THYAO"]["decision_flips"] == 0


# ==========================================================================
# Verdict interpretation
# ==========================================================================

class TestInterpret:
    def test_insufficient_data(self):
        a = {"n_paired_rows": 5, "overall": {"spearman_rho_overall": 0.99}}
        assert ab_analyze.interpret(a) == "insufficient_data"

    def test_no_rho(self):
        a = {"n_paired_rows": 50, "overall": {"spearman_rho_overall": None}}
        assert ab_analyze.interpret(a) == "insufficient_data"

    def test_very_aligned(self):
        a = {"n_paired_rows": 100, "overall": {"spearman_rho_overall": 0.97}}
        assert ab_analyze.interpret(a) == "very_aligned"

    def test_moderate(self):
        a = {"n_paired_rows": 100, "overall": {"spearman_rho_overall": 0.85}}
        assert ab_analyze.interpret(a) == "moderate"

    def test_divergent(self):
        a = {"n_paired_rows": 100, "overall": {"spearman_rho_overall": 0.55}}
        assert ab_analyze.interpret(a) == "divergent"


# ==========================================================================
# Markdown rendering
# ==========================================================================

class TestRenderMarkdown:
    def test_empty_analysis_renders_warning(self):
        a = {"n_paired_rows": 0, "warning": "test warning"}
        md = ab_analyze.render_markdown(a, "insufficient_data", 30, "/tmp/x.db")
        assert "No paired data" in md
        assert "test warning" in md

    def test_basic_report_includes_sections(self, synthetic_db):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        sector_map = {
            "THYAO": "ulasim", "TUPRS": "enerji",
            "AKBNK": "banka", "ASELS": "savunma", "EREGL": "sanayi",
        }
        a = ab_analyze.analyze(rows, sector_map)
        v = ab_analyze.interpret(a)
        md = ab_analyze.render_markdown(a, v, 30, synthetic_db)

        # Section headers
        assert "## Verdict" in md
        assert "## Overall statistics" in md
        assert "## Decision quadrant" in md
        assert "## By sector" in md
        assert "## Top" in md and "most divergent symbols" in md
        assert "## Recommendations for next phases" in md

        # Sector names should appear
        assert "ulasim" in md
        assert "banka" in md

    def test_verdict_emoji_present(self, synthetic_db):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        sector_map = {sym: "sanayi" for sym in
                      ["THYAO", "TUPRS", "AKBNK", "ASELS", "EREGL"]}
        a = ab_analyze.analyze(rows, sector_map)
        v = ab_analyze.interpret(a)
        md = ab_analyze.render_markdown(a, v, 30, synthetic_db)
        # Should have one of the emoji indicators
        assert any(e in md for e in ["🟢", "🟡", "🔴", "⚪"])


# ==========================================================================
# CSV output
# ==========================================================================

class TestSymbolCsv:
    def test_csv_has_expected_columns(self, synthetic_db, tmp_path):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        sector_map = {sym: "sanayi" for sym in
                      ["THYAO", "TUPRS", "AKBNK", "ASELS", "EREGL"]}
        a = ab_analyze.analyze(rows, sector_map)
        out = tmp_path / "out.csv"
        ab_analyze.write_symbol_csv(a, out)

        with open(out) as f:
            r = csv.reader(f)
            header = next(r)
            assert "symbol" in header
            assert "max_abs_diff" in header
            assert "decision_flips" in header

            data = list(r)
            # 5 symbols with at least one paired row → 5 rows
            assert len(data) == 5

    def test_csv_sorted_by_max_abs_diff_desc(self, synthetic_db, tmp_path):
        rows = ab_analyze.fetch_paired_snapshots(synthetic_db, lookback_days=30)
        sector_map = {sym: "sanayi" for sym in
                      ["THYAO", "TUPRS", "AKBNK", "ASELS", "EREGL"]}
        a = ab_analyze.analyze(rows, sector_map)
        out = tmp_path / "out.csv"
        ab_analyze.write_symbol_csv(a, out)

        with open(out) as f:
            r = csv.DictReader(f)
            data = list(r)

        # First row should be ASELS (max abs diff = 5)
        assert data[0]["symbol"] == "ASELS"
        # Last row should be AKBNK (max abs diff = 0)
        assert data[-1]["symbol"] == "AKBNK"


# ==========================================================================
# CLI integration
# ==========================================================================

class TestMainCli:
    def test_db_not_found_returns_1(self, tmp_path, caplog):
        argv = [
            "--db", str(tmp_path / "nonexistent.db"),
            "--out-md", str(tmp_path / "out.md"),
            "--out-csv", str(tmp_path / "out.csv"),
        ]
        rc = ab_analyze.main(argv)
        assert rc == 1

    def test_full_run_with_synthetic_db(self, synthetic_db, tmp_path, monkeypatch):
        # Skip the sector lookup which would try to call into yfinance
        def fake_sector_map(symbols):
            return {sym: "sanayi" for sym in symbols}
        monkeypatch.setattr(ab_analyze, "build_sector_map", fake_sector_map)

        out_md = tmp_path / "report.md"
        out_csv = tmp_path / "data.csv"
        out_json = tmp_path / "data.json"
        argv = [
            "--db", synthetic_db,
            "--days", "30",
            "--out-md", str(out_md),
            "--out-csv", str(out_csv),
            "--out-json", str(out_json),
        ]
        rc = ab_analyze.main(argv)
        assert rc == 0
        assert out_md.exists()
        assert out_csv.exists()
        assert out_json.exists()

        # Sanity: JSON parseable
        data = json.loads(out_json.read_text())
        assert data["n_paired_rows"] == 35

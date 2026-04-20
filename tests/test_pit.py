"""Phase 2 PIT layer + ingestion tests.

Covers:
- fundamentals_pit upsert and as-of semantics (no look-ahead)
- universe_history as-of semantics (survivorship-free)
- CSV loader
- research.ingest_filings dry-run path + checkpoint resume

Each test gets its own DB via BISTBULL_DB_PATH set to a tmp_path file
(Phase 2 non-negotiable #2).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest


@pytest.fixture
def pit_db(tmp_path, monkeypatch):
    """Fresh DB + reset storage's module-level thread-local connection so
    subsequent imports pick up the new BISTBULL_DB_PATH."""
    db_path = tmp_path / "pit.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db_path))

    # Reset storage's thread-local connection (it may have opened an older DB).
    import infra.storage
    infra.storage._local = __import__("threading").local()
    infra.storage.DB_PATH = str(db_path)

    from infra.storage import init_db
    init_db()
    yield db_path


class TestFundamentalsAsOf:
    def test_empty_when_no_filings(self, pit_db):
        from infra.pit import get_fundamentals_at
        assert get_fundamentals_at("THYAO", "2023-01-01") == {}

    def test_look_ahead_guard(self, pit_db):
        """A filing on 2022-03-15 must not be visible on 2022-03-14."""
        from infra.pit import save_fundamental, get_fundamentals_at
        save_fundamental("THYAO", "2021-12-31", "2022-03-15", "borsapy",
                         "net_income", 9e9)
        # Before the filing: empty
        assert get_fundamentals_at("THYAO", "2022-03-14") == {}
        # On the filing date: visible
        r = get_fundamentals_at("THYAO", "2022-03-15")
        assert r["net_income"]["value"] == 9e9
        assert r["net_income"]["period_end"] == "2021-12-31"

    def test_picks_latest_period_end(self, pit_db):
        """Multiple periods: picks latest period_end that is also filed_at <= as_of."""
        from infra.pit import save_fundamental, get_fundamentals_at
        save_fundamental("THYAO", "2021-12-31", "2022-03-15", "borsapy",
                         "net_income", 9e9)
        save_fundamental("THYAO", "2022-03-31", "2022-05-10", "borsapy",
                         "net_income", 3e9)
        save_fundamental("THYAO", "2022-06-30", "2022-08-15", "borsapy",
                         "net_income", 5e9)

        # As of 2022-06-01: Q4-21 and Q1-22 are public; Q2-22 filing is future
        r = get_fundamentals_at("THYAO", "2022-06-01")
        assert r["net_income"]["period_end"] == "2022-03-31"
        assert r["net_income"]["value"] == 3e9

        # As of 2022-09-01: all three public; latest period is Q2-22
        r2 = get_fundamentals_at("THYAO", "2022-09-01")
        assert r2["net_income"]["period_end"] == "2022-06-30"
        assert r2["net_income"]["value"] == 5e9

    def test_symbol_case_insensitive(self, pit_db):
        from infra.pit import save_fundamental, get_fundamentals_at
        save_fundamental("thyao", "2022-03-31", "2022-05-10", "borsapy",
                         "roe", 0.18)
        assert get_fundamentals_at("THYAO", "2022-06-01")["roe"]["value"] == 0.18

    def test_survivorship_free_works_for_delisted(self, pit_db):
        """PIT read works for symbols that no longer trade (orthogonal to universe)."""
        from infra.pit import save_fundamental, get_fundamentals_at
        # A delisted symbol (e.g. KOZAA) still has historical filings
        save_fundamental("KOZAA", "2020-03-31", "2020-05-10", "borsapy",
                         "revenue", 500e6)
        r = get_fundamentals_at("KOZAA", "2021-01-01")
        assert r["revenue"]["value"] == 500e6


class TestUniverseAsOf:
    def test_empty_when_universe_unknown(self, pit_db):
        from infra.pit import get_universe_at
        assert get_universe_at("NONEXISTENT", "2023-01-01") == []

    def test_from_date_inclusive(self, pit_db):
        """A symbol added on date D is a member on D."""
        from infra.pit import load_universe_history_csv, get_universe_at
        csv_path = Path(__file__).parent.parent / "data" / "universe_history.csv"
        load_universe_history_csv(csv_path)
        # SASA from_date = 2020-07-01
        assert "SASA" in get_universe_at("BIST30", "2020-07-01")
        # The day before: not a member
        assert "SASA" not in get_universe_at("BIST30", "2020-06-30")

    def test_to_date_exclusive(self, pit_db):
        """A symbol removed with to_date=D is NOT a member on D."""
        from infra.pit import load_universe_history_csv, get_universe_at
        csv_path = Path(__file__).parent.parent / "data" / "universe_history.csv"
        load_universe_history_csv(csv_path)
        # KOZAL to_date = 2023-07-01
        assert "KOZAL" in get_universe_at("BIST30", "2023-06-30")
        assert "KOZAL" not in get_universe_at("BIST30", "2023-07-01")

    def test_universe_differs_historically(self, pit_db):
        """The core Phase 2 deliverable: get_universe_at('BIST30', 2020-06-15)
        returns a different set than today's BIST30."""
        from infra.pit import load_universe_history_csv, get_universe_at
        csv_path = Path(__file__).parent.parent / "data" / "universe_history.csv"
        load_universe_history_csv(csv_path)

        today = set(get_universe_at("BIST30", "2026-04-20"))
        hist = set(get_universe_at("BIST30", "2020-06-15"))
        only_then = hist - today
        only_now = today - hist
        # Must differ in at least some symbols (survivorship fix live proof)
        assert only_then, f"history should include symbols no longer in universe; got {only_then}"
        assert only_now, f"today should include symbols not in 2020; got {only_now}"
        # Sanity: some known historicals
        assert {"KOZAA", "KOZAL"} <= only_then, \
            f"KOZAA/KOZAL should be in 2020 but not today; only_then={only_then}"


class TestCsvLoader:
    def test_load_csv_count_matches(self, pit_db):
        from infra.pit import load_universe_history_csv
        csv_path = Path(__file__).parent.parent / "data" / "universe_history.csv"
        n = load_universe_history_csv(csv_path)
        # Seed currently has 33 rows
        assert n >= 30

    def test_load_csv_missing_header_raises(self, pit_db, tmp_path):
        from infra.pit import load_universe_history_csv
        bad = tmp_path / "bad.csv"
        bad.write_text("universe_name,symbol\nBIST30,THYAO\n")
        with pytest.raises(ValueError, match="missing columns"):
            load_universe_history_csv(bad)

    def test_load_csv_idempotent_upsert(self, pit_db, tmp_path):
        from infra.pit import load_universe_history_csv, get_universe_at
        csv = tmp_path / "u.csv"
        csv.write_text(
            "universe_name,symbol,from_date,to_date,reason\n"
            "BIST30,DEMO,2020-01-01,,approximate\n"
        )
        load_universe_history_csv(csv)
        assert "DEMO" in get_universe_at("BIST30", "2023-01-01")
        # Re-load with to_date set -- UPSERT should close membership
        csv.write_text(
            "universe_name,symbol,from_date,to_date,reason\n"
            "BIST30,DEMO,2020-01-01,2022-01-01,removal\n"
        )
        load_universe_history_csv(csv)
        assert "DEMO" not in get_universe_at("BIST30", "2023-01-01")
        assert "DEMO" in get_universe_at("BIST30", "2021-01-01")


class TestIngestFilings:
    def test_dry_run_populates_pit(self, pit_db, tmp_path, monkeypatch):
        from research.ingest_filings import ingest
        # Redirect checkpoint to tmp so we don't pollute /tmp
        monkeypatch.setattr(
            "research.ingest_filings.CHECKPOINT_PATH",
            tmp_path / "ck.json",
        )
        result = ingest(
            symbols=["THYAO", "AKBNK", "ISCTR"],
            from_date=date(2022, 1, 1),
            to_date=date(2024, 1, 1),
            dry_run=True,
        )
        # 3 symbols × 8 quarters × 4 metrics = 96 rows
        assert result["totals"]["symbols"] == 3
        assert result["totals"]["filings"] == 3 * 8
        assert result["totals"]["rows"] == 3 * 8 * 4

        # Verify one of the symbols has data end-to-end
        from infra.pit import get_fundamentals_at
        r = get_fundamentals_at("THYAO", "2024-01-01")
        assert "net_income" in r
        assert "revenue" in r
        assert r["net_income"]["source"] == "synthetic"

    def test_dry_run_is_deterministic(self, pit_db, tmp_path, monkeypatch):
        """Same symbol+period should yield the same synthetic value across runs."""
        from research.ingest_filings import _synthetic_filing
        a = _synthetic_filing("THYAO", date(2023, 3, 31))
        b = _synthetic_filing("THYAO", date(2023, 3, 31))
        assert a["metrics"] == b["metrics"]
        assert a["filed_at"] == b["filed_at"]

    def test_checkpoint_written_per_symbol(self, pit_db, tmp_path, monkeypatch):
        from research.ingest_filings import ingest
        ck = tmp_path / "ck.json"
        monkeypatch.setattr("research.ingest_filings.CHECKPOINT_PATH", ck)
        ingest(
            symbols=["THYAO", "AKBNK"],
            from_date=date(2023, 1, 1),
            to_date=date(2023, 12, 31),
            dry_run=True,
        )
        assert ck.exists()
        state = json.loads(ck.read_text())
        assert state["completed"] == ["AKBNK", "THYAO"]  # sorted
        assert state["totals"]["symbols"] == 2

    def test_real_mode_stub_raises(self, pit_db, tmp_path, monkeypatch):
        """Until real borsapy wiring lands, --dry-run is required."""
        from research.ingest_filings import ingest
        monkeypatch.setattr(
            "research.ingest_filings.CHECKPOINT_PATH",
            tmp_path / "ck.json",
        )
        with pytest.raises(NotImplementedError, match="borsapy fetch"):
            ingest(
                symbols=["THYAO"],
                from_date=date(2023, 1, 1),
                to_date=date(2023, 6, 30),
                dry_run=False,
            )

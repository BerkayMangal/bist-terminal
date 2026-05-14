# ================================================================
# tests/test_viop_storage.py
#
# VIOP snapshot storage + code parser + ingest cycle (Faz 1).
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Code parser
# ────────────────────────────────────────────────────────────────


class TestParseCode:
    def test_parse_stock_option_call(self):
        from infra.viop_storage import parse_code
        p = parse_code("O_BIMASE0526C33.00")
        assert p["kind"] == "option"
        assert p["underlying"] == "BIMAS"
        assert p["side"] == "C"
        assert p["strike"] == 33.0
        assert p["expiry"] == "2026-05"

    def test_parse_stock_option_put(self):
        from infra.viop_storage import parse_code
        p = parse_code("O_KCHOLE0526P200.00")
        assert p["kind"] == "option"
        assert p["underlying"] == "KCHOL"
        assert p["side"] == "P"
        assert p["strike"] == 200.0

    def test_parse_index_future(self):
        from infra.viop_storage import parse_code
        p = parse_code("F_XU0301226")
        assert p["kind"] == "future"
        assert p["underlying"] == "XU030"
        assert p["side"] == "F"
        assert p["expiry"] == "2026-12"
        assert p["strike"] is None

    def test_parse_currency_future(self):
        from infra.viop_storage import parse_code
        p = parse_code("F_USDTRY0826")
        assert p["kind"] == "future"
        assert p["underlying"] == "USDTRY"
        assert p["expiry"] == "2026-08"

    def test_parse_index_option_with_digit_underlying(self):
        # XU030 has digits IN the underlying — caught the regex bug.
        from infra.viop_storage import parse_code
        p = parse_code("O_XU030E0826C17500.00")
        assert p["kind"] == "option"
        assert p["underlying"] == "XU030"
        assert p["side"] == "C"
        assert p["strike"] == 17500.0
        assert p["expiry"] == "2026-08"

    def test_parse_future_with_alphanumeric_underlying(self):
        from infra.viop_storage import parse_code
        p = parse_code("F_X10XB1226")
        assert p["kind"] == "future"
        assert p["underlying"] == "X10XB"
        assert p["expiry"] == "2026-12"

    def test_parse_index_option_put(self):
        from infra.viop_storage import parse_code
        p = parse_code("O_XU030E0626P15000.00")
        assert p["kind"] == "option"
        assert p["underlying"] == "XU030"
        assert p["side"] == "P"
        assert p["strike"] == 15000.0

    def test_parse_xlbnk_future(self):
        from infra.viop_storage import parse_code
        p = parse_code("F_XLBNK0626")
        assert p["kind"] == "future"
        assert p["underlying"] == "XLBNK"

    def test_parse_malformed(self):
        from infra.viop_storage import parse_code
        p = parse_code("NONSENSE")
        assert p["kind"] is None
        assert p["underlying"] is None

    def test_parse_empty(self):
        from infra.viop_storage import parse_code
        p = parse_code("")
        assert p["kind"] is None


# ────────────────────────────────────────────────────────────────
# Storage round-trip
# ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Use a temp SQLite DB so tests don't pollute the real one."""
    db_file = tmp_path / "test_viop.db"
    monkeypatch.setattr("infra.storage.DB_PATH", str(db_file))
    # Reset thread-local connection so it re-opens against the temp DB
    import infra.viop_storage as v
    v._local.conn = None
    v.init_db()
    yield v
    v._local.conn = None


class TestStorageRoundTrip:
    def test_save_and_read_today(self, tmp_db):
        rows = [
            {
                "code": "O_BIMASE0526C33.00",
                "contract": "BIMAS Mayis 2026 Call 33.00 E",
                "category": "stock",
                "price": 1.25, "change": 0.05,
                "volume_tl": 250000, "volume_qty": 200000,
            },
            {
                "code": "F_XU0300626",
                "contract": "XU030 Haziran 2026 Vadeli",
                "category": "index",
                "price": 12500, "change": 110,
                "volume_tl": 3.5e10, "volume_qty": 3.86e10,
            },
        ]
        n = tmp_db.save_snapshot(rows)
        assert n == 2
        today = tmp_db.get_today()
        assert len(today) == 2
        # Sorted by volume_tl desc — future should be first
        assert today[0]["code"] == "F_XU0300626"
        # Code parsing was applied
        assert today[1]["underlying"] == "BIMAS"
        assert today[1]["kind"] == "option"
        assert today[1]["side"] == "C"
        assert today[1]["strike"] == 33.0

    def test_save_empty_list(self, tmp_db):
        assert tmp_db.save_snapshot([]) == 0

    def test_filter_by_kind(self, tmp_db):
        rows = [
            {"code": "O_BIMASE0526C33.00", "price": 1, "volume_tl": 100, "volume_qty": 100},
            {"code": "F_XU0300626", "price": 1, "volume_tl": 200, "volume_qty": 200},
        ]
        tmp_db.save_snapshot(rows)
        options_only = tmp_db.get_today(kind="option")
        assert len(options_only) == 1
        assert options_only[0]["code"] == "O_BIMASE0526C33.00"

    def test_filter_by_underlying(self, tmp_db):
        rows = [
            {"code": "O_BIMASE0526C33.00", "price": 1, "volume_tl": 100, "volume_qty": 100},
            {"code": "O_KCHOLE0526P200.00", "price": 1, "volume_tl": 200, "volume_qty": 200},
        ]
        tmp_db.save_snapshot(rows)
        only_bimas = tmp_db.get_today(underlying="BIMAS")
        assert len(only_bimas) == 1

    def test_idempotent_same_day_replace(self, tmp_db):
        # Saving the same code twice on the same day should REPLACE (not duplicate).
        # latest snapshot should reflect the new values.
        tmp_db.save_snapshot([
            {"code": "F_XU0300626", "price": 100, "volume_tl": 1000, "volume_qty": 1000},
        ])
        tmp_db.save_snapshot([
            {"code": "F_XU0300626", "price": 105, "volume_tl": 2000, "volume_qty": 2000},
        ])
        today = tmp_db.get_today()
        assert len(today) == 1
        assert today[0]["price"] == 105.0
        assert today[0]["volume_tl"] == 2000.0

    def test_history_returns_rows(self, tmp_db):
        # Insert two days manually with raw SQL
        c = tmp_db._conn()
        c.execute(
            "INSERT INTO viop_snapshots "
            "(fetched_at, snap_date, code, contract, category, kind, "
            " underlying, side, strike, expiry, price, change, volume_tl, volume_qty) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-05-12T08:00:00+00:00", "2026-05-12",
             "F_XU0300626", "X", "index", "future",
             "XU030", "F", None, "2026-06",
             100.0, 1.0, 1000.0, 1000.0),
        )
        c.execute(
            "INSERT INTO viop_snapshots "
            "(fetched_at, snap_date, code, contract, category, kind, "
            " underlying, side, strike, expiry, price, change, volume_tl, volume_qty) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-05-13T08:00:00+00:00", "2026-05-13",
             "F_XU0300626", "X", "index", "future",
             "XU030", "F", None, "2026-06",
             105.0, 5.0, 2000.0, 2000.0),
        )
        c.commit()
        hist = tmp_db.get_history("F_XU0300626", days=30)
        assert len(hist) == 2
        # Newest first
        assert hist[0]["snap_date"] == "2026-05-13"
        assert hist[1]["snap_date"] == "2026-05-12"

    def test_get_stats(self, tmp_db):
        rows = [
            {"code": "O_BIMASE0526C33.00", "category": "stock",
             "price": 1, "volume_tl": 100, "volume_qty": 100},
            {"code": "O_KCHOLE0526P200.00", "category": "stock",
             "price": 1, "volume_tl": 200, "volume_qty": 200},
            {"code": "F_XU0300626", "category": "index",
             "price": 1, "volume_tl": 300, "volume_qty": 300},
        ]
        tmp_db.save_snapshot(rows)
        stats = tmp_db.get_stats()
        assert stats["total_today"] == 3
        assert stats["by_kind"].get("option") == 2
        assert stats["by_kind"].get("future") == 1
        assert stats["by_category"].get("stock") == 2

    def test_save_handles_missing_optional_fields(self, tmp_db):
        # Missing 'change' is OK
        tmp_db.save_snapshot([
            {"code": "F_XU0300626", "price": 100,
             "volume_tl": 100, "volume_qty": 100},
        ])
        today = tmp_db.get_today()
        assert today[0]["change"] is None

    def test_save_skips_rows_without_code(self, tmp_db):
        tmp_db.save_snapshot([
            {"price": 100, "volume_tl": 100, "volume_qty": 100},
            {"code": "F_XU0300626", "price": 100,
             "volume_tl": 100, "volume_qty": 100},
        ])
        today = tmp_db.get_today()
        assert len(today) == 1


# ────────────────────────────────────────────────────────────────
# Feed ingest cycle
# ────────────────────────────────────────────────────────────────


class TestViopFeedCycle:
    def test_borsapy_unavailable_records_error(self, monkeypatch):
        # If borsapy import fails, cycle should not raise but record the error.
        import engine.viop_feed as feed
        import builtins
        real_import = builtins.__import__

        def _patched_import(name, *a, **kw):
            if name == "borsapy":
                raise ImportError("simulated")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _patched_import)
        res = feed.run_one_cycle()
        assert res.rows_persisted == 0
        assert any("borsapy" in e for e in res.errors)

    def test_cycle_persists_dataframe_rows(self, monkeypatch, tmp_db):
        """Mock borsapy.VIOP to return fake DataFrames; verify rows
        flow all the way into viop_storage."""
        import pandas as pd
        import engine.viop_feed as feed

        class _FakeVIOP:
            stock_options = pd.DataFrame([
                {"code": "O_BIMASE0526C33.00",
                 "contract": "BIMAS Mayis 2026 Call 33.00 E",
                 "category": "stock", "price": 1.0, "change": 0.0,
                 "volume_tl": 100.0, "volume_qty": 100.0},
            ])
            stock_futures = pd.DataFrame([])
            index_futures = pd.DataFrame([
                {"code": "F_XU0300626",
                 "contract": "XU030 Haziran",
                 "category": "index", "price": 12000, "change": 5,
                 "volume_tl": 2e10, "volume_qty": 2e10},
            ])
            index_options = pd.DataFrame([])
            currency_futures = pd.DataFrame([])
            commodity_futures = pd.DataFrame([])

        class _FakeBP:
            VIOP = _FakeVIOP

        monkeypatch.setitem(sys.modules, "borsapy", _FakeBP)
        # Re-point viop_storage to the test DB via the existing fixture's
        # already-patched DB_PATH; just re-init the connection.
        import infra.viop_storage as v
        v._local.conn = None
        v.init_db()

        res = feed.run_one_cycle()
        assert res.rows_persisted >= 2
        today = v.get_today()
        codes = {r["code"] for r in today}
        assert "O_BIMASE0526C33.00" in codes
        assert "F_XU0300626" in codes

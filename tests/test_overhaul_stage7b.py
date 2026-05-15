# ================================================================
# tests/test_overhaul_stage7b.py
#
# Great Overhaul Stage 7b: Daily Bulletin engine + storage + API.
#
# User request: "App içinde Günlük Bülten sayfası". This stage ships
# the backend pipeline:
#   - SQLite table daily_bulletin (migration 007)
#   - infra/bulletin_storage.py: CRUD
#   - engine/daily_bulletin.py: generator (composes from already-warm
#     BullWatch + KAP + heatmap + sector rotation sources)
#   - api/daily_brief.py: REST endpoints
#
# The schedule trigger that fires this AT 18:30 IST lives in Stage 7c
# (depends on Stage 7a schedule helper landing first).
# ================================================================

from __future__ import annotations

import datetime as dt
import json
import os
import sys

# Force test DB path BEFORE importing storage — same pattern as other
# DB-touching test modules (test_auth, test_delta, etc.).
os.environ.setdefault("BISTBULL_DB_PATH", "/tmp/test_bulletin.db")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Migration 007 created the table
# ────────────────────────────────────────────────────────────────


class TestMigration:
    def test_migration_file_exists(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "infra", "migrations",
            "007_daily_bulletin.sql",
        )
        assert os.path.exists(path)
        with open(path) as fh:
            sql = fh.read()
        assert "CREATE TABLE IF NOT EXISTS daily_bulletin" in sql

    def test_table_created_after_init_db(self):
        from infra import storage
        storage.init_db()
        conn = storage._get_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='daily_bulletin'"
        ).fetchone()
        assert row is not None


# ────────────────────────────────────────────────────────────────
# infra/bulletin_storage.py — CRUD
# ────────────────────────────────────────────────────────────────


class TestBulletinStorage:
    def test_save_and_get_round_trip(self):
        from infra import bulletin_storage as bs
        from infra import storage
        storage.init_db()

        date = "2026-05-13"
        content = {"headline": "test", "stats": {"conviction": 3}}
        bs.save(date, content)
        record = bs.get(date)

        assert record is not None
        assert record["bulletin_date"] == date
        assert record["content"]["headline"] == "test"
        assert record["content"]["stats"]["conviction"] == 3
        assert record["schema_version"] == bs.CURRENT_SCHEMA_VERSION

        bs.delete(date)

    def test_upsert_replaces_existing(self):
        from infra import bulletin_storage as bs
        from infra import storage
        storage.init_db()

        date = "2026-05-13"
        bs.save(date, {"version": "v1"})
        bs.save(date, {"version": "v2"})  # overwrite

        record = bs.get(date)
        assert record["content"]["version"] == "v2"
        bs.delete(date)

    def test_get_missing_returns_none(self):
        from infra import bulletin_storage as bs
        from infra import storage
        storage.init_db()
        assert bs.get("1999-01-01") is None

    def test_get_latest_returns_newest(self):
        from infra import bulletin_storage as bs
        from infra import storage
        storage.init_db()

        # Use far-future dates so other tests (which may have written
        # today's bulletin via generate_and_persist) can't poison the
        # "latest" lookup.
        dates = ("2099-01-01", "2099-01-02", "2099-01-03")
        for d in dates:
            bs.delete(d)

        bs.save(dates[0], {"day": 1})
        bs.save(dates[2], {"day": 3})  # newest
        bs.save(dates[1], {"day": 2})

        latest = bs.get_latest()
        assert latest["bulletin_date"] == dates[2]
        assert latest["content"]["day"] == 3

        for d in dates:
            bs.delete(d)

    def test_list_dates_returns_desc(self):
        from infra import bulletin_storage as bs
        from infra import storage
        storage.init_db()

        dates = ("2026-05-10", "2026-05-11", "2026-05-12")
        for d in dates:
            bs.delete(d)
        for d in dates:
            bs.save(d, {"date": d})

        result = bs.list_dates(limit=10)
        result_dates = [r["bulletin_date"] for r in result if r["bulletin_date"] in dates]
        assert result_dates == ["2026-05-12", "2026-05-11", "2026-05-10"]

        for d in dates:
            bs.delete(d)

    def test_delete_returns_true_only_when_deleted(self):
        from infra import bulletin_storage as bs
        from infra import storage
        storage.init_db()

        date = "2026-05-09"
        bs.save(date, {"x": 1})
        assert bs.delete(date) is True
        assert bs.delete(date) is False  # already gone

    def test_istanbul_today_iso_format(self):
        from infra import bulletin_storage as bs
        today = bs.istanbul_today()
        # YYYY-MM-DD
        assert len(today) == 10
        assert today[4] == "-" and today[7] == "-"
        dt.date.fromisoformat(today)  # raises if malformed


# ────────────────────────────────────────────────────────────────
# engine/daily_bulletin.py — generator contract
# ────────────────────────────────────────────────────────────────


class TestBulletinGenerator:
    def test_payload_has_required_sections(self):
        from engine.daily_bulletin import generate_bulletin_payload
        payload = generate_bulletin_payload()
        # All required top-level keys present
        required = {
            "schema_version", "generated_at", "headline", "stats",
            "conviction_top", "confirmed_new", "sector_rotation",
            "biggest_movers", "kap_highlights", "pre_alarms",
        }
        assert required.issubset(payload.keys())

    def test_biggest_movers_shape(self):
        from engine.daily_bulletin import generate_bulletin_payload
        payload = generate_bulletin_payload()
        mv = payload["biggest_movers"]
        assert "gainers" in mv
        assert "losers" in mv
        assert isinstance(mv["gainers"], list)
        assert isinstance(mv["losers"], list)

    def test_headline_is_string(self):
        from engine.daily_bulletin import generate_bulletin_payload
        payload = generate_bulletin_payload()
        assert isinstance(payload["headline"], str)
        assert len(payload["headline"]) > 0

    def test_generate_and_persist_writes_today_by_default(self):
        from engine.daily_bulletin import generate_and_persist
        from infra import bulletin_storage as bs
        from infra import storage
        storage.init_db()

        record = generate_and_persist()
        today = bs.istanbul_today()
        assert record["bulletin_date"] == today

        # Verify it actually landed in the DB
        stored = bs.get(today)
        assert stored is not None
        assert stored["content"]["headline"] == record["content"]["headline"]

    def test_generate_and_persist_with_override_date(self):
        from engine.daily_bulletin import generate_and_persist
        from infra import bulletin_storage as bs
        from infra import storage
        storage.init_db()

        target = "2026-04-30"
        bs.delete(target)
        record = generate_and_persist(target)
        assert record["bulletin_date"] == target
        assert bs.get(target) is not None
        bs.delete(target)


# ────────────────────────────────────────────────────────────────
# Safety: one source raising doesn't kill the bulletin
# ────────────────────────────────────────────────────────────────


class TestGeneratorResilience:
    def test_kap_source_failure_doesnt_break_bulletin(self, monkeypatch):
        """If kap_storage.get_recent raises, the bulletin still ships
        with empty kap_highlights but other sections intact."""
        from engine import daily_bulletin

        def _boom(*a, **kw):
            raise RuntimeError("kap storage offline")

        try:
            from infra import kap_storage
            monkeypatch.setattr(kap_storage, "get_recent", _boom)
        except ImportError:
            pytest.skip("kap_storage not importable in this build")

        payload = daily_bulletin.generate_bulletin_payload()
        # Bulletin completed despite the error
        assert "headline" in payload
        # The kap section degraded gracefully to []
        assert payload["kap_highlights"] == []


# ────────────────────────────────────────────────────────────────
# REST endpoints
# ────────────────────────────────────────────────────────────────


class TestEndpoints:
    def test_router_registered(self):
        from api.daily_brief import router
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/api/daily-brief" in paths
        assert "/api/daily-brief/{bulletin_date}" in paths
        assert "/api/daily-brief/history" in paths
        assert "/api/daily-brief/regenerate" in paths

    @pytest.mark.asyncio
    async def test_get_latest_returns_envelope(self):
        from api.daily_brief import api_daily_brief_latest
        from infra import storage
        storage.init_db()
        resp = await api_daily_brief_latest()
        body = json.loads(resp.body.decode("utf-8"))
        # success() envelope wraps under "data" or flat — either way,
        # we should see either "bulletin" or "message" surfaced.
        flat = {**body, **body.get("data", {})}
        assert "bulletin" in flat or "message" in flat

    @pytest.mark.asyncio
    async def test_invalid_date_format_rejected(self):
        """Endpoint must reject anything that isn't YYYY-MM-DD."""
        from api.daily_brief import api_daily_brief_by_date
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await api_daily_brief_by_date("not-a-date")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_date_returns_404_envelope(self):
        from api.daily_brief import api_daily_brief_by_date
        from infra import storage, bulletin_storage as bs
        storage.init_db()
        bs.delete("2026-01-01")
        resp = await api_daily_brief_by_date("2026-01-01")
        # error() helper returns a JSONResponse with status_code 404
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_history_returns_dates_array(self):
        from api.daily_brief import api_daily_brief_history
        from infra import storage, bulletin_storage as bs
        storage.init_db()
        # Seed one entry to guarantee non-empty
        bs.save("2026-05-13", {"x": 1})
        resp = await api_daily_brief_history(limit=10)
        body = json.loads(resp.body.decode("utf-8"))
        flat = {**body, **body.get("data", {})}
        assert "dates" in flat
        assert isinstance(flat["dates"], list)
        bs.delete("2026-05-13")

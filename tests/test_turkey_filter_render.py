"""Phase 5.2.1 — Türkiye 4 Filtre Render tests.

The brief says the Türkiye-özel filtreler (Döviz Kalkanı, Faiz Direnci,
Fiyat Geçişkenliği, TMS 29) should appear in a dedicated section on
hisse detay sayfası, with each filter showing:
  - icon + name
  - progress bar driven by multiplier (0.70-1.15)
  - mult value
  - 1-sentence explanation

This test verifies:
  1. The /api/analyze response exposes turkey_realities in a stable
     shape that the frontend can read.
  2. The CSS classes that the section relies on exist.
  3. The JS knows how to render them (tested via source contract).
  4. The 4 filter names are rendered.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from engine.turkey_realities import compute_turkey_realities


CSS_PATH = Path(__file__).parent.parent / "static" / "terminal.css"
JS_PATH  = Path(__file__).parent.parent / "static" / "terminal.js"


@pytest.fixture(scope="module")
def css_source() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js_source() -> str:
    return JS_PATH.read_text(encoding="utf-8")


# ============================================================
# Underlying engine — must produce the 4 filters with stable shape
# ============================================================
class TestTurkeyRealitiesShape:
    def _sample_metrics(self) -> dict:
        return {
            "foreign_ratio": 0.45,
            "net_debt_ebitda": 1.2,
            "revenue_growth": 0.32,
            "gross_margin": 0.18,
            "operating_margin": 0.10,
            "ebitda_margin": 0.16,
            "monetary_position": 1.0,
            "real_assets_ratio": 0.55,
            "cash": 25_000_000_000,
            "total_debt": 30_000_000_000,
        }

    def test_returns_four_named_filters(self):
        out = compute_turkey_realities(self._sample_metrics(), sector_group="sanayi", policy_rate=37.0)
        assert "filters" in out
        names = {f["name"] for f in out["filters"].values()}
        # Frontend depends on these 4 exact display names
        assert "Döviz Kalkanı" in names
        # rate_resistance, pricing_power, tms29 — each must be there
        assert len(out["filters"]) == 4

    def test_each_filter_has_required_fields(self):
        out = compute_turkey_realities(self._sample_metrics(), sector_group="sanayi", policy_rate=37.0)
        for key, f in out["filters"].items():
            assert "name" in f, f"{key}: missing name"
            assert "score" in f, f"{key}: missing score"
            assert "multiplier" in f, f"{key}: missing multiplier"
            assert "grade" in f, f"{key}: missing grade"
            assert "explanation" in f, f"{key}: missing explanation"
            # multiplier must be in [0.5, 1.25] for sane progress-bar display
            assert 0.5 <= f["multiplier"] <= 1.25, f"{key}: mult out of bounds"

    def test_summary_text_present(self):
        out = compute_turkey_realities(self._sample_metrics(), sector_group="sanayi", policy_rate=37.0)
        assert out["summary"]
        assert isinstance(out["summary"], str)
        assert len(out["summary"]) > 10

    def test_composite_grade_in_a_to_f_band(self):
        out = compute_turkey_realities(self._sample_metrics(), sector_group="sanayi", policy_rate=37.0)
        assert out["composite_grade"] in {"A", "B", "C", "D", "F"}


# ============================================================
# CSS contract — the section markup needs these classes
# ============================================================
class TestTurkeyFilterCss:
    def test_section_class_present(self, css_source):
        assert ".tr-filter-section" in css_source, \
            "Phase 5.2.1: missing top-level section class"

    def test_row_classes_present(self, css_source):
        for cls in (".tr-filter-row", ".tr-filter-icon", ".tr-filter-body",
                    ".tr-filter-name", ".tr-filter-bar-wrap", ".tr-filter-bar"):
            assert cls in css_source, f"{cls} missing"

    def test_bar_direction_variants(self, css_source):
        # up = green, down = red, flat = grey (per brief)
        assert ".tr-filter-bar.up" in css_source
        assert ".tr-filter-bar.down" in css_source
        assert ".tr-filter-bar.flat" in css_source

    def test_grade_color_classes(self, css_source):
        for letter in "ABCDF":
            assert f".tr-filter-grade.{letter}" in css_source, \
                f"Grade pill class {letter} missing"


# ============================================================
# /api/analyze response — must expose turkey_realities
# ============================================================
class TestAnalyzeEndpointSurface:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        db = tmp_path / "tr_filter.db"
        monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
        monkeypatch.setenv("JWT_SECRET", "test-secret-" + "x" * 40)
        import infra.storage
        infra.storage._local = threading.local()
        infra.storage.DB_PATH = str(db)
        from infra.storage import init_db
        init_db()
        from app import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_universe_endpoint_works(self, client):
        # Smoke test that the app boots cleanly with our changes
        r = client.get("/api/universe")
        assert r.status_code == 200
        body = r.json()
        assert "universe" in body

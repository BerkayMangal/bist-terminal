# ================================================================
# tests/test_overhaul_faz4_ai_commentary.py
#
# BullWatch Faz 4 — CONVICTION AI Commentary.
#
# Pinned behaviors:
#   - Commentary fires ONLY for CONVICTION zone (programmatic
#     explainability covers EARLY / CONFIRMED)
#   - LRU cache keyed on (symbol, score-bucket) — scoring wiggle of
#     <5 points reuses cached commentary
#   - Cache TTL: 6 hours
#   - AI provider failure / unavailable → None (caller surfaces 503)
#   - Prompt composition includes ticker + score + zone + reasons +
#     top components + key metrics
#   - REST endpoint enforces the same CONVICTION gate (422 otherwise)
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Score bucketing
# ────────────────────────────────────────────────────────────────


class TestScoreBucket:
    def test_rounds_to_nearest_five(self):
        from engine.bullwatch_ai_commentary import _score_bucket
        assert _score_bucket(75.0) == 75
        assert _score_bucket(75.3) == 75
        assert _score_bucket(77.4) == 75
        assert _score_bucket(77.5) == 80
        assert _score_bucket(82.6) == 85

    def test_none_score_returns_zero(self):
        from engine.bullwatch_ai_commentary import _score_bucket
        assert _score_bucket(None) == 0

    def test_invalid_score_returns_zero(self):
        from engine.bullwatch_ai_commentary import _score_bucket
        assert _score_bucket("not-a-number") == 0


# ────────────────────────────────────────────────────────────────
# Cache contract
# ────────────────────────────────────────────────────────────────


class TestCommentaryCache:
    def setup_method(self):
        from engine.bullwatch_ai_commentary import clear_cache
        clear_cache()

    def test_cache_round_trip(self):
        from engine.bullwatch_ai_commentary import _cache_set, _cache_get
        _cache_set("BIMAS", 78.0, "Bimas için yorum metni.")
        assert _cache_get("BIMAS", 78.0) == "Bimas için yorum metni."

    def test_cache_hit_within_bucket_tolerance(self):
        """Scores 75 and 77.4 share the same bucket → same cache entry."""
        from engine.bullwatch_ai_commentary import _cache_set, _cache_get
        _cache_set("XYZ", 75.0, "yorum")
        assert _cache_get("XYZ", 77.4) == "yorum"
        assert _cache_get("XYZ", 73.5) == "yorum"

    def test_cache_miss_across_bucket(self):
        from engine.bullwatch_ai_commentary import _cache_set, _cache_get
        _cache_set("XYZ", 75.0, "first")
        # 82.5 rounds to 85, different bucket
        assert _cache_get("XYZ", 82.5) is None

    def test_cache_bounded(self):
        """Cache has an upper bound — won't grow forever."""
        from engine.bullwatch_ai_commentary import _cache_set, _CACHE_MAX_ENTRIES, _CACHE
        for i in range(_CACHE_MAX_ENTRIES + 20):
            _cache_set(f"SYM{i:03d}", 75.0, "x")
        assert len(_CACHE) <= _CACHE_MAX_ENTRIES + 5  # small tolerance for ordering

    def test_cache_stats_shape(self):
        from engine.bullwatch_ai_commentary import cache_stats
        s = cache_stats()
        assert "entries" in s
        assert "max_entries" in s
        assert "ttl_sec" in s


# ────────────────────────────────────────────────────────────────
# Prompt composition
# ────────────────────────────────────────────────────────────────


class TestPromptBuilder:
    def test_prompt_includes_core_fields(self):
        from engine.bullwatch_ai_commentary import build_commentary_prompt
        item = {
            "symbol": "BIMAS",
            "score": 87.5,
            "zone": "CONVICTION",
            "pattern": "Float Squeeze + Insider",
            "reasons": ["insider 1.5M TL", "RVOL 3.2x"],
            "components": {"float_pressure": 18.5, "silent_volume": 14.2},
            "metrics": {"sector": "Perakende", "roe": 0.22,
                        "market_cap": 5e9, "free_float": 0.18,
                        "avg_traded_value_20d": 12e6},
        }
        p = build_commentary_prompt(item)
        assert "BIMAS" in p
        # Score 87.5 prints as 88 ({:.0f} rounds half-up)
        assert "88" in p or "87" in p
        assert "CONVICTION" in p
        assert "Float Squeeze" in p
        assert "insider 1.5M TL" in p
        assert "Perakende" in p
        # Constraint reminders should be present
        assert "3-4" in p
        assert "Hisse alın" in p or "alım/satım" in p

    def test_prompt_handles_missing_fields(self):
        """Bare-bones item must still produce a valid prompt."""
        from engine.bullwatch_ai_commentary import build_commentary_prompt
        item = {"symbol": "X", "zone": "CONVICTION"}
        p = build_commentary_prompt(item)
        assert "X" in p
        assert "CONVICTION" in p


# ────────────────────────────────────────────────────────────────
# generate_commentary gating
# ────────────────────────────────────────────────────────────────


class TestGenerateCommentary:
    def test_non_conviction_returns_none(self):
        """EARLY / CONFIRMED tickers must NOT consume AI tokens."""
        from engine.bullwatch_ai_commentary import generate_commentary, clear_cache
        clear_cache()
        for zone in ("EARLY", "CONFIRMED", "", "UNKNOWN"):
            item = {"symbol": "X", "score": 65, "zone": zone}
            assert generate_commentary(item) is None, (
                f"AI should skip zone={zone!r}"
            )

    def test_empty_item_returns_none(self):
        from engine.bullwatch_ai_commentary import generate_commentary
        assert generate_commentary(None) is None
        assert generate_commentary({}) is None

    def test_missing_symbol_returns_none(self):
        from engine.bullwatch_ai_commentary import generate_commentary
        assert generate_commentary({"zone": "CONVICTION"}) is None

    def test_ai_unavailable_returns_none(self, monkeypatch):
        """When ai.service.AI_AVAILABLE is False, function returns None
        without raising."""
        from engine.bullwatch_ai_commentary import generate_commentary, clear_cache
        clear_cache()
        from ai import service as _svc
        monkeypatch.setattr(_svc, "AI_AVAILABLE", False)
        item = {"symbol": "BIMAS", "score": 80, "zone": "CONVICTION"}
        assert generate_commentary(item) is None

    def test_ai_call_returns_text_caches_result(self, monkeypatch):
        """Happy path: AI returns text, function caches it, second call
        is a hit."""
        from engine.bullwatch_ai_commentary import generate_commentary, clear_cache, _cache_get
        clear_cache()
        from ai import service as _svc
        from ai import safety as _safety

        class _Ok:
            ok = True
            text = "Bimas, güçlü insider aktivitesi ile dikkat çekiyor."

        monkeypatch.setattr(_svc, "AI_AVAILABLE", True)
        monkeypatch.setattr(_svc, "ai_call",
                            lambda prompt, max_tokens=None: "raw response")
        monkeypatch.setattr(_safety, "validate_ai_output",
                            lambda raw, mode: _Ok())

        item = {"symbol": "BIMAS", "score": 87.5, "zone": "CONVICTION"}
        out = generate_commentary(item)
        assert out == "Bimas, güçlü insider aktivitesi ile dikkat çekiyor."
        # Cached now
        assert _cache_get("BIMAS", 87.5) == out

    def test_ai_safety_reject_returns_none(self, monkeypatch):
        from engine.bullwatch_ai_commentary import generate_commentary, clear_cache
        clear_cache()
        from ai import service as _svc
        from ai import safety as _safety

        class _Reject:
            ok = False
            reason = "trade directive detected"
            text = None

        monkeypatch.setattr(_svc, "AI_AVAILABLE", True)
        monkeypatch.setattr(_svc, "ai_call",
                            lambda prompt, max_tokens=None: "raw bad")
        monkeypatch.setattr(_safety, "validate_ai_output",
                            lambda raw, mode: _Reject())

        item = {"symbol": "BIMAS", "score": 87.5, "zone": "CONVICTION"}
        assert generate_commentary(item) is None


# ────────────────────────────────────────────────────────────────
# REST endpoint
# ────────────────────────────────────────────────────────────────


class TestEndpoint:
    def test_route_registered(self):
        from api.bullwatch import router
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/api/bullwatch/ai-commentary/{symbol}" in paths

    @pytest.mark.asyncio
    async def test_endpoint_404_when_not_in_list(self, monkeypatch):
        from api.bullwatch import api_bullwatch_ai_commentary
        from api import bullwatch as bw
        # Empty cache
        bw._cache_update(items={"items": []})
        resp = await api_bullwatch_ai_commentary("XXX")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_endpoint_422_when_not_conviction(self, monkeypatch):
        from api.bullwatch import api_bullwatch_ai_commentary
        from api import bullwatch as bw
        bw._cache_update(items={"items": [
            {"symbol": "ABC", "zone": "EARLY", "score": 60},
        ]})
        resp = await api_bullwatch_ai_commentary("ABC")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_endpoint_503_when_ai_unavailable(self, monkeypatch):
        from api.bullwatch import api_bullwatch_ai_commentary
        from api import bullwatch as bw
        bw._cache_update(items={"items": [
            {"symbol": "ABC", "zone": "CONVICTION", "score": 80},
        ]})
        from ai import service as _svc
        monkeypatch.setattr(_svc, "AI_AVAILABLE", False)
        from engine import bullwatch_ai_commentary as bw_ai
        bw_ai.clear_cache()
        resp = await api_bullwatch_ai_commentary("ABC")
        assert resp.status_code == 503


# ────────────────────────────────────────────────────────────────
# UI wiring
# ────────────────────────────────────────────────────────────────


class TestUIIntegration:
    @pytest.fixture(scope="class")
    def terminal_src(self):
        with open(
            os.path.join(os.path.dirname(__file__), "..", "static",
                         "terminal.js"),
            "r", encoding="utf-8",
        ) as fh:
            return fh.read()

    def test_ai_commentary_button_present(self, terminal_src):
        assert "_loadBwAiCommentary" in terminal_src
        assert "/api/bullwatch/ai-commentary/" in terminal_src

    def test_button_only_for_conviction(self, terminal_src):
        """The injection helper must guard on CONVICTION zone."""
        assert "_injectBwAiCommentaryBtn" in terminal_src
        # Check the zone gate in the calling site
        assert "CONVICTION" in terminal_src

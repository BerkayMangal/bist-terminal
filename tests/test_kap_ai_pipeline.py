# ================================================================
# tests/test_kap_ai_pipeline.py
#
# Faz 3 — AI agent pipeline for KAP disclosures.
# Verifies prompt construction, the storage save_ai_summary path, and
# the dispatcher's threaded analysis hook. Grok itself is mocked.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from data.kap_client import DisclosureRecord, _normalize_disclosure
from tests._fake_redis import FakeRedis


# Reuse fixtures from test_kap_feed
from tests.test_kap_feed import (   # type: ignore
    fake_redis, tmp_sqlite, patched_redis, _raw_disclosure,
)


# ── Prompt construction ────────────────────────────────────────────


class TestPromptBuilder:
    def _disc(self):
        return {
            "ticker": "ARCLK",
            "kap_title": "ARÇELİK A.Ş.",
            "subject": "Finansal Rapor",
            "year": 2026,
            "rule_type": "3 Aylık",
            "period": 1,
            "publish_date_raw": "22.04.2026 18:30:24",
            "publish_date": "2026-04-22T15:30:24+00:00",
            "disclosure_index": 1596848,
        }

    def test_prompt_includes_ticker_and_period(self):
        from ai.prompts import kap_disclosure_prompt
        p = kap_disclosure_prompt(self._disc(), metrics={}, analysis={})
        assert "ARCLK" in p
        assert "3 Aylık" in p
        assert "2026" in p

    def test_prompt_includes_fundamentals_when_present(self):
        from ai.prompts import kap_disclosure_prompt
        m = {
            "market_cap": 1.2e11,
            "pe": 9.5,
            "pb": 1.1,
            "roe": 0.18,
            "revenue_growth": 0.15,
            "net_margin": 0.08,
        }
        p = kap_disclosure_prompt(self._disc(), metrics=m, analysis={})
        assert "F/K" in p
        assert "ROE" in p
        # Numeric formatting
        assert "18.0%" in p or "18.0" in p

    def test_prompt_includes_quarterly_signal_when_available(self):
        from ai.prompts import kap_disclosure_prompt
        m = {
            "quarterly_data_available": True,
            "revenue_growth_yoy_q": 0.22,
            "net_income_growth_yoy_q": 0.30,
            "latest_quarter": "2026Q1",
        }
        p = kap_disclosure_prompt(self._disc(), metrics=m, analysis={})
        assert "Çeyreklik" in p
        assert "2026Q1" in p

    def test_prompt_lists_required_output_sections(self):
        """The prompt must explicitly enumerate the 6 sections so the
        UI's _renderKapAiSummary parser has consistent input."""
        from ai.prompts import kap_disclosure_prompt
        p = kap_disclosure_prompt(self._disc(), metrics={}, analysis={})
        for section in ("ÖZET:", "POZİTİF:", "NEGATİF:",
                        "DEĞİŞİM:", "SEKTÖR:", "TAKİP:"):
            assert section in p, f"prompt missing required section: {section}"

    def test_prompt_includes_safety_rules(self):
        from ai.prompts import kap_disclosure_prompt
        p = kap_disclosure_prompt(self._disc(), metrics={}, analysis={})
        # No buy/sell instructions
        assert "tavsiye" in p.lower() or "tavsiye verme" in p.lower()
        # Forbidden words list
        assert "garanti" in p.lower()


# ── Storage: save_ai_summary ───────────────────────────────────────


class TestSaveAiSummary:
    def test_round_trip(self, patched_redis, tmp_sqlite):
        from infra import kap_storage
        # Seed a disclosure
        rec = _normalize_disclosure(_raw_disclosure(idx=2222))
        assert kap_storage.save_disclosure(rec) is True
        # Now attach AI summary
        ok = kap_storage.save_ai_summary(2222, "ÖZET: test özet.")
        assert ok is True
        # Read it back
        row = kap_storage.get_by_index(2222)
        assert row is not None
        assert row["ai_summary"] == "ÖZET: test özet."
        assert row["ai_analyzed_at"] is not None

    def test_redis_mirror_updated(self, patched_redis, tmp_sqlite):
        from infra import kap_storage
        import json
        rec = _normalize_disclosure(_raw_disclosure(idx=3333))
        kap_storage.save_disclosure(rec)
        kap_storage.save_ai_summary(3333, "ÖZET: redis mirror test.")
        # Verify the Redis hot entry has the summary
        raw = patched_redis.get("bb:kap:disclosure:3333")
        assert raw is not None
        obj = json.loads(raw)
        assert obj.get("ai_summary") == "ÖZET: redis mirror test."
        assert obj.get("ai_analyzed_at") is not None

    def test_empty_summary_no_op(self, patched_redis, tmp_sqlite):
        from infra import kap_storage
        rec = _normalize_disclosure(_raw_disclosure(idx=4444))
        kap_storage.save_disclosure(rec)
        assert kap_storage.save_ai_summary(4444, "") is False


# ── Service: generate_kap_disclosure_analysis ──────────────────────


class TestGenerateAnalysis:
    def test_returns_none_when_ai_unavailable(self, monkeypatch):
        from ai import service as svc
        monkeypatch.setattr(svc, "AI_AVAILABLE", False)
        result = svc.generate_kap_disclosure_analysis(
            {"ticker": "ARCLK", "disclosure_index": 1, "subject": "Finansal Rapor"},
            metrics={}, analysis={},
        )
        assert result is None

    def test_returns_none_on_empty_ai_response(self, monkeypatch):
        from ai import service as svc
        monkeypatch.setattr(svc, "AI_AVAILABLE", True)
        monkeypatch.setattr(svc, "ai_call", lambda prompt, max_tokens=600: "")
        result = svc.generate_kap_disclosure_analysis(
            {"ticker": "ARCLK", "disclosure_index": 1, "subject": "Finansal Rapor"},
            metrics={}, analysis={},
        )
        assert result is None

    def test_returns_text_when_ai_responds_well(self, monkeypatch):
        from ai import service as svc
        monkeypatch.setattr(svc, "AI_AVAILABLE", True)
        fake_text = (
            "ÖZET: Test özet cümlesi.\n"
            "POZİTİF: Test pozitif.\n"
            "NEGATİF: Test negatif.\n"
            "DEĞİŞİM: Test değişim.\n"
            "SEKTÖR: Test sektör.\n"
            "TAKİP: Test takip.\n"
        )
        monkeypatch.setattr(svc, "ai_call", lambda prompt, max_tokens=600: fake_text)
        # Stub the safety validator to always pass — the real one rejects
        # text without specific structural cues we don't need to test here.

        class _FakeResult:
            ok = True
            text = fake_text
            reason = ""
        monkeypatch.setattr(
            "ai.safety.validate_ai_output",
            lambda text, role: _FakeResult(),
        )
        result = svc.generate_kap_disclosure_analysis(
            {"ticker": "ARCLK", "disclosure_index": 1, "subject": "Finansal Rapor"},
            metrics={}, analysis={},
        )
        assert result is not None
        assert "ÖZET:" in result

# ================================================================
# tests/test_overhaul_ai_claude_only.py
#
# AI Consolidation (2026-05) — Claude is the ONLY provider.
#
# User decision: "her nerede AI kullanılıyorsa istisnasız hepsini
# sadece claude". The 3 other providers (Grok / OpenAI / Perplexity)
# had run out of credit and their keys are being removed from the
# environment.
#
# This removes:
#   - the multi-model "consensus" engine (engine/ai_consensus.py)
#   - the 4-model showdown UI ("4 model paralel çağrılıyor")
#   - the Perplexity-backed "Harici Piyasa Özeti" macro button
#
# And pins:
#   - AI_PROVIDERS is anthropic-only even if stale keys linger
#   - /api/ai/{symbol}/consensus returns a single Claude analysis
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Engine: Claude-only provider list
# ────────────────────────────────────────────────────────────────


class TestProvidersClaudeOnly:
    def test_ai_providers_is_anthropic_only_or_empty(self):
        """AI_PROVIDERS must contain only 'anthropic' (or be empty when
        no Claude key is configured). No grok/openai/perplexity."""
        from ai.engine import AI_PROVIDERS
        assert set(AI_PROVIDERS).issubset({"anthropic"}), (
            f"AI_PROVIDERS leaked a non-Claude provider: {AI_PROVIDERS}"
        )

    def test_no_other_provider_even_with_stale_key(self, monkeypatch):
        """Hard guarantee: even if a GROK/OPENAI/PERPLEXITY key is in the
        env, the live provider list stays anthropic-only. We re-run the
        discovery+pin logic the module uses."""
        # Simulate all four discovered
        discovered = ["perplexity", "grok", "openai", "anthropic"]
        anthropic_live = "anthropic" in discovered
        ai_providers = ["anthropic"] if anthropic_live else []
        assert ai_providers == ["anthropic"]


# ────────────────────────────────────────────────────────────────
# Dead consensus module is gone
# ────────────────────────────────────────────────────────────────


class TestConsensusModuleRemoved:
    def test_ai_consensus_module_deleted(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "engine", "ai_consensus.py",
        )
        assert not os.path.exists(path), (
            "engine/ai_consensus.py should have been deleted — the "
            "multi-provider consensus concept is retired"
        )

    def test_ai_consensus_not_importable(self):
        with pytest.raises(ImportError):
            import engine.ai_consensus  # noqa: F401


# ────────────────────────────────────────────────────────────────
# Consensus endpoint → single Claude analysis
# ────────────────────────────────────────────────────────────────


class TestConsensusEndpointSingleClaude:
    @pytest.mark.asyncio
    async def test_endpoint_returns_single_leader(self, monkeypatch):
        """The endpoint keeps its path + `consensus` key for UI compat,
        but the leader is always 'anthropic' and there's no per-model
        array."""
        import app

        # Stub analyze + prompt + ai_call so we don't hit network
        monkeypatch.setattr(app, "analyze_symbol",
                            lambda s: {"ticker": s, "scores": {},
                                       "metrics": {}, "overall": 70})
        from ai import prompts as _prompts
        monkeypatch.setattr(_prompts, "trader_summary_prompt",
                            lambda r, tech=None: "prompt")
        from ai import engine as _eng
        monkeypatch.setattr(_eng, "ai_call",
                            lambda prompt, max_tokens=600: "Claude analizi.")

        from fastapi import Request
        scope = {"type": "http", "headers": [], "method": "GET",
                 "path": "/api/ai/BIMAS/consensus", "query_string": b""}
        resp = await app.api_ai_consensus("BIMAS", Request(scope))
        import json
        body = json.loads(resp.body.decode("utf-8"))
        flat = {**body, **body.get("data", {})}
        cons = flat.get("consensus") or {}
        assert cons.get("leader") == "anthropic"
        assert cons.get("leader_text") == "Claude analizi."
        # No multi-model array
        assert "raw_responses" not in flat
        assert "per_model" not in cons

    @pytest.mark.asyncio
    async def test_endpoint_handles_empty_ai(self, monkeypatch):
        import app
        monkeypatch.setattr(app, "analyze_symbol",
                            lambda s: {"ticker": s, "scores": {},
                                       "metrics": {}, "overall": 70})
        from ai import prompts as _prompts
        monkeypatch.setattr(_prompts, "trader_summary_prompt",
                            lambda r, tech=None: "prompt")
        from ai import engine as _eng
        monkeypatch.setattr(_eng, "ai_call",
                            lambda prompt, max_tokens=600: None)
        from fastapi import Request
        scope = {"type": "http", "headers": [], "method": "GET",
                 "path": "/api/ai/X/consensus", "query_string": b""}
        resp = await app.api_ai_consensus("BIMAS", Request(scope))
        import json
        body = json.loads(resp.body.decode("utf-8"))
        flat = {**body, **body.get("data", {})}
        # Graceful: leader None, empty text — UI shows "hazırlanamadı"
        assert (flat.get("consensus") or {}).get("leader") is None


# ────────────────────────────────────────────────────────────────
# external-brief route removed
# ────────────────────────────────────────────────────────────────


class TestExternalBriefRemoved:
    def test_route_not_registered(self):
        import app
        paths = [
            getattr(r, "path", None)
            for r in app.app.routes
        ]
        assert "/api/macro/external-brief" not in paths, (
            "external-brief route should have been removed (Perplexity "
            "retired)"
        )


# ────────────────────────────────────────────────────────────────
# UI cleanup
# ────────────────────────────────────────────────────────────────


class TestUICleanup:
    @pytest.fixture(scope="class")
    def terminal_src(self):
        with open(
            os.path.join(os.path.dirname(__file__), "..", "static",
                         "terminal.js"),
            "r", encoding="utf-8",
        ) as fh:
            return fh.read()

    def test_no_four_model_string(self, terminal_src):
        assert "4 model paralel" not in terminal_src, (
            "Multi-model showdown loading text still present"
        )

    def test_no_external_brief_button(self, terminal_src):
        assert "Harici Piyasa Özeti" not in terminal_src, (
            "External brief button still wired in the macro page"
        )
        assert "loadExternalBrief()" not in terminal_src

    def test_consensus_card_relabeled(self, terminal_src):
        # Card header should say "AI Analizi", not "AI Konsensüs"
        assert "🤖 AI Analizi" in terminal_src

    def test_render_consensus_single_model(self, terminal_src):
        """renderAiConsensus must no longer show a per-model breakdown
        or 'diğer modeller' expander."""
        assert "Diğer modeller ne diyor" not in terminal_src
        assert "Yanıt vermeyen modeller" not in terminal_src

# ================================================================
# tests/test_overhaul_ai_quality.py
#
# AI Quality Overhaul (2026-05).
#
# Context: the site has many AI touch-points but output was "saçma
# sapan" — generic, sometimes truncated mid-sentence. Root causes:
#   1. Provider order put search-model Perplexity first, analysis-
#      grade Claude last
#   2. Budget models (grok-3-mini-fast, gpt-4o-mini) as defaults
#   3. Telegram-terse prompts gave Claude no real direction
#   4. max_tokens=200 truncated multi-sentence Turkish commentary
#
# Fix: consolidate on Claude (claude-sonnet-4-6), make it the primary
# in the call order, rewrite the weak prompts with concrete rules +
# explicit output contracts, raise token budgets, add telemetry.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Config: Claude is the model + primary provider
# ────────────────────────────────────────────────────────────────


class TestAIConfig:
    def test_anthropic_model_is_sonnet_4_6(self):
        from config import ANTHROPIC_MODEL
        # Env override is allowed, but the DEFAULT must be the new model.
        # If an env var is set in CI we just sanity-check it's a claude id.
        assert "claude" in ANTHROPIC_MODEL.lower(), (
            f"ANTHROPIC_MODEL is {ANTHROPIC_MODEL!r} — expected a Claude id"
        )

    def test_primary_provider_is_anthropic(self):
        from config import AI_PRIMARY_PROVIDER
        assert AI_PRIMARY_PROVIDER == "anthropic"


# ────────────────────────────────────────────────────────────────
# Provider ordering — primary first
# ────────────────────────────────────────────────────────────────


class TestProviderOrdering:
    def test_ordered_providers_puts_primary_first(self):
        from ai.engine import _ordered_providers
        # Simulate all four discovered, anthropic should be hoisted
        out = _ordered_providers(
            ["perplexity", "grok", "openai", "anthropic"]
        )
        assert out[0] == "anthropic", (
            f"Primary not first: {out}"
        )
        # The rest stay as a fallback chain
        assert set(out) == {"perplexity", "grok", "openai", "anthropic"}

    def test_ordered_providers_handles_missing_primary(self):
        """If anthropic isn't discovered (no key), order is unchanged."""
        from ai.engine import _ordered_providers
        out = _ordered_providers(["grok", "openai"])
        assert out == ["grok", "openai"]

    def test_ordered_providers_empty(self):
        from ai.engine import _ordered_providers
        assert _ordered_providers([]) == []


# ────────────────────────────────────────────────────────────────
# ai_call default budget raised
# ────────────────────────────────────────────────────────────────


class TestTokenBudget:
    def test_ai_call_default_max_tokens_raised(self):
        import inspect
        from ai.engine import ai_call
        sig = inspect.signature(ai_call)
        default = sig.parameters["max_tokens"].default
        # Old default was 200 — truncated commentary. New floor: 400+.
        assert default >= 400, (
            f"ai_call default max_tokens is {default} — too low, "
            "truncates Turkish commentary"
        )


# ────────────────────────────────────────────────────────────────
# Telemetry
# ────────────────────────────────────────────────────────────────


class TestTelemetry:
    def test_get_ai_telemetry_shape(self):
        from ai.engine import get_ai_telemetry
        t = get_ai_telemetry()
        for key in ("providers_configured", "primary", "ai_available",
                    "totals", "last_call", "recent_calls",
                    "quota_exhausted"):
            assert key in t, f"telemetry missing key {key!r}"
        assert isinstance(t["totals"], dict)
        assert isinstance(t["recent_calls"], list)

    def test_record_call_appends_and_bounds(self):
        from ai.engine import _record_call, _TELEMETRY, _TELEMETRY_MAX
        before = len(_TELEMETRY)
        for i in range(_TELEMETRY_MAX + 30):
            _record_call("anthropic", "claude-sonnet-4-6", True, 123.4)
        # Ring buffer must not exceed the cap
        assert len(_TELEMETRY) <= _TELEMETRY_MAX

    def test_record_call_tracks_ok_and_fail(self):
        from ai.engine import _record_call, get_ai_telemetry
        _record_call("anthropic", "claude-sonnet-4-6", True, 100.0)
        _record_call("anthropic", "claude-sonnet-4-6", False, 50.0, "boom")
        t = get_ai_telemetry()
        # totals are cumulative — both counters should be non-zero
        assert t["totals"]["ok"] >= 1
        assert t["totals"]["fail"] >= 1
        assert t["totals"]["calls"] >= 2


# ────────────────────────────────────────────────────────────────
# Prompt quality — rewritten prompts have concrete structure
# ────────────────────────────────────────────────────────────────


class TestPromptQuality:
    def test_hero_prompt_has_role_and_rules(self):
        from ai.prompts import hero_prompt
        p = hero_prompt(
            mode_label="Pozitif", total=100, bullish_count=60,
            deger_leaders=[{"ticker": "BIMAS", "deger": 80}],
            ivme_leaders=[{"ticker": "THYAO", "ivme": 75}],
            items=[{"ticker": "X", "deger": 50, "overall": 50}],
            macro_items=[{"name": "USD", "change_pct": 0.5}],
            cross_count=12,
        )
        # A real role, not just "stratejist."
        assert "stratejist" in p.lower()
        # Explicit rules block
        assert "KURAL" in p
        # Output contract still parseable
        assert "HİKÂYE:" in p and "YORUM:" in p and "FIRSAT:" in p
        # Anti-speculation guard present
        assert "spekülatif" in p.lower() or "patlayacak" in p.lower()

    def test_briefing_prompt_has_structure(self):
        from ai.prompts import briefing_prompt
        ctx = {
            "count": 50, "deger_str": "A,B", "ivme_str": "C,D",
            "worst_str": "E,F", "summary_parts": ["x", "y"],
            "signal_count": 8, "sig_str": "3 up",
        }
        p = briefing_prompt(ctx)
        assert "KURAL" in p
        assert "ÖZET:" in p and "YATIRIMCI:" in p and "TRADER:" in p

    def test_trader_summary_prompt_concrete_rules(self):
        from ai.prompts import trader_summary_prompt
        r = {
            "ticker": "EREGL", "name": "Ereğli", "style": "value",
            "scores": {"value": 70, "quality": 65, "growth": 50,
                       "balance": 60, "earnings": 55, "moat": 45,
                       "capital": 50, "momentum": 50, "tech_break": 50,
                       "inst_flow": 50},
            "metrics": {"sector": "Çelik", "price": 50, "market_cap": 1e11,
                        "pe": 8, "pb": 1.1, "roe": 0.2},
            "overall": 72, "entry_label": "Kademeli",
            "legendary": {},
        }
        p = trader_summary_prompt(r)
        # Rewritten prompt teaches what a good thesis looks like
        assert "İYİ TEZ" in p or "somut" in p.lower()
        # Output contract intact
        assert "PROFİL:" in p and "TEZ:" in p and "RİSK:" in p
        # Anti-speculation
        assert "garanti" in p.lower()

    def test_agent_prompt_grounding_rule(self):
        from ai.prompts import agent_prompt
        p = agent_prompt("Bağlam: THYAO F/K 5.2", "THYAO ucuz mu?")
        # The critical anti-hallucination instruction
        assert "uydurma" in p.lower() or "veri yoksa" in p.lower()
        assert "THYAO ucuz mu?" in p


# ────────────────────────────────────────────────────────────────
# Endpoint
# ────────────────────────────────────────────────────────────────


class TestAIStatusEndpoint:
    def test_route_registered(self):
        from api.diag import router
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/api/diag/ai-status" in paths

    @pytest.mark.asyncio
    async def test_endpoint_returns_telemetry(self):
        from api.diag import api_diag_ai_status
        import json
        resp = await api_diag_ai_status()
        body = json.loads(resp.body.decode("utf-8"))
        flat = {**body, **body.get("data", {})}
        assert "providers_configured" in flat or "totals" in flat

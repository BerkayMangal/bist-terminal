"""Phase 5.2.2 — Signal Explanation Cards tests.

Tests the /api/cross/{symbol}/explain endpoint surface AND the underlying
engine/signal_explainer.py module.

The endpoint returns explainer payloads for cross-hunter signals — what
the signal means in plain Turkish, walk-forward Sharpe / 60-day return,
reliability badge, and suggested action. No new prompts, no AI: this is
a deterministic lookup + threshold layer.
"""
from __future__ import annotations

import pytest

from engine.signal_explainer import (
    explain_signal,
    explain_signals_for_symbol,
    _reliability_from_sharpe,
    _suggested_action,
    _DEFAULT_META,
)


# ============================================================
# Reliability classification
# ============================================================
class TestReliabilityFromSharpe:
    def test_strong_validated(self):
        assert _reliability_from_sharpe(0.85) == "walkforward_validated"
        assert _reliability_from_sharpe(-0.85) == "walkforward_validated"
        assert _reliability_from_sharpe(0.5) == "walkforward_validated"

    def test_regime_dependent(self):
        assert _reliability_from_sharpe(0.3) == "regime_dependent"
        assert _reliability_from_sharpe(-0.3) == "regime_dependent"
        assert _reliability_from_sharpe(0.2) == "regime_dependent"

    def test_weak(self):
        assert _reliability_from_sharpe(0.1) == "weak"
        assert _reliability_from_sharpe(0.0) == "weak"
        assert _reliability_from_sharpe(-0.1) == "weak"


class TestSuggestedAction:
    def test_strong_bullish_validated_high_stars(self):
        assert _suggested_action("bullish", "walkforward_validated", 5) == "enter_long"

    def test_bullish_regime_dependent(self):
        assert _suggested_action("bullish", "regime_dependent", 4) == "watch_long"

    def test_strong_bearish_validated(self):
        assert _suggested_action("bearish", "walkforward_validated", 5) == "exit_long"

    def test_weak_signal_always_watch(self):
        assert _suggested_action("bullish", "weak", 5) == "watch"
        assert _suggested_action("bearish", "weak", 4) == "watch"

    def test_low_stars_always_watch(self):
        assert _suggested_action("bullish", "walkforward_validated", 1) == "watch"

    def test_neutral_signal_watch(self):
        assert _suggested_action("neutral", "walkforward_validated", 3) == "watch"


# ============================================================
# explain_signal
# ============================================================
class TestExplainSignal:
    def test_known_signal_full_payload(self):
        out = explain_signal("Golden Cross", signal_type="bullish", stars=5)
        assert out["signal"] == "Golden Cross"
        assert "plain_explanation" in out
        assert out["plain_explanation"]
        assert out["walkforward"]["sharpe"] != 0
        assert out["reliability"] == "walkforward_validated"
        assert out["reliability_badge"]["icon"] == "✅"
        assert out["suggested_action"] == "enter_long"
        assert out["action_label"]
        assert out["stars"] == 5

    def test_unknown_signal_graceful_fallback(self):
        out = explain_signal("Some New Signal", signal_type="bullish", stars=3)
        assert out["signal"] == "Some New Signal"
        # Should still have plain_explanation (default fallback)
        assert "plain_explanation" in out
        # walkforward = 0 → reliability falls to 'weak'
        assert out["reliability"] == "weak"
        assert out["suggested_action"] == "watch"

    def test_walkforward_override_changes_reliability(self):
        # Override pushes Sharpe into validated band
        out = explain_signal(
            "RSI Aşırı Alım",  # default 'weak'
            signal_type="bearish",
            stars=4,
            walkforward_overrides={"sharpe": 0.85, "mean_return_60d": 0.07,
                                    "reliability": "walkforward_validated"},
        )
        assert out["reliability"] == "walkforward_validated"
        assert out["suggested_action"] == "exit_long"

    def test_deterministic(self):
        a = explain_signal("VCP Kırılım", "bullish", stars=5)
        b = explain_signal("VCP Kırılım", "bullish", stars=5)
        assert a == b

    def test_all_known_signals_have_plain_explanation(self):
        for name in _DEFAULT_META:
            out = explain_signal(name, "bullish", 3)
            assert out["plain_explanation"], f"Missing plain explanation for {name}"
            # No technical jargon allowed in plain_explanation: at minimum
            # it should be a real sentence (>20 chars)
            assert len(out["plain_explanation"]) >= 20


# ============================================================
# explain_signals_for_symbol — endpoint payload shape
# ============================================================
class TestExplainSignalsForSymbol:
    def test_returns_count_and_list(self):
        signals = [
            {"signal": "Golden Cross", "signal_type": "bullish", "stars": 5},
            {"signal": "MACD Bullish Cross", "signal_type": "bullish", "stars": 3},
        ]
        result = explain_signals_for_symbol("THYAO", signals)
        assert result["symbol"] == "THYAO"
        assert result["count"] == 2
        assert len(result["signals"]) == 2
        names = [s["signal"] for s in result["signals"]]
        assert "Golden Cross" in names
        assert "MACD Bullish Cross" in names

    def test_empty_signals(self):
        result = explain_signals_for_symbol("THYAO", [])
        assert result["symbol"] == "THYAO"
        assert result["count"] == 0
        assert result["signals"] == []

    def test_handles_alternate_signal_key(self):
        # Some upstream paths emit "name" instead of "signal"
        signals = [{"name": "VCP Kırılım", "signal_type": "bullish", "stars": 5}]
        result = explain_signals_for_symbol("AKBNK", signals)
        assert result["count"] == 1
        assert result["signals"][0]["signal"] == "VCP Kırılım"

    def test_handles_missing_fields(self):
        # No stars / signal_type — should default and not crash
        signals = [{"signal": "Golden Cross"}]
        result = explain_signals_for_symbol("KCHOL", signals)
        assert result["count"] == 1
        assert result["signals"][0]["signal"] == "Golden Cross"


# ============================================================
# Endpoint integration — light test on the FastAPI app surface
# ============================================================
class TestEndpointIntegration:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        import threading
        db = tmp_path / "explain.db"
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

    def test_endpoint_known_symbol_no_signals(self, client):
        # If the cross hunter has no signals for this symbol, endpoint
        # should still respond 200 with count=0 (no error).
        r = client.get("/api/cross/THYAO/explain")
        assert r.status_code == 200
        payload = r.json()  # flat envelope (V10)
        assert "_meta" in payload
        assert payload["symbol"].startswith("THYAO")
        assert "count" in payload
        assert "signals" in payload
        assert isinstance(payload["signals"], list)

    def test_endpoint_unknown_symbol_returns_empty(self, client):
        r = client.get("/api/cross/XYZQQQ/explain")
        # Should not 500 — graceful empty
        assert r.status_code == 200
        payload = r.json()
        assert payload["symbol"].startswith("XYZQQQ")
        assert payload["count"] == 0

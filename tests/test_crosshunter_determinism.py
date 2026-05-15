"""Phase 5.1.2 — CrossHunter determinism tests.

Same OHLCV input → same signal output, every time. The brief flagged
that ``/api/cross`` was returning different signal sets across requests
because each call triggered a fresh scan that hit different borsapy
slices. The fix in Phase 4.7 was to pin to ``last_results``; this test
locks it down so it doesn't regress.
"""
from __future__ import annotations

import threading
import pandas as pd
import numpy as np
import pytest

from engine.technical import CrossHunter, CrossHunterConfig


def _build_ohlcv(seed: int = 42, n_bars: int = 260) -> pd.DataFrame:
    """Deterministic synthetic OHLCV — fixed seed produces fixed output.

    The point of this fixture is the seed: every test run gets the
    SAME prices, which means downstream signal detection must be a
    pure function of (config, OHLCV). No hidden state.
    """
    rng = np.random.default_rng(seed)
    base = 100.0
    rets = rng.normal(0.0005, 0.018, size=n_bars)
    prices = base * np.cumprod(1 + rets)
    closes = pd.Series(prices)
    highs = closes * (1 + rng.uniform(0.001, 0.012, size=n_bars))
    lows = closes * (1 - rng.uniform(0.001, 0.012, size=n_bars))
    opens = closes.shift(1).fillna(base)
    vols = rng.integers(1_000_000, 10_000_000, size=n_bars).astype(float)
    dates = pd.date_range("2023-01-01", periods=n_bars, freq="B")
    df = pd.DataFrame({
        "Open": opens.values,
        "High": highs,
        "Low": lows,
        "Close": closes.values,
        "Volume": vols,
    }, index=dates)
    return df


# ============================================================
# CrossHunter — same OHLCV → same signals
# ============================================================
class TestCrossHunterDeterminism:
    def _build_history_map(self, tickers: list[str], seed: int = 42) -> dict:
        return {t: _build_ohlcv(seed=seed + i) for i, t in enumerate(tickers)}

    def test_same_ohlcv_same_signals_5x(self, monkeypatch):
        from engine import technical as tech

        # Pin UNIVERSE to a small deterministic subset
        monkeypatch.setattr(tech, "UNIVERSE", ["THYAO", "AKBNK", "EREGL"])
        history_map = self._build_history_map(["THYAO", "AKBNK", "EREGL"])
        # Bypass borsapy by passing the history_map ourselves
        hunter = CrossHunter()

        # Scan 5 times, capture signal-name lists
        results = []
        for _ in range(5):
            sigs = hunter.scan_all(history_map=history_map, adaptive_regime=False)
            # Snapshot the (ticker, signal_name, signal_type, stars) tuple
            snap = sorted([
                (s.get("ticker"), s.get("signal"), s.get("signal_type"), s.get("stars"))
                for s in sigs
            ])
            results.append(snap)

        # All 5 results must be identical
        for i, r in enumerate(results[1:], start=2):
            assert r == results[0], f"Run #{i} diverged from run #1"

    def test_signal_order_is_deterministic(self, monkeypatch):
        from engine import technical as tech
        monkeypatch.setattr(tech, "UNIVERSE", ["AKBNK", "EREGL", "THYAO"])
        history_map = self._build_history_map(["AKBNK", "EREGL", "THYAO"])
        h1 = CrossHunter()
        h2 = CrossHunter()
        s1 = h1.scan_all(history_map=history_map, adaptive_regime=False)
        s2 = h2.scan_all(history_map=history_map, adaptive_regime=False)
        # Order matters — verify same sequence (not just same set)
        names_1 = [(s.get("ticker"), s.get("signal")) for s in s1]
        names_2 = [(s.get("ticker"), s.get("signal")) for s in s2]
        assert names_1 == names_2

    def test_last_results_attribute_populated(self, monkeypatch):
        from engine import technical as tech
        monkeypatch.setattr(tech, "UNIVERSE", ["THYAO"])
        history_map = self._build_history_map(["THYAO"])
        hunter = CrossHunter()
        hunter.scan_all(history_map=history_map, adaptive_regime=False)
        # last_results must be set so /api/cross can short-circuit
        assert isinstance(hunter.last_results, list)
        assert hunter.last_scan > 0

    def test_repeated_scans_reuse_same_object_consistently(self, monkeypatch):
        """Ensure that invoking scan_all() multiple times in a row on
        the SAME CrossHunter instance produces the same cached output."""
        from engine import technical as tech
        monkeypatch.setattr(tech, "UNIVERSE", ["EREGL"])
        history_map = self._build_history_map(["EREGL"])
        hunter = CrossHunter()
        for _ in range(3):
            hunter.scan_all(history_map=history_map, adaptive_regime=False)
            snap = sorted([(s.get("signal"), s.get("stars")) for s in hunter.last_results])
            if "_first" not in dir(self):
                self._first = snap
            assert snap == self._first


# ============================================================
# /api/cross endpoint determinism
# ============================================================
class TestApiCrossDeterminism:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        db = tmp_path / "cross_det.db"
        monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
        monkeypatch.setenv("JWT_SECRET", "test-secret-" + "x" * 40)
        import infra.storage
        infra.storage._local = threading.local()
        infra.storage.DB_PATH = str(db)
        from infra.storage import init_db
        init_db()
        from app import app, cross_hunter
        # Pre-populate last_results so the endpoint doesn't trigger
        # an actual scan path
        cross_hunter.last_results = [
            {"ticker": "THYAO", "signal": "Golden Cross", "signal_type": "bullish",
             "stars": 5, "category": "kirilim", "explanation": "test",
             "vol_confirmed": True, "adx_confirmed": True, "confirmation_count": 2,
             "ticker_total_stars": 5, "ticker_signal_count": 1},
            {"ticker": "AKBNK", "signal": "MACD Bullish Cross", "signal_type": "bullish",
             "stars": 3, "category": "momentum", "explanation": "test",
             "vol_confirmed": False, "adx_confirmed": False, "confirmation_count": 1,
             "ticker_total_stars": 3, "ticker_signal_count": 1},
        ]
        cross_hunter.last_scan = 1234567890.0  # non-zero → no fresh scan
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_repeated_calls_return_same_signals(self, client):
        r1 = client.get("/api/cross").json()
        r2 = client.get("/api/cross").json()
        r3 = client.get("/api/cross").json()
        # Strip the timestamp-y _meta fields
        for r in (r1, r2, r3):
            r.pop("_meta", None)
            r.pop("asof", None)
            # ai_commentary may be set when AI providers are present;
            # we don't lock down its determinism here
            r.pop("ai_commentary", None)
        # Signals list AND summary counts must be identical
        assert r1["signals"] == r2["signals"] == r3["signals"]
        assert r1["summary"] == r2["summary"] == r3["summary"]

    def test_signal_order_stable_across_calls(self, client):
        names_a = [s["signal"] for s in client.get("/api/cross").json()["signals"]]
        names_b = [s["signal"] for s in client.get("/api/cross").json()["signals"]]
        assert names_a == names_b

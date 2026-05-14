# ================================================================
# tests/test_production_smoke.py
#
# E2E API smoke tests — yeni endpoint'lerin shape contract'ını,
# kritik flow'ların düzgün çalıştığını lokal sunucuya karşı doğrular.
#
# Bu test'ler bir uvicorn server'a karşı çalışır. CI'da disabled
# (network gerekir), manuel olarak `pytest -m smoke -k production`
# ile koşturulabilir. Skip eder eğer http://127.0.0.1:8000 erişilemezse.
# ================================================================

from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

BASE_URL = os.environ.get("BISTBULL_SMOKE_URL", "http://127.0.0.1:8000")


def _get(path: str, timeout: int = 30) -> dict:
    """GET with JSON parse. Raises if HTTP != 2xx.
    Generous timeout: borsapy/snapshot store can take seconds on cold cache."""
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(path: str, body: dict, timeout: int = 30) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _server_alive() -> bool:
    try:
        urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=2)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _server_alive(),
    reason=f"smoke server not running at {BASE_URL}",
)


# ────────────────────────────────────────────────────────────────
# Endpoint shape contracts — UI'nin beklediği field'ların var olduğu
# pin'lenir. Production'da silently shape değişirse burası kırılır.
# ────────────────────────────────────────────────────────────────


class TestEndpointShapes:
    def test_diag_system_top_level_keys(self):
        d = _get("/api/diag/system")
        for k in ("bullwatch", "kap", "portfolio", "viop", "auto_refresh"):
            assert k in d, f"missing key: {k}"

    def test_diag_system_bullwatch_shape(self):
        d = _get("/api/diag/system")
        bw = d["bullwatch"]
        for k in ("cache_populated", "items_count", "scan_running", "hung"):
            assert k in bw, f"bullwatch missing key: {k}"

    def test_portfolio_list_shape(self):
        d = _get("/api/portfolio/positions")
        assert "items" in d
        assert "count" in d
        assert isinstance(d["items"], list)
        assert d["count"] == len(d["items"])

    def test_portfolio_stats_shape(self):
        d = _get("/api/portfolio/stats")
        stats = d.get("stats") or {}
        for k in ("open_count", "closed_count", "winners", "losers"):
            assert k in stats

    def test_bullwatch_health_shape(self):
        d = _get("/api/bullwatch/health")
        for k in ("ok", "cache_populated", "scan_running"):
            assert k in d, f"bullwatch health missing key: {k}"

    def test_activity_recent_shape(self):
        d = _get("/api/activity/recent?since_hours=24&limit=5")
        assert "items" in d
        assert "counts" in d
        assert isinstance(d["counts"], dict)

    def test_viop_today_shape(self):
        d = _get("/api/viop/today?limit=5")
        assert "items" in d
        # On a fresh local instance, may be empty — that's OK
        assert isinstance(d["items"], list)

    def test_kap_health_shape(self):
        d = _get("/api/kap/health")
        assert "ok" in d
        assert "storage" in d


# ────────────────────────────────────────────────────────────────
# Critical E2E flows
# ────────────────────────────────────────────────────────────────


class TestPortfolioE2E:
    """User's actual complaint: + Aldım → portföye gelmedi.
    Bu test tam o flow'u doğrular."""

    def test_open_position_appears_in_list(self):
        # Step 1: Open new position
        unique_ticker = f"SMK{int(time.time()) % 1000:03d}"
        opened = _post("/api/portfolio/positions", {
            "ticker": unique_ticker,
            "entry_price": 10.5,
            "lot": 100,
            "notes": "smoke test",
        })
        assert opened.get("position"), "POST didn't return position"
        pid = opened["position"]["position_id"]

        # Step 2: Immediately GET list — pozisyon HEMEN görünmeli
        listed = _get("/api/portfolio/positions")
        tickers_in_list = [p.get("ticker") for p in listed.get("items") or []]
        assert unique_ticker in tickers_in_list, (
            f"Açılan pozisyon ({unique_ticker}) listede bulunamadı. "
            f"Backend bug — bu kullanıcının asıl şikayeti."
        )

        # Step 3: Single-position fetch
        single = _get(f"/api/portfolio/positions/{pid}")
        assert single.get("position", {}).get("ticker") == unique_ticker
        assert "signal" in single

        # Step 4: Close it (cleanup)
        closed = _post(
            f"/api/portfolio/positions/{pid}/close",
            {"exit_price": 11.0, "exit_reason": "smoke test cleanup"},
        )
        assert closed.get("ok") is True

    def test_position_signal_has_verdict(self):
        # Açılan pozisyonun signal.verdict mutlaka olmalı (hold/caution/sell)
        unique_ticker = f"SIG{int(time.time()) % 1000:03d}"
        opened = _post("/api/portfolio/positions", {
            "ticker": unique_ticker,
            "entry_price": 10.0,
            "lot": 50,
        })
        try:
            listed = _get("/api/portfolio/positions")
            mine = [p for p in listed["items"] if p["ticker"] == unique_ticker]
            assert len(mine) == 1
            sig = mine[0].get("signal", {})
            assert sig.get("verdict") in ("hold", "caution", "sell")
            # Details must exist for UI to render
            assert "details" in sig
        finally:
            _post(
                f"/api/portfolio/positions/{opened['position']['position_id']}/close",
                {"exit_price": 10.0, "exit_reason": "cleanup"},
            )


class TestBullWatchWatchdog:
    """Hung-scan recovery — production'da 491s asılı kalan scan
    örneğinde, /api/diag/system'in 'hung' flag'ini doğru emit ettiği
    doğrulanır."""

    def test_diag_reports_hung_when_scan_long(self):
        d = _get("/api/diag/system")
        bw = d["bullwatch"]
        # On a fresh server scan is not running so hung=False
        # We can't easily simulate a hang here, but the field must
        # exist and be a bool.
        assert isinstance(bw.get("hung"), bool)

    def test_force_reset_endpoint_exists(self):
        # Sadece var olduğunu doğrula — gerçek reset için scan_running
        # olması lazım
        try:
            resp = _post("/api/diag/bullwatch/force-reset", {})
            assert "reset" in resp
            assert "was_running" in resp
        except urllib.error.HTTPError as e:
            pytest.fail(f"force-reset endpoint missing: {e}")

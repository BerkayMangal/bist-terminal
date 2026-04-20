# ================================================================
# BISTBULL TERMINAL V10.0 — DEPLOY VALIDATION
# Deploy sonrası çalıştır: python validate.py
# Tüm modüllerin import edilebilirliğini, config tutarlılığını,
# cache ve CB sistemlerini test eder.
# ================================================================

from __future__ import annotations

import sys
import time


def validate_all() -> bool:
    passed = 0
    failed = 0
    total = 0

    def check(name: str, fn):
        nonlocal passed, failed, total
        total += 1
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {name}: {e}")
            failed += 1

    print("=" * 60)
    print("  BISTBULL V10.0 — DEPLOY VALIDATION")
    print("=" * 60)
    print()

    # ============================================================
    # 1. CONFIG
    # ============================================================
    print("[1] CONFIG")

    def _config():
        from config import BOT_VERSION, UNIVERSE, MACRO_SYMBOLS, STATIC_RATES, FINANCE_QUOTES, FINANCE_BOOKS
        from config import FA_WEIGHTS, IVME_WEIGHTS, SECTOR_THRESHOLDS, SECTOR_APPLICABILITY
        from config import REDIS_URL, CB_FAILURE_THRESHOLD, RATE_LIMIT_ENABLED, SCAN_PHASES
        assert BOT_VERSION == "V10.0"
        assert len(UNIVERSE) >= 100
        assert len(MACRO_SYMBOLS) >= 20
        assert len(STATIC_RATES) >= 5
        assert len(FINANCE_QUOTES) >= 20
        assert len(FINANCE_BOOKS) >= 10
        assert abs(sum(FA_WEIGHTS.values()) - 1.0) < 0.01
        assert abs(sum(IVME_WEIGHTS.values()) - 1.0) < 0.01
        assert len(SECTOR_THRESHOLDS) >= 5
        assert len(SECTOR_APPLICABILITY) >= 3
        assert len(SCAN_PHASES) >= 5
    check("config.py — sabitler ve tutarlılık", _config)

    # ============================================================
    # 2. CORE INFRASTRUCTURE
    # ============================================================
    print("\n[2] CORE INFRASTRUCTURE")

    def _logging():
        from core.logging_config import setup_logging, get_logger, generate_id, LogTimer
        setup_logging()
        log = get_logger("test")
        rid = generate_id("test_")
        assert len(rid) > 5
        with LogTimer() as t:
            time.sleep(0.001)
        assert t.ms > 0
    check("core/logging_config.py", _logging)

    def _redis():
        from core.redis_client import is_available, health_check, startup, shutdown
        startup()
        hc = health_check()
        assert "available" in hc
        shutdown()
    check("core/redis_client.py", _redis)

    def _cb():
        from core.circuit_breaker import ALL_CIRCUIT_BREAKERS, all_provider_status, CircuitBreaker
        assert len(ALL_CIRCUIT_BREAKERS) == 5
        status = all_provider_status()
        assert len(status) == 5
        for name, s in status.items():
            assert s["state"] == "closed"
    check("core/circuit_breaker.py", _cb)

    def _cache():
        from core.cache import SafeCache, ALL_CACHES, all_cache_stats
        from core.cache import set_top10, get_top10_items, get_scan_status, update_scan_status
        assert len(ALL_CACHES) == 13
        tc = SafeCache(5, 60, "test_val", l2_enabled=False)
        tc.set("k", "v")
        assert tc.get("k") == "v"
        tc.clear()
        stats = all_cache_stats()
        assert len(stats) == 13
    check("core/cache.py", _cache)

    def _ratelimit():
        from core.rate_limiter import RATE_LIMITS, rate_limit_status
        assert len(RATE_LIMITS) >= 4
        status = rate_limit_status()
        assert status["enabled"] is True
    check("core/rate_limiter.py", _ratelimit)

    def _envelope():
        from core.response_envelope import success, error, not_found, rate_limited, now_iso
        import json
        r = success({"test": 1})
        body = json.loads(r.body.decode())
        assert body["test"] == 1
        assert "_meta" in body
        assert body["_meta"]["build_version"] == "V10.0"
        ts = now_iso()
        assert "T" in ts
    check("core/response_envelope.py", _envelope)

    def _scan_coord():
        from core.scan_coordinator import scan_coordinator
        assert scan_coordinator.is_running is False
        status = scan_coordinator.status()
        assert "running" in status
    check("core/scan_coordinator.py", _scan_coord)

    # ============================================================
    # 3. UTILS
    # ============================================================
    print("\n[3] UTILS")

    def _helpers():
        from utils.helpers import safe_num, fmt_num, normalize_symbol, base_ticker, clean_for_json, is_stale_date
        assert safe_num(42) == 42.0
        assert safe_num(None) is None
        assert fmt_num(1e9) == "1.00B"
        assert normalize_symbol("THYAO") == "THYAO.IS"
        assert base_ticker("THYAO.IS") == "THYAO"
        assert is_stale_date("2020-01-01") is True
    check("utils/helpers.py", _helpers)

    def _market():
        from utils.market_status import get_market_status, is_scan_worthwhile
        ms = get_market_status()
        assert "status" in ms
        assert ms["status"] in ("open", "closed", "pre_market", "after_hours")
    check("utils/market_status.py", _market)

    # ============================================================
    # 4. DATA LAYER
    # ============================================================
    print("\n[4] DATA LAYER")

    def _providers():
        from data.providers import BORSAPY_AVAILABLE, is_bank, BS_MAP, IS_MAP, CF_MAP
        assert is_bank("AKBNK") is True
        assert is_bank("THYAO") is False
        assert len(BS_MAP) >= 8
        assert len(IS_MAP) >= 8
    check("data/providers.py", _providers)

    def _macro():
        from data.macro import is_yfinance_available
        # Just verify import — actual fetch needs network
        assert isinstance(is_yfinance_available(), bool)
    check("data/macro.py", _macro)

    # ============================================================
    # 5. ENGINE
    # ============================================================
    print("\n[5] ENGINE")

    def _applicability():
        from engine.applicability import get_applicability, adjust_weights, build_applicability_flags
        from config import FA_WEIGHTS
        assert get_applicability("banka", "altman_z") == "na"
        assert get_applicability("sanayi", "roe") == "full"
        adj = adjust_weights(FA_WEIGHTS, "banka")
        assert abs(sum(adj.values()) - 1.0) < 0.01
        flags = build_applicability_flags("banka")
        assert "metrics" in flags and "scores" in flags
    check("engine/applicability.py", _applicability)

    def _scoring():
        from engine.scoring import map_sector, score_value, compute_fa_pure, decision_engine
        assert map_sector("Financial Services") == "banka"
        assert map_sector("Unknown") == "sanayi"
        mock_scores = {"value": 70, "quality": 65, "growth": 60, "balance": 55, "earnings": 50, "moat": 45, "capital": 40}
        fa = compute_fa_pure(mock_scores)
        assert 1 <= fa <= 99
    check("engine/scoring.py", _scoring)

    def _analysis():
        from engine.analysis import compute_piotroski, compute_altman, compute_beneish
        # Test with empty metrics — should return None gracefully
        assert compute_piotroski({}) is None
        assert compute_altman({}) is None
        assert compute_beneish({}) is None
    check("engine/analysis.py", _analysis)

    def _technical():
        from engine.technical import cross_hunter, CHART_AVAILABLE, SIGNAL_INFO
        assert len(SIGNAL_INFO) >= 15
        assert hasattr(cross_hunter, "scan_all")
        assert hasattr(cross_hunter, "last_results")
    check("engine/technical.py", _technical)

    # ============================================================
    # 6. AI
    # ============================================================
    print("\n[6] AI")

    def _ai():
        from ai.engine import AI_AVAILABLE, AI_PROVIDERS, build_rich_context
        assert isinstance(AI_AVAILABLE, bool)
        assert isinstance(AI_PROVIDERS, list)
        # Test context builder with mock data
        mock_r = {
            "ticker": "TEST", "name": "Test", "style": "Dengeli",
            "scores": {"value": 50, "quality": 50, "growth": 50, "balance": 50, "earnings": 50, "moat": 50, "capital": 50, "momentum": 50, "tech_break": 50, "inst_flow": 50},
            "metrics": {"sector": "Test", "price": 100, "market_cap": 1e9, "pe": 10, "pb": 1.5, "ev_ebitda": 8, "roe": 0.15, "roic": 0.12, "gross_margin": 0.30, "net_margin": 0.10, "revenue_growth": 0.15, "eps_growth": 0.20, "net_debt_ebitda": 1.5, "current_ratio": 1.5, "interest_coverage": 5.0, "fcf_yield": 0.05, "cfo_to_ni": 1.0},
            "legendary": {"piotroski": "7/9", "altman": "3.5", "beneish": "-2.5", "graham_filter": "Geçti", "buffett_filter": "Geçti"},
            "fa_score": 65, "risk_score": 0, "overall": 70, "entry_label": "TEYİTLİ",
            "decision": "AL", "quality_tag": "GÜÇLÜ", "ivme": 55, "timing": "TEYİTLİ",
            "sector_group": "sanayi", "is_hype": False, "risk_reasons": [], "positives": ["Test"], "negatives": ["Test"],
        }
        ctx = build_rich_context(mock_r)
        assert "TEST" in ctx
        assert "FA SCORE" in ctx
    check("ai/engine.py", _ai)

    # ============================================================
    # 7. APP
    # ============================================================
    print("\n[7] APP")

    def _app():
        from app import app
        routes = [r.path for r in app.routes]
        required = ["/api/universe", "/api/top10", "/api/health", "/api/scan-status", "/api/macro", "/api/cross", "/ws/scan"]
        for r in required:
            assert r in routes, f"Missing route: {r}"
    check("app.py — routes", _app)

    # ============================================================
    # SUMMARY
    # ============================================================
    print()
    print("=" * 60)
    print(f"  SONUÇ: {passed}/{total} başarılı, {failed} başarısız")
    print("=" * 60)

    if failed > 0:
        print("\n⚠️  UYARI: Bazı testler başarısız! Deploy öncesi düzeltin.")
        return False
    else:
        print("\n✅ TÜM TESTLER GEÇTİ — Deploy hazır!")
        return True


if __name__ == "__main__":
    ok = validate_all()
    sys.exit(0 if ok else 1)

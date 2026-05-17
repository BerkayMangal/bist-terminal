# ================================================================
# BISTBULL TERMINAL — TAHTALAB API
# api/tahtalab.py
#
# TahtaLab: BIST tahta davranışı uyarıları. AL/SAT önerisi DEĞİLDİR.
#
#   GET /api/tahtalab           → tam sayfa yükü
#   GET /api/tahtalab/{ticker}  → tek hisse uyarıları
#
# Veri kaynağı: history_cache (radar/BullWatch taramasının doldurduğu
# günlük OHLCV). Cache soğuksa boş + data_status ile dürüst yanıt.
# ================================================================

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from fastapi import APIRouter

from core.response_envelope import success, error
from core.cache import history_cache, macro_cache, SafeCache
from config import RADAR_UNIVERSE
from utils.helpers import normalize_symbol, base_ticker
from engine.tahta_warnings import ENGINE
from engine.tahta_warning_registry import get_rule_library

log = logging.getLogger("bistbull.tahtalab.api")
router = APIRouter()

# ~5 dk cache — tüm evren değerlendirmesi 5 dakikada bir tazelenir.
_cache = SafeCache(8, 300, "tahtalab", l2_enabled=False)

_MIN_ROWS = 22


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _macro_index_return():
    """BIST 100 günlük getirisi (oran) — makro cache'inden."""
    md = macro_cache.get("macro_all")
    if not md:
        return None
    items = md.get("items", []) or []
    for want in ("XU100", "XU030"):
        for it in items:
            if it.get("key") == want and it.get("change_pct") is not None:
                try:
                    return float(it["change_pct"]) / 100.0
                except (TypeError, ValueError):
                    pass
    return None


def _load_universe() -> dict:
    """history_cache'ten günlük OHLCV evrenini topla. {ticker: df}."""
    universe: dict = {}
    for t in RADAR_UNIVERSE:
        try:
            df = history_cache.get(normalize_symbol(t))
        except Exception:
            df = None
        if df is not None and hasattr(df, "__len__") and len(df) >= _MIN_ROWS:
            universe[base_ticker(t)] = df
    return universe


def _build_payload() -> dict:
    """Tam TahtaLab sayfa yükünü üret (senkron — to_thread'den çağrılır)."""
    universe = _load_universe()
    index_df = None
    try:
        index_df = history_cache.get("XU100.IS")
    except Exception:
        index_df = None
    index_return = _macro_index_return()

    result = ENGINE.evaluate_universe(
        universe, index_df=index_df, index_return_1d=index_return,
    )
    data_status = {
        "daily_available": len(universe) > 0,
        "intraday_available": False,          # v1 — intraday veri yok
        "corporate_actions_available": False,  # v1 — KAP bölünme verisi yok
        "index_available": index_df is not None or index_return is not None,
        "tickers_evaluated": len(universe),
    }
    return {
        "asof": _now_iso(),
        "data_status": data_status,
        "summary": result["summary"],
        "warnings_by_ticker": result["warnings_by_ticker"],
        "rules": get_rule_library(),
    }


@router.get("/api/tahtalab")
async def api_tahtalab():
    """TahtaLab tam sayfa yükü — özet + hisse bazında uyarılar + kural
    kütüphanesi. AL/SAT önerisi değildir."""
    try:
        cached = _cache.get("payload")
        if cached is not None:
            return success(cached, cache_status="hit")
        payload = await asyncio.to_thread(_build_payload)
        _cache.set("payload", payload)
        return success(payload, cache_status="miss")
    except Exception as e:
        log.error(f"tahtalab: {e}")
        return error("TahtaLab yüklenemedi", status_code=500)


@router.get("/api/tahtalab/{ticker}")
async def api_tahtalab_ticker(ticker: str):
    """Tek hisse için TahtaLab uyarıları."""
    try:
        t = base_ticker((ticker or "").strip().upper())
        if not t:
            return error("Geçersiz hisse kodu", status_code=400)
        df = None
        try:
            df = history_cache.get(normalize_symbol(t))
        except Exception:
            df = None
        if df is None or not hasattr(df, "__len__") or len(df) < _MIN_ROWS:
            return success({
                "ticker": t,
                "warning_count": 0,
                "warnings": [],
                "message": "Bu hisse için yeterli günlük veri yok.",
                "data_available": False,
            })
        index_df = None
        try:
            index_df = history_cache.get("XU100.IS")
        except Exception:
            index_df = None
        warnings = await asyncio.to_thread(
            ENGINE.evaluate_ticker, t, df, index_df, None, None,
            _macro_index_return(),
        )
        if not warnings:
            return success({
                "ticker": t,
                "warning_count": 0,
                "warnings": [],
                "message": "Bugün bu hissede TahtaLab uyarısı yok.",
                "data_available": True,
            })
        return success({
            "ticker": t,
            "warning_count": len(warnings),
            "warnings": [w.to_dict() for w in warnings],
            "data_available": True,
        })
    except Exception as e:
        log.error(f"tahtalab ticker: {e}")
        return error("TahtaLab hisse verisi yüklenemedi", status_code=500)

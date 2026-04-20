# ================================================================
# BISTBULL TERMINAL V10.1 — MACRO DATA (EODHD)
# 25 makro varlığın (endeks, emtia, döviz) fiyat verisini çeker.
# yfinance → EODHD API migrasyonu.
# ================================================================

from __future__ import annotations

import os
import logging
import datetime as dt
from typing import Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from config import MACRO_SYMBOLS, EODHD_BASE_URL
from core.circuit_breaker import cb_eodhd, CircuitBreakerOpen

log = logging.getLogger("bistbull.macro")

# ================================================================
# EODHD CONFIG
# ================================================================
EODHD_API_KEY: str = os.environ.get("EODHD_API_KEY", "")
EODHD_AVAILABLE = bool(EODHD_API_KEY)
_SESSION = requests.Session()


# ================================================================
# EODHD HELPER
# ================================================================
def _eodhd_eod(symbol: str, from_date: str, timeout: int = 20) -> list[dict]:
    """EODHD eod endpoint'inden fiyat geçmişi çek."""
    url = f"{EODHD_BASE_URL}/eod/{symbol}"
    params = {
        "api_token": EODHD_API_KEY,
        "fmt": "json",
        "from": from_date,
        "period": "d",
    }
    resp = _SESSION.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


# ================================================================
# TEK SEMBOL FETCH
# ================================================================
def _fetch_one_macro(key: str, info: dict, ytd_start: str) -> Optional[dict]:
    """Tek bir makro sembolün verisini EODHD'den çek."""
    try:
        eodhd_symbol = info["eodhd_symbol"]
        data = _eodhd_eod(eodhd_symbol, ytd_start)

        if not data or len(data) < 2:
            return None

        price = float(data[-1]["close"])
        prev = float(data[-2]["close"])
        change_pct = ((price - prev) / prev * 100) if prev != 0 else 0
        first_close = float(data[0]["close"])
        ytd_pct = ((price - first_close) / first_close * 100) if first_close != 0 else 0

        # 1 aylık (son ~22 gün)
        m1_pct = None
        if len(data) >= 22:
            m1_close = float(data[-22]["close"])
            m1_pct = ((price - m1_close) / m1_close * 100) if m1_close != 0 else None

        # 1 haftalık (son ~5 gün)
        w1_pct = None
        if len(data) >= 5:
            w1_close = float(data[-5]["close"])
            w1_pct = ((price - w1_close) / w1_close * 100) if w1_close != 0 else None

        return {
            "key": key,
            "name": info["name"],
            "category": info["category"],
            "flag": info.get("flag", ""),
            "price": round(price, 4),
            "change": round(price - prev, 4),
            "change_pct": round(change_pct, 2),
            "ytd_pct": round(ytd_pct, 2),
            "m1_pct": round(m1_pct, 2) if m1_pct is not None else None,
            "w1_pct": round(w1_pct, 2) if w1_pct is not None else None,
        }
    except Exception as e:
        log.debug(f"Macro {key}: {e}")
        return None


# ================================================================
# TOPLU FETCH — 25 sembol, 10 paralel worker
# ================================================================
def fetch_all_macro() -> list[dict]:
    """
    Tüm makro varlıkları EODHD'den çek. CB korumalı.
    Returns: [{key, name, category, flag, price, change_pct, ytd_pct, ...}, ...]
    """
    if not EODHD_AVAILABLE:
        log.warning("EODHD API key yok — makro veri alınamıyor")
        return []

    # CB kontrolü
    try:
        cb_eodhd.before_call()
    except CircuitBreakerOpen:
        log.warning("Macro fetch: EODHD CB OPEN, skip")
        return []

    now = dt.datetime.now()
    ytd_start = dt.datetime(now.year, 1, 1).strftime("%Y-%m-%d")
    results: list[dict] = []

    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {
                pool.submit(_fetch_one_macro, k, v, ytd_start): k
                for k, v in MACRO_SYMBOLS.items()
            }
            for future in as_completed(futures, timeout=45):
                try:
                    r = future.result(timeout=15)
                    if r:
                        results.append(r)
                except Exception:
                    pass

        cb_eodhd.on_success()
        log.info(
            f"Macro: {len(results)}/{len(MACRO_SYMBOLS)} başarılı (EODHD)",
            extra={"success_count": len(results), "total": len(MACRO_SYMBOLS)},
        )

    except Exception as e:
        cb_eodhd.on_failure(e)
        log.error(f"Macro fetch error: {e}")

    return results


# ================================================================
# AVAILABILITY CHECK
# ================================================================
def is_yfinance_available() -> bool:
    """Backward compat — EODHD mevcutluğunu döner."""
    return EODHD_AVAILABLE

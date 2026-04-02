# ================================================================
# BISTBULL TERMINAL V10.0 — MACRO DATA
# 25 makro varlığın (endeks, emtia, döviz) fiyat verisini çeker.
# V9.1 app.py'deki _fetch_one_macro / _fetch_all_macro aynen korunmuş.
# V10 farkı:
# - Ayrı modül (app.py'den çıkarıldı)
# - Circuit Breaker sarmalı (cb_yfinance)
# - Import path'ler güncellendi
# ================================================================

from __future__ import annotations

import logging
import datetime as dt
from typing import Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import MACRO_SYMBOLS
from core.circuit_breaker import cb_yfinance, CircuitBreakerOpen

log = logging.getLogger("bistbull.macro")

# ================================================================
# YFINANCE IMPORT — opsiyonel
# ================================================================
try:
    import os
    import yfinance as yf
    os.makedirs("/tmp/yf-cache", exist_ok=True)
    yf.set_tz_cache_location("/tmp/yf-cache")
    YF_AVAILABLE = True
except ImportError:
    yf = None  # type: ignore
    YF_AVAILABLE = False


# ================================================================
# TEK SEMBOL FETCH
# ================================================================
def _fetch_one_macro(key: str, info: dict, ytd_start: str) -> Optional[dict]:
    """Tek bir makro sembolün verisini çek — yf.Ticker ile."""
    try:
        tk = yf.Ticker(info["symbol"])
        h = tk.history(start=ytd_start, interval="1d")
        if h is None or h.empty or len(h) < 2:
            return None
        price = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2])
        change_pct = ((price - prev) / prev * 100) if prev != 0 else 0
        first_close = float(h["Close"].iloc[0])
        ytd_pct = ((price - first_close) / first_close * 100) if first_close != 0 else 0
        m1_pct = (
            ((price - float(h["Close"].iloc[-22])) / float(h["Close"].iloc[-22]) * 100)
            if len(h) >= 22 else None
        )
        w1_pct = (
            ((price - float(h["Close"].iloc[-5])) / float(h["Close"].iloc[-5]) * 100)
            if len(h) >= 5 else None
        )
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
# TOPLU FETCH — 25 sembol, 5 paralel worker
# ================================================================
def fetch_all_macro() -> list[dict]:
    """
    Tüm makro varlıkları çek. CB korumalı.
    Returns: [{key, name, category, flag, price, change_pct, ytd_pct, ...}, ...]
    """
    if not YF_AVAILABLE:
        log.warning("yfinance yok — makro veri alınamıyor")
        return []

    # CB kontrolü
    try:
        cb_yfinance.before_call()
    except CircuitBreakerOpen:
        log.warning("Macro fetch: yfinance CB OPEN, skip")
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

        cb_yfinance.on_success()
        log.info(
            f"Macro: {len(results)}/{len(MACRO_SYMBOLS)} başarılı",
            extra={"success_count": len(results), "total": len(MACRO_SYMBOLS)},
        )

    except Exception as e:
        cb_yfinance.on_failure(e)
        log.error(f"Macro fetch error: {e}")

    return results


# ================================================================
# YFINANCE AVAILABILITY
# ================================================================
def is_yfinance_available() -> bool:
    """yfinance import edildi mi?"""
    return YF_AVAILABLE

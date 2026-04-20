# ================================================================
# BISTBULL TERMINAL V10.1 — MACRO DATA
# 25 makro varlığın (endeks, emtia, döviz) fiyat verisini çeker.
# yfinance kütüphanesi kaldırıldı — direkt Yahoo Finance HTTP API.
# ================================================================

from __future__ import annotations

import logging
import datetime as dt
from typing import Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from config import MACRO_SYMBOLS
from core.circuit_breaker import cb_yfinance, CircuitBreakerOpen

log = logging.getLogger("bistbull.macro")

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})


# ================================================================
# YAHOO FINANCE DIRECT HTTP — no yfinance library needed
# ================================================================
def _yahoo_chart(symbol: str, range_str: str = "6mo", interval: str = "1d") -> Optional[dict]:
    """
    Yahoo Finance chart API — direkt HTTP. yfinance kütüphanesi yok.
    Returns: {"closes": [...], "timestamps": [...]} or None
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"interval": interval, "range": range_str}
    try:
        resp = _SESSION.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        r = result[0]
        timestamps = r.get("timestamp", [])
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        if not timestamps or not closes:
            return None
        return {"timestamps": timestamps, "closes": closes}
    except Exception:
        return None


# ================================================================
# TEK SEMBOL FETCH
# ================================================================
def _fetch_one_macro(key: str, info: dict, ytd_start: str) -> Optional[dict]:
    """Tek bir makro sembolün verisini Yahoo Finance HTTP ile çek."""
    try:
        chart = _yahoo_chart(info["symbol"], range_str="6mo", interval="1d")
        if chart is None or len(chart["closes"]) < 2:
            return None

        # Filter None values from closes
        valid = [(t, c) for t, c in zip(chart["timestamps"], chart["closes"]) if c is not None]
        if len(valid) < 2:
            return None

        # Find YTD start index
        ytd_ts = int(dt.datetime.strptime(ytd_start, "%Y-%m-%d").timestamp())
        ytd_idx = 0
        for i, (t, _) in enumerate(valid):
            if t >= ytd_ts:
                ytd_idx = i
                break

        price = valid[-1][1]
        prev = valid[-2][1]
        change_pct = ((price - prev) / prev * 100) if prev != 0 else 0
        first_close = valid[ytd_idx][1] if ytd_idx < len(valid) else valid[0][1]
        ytd_pct = ((price - first_close) / first_close * 100) if first_close != 0 else 0

        m1_pct = None
        if len(valid) >= 22:
            m1_close = valid[-22][1]
            m1_pct = ((price - m1_close) / m1_close * 100) if m1_close != 0 else None

        w1_pct = None
        if len(valid) >= 5:
            w1_close = valid[-5][1]
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
    Tüm makro varlıkları çek. CB korumalı.
    Returns: [{key, name, category, flag, price, change_pct, ytd_pct, ...}, ...]
    """
    # CB kontrolü
    try:
        cb_yfinance.before_call()
    except CircuitBreakerOpen:
        log.warning("Macro fetch: Yahoo HTTP CB OPEN, skip")
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
# AVAILABILITY — backward compat
# ================================================================
def is_yfinance_available() -> bool:
    """Yahoo Finance HTTP her zaman mevcut (requests ile)."""
    return True

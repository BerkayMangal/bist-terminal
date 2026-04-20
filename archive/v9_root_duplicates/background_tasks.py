# ================================================================
# BISTBULL TERMINAL V10.2 — BACKGROUND TASKS
# engine/background_tasks.py
#
# PURPOSE:
#   Dedicated async background task module. Keeps all heavyweight
#   periodic work completely separate from the FastAPI request loop
#   and from the main background scanner.
#
# TASKS:
#   1. heatmap_refresh_loop()   — refreshes heatmap cache every 10 min
#   2. paper_trade_loop()       — checks TP/SL on active signals every 5 min
#
# CRASH FIX (root cause of V10.1 OOM):
#   The previous implementation triggered heatmap precomputation
#   IMMEDIATELY after the scan completed, while 108-symbol yfinance
#   data was still resident in memory. This doubled peak RAM usage
#   and crashed the Railway container.
#
#   Fix: heatmap_refresh_loop() waits HEATMAP_STARTUP_DELAY (15 min)
#   before its first run, giving the scan's ThreadPoolExecutor and all
#   yfinance DataFrames time to be garbage-collected. Subsequent runs
#   happen every HEATMAP_REFRESH_INTERVAL (10 min) independently of
#   the scan cycle — they are never co-scheduled.
#
# EVENT LOOP CONTRACT:
#   All synchronous operations (yfinance, pandas, borsapy) are wrapped
#   in asyncio.to_thread(). The FastAPI event loop is NEVER blocked.
#   Both loops are launched via asyncio.create_task() in app.py lifespan
#   and cancelled cleanly on shutdown.
# ================================================================

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from collections import defaultdict
from typing import Optional

from core.cache import (
    heatmap_cache,
    tech_cache,
    get_top10_items,
)
from core.response_envelope import now_iso
from config import UNIVERSE
from utils.helpers import normalize_symbol, clean_for_json
from engine.signal_tracker import signal_tracker
from engine.technical import compute_technical

log = logging.getLogger("bistbull.bgtasks")

# ================================================================
# TIMING CONSTANTS
# ================================================================
# Heatmap: wait 15 min on startup so scan RAM is fully freed,
# then refresh every 10 min.
HEATMAP_STARTUP_DELAY:    int = 900   # 15 minutes
HEATMAP_REFRESH_INTERVAL: int = 600   # 10 minutes

# Paper trade: 90s startup delay, then every 5 min.
PAPER_TRADE_STARTUP_DELAY:    int = 90   # 1.5 minutes
PAPER_TRADE_INTERVAL:          int = 300  # 5 minutes

# ================================================================
# DATA SOURCE IMPORTS — borsapy only
# ================================================================
try:
    from data.providers import BORSAPY_AVAILABLE
except ImportError:
    BORSAPY_AVAILABLE = False


# ================================================================
# HEATMAP DATA FETCHER — synchronous, runs in thread
# ================================================================

def _fetch_heatmap_data() -> list[dict]:
    """
    Fetches latest price + change data for every stock in UNIVERSE.

    Data source priority:
      1. borsapy fast_info  → real-time BIST prices (preferred)
      2. yfinance batch download → 2-day window for change_pct

    Returns a flat list of stock dicts. Returns [] on total failure.
    This function is synchronous — always call via asyncio.to_thread().
    """
    items    = get_top10_items()
    item_map = {item["ticker"]: item for item in items}
    results: list[dict] = []

    # ── 1. borsapy path ────────────────────────────────────────────
    if BORSAPY_AVAILABLE:
        try:
            import borsapy as bp_m
            for t in UNIVERSE:
                try:
                    _tk  = bp_m.Ticker(t)
                    fi   = _tk.fast_info
                    last = getattr(fi, "last_price", None)
                    prev = getattr(fi, "previous_close", None)
                    mcap = getattr(fi, "market_cap", None)
                    if last is not None and prev is not None and prev > 0:
                        chg = (last - prev) / prev * 100.0
                        si  = item_map.get(t)
                        results.append({
                            "ticker":     t,
                            "price":      round(float(last), 2),
                            "change_pct": round(chg, 2),
                            "market_cap": float(mcap) if mcap else None,
                            "sector":     (si.get("sector", "Diger") if si else "Diger") or "Diger",
                            "score":      si["overall"] if si else None,
                        })
                except Exception:
                    continue
            if results:
                log.debug(f"Heatmap borsapy: {len(results)} hisse")
                return results
        except Exception as e:
            log.warning(f"Heatmap borsapy hatasi: {e}")

    return results


def _build_heatmap_result(stock_list: list[dict]) -> dict:
    """
    Aggregates flat stock list into sector groups.
    Pure CPU work — no I/O. Safe to call in main thread or in thread.
    """
    sectors: dict[str, list] = defaultdict(list)
    for d in stock_list:
        sectors[d["sector"]].append(d)

    sector_list = sorted(
        [
            {
                "sector":     sec,
                "avg_change": round(
                    sum(i["change_pct"] for i in si) / len(si), 2
                ) if si else 0.0,
                "total_mcap": sum(i["market_cap"] or 0 for i in si),
                "count":      len(si),
                "stocks":     sorted(si, key=lambda x: abs(x["change_pct"]), reverse=True),
            }
            for sec, si in sectors.items()
        ],
        key=lambda x: x["avg_change"],
        reverse=True,
    )

    return {
        "timestamp": now_iso(),
        "sectors":   clean_for_json(sector_list),
        "total":     len(stock_list),
        "computing": False,
    }


async def _refresh_heatmap_once() -> bool:
    """
    Single heatmap refresh cycle. Runs fetch in thread, stores in cache.
    Returns True on success, False on failure.
    """
    try:
        log.info("Heatmap yenileme basliyor...")
        stock_list = await asyncio.to_thread(_fetch_heatmap_data)
        if not stock_list:
            log.warning("Heatmap: hic veri alinamadi, cache guncellenmedi")
            return False
        result = _build_heatmap_result(stock_list)
        heatmap_cache.set("heatmap", result)
        log.info(
            f"Heatmap cache guncellendi: {len(stock_list)} hisse, "
            f"{len(result['sectors'])} sektor"
        )
        return True
    except Exception as e:
        log.error(f"Heatmap refresh hatasi: {e}", exc_info=True)
        return False


# ================================================================
# HEATMAP REFRESH LOOP
# ================================================================

async def heatmap_refresh_loop() -> None:
    """
    Standalone async loop: refreshes heatmap cache every 10 minutes.

    STARTUP DELAY = 15 minutes.
    Rationale: On boot, the background scanner runs first and loads
    108 symbols of yfinance data into RAM (~200-300 MB). If we start
    the heatmap fetch immediately after, peak RAM doubles and crashes
    the Railway container (OOM).

    By waiting 15 minutes, the scanner's ThreadPoolExecutor workers
    have finished, yfinance DataFrames have been garbage-collected,
    and RAM usage has returned to baseline before we start a new
    batch download.

    All yfinance/pandas calls are inside asyncio.to_thread() so the
    FastAPI event loop is never blocked during heatmap refresh.
    """
    log.info(
        f"Heatmap loop baslatildi — ilk calisma {HEATMAP_STARTUP_DELAY // 60} "
        f"dakika sonra, sonraki her {HEATMAP_REFRESH_INTERVAL // 60} dakikada bir"
    )
    await asyncio.sleep(HEATMAP_STARTUP_DELAY)

    while True:
        await _refresh_heatmap_once()
        await asyncio.sleep(HEATMAP_REFRESH_INTERVAL)


# ================================================================
# PAPER TRADE LOOP
# ================================================================

async def paper_trade_loop() -> None:
    """
    Standalone async loop: checks TP/SL on all active signals every 5 min.

    Price source priority:
      1. yfinance batch download (fast, low bandwidth — 2-day window)
      2. tech_cache last known price (stale but better than nothing)
      3. compute_technical() individual call (slowest, max 5 calls)

    All blocking calls use asyncio.to_thread() so the event loop stays free.
    Purges signals older than PURGE_AFTER_DAYS once per day at 01:xx.
    """
    log.info("PaperTrade loop baslatildi")
    await asyncio.sleep(PAPER_TRADE_STARTUP_DELAY)

    while True:
        try:
            active = signal_tracker.get_all_active()

            if not active:
                log.debug("PaperTrade: aktif sinyal yok, kontrol atlandi")
                await asyncio.sleep(PAPER_TRADE_INTERVAL)
                continue

            tickers   = list({s["ticker"] for s in active})
            price_map: dict[str, float] = {}
            log.info(f"PaperTrade: {len(tickers)} ticker fiyat kontrolu basliyor")

            # ── 1. borsapy fast_info prices ───────────────────────────
            if BORSAPY_AVAILABLE and tickers:
                try:
                    import borsapy as bp_m
                    for t in tickers:
                        try:
                            _tk = bp_m.Ticker(t)
                            fi = _tk.fast_info
                            lp = getattr(fi, "last_price", None)
                            if lp is not None and float(lp) > 0:
                                price_map[t] = round(float(lp), 4)
                        except Exception:
                            pass
                    log.debug(f"PaperTrade borsapy: {len(price_map)}/{len(tickers)} fiyat alindi")
                except Exception as e:
                    log.warning(f"PaperTrade borsapy batch hatasi: {e}")

            # ── 2. tech_cache fallback for missing tickers ──────────
            for t in [x for x in tickers if x not in price_map]:
                try:
                    cached_tech = tech_cache.get(normalize_symbol(t))
                    if cached_tech and cached_tech.get("price") and cached_tech["price"] > 0:
                        price_map[t] = round(float(cached_tech["price"]), 4)
                except Exception:
                    pass

            # ── 3. compute_technical for still-missing (max 5) ─────
            still_missing = [t for t in tickers if t not in price_map]
            if still_missing and BORSAPY_AVAILABLE:
                for t in still_missing[:5]:
                    try:
                        tech = await asyncio.to_thread(
                            compute_technical, normalize_symbol(t)
                        )
                        if tech and tech.get("price") and float(tech["price"]) > 0:
                            price_map[t] = round(float(tech["price"]), 4)
                        await asyncio.sleep(0.5)  # rate-limit guard
                    except Exception as e:
                        log.debug(f"PaperTrade compute_technical [{t}]: {e}")

            # ── TP/SL update ────────────────────────────────────────
            if price_map:
                counts = await asyncio.to_thread(signal_tracker.update_prices, price_map)
                log.info(
                    f"PaperTrade tamamlandi: "
                    f"TP={counts['tp']} SL={counts['sl']} "
                    f"Aktif={counts['still_active']} "
                    f"FiyatYok={counts['no_price']}"
                )
            else:
                log.warning(
                    f"PaperTrade: hic fiyat alinamadi, "
                    f"{len(active)} sinyal guncellenemedi"
                )

            # ── Daily purge at 01:xx ────────────────────────────────
            if dt.datetime.now().hour == 1:
                await asyncio.to_thread(signal_tracker.purge_old)

        except Exception as e:
            log.error(f"PaperTrade loop hatasi: {e}", exc_info=True)

        await asyncio.sleep(PAPER_TRADE_INTERVAL)

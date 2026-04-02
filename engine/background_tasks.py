# ================================================================
# BISTBULL — BACKGROUND TASKS
# paper_trade_loop: 5 dakikada bir aktif sinyallerin fiyatlarını
# kontrol eder, TP/SL vurduysa kapatır.
# ================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from engine.signal_tracker import signal_tracker

log = logging.getLogger("bistbull.background_tasks")

# ================================================================
# CONFIG
# ================================================================
PAPER_TRADE_INTERVAL = 300       # 5 dakika
PAPER_TRADE_STARTUP_DELAY = 120  # İlk scan bitmesini bekle (2dk)


# ================================================================
# PRICE FETCHER — aktif sinyaller için güncel fiyat çek
# ================================================================
def _fetch_active_prices() -> dict[str, float]:
    """
    Aktif sinyallerin tickerlarını topla, yfinance'den fiyat çek.
    Returns: {ticker: current_price}
    """
    # Aktif sinyallerin ticker listesi
    active = [
        s for s in signal_tracker._signals
        if s["status"] == "active"
    ]
    if not active:
        return {}

    tickers = list({s["ticker"] for s in active})
    price_map: dict[str, float] = {}

    # Önce tech_cache'den bak (scan sırasında zaten çekilmiş)
    try:
        from core.cache import tech_cache
        for t in tickers:
            from utils.helpers import normalize_symbol
            sym = normalize_symbol(t)
            cached = tech_cache.get(sym)
            if cached and cached.get("price"):
                price_map[t] = float(cached["price"])
    except Exception:
        pass

    # Cache'de bulunamayanlar için yfinance ile çek
    missing = [t for t in tickers if t not in price_map]
    if missing:
        try:
            import yfinance as yf
            from utils.helpers import normalize_symbol
            symbols = [normalize_symbol(t) for t in missing]
            df = yf.download(
                symbols, period="1d", interval="1d",
                group_by="ticker", progress=False, threads=True,
            )
            if df is not None and not df.empty:
                for t in missing:
                    sym = normalize_symbol(t)
                    try:
                        if len(missing) == 1:
                            ticker_df = df
                        else:
                            if sym in df.columns.get_level_values(0):
                                ticker_df = df[sym]
                            else:
                                continue
                        if ticker_df is not None and not ticker_df.empty:
                            close = float(ticker_df["Close"].iloc[-1])
                            if close > 0:
                                price_map[t] = close
                    except Exception:
                        continue
        except ImportError:
            log.debug("yfinance not available for paper trade price check")
        except Exception as e:
            log.warning(f"Paper trade price fetch error: {e}")

    # Heatmap cache'den de bakalım (daily changes varsa)
    if missing:
        still_missing = [t for t in missing if t not in price_map]
        if still_missing:
            try:
                from core.cache import heatmap_cache
                hm = heatmap_cache.get("heatmap")
                if hm and hm.get("sectors"):
                    for sec in hm["sectors"]:
                        for stock in sec.get("stocks", []):
                            if stock["ticker"] in still_missing and stock.get("price"):
                                price_map[stock["ticker"]] = float(stock["price"])
            except Exception:
                pass

    return price_map


# ================================================================
# PAPER TRADE LOOP — async arka plan görevi
# ================================================================
async def paper_trade_loop() -> None:
    """
    5 dakikada bir aktif sinyallerin fiyatlarını kontrol et.
    TP/SL vurduysa kapat.
    """
    await asyncio.sleep(PAPER_TRADE_STARTUP_DELAY)
    log.info("PaperTrade loop başlatıldı (5dk aralık)")

    while True:
        try:
            # Aktif sinyal var mı kontrol et
            active_count = sum(
                1 for s in signal_tracker._signals
                if s["status"] == "active"
            )

            if active_count > 0:
                # Fiyatları çek
                price_map = await asyncio.to_thread(_fetch_active_prices)

                if price_map:
                    # TP/SL kontrol et
                    closed = signal_tracker.check_prices(price_map)
                    if closed > 0:
                        log.info(f"PaperTrade: {closed} sinyal kapatıldı, {active_count - closed} aktif kaldı")
                    else:
                        log.debug(f"PaperTrade: {active_count} aktif sinyal kontrol edildi, değişiklik yok")
                else:
                    log.debug(f"PaperTrade: {active_count} aktif sinyal var ama fiyat alınamadı")
            else:
                log.debug("PaperTrade: aktif sinyal yok, bekleniyor")

        except Exception as e:
            log.error(f"PaperTrade loop hatası: {e}")

        await asyncio.sleep(PAPER_TRADE_INTERVAL)

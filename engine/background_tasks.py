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
#   Fix: heatmap_refresh_loop() waits HEATMAP_STARTUP_DELAY before
#   its first run, giving the scan's ThreadPoolExecutor and all
#   yfinance DataFrames time to be garbage-collected. Subsequent runs
#   happen every HEATMAP_REFRESH_INTERVAL independently of
#   the scan cycle — they are never co-scheduled.
#
#   HOTFIX 1 (2026-Q2 prod incident): startup delay trimmed from
#   15min -> 3min. The 15min window meant users saw a blank heatmap
#   for the first 15 minutes after deploy, which combined with the
#   now-fixed `for t in UNIVERSE` sequential-fetch bug to produce a
#   10-minute blank page. Empirically the scanner's RAM spike has
#   dropped since Phase 3 (aggressive GC + streaming yfinance), so
#   3min is safe. Additionally, /api/heatmap now kicks a one-shot
#   background refresh on first cold-request (see app.py HOTFIX 1),
#   so this loop's startup delay is no longer the critical path.
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
# Heatmap: wait 3 min on startup (HOTFIX 1: reduced from 15 min —
# /api/heatmap now has its own one-shot background kick on cold miss,
# so the loop's first run isn't the critical path for user-visible
# blank page prevention), then refresh every 30 min.
HEATMAP_STARTUP_DELAY:    int = 180   # 3 minutes (was 900)
HEATMAP_REFRESH_INTERVAL: int = 1800  # OPT: 600→1800 (10dk→30dk, API yükü -%66)

# Stage 4 perf: heatmap fetcher tuning. 8 workers stays well under
# borsapy's observed rate-limit ceiling (it tolerates ~25 concurrent
# fast_info calls), per-ticker 6s prevents a single stuck symbol from
# blocking the budget, total budget 30s caps worst-case wall-time.
HEATMAP_FETCH_WORKERS:      int = 8
HEATMAP_PER_TICKER_TIMEOUT: int = 6
HEATMAP_FETCH_BUDGET_SEC:   int = 30

# Paper trade: 90s startup delay, then every 5 min.
PAPER_TRADE_STARTUP_DELAY:    int = 90   # 1.5 minutes
PAPER_TRADE_INTERVAL:          int = 300  # 5 minutes

# BullWatch snapshot refresh — tiered cadence (D.3).
#   Cold (full universe, ~437 tickers): every 30 min, the canonical source.
#   Hot  (top 50 from cold snapshot):    every  5 min, subset re-scan.
# Hot tier writes a separate `bullwatch_hot` snapshot consumed via
# /api/bullwatch?tier=hot. Default endpoint behavior is unchanged.
BULLWATCH_REFRESH_STARTUP_DELAY: int = 240   # cold tier startup delay
BULLWATCH_REFRESH_INTERVAL:      int = 1800  # cold tier — 30 min
BULLWATCH_RETRY_AFTER_ERROR:     int = 300

BULLWATCH_HOT_STARTUP_DELAY:     int = 480   # ~8 min — wait for first cold scan
BULLWATCH_HOT_INTERVAL:          int = 180   # hot tier — 3 min (was 5min)
BULLWATCH_HOT_SIZE:              int = 50    # how many top tickers to refresh

# KAP disclosure feed (Faz 1). Cadence is market-hours aware: peak
# announcement windows in Türkiye are 18:00–21:00 and 08:00–09:30 local
# time. Off-hours we relax to KAP_INTERVAL_OFFHOURS so we don't beat on
# KAP servers overnight for nothing.
KAP_FEED_STARTUP_DELAY:    int = 120   # 2 min — let the rest of boot settle
KAP_FEED_INTERVAL_PEAK:    int = 300   # 5 min during peak announcement windows
KAP_FEED_INTERVAL_OFFHOURS: int = 1800  # 30 min overnight
KAP_FEED_RETRY_AFTER_ERROR: int = 600   # back off 10 min on hard error

# Reaction tracker refresh — runs once per day (after Borsa İstanbul
# close). Backfills 1d/1w/1m reactions on disclosures that have aged
# enough.
KAP_REACTIONS_STARTUP_DELAY: int = 900   # 15 min after boot
KAP_REACTIONS_INTERVAL:      int = 86400  # daily

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

    Stage 4 (Great Overhaul): the per-ticker fetch is now parallelized
    via ThreadPoolExecutor. Previously 108 tickers × ~500ms borsapy ≈
    54s sequential; now ~7-8s with 8 workers. Per-ticker timeout caps
    a single bad symbol at 6s.
    """
    items    = get_top10_items()
    item_map = {item["ticker"]: item for item in items}
    results: list[dict] = []

    # ── 1. borsapy path (parallel) ─────────────────────────────────
    if not BORSAPY_AVAILABLE:
        return results

    try:
        import borsapy as bp_m
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from concurrent.futures import TimeoutError as _FutureTimeout

        def _fetch_one(t: str) -> Optional[dict]:
            try:
                _tk  = bp_m.Ticker(t)
                fi   = _tk.fast_info
                last = getattr(fi, "last_price", None)
                prev = getattr(fi, "previous_close", None)
                mcap = getattr(fi, "market_cap", None)
                if last is None or prev is None or prev <= 0:
                    return None
                chg = (last - prev) / prev * 100.0
                si  = item_map.get(t)
                return {
                    "ticker":     t,
                    "price":      round(float(last), 2),
                    "change_pct": round(chg, 2),
                    "market_cap": float(mcap) if mcap else None,
                    "sector":     (si.get("sector", "Diger") if si else "Diger") or "Diger",
                    "score":      si["overall"] if si else None,
                }
            except Exception:
                return None

        timeouts = 0
        with ThreadPoolExecutor(max_workers=HEATMAP_FETCH_WORKERS) as pool:
            futs = {pool.submit(_fetch_one, t): t for t in UNIVERSE}
            for fut in as_completed(futs, timeout=HEATMAP_FETCH_BUDGET_SEC):
                try:
                    row = fut.result(timeout=HEATMAP_PER_TICKER_TIMEOUT)
                except _FutureTimeout:
                    timeouts += 1
                    continue
                except Exception:
                    continue
                if row is not None:
                    results.append(row)
        if timeouts:
            log.info("Heatmap: %d ticker timed out (>%ds)",
                     timeouts, HEATMAP_PER_TICKER_TIMEOUT)
        if results:
            log.debug(f"Heatmap borsapy: {len(results)} hisse (parallel)")
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

            tickers   = sorted(set(s["ticker"] for s in active))[:30]  # OPT: max 30 ticker
            price_map: dict[str, float] = {}
            log.info(f"PaperTrade: {len(tickers)} ticker fiyat kontrolu basliyor")

            # ── 1. borsapy fast_info prices ───────────────────────────
            if BORSAPY_AVAILABLE and tickers:
                def _fetch_borsapy_prices() -> dict[str, float]:
                    out: dict[str, float] = {}
                    try:
                        import borsapy as bp_m
                        for t in tickers:
                            try:
                                _tk = bp_m.Ticker(t)
                                fi = _tk.fast_info
                                lp = getattr(fi, "last_price", None)
                                if lp is not None and float(lp) > 0:
                                    out[t] = round(float(lp), 4)
                            except Exception:
                                pass
                    except Exception as e:
                        log.warning(f"PaperTrade borsapy batch hatasi: {e}")
                    return out
                # audit #4 — these are blocking borsapy HTTP calls; run the
                # whole batch off the event loop (was a sync N+1 directly
                # on the asyncio loop, freezing request handling).
                price_map.update(await asyncio.to_thread(_fetch_borsapy_prices))
                log.debug(f"PaperTrade borsapy: {len(price_map)}/{len(tickers)} fiyat alindi")

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


# ================================================================
# HISTORY CACHE PRE-WARM (Stage 8 — Railway Pro)
# ================================================================

async def history_cache_prewarm() -> None:
    """Fire-and-forget: populate history_cache for the full BullWatch
    universe right after boot so the FIRST user-triggered refresh is
    already cache-warm and returns in seconds instead of 2-3 minutes.

    Why this exists:
      Stage 6a made every refresh after the first one fast. But the
      first refresh (cold boot, empty cache) still needed a full
      borsapy fetch — measured at 2-3 minutes for 437 tickers.
      Pre-warming during boot moves that wait to a time when no user
      is staring at the page.

    Why it's safe to run early:
      With Stage 6a's cache-first lookup, this populates the cache
      via the same path a regular scan uses. No new code path. If
      the prewarm fails (borsapy 502 etc.), the cache stays empty
      and the first user refresh falls back to the old "cold load"
      behavior — degrades gracefully.

    Cadence:
      Runs ONCE shortly after boot. Scheduled scans (Stage 7a,
      09:30 + 13:30 + 18:30 IST) keep the cache refreshed thereafter.
    """
    # Small delay so the rest of the lifespan can finish initializing
    # without competing for the borsapy circuit breaker on startup.
    await asyncio.sleep(60)

    try:
        from engine.technical import batch_download_history
        try:
            from config import FULL_BIST as _universe
        except Exception:
            from config import UNIVERSE as _universe

        log.info(
            "History cache pre-warm starting: %d tickers (background)",
            len(_universe),
        )
        await asyncio.to_thread(batch_download_history, list(_universe))
        log.info("History cache pre-warm complete")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("History cache pre-warm failed (non-fatal): %r", exc)


# ================================================================
# BULLWATCH SNAPSHOT REFRESH LOOP
# ================================================================

async def bullwatch_refresh_loop() -> None:
    """Refresh the BullWatch snapshot store at fixed Istanbul times.

    Stage 7a (Great Overhaul):
      Replaces the old "every 30 min around the clock" cadence with a
      clock-anchored schedule (09:30 + 13:30 Istanbul, weekdays only).
      Why: the 30-min cadence burned borsapy quota even at 03:00 when
      BIST is closed. Anchoring to market-relevant times gives users
      fresh data when they actually look and lets the system idle
      overnight.

    Startup behavior:
      First run still kicks ~4 min after boot so a deployed instance
      isn't dead until the next scheduled slot. After that, the loop
      sleeps until the next scheduled time per engine.scan_schedule.
    """
    from engine.scan_schedule import seconds_until_next_scan

    log.info(
        "BullWatch refresh loop: first run in %ds, then clock-anchored "
        "(09:30 + 13:30 + 18:30 Istanbul, weekdays)",
        BULLWATCH_REFRESH_STARTUP_DELAY,
    )
    await asyncio.sleep(BULLWATCH_REFRESH_STARTUP_DELAY)

    # Stage 7c: track the slot we just woke from so we can fire the
    # daily bulletin generator after the post_close (18:30) scan.
    last_slot_label: Optional[str] = None

    while True:
        # ── Run the scan ───────────────────────────────────────────
        try:
            from api.bullwatch import _refresh_and_persist as _bw_refresh
            payload = await _bw_refresh()
            if payload is None:
                # Scan-in-flight skip or hard failure. Don't ride the
                # error all the way to the next schedule slot — wait a
                # short cool-down and re-evaluate.
                log.debug("BullWatch refresh returned None; brief cool-down")
                await asyncio.sleep(BULLWATCH_RETRY_AFTER_ERROR)
                continue
        except asyncio.CancelledError:
            log.info("BullWatch refresh loop cancelled")
            raise
        except Exception as e:
            log.warning("BullWatch refresh loop tick failed: %r", e)
            await asyncio.sleep(BULLWATCH_RETRY_AFTER_ERROR)
            continue

        # ── Stage 7c: daily bulletin auto-fire ─────────────────────
        # The bulletin generator (Stage 7b) composes a daily summary
        # from currently-warm sources. It must fire ONLY after the
        # post_close (18:30 IST) scan so the daily candle is final
        # and confirmed_new_today actually reflects the day's
        # zone-entries. last_slot_label was set the previous loop tick.
        if last_slot_label == "post_close":
            try:
                from engine.daily_bulletin import generate_and_persist
                rec = await asyncio.to_thread(generate_and_persist)
                log.info(
                    "Daily bulletin saved for %s",
                    rec.get("bulletin_date"),
                )
            except Exception as exc:
                # Best-effort — bulletin failure must never break the
                # scan loop. Operator gets a warning and we move on.
                log.warning("Daily bulletin generation failed: %r", exc)

        # ── Sleep until next scheduled slot ────────────────────────
        sleep_for, label = seconds_until_next_scan()
        last_slot_label = label
        log.info(
            "BullWatch refresh: next slot %s in %.0fs (~%.1f h)",
            label, sleep_for, sleep_for / 3600.0,
        )
        await asyncio.sleep(sleep_for)


# ================================================================
# BULLWATCH HOT TIER LOOP (D.3, Tier 1)
# ================================================================

async def bullwatch_hot_tier_loop() -> None:
    """Re-scan the top N tickers from the latest cold snapshot every
    HOT_INTERVAL seconds and persist the result under module
    `bullwatch_hot`.

    Reads the canonical bullwatch snapshot, narrows the universe to the
    top BULLWATCH_HOT_SIZE tickers, calls engine.bullwatch.scan on that
    subset (with fundamental cache warm from the cold scan, this is
    typically a 30–60 s job), and writes a separate snapshot. The
    /api/bullwatch endpoint serves the hot snapshot when called with
    `?tier=hot`; default behavior is unchanged.

    Why a separate snapshot module: keeping cold and hot in separate
    namespaces preserves the atomicity guarantees of SnapshotStore.
    No partial-write merging across tiers, no scan_id mismatches.
    """
    log.info(
        "BullWatch hot tier loop scheduled — first run in %ds, then every %ds (top %d)",
        BULLWATCH_HOT_STARTUP_DELAY, BULLWATCH_HOT_INTERVAL, BULLWATCH_HOT_SIZE,
    )
    await asyncio.sleep(BULLWATCH_HOT_STARTUP_DELAY)

    while True:
        sleep_for = BULLWATCH_HOT_INTERVAL
        try:
            payload = await asyncio.to_thread(_run_bullwatch_hot_tier)
            if payload is None:
                # No cold snapshot to base on yet — back off briefly
                sleep_for = BULLWATCH_RETRY_AFTER_ERROR
        except asyncio.CancelledError:
            log.info("BullWatch hot tier loop cancelled")
            raise
        except Exception as e:
            log.warning("BullWatch hot tier loop tick failed: %r", e)
            sleep_for = BULLWATCH_RETRY_AFTER_ERROR
        await asyncio.sleep(sleep_for)


def _run_bullwatch_hot_tier() -> Optional[dict]:
    """Subset-scan the top N tickers and persist as `bullwatch_hot`.

    Returns the new snapshot payload, None when no cold snapshot exists
    yet (boot race) or there's nothing scoreable.
    """
    from core.snapshot_store import get_default_store
    store = get_default_store()
    top = store.read_top("bullwatch", BULLWATCH_HOT_SIZE)
    if not top:
        log.debug("hot tier: no cold snapshot yet, skipping")
        return None
    tickers = [t for t, _ in top]

    from engine.bullwatch import scan as _bw_scan
    results = _bw_scan(
        tickers,
        include_ineligible=True,
        max_workers=8,
    )
    items = [r.to_dict() for r in results if r.eligible]
    if not items:
        log.info("hot tier: no eligible items in subset (universe=%d)", len(tickers))
        return None

    scored = [
        (it.get("symbol"), float(it.get("score") or 0), it)
        for it in items
        if it.get("symbol")
    ]
    scan_id = store.write_snapshot(
        "bullwatch_hot",
        scored,
        meta={
            "universe_size": len(tickers),
            "source_module": "bullwatch",
            "tier": "hot",
            "size_target": BULLWATCH_HOT_SIZE,
        },
    )
    log.info(
        "hot tier: wrote %d items as %s (subset of bullwatch top %d)",
        len(items), scan_id, BULLWATCH_HOT_SIZE,
    )
    return {"scan_id": scan_id, "items": items}


# ================================================================
# KAP DISCLOSURE FEED LOOP (Faz 1)
# ================================================================


def _in_peak_window() -> bool:
    """Return True during the two daily windows when KAP announcements
    spike: 08:00-09:30 local and 18:00-21:00 local."""
    now = dt.datetime.now()  # local time on the deploy host
    h, m = now.hour, now.minute
    if h == 8 or (h == 9 and m <= 30):
        return True
    if 18 <= h < 21:
        return True
    return False


async def kap_feed_loop() -> None:
    """Periodically poll KAP for new disclosures and fan out side
    effects (Plan C cache invalidation; Faz 3 AI queue).

    Errors are isolated to one cycle — the loop never dies on a
    bad day at kap.org.tr. Cadence switches between peak and
    off-hours intervals based on local time.
    """
    log.info(
        "KAP feed loop scheduled — first run in %ds, then %ds peak / %ds off-hours",
        KAP_FEED_STARTUP_DELAY, KAP_FEED_INTERVAL_PEAK, KAP_FEED_INTERVAL_OFFHOURS,
    )
    await asyncio.sleep(KAP_FEED_STARTUP_DELAY)

    while True:
        sleep_for = (
            KAP_FEED_INTERVAL_PEAK if _in_peak_window()
            else KAP_FEED_INTERVAL_OFFHOURS
        )
        try:
            from engine.kap_feed import run_one_cycle
            await asyncio.to_thread(run_one_cycle)
        except asyncio.CancelledError:
            log.info("KAP feed loop cancelled")
            raise
        except Exception as exc:
            log.warning("KAP feed cycle raised: %r", exc)
            sleep_for = KAP_FEED_RETRY_AFTER_ERROR
        await asyncio.sleep(sleep_for)


# ================================================================
# KAP REACTION REFRESH LOOP (Faz 4)
# ================================================================


async def kap_reactions_loop() -> None:
    """Once-per-day backfill of 1d/1w/1m post-announcement price
    reactions on KAP disclosures."""
    log.info(
        "KAP reactions loop scheduled — first run in %ds, then daily",
        KAP_REACTIONS_STARTUP_DELAY,
    )
    await asyncio.sleep(KAP_REACTIONS_STARTUP_DELAY)
    while True:
        try:
            from engine.kap_reactions import refresh_reactions
            stats = await asyncio.to_thread(refresh_reactions, 200)
            log.info("KAP reactions tick stats: %s", stats)
        except asyncio.CancelledError:
            log.info("KAP reactions loop cancelled")
            raise
        except Exception as exc:
            log.warning("KAP reactions cycle raised: %r", exc)
        await asyncio.sleep(KAP_REACTIONS_INTERVAL)


# ================================================================
# BULLWATCH ALARM REACTION REFRESH LOOP (BW Alarm Faz 4)
# ================================================================


async def bw_alert_reactions_loop() -> None:
    """Daily backfill of 1d/1w/1m post-alarm price reactions on
    BullWatchAlert rows. Mirrors kap_reactions_loop structure."""
    log.info(
        "BW alarm reactions loop scheduled — first run in %ds, then daily",
        KAP_REACTIONS_STARTUP_DELAY + 600,  # +10 min after KAP loop
    )
    await asyncio.sleep(KAP_REACTIONS_STARTUP_DELAY + 600)
    while True:
        try:
            from engine.bullwatch_alert_reactions import refresh_alert_reactions
            stats = await asyncio.to_thread(refresh_alert_reactions, 200)
            log.info("BW alarm reactions tick stats: %s", stats)
        except asyncio.CancelledError:
            log.info("BW alarm reactions loop cancelled")
            raise
        except Exception as exc:
            log.warning("BW alarm reactions cycle raised: %r", exc)
        await asyncio.sleep(KAP_REACTIONS_INTERVAL)

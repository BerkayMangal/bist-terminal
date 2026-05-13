# ================================================================
# BISTBULL TERMINAL — KAP FEED ENGINE
# engine/kap_feed.py
#
# Pulls fresh KAP disclosures across the whole BIST universe, persists
# them, and dispatches side effects (Plan C cache invalidation; Faz 3
# AI analysis queue).
#
# Polling strategy
#   - One cycle iterates every ticker pykap knows about (~700 with
#     funds; ~500 real companies).
#   - 16 concurrent workers — small per-ticker calls, network-bound.
#   - Per-ticker window: only the last LOOKBACK_DAYS so we don't refetch
#     years of history every cycle. Combined with disclosure_index
#     dedup we still get full coverage with minimal HTTP cost.
#   - Cadence is owned by engine.background_tasks (5-30 min depending
#     on market hours). This module only knows how to run ONE cycle.
# ================================================================

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from data import kap_client
from infra import kap_storage
from engine import kap_dispatcher

log = logging.getLogger("bistbull.kap_feed")

# How far back each ticker poll looks. 7 days is a comfortable buffer
# for any realistic polling cadence (we run every 5-30 min) — plenty of
# slack for transient outages.
LOOKBACK_DAYS = 7

# Concurrent workers per cycle. pykap calls are I/O-bound; the kap.org.tr
# server tolerates this load comfortably in our recon tests.
MAX_WORKERS = 16

# Restrict the per-cycle universe so we don't go wild on the first run.
# Production setting in config is FULL_BIST (437); pykap returns ~700
# names (funds included). We intersect with the FULL_BIST list to keep
# the cycle focused.
def _universe() -> list[str]:
    try:
        from config import FULL_BIST
        return list(FULL_BIST)
    except ImportError:
        return kap_client.bist_company_tickers()


@dataclass
class CycleStats:
    """Per-cycle telemetry — surfaced in /api/kap/health."""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    universe_size: int = 0
    tickers_with_disclosures: int = 0
    total_disclosures_seen: int = 0
    new_disclosures_persisted: int = 0
    errors: int = 0
    highest_index_seen: int = 0

    def duration_sec(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": self.duration_sec(),
            "universe_size": self.universe_size,
            "tickers_with_disclosures": self.tickers_with_disclosures,
            "total_disclosures_seen": self.total_disclosures_seen,
            "new_disclosures_persisted": self.new_disclosures_persisted,
            "errors": self.errors,
            "highest_index_seen": self.highest_index_seen,
        }


# Last cycle's stats — read by /api/kap/health
_last_cycle: Optional[CycleStats] = None


def get_last_cycle_stats() -> Optional[dict]:
    return _last_cycle.to_dict() if _last_cycle is not None else None


def run_one_cycle(universe: Optional[list[str]] = None) -> CycleStats:
    """Execute a single poll cycle. Returns telemetry; new disclosures
    are persisted and dispatched as a side effect."""
    global _last_cycle
    stats = CycleStats()
    syms = list(universe) if universe is not None else _universe()
    stats.universe_size = len(syms)
    if not syms:
        log.warning("KAP feed: empty universe, skipping cycle")
        stats.finished_at = time.time()
        _last_cycle = stats
        return stats

    last_seen = kap_storage.get_last_seen_index()
    log.info(
        "KAP feed cycle start: %d tickers, last_seen_index=%d, lookback=%dd",
        len(syms), last_seen, LOOKBACK_DAYS,
    )

    # Per-ticker fetch in parallel. pykap calls are independent so a
    # straight ThreadPoolExecutor scales well. We pull BOTH financial
    # reports (FR) and general announcements (ODA) — the latter is
    # where operator signals (insider trades, KAP warnings, M&A) live.
    def _fetch_one(sym: str) -> tuple[str, list, Optional[Exception]]:
        try:
            fr = kap_client.list_disclosures(sym, days=LOOKBACK_DAYS) or []
            oda = kap_client.list_general_announcements(sym, days=LOOKBACK_DAYS) or []
            return sym, list(fr) + list(oda), None
        except Exception as exc:
            return sym, [], exc

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_fetch_one, s) for s in syms]
        for fut in as_completed(futures):
            try:
                sym, records, err = fut.result(timeout=30)
            except Exception as exc:
                stats.errors += 1
                log.debug("KAP feed future failed: %r", exc)
                continue
            if err is not None:
                stats.errors += 1
                continue
            if not records:
                continue
            stats.tickers_with_disclosures += 1
            stats.total_disclosures_seen += len(records)
            for rec in records:
                if rec.disclosure_index > stats.highest_index_seen:
                    stats.highest_index_seen = rec.disclosure_index
                # Skip if we've already seen this disclosure_index in a
                # previous cycle (incremental polling). For the FIRST
                # cycle (last_seen=0) we still want to persist everything
                # so we have a baseline.
                if last_seen and rec.disclosure_index <= last_seen:
                    continue
                if kap_storage.save_disclosure(rec):
                    stats.new_disclosures_persisted += 1
                    try:
                        kap_dispatcher.dispatch_new_disclosure(rec)
                    except Exception as exc:
                        log.warning(
                            "dispatch failed for %s/%s: %r",
                            rec.ticker, rec.disclosure_index, exc,
                        )

    # Bump high-water mark only on success — if everything errored we
    # don't want to silently advance past unseen events.
    if stats.highest_index_seen > last_seen:
        kap_storage.set_last_seen_index(stats.highest_index_seen)

    stats.finished_at = time.time()
    _last_cycle = stats
    log.info(
        "KAP feed cycle done in %.1fs — %d tickers, %d new (errors=%d)",
        stats.duration_sec(),
        stats.tickers_with_disclosures,
        stats.new_disclosures_persisted,
        stats.errors,
    )
    return stats

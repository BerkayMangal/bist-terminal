"""Background auto-refresh for stale fundamental data.

Runs on a fixed cadence (default 6h). Each cycle:

  1. Pulls the current scan universe (or config UNIVERSE as fallback)
  2. Computes the freshness summary (compute_summary)
  3. Picks the worst N stale/unknown tickers
  4. Force-refreshes each via analyze_symbol — same path as the manual
     button on the Veri Tazeliği panel
  5. Records before/after score so the UI can show "N skor değişti
     (avg Δ X)" — the smoking gun for "did my system actually pick up
     the new bilanço?"

This is the passive companion to the manual batch refresh. The user
shouldn't need to click anything for the system to stay healthy.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("bistbull.auto_refresh")

# Cycle cadence + per-cycle budget.
DEFAULT_INTERVAL_SEC = 6 * 3600          # 6 hours
DEFAULT_MAX_PER_CYCLE = 20               # don't hammer borsapy
SIGNIFICANT_SCORE_DELTA = 2.0            # ≥2 points = "moved"


@dataclass
class CycleResult:
    """Per-cycle telemetry — surfaced by /api/diag/auto-refresh/status."""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    universe_size: int = 0
    candidates_found: int = 0
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    score_changes: list[dict[str, Any]] = field(default_factory=list)
    sample_errors: list[str] = field(default_factory=list)

    def duration_sec(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    def to_dict(self) -> dict[str, Any]:
        deltas = [abs(c["delta"]) for c in self.score_changes
                  if c.get("delta") is not None]
        avg_delta = round(sum(deltas) / len(deltas), 2) if deltas else None
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": self.duration_sec(),
            "universe_size": self.universe_size,
            "candidates_found": self.candidates_found,
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "score_changes": self.score_changes,
            "score_change_count": len(self.score_changes),
            "avg_abs_delta": avg_delta,
            "sample_errors": self.sample_errors[:5],
        }


_last_cycle: Optional[CycleResult] = None
_lock = threading.Lock()


def get_last_cycle() -> Optional[dict[str, Any]]:
    with _lock:
        return _last_cycle.to_dict() if _last_cycle else None


def _universe_for_refresh() -> list[str]:
    """Prefer the live scan output (the tickers the user actually sees on
    the Radar) over the config UNIVERSE — refreshing names that aren't
    in the radar is wasted borsapy budget."""
    try:
        from app import get_top10_items  # type: ignore
        items = get_top10_items() or []
        syms = [
            (i.get("ticker") or i.get("symbol") or "").upper()
            for i in items if (i.get("ticker") or i.get("symbol"))
        ]
        if syms:
            return syms
    except Exception:
        pass
    try:
        from config import UNIVERSE
        return [u.replace(".IS", "").upper() for u in UNIVERSE]
    except Exception:
        return []


def _previous_score(ticker: str) -> Optional[float]:
    """Pull the most recent score from analysis_cache for delta tracking.
    Falls back to None so the cycle still completes if cache is empty."""
    try:
        from core.cache import analysis_cache
        cached = analysis_cache.get(ticker + ".IS") or analysis_cache.get(ticker)
        if cached and isinstance(cached, dict):
            return cached.get("score")
    except Exception as exc:
        log.debug("prev score lookup %s: %r", ticker, exc)
    return None


def run_one_cycle(
    max_per_cycle: int = DEFAULT_MAX_PER_CYCLE,
) -> CycleResult:
    """Execute a single auto-refresh cycle. Returns telemetry."""
    global _last_cycle
    res = CycleResult()
    try:
        from engine.diag_fundamentals import (
            compute_summary, filter_stale_rows,
        )
        from engine.kap_dispatcher import _invalidate_caches_for_ticker
        from engine.analysis import analyze_symbol

        universe = _universe_for_refresh()
        res.universe_size = len(universe)
        if not universe:
            res.finished_at = time.time()
            with _lock:
                _last_cycle = res
            return res

        summary = compute_summary(universe)
        candidates = filter_stale_rows(
            summary.get("items") or [],
            threshold="stale",
        )
        res.candidates_found = len(candidates)
        to_refresh = candidates[:max_per_cycle]
        res.attempted = len(to_refresh)

        for row in to_refresh:
            sym = (row.get("ticker") or "").upper()
            if not sym:
                continue
            try:
                before_score = _previous_score(sym)
                _invalidate_caches_for_ticker(sym)
                analysis = analyze_symbol(sym + ".IS")
                if analysis is None:
                    res.failed += 1
                    continue
                res.succeeded += 1
                after_score = (analysis or {}).get("score")
                if (before_score is not None
                        and after_score is not None
                        and abs(after_score - before_score) >= SIGNIFICANT_SCORE_DELTA):
                    res.score_changes.append({
                        "ticker": sym,
                        "before": round(float(before_score), 1),
                        "after": round(float(after_score), 1),
                        "delta": round(float(after_score) - float(before_score), 1),
                    })
            except Exception as exc:
                res.failed += 1
                msg = f"{sym}: {type(exc).__name__}: {exc}"
                res.sample_errors.append(msg)
                log.debug("auto_refresh %s failed: %r", sym, exc)
    except Exception as exc:
        log.exception("auto_refresh cycle hard-failed: %r", exc)
        res.sample_errors.append(f"cycle: {type(exc).__name__}: {exc}")
    finally:
        res.finished_at = time.time()
        # Sort score_changes by abs(delta) desc so the UI surfaces the
        # biggest moves first.
        res.score_changes.sort(
            key=lambda c: -abs(c.get("delta") or 0),
        )
        with _lock:
            _last_cycle = res
    log.info(
        "auto_refresh cycle: universe=%d candidates=%d attempted=%d "
        "succeeded=%d failed=%d score_changes=%d duration=%.1fs",
        res.universe_size, res.candidates_found, res.attempted,
        res.succeeded, res.failed, len(res.score_changes),
        res.duration_sec(),
    )
    return res


async def background_loop(
    interval_sec: int = DEFAULT_INTERVAL_SEC,
    startup_delay_sec: int = 120,
    max_per_cycle: int = DEFAULT_MAX_PER_CYCLE,
) -> None:
    """asyncio loop driver. Sleeps interval_sec between cycles."""
    import asyncio
    log.info(
        "auto_refresh loop starting (interval=%ds, startup_delay=%ds, "
        "max_per_cycle=%d)",
        interval_sec, startup_delay_sec, max_per_cycle,
    )
    await asyncio.sleep(startup_delay_sec)
    while True:
        try:
            await asyncio.to_thread(run_one_cycle, max_per_cycle)
        except Exception as exc:
            log.warning("auto_refresh loop caught error: %r", exc)
        await asyncio.sleep(interval_sec)

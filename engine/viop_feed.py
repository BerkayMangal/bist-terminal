"""VIOP daily snapshot ingestion.

Pulls the 4 VIOP categories from borsapy and persists them via
viop_storage.save_snapshot. Runs on a background cadence — once
per trading day is plenty; UOA z-scores need DAILY granularity not
intraday.

Categories (borsapy.VIOP attributes — cached_property on instance):
  - stock_options       (~180 stock-tied options)
  - stock_futures
  - index_futures       (XU030, XU100, XLBNK ...)
  - index_options
  - currency_futures    (USDTRY, EURTRY)
  - commodity_futures   (gold, silver, oil)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("bistbull.viop_feed")

# Background cadence — pull once an hour during market hours.
DEFAULT_INTERVAL_SEC = 60 * 60                # 1 hour
DEFAULT_STARTUP_DELAY_SEC = 90                # let other init settle

# Which VIOP DataFrames to ingest. Tuple of (instance-attr, category-hint)
_CATEGORIES = (
    "stock_options",
    "stock_futures",
    "index_futures",
    "index_options",
    "currency_futures",
    "commodity_futures",
)


@dataclass
class CycleStats:
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    categories_fetched: int = 0
    rows_persisted: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": round(
                (self.finished_at or time.time()) - self.started_at, 1
            ),
            "categories_fetched": self.categories_fetched,
            "rows_persisted": self.rows_persisted,
            "errors": self.errors[:5],
        }


_last_cycle: Optional[CycleStats] = None


def get_last_cycle() -> Optional[dict[str, Any]]:
    return _last_cycle.to_dict() if _last_cycle else None


def _df_to_rows(df: Any) -> list[dict[str, Any]]:
    """Convert a borsapy VIOP DataFrame into list-of-dicts safely."""
    if df is None:
        return []
    try:
        # pandas DataFrame
        return df.to_dict("records")
    except Exception:
        pass
    # Already iterable?
    try:
        return list(df)
    except Exception:
        return []


def run_one_cycle() -> CycleStats:
    """Fetch all VIOP categories and persist. Single sync pass — safe to
    wrap in asyncio.to_thread from the loop driver.
    """
    global _last_cycle
    res = CycleStats()
    try:
        import borsapy as bp
    except Exception as exc:
        msg = f"borsapy import: {type(exc).__name__}: {exc}"
        log.warning("viop ingest: %s", msg)
        res.errors.append(msg)
        res.finished_at = time.time()
        _last_cycle = res
        return res

    try:
        v = bp.VIOP()
    except Exception as exc:
        msg = f"VIOP instance: {type(exc).__name__}: {exc}"
        log.warning("viop ingest: %s", msg)
        res.errors.append(msg)
        res.finished_at = time.time()
        _last_cycle = res
        return res

    try:
        from infra import viop_storage
    except Exception as exc:
        msg = f"viop_storage import: {type(exc).__name__}: {exc}"
        log.warning("viop ingest: %s", msg)
        res.errors.append(msg)
        res.finished_at = time.time()
        _last_cycle = res
        return res

    for cat in _CATEGORIES:
        try:
            df = getattr(v, cat, None)
            if df is None:
                continue
            rows = _df_to_rows(df)
            if not rows:
                continue
            # Some borsapy frames may return rows without `category`;
            # fill from the attr name so downstream queries work.
            for r in rows:
                if not r.get("category"):
                    r["category"] = cat.replace("_futures", "").replace(
                        "_options", ""
                    )
            n = viop_storage.save_snapshot(rows)
            res.rows_persisted += n
            res.categories_fetched += 1
        except Exception as exc:
            msg = f"{cat}: {type(exc).__name__}: {exc}"
            log.debug("viop ingest %s", msg)
            res.errors.append(msg)

    res.finished_at = time.time()
    log.info(
        "VIOP ingest cycle: categories=%d rows=%d duration=%.1fs errors=%d",
        res.categories_fetched, res.rows_persisted,
        res.finished_at - res.started_at, len(res.errors),
    )
    _last_cycle = res
    return res


async def background_loop(
    interval_sec: int = DEFAULT_INTERVAL_SEC,
    startup_delay_sec: int = DEFAULT_STARTUP_DELAY_SEC,
) -> None:
    """asyncio loop driver — same shape as auto_refresh_stale.background_loop.
    Fires `run_one_cycle` every interval_sec, off the event loop thread."""
    log.info(
        "VIOP feed loop starting (interval=%ds, startup_delay=%ds)",
        interval_sec, startup_delay_sec,
    )
    await asyncio.sleep(startup_delay_sec)
    while True:
        try:
            await asyncio.to_thread(run_one_cycle)
        except Exception as exc:
            log.warning("VIOP feed loop caught error: %r", exc)
        await asyncio.sleep(interval_sec)

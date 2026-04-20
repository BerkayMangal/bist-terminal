"""Fundamentals backfill into fundamentals_pit.

Phase 2 shipped --dry-run + checkpoint-resume; Phase 3 FAZ 3.0 lands
the real borsapy fetch + threaded concurrent runner.

Respects:
- BATCH_HISTORY_WORKERS (config.py) as the concurrent-request cap.
- core.circuit_breaker -- borsapy 'open' state bails with a checkpoint.
- --dry-run mode -- synthesizes plausible filings (unchanged from Phase 2).

USAGE
  # Real run (requires borsapy + network):
  python -m research.ingest_filings \\
      --symbols THYAO,AKBNK,ISCTR \\
      --from 2016-01-01 --to 2026-01-01

  # Dry run (no network):
  python -m research.ingest_filings \\
      --symbols THYAO,AKBNK,ISCTR \\
      --from 2016-01-01 --to 2026-01-01 \\
      --dry-run

  # Resume from checkpoint:
  python -m research.ingest_filings --resume

CHECKPOINT
  /tmp/bistbull_ingest_checkpoint.json -- written after every symbol
  completes. Includes args + completed[] + totals + per-symbol errors.

THREADING
  Uses ThreadPoolExecutor(max_workers=BATCH_HISTORY_WORKERS) when
  --threaded (default true for real, false for dry_run because there's
  no network waiting). The checkpoint write is serialized via a lock
  so concurrent completions don't race the JSON file.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("bistbull.research.ingest_filings")

CHECKPOINT_PATH = Path("/tmp/bistbull_ingest_checkpoint.json")

QUARTERLY_METRICS = ["revenue", "net_income", "roe", "debt_to_equity"]

_CHECKPOINT_LOCK = threading.Lock()


def _iso(d: date) -> str:
    return d.isoformat()


def _quarter_ends(start: date, end: date) -> list[date]:
    """All quarter-end dates between start and end (inclusive)."""
    ends = []
    year = start.year
    while year <= end.year:
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            qend = date(year, month, day)
            if start <= qend <= end:
                ends.append(qend)
        year += 1
    return ends


def _synthetic_filing(symbol: str, period_end: date) -> dict:
    """Deterministic synthetic filing for a (symbol, period_end) pair.

    Same symbol + period -> same values across runs. Filing lag 40-75
    days. Stable for test assertions.
    """
    seed = hash((symbol, period_end.isoformat())) & 0xFFFFFFFF
    rng = random.Random(seed)
    base_rev = 1e9 + rng.random() * 9e9
    ni_margin = 0.05 + rng.random() * 0.15
    revenue = round(base_rev, 0)
    net_income = round(revenue * ni_margin, 0)
    equity = round(net_income * 8, 0) or 1.0
    roe = round(net_income / equity, 4)
    debt_to_equity = round(0.3 + rng.random() * 1.2, 4)
    filed_at = period_end + timedelta(days=40 + int(rng.random() * 36))
    return {
        "period_end": period_end,
        "filed_at": filed_at,
        "metrics": {
            "revenue": revenue,
            "net_income": net_income,
            "roe": roe,
            "debt_to_equity": debt_to_equity,
        },
    }


def _write_pit_rows(symbol: str, filings: list[dict], source: str) -> int:
    """Persist a list of filings into fundamentals_pit. Returns rows written."""
    from infra.pit import save_fundamental

    count = 0
    for filing in filings:
        for metric, value in filing["metrics"].items():
            save_fundamental(
                symbol=symbol,
                period_end=filing["period_end"],
                filed_at=filing["filed_at"],
                source=source,
                metric=metric,
                value=value,
            )
            count += 1
    return count


def _fetch_real(
    symbol: str,
    from_date: date,
    to_date: date,
    fetcher: Optional[Callable] = None,
) -> list[dict]:
    """Fetch quarterly filings from borsapy for one symbol.

    Shape returned: list of filings in the internal dict format:
        {"period_end": date, "filed_at": date,
         "metrics": {name: value_or_none, ...}}

    The fetcher argument exists so tests (and the Phase 3.0 dry-run
    harness) can inject a deterministic mock:
        fetcher(symbol) -> iterable of raw filing records
    If None, the real borsapy path is used.

    Real-path contract (expected from data/providers.py:fetch_raw_v9 when
    a Phase 3b follow-up wires it through):
        borsapy.get_filings(symbol) -> list[RawFiling]
        RawFiling fields used here:
          .period_end (date), .filed_at (date or datetime), .statements
          .statements["income"]["revenue"] etc.
    We map RawFiling.statements into QUARTERLY_METRICS values; missing
    metrics are None (persists as NULL in value column).

    Error semantics:
      - Transient HTTP errors (timeout, 5xx) raise -- caller retries via
        the ThreadPoolExecutor's future, which ingest() catches and
        records in the checkpoint.
      - Data-shape errors (unexpected RawFiling layout) also raise -- the
        operator needs to see them, not silently drop rows.
      - Empty responses return [] (symbol truly has no filings in range).
    """
    if fetcher is None:
        # Default path: import borsapy lazily so the module is importable
        # in environments without borsapy installed (e.g. this sandbox).
        try:
            import borsapy  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "borsapy not installed. Install it on the ingest host or "
                "pass fetcher=... for testing. See data/providers.py."
            ) from e
        fetcher = borsapy.get_filings

    raw_filings = list(fetcher(symbol))
    out: list[dict] = []
    for raw in raw_filings:
        try:
            period_end = _coerce_date(getattr(raw, "period_end", None)
                                      if not isinstance(raw, dict)
                                      else raw.get("period_end"))
            filed_at = _coerce_date(getattr(raw, "filed_at", None)
                                    if not isinstance(raw, dict)
                                    else raw.get("filed_at"))
        except Exception as e:
            raise ValueError(
                f"unparseable date in raw filing for {symbol}: {e}"
            ) from e

        if period_end is None or filed_at is None:
            # Skip filings without a period_end / filed_at. Defensive:
            # better to drop a row than to stamp a wrong date into PIT.
            continue
        if not (from_date <= period_end <= to_date):
            continue

        statements = (raw.get("statements") if isinstance(raw, dict)
                      else getattr(raw, "statements", {})) or {}
        metrics: dict[str, Optional[float]] = {}
        for metric in QUARTERLY_METRICS:
            metrics[metric] = _extract_metric(statements, metric)

        out.append({"period_end": period_end, "filed_at": filed_at,
                    "metrics": metrics})
    return out


def _coerce_date(x) -> Optional[date]:
    """Best-effort date coercion. None for None / empty."""
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, str):
        return datetime.fromisoformat(x[:10]).date()
    raise TypeError(f"cannot coerce {type(x).__name__} to date")


# Mapping of our internal metric name -> borsapy statement path.
# Split out so Phase 3 follow-ups can add metrics without touching _fetch_real.
_METRIC_PATHS: dict[str, list[tuple[str, str]]] = {
    # (statement_bucket, field_name) candidates in order; first match wins
    "revenue":        [("income", "revenue"), ("income", "total_revenue"),
                       ("income", "net_sales")],
    "net_income":     [("income", "net_income"), ("income", "profit_after_tax")],
    "roe":            [("ratios", "roe"), ("ratios", "return_on_equity")],
    "debt_to_equity": [("ratios", "debt_to_equity"), ("balance", "debt_to_equity")],
}


def _extract_metric(statements: dict, metric: str) -> Optional[float]:
    """Pull a single metric value out of a borsapy statements blob.

    Returns None if not found in any of the candidate paths.
    """
    for bucket, field in _METRIC_PATHS.get(metric, []):
        try:
            v = statements.get(bucket, {}).get(field)
        except AttributeError:
            continue
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _load_checkpoint() -> Optional[dict]:
    if not CHECKPOINT_PATH.exists():
        return None
    try:
        return json.loads(CHECKPOINT_PATH.read_text())
    except Exception as e:
        log.warning(f"checkpoint read failed: {e}; starting from scratch")
        return None


def _write_checkpoint(state: dict) -> None:
    with _CHECKPOINT_LOCK:
        CHECKPOINT_PATH.write_text(json.dumps(state, indent=2, default=str))


def _run_one_symbol(
    symbol: str,
    from_date: date,
    to_date: date,
    dry_run: bool,
    source: str,
    fetcher: Optional[Callable] = None,
) -> tuple[str, int, int, Optional[str]]:
    """Fetch + persist filings for one symbol. Returns (sym, n_filings, n_rows, err_str).

    Runs in a worker thread when threaded. SQLite writes are serialized
    at the DB layer (single WAL connection per thread; PRAGMA busy_timeout
    handles contention).
    """
    try:
        if dry_run:
            q_ends = _quarter_ends(from_date, to_date)
            filings = [_synthetic_filing(symbol, qe) for qe in q_ends]
            time.sleep(0.001)
        else:
            filings = _fetch_real(symbol, from_date, to_date, fetcher=fetcher)
    except Exception as e:
        return symbol, 0, 0, f"{type(e).__name__}: {e}"

    try:
        rows = _write_pit_rows(symbol, filings, source=source)
    except Exception as e:
        return symbol, len(filings), 0, f"{type(e).__name__}: {e}"

    return symbol, len(filings), rows, None


def ingest(
    symbols: list[str],
    from_date: date,
    to_date: date,
    dry_run: bool = False,
    source: Optional[str] = None,
    resume: bool = False,
    threaded: Optional[bool] = None,
    max_workers: Optional[int] = None,
    fetcher: Optional[Callable] = None,
) -> dict:
    """Backfill fundamentals for the given symbols over the date range.

    threaded:
      None (default) -> False for dry_run (I/O is fake), True for real.
      True  -> ThreadPoolExecutor(max_workers=BATCH_HISTORY_WORKERS or arg).
      False -> sequential.

    max_workers:
      None -> config.BATCH_HISTORY_WORKERS (falls back to 5 if unavailable).

    Returns summary {completed, totals, errors}.
    """
    source = source or ("synthetic" if dry_run else "borsapy")

    if threaded is None:
        threaded = not dry_run

    if max_workers is None:
        try:
            from config import BATCH_HISTORY_WORKERS
            max_workers = int(BATCH_HISTORY_WORKERS)
        except Exception:
            max_workers = 5

    # Checkpoint load
    state: dict
    if resume:
        state = _load_checkpoint() or {}
    else:
        state = {}
    if state:
        completed = set(state.get("completed", []))
        errors = dict(state.get("errors", {}))
        log.info(f"resume: skipping {len(completed)} completed symbols")
    else:
        completed = set()
        errors = {}
        state = {
            "args": {
                "symbols": symbols, "from": _iso(from_date), "to": _iso(to_date),
                "dry_run": dry_run, "source": source,
                "threaded": threaded, "max_workers": max_workers,
            },
            "completed": [],
            "errors": {},
            "totals": {"symbols": 0, "filings": 0, "rows": 0},
        }

    # Circuit breaker check (real-mode only)
    if not dry_run:
        try:
            from core.circuit_breaker import all_provider_status
            breaker = all_provider_status().get("borsapy")
            if breaker and breaker.get("state") == "open":
                log.warning("borsapy circuit breaker is open; aborting ingest")
                state["totals"]["error"] = "breaker_open_at_start"
                _write_checkpoint(state)
                return state
        except Exception as e:
            log.debug(f"breaker check skipped: {e}")

    q_ends = _quarter_ends(from_date, to_date)
    todo = [s for s in symbols if s not in completed]
    log.info(
        f"ingest: {len(todo)}/{len(symbols)} symbols × {len(q_ends)} quarters "
        f"({from_date} to {to_date}), source={source}, dry_run={dry_run}, "
        f"threaded={threaded} (max_workers={max_workers})"
    )

    def _on_done(symbol: str, n_filings: int, n_rows: int, err: Optional[str]) -> None:
        if err is not None:
            errors[symbol] = err
            state["errors"] = dict(errors)
            log.warning(f"  {symbol}: ERROR {err}")
        else:
            completed.add(symbol)
            state["completed"] = sorted(completed)
            state["totals"]["symbols"] += 1
            state["totals"]["filings"] += n_filings
            state["totals"]["rows"] += n_rows
            log.info(f"  {symbol}: {n_filings} filings, {n_rows} rows")
        _write_checkpoint(state)

    if threaded and len(todo) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _run_one_symbol, sym, from_date, to_date,
                    dry_run, source, fetcher,
                ): sym
                for sym in todo
            }
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    result = fut.result()
                except Exception as e:
                    # _run_one_symbol catches its own errors; reaching here
                    # means something unexpected happened in the pool harness.
                    result = (sym, 0, 0, f"PoolError: {type(e).__name__}: {e}")
                _on_done(*result)
    else:
        for sym in todo:
            result = _run_one_symbol(sym, from_date, to_date, dry_run, source, fetcher)
            _on_done(*result)

    log.info(f"ingest done: {state['totals']} (errors: {len(errors)})")
    return state


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", type=str,
                    help="comma-separated symbols e.g. THYAO,AKBNK,ISCTR")
    ap.add_argument("--from", dest="from_date", type=str,
                    help="ISO start date (inclusive)")
    ap.add_argument("--to", dest="to_date", type=str,
                    help="ISO end date (inclusive)")
    ap.add_argument("--dry-run", action="store_true",
                    help="generate synthetic filings instead of fetching")
    ap.add_argument("--source", type=str, default=None,
                    help="override source tag")
    ap.add_argument("--resume", action="store_true",
                    help="resume from checkpoint, skipping completed symbols")
    ap.add_argument("--threaded", action="store_true", default=None,
                    help="force threaded run (default: threaded for real, sequential for dry-run)")
    ap.add_argument("--no-threaded", dest="threaded", action="store_false",
                    help="force sequential run")
    ap.add_argument("--max-workers", type=int, default=None,
                    help="override BATCH_HISTORY_WORKERS")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)

    if args.resume:
        state = _load_checkpoint()
        if not state:
            print("no checkpoint to resume from", file=sys.stderr)
            return 1
        prev = state["args"]
        symbols = prev["symbols"]
        from_date = datetime.fromisoformat(prev["from"]).date()
        to_date = datetime.fromisoformat(prev["to"]).date()
        dry_run = prev["dry_run"]
    else:
        if not (args.symbols and args.from_date and args.to_date):
            print("need --symbols, --from, --to (or --resume)", file=sys.stderr)
            return 1
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        from_date = datetime.fromisoformat(args.from_date).date()
        to_date = datetime.fromisoformat(args.to_date).date()
        dry_run = args.dry_run

    from infra.storage import init_db
    init_db()

    result = ingest(
        symbols=symbols, from_date=from_date, to_date=to_date,
        dry_run=dry_run, source=args.source, resume=args.resume,
        threaded=args.threaded, max_workers=args.max_workers,
    )
    print(json.dumps(result["totals"], indent=2))
    if result.get("errors"):
        print(f"errors in {len(result['errors'])} symbols; see checkpoint",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""OHLCV price-history backfill into price_history_pit.

Phase 3 FAZ 3.0. Companion to ingest_filings.py. Same shape:
--dry-run synthesizes a plausible random-walk; real mode pulls from
borsapy via data/providers.py.

Volume estimation (Phase 3 spec): 30 tickers × 10 years × ~250 trading
days ≈ 75,000 bars. BATCH_HISTORY_WORKERS=5 concurrent. Checkpoint file
/tmp/bistbull_ingest_prices_checkpoint.json.
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

log = logging.getLogger("bistbull.research.ingest_prices")

CHECKPOINT_PATH = Path("/tmp/bistbull_ingest_prices_checkpoint.json")
_CHECKPOINT_LOCK = threading.Lock()


def _iso(d: date) -> str:
    return d.isoformat()


def _trading_days(start: date, end: date) -> list[date]:
    """Weekdays (Mon-Fri) in [start, end]. Borsa Istanbul mostly observes
    these; a real implementation would also filter holidays, but for the
    synthetic dry-run and the labeler which uses <=as_of anyway, this is
    close enough. Real ingest queries borsapy which returns only actual
    trading days, so the holiday list is implicit there.
    """
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _synthetic_bars(symbol: str, trading_days: list[date]) -> list[dict]:
    """Deterministic random-walk OHLCV, seeded per symbol.

    Start price = 10 + hash(symbol) % 91  (10 to 100). Daily log-return
    mu=0.0004, sigma=0.02. Volume 500k-2M shares log-normal.
    """
    seed = hash(("ohlcv", symbol)) & 0xFFFFFFFF
    rng = random.Random(seed)
    price = 10.0 + (hash(symbol) & 0xFF) % 91  # 10..100
    bars: list[dict] = []
    for d in trading_days:
        # Log-return
        logret = rng.gauss(0.0004, 0.02)
        close = max(price * (2.718 ** logret), 0.01)
        # Intraday: open typically near prior close, high/low around mid
        open_ = price * (1 + rng.gauss(0, 0.003))
        high = max(open_, close) * (1 + abs(rng.gauss(0, 0.005)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, 0.005)))
        volume = round(rng.lognormvariate(13.5, 0.5))  # ~500k..2M
        bars.append({
            "trade_date": d,
            "open": round(open_, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": volume,
            "adjusted_close": round(close, 2),
        })
        price = close
    return bars


def _write_bars(symbol: str, bars: list[dict], source: str) -> int:
    from infra.pit import save_price
    for bar in bars:
        save_price(
            symbol=symbol, trade_date=bar["trade_date"], source=source,
            open_=bar["open"], high=bar["high"], low=bar["low"],
            close=bar["close"], volume=bar["volume"],
            adjusted_close=bar["adjusted_close"],
        )
    return len(bars)


def _fetch_real(
    symbol: str,
    from_date: date,
    to_date: date,
    fetcher: Optional[Callable] = None,
) -> list[dict]:
    """Fetch OHLCV from borsapy. See ingest_filings._fetch_real for the
    same contract shape. Default path imports borsapy lazily; tests inject
    fetcher(symbol, from_date, to_date) -> iterable of OHLCV records.
    """
    if fetcher is None:
        try:
            import borsapy  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "borsapy not installed. Pass fetcher=... for testing."
            ) from e
        # borsapy >= 0.8.x uses Ticker(sym).history(period=, interval=)
        # rather than module-level borsapy.get_prices(). Adapt the DataFrame
        # output to the dict-of-OHLCV shape this function expects.
        def _bp_history(sym: str, fd, td) -> list[dict]:
            import pandas as _pd
            tk = borsapy.Ticker(sym)
            df = tk.history(period="max", interval="1d")
            if df is None or df.empty:
                return []
            idx = _pd.to_datetime(df.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
            df = df.copy()
            df.index = idx
            # column names vary in case ('Close' vs 'close'); normalize lower
            df.columns = [str(c).lower() for c in df.columns]
            out = []
            for ts, row in df.iterrows():
                out.append({
                    "trade_date": ts.date(),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume"),
                    "adjusted_close": row.get("adj close")
                                       or row.get("adj_close")
                                       or row.get("close"),
                })
            return out
        fetcher = _bp_history

    raw = list(fetcher(symbol, from_date, to_date))
    out: list[dict] = []
    for r in raw:
        get = r.get if isinstance(r, dict) else (lambda k, d=None: getattr(r, k, d))
        try:
            d = _coerce_date(get("trade_date") or get("date"))
        except Exception:
            continue
        if d is None or not (from_date <= d <= to_date):
            continue
        out.append({
            "trade_date": d,
            "open": _f(get("open")), "high": _f(get("high")),
            "low":  _f(get("low")),  "close": _f(get("close")),
            "volume": _f(get("volume")),
            "adjusted_close": _f(get("adjusted_close") or get("adj_close") or get("close")),
        })
    return out


def _coerce_date(x):
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, str):
        return datetime.fromisoformat(x[:10]).date()
    raise TypeError(type(x).__name__)


def _f(x):
    if x is None: return None
    try: return float(x)
    except (TypeError, ValueError): return None


def _load_checkpoint() -> Optional[dict]:
    if not CHECKPOINT_PATH.exists():
        return None
    try:
        return json.loads(CHECKPOINT_PATH.read_text())
    except Exception:
        return None


def _write_checkpoint(state: dict) -> None:
    with _CHECKPOINT_LOCK:
        CHECKPOINT_PATH.write_text(json.dumps(state, indent=2, default=str))


def _run_one(symbol, from_date, to_date, dry_run, source, fetcher):
    try:
        if dry_run:
            bars = _synthetic_bars(symbol, _trading_days(from_date, to_date))
        else:
            bars = _fetch_real(symbol, from_date, to_date, fetcher=fetcher)
    except Exception as e:
        return symbol, 0, f"{type(e).__name__}: {e}"
    try:
        n = _write_bars(symbol, bars, source=source)
    except Exception as e:
        return symbol, 0, f"{type(e).__name__}: {e}"
    return symbol, n, None


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
    source = source or ("synthetic" if dry_run else "borsapy")
    if threaded is None:
        threaded = not dry_run
    if max_workers is None:
        try:
            from config import BATCH_HISTORY_WORKERS
            max_workers = int(BATCH_HISTORY_WORKERS)
        except Exception:
            max_workers = 5

    if resume:
        state = _load_checkpoint() or {}
    else:
        state = {}
    if state:
        completed = set(state.get("completed", []))
        errors = dict(state.get("errors", {}))
    else:
        completed = set()
        errors = {}
        state = {
            "args": {
                "symbols": symbols, "from": _iso(from_date), "to": _iso(to_date),
                "dry_run": dry_run, "source": source,
                "threaded": threaded, "max_workers": max_workers,
            },
            "completed": [], "errors": {},
            "totals": {"symbols": 0, "bars": 0},
        }

    if not dry_run:
        try:
            from core.circuit_breaker import all_provider_status
            breaker = all_provider_status().get("borsapy")
            if breaker and breaker.get("state") == "open":
                state["totals"]["error"] = "breaker_open_at_start"
                _write_checkpoint(state)
                return state
        except Exception:
            pass

    todo = [s for s in symbols if s not in completed]
    log.info(f"ingest_prices: {len(todo)}/{len(symbols)} symbols, dry_run={dry_run}, threaded={threaded}")

    def _done(sym, n, err):
        if err:
            errors[sym] = err
            state["errors"] = dict(errors)
        else:
            completed.add(sym)
            state["completed"] = sorted(completed)
            state["totals"]["symbols"] += 1
            state["totals"]["bars"] += n
        _write_checkpoint(state)

    if threaded and len(todo) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_one, s, from_date, to_date, dry_run, source, fetcher): s for s in todo}
            for fut in as_completed(futures):
                _done(*fut.result())
    else:
        for s in todo:
            _done(*_run_one(s, from_date, to_date, dry_run, source, fetcher))

    return state


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str)
    ap.add_argument("--from", dest="from_date", type=str)
    ap.add_argument("--to", dest="to_date", type=str)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source", default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-workers", type=int, default=None)
    args = ap.parse_args(argv)

    if args.resume:
        state = _load_checkpoint()
        if not state:
            print("no checkpoint", file=sys.stderr); return 1
        prev = state["args"]
        symbols = prev["symbols"]
        fd = datetime.fromisoformat(prev["from"]).date()
        td = datetime.fromisoformat(prev["to"]).date()
        dry = prev["dry_run"]
    else:
        if not (args.symbols and args.from_date and args.to_date):
            print("need --symbols, --from, --to", file=sys.stderr); return 1
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
        fd = datetime.fromisoformat(args.from_date).date()
        td = datetime.fromisoformat(args.to_date).date()
        dry = args.dry_run

    from infra.storage import init_db
    init_db()
    res = ingest(symbols=symbols, from_date=fd, to_date=td,
                 dry_run=dry, source=args.source, resume=args.resume,
                 max_workers=args.max_workers)
    print(json.dumps(res["totals"], indent=2))
    return 2 if res.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

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

    Phase 4 FAZ 4.0.1: corrected to use the actual borsapy 0.8.7 API,
    which does NOT have a top-level ``borsapy.get_filings()`` function.
    The real API is per-ticker methods returning pandas DataFrames:

        bp.Ticker(tc).get_income_stmt(quarterly=True,
                                      financial_group='UFRS' or None,
                                      last_n=32)
        bp.Ticker(tc).get_balance_sheet(quarterly=True, ...)
        bp.Ticker(tc).get_cashflow(quarterly=True, ...)

    Each DataFrame has Turkish KAP line names as rows ("Satış Gelirleri",
    "DÖNEM KARI (ZARARI)", etc.) and period-end dates as columns, with
    the most recent period in column 0.

    Reference implementation: ``data/providers.py:fetch_raw_v9`` uses
    this exact pattern for the live-trading path; the line-name maps
    (IS_MAP, BS_MAP, CF_MAP) are imported from there when available.

    Returned shape (unchanged from Phase 3 to keep the persistence
    layer callers uncoupled):
        [{"period_end": date, "filed_at": date,
          "metrics": {revenue, net_income, roe, debt_to_equity}}]

    filed_at estimation:
        The statement DataFrames do not expose KAP disclosure dates, so
        we estimate filed_at as period_end + 60 days (typical BIST
        disclosure lag). A Phase 4 follow-up could scrape KAP for exact
        dates; for the PIT guarantees we only need filed_at to be
        conservative (later rather than earlier), so +60 days is safe
        for look-ahead-free reads.

    ROE / debt_to_equity:
        Computed from line items (not a separate 'ratios' statement,
        which borsapy does not expose):
            ROE = net_income / equity       (same period)
            D/E = total_debt / equity       (same period)
        Returns None if either input is missing/zero.

    Fetcher injection:
        ``fetcher(symbol) -> {'income': DataFrame, 'balance': DataFrame,
                              'cashflow': DataFrame}``
        Lets tests run the parse logic deterministically without
        installing borsapy. When fetcher is None, the default path
        lazily imports borsapy and calls the Ticker methods above.

    Banks get ``financial_group='UFRS'`` automatically via the shared
    BANK_TICKERS set; matches fetch_raw_v9.
    """
    if fetcher is None:
        try:
            import borsapy as bp  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "borsapy not installed. Install it on the ingest host "
                "(pip install borsapy) or pass fetcher=... for testing."
            ) from e

        # Respect data/providers.py's bank list and ticker cleaning.
        # Soft-import so a broken providers import doesn't block ingest.
        try:
            from data.providers import is_bank as _is_bank
        except Exception:
            def _is_bank(tc: str) -> bool:  # type: ignore
                return False

        def _real_fetch(sym: str) -> dict:
            tc = sym.upper().replace(".IS", "").replace(".E", "")
            tk = bp.Ticker(tc)
            fg = "UFRS" if _is_bank(tc) else None
            # last_n=40 comfortably covers 10 years × 4 quarters even
            # when a few older quarters are missing in borsapy.
            return {
                "income":   tk.get_income_stmt(quarterly=True, financial_group=fg, last_n=40),
                "balance":  tk.get_balance_sheet(quarterly=True, financial_group=fg, last_n=40),
                "cashflow": tk.get_cashflow(quarterly=True, financial_group=fg, last_n=40),
            }

        fetcher = _real_fetch

    data = fetcher(symbol)
    income = data.get("income")
    balance = data.get("balance")
    cashflow = data.get("cashflow")

    # Collect all distinct period_end columns across the three statements.
    # Columns are usually pandas Timestamps or ISO strings.
    periods: set[date] = set()
    for df in (income, balance, cashflow):
        if df is None:
            continue
        try:
            empty = df.empty
        except AttributeError:
            continue
        if empty:
            continue
        for col in df.columns:
            d = _coerce_date(col)
            if d is not None:
                periods.add(d)

    # Restrict to the requested window
    periods_sorted = sorted(p for p in periods if from_date <= p <= to_date)

    filings: list[dict] = []
    for period in periods_sorted:
        revenue      = _pick_kap(income,  _KAP_NAMES["revenue"], period)
        net_income   = _pick_kap(income,  _KAP_NAMES["net_income"], period)
        equity       = _pick_kap(balance, _KAP_NAMES["equity"], period)
        total_debt   = _extract_total_debt(balance, period)

        roe: Optional[float] = None
        if net_income is not None and equity and equity != 0:
            roe = round(net_income / equity, 6)

        debt_to_equity: Optional[float] = None
        if total_debt is not None and equity and equity != 0:
            debt_to_equity = round(total_debt / equity, 6)

        filings.append({
            "period_end": period,
            # Conservative filed_at estimate: +60 days after period end.
            # Later than truth is PIT-safe; earlier than truth would be a
            # look-ahead bug. We explicitly err on the side of later.
            "filed_at": period + timedelta(days=60),
            "metrics": {
                "revenue": revenue,
                "net_income": net_income,
                "roe": roe,
                "debt_to_equity": debt_to_equity,
            },
        })

    return filings


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


# ================================================================
# KAP line-name dictionary (Phase 4 FAZ 4.0.1)
# ================================================================
# Matches data/providers.py IS_MAP / BS_MAP so the backfill speaks the
# same Turkish KAP vocabulary as the live-trading fetch path. Each
# entry is a list of candidate row-name strings; first match wins.
# Partial (substring) matching is handled by _pick_kap as a fallback.

_KAP_NAMES: dict[str, list[str]] = {
    "revenue": ["Satış Gelirleri", "Hasılat"],
    "net_income": [
        "DÖNEM KARI (ZARARI)",
        "SÜRDÜRÜLEN FAALİYETLER DÖNEM KARI/ZARARI",
        "Net Dönem Karı/Zararı",
    ],
    "equity": [
        "Ana Ortaklığa Ait Özkaynaklar",
        "Özkaynaklar",
        "TOPLAM ÖZKAYNAKLAR",
    ],
    # Debt is composed from long- and short-term financial liabilities;
    # we sum them in _extract_total_debt.
    "long_term_debt": [
        "Uzun Vadeli Finansal Borçlar",
        "Uzun Vadeli Yükümlülükler - Finansal Borçlar",
    ],
    "short_term_debt": [
        "Kısa Vadeli Finansal Borçlar",
        "Kısa Vadeli Yükümlülükler - Finansal Borçlar",
    ],
    # Fallback if only a total-debt line is available
    "total_liabilities_long": ["Uzun Vadeli Yükümlülükler"],
    "total_liabilities_short": ["Kısa Vadeli Yükümlülükler"],
}


def _norm_name(s) -> str:
    """Whitespace + nbsp normalization. Matches data/providers._norm."""
    import re as _re
    if not isinstance(s, str):
        return ""
    return _re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _pick_kap(df, names: list[str], period: date) -> Optional[float]:
    """Select a value from a period-columned KAP DataFrame by (name, date).

    Mirrors ``data/providers._pick`` but keys the column by period_end
    date rather than positional offset. Returns float or None.
    - Exact row-name match first (case-insensitive, whitespace-normalized).
    - Substring fallback second (catches minor KAP re-phrasings).
    """
    if df is None:
        return None
    try:
        if df.empty:
            return None
    except AttributeError:
        return None

    # Find the column matching the requested period
    target_col = None
    for col in df.columns:
        if _coerce_date(col) == period:
            target_col = col
            break
    if target_col is None:
        return None

    # Row-name match
    target_names = [_norm_name(n).lower() for n in names]

    # Exact
    for idx in df.index:
        if _norm_name(idx).lower() in target_names:
            try:
                v = df.loc[idx, target_col]
                if hasattr(v, "iloc"):  # duplicate-index Series -> take first
                    v = v.iloc[0]
                return float(v)
            except (TypeError, ValueError, KeyError):
                continue

    # Partial
    for name_norm in target_names:
        for idx in df.index:
            if name_norm in _norm_name(idx).lower():
                try:
                    v = df.loc[idx, target_col]
                    if hasattr(v, "iloc"):
                        v = v.iloc[0]
                    return float(v)
                except (TypeError, ValueError, KeyError):
                    continue
    return None


def _extract_total_debt(balance_df, period: date) -> Optional[float]:
    """Compute total debt from long + short term financial borrowings.

    Priority:
      1. If both LT and ST financial-debt lines are present, sum them.
      2. Otherwise return None (don't fall back to total liabilities --
         that inflates D/E with non-interest-bearing items like trade
         payables and gives a misleading leverage reading).

    Phase 4 follow-up could add more granular extraction (e.g. long
    term provisions vs borrowings), but for the calibration Phase 4.7
    consumes, LT+ST financial borrowings / equity is the canonical D/E.
    """
    lt = _pick_kap(balance_df, _KAP_NAMES["long_term_debt"], period)
    st = _pick_kap(balance_df, _KAP_NAMES["short_term_debt"], period)
    if lt is None and st is None:
        return None
    return (lt or 0.0) + (st or 0.0)


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

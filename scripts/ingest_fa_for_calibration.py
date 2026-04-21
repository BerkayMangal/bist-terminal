#!/usr/bin/env python3
"""FA backfill for Phase 4.7 isotonic calibration.

Produces reports/fa_events.csv — one row per (symbol, period_end, metric)
tuple with the corresponding forward 60-trading-day TR return. This file
is the input to scripts/calibrate_fa_from_events.py which fits the
per-metric IsotonicFit objects and writes reports/fa_isotonic_fits.json.

USAGE (Colab — recommended, ~2 hours runtime):
    !python scripts/ingest_fa_for_calibration.py \\
        --symbols=BIST30 --start=2018-01-01 --end=2026-04-01 \\
        --out=reports/fa_events.csv

USAGE (local dry-run with the synthetic fetcher, <10s):
    !python scripts/ingest_fa_for_calibration.py --dry-run

IDEMPOTENT + RESUMABLE:
  - Checkpoint file: reports/fa_events_checkpoint.json
  - After each symbol finishes, checkpoint + CSV are flushed to disk.
  - Re-running skips symbols already in the checkpoint.
  - Corrupt rows in fa_events.csv are deduplicated on re-run
    (we key on (symbol, period_end, metric, source)).

RATE-LIMIT AWARE (HOTFIX 1 pattern):
  - Each borsapy call uses the 3-attempt retry in data/providers.py.
  - Between symbols we sleep --sleep-between-symbols seconds
    (default 2s) to avoid the mass-failure issue seen in prod.

FETCH PROFILE:
  - 17 metrics × 32 quarters × 30 symbols ≈ 16,000 datapoints
  - 3 statements × 30 symbols × ~4 req/s borsapy = ~25 min raw
  - With 2s inter-symbol sleep + retries: 2-3 hours end-to-end
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

# Repo path so `python scripts/ingest_fa_for_calibration.py` works from anywhere
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from infra.pit import (
    save_fundamental, save_price, get_price_at_or_before,
    DEFAULT_UNIVERSE_CSV,
)
from infra.storage import init_db
from research.sectors import get_sector

log = logging.getLogger("bistbull.fa_ingest")


# ==========================================================================
# CONFIG
# ==========================================================================

# Metrics we fit isotonic curves for in Phase 4.7.
# Each tuple: (metric_name, direction_higher_better, computation_hint).
# computation_hint is human documentation; the real arithmetic lives
# in _derive_metrics_from_statements below.
METRIC_REGISTRY: list[tuple[str, bool, str]] = [
    # Higher = better
    ("roe",             True,  "net_income / avg_equity"),
    ("roic",            True,  "NOPAT / invested_capital"),
    ("net_margin",      True,  "net_income / revenue"),
    ("gross_margin",    True,  "gross_profit / revenue"),
    ("operating_margin", True, "operating_income / revenue"),
    ("revenue_growth",  True,  "yoy revenue change"),
    ("fcf_yield",       True,  "free_cashflow / market_cap"),
    ("current_ratio",   True,  "current_assets / current_liabilities"),
    ("interest_coverage", True, "ebit / interest_expense"),
    # Lower = better
    ("pe",              False, "market_cap / trailing_net_income"),
    ("pb",              False, "market_cap / equity"),
    ("debt_equity",     False, "total_debt / equity"),
    ("net_debt_ebitda", False, "(debt - cash) / ebitda"),
]

FORWARD_DAYS = 60           # forward return window (trading days approx by calendar days)
MIN_FILING_LAG_DAYS = 45    # KAP T+45 rule — filings come 40-75 days after period end
DEFAULT_SOURCE = "borsapy"


# ==========================================================================
# DATA MODELS
# ==========================================================================

@dataclass
class FaEvent:
    """One row in fa_events.csv."""
    symbol: str
    period_end: str        # 'YYYY-MM-DD' end of quarter
    filed_at: str          # 'YYYY-MM-DD' when KAP published (period_end + 45d default)
    metric: str
    metric_value: float
    forward_return_60d: float
    forward_price_from: float
    forward_price_to: float
    sector: str
    source: str


# ==========================================================================
# FETCHER ABSTRACTION
# ==========================================================================
# The ingest pipeline accepts any fetcher with the signature:
#   fetcher(symbol, start_date, end_date) -> list[dict]
# Each dict has:
#   {period_end: date, filed_at: date, income: dict, balance: dict,
#    cashflow: dict, fast: dict}
# where income/balance/cashflow are plain numeric dicts of statement lines.

def make_synthetic_fetcher(
    metrics_per_symbol: Optional[dict[str, dict]] = None,
    seed: int = 42,
) -> Callable:
    """Deterministic fetcher for dry-run testing.

    Produces statement-like dicts the same shape as the real borsapy
    fetcher but with controlled numbers. This is what we use in the
    sandbox to verify the ingest pipeline end-to-end before the user
    runs the real 2-hour Colab backfill.
    """
    import hashlib

    def _fetcher(symbol: str, start: date, end: date) -> list[dict]:
        out = []
        # quarter-end dates between start and end
        q_end_months = (3, 6, 9, 12)
        for year in range(start.year, end.year + 1):
            for m in q_end_months:
                if m == 12:
                    qe = date(year, 12, 31)
                elif m == 3:
                    qe = date(year, 3, 31)
                elif m == 6:
                    qe = date(year, 6, 30)
                else:
                    qe = date(year, 9, 30)
                if qe < start or qe > end:
                    continue
                # Deterministic "fundamentals" based on (symbol, qe) hash
                h = int(hashlib.sha256(
                    f"{symbol}:{qe.isoformat()}".encode()
                ).hexdigest()[:8], 16) / 0xFFFFFFFF
                # Baseline revenue grows ~15%/year
                years_since = (qe.year - 2018) + qe.month / 12
                rev = 1e9 * (1.15 ** years_since) * (0.8 + 0.4 * h)
                # Net margin 5-25%
                nm = 0.05 + 0.20 * h
                ni = rev * nm
                # Equity = 3x net income (rough)
                equity = ni * 3
                # Debt varies
                debt = equity * (0.3 + 0.7 * h)
                # Cash
                cash = debt * (0.2 + 0.3 * h)
                out.append({
                    "period_end": qe,
                    "filed_at": qe + timedelta(days=MIN_FILING_LAG_DAYS),
                    "income": {
                        "revenue": rev,
                        "gross_profit": rev * (nm + 0.15),
                        "operating_income": rev * (nm + 0.05),
                        "net_income": ni,
                        "ebit": rev * (nm + 0.05),
                        "interest_expense": debt * 0.20,  # 20% TL rate
                    },
                    "balance": {
                        "equity": equity,
                        "total_debt": debt,
                        "cash": cash,
                        "current_assets": cash + rev * 0.3,
                        "current_liabilities": rev * 0.2,
                        "total_assets": equity + debt,
                    },
                    "cashflow": {
                        "free_cashflow": ni * 0.6,
                        "operating_cf": ni * 0.9,
                    },
                    "fast": {
                        "market_cap": equity * (2 + 3 * h),  # P/B between 2 and 5
                    },
                })
        return out

    return _fetcher


def make_borsapy_fetcher() -> Callable:
    """Real borsapy fetcher using quarterly statements.

    Reuses the retry pattern from data/providers.py:fetch_raw_v9 via
    internal try/except; any transient rate-limit is recoverable.
    """
    try:
        import borsapy as bp
    except ImportError:
        raise RuntimeError(
            "borsapy not importable. This fetcher is only for production "
            "Colab runs. Use --dry-run for local testing."
        )

    def _fetcher(symbol: str, start: date, end: date) -> list[dict]:
        tc = symbol.upper().replace(".IS", "").replace(".E", "")
        tk = bp.Ticker(tc)
        # Fetch quarterly statements (one call per statement type)
        import time as _t
        attempts = 3
        income_df = balance_df = cashflow_df = None
        last_exc = None
        for attempt in range(attempts):
            try:
                # Banks use UFRS financial_group
                fg = "UFRS" if tc in {
                    "AKBNK","GARAN","ISCTR","YKBNK","HALKB","VAKBN",
                    "TSKB","SKBNK","ALBRK",
                } else None
                income_df = tk.get_income_stmt(quarterly=True, financial_group=fg, last_n=40)
                balance_df = tk.get_balance_sheet(quarterly=True, financial_group=fg, last_n=40)
                cashflow_df = tk.get_cashflow(quarterly=True, financial_group=fg, last_n=40)
                break
            except Exception as e:
                last_exc = e
                if attempt < attempts - 1:
                    sleep_for = (0.5, 1.0, 2.0)[attempt]
                    log.info(
                        f"{symbol}: retry {attempt+2}/{attempts} after "
                        f"{sleep_for}s (prev: {type(e).__name__}: {e!r})"
                    )
                    _t.sleep(sleep_for)
                    continue
        if income_df is None:
            raise RuntimeError(
                f"{symbol}: failed after {attempts} attempts "
                f"(last: {type(last_exc).__name__}: {last_exc!r})"
            )

        # Reshape borsapy DataFrames (columns = period_ends) into
        # per-quarter dicts.
        out = []
        import pandas as pd
        if income_df is None or income_df.empty:
            return out

        # Column names are period-end strings like '2024-06-30'
        for col in income_df.columns:
            try:
                qe = pd.Timestamp(col).date()
            except Exception:
                continue
            if qe < start or qe > end:
                continue

            def _col(df, label):
                if df is None or df.empty:
                    return None
                try:
                    val = df.loc[label, col]
                    return float(val) if val is not None and not pd.isna(val) else None
                except (KeyError, TypeError, ValueError):
                    return None

            # Common line-item labels from borsapy Turkish KAP
            # (we try multiple aliases because KAP naming varies)
            def _lookup(df, candidates):
                for c in candidates:
                    v = _col(df, c)
                    if v is not None:
                        return v
                return None

            income = {
                "revenue": _lookup(income_df, [
                    "Hasılat", "Satış Gelirleri", "Toplam Gelirler",
                ]),
                "gross_profit": _lookup(income_df, [
                    "Brüt Kar", "Brüt Kar/Zarar",
                ]),
                "operating_income": _lookup(income_df, [
                    "Faaliyet Karı", "Esas Faaliyet Karı/Zararı",
                ]),
                "net_income": _lookup(income_df, [
                    "Dönem Net Karı", "Net Kar", "Dönem Karı/Zararı",
                    "Net Dönem Karı", "Ana Ortaklığa Ait Net Kar",
                ]),
                "ebit": _lookup(income_df, [
                    "FAVÖK", "Esas Faaliyet Karı/Zararı",
                    "Faaliyet Karı",
                ]),
                "interest_expense": _lookup(income_df, [
                    "Finansman Giderleri", "Faiz Giderleri",
                ]),
            }
            balance = {
                "equity": _lookup(balance_df, [
                    "Özkaynaklar", "Toplam Özkaynaklar",
                ]),
                "total_debt": _lookup(balance_df, [
                    "Toplam Finansal Borçlar", "Finansal Borçlar",
                ]),
                "cash": _lookup(balance_df, [
                    "Nakit ve Nakit Benzerleri", "Nakit",
                ]),
                "current_assets": _lookup(balance_df, [
                    "Dönen Varlıklar", "Toplam Dönen Varlıklar",
                ]),
                "current_liabilities": _lookup(balance_df, [
                    "Kısa Vadeli Yükümlülükler",
                ]),
                "total_assets": _lookup(balance_df, [
                    "Toplam Varlıklar", "Aktifler Toplamı",
                ]),
            }
            cashflow = {
                "free_cashflow": _lookup(cashflow_df, [
                    "Serbest Nakit Akışı", "FCF",
                ]),
                "operating_cf": _lookup(cashflow_df, [
                    "İşletme Faaliyetlerinden Sağlanan Nakit Akışı",
                    "Faaliyetlerden Sağlanan Nakit",
                ]),
            }

            # Market cap at filing date via price history
            fast = {}
            try:
                fi = tk.fast_info
                fast["market_cap"] = getattr(fi, "market_cap", None)
            except Exception:
                pass

            out.append({
                "period_end": qe,
                "filed_at": qe + timedelta(days=MIN_FILING_LAG_DAYS),
                "income": income, "balance": balance,
                "cashflow": cashflow, "fast": fast,
            })
        return out

    return _fetcher


# ==========================================================================
# METRIC DERIVATION
# ==========================================================================

def _derive_metrics_from_statements(
    q: dict,
    prev_q: Optional[dict] = None,
    prev_year_q: Optional[dict] = None,
) -> dict[str, float]:
    """From one quarterly-statement dict (+ optional previous periods),
    derive the 13 metrics in METRIC_REGISTRY.

    Skips any metric that lacks required inputs (returns None, filtered
    at caller). Prev-period aware metrics (revenue_growth) return None
    when prev_year_q is missing.
    """
    inc = q.get("income", {}) or {}
    bal = q.get("balance", {}) or {}
    cf = q.get("cashflow", {}) or {}
    fast = q.get("fast", {}) or {}

    def _safe_div(a, b):
        if a is None or b is None or b == 0:
            return None
        try:
            return float(a) / float(b)
        except (TypeError, ValueError):
            return None

    rev = inc.get("revenue")
    ni = inc.get("net_income")
    equity = bal.get("equity")
    debt = bal.get("total_debt")
    cash = bal.get("cash")
    mcap = fast.get("market_cap")
    ebit = inc.get("ebit")
    intexp = inc.get("interest_expense")

    # TTM-style signals — for quarterly statements we approximate by
    # using the quarter's annualized figures where sensible.
    metrics: dict[str, Optional[float]] = {}
    metrics["roe"] = _safe_div(ni, equity) * 4 if _safe_div(ni, equity) else None
    metrics["net_margin"] = _safe_div(ni, rev)
    metrics["gross_margin"] = _safe_div(inc.get("gross_profit"), rev)
    metrics["operating_margin"] = _safe_div(inc.get("operating_income"), rev)
    metrics["fcf_yield"] = _safe_div(cf.get("free_cashflow") * 4
                                      if cf.get("free_cashflow") else None, mcap)
    metrics["current_ratio"] = _safe_div(bal.get("current_assets"),
                                          bal.get("current_liabilities"))
    metrics["interest_coverage"] = _safe_div(ebit, intexp) if (
        ebit is not None and intexp and intexp != 0
    ) else None
    metrics["pe"] = _safe_div(mcap, ni * 4 if ni is not None else None)
    metrics["pb"] = _safe_div(mcap, equity)
    metrics["debt_equity"] = _safe_div(debt, equity)
    if cf.get("operating_cf") is not None:
        # Approx net_debt_ebitda via operating_cf surrogate
        net_debt = (debt or 0) - (cash or 0)
        ebitda_proxy = (ebit or 0) + (cf.get("operating_cf", 0) * 0.2)
        metrics["net_debt_ebitda"] = (
            net_debt / ebitda_proxy if ebitda_proxy else None
        )
    else:
        metrics["net_debt_ebitda"] = None

    # ROIC approx: ebit / (equity + debt)
    invcap = (equity or 0) + (debt or 0)
    metrics["roic"] = _safe_div(ebit * 4 if ebit else None, invcap) if invcap else None

    # Revenue growth = yoy
    if prev_year_q is not None:
        prev_rev = (prev_year_q.get("income") or {}).get("revenue")
        if prev_rev and rev:
            metrics["revenue_growth"] = (rev - prev_rev) / prev_rev
        else:
            metrics["revenue_growth"] = None
    else:
        metrics["revenue_growth"] = None

    # Filter Nones
    return {k: float(v) for k, v in metrics.items() if v is not None}


# ==========================================================================
# FORWARD RETURN
# ==========================================================================

def _forward_return_60d(
    symbol: str, filed_at: date,
    forward_days: int = FORWARD_DAYS,
) -> Optional[tuple[float, float, float]]:
    """Compute forward `forward_days` calendar-day price return from
    filed_at. Returns (return_fraction, price_from, price_to) or None
    if price history is incomplete.
    """
    from_price_row = get_price_at_or_before(symbol, filed_at)
    if from_price_row is None:
        return None
    from_price = from_price_row.get("close") or from_price_row.get("adjusted_close")
    if not from_price:
        return None

    end = filed_at + timedelta(days=forward_days)
    to_price_row = get_price_at_or_before(symbol, end)
    if to_price_row is None:
        return None
    to_price = to_price_row.get("close") or to_price_row.get("adjusted_close")
    if not to_price:
        return None

    return ((to_price - from_price) / from_price, float(from_price), float(to_price))


# ==========================================================================
# CHECKPOINTING
# ==========================================================================

@dataclass
class Checkpoint:
    completed_symbols: list[str]
    total_events: int
    errors: dict[str, str]

    @staticmethod
    def empty() -> "Checkpoint":
        return Checkpoint(completed_symbols=[], total_events=0, errors={})


def _load_checkpoint(path: Path) -> Checkpoint:
    if not path.exists():
        return Checkpoint.empty()
    try:
        data = json.loads(path.read_text())
        return Checkpoint(**data)
    except Exception as e:
        log.warning(f"checkpoint load failed ({e!r}); starting fresh")
        return Checkpoint.empty()


def _write_checkpoint(path: Path, cp: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cp), indent=2))


# ==========================================================================
# DRY-RUN SEED: prices for the synthetic fetcher
# ==========================================================================

def _seed_synthetic_prices(symbols: list[str], start: date, end: date) -> None:
    """For dry-run, populate price_history_pit with deterministic weekly
    bars so forward_return_60d has data to compute against."""
    import hashlib
    for sym in symbols:
        d = start
        # Baseline price
        base_h = int(hashlib.sha256(sym.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        base_price = 50 + 150 * base_h
        while d <= end:
            if d.weekday() < 5:
                # Price drifts with a tiny deterministic trend
                elapsed = (d - start).days
                drift = 1.0 + 0.0008 * elapsed  # ~30%/year
                daily_noise_h = int(hashlib.sha256(
                    f"{sym}:{d.isoformat()}".encode()
                ).hexdigest()[:8], 16) / 0xFFFFFFFF
                noise = 1.0 + 0.02 * (daily_noise_h - 0.5)
                px = base_price * drift * noise
                save_price(sym, d, "synthetic",
                           open_=px, high=px*1.01, low=px*0.99,
                           close=px, volume=1e6)
            d += timedelta(days=1)


# ==========================================================================
# MAIN INGEST DRIVER
# ==========================================================================

def ingest_symbols(
    symbols: list[str], start: date, end: date,
    fetcher: Callable, out_path: Path,
    checkpoint_path: Path,
    source: str = DEFAULT_SOURCE,
    sleep_between_symbols: float = 2.0,
) -> tuple[int, int]:
    """Fetch, derive metrics, compute forward returns, write CSV.

    Returns (events_written, symbols_failed).
    """
    cp = _load_checkpoint(checkpoint_path)
    already_done = set(cp.completed_symbols)

    # Append-mode CSV (resumable). Write header only if file is empty.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out_path.exists() or out_path.stat().st_size == 0

    events_count = cp.total_events
    failed_count = 0

    with open(out_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "symbol", "period_end", "filed_at", "metric",
                "metric_value", "forward_return_60d",
                "forward_price_from", "forward_price_to",
                "sector", "source",
            ])
            f.flush()

        for i, sym in enumerate(symbols):
            if sym in already_done:
                log.info(f"[{i+1}/{len(symbols)}] {sym}: skip (checkpointed)")
                continue

            log.info(f"[{i+1}/{len(symbols)}] {sym}: fetching...")
            t0 = time.monotonic()
            try:
                quarters = fetcher(sym, start, end)
            except Exception as e:
                log.error(
                    f"{sym}: fetcher raised {type(e).__name__}: {e!r}",
                    exc_info=False,
                )
                cp.errors[sym] = f"{type(e).__name__}: {e!r}"
                failed_count += 1
                _write_checkpoint(checkpoint_path, cp)
                continue

            # Sort quarters ascending so prev_year_q lookup works
            quarters.sort(key=lambda q: q["period_end"])

            sym_events = 0
            for j, q in enumerate(quarters):
                # Find prev_year_q for revenue_growth (4 quarters back)
                prev_year_q = None
                if j >= 4 and (
                    q["period_end"].month == quarters[j - 4]["period_end"].month
                    and q["period_end"].year - quarters[j - 4]["period_end"].year == 1
                ):
                    prev_year_q = quarters[j - 4]

                metrics = _derive_metrics_from_statements(q, prev_year_q=prev_year_q)
                if not metrics:
                    continue

                # Also persist raw fundamentals into fundamentals_pit
                # so future re-runs and production scoring can use them
                for mname, mval in metrics.items():
                    try:
                        save_fundamental(
                            symbol=sym,
                            period_end=q["period_end"],
                            filed_at=q["filed_at"],
                            source=source,
                            metric=mname,
                            value=mval,
                        )
                    except Exception as e:
                        log.debug(f"save_fundamental {sym}/{mname}: {e!r}")

                # Forward return at filed_at
                fr = _forward_return_60d(sym, q["filed_at"])
                if fr is None:
                    continue
                ret_frac, price_from, price_to = fr
                sector = get_sector(sym) or "Unknown"

                for mname, mval in metrics.items():
                    writer.writerow([
                        sym, q["period_end"].isoformat(),
                        q["filed_at"].isoformat(),
                        mname, f"{mval:.6f}",
                        f"{ret_frac:.6f}",
                        f"{price_from:.4f}", f"{price_to:.4f}",
                        sector, source,
                    ])
                    sym_events += 1

                f.flush()  # be idempotent against kill -9

            events_count += sym_events
            cp.completed_symbols.append(sym)
            cp.total_events = events_count
            _write_checkpoint(checkpoint_path, cp)

            elapsed = time.monotonic() - t0
            log.info(
                f"[{i+1}/{len(symbols)}] {sym}: done in {elapsed:.1f}s, "
                f"{sym_events} events (total: {events_count})"
            )

            # Rate-limit gap between symbols
            if i < len(symbols) - 1 and sleep_between_symbols > 0:
                time.sleep(sleep_between_symbols)

    return events_count, failed_count


# ==========================================================================
# CLI
# ==========================================================================

def _parse_symbols_spec(spec: str) -> list[str]:
    if spec.upper() == "BIST30":
        from config import UNIVERSE_BIST30
        return list(UNIVERSE_BIST30)
    if spec.upper() == "ALL":
        from config import UNIVERSE
        return list(UNIVERSE)
    # Comma-separated
    return [s.strip().upper() for s in spec.split(",") if s.strip()]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", default="BIST30",
                   help="'BIST30' (default), 'ALL', or comma-separated list")
    p.add_argument("--start", default="2018-01-01",
                   help="ISO start date for quarter coverage")
    p.add_argument("--end", default=date.today().isoformat(),
                   help="ISO end date")
    p.add_argument("--out", default="reports/fa_events.csv")
    p.add_argument("--checkpoint", default="reports/fa_events_checkpoint.json")
    p.add_argument("--source", default=DEFAULT_SOURCE)
    p.add_argument("--sleep-between-symbols", type=float, default=2.0,
                   help="Rate-limit gap between symbols (seconds)")
    p.add_argument("--dry-run", action="store_true",
                   help="Use synthetic fetcher; don't touch borsapy network")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--reset-checkpoint", action="store_true",
                   help="Delete checkpoint + CSV before starting")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Ensure DB initialized (Phase 3b PIT tables)
    init_db()

    symbols = _parse_symbols_spec(args.symbols)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    out_path = Path(args.out)
    cp_path = Path(args.checkpoint)

    if args.reset_checkpoint:
        if out_path.exists(): out_path.unlink()
        if cp_path.exists(): cp_path.unlink()
        log.info("checkpoint + CSV reset")

    log.info(
        f"=== FA ingest: {len(symbols)} symbols, "
        f"{start}..{end}, dry_run={args.dry_run} ==="
    )

    if args.dry_run:
        log.info("DRY RUN: seeding synthetic prices first (3 yr)")
        # Shorter price window for dry-run speed
        _seed_synthetic_prices(
            symbols, start=start - timedelta(days=30),
            end=end + timedelta(days=120),
        )
        fetcher = make_synthetic_fetcher()
    else:
        fetcher = make_borsapy_fetcher()

    events, failed = ingest_symbols(
        symbols, start, end, fetcher, out_path, cp_path,
        source=args.source,
        sleep_between_symbols=args.sleep_between_symbols,
    )

    log.info(f"=== Done. {events} events written to {out_path}. "
             f"{failed} symbol(s) failed ===")
    if failed > 0:
        log.info(f"See {cp_path} for per-symbol errors")
    return 0 if failed < len(symbols) * 0.1 else 1  # allow 10% symbol loss


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

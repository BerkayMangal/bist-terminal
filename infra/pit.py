# ================================================================
# BISTBULL TERMINAL -- POINT-IN-TIME QUERIES (Phase 2)
# infra/pit.py
#
# Survivorship-free and look-ahead-free lookups:
#   get_fundamentals_at(symbol, as_of)  -- fundamentals known by as_of
#   get_universe_at(universe, as_of)    -- universe membership on as_of
#   save_fundamental(...)               -- ingestion-side upsert
#   load_universe_history_csv(path)     -- seed loader
#
# All dates are ISO-8601 strings ('YYYY-MM-DD') at the SQL boundary.
# Accept date / datetime / str at the Python boundary for convenience.
# ================================================================

from __future__ import annotations

import csv
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

from infra.storage import _get_conn

log = logging.getLogger("bistbull.pit")

DateLike = Union[str, date, datetime]

# Phase 4 FAZ 4.3.5: default data paths resolved at module-load time
# so callers (tests, scripts, ad-hoc Python sessions) don't have to
# know the repo layout. Mirrors the infra/migrations/__init__.py
# approach of using Path(__file__).resolve() so os.chdir doesn't
# silently invalidate the path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UNIVERSE_CSV = _REPO_ROOT / "data" / "universe_history.csv"


def _iso(d: DateLike) -> str:
    """Normalize date input to ISO-8601 YYYY-MM-DD."""
    if isinstance(d, str):
        # Trust the caller, but slice to the date portion if a full ts slipped in.
        return d[:10]
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    raise TypeError(f"expected date/datetime/str, got {type(d).__name__}")


def save_fundamental(
    symbol: str,
    period_end: DateLike,
    filed_at: DateLike,
    source: str,
    metric: str,
    value: Optional[float] = None,
    value_text: Optional[str] = None,
) -> None:
    """Upsert a single fundamental row. PK = (symbol, period_end, metric, source).

    A single period/metric can have rows from multiple sources; the PIT
    reader (get_fundamentals_at) picks the most-recently-filed row regardless
    of source. Phase 3 audit will compare cross-source.
    """
    conn = _get_conn()
    conn.execute(
        """INSERT INTO fundamentals_pit
             (symbol, period_end, filed_at, source, metric, value, value_text)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol, period_end, metric, source) DO UPDATE SET
             filed_at   = excluded.filed_at,
             value      = excluded.value,
             value_text = excluded.value_text""",
        (symbol.upper(), _iso(period_end), _iso(filed_at), source, metric,
         value, value_text),
    )
    conn.commit()


def get_fundamentals_at(symbol: str, as_of: DateLike) -> dict:
    """Return the most recent value for each metric, as of `as_of`.

    Definition of "as of":
      - Only rows where filed_at <= as_of are considered (no look-ahead).
      - For each metric, pick the row with the LATEST period_end that is
        also filed_at <= as_of. Within a tied period_end, break by
        filed_at DESC, then source alphabetical.
      - Returns {metric: {value, value_text, period_end, filed_at, source}}.

    Survivorship-free:
      - Works for symbols that have since been delisted -- membership is
        orthogonal; this only cares about what filings were public by as_of.

    Empty dict if the symbol has no filings on or before as_of.
    """
    as_of_iso = _iso(as_of)
    conn = _get_conn()
    # Window function pick-one-per-metric query. SQLite has ROW_NUMBER since 3.25.
    rows = conn.execute(
        """
        WITH ranked AS (
          SELECT
            symbol, period_end, filed_at, source, metric, value, value_text,
            ROW_NUMBER() OVER (
              PARTITION BY metric
              ORDER BY period_end DESC, filed_at DESC, source ASC
            ) AS rn
          FROM fundamentals_pit
          WHERE symbol = ? AND filed_at <= ?
        )
        SELECT metric, value, value_text, period_end, filed_at, source
        FROM ranked
        WHERE rn = 1
        """,
        (symbol.upper(), as_of_iso),
    ).fetchall()
    return {
        r["metric"]: {
            "value": r["value"],
            "value_text": r["value_text"],
            "period_end": r["period_end"],
            "filed_at": r["filed_at"],
            "source": r["source"],
        }
        for r in rows
    }


def get_universe_at(universe_name: str, as_of: DateLike) -> list[str]:
    """Return the list of symbols in `universe_name` on `as_of` date.

    Membership semantics: a symbol is a member on date D if
        from_date <= D AND (to_date IS NULL OR D < to_date)
    That is, from_date inclusive, to_date exclusive -- matches index
    convention where the "removal" effective date is when the symbol
    is already gone.

    Returns symbols alphabetically sorted. Empty list if the universe
    is unknown or had no members on that date.
    """
    as_of_iso = _iso(as_of)
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT DISTINCT symbol
        FROM universe_history
        WHERE universe_name = ?
          AND from_date <= ?
          AND (to_date IS NULL OR ? < to_date)
        ORDER BY symbol
        """,
        (universe_name, as_of_iso, as_of_iso),
    ).fetchall()
    return [r["symbol"] for r in rows]


VALID_UNIVERSE_REASONS = {"approximate", "addition", "removal", "verified"}


def load_universe_history_csv(path: Optional[Union[str, Path]] = None) -> int:
    """Load a universe_history CSV into the DB. Returns rows inserted.

    CSV format (header required):
        universe_name,symbol,from_date,to_date,reason[,source_url]

    source_url column is optional for backwards compatibility with the
    Phase 2 seed. Phase 3 adds it; after migration 005 the DB has the
    source_url column always, but old CSVs without the column still load.

    Row-level validation (Phase 3 FAZ 3.1):
      - reason must be one of {approximate, addition, removal, verified}
      - If reason != 'approximate', source_url must be non-empty (enforces
        the "audit rows must have a source" contract).
      - Empty to_date -> NULL (still a member).
      - ON CONFLICT (PK) -> UPDATE to_date/reason/source_url. Idempotent
        re-seed.

    Phase 4 FAZ 4.3.5: path is now optional. When None, defaults to
    DEFAULT_UNIVERSE_CSV (resolved at module-load time from __file__,
    so os.chdir doesn't silently change what file we read -- this
    was the kin of the Phase 4.0.3 migrations bug for data loaders).
    """
    if path is None:
        path = DEFAULT_UNIVERSE_CSV
    path = Path(path)
    conn = _get_conn()
    inserted = 0
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"universe_name", "symbol", "from_date", "to_date", "reason"}
        fieldnames = set(reader.fieldnames or [])
        missing = required - fieldnames
        if missing:
            raise ValueError(f"{path}: missing columns: {sorted(missing)}")
        has_source_url = "source_url" in fieldnames

        for row in reader:
            universe = (row["universe_name"] or "").strip()
            symbol = (row["symbol"] or "").strip().upper()
            from_date = (row["from_date"] or "").strip()
            to_date_raw = (row["to_date"] or "").strip()
            to_date = to_date_raw if to_date_raw else None
            reason = (row["reason"] or "approximate").strip() or "approximate"
            source_url_raw = (row.get("source_url", "") or "").strip() if has_source_url else ""
            source_url = source_url_raw if source_url_raw else None

            if not (universe and symbol and from_date):
                continue

            if reason not in VALID_UNIVERSE_REASONS:
                raise ValueError(
                    f"{path}: row {reader.line_num}: invalid reason {reason!r}; "
                    f"allowed: {sorted(VALID_UNIVERSE_REASONS)}"
                )
            if reason != "approximate" and not source_url:
                raise ValueError(
                    f"{path}: row {reader.line_num}: reason={reason!r} "
                    f"requires source_url (audit rows must cite a source)"
                )

            conn.execute(
                """INSERT INTO universe_history
                     (universe_name, symbol, from_date, to_date, reason, source_url)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(universe_name, symbol, from_date) DO UPDATE SET
                     to_date    = excluded.to_date,
                     reason     = excluded.reason,
                     source_url = excluded.source_url""",
                (universe, symbol, from_date, to_date, reason, source_url),
            )
            inserted += 1
    conn.commit()
    log.info(f"load_universe_history_csv({path}): {inserted} rows")
    return inserted


# ================================================================
# Phase 3 FAZ 3.x additions
# ================================================================

SOURCE_PRIORITY_DEFAULT = ("kap", "borsapy", "synthetic", "manual")


def get_fundamentals_at_preferred(
    symbol: str,
    as_of: DateLike,
    source_priority: Optional[tuple[str, ...]] = None,
) -> dict:
    """Like get_fundamentals_at, but for each (metric, period_end) pair picks the
    row whose source appears earliest in source_priority.

    Phase 1 reviewer spec (S4):
      Default priority = ('kap', 'borsapy', 'synthetic', 'manual').
      Sources not in the priority list are ignored.

    Semantic vs get_fundamentals_at:
      - get_fundamentals_at returns whatever row wins the straight
        period_end/filed_at/source-alpha tie-break. Good for debug / seeing
        what's actually in the DB.
      - get_fundamentals_at_preferred enforces a caller-chosen precedence.
        This is what the labeler / validator should use so fundamentals
        from KAP win over borsapy win over synthetic, consistently.

    Returns the same dict shape:
        {metric: {value, value_text, period_end, filed_at, source}}
    """
    priority = source_priority or SOURCE_PRIORITY_DEFAULT
    # Sanity: priority items must be hashable strings
    if not all(isinstance(s, str) for s in priority):
        raise TypeError("source_priority must be a tuple of strings")

    as_of_iso = _iso(as_of)
    conn = _get_conn()

    # Build a CASE expression that maps source -> priority rank (lower is better).
    case_parts = ["CASE source"]
    for rank, src in enumerate(priority):
        case_parts.append(f"WHEN ? THEN {rank}")
    case_parts.append(f"ELSE {len(priority)} END")
    case_sql = " ".join(case_parts)

    # Window function pick-one-per-metric: order by period_end DESC first
    # (want the latest period), THEN by source priority, THEN by filed_at DESC.
    sql = f"""
        WITH filtered AS (
          SELECT symbol, period_end, filed_at, source, metric, value, value_text,
                 {case_sql} AS src_rank
          FROM fundamentals_pit
          WHERE symbol = ? AND filed_at <= ?
        ),
        ranked AS (
          SELECT *,
                 ROW_NUMBER() OVER (
                   PARTITION BY metric
                   ORDER BY period_end DESC, src_rank ASC, filed_at DESC
                 ) AS rn
          FROM filtered
          WHERE src_rank < ?
        )
        SELECT metric, value, value_text, period_end, filed_at, source
        FROM ranked WHERE rn = 1
    """
    params = list(priority) + [symbol.upper(), as_of_iso, len(priority)]
    rows = conn.execute(sql, params).fetchall()
    return {
        r["metric"]: {
            "value": r["value"],
            "value_text": r["value_text"],
            "period_end": r["period_end"],
            "filed_at": r["filed_at"],
            "source": r["source"],
        }
        for r in rows
    }


def save_price(
    symbol: str,
    trade_date: DateLike,
    source: str,
    open_: Optional[float] = None,
    high: Optional[float] = None,
    low: Optional[float] = None,
    close: Optional[float] = None,
    volume: Optional[float] = None,
    adjusted_close: Optional[float] = None,
) -> None:
    """Upsert a single daily OHLCV bar into price_history_pit.

    PK = (symbol, trade_date, source). Multiple sources can coexist; callers
    that want a specific source filter in the SELECT.
    """
    conn = _get_conn()
    conn.execute(
        """INSERT INTO price_history_pit
             (symbol, trade_date, source, open, high, low, close, volume, adjusted_close)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol, trade_date, source) DO UPDATE SET
             open = excluded.open, high = excluded.high, low = excluded.low,
             close = excluded.close, volume = excluded.volume,
             adjusted_close = excluded.adjusted_close""",
        (symbol.upper(), _iso(trade_date), source,
         open_, high, low, close, volume, adjusted_close),
    )
    conn.commit()


def get_prices(
    symbol: str,
    from_date: DateLike,
    to_date: DateLike,
    source: Optional[str] = None,
) -> list[dict]:
    """Fetch OHLCV bars for [from_date, to_date] inclusive.

    If source is None, returns rows from any source -- useful when you know
    there's only one source loaded. For multi-source DBs, pass the desired
    source explicitly to avoid duplicates.

    Returns list of dicts, sorted by trade_date ASC.
    """
    conn = _get_conn()
    if source:
        rows = conn.execute(
            """SELECT trade_date, open, high, low, close, volume, adjusted_close, source
               FROM price_history_pit
               WHERE symbol = ? AND trade_date BETWEEN ? AND ? AND source = ?
               ORDER BY trade_date ASC""",
            (symbol.upper(), _iso(from_date), _iso(to_date), source),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT trade_date, open, high, low, close, volume, adjusted_close, source
               FROM price_history_pit
               WHERE symbol = ? AND trade_date BETWEEN ? AND ?
               ORDER BY trade_date ASC""",
            (symbol.upper(), _iso(from_date), _iso(to_date)),
        ).fetchall()
    return [dict(r) for r in rows]


def get_price_at_or_before(
    symbol: str,
    as_of: DateLike,
    source: Optional[str] = None,
) -> Optional[dict]:
    """Return the most-recent bar with trade_date <= as_of. None if no data.

    Used by the labeler to convert 'as_of_date -> price' when as_of lands
    on a non-trading day.
    """
    conn = _get_conn()
    if source:
        row = conn.execute(
            """SELECT trade_date, open, high, low, close, volume, adjusted_close, source
               FROM price_history_pit
               WHERE symbol = ? AND trade_date <= ? AND source = ?
               ORDER BY trade_date DESC LIMIT 1""",
            (symbol.upper(), _iso(as_of), source),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT trade_date, open, high, low, close, volume, adjusted_close, source
               FROM price_history_pit
               WHERE symbol = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 1""",
            (symbol.upper(), _iso(as_of)),
        ).fetchone()
    return dict(row) if row else None

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


def load_universe_history_csv(path: Union[str, Path]) -> int:
    """Load a universe_history CSV into the DB. Returns rows inserted.

    CSV format (header required):
        universe_name,symbol,from_date,to_date,reason

    - Empty to_date cell is stored as NULL (still a member).
    - Duplicate rows (same PK triple) are UPDATEd -- lets you re-seed with
      updated to_date/reason without manual cleanup.
    """
    path = Path(path)
    conn = _get_conn()
    inserted = 0
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"universe_name", "symbol", "from_date", "to_date", "reason"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns: {sorted(missing)}")

        for row in reader:
            universe = (row["universe_name"] or "").strip()
            symbol = (row["symbol"] or "").strip().upper()
            from_date = (row["from_date"] or "").strip()
            to_date_raw = (row["to_date"] or "").strip()
            to_date = to_date_raw if to_date_raw else None
            reason = (row["reason"] or "approximate").strip() or "approximate"

            if not (universe and symbol and from_date):
                # Skip blank/malformed rows instead of raising -- CSV tools
                # often leave trailing empties.
                continue

            conn.execute(
                """INSERT INTO universe_history
                     (universe_name, symbol, from_date, to_date, reason)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(universe_name, symbol, from_date) DO UPDATE SET
                     to_date = excluded.to_date,
                     reason  = excluded.reason""",
                (universe, symbol, from_date, to_date, reason),
            )
            inserted += 1
    conn.commit()
    log.info(f"load_universe_history_csv({path}): {inserted} rows")
    return inserted

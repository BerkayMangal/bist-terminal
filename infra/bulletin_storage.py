# ================================================================
# BISTBULL TERMINAL — DAILY BULLETIN STORAGE
# infra/bulletin_storage.py
#
# Stage 7b. CRUD layer for the `daily_bulletin` SQLite table. One row
# per day; the bulletin generator writes once at the post_close slot
# and the /api/daily-brief endpoints read.
#
# Date semantics: the bulletin's "for" date is always Istanbul local
# date (not UTC) — bulletins anchor to BIST trading days, and a
# user opening the app at 22:00 IST is conceptually looking at "today"
# even though it's 19:00 UTC.
# ================================================================

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Optional

from infra.storage import _get_conn

log = logging.getLogger("bistbull.bulletin_storage")

ISTANBUL_TZ = dt.timezone(dt.timedelta(hours=3), name="TRT")
CURRENT_SCHEMA_VERSION = 1


def istanbul_today() -> str:
    """Return today's Istanbul date as YYYY-MM-DD."""
    return dt.datetime.now(dt.timezone.utc).astimezone(ISTANBUL_TZ).date().isoformat()


def save(bulletin_date: str, content: dict[str, Any]) -> None:
    """Upsert one day's bulletin. ``bulletin_date`` is YYYY-MM-DD.

    If a bulletin for the same day already exists, it's replaced —
    later writes on the same day (e.g. an end-of-day regenerate)
    supersede earlier ones.
    """
    conn = _get_conn()
    payload = json.dumps(content, ensure_ascii=False, default=str)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO daily_bulletin
            (bulletin_date, content_json, generated_at, schema_version)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(bulletin_date) DO UPDATE SET
            content_json = excluded.content_json,
            generated_at = excluded.generated_at,
            schema_version = excluded.schema_version
        """,
        (bulletin_date, payload, now, CURRENT_SCHEMA_VERSION),
    )
    conn.commit()


def get(bulletin_date: str) -> Optional[dict[str, Any]]:
    """Return the bulletin payload for the given date, or None."""
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT content_json, generated_at, schema_version
        FROM daily_bulletin
        WHERE bulletin_date = ?
        """,
        (bulletin_date,),
    ).fetchone()
    if not row:
        return None
    try:
        content = json.loads(row["content_json"])
    except (ValueError, TypeError) as exc:
        log.warning("bulletin %s has invalid JSON: %r", bulletin_date, exc)
        return None
    return {
        "bulletin_date": bulletin_date,
        "generated_at": row["generated_at"],
        "schema_version": row["schema_version"],
        "content": content,
    }


def get_latest() -> Optional[dict[str, Any]]:
    """Most recent bulletin regardless of date. Used by the UI when the
    user lands on the page and we don't yet know which date to load."""
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT bulletin_date, content_json, generated_at, schema_version
        FROM daily_bulletin
        ORDER BY bulletin_date DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    try:
        content = json.loads(row["content_json"])
    except (ValueError, TypeError):
        return None
    return {
        "bulletin_date": row["bulletin_date"],
        "generated_at": row["generated_at"],
        "schema_version": row["schema_version"],
        "content": content,
    }


def list_dates(limit: int = 30) -> list[dict[str, Any]]:
    """Return last N bulletin dates with generation timestamps. Used
    for the archive sidebar so the user can scroll back through
    history."""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT bulletin_date, generated_at
        FROM daily_bulletin
        ORDER BY bulletin_date DESC
        LIMIT ?
        """,
        (max(1, min(limit, 365)),),
    ).fetchall()
    return [
        {"bulletin_date": r["bulletin_date"], "generated_at": r["generated_at"]}
        for r in rows
    ]


def delete(bulletin_date: str) -> bool:
    """Remove a bulletin by date. Returns True if a row was deleted."""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM daily_bulletin WHERE bulletin_date = ?",
        (bulletin_date,),
    )
    conn.commit()
    return cur.rowcount > 0

# ================================================================
# BISTBULL TERMINAL -- SCHEMA MIGRATIONS (Phase 2)
# infra/migrations/__init__.py
#
# Sequential versioned schema migrations. Each .sql file in this
# directory is applied in lexicographic order (001_*, 002_*, 003_*)
# and tracked in the _schema_migrations table so rerun is idempotent.
#
# Pattern replaces the ad-hoc init_db() inline-CREATE-TABLE approach
# from Phase 1. New schema changes are a new .sql file -- NOT edits
# to an existing migration, NOT inline ALTER TABLE in Python code.
#
# USAGE
#   from infra.migrations import apply_migrations, _ensure_column
#   apply_migrations(conn)  # idempotent; safe to call on every startup
# ================================================================

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("bistbull.migrations")

_MIGRATIONS_DIR = Path(__file__).parent


def _init_tracking_table(conn: sqlite3.Connection) -> None:
    """Create _schema_migrations tracking table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            name       TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _discover_migrations() -> list[tuple[int, str, Path]]:
    """Find NNN_name.sql files; sorted by version. Fails on bad names / dupes."""
    migrations: list[tuple[int, str, Path]] = []
    for p in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        stem = p.stem
        try:
            version_str, _, name = stem.partition("_")
            version = int(version_str)
        except (ValueError, TypeError) as e:
            raise RuntimeError(
                f"Invalid migration filename {p.name!r}; expected NNN_name.sql"
            ) from e
        migrations.append((version, stem, p))

    seen: set[int] = set()
    for version, name, _ in migrations:
        if version in seen:
            raise RuntimeError(f"Duplicate migration version {version!r}: {name}")
        seen.add(version)

    return migrations


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM _schema_migrations").fetchall()
    return {r[0] for r in rows}


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply any pending migrations. Returns versions applied in this call.

    Each migration runs in BEGIN IMMEDIATE...COMMIT so a failure mid-way
    rolls back atomically; the _schema_migrations row is inserted only if
    the SQL succeeds.
    """
    _init_tracking_table(conn)
    applied = _applied_versions(conn)
    migrations = _discover_migrations()
    applied_this_call: list[int] = []

    for version, name, path in migrations:
        if version in applied:
            continue

        sql = path.read_text(encoding="utf-8")
        # Strip SQL comments BEFORE splitting on ';' so a ';' inside a
        # -- comment doesn't create a garbage fragment the naive split
        # would try to execute. Migrations do not contain string
        # literals with '--' in them (would not make sense in DDL), so
        # this simple pass is safe.
        cleaned_lines: list[str] = []
        for raw in sql.split("\n"):
            idx = raw.find("--")
            if idx >= 0:
                raw = raw[:idx]
            if raw.strip():
                cleaned_lines.append(raw)
        cleaned = "\n".join(cleaned_lines)

        # Now split on ';' and run each statement inside our BEGIN IMMEDIATE
        # transaction. conn.executescript() would auto-commit our pending
        # transaction and defeat rollback -- so we intentionally do not use it.
        stmts = [s.strip() for s in cleaned.split(";") if s.strip()]

        try:
            conn.execute("BEGIN IMMEDIATE")
            for stmt in stmts:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO _schema_migrations (version, name) VALUES (?, ?)",
                (version, name),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception(f"Migration {name!r} failed; rolled back")
            raise

        log.info(f"Applied migration {version:03d} — {name}")
        applied_this_call.append(version)

    return applied_this_call


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, ddl: str
) -> None:
    """Idempotent ALTER TABLE ... ADD COLUMN (PRAGMA table_info + ALTER).

    Promoted from Phase 1's infra/storage.py. Caller commits.
    """
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        return
    existing = {r[1] for r in rows}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

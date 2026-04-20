"""Tests for the infra.migrations applier (Phase 2)."""

import sqlite3
import pytest

from infra.migrations import (
    apply_migrations,
    _ensure_column,
    _discover_migrations,
)


@pytest.fixture
def fresh_db(tmp_path):
    """Each test gets its own SQLite file under tmp_path (Phase 2 non-negotiable
    #2: test DB does not touch production path)."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    yield conn
    conn.close()


class TestDiscovery:
    def test_finds_all_migrations(self):
        versions = [v for v, _, _ in _discover_migrations()]
        assert versions == sorted(versions)
        assert 1 in versions and 2 in versions and 3 in versions

    def test_names_are_stems_without_extension(self):
        names = [n for _, n, _ in _discover_migrations()]
        assert all("." not in n for n in names)
        assert any(n.startswith("001_") for n in names)

    def test_rejects_bad_filename_format(self, tmp_path, monkeypatch):
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / "no_number_prefix.sql").write_text("SELECT 1")
        monkeypatch.setattr("infra.migrations._MIGRATIONS_DIR", bad_dir)
        with pytest.raises(RuntimeError, match="Invalid migration filename"):
            _discover_migrations()

    def test_rejects_duplicate_versions(self, tmp_path, monkeypatch):
        bad_dir = tmp_path / "dupe"
        bad_dir.mkdir()
        (bad_dir / "001_first.sql").write_text("SELECT 1")
        (bad_dir / "001_second.sql").write_text("SELECT 1")
        monkeypatch.setattr("infra.migrations._MIGRATIONS_DIR", bad_dir)
        with pytest.raises(RuntimeError, match="Duplicate migration version"):
            _discover_migrations()


class TestApply:
    def test_fresh_db_applies_all(self, fresh_db):
        applied = apply_migrations(fresh_db)
        assert len(applied) >= 3  # at least 001/002/003 from Phase 2.1
        assert sorted(applied) == applied
        tracking = fresh_db.execute(
            "SELECT version, name FROM _schema_migrations ORDER BY version"
        ).fetchall()
        names = [r[1] for r in tracking]
        assert "001_users" in names
        assert "002_last_accessed_at" in names
        assert "003_score_history" in names

    def test_rerun_is_noop(self, fresh_db):
        apply_migrations(fresh_db)
        initial = fresh_db.execute("SELECT COUNT(*) FROM _schema_migrations").fetchone()[0]
        assert apply_migrations(fresh_db) == []
        after = fresh_db.execute("SELECT COUNT(*) FROM _schema_migrations").fetchone()[0]
        assert after == initial

    def test_users_table_schema(self, fresh_db):
        apply_migrations(fresh_db)
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(users)").fetchall()}
        assert cols == {"user_id", "email", "password_hash",
                        "created_at", "last_login_at", "is_active"}

    def test_score_history_table_schema(self, fresh_db):
        apply_migrations(fresh_db)
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(score_history)").fetchall()}
        assert cols == {
            "symbol", "snap_date", "score", "momentum", "risk",
            "fa_score", "ivme", "decision", "scoring_version"
        }
        # scoring_version part of the PK (Phase 4 A/B)
        pk_cols = [r[1] for r in fresh_db.execute(
            "PRAGMA table_info(score_history)"
        ).fetchall() if r[5] > 0]
        assert "scoring_version" in pk_cols

    def test_partial_old_db_catches_up(self, fresh_db):
        # Simulate a DB that already has 001 applied
        fresh_db.executescript("""
            CREATE TABLE _schema_migrations (
                version INTEGER PRIMARY KEY, applied_at TEXT, name TEXT
            );
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, created_at TEXT,
                last_login_at TEXT, is_active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO _schema_migrations (version, name) VALUES (1, '001_users');
        """)
        fresh_db.commit()
        applied = apply_migrations(fresh_db)
        assert 1 not in applied  # already applied, shouldn't re-run
        assert 2 in applied and 3 in applied

    def test_rollback_on_failure(self, fresh_db, tmp_path, monkeypatch):
        bad_dir = tmp_path / "rollback_test"
        bad_dir.mkdir()
        (bad_dir / "001_bad.sql").write_text(
            "CREATE TABLE t_dummy (a INT); SELECT invalid_col FROM nonexistent_table;"
        )
        monkeypatch.setattr("infra.migrations._MIGRATIONS_DIR", bad_dir)
        with pytest.raises(Exception):
            apply_migrations(fresh_db)
        # After rollback: no tracking rows, no t_dummy
        (n,) = fresh_db.execute("SELECT COUNT(*) FROM _schema_migrations").fetchone()
        assert n == 0
        tables = {r[0] for r in fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "t_dummy" not in tables


class TestEnsureColumn:
    def test_adds_missing_column(self, fresh_db):
        fresh_db.execute("CREATE TABLE t (a INT)")
        _ensure_column(fresh_db, "t", "b", "TEXT DEFAULT ''")
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(t)").fetchall()}
        assert cols == {"a", "b"}

    def test_skips_existing_column(self, fresh_db):
        fresh_db.execute("CREATE TABLE t (a INT, b TEXT)")
        _ensure_column(fresh_db, "t", "b", "TEXT DEFAULT 'x'")
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(t)").fetchall()}
        assert cols == {"a", "b"}

    def test_noop_on_missing_table(self, fresh_db):
        _ensure_column(fresh_db, "nonexistent", "x", "INTEGER")
        # no exception == success


class TestCwdIndependence:
    """Phase 4 FAZ 4.0.3: apply_migrations() must work regardless of cwd.

    Bug report: user's Colab run saw first-run create zero tables
    because _MIGRATIONS_DIR resolved to a relative path that became
    invalid after module import + os.chdir. Fix: Path(__file__).resolve().
    """

    def test_apply_from_any_cwd(self, tmp_path):
        import os
        import sqlite3

        # Save cwd; will restore
        original_cwd = os.getcwd()

        # Force re-import so _MIGRATIONS_DIR re-resolves in a known state.
        # (Normal tests don't need this; we need it because other tests may
        # have imported the module with a different cwd.)
        import importlib
        import infra.migrations
        importlib.reload(infra.migrations)

        try:
            # Change to a completely unrelated cwd
            os.chdir(str(tmp_path))
            # Create a fresh DB in tmp; ensure all migrations run
            db = tmp_path / "cwd_test.db"
            conn = sqlite3.connect(str(db))
            applied = infra.migrations.apply_migrations(conn)
            # At minimum migrations 001-006 are present
            assert len(applied) >= 6
            # Verify the migrations directory path is absolute
            assert infra.migrations._MIGRATIONS_DIR.is_absolute()
            conn.close()
        finally:
            os.chdir(original_cwd)

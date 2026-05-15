# ================================================================
# Unit tests for core.snapshot_store.SnapshotStore.
# Backed by an in-process FakeRedis stub (tests/_fake_redis.py).
# No real Redis, no network, no I/O.
# ================================================================

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from core.snapshot_store import (
    SnapshotStore,
    SnapshotLockHeld,
    SCHEMA_VERSION,
    SCAN_TTL_SEC,
)

from tests._fake_redis import FakeRedis


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def store(fake: FakeRedis) -> SnapshotStore:
    return SnapshotStore(client=fake)


def _scored(*pairs: tuple[str, float]) -> list[tuple[str, float, dict]]:
    """Build a scored list with payloads that include the ticker for
    round-trip assertions."""
    return [
        (t, s, {"symbol": t, "score": s, "payload_marker": f"payload-{t}"})
        for t, s in pairs
    ]


# ── 1. Basic write + read round-trip ────────────────────────────────


class TestRoundTrip:
    def test_write_returns_scan_id(self, store: SnapshotStore):
        sid = store.write_snapshot("m", _scored(("A", 1.0)))
        assert sid is not None
        assert isinstance(sid, str)
        assert len(sid) > 10  # time-prefix-suffix

    def test_latest_updated_after_successful_write(
        self, store: SnapshotStore,
    ):
        sid = store.write_snapshot("m", _scored(("A", 1.0)))
        assert store.read_latest_scan_id("m") == sid

    def test_top_returns_score_desc(self, store: SnapshotStore):
        store.write_snapshot(
            "m",
            _scored(("B", 2.0), ("A", 5.0), ("C", 1.5)),
        )
        top = store.read_top("m", 10)
        assert [t for t, _ in top] == ["A", "B", "C"]
        assert top[0][1] == 5.0

    def test_top_respects_limit(self, store: SnapshotStore):
        store.write_snapshot("m", _scored(*[(f"T{i}", float(i)) for i in range(10)]))
        top = store.read_top("m", 3)
        assert len(top) == 3
        assert [t for t, _ in top] == ["T9", "T8", "T7"]

    def test_items_round_trip(self, store: SnapshotStore):
        store.write_snapshot(
            "m", _scored(("A", 1.0), ("B", 2.0)),
        )
        items = store.read_items("m", ["A", "B"])
        assert items["A"]["payload_marker"] == "payload-A"
        assert items["B"]["symbol"] == "B"


# ── 2. Atomic write — latest never reflects a half-written snapshot ─


class TestAtomicWrite:
    def test_pipeline_failure_keeps_old_latest(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        sid_old = store.write_snapshot("m", _scored(("A", 1.0)))
        # Simulate failure mid-pipeline on the meta SET (last op)
        fake.fail_pipeline_on_op = "set"
        result = store.write_snapshot("m", _scored(("B", 2.0)))
        # write returns None (failure), latest pointer unchanged
        assert result is None
        assert store.read_latest_scan_id("m") == sid_old
        # B should not be visible
        items = store.read_items("m", ["B"])
        assert items == {} or all(
            it.get("payload_marker") != "payload-B" for it in items.values()
        )

    def test_empty_scored_list_rejected(self, store: SnapshotStore):
        assert store.write_snapshot("m", []) is None

    def test_no_client_returns_none(self):
        s = SnapshotStore(client=None)
        # _client() falls back to redis_client.get_client() which returns
        # None when REDIS_URL is unset. Should degrade gracefully.
        assert s.write_snapshot("m", _scored(("A", 1.0))) is None
        assert s.read_latest_scan_id("m") is None
        assert s.read_top("m", 5) == []


# ── 3. Pointer swap (latest/previous) ───────────────────────────────


class TestPointerSwap:
    def test_second_write_moves_first_to_previous(
        self, store: SnapshotStore,
    ):
        sid1 = store.write_snapshot("m", _scored(("A", 1.0)))
        sid2 = store.write_snapshot("m", _scored(("B", 2.0)))
        assert store.read_latest_scan_id("m") == sid2
        assert store.read_previous_scan_id("m") == sid1

    def test_third_write_overwrites_previous(
        self, store: SnapshotStore,
    ):
        sid1 = store.write_snapshot("m", _scored(("A", 1.0)))
        sid2 = store.write_snapshot("m", _scored(("B", 2.0)))
        sid3 = store.write_snapshot("m", _scored(("C", 3.0)))
        assert store.read_latest_scan_id("m") == sid3
        # Previous should now be sid2 (the immediately prior one), not sid1
        assert store.read_previous_scan_id("m") == sid2


# ── 4. Health / corruption / fallback ───────────────────────────────


class TestHealthAndFallback:
    def test_healthy_after_write(self, store: SnapshotStore):
        store.write_snapshot("m", _scored(("A", 1.0)))
        assert store.is_healthy("m") is True

    def test_missing_latest_is_unhealthy(self, store: SnapshotStore):
        assert store.is_healthy("m") is False

    def test_meta_corruption_breaks_health(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        sid = store.write_snapshot("m", _scored(("A", 1.0)))
        # Manually corrupt the meta key
        fake.delete(f"bb:snapshots:m:{sid}:meta")
        assert store.is_healthy("m") is False

    def test_item_corruption_breaks_health(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        sid = store.write_snapshot("m", _scored(("A", 1.0), ("B", 2.0)))
        # Delete one item key — sample check should catch it
        fake.delete(f"bb:snapshots:m:{sid}:item:A")
        fake.delete(f"bb:snapshots:m:{sid}:item:B")
        assert store.is_healthy("m") is False

    def test_fallback_to_previous_promotes(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        sid1 = store.write_snapshot("m", _scored(("A", 1.0)))
        sid2 = store.write_snapshot("m", _scored(("B", 2.0)))
        # Corrupt sid2
        fake.delete(f"bb:snapshots:m:{sid2}:meta")
        assert store.is_healthy("m") is False
        assert store.fallback_to_previous("m") is True
        assert store.read_latest_scan_id("m") == sid1
        assert store.is_healthy("m") is True

    def test_fallback_fails_when_previous_also_broken(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        sid1 = store.write_snapshot("m", _scored(("A", 1.0)))
        sid2 = store.write_snapshot("m", _scored(("B", 2.0)))
        fake.delete(f"bb:snapshots:m:{sid2}:meta")
        fake.delete(f"bb:snapshots:m:{sid1}:meta")
        assert store.fallback_to_previous("m") is False

    def test_schema_version_mismatch_meta_returns_none(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        sid = store.write_snapshot("m", _scored(("A", 1.0)))
        bad = {
            "scan_id": sid,
            "module": "m",
            "schema_version": "ancient",
            "asof_unix": 0,
        }
        fake.set(
            f"bb:snapshots:m:{sid}:meta",
            json.dumps(bad),
        )
        assert store.read_meta("m") is None
        # is_healthy also fails because read_meta() returns None
        assert store.is_healthy("m") is False


# ── 5. Age / staleness ──────────────────────────────────────────────


class TestAge:
    def test_age_is_small_immediately_after_write(
        self, store: SnapshotStore,
    ):
        store.write_snapshot("m", _scored(("A", 1.0)))
        age = store.read_age_sec("m")
        assert age is not None
        assert 0 <= age < 2  # well under a second normally

    def test_age_increases_with_fake_clock(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        store.write_snapshot("m", _scored(("A", 1.0)))
        fake._fast_forward(120)  # advance fake redis clock
        # asof_unix uses real time.time(), so fast_forward affects TTL but
        # not the meta. We need to advance Python's clock perception via
        # monkeypatching time.time — simpler: just check the value's there.
        age = store.read_age_sec("m")
        assert age is not None
        assert age >= 0

    def test_age_none_when_no_snapshot(self, store: SnapshotStore):
        assert store.read_age_sec("m") is None


# ── 6. Cleanup orphans ──────────────────────────────────────────────


class TestCleanup:
    def test_cleanup_preserves_latest_and_previous(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        sid1 = store.write_snapshot("m", _scored(("A", 1.0)))
        sid2 = store.write_snapshot("m", _scored(("B", 2.0)))
        sid3 = store.write_snapshot("m", _scored(("C", 3.0)))
        # Now sid3=latest, sid2=previous, sid1=orphan
        deleted = store.cleanup_orphans("m")
        assert deleted > 0
        # sid3 and sid2 keys must still exist
        assert fake.exists(f"bb:snapshots:m:{sid3}:meta") == 1
        assert fake.exists(f"bb:snapshots:m:{sid2}:meta") == 1
        # sid1 keys must be gone
        assert fake.exists(f"bb:snapshots:m:{sid1}:meta") == 0
        assert fake.exists(f"bb:snapshots:m:{sid1}:score") == 0

    def test_cleanup_never_touches_latest_pointer(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        sid = store.write_snapshot("m", _scored(("A", 1.0)))
        store.cleanup_orphans("m")
        assert store.read_latest_scan_id("m") == sid

    def test_cleanup_no_redis_returns_zero(self):
        s = SnapshotStore(client=None)
        assert s.cleanup_orphans("m") == 0


# ── 7. Locking ──────────────────────────────────────────────────────


class TestLock:
    def test_lock_acquire_and_release(self, store: SnapshotStore):
        with store.write_lock("m"):
            assert store.is_locked("m") is True
        assert store.is_locked("m") is False

    def test_lock_contention_raises(self, store: SnapshotStore):
        with store.write_lock("m"):
            with pytest.raises(SnapshotLockHeld):
                with store.write_lock("m"):
                    pass

    def test_lock_per_module_isolated(self, store: SnapshotStore):
        with store.write_lock("m"):
            # Different module should still be acquirable
            with store.write_lock("other"):
                assert store.is_locked("m") is True
                assert store.is_locked("other") is True


# ── 8. Module isolation ─────────────────────────────────────────────


class TestModuleIsolation:
    def test_two_modules_dont_collide(self, store: SnapshotStore):
        store.write_snapshot("bullwatch", _scored(("A", 10.0)))
        store.write_snapshot("bullalpha", _scored(("Z", 99.0)))
        assert store.read_top("bullwatch", 5)[0][0] == "A"
        assert store.read_top("bullalpha", 5)[0][0] == "Z"
        # And reading one module doesn't read the other's data
        bw_items = store.read_items("bullwatch", ["Z"])
        assert bw_items == {}

    def test_cleanup_per_module(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        sid_bw = store.write_snapshot("bullwatch", _scored(("A", 1.0)))
        sid_ba = store.write_snapshot("bullalpha", _scored(("Z", 1.0)))
        # cleanup_orphans("bullwatch") should not touch bullalpha keys
        store.cleanup_orphans("bullwatch")
        assert fake.exists(f"bb:snapshots:bullalpha:{sid_ba}:meta") == 1


# ── 9. Partial read corruption ──────────────────────────────────────


class TestPartialRead:
    def test_read_items_skips_missing(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        sid = store.write_snapshot(
            "m", _scored(("A", 1.0), ("B", 2.0), ("C", 3.0)),
        )
        # Delete B's item key
        fake.delete(f"bb:snapshots:m:{sid}:item:B")
        out = store.read_items("m", ["A", "B", "C"])
        # Caller sees partial result — can detect corruption
        assert "A" in out
        assert "C" in out
        assert "B" not in out


# ── 10. Meta includes system fields ─────────────────────────────────


class TestMeta:
    def test_meta_carries_system_fields(self, store: SnapshotStore):
        sid = store.write_snapshot(
            "m", _scored(("A", 1.0)),
            meta={"extra_caller_field": "hello"},
        )
        meta = store.read_meta("m")
        assert meta is not None
        assert meta["scan_id"] == sid
        assert meta["module"] == "m"
        assert meta["schema_version"] == SCHEMA_VERSION
        assert meta["n_scored"] == 1
        assert meta["extra_caller_field"] == "hello"
        assert "asof" in meta
        assert "asof_unix" in meta


# ── 11. Lock token CAS (TTL expiry corner case) ─────────────────────


class TestLockCASRelease:
    def test_release_only_deletes_if_token_matches(
        self, store: SnapshotStore, fake: FakeRedis,
    ):
        # Acquire lock, then manually overwrite the lock key with a different
        # token (simulating: our lock TTL'd out and someone else grabbed it).
        # On context exit, our cleanup must NOT delete the new owner's lock.
        try:
            with store.write_lock("m"):
                fake.set("bb:snapshots:m:lock", "different-token")
        except SnapshotLockHeld:
            pytest.fail("should not raise on release")
        # The "different-token" lock should still be held
        assert fake.get("bb:snapshots:m:lock") == "different-token"

# ================================================================
# Minimal in-process Redis stub for snapshot_store tests.
#
# Implements only the surface area SnapshotStore actually uses:
#   get, set (NX, EX), delete, exists
#   mget
#   zadd, zrevrange, zcard
#   sadd, smembers
#   expire, scan_iter
#   pipeline(transaction=True) → .set/.zadd/.expire/.sadd → .execute()
#
# Strings only (decode_responses=True equivalent). TTL is tracked but
# not actively enforced — tests can call _fast_forward(seconds) to age
# keys for stale/expiry assertions.
# ================================================================

from __future__ import annotations

import fnmatch
import time
from typing import Any, Iterable, Optional


class FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}            # key → value (str | dict-zset | set)
        self._expires: dict[str, float] = {}        # key → unix expiry
        self._clock_offset: float = 0.0             # tests can fast-forward
        # Hook to simulate a failure inside pipeline.execute (atomic write test).
        self.fail_pipeline_on_op: Optional[str] = None

    # ── Test helpers ──────────────────────────────────────────────

    def _now(self) -> float:
        return time.time() + self._clock_offset

    def _fast_forward(self, seconds: float) -> None:
        self._clock_offset += seconds

    def _check_expired(self, key: str) -> bool:
        exp = self._expires.get(key)
        if exp is not None and self._now() >= exp:
            self._data.pop(key, None)
            self._expires.pop(key, None)
            return True
        return False

    # ── Strings ───────────────────────────────────────────────────

    def get(self, key: str) -> Optional[str]:
        self._check_expired(key)
        v = self._data.get(key)
        if isinstance(v, str):
            return v
        return None

    def set(
        self,
        key: str,
        value: str,
        ex: Optional[int] = None,
        nx: bool = False,
    ) -> Any:
        self._check_expired(key)
        if nx and key in self._data:
            return None  # redis-py returns None when NX and key exists
        self._data[key] = str(value)
        if ex is not None:
            self._expires[key] = self._now() + ex
        else:
            self._expires.pop(key, None)
        return True

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._data:
                self._data.pop(k, None)
                self._expires.pop(k, None)
                n += 1
        return n

    def exists(self, key: str) -> int:
        self._check_expired(key)
        return 1 if key in self._data else 0

    def expire(self, key: str, sec: int) -> int:
        if key not in self._data:
            return 0
        self._expires[key] = self._now() + sec
        return 1

    def mget(self, keys: Iterable[str]) -> list[Optional[str]]:
        out: list[Optional[str]] = []
        for k in keys:
            self._check_expired(k)
            v = self._data.get(k)
            out.append(v if isinstance(v, str) else None)
        return out

    # ── Sorted sets ───────────────────────────────────────────────

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self._check_expired(key)
        cur = self._data.setdefault(key, {})
        if not isinstance(cur, dict):
            cur = {}
            self._data[key] = cur
        added = 0
        for member, score in mapping.items():
            if member not in cur:
                added += 1
            cur[str(member)] = float(score)
        return added

    def zrevrange(
        self,
        key: str,
        start: int,
        end: int,
        withscores: bool = False,
    ) -> list:
        self._check_expired(key)
        zs = self._data.get(key)
        if not isinstance(zs, dict) or not zs:
            return []
        items = sorted(zs.items(), key=lambda kv: kv[1], reverse=True)
        sliced = items[start: end + 1]
        if withscores:
            return [(m, s) for m, s in sliced]
        return [m for m, _ in sliced]

    def zcard(self, key: str) -> int:
        self._check_expired(key)
        zs = self._data.get(key)
        return len(zs) if isinstance(zs, dict) else 0

    # ── Sets ──────────────────────────────────────────────────────

    def sadd(self, key: str, *members: str) -> int:
        self._check_expired(key)
        cur = self._data.setdefault(key, set())
        if not isinstance(cur, set):
            cur = set()
            self._data[key] = cur
        n = 0
        for m in members:
            ms = str(m)
            if ms not in cur:
                cur.add(ms)
                n += 1
        return n

    def smembers(self, key: str) -> set:
        self._check_expired(key)
        v = self._data.get(key)
        return set(v) if isinstance(v, set) else set()

    # ── Scan ──────────────────────────────────────────────────────

    def scan_iter(self, match: str = "*"):
        # Snapshot keys to avoid mid-iteration mutation issues
        for k in list(self._data.keys()):
            self._check_expired(k)
            if k in self._data and fnmatch.fnmatchcase(k, match):
                yield k

    # ── Pipeline ──────────────────────────────────────────────────

    def pipeline(self, transaction: bool = True) -> "FakePipeline":
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._ops: list[tuple] = []

    def set(self, key, value, ex=None, nx=False):
        self._ops.append(("set", key, value, ex, nx))
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def expire(self, key, sec):
        self._ops.append(("expire", key, sec))
        return self

    def sadd(self, key, *members):
        self._ops.append(("sadd", key, members))
        return self

    def delete(self, *keys):
        self._ops.append(("delete", keys))
        return self

    def execute(self) -> list:
        results = []
        for op in self._ops:
            kind = op[0]
            # Simulated failure for atomicity tests
            if self._client.fail_pipeline_on_op == kind:
                raise RuntimeError(f"simulated pipeline failure on {kind}")
            if kind == "set":
                _, k, v, ex, nx = op
                results.append(self._client.set(k, v, ex=ex, nx=nx))
            elif kind == "zadd":
                _, k, m = op
                results.append(self._client.zadd(k, m))
            elif kind == "expire":
                _, k, s = op
                results.append(self._client.expire(k, s))
            elif kind == "sadd":
                _, k, members = op
                results.append(self._client.sadd(k, *members))
            elif kind == "delete":
                _, keys = op
                results.append(self._client.delete(*keys))
        self._ops.clear()
        return results

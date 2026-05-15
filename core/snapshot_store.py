# ================================================================
# BISTBULL TERMINAL — SHARED SNAPSHOT STORE
# core/snapshot_store.py
#
# Module-agnostic persistence layer for ranked snapshots.
#
# Concepts
# --------
#   scan_id        Monotonic identifier for one consistent snapshot.
#   latest pointer snapshots:{module}:latest   → current scan_id
#   previous       snapshots:{module}:previous → last-known-good scan_id
#                  (used as fallback if latest becomes corrupted).
#
# Per-scan keys (all 7-day TTL):
#   snapshots:{module}:{scan_id}:meta              JSON
#   snapshots:{module}:{scan_id}:score             ZSET (ticker→score)
#   snapshots:{module}:{scan_id}:items_set         SET   (manifest)
#   snapshots:{module}:{scan_id}:item:{TICKER}     JSON  (per-row payload)
#
# Write flow (atomic) — pipeline/MULTI-EXEC:
#   1. Write all per-item JSON keys with TTL.
#   2. Write score ZSET + items_set manifest with TTL.
#   3. Write meta JSON with TTL.
#   4. AFTER pipeline succeeds: read old latest, SET latest = new scan_id,
#      SET previous = old scan_id. Pointers swap last so partial writes
#      never replace the visible snapshot.
#
# Generic by design — NO module-specific logic. The same instance serves
# bullwatch, bullalpha, radar, fundamental, momentum, portfolio, …
# ================================================================

from __future__ import annotations

import datetime as _dt
import json
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Optional

from core import redis_client as _redis_client_module

log = logging.getLogger("bistbull.snapshot_store")

# Schema version travels with each snapshot's meta. Readers reject mismatched
# versions so we don't try to interpret a structurally-different payload.
SCHEMA_VERSION = "d1"

# Per-scan key TTL. Long enough to compute 7-day delta history; short enough
# that orphaned scans (crashed before pointer swap) age out on their own.
SCAN_TTL_SEC = 7 * 24 * 3600

DEFAULT_LOCK_TTL_SEC = 600
DEFAULT_KEY_PREFIX = "bb:snapshots"


class SnapshotLockHeld(Exception):
    """Raised when write_lock cannot be acquired."""


class SnapshotStore:
    """Generic ranked-snapshot persistence.

    Pass a redis-py-compatible client at construction (optional — default
    pulls the singleton from core.redis_client). All methods degrade safely
    when no client is available (Redis-less dev mode): writes return None,
    reads return empty.
    """

    def __init__(
        self,
        client: Optional[Any] = None,
        key_prefix: str = DEFAULT_KEY_PREFIX,
    ) -> None:
        self._client_override = client
        self._prefix = key_prefix

    # ── Client access ───────────────────────────────────────────────

    def _client(self) -> Optional[Any]:
        if self._client_override is not None:
            return self._client_override
        return _redis_client_module.get_client()

    # ── Key builders ────────────────────────────────────────────────

    def _ns(self, module: str) -> str:
        return f"{self._prefix}:{module}"

    def _latest_key(self, module: str) -> str:
        return f"{self._ns(module)}:latest"

    def _previous_key(self, module: str) -> str:
        return f"{self._ns(module)}:previous"

    def _lock_key(self, module: str) -> str:
        return f"{self._ns(module)}:lock"

    def _meta_key(self, module: str, scan_id: str) -> str:
        return f"{self._ns(module)}:{scan_id}:meta"

    def _score_key(self, module: str, scan_id: str) -> str:
        return f"{self._ns(module)}:{scan_id}:score"

    def _items_set_key(self, module: str, scan_id: str) -> str:
        return f"{self._ns(module)}:{scan_id}:items_set"

    def _item_key(self, module: str, scan_id: str, ticker: str) -> str:
        return f"{self._ns(module)}:{scan_id}:item:{ticker}"

    # ── Writer ──────────────────────────────────────────────────────

    def write_snapshot(
        self,
        module: str,
        scored: list[tuple[str, float, dict]],
        meta: Optional[dict] = None,
        scan_id: Optional[str] = None,
    ) -> Optional[str]:
        """Atomically persist a ranked snapshot.

        Args:
            module:   Module namespace (e.g. "bullwatch").
            scored:   List of (ticker, score, payload) tuples.
            meta:     Optional caller-provided meta; merged with system fields.
            scan_id:  Override scan_id (tests/migration). Default: time-based.

        Returns:
            scan_id on success, None on failure (Redis down, empty list,
            or pipeline error).
        """
        client = self._client()
        if client is None:
            log.debug("snapshot write skipped (no redis): module=%s", module)
            return None
        if not scored:
            log.warning("snapshot write rejected: empty scored list for %s", module)
            return None

        scan_id = scan_id or _new_scan_id()
        tickers = [t for t, _, _ in scored]

        full_meta: dict[str, Any] = {
            "scan_id": scan_id,
            "module": module,
            "asof": _now_iso(),
            "asof_unix": time.time(),
            "schema_version": SCHEMA_VERSION,
            "n_scored": len(scored),
            **(meta or {}),
        }

        # ── Step 1: atomic data write ───────────────────────────────
        try:
            pipe = client.pipeline(transaction=True)
            for ticker, _score, payload in scored:
                pipe.set(
                    self._item_key(module, scan_id, ticker),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    ex=SCAN_TTL_SEC,
                )
            score_key = self._score_key(module, scan_id)
            pipe.zadd(score_key, {t: float(s) for t, s, _ in scored})
            pipe.expire(score_key, SCAN_TTL_SEC)
            items_set_key = self._items_set_key(module, scan_id)
            pipe.sadd(items_set_key, *tickers)
            pipe.expire(items_set_key, SCAN_TTL_SEC)
            pipe.set(
                self._meta_key(module, scan_id),
                json.dumps(full_meta, ensure_ascii=False, default=str),
                ex=SCAN_TTL_SEC,
            )
            pipe.execute()
        except Exception as e:
            log.exception(
                "snapshot pipeline write failed: %s/%s: %r",
                module, scan_id, e,
            )
            return None

        # ── Step 2: pointer swap (only after data fully written) ────
        try:
            old_latest = self._safe_get(self._latest_key(module))
            client.set(self._latest_key(module), scan_id)
            if old_latest and old_latest != scan_id:
                client.set(self._previous_key(module), old_latest)
        except Exception as e:
            log.warning(
                "snapshot pointer update failed: %s/%s: %r",
                module, scan_id, e,
            )
            return None

        log.info(
            "snapshot written: %s/%s (%d items)",
            module, scan_id, len(scored),
        )
        return scan_id

    # ── Readers ─────────────────────────────────────────────────────

    def read_latest_scan_id(self, module: str) -> Optional[str]:
        return self._safe_get(self._latest_key(module))

    def read_previous_scan_id(self, module: str) -> Optional[str]:
        return self._safe_get(self._previous_key(module))

    def read_meta(
        self,
        module: str,
        scan_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Read meta JSON. Returns None if missing, unparseable, or schema
        version mismatch."""
        if scan_id is None:
            scan_id = self.read_latest_scan_id(module)
            if scan_id is None:
                return None
        raw = self._safe_get(self._meta_key(module, scan_id))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.warning(
                "snapshot meta unparseable: %s/%s", module, scan_id,
            )
            return None
        if data.get("schema_version") != SCHEMA_VERSION:
            log.warning(
                "snapshot schema mismatch: %s/%s (want %s, got %s)",
                module, scan_id, SCHEMA_VERSION, data.get("schema_version"),
            )
            return None
        return data

    def read_top(
        self,
        module: str,
        n: int,
        scan_id: Optional[str] = None,
    ) -> list[tuple[str, float]]:
        """Return up to N (ticker, score) tuples sorted by score desc."""
        client = self._client()
        if client is None:
            return []
        if scan_id is None:
            scan_id = self.read_latest_scan_id(module)
            if scan_id is None:
                return []
        try:
            raw = client.zrevrange(
                self._score_key(module, scan_id),
                0, max(0, n - 1),
                withscores=True,
            )
        except Exception as e:
            log.warning(
                "snapshot read_top failed: %s/%s: %r", module, scan_id, e,
            )
            return []
        return [(str(t), float(s)) for t, s in raw]

    def read_items(
        self,
        module: str,
        tickers: Iterable[str],
        scan_id: Optional[str] = None,
    ) -> dict[str, dict]:
        """Bulk read per-ticker payloads. Missing tickers are silently
        skipped (a partial item set is itself a corruption signal —
        callers can detect via len(returned) < len(requested))."""
        client = self._client()
        if client is None:
            return {}
        if scan_id is None:
            scan_id = self.read_latest_scan_id(module)
            if scan_id is None:
                return {}
        ticker_list = list(tickers)
        if not ticker_list:
            return {}
        keys = [self._item_key(module, scan_id, t) for t in ticker_list]
        try:
            raws = client.mget(keys)
        except Exception as e:
            log.warning(
                "snapshot mget failed: %s/%s: %r", module, scan_id, e,
            )
            return {}
        out: dict[str, dict] = {}
        for ticker, raw in zip(ticker_list, raws):
            if raw is None:
                continue
            try:
                out[ticker] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def read_age_sec(self, module: str) -> Optional[float]:
        """Seconds since latest snapshot's asof_unix. None if unknown."""
        meta = self.read_meta(module)
        if meta is None:
            return None
        asof_unix = meta.get("asof_unix")
        if asof_unix is None:
            return None
        try:
            return max(0.0, time.time() - float(asof_unix))
        except (TypeError, ValueError):
            return None

    # ── Integrity ───────────────────────────────────────────────────

    def is_healthy(
        self,
        module: str,
        scan_id: Optional[str] = None,
    ) -> bool:
        """Lightweight integrity check.

        Verifies that:
          - meta exists, parses, and matches SCHEMA_VERSION
          - score ZSET has members
          - items_set is non-empty
          - a sample of item keys (≤5) exists
        """
        client = self._client()
        if client is None:
            return False
        if scan_id is None:
            scan_id = self.read_latest_scan_id(module)
            if scan_id is None:
                return False
        if self.read_meta(module, scan_id) is None:
            return False
        try:
            if int(client.zcard(self._score_key(module, scan_id))) == 0:
                return False
        except Exception:
            return False
        try:
            members = client.smembers(self._items_set_key(module, scan_id))
        except Exception:
            return False
        if not members:
            return False
        sample = list(members)[:5]
        try:
            sample_keys = [
                self._item_key(module, scan_id, str(t)) for t in sample
            ]
            sample_vals = client.mget(sample_keys)
        except Exception:
            return False
        if any(v is None for v in sample_vals):
            return False
        return True

    def fallback_to_previous(self, module: str) -> bool:
        """Promote `previous` pointer to `latest`. Returns False if no
        previous exists or it's also unhealthy."""
        client = self._client()
        if client is None:
            return False
        prev = self.read_previous_scan_id(module)
        if prev is None:
            return False
        if not self.is_healthy(module, scan_id=prev):
            return False
        try:
            client.set(self._latest_key(module), prev)
            client.delete(self._previous_key(module))
        except Exception as e:
            log.warning("snapshot fallback failed: %s: %r", module, e)
            return False
        log.info(
            "snapshot fallback: %s promoted previous %s → latest",
            module, prev,
        )
        return True

    # ── Cleanup ─────────────────────────────────────────────────────

    def cleanup_orphans(
        self,
        module: str,
        keep_scan_ids: Optional[Iterable[str]] = None,
    ) -> int:
        """Delete per-scan keys belonging to scan_ids that are neither
        current `latest`/`previous` nor in `keep_scan_ids`."""
        client = self._client()
        if client is None:
            return 0
        keep = set(keep_scan_ids or [])
        latest = self.read_latest_scan_id(module)
        previous = self.read_previous_scan_id(module)
        if latest:
            keep.add(latest)
        if previous:
            keep.add(previous)
        ns = self._ns(module) + ":"
        pattern = ns + "*"
        deleted = 0
        try:
            for key in client.scan_iter(match=pattern):
                tail = key[len(ns):]
                if tail in {"latest", "previous", "lock"}:
                    continue
                # Per-scan keys are "{scan_id}:meta", "{scan_id}:score",
                # "{scan_id}:items_set", "{scan_id}:item:{TICKER}".
                # scan_id is the first segment before ":".
                scan_id = tail.split(":", 1)[0] if ":" in tail else tail
                if scan_id in keep:
                    continue
                try:
                    client.delete(key)
                    deleted += 1
                except Exception:
                    continue
        except Exception as e:
            log.warning("snapshot cleanup failed: %s: %r", module, e)
        return deleted

    # ── Lock ────────────────────────────────────────────────────────

    @contextmanager
    def write_lock(
        self,
        module: str,
        ttl_sec: int = DEFAULT_LOCK_TTL_SEC,
    ):
        """Acquire an exclusive write lock for this module. Raises
        SnapshotLockHeld if another writer holds it. Lock auto-expires
        after ttl_sec so a crashed writer never leaves us stuck."""
        client = self._client()
        if client is None:
            raise SnapshotLockHeld(f"{module}: no redis client")
        key = self._lock_key(module)
        token = uuid.uuid4().hex
        try:
            acquired = client.set(key, token, nx=True, ex=ttl_sec)
        except Exception as e:
            raise SnapshotLockHeld(f"{module}: lock set failed: {e}") from e
        if not acquired:
            raise SnapshotLockHeld(f"{module}: already held")
        try:
            yield token
        finally:
            try:
                # CAS release: only delete if we still own the token. Stops
                # us from killing a successor's lock if ours TTL'd out and
                # someone else acquired it.
                current = client.get(key)
                if current == token:
                    client.delete(key)
            except Exception as e:
                log.warning("snapshot lock release failed: %s: %r", module, e)

    def is_locked(self, module: str) -> bool:
        client = self._client()
        if client is None:
            return False
        try:
            return bool(client.exists(self._lock_key(module)))
        except Exception:
            return False

    # ── Helpers ─────────────────────────────────────────────────────

    def _safe_get(self, key: str) -> Optional[str]:
        client = self._client()
        if client is None:
            return None
        try:
            return client.get(key)
        except Exception as e:
            log.warning("redis get failed: %s: %r", key, e)
            return None


# ── Module-level helpers ────────────────────────────────────────────

def _new_scan_id() -> str:
    """Monotonic, ULID-ish scan_id: 13-digit ms timestamp + 8-char random
    suffix. Sortable lexicographically by time, collision-resistant."""
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


_default_store: Optional[SnapshotStore] = None


def get_default_store() -> SnapshotStore:
    """Singleton bound to the default Redis client. Use from API handlers
    and background loops. Tests should construct their own SnapshotStore
    with an injected fake client."""
    global _default_store
    if _default_store is None:
        _default_store = SnapshotStore()
    return _default_store

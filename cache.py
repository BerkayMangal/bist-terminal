# ================================================================
# BISTBULL TERMINAL V10.0 — DUAL-LAYER CACHE
# V9.1'in cache.py'sini tamamen replace eder.
#
# MİMARİ:
#   L1 = In-memory TTLCache (hızlı, process-local, volatile)
#   L2 = Redis (persistent, Railway restart'a dayanıklı, opsiyonel)
#
# AKIŞ:
#   GET → L1 hit? → dön
#       → L1 miss → L2 hit? → L1'e yaz → dön
#       → L2 miss → None (caller hesaplar, sonra set çağırır)
#
#   SET → L1'e yaz + L2'ye yaz (fire-and-forget)
#
# STALE-WHILE-REVALIDATE:
#   Veri TTL aşmış ama STALE_GRACE içindeyse:
#   → stale=True meta ile birlikte dön
#   → arka planda yenileme tetiklenebilir
#
# SNAPSHOT PERSISTENCE:
#   top10, scan_status gibi kritik veriler Redis'e persist edilir.
#   Railway restart sonrası L2'den restore edilir — boş ekran yok.
# ================================================================

from __future__ import annotations

import threading
import time
import json
import logging
from typing import Any, Optional

from cachetools import TTLCache

from config import (
    RAW_CACHE_TTL, RAW_CACHE_SIZE,
    ANALYSIS_CACHE_TTL, ANALYSIS_CACHE_SIZE,
    TECH_CACHE_TTL, TECH_CACHE_SIZE,
    AI_CACHE_TTL, AI_CACHE_SIZE,
    HISTORY_CACHE_TTL, HISTORY_CACHE_SIZE,
    MACRO_CACHE_TTL, TAKAS_CACHE_TTL, SOCIAL_CACHE_TTL,
    BRIEFING_CACHE_TTL, HERO_CACHE_TTL, AGENT_CACHE_TTL,
    HEATMAP_CACHE_TTL, MACRO_AI_CACHE_TTL,
    STALE_GRACE_SECONDS,
    REDIS_SNAPSHOT_KEY,
)
from core import redis_client

log = logging.getLogger("bistbull.cache")


# ================================================================
# SAFE CACHE — Thread-safe L1 + L2 dual-layer wrapper
# ================================================================
class SafeCache:
    """
    Thread-safe dual-layer cache.

    L1: In-memory TTLCache — hızlı, volatile
    L2: Redis — persistent, opsiyonel

    Her instance'ın bir namespace'i var (ör: "raw", "tech").
    L2 key'leri: "cache:{namespace}:{key}" formatında.

    Stale-while-revalidate:
      L1'deki veri expire olmuşsa ama stale_grace içindeyse,
      get() stale veriyi meta ile birlikte döner.
      Bunun için dahili _timestamps dict'i tutulur.
    """

    __slots__ = ("_cache", "_lock", "_namespace", "_ttl", "_timestamps", "_l2_enabled")

    def __init__(self, maxsize: int, ttl: int, namespace: str, l2_enabled: bool = True) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock: threading.Lock = threading.Lock()
        self._namespace: str = namespace
        self._ttl: int = ttl
        self._timestamps: dict[str, float] = {}
        self._l2_enabled: bool = l2_enabled

    def _l2_key(self, key: str) -> str:
        return f"cache:{self._namespace}:{key}"

    # ================================================================
    # GET — L1 → L2 → None
    # ================================================================
    def get(self, key: str, default: Any = None) -> Any:
        """
        Değer oku. L1'de varsa döner, yoksa L2'ye bakar.
        L2'de bulursa L1'e yazar (promote).
        """
        with self._lock:
            # L1 hit
            try:
                val = self._cache[key]
                return val
            except KeyError:
                pass

        # L1 miss — L2'ye bak
        if self._l2_enabled and redis_client.is_available():
            l2_val = redis_client.get_json(self._l2_key(key))
            if l2_val is not None:
                with self._lock:
                    try:
                        self._cache[key] = l2_val
                        self._timestamps[key] = time.time()
                    except ValueError:
                        pass
                return l2_val

        return default

    def get_with_meta(self, key: str) -> tuple[Any, bool, Optional[float]]:
        """
        Değer + stale durumu + yaş bilgisi döner.
        Returns: (value, is_stale, age_seconds)

        Stale-while-revalidate: TTL aşılmış ama stale_grace içindeyse
        veri dönülür ve is_stale=True olur.
        """
        # Normal get
        val = self.get(key)
        if val is not None:
            ts = self._timestamps.get(key)
            age = time.time() - ts if ts else None
            is_stale = age is not None and age > self._ttl
            return val, is_stale, age

        # L1 expire olmuş ama stale grace içinde olabilir mi?
        # _timestamps'te hala varsa ve grace süresi içindeyse, stale dön
        ts = self._timestamps.get(key)
        if ts is not None:
            age = time.time() - ts
            if age <= self._ttl + STALE_GRACE_SECONDS:
                # L2'den stale veri çek
                if self._l2_enabled and redis_client.is_available():
                    l2_val = redis_client.get_json(self._l2_key(key))
                    if l2_val is not None:
                        return l2_val, True, age

        return None, False, None

    # ================================================================
    # SET — L1 + L2
    # ================================================================
    def set(self, key: str, value: Any) -> None:
        """Değer yaz. L1 + L2 (fire-and-forget)."""
        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = time.time()

        # L2'ye async-ish yaz (blocking ama hızlı)
        if self._l2_enabled and redis_client.is_available():
            try:
                redis_client.set_json(
                    self._l2_key(key),
                    value,
                    ttl=self._ttl + STALE_GRACE_SECONDS,
                )
            except Exception as e:
                log.debug(f"L2 write failed [{self._namespace}:{key}]: {e}")

    # ================================================================
    # OTHER OPERATIONS
    # ================================================================
    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._cache

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            self._timestamps.pop(key, None)
            return self._cache.pop(key, default)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()

    def stats(self) -> dict[str, Any]:
        """Cache istatistikleri — dashboard için."""
        with self._lock:
            now = time.time()
            ages = [now - ts for ts in self._timestamps.values() if ts > 0]
            return {
                "namespace": self._namespace,
                "size": len(self._cache),
                "maxsize": self._cache.maxsize,
                "ttl": self._ttl,
                "l2_enabled": self._l2_enabled,
                "oldest_age_s": round(max(ages), 1) if ages else None,
                "newest_age_s": round(min(ages), 1) if ages else None,
            }


# ================================================================
# GLOBAL CACHE INSTANCES
# Her modül buradan import eder. Magic number sıfır — config'den gelir.
# ================================================================
raw_cache = SafeCache(RAW_CACHE_SIZE, RAW_CACHE_TTL, "raw")
analysis_cache = SafeCache(ANALYSIS_CACHE_SIZE, ANALYSIS_CACHE_TTL, "analysis")
tech_cache = SafeCache(TECH_CACHE_SIZE, TECH_CACHE_TTL, "tech")
ai_cache = SafeCache(AI_CACHE_SIZE, AI_CACHE_TTL, "ai", l2_enabled=True)
history_cache = SafeCache(HISTORY_CACHE_SIZE, HISTORY_CACHE_TTL, "history", l2_enabled=False)
macro_cache = SafeCache(50, MACRO_CACHE_TTL, "macro")
takas_cache = SafeCache(50, TAKAS_CACHE_TTL, "takas")
social_cache = SafeCache(10, SOCIAL_CACHE_TTL, "social")
briefing_cache = SafeCache(10, BRIEFING_CACHE_TTL, "briefing")
hero_cache = SafeCache(5, HERO_CACHE_TTL, "hero")
agent_cache = SafeCache(100, AGENT_CACHE_TTL, "agent")
heatmap_cache = SafeCache(5, HEATMAP_CACHE_TTL, "heatmap")
macro_ai_cache = SafeCache(5, MACRO_AI_CACHE_TTL, "macro_ai")

# Tüm cache instance'ları — toplu stats ve health check için
ALL_CACHES: list[SafeCache] = [
    raw_cache, analysis_cache, tech_cache, ai_cache, history_cache,
    macro_cache, takas_cache, social_cache, briefing_cache, hero_cache,
    agent_cache, heatmap_cache, macro_ai_cache,
]


def all_cache_stats() -> list[dict[str, Any]]:
    """Tüm cache istatistiklerini döner — /api/health için."""
    return [c.stats() for c in ALL_CACHES]


# ================================================================
# GLOBAL MUTABLE STATE — scan status, top10, briefing history
# Thread-safe erişim. Redis persistence ile Railway restart'a dayanıklı.
# ================================================================
_state_lock = threading.Lock()

_top10_data: dict[str, Any] = {"asof": None, "items": []}
_scan_status: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "progress": 0,
    "total": 0,
    "started": None,
    "scan_id": None,
}
_briefing_history: list[dict] = []


# ================================================================
# TOP10 — scan sonuçları
# ================================================================
def get_top10() -> dict[str, Any]:
    with _state_lock:
        return _top10_data.copy()


def set_top10(asof: Any, items: list) -> None:
    with _state_lock:
        _top10_data["asof"] = asof
        _top10_data["items"] = items
    # L2 persist — Railway restart sonrası restore için
    _persist_top10()


def get_top10_items() -> list:
    with _state_lock:
        return list(_top10_data["items"])


def get_top10_asof() -> Any:
    with _state_lock:
        return _top10_data["asof"]


def _persist_top10() -> None:
    """top10 verisini Redis'e persist et."""
    if not redis_client.is_available():
        return
    try:
        with _state_lock:
            snapshot = {
                "asof": str(_top10_data["asof"]) if _top10_data["asof"] else None,
                "items": _top10_data["items"],
                "persisted_at": time.time(),
            }
        redis_client.save_snapshot(REDIS_SNAPSHOT_KEY, snapshot)
        log.info(
            f"top10 snapshot persisted ({len(snapshot['items'])} items)",
            extra={"phase": "snapshot_persist"},
        )
    except Exception as e:
        log.warning(f"top10 persist failed: {e}")


def restore_top10_from_redis() -> bool:
    """
    Railway restart sonrası Redis'ten top10 restore et.
    app.py lifespan başlangıcında çağrılır.
    Returns True if restored, False otherwise.
    """
    if not redis_client.is_available():
        return False
    try:
        snapshot = redis_client.load_snapshot(REDIS_SNAPSHOT_KEY)
        if snapshot and snapshot.get("items"):
            with _state_lock:
                _top10_data["asof"] = snapshot.get("asof")
                _top10_data["items"] = snapshot["items"]
            log.info(
                f"top10 restored from Redis ({len(snapshot['items'])} items, "
                f"persisted_at={snapshot.get('persisted_at')})",
                extra={"phase": "snapshot_restore"},
            )
            return True
    except Exception as e:
        log.warning(f"top10 restore failed: {e}")
    return False


# ================================================================
# SCAN STATUS
# ================================================================
def get_scan_status() -> dict[str, Any]:
    with _state_lock:
        return _scan_status.copy()


def update_scan_status(**kwargs: Any) -> None:
    with _state_lock:
        _scan_status.update(kwargs)
    # Persist scan status to Redis
    if redis_client.is_available():
        try:
            redis_client.set_json(
                "state:scan_status",
                _scan_status,
                ttl=3600,
            )
        except Exception:
            pass


def increment_scan_progress() -> None:
    with _state_lock:
        _scan_status["progress"] += 1


def restore_scan_status_from_redis() -> bool:
    """Restart sonrası scan status restore — stuck scan tespiti için."""
    if not redis_client.is_available():
        return False
    try:
        status = redis_client.get_json("state:scan_status")
        if status:
            with _state_lock:
                # Restart olduğu için running=True ise False'a çevir
                _scan_status["phase"] = status.get("phase", "idle")
                _scan_status["progress"] = status.get("progress", 0)
                _scan_status["total"] = status.get("total", 0)
                _scan_status["running"] = False
                _scan_status["scan_id"] = status.get("scan_id")
            return True
    except Exception:
        pass
    return False


# ================================================================
# BRIEFING HISTORY
# ================================================================
def append_briefing(entry: dict) -> None:
    with _state_lock:
        _briefing_history.append(entry)
        if len(_briefing_history) > 10:
            _briefing_history.pop(0)
    # Persist to Redis
    if redis_client.is_available():
        try:
            redis_client.set_json(
                "state:briefing_history",
                _briefing_history,
                ttl=86400,
            )
        except Exception:
            pass


def get_briefing_history() -> list[dict]:
    with _state_lock:
        return list(_briefing_history)


def restore_briefing_from_redis() -> bool:
    """Restart sonrası briefing history restore."""
    if not redis_client.is_available():
        return False
    try:
        history = redis_client.get_json("state:briefing_history")
        if history and isinstance(history, list):
            with _state_lock:
                _briefing_history.clear()
                _briefing_history.extend(history[-10:])
            return True
    except Exception:
        pass
    return False


# ================================================================
# STARTUP RESTORE — tüm state'leri Redis'ten yükle
# ================================================================
def restore_all_from_redis() -> dict[str, bool]:
    """
    Tüm persistent state'leri Redis'ten restore et.
    app.py lifespan başlangıcında çağrılır.
    Returns: hangi restore'lar başarılı oldu.
    """
    results = {
        "top10": restore_top10_from_redis(),
        "scan_status": restore_scan_status_from_redis(),
        "briefing": restore_briefing_from_redis(),
    }
    restored_count = sum(1 for v in results.values() if v)
    if restored_count > 0:
        log.info(
            f"Redis restore tamamlandı: {restored_count}/3 başarılı",
            extra={"phase": "startup_restore"},
        )
    else:
        log.info("Redis restore: veri yok veya Redis devre dışı — soğuk başlangıç")
    return results

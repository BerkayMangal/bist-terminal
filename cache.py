# ================================================================
# BISTBULL TERMINAL V9.1 — THREAD-SAFE CACHE
# TTLCache thread-safe DEĞİL. Bu wrapper Lock ile korur.
# Tüm modüller cache'e sadece bu dosya üzerinden erişir.
# ================================================================

from __future__ import annotations

import threading
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
)


class SafeCache:
    """Thread-safe TTLCache wrapper.
    Her get/set/contains/len operasyonu Lock ile korunur.
    """

    __slots__ = ("_cache", "_lock")

    def __init__(self, maxsize: int, ttl: int) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock: threading.Lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._cache.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._cache

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._cache.pop(key, default)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


# ================================================================
# GLOBAL CACHE INSTANCES — tek yerden oluştur, her yerden import et
# ================================================================
raw_cache = SafeCache(RAW_CACHE_SIZE, RAW_CACHE_TTL)
analysis_cache = SafeCache(ANALYSIS_CACHE_SIZE, ANALYSIS_CACHE_TTL)
tech_cache = SafeCache(TECH_CACHE_SIZE, TECH_CACHE_TTL)
ai_cache = SafeCache(AI_CACHE_SIZE, AI_CACHE_TTL)
history_cache = SafeCache(HISTORY_CACHE_SIZE, HISTORY_CACHE_TTL)
macro_cache = SafeCache(50, MACRO_CACHE_TTL)
takas_cache = SafeCache(50, TAKAS_CACHE_TTL)
social_cache = SafeCache(10, SOCIAL_CACHE_TTL)
briefing_cache = SafeCache(10, BRIEFING_CACHE_TTL)
hero_cache = SafeCache(5, HERO_CACHE_TTL)
agent_cache = SafeCache(100, AGENT_CACHE_TTL)
heatmap_cache = SafeCache(5, HEATMAP_CACHE_TTL)
macro_ai_cache = SafeCache(5, MACRO_AI_CACHE_TTL)


# ================================================================
# GLOBAL MUTABLE STATE — scan status, top10, briefing history
# Thread-safe erişim için Lock kullan.
# ================================================================
_state_lock = threading.Lock()

_top10_data: dict = {"asof": None, "items": []}
_scan_status: dict = {
    "running": False,
    "phase": "idle",
    "progress": 0,
    "total": 0,
    "started": None,
}
_briefing_history: list[dict] = []


def get_top10() -> dict:
    with _state_lock:
        return _top10_data.copy()


def set_top10(asof: Any, items: list) -> None:
    with _state_lock:
        _top10_data["asof"] = asof
        _top10_data["items"] = items


def get_top10_items() -> list:
    with _state_lock:
        return list(_top10_data["items"])


def get_top10_asof() -> Any:
    with _state_lock:
        return _top10_data["asof"]


def get_scan_status() -> dict:
    with _state_lock:
        return _scan_status.copy()


def update_scan_status(**kwargs: Any) -> None:
    with _state_lock:
        _scan_status.update(kwargs)


def increment_scan_progress() -> None:
    with _state_lock:
        _scan_status["progress"] += 1


def append_briefing(entry: dict) -> None:
    with _state_lock:
        _briefing_history.append(entry)
        if len(_briefing_history) > 10:
            _briefing_history.pop(0)


def get_briefing_history() -> list[dict]:
    with _state_lock:
        return list(_briefing_history)

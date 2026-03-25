# ================================================================
# BISTBULL TERMINAL V10.0 — RATE LIMITER
# Pahalı endpoint'leri (AI summary, agent, briefing, scan) koruyan
# IP bazlı sliding window rate limiter.
#
# TASARIM:
# - In-memory sliding window — Redis gerektirmez
# - Her endpoint için ayrı limit ve window tanımlanabilir
# - Otomatik eski kayıt temizleme
# - FastAPI middleware veya dependency olarak kullanılabilir
#
# KULLANIM:
#   from core.rate_limiter import check_rate_limit, RateLimitExceeded
#
#   @app.get("/api/agent")
#   async def api_agent(request: Request):
#       check_rate_limit(request, "agent")
#       ...
# ================================================================

from __future__ import annotations

import time
import threading
import logging
from collections import defaultdict, deque
from typing import Any, Optional

from config import (
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_AI_SUMMARY,
    RATE_LIMIT_AI_SUMMARY_WINDOW,
    RATE_LIMIT_AGENT,
    RATE_LIMIT_AGENT_WINDOW,
    RATE_LIMIT_BRIEFING,
    RATE_LIMIT_BRIEFING_WINDOW,
    RATE_LIMIT_SCAN,
    RATE_LIMIT_SCAN_WINDOW,
)

log = logging.getLogger("bistbull.ratelimit")


# ================================================================
# RATE LIMIT CONFIG — endpoint bazlı kurallar
# ================================================================
RATE_LIMITS: dict[str, dict[str, int]] = {
    "ai_summary": {
        "max_requests": RATE_LIMIT_AI_SUMMARY,
        "window_seconds": RATE_LIMIT_AI_SUMMARY_WINDOW,
    },
    "agent": {
        "max_requests": RATE_LIMIT_AGENT,
        "window_seconds": RATE_LIMIT_AGENT_WINDOW,
    },
    "briefing": {
        "max_requests": RATE_LIMIT_BRIEFING,
        "window_seconds": RATE_LIMIT_BRIEFING_WINDOW,
    },
    "scan": {
        "max_requests": RATE_LIMIT_SCAN,
        "window_seconds": RATE_LIMIT_SCAN_WINDOW,
    },
    "macro_commentary": {
        "max_requests": RATE_LIMIT_BRIEFING,
        "window_seconds": RATE_LIMIT_BRIEFING_WINDOW,
    },
    "social": {
        "max_requests": RATE_LIMIT_AI_SUMMARY,
        "window_seconds": RATE_LIMIT_AI_SUMMARY_WINDOW,
    },
}


# ================================================================
# EXCEPTION
# ================================================================
class RateLimitExceeded(Exception):
    """Rate limit aşıldı."""

    def __init__(self, endpoint: str, limit: int, window: int, retry_after: float) -> None:
        self.endpoint = endpoint
        self.limit = limit
        self.window = window
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit aşıldı: {endpoint} — max {limit} istek / {window}s, "
            f"{retry_after:.0f}s sonra tekrar deneyin"
        )


# ================================================================
# SLIDING WINDOW STORE
# ================================================================
_store_lock = threading.Lock()
_store: dict[str, deque[float]] = defaultdict(deque)
_last_cleanup: float = 0.0
_CLEANUP_INTERVAL: float = 60.0


def _make_key(ip: str, endpoint: str) -> str:
    return f"{ip}:{endpoint}"


def _cleanup_old_entries() -> None:
    """Eski kayıtları periyodik olarak temizle — memory leak önleme."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now

    max_window = max(
        (r["window_seconds"] for r in RATE_LIMITS.values()),
        default=300,
    )
    cutoff = now - max_window - 60

    keys_to_delete = []
    for key, timestamps in _store.items():
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()
        if not timestamps:
            keys_to_delete.append(key)

    for key in keys_to_delete:
        del _store[key]


# ================================================================
# CORE CHECK FUNCTION
# ================================================================
def check_rate_limit(request: Any, endpoint: str) -> None:
    """
    Rate limit kontrolü. Aşıldıysa RateLimitExceeded fırlatır.

    Args:
        request: FastAPI Request nesnesi (client.host için)
        endpoint: Rate limit kuralı ismi (ör: "agent", "ai_summary")
    """
    if not RATE_LIMIT_ENABLED:
        return

    rule = RATE_LIMITS.get(endpoint)
    if not rule:
        return

    max_requests = rule["max_requests"]
    window = rule["window_seconds"]

    # IP çıkar
    ip = _extract_ip(request)
    key = _make_key(ip, endpoint)
    now = time.time()
    cutoff = now - window

    with _store_lock:
        _cleanup_old_entries()

        timestamps = _store[key]

        # Window dışı eski kayıtları temizle
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        # Limit kontrolü
        if len(timestamps) >= max_requests:
            oldest = timestamps[0]
            retry_after = oldest + window - now
            log.warning(
                f"Rate limit aşıldı: {ip} → {endpoint} "
                f"({len(timestamps)}/{max_requests} in {window}s)",
                extra={"endpoint": endpoint, "status_code": 429},
            )
            raise RateLimitExceeded(endpoint, max_requests, window, retry_after)

        # Kaydet
        timestamps.append(now)


def _extract_ip(request: Any) -> str:
    """Request nesnesinden IP adresini çıkar."""
    # FastAPI Request
    if hasattr(request, "client") and request.client:
        return request.client.host or "unknown"
    # Proxy arkasında
    if hasattr(request, "headers"):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip", "")
        if real_ip:
            return real_ip.strip()
    return "unknown"


# ================================================================
# STATUS — dashboard ve health endpoint için
# ================================================================
def rate_limit_status() -> dict[str, Any]:
    """Rate limiter durumu ve istatistikleri."""
    with _store_lock:
        now = time.time()
        active_keys = 0
        total_entries = 0
        endpoint_counts: dict[str, int] = defaultdict(int)

        for key, timestamps in _store.items():
            if timestamps:
                active_keys += 1
                total_entries += len(timestamps)
                parts = key.split(":", 1)
                if len(parts) == 2:
                    endpoint_counts[parts[1]] += len(timestamps)

        return {
            "enabled": RATE_LIMIT_ENABLED,
            "active_keys": active_keys,
            "total_entries": total_entries,
            "endpoint_counts": dict(endpoint_counts),
            "rules": {
                name: {
                    "max": rule["max_requests"],
                    "window_s": rule["window_seconds"],
                }
                for name, rule in RATE_LIMITS.items()
            },
        }


def get_remaining(request: Any, endpoint: str) -> dict[str, Any]:
    """
    Belirli bir IP+endpoint için kalan limit bilgisi.
    Response header'larına eklemek için kullanılabilir.
    """
    rule = RATE_LIMITS.get(endpoint)
    if not rule or not RATE_LIMIT_ENABLED:
        return {"limit": 0, "remaining": 0, "reset": 0}

    max_requests = rule["max_requests"]
    window = rule["window_seconds"]
    ip = _extract_ip(request)
    key = _make_key(ip, endpoint)
    now = time.time()
    cutoff = now - window

    with _store_lock:
        timestamps = _store.get(key, deque())
        # Window içindeki kayıt sayısı
        count = sum(1 for ts in timestamps if ts >= cutoff)
        remaining = max(0, max_requests - count)
        # En eski kayıt ne zaman expire olacak
        reset = 0.0
        for ts in timestamps:
            if ts >= cutoff:
                reset = ts + window - now
                break

        return {
            "limit": max_requests,
            "remaining": remaining,
            "reset": round(reset, 1),
            "window": window,
        }

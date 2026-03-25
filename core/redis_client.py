# ================================================================
# BISTBULL TERMINAL V10.0 — REDIS CLIENT
# L2 persistent cache katmanı için Redis bağlantı yönetimi.
#
# TASARIM PRENSİBİ:
# - REDIS_URL varsa → Redis bağlantısı kurulur (L2 aktif)
# - REDIS_URL yoksa → Tüm operasyonlar sessizce None döner (L1-only mod)
# - Redis çökerse → Otomatik degrade, hata loglanır, sistem çalışmaya devam eder
#
# Railway, Redis add-on eklenince REDIS_URL env var'ını otomatik set eder.
# Lokal geliştirmede REDIS_URL boş bırakılabilir — sistem V9.1 gibi RAM-only çalışır.
# ================================================================

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from config import (
    REDIS_URL,
    REDIS_SOCKET_TIMEOUT,
    REDIS_SOCKET_CONNECT_TIMEOUT,
    REDIS_RETRY_ON_TIMEOUT,
    REDIS_MAX_CONNECTIONS,
    REDIS_HEALTH_CHECK_INTERVAL,
    REDIS_KEY_PREFIX,
)

log = logging.getLogger("bistbull.redis")

# ================================================================
# REDIS IMPORT — opsiyonel
# ================================================================
try:
    import redis as _redis_lib
    REDIS_LIB_AVAILABLE = True
except ImportError:
    _redis_lib = None  # type: ignore
    REDIS_LIB_AVAILABLE = False

# ================================================================
# SINGLETON CONNECTION POOL
# ================================================================
_pool: Optional[Any] = None
_client: Optional[Any] = None
_available: bool = False
_last_health_check: float = 0.0
_health_ok: bool = False


def _init_pool() -> bool:
    """Redis connection pool oluştur. Başarısızsa False döner."""
    global _pool, _client, _available

    if not REDIS_URL:
        log.info("REDIS_URL boş — L2 cache devre dışı (RAM-only mod)")
        _available = False
        return False

    if not REDIS_LIB_AVAILABLE:
        log.warning("redis paketi yüklü değil — L2 cache devre dışı")
        _available = False
        return False

    try:
        _pool = _redis_lib.ConnectionPool.from_url(
            REDIS_URL,
            max_connections=REDIS_MAX_CONNECTIONS,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
            retry_on_timeout=REDIS_RETRY_ON_TIMEOUT,
            decode_responses=True,
        )
        _client = _redis_lib.Redis(connection_pool=_pool)
        # Bağlantı testi
        _client.ping()
        _available = True
        log.info("Redis bağlantısı kuruldu — L2 cache aktif")
        return True
    except Exception as e:
        log.error(f"Redis bağlantısı başarısız: {e} — L2 cache devre dışı")
        _pool = None
        _client = None
        _available = False
        return False


def is_available() -> bool:
    """Redis bağlantısı aktif mi?"""
    return _available and _client is not None


def get_client() -> Optional[Any]:
    """Redis client instance'ını döner. Yoksa None."""
    if _available and _client is not None:
        return _client
    return None


# ================================================================
# HEALTH CHECK
# ================================================================
def health_check() -> dict[str, Any]:
    """Redis sağlık durumu. Dashboard ve /api/health için."""
    global _last_health_check, _health_ok

    result: dict[str, Any] = {
        "available": _available,
        "lib_installed": REDIS_LIB_AVAILABLE,
        "url_configured": bool(REDIS_URL),
        "connected": False,
        "latency_ms": None,
        "info": None,
        "error": None,
    }

    if not _available or _client is None:
        return result

    try:
        start = time.monotonic()
        _client.ping()
        latency = round((time.monotonic() - start) * 1000, 1)

        info = _client.info(section="memory")
        result["connected"] = True
        result["latency_ms"] = latency
        result["info"] = {
            "used_memory_human": info.get("used_memory_human", "?"),
            "connected_clients": info.get("connected_clients", "?"),
            "uptime_in_seconds": info.get("uptime_in_seconds", "?"),
        }
        _last_health_check = time.monotonic()
        _health_ok = True
    except Exception as e:
        result["error"] = str(e)
        _health_ok = False
        log.warning(f"Redis health check başarısız: {e}")

    return result


# ================================================================
# CORE OPERATIONS — tüm dış çağrılar try/except sarmalı
# Redis çökerse sessizce None döner, sistem RAM-only'ye düşer.
# ================================================================
def _prefixed(key: str) -> str:
    """Key'e prefix ekle — namespace collision önleme."""
    return f"{REDIS_KEY_PREFIX}{key}"


def get(key: str) -> Optional[str]:
    """Redis'ten string değer oku. Hata durumunda None."""
    if not _available or _client is None:
        return None
    try:
        return _client.get(_prefixed(key))
    except Exception as e:
        log.warning(f"Redis GET hatası [{key}]: {e}")
        return None


def set(key: str, value: str, ttl: Optional[int] = None) -> bool:
    """Redis'e string değer yaz. TTL saniye cinsinden (None = kalıcı). Başarısızsa False."""
    if not _available or _client is None:
        return False
    try:
        if ttl is not None and ttl > 0:
            _client.setex(_prefixed(key), ttl, value)
        else:
            _client.set(_prefixed(key), value)
        return True
    except Exception as e:
        log.warning(f"Redis SET hatası [{key}]: {e}")
        return False


def delete(key: str) -> bool:
    """Redis'ten key sil. Başarısızsa False."""
    if not _available or _client is None:
        return False
    try:
        _client.delete(_prefixed(key))
        return True
    except Exception as e:
        log.warning(f"Redis DELETE hatası [{key}]: {e}")
        return False


def exists(key: str) -> bool:
    """Key Redis'te var mı?"""
    if not _available or _client is None:
        return False
    try:
        return bool(_client.exists(_prefixed(key)))
    except Exception as e:
        log.warning(f"Redis EXISTS hatası [{key}]: {e}")
        return False


def get_json(key: str) -> Optional[Any]:
    """Redis'ten JSON deserialize ederek oku."""
    raw = get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        log.warning(f"Redis JSON parse hatası [{key}]: {e}")
        return None


def set_json(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """Python nesnesini JSON serialize edip Redis'e yaz."""
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        return set(key, serialized, ttl=ttl)
    except (TypeError, ValueError) as e:
        log.warning(f"Redis JSON serialize hatası [{key}]: {e}")
        return False


# ================================================================
# DISTRIBUTED LOCK — scan coordinator için
# ================================================================
def acquire_lock(lock_key: str, ttl: int = 300, value: str = "locked") -> bool:
    """
    Distributed lock al. SET NX EX ile atomik.
    True = lock alındı, False = zaten başkası tutuyor veya Redis yok.
    """
    if not _available or _client is None:
        return False
    try:
        result = _client.set(_prefixed(lock_key), value, nx=True, ex=ttl)
        return result is True
    except Exception as e:
        log.warning(f"Redis LOCK acquire hatası [{lock_key}]: {e}")
        return False


def release_lock(lock_key: str) -> bool:
    """Distributed lock serbest bırak."""
    return delete(lock_key)


def extend_lock(lock_key: str, ttl: int = 300) -> bool:
    """Lock TTL'ini uzat (scan uzun sürerse)."""
    if not _available or _client is None:
        return False
    try:
        return bool(_client.expire(_prefixed(lock_key), ttl))
    except Exception as e:
        log.warning(f"Redis LOCK extend hatası [{lock_key}]: {e}")
        return False


# ================================================================
# SNAPSHOT PERSISTENCE — top10 ve scan sonuçları için
# ================================================================
def save_snapshot(key: str, data: Any, ttl: Optional[int] = None) -> bool:
    """
    Büyük veri snapshot'ını Redis'e yaz.
    Scan sonuçları, top10, heatmap gibi veriler için.
    """
    return set_json(key, data, ttl=ttl)


def load_snapshot(key: str) -> Optional[Any]:
    """
    Snapshot oku. Railway restart sonrası ilk yüklemede kullanılır.
    Cache boşsa Redis'ten restore edilir.
    """
    return get_json(key)


# ================================================================
# STARTUP — uygulama başlangıcında çağrılır
# ================================================================
def startup() -> None:
    """
    Redis bağlantısını kur. app.py lifespan içinden çağrılır.
    Başarısız olursa sistem RAM-only modda çalışmaya devam eder.
    """
    _init_pool()


def shutdown() -> None:
    """
    Redis bağlantısını kapat. app.py shutdown'da çağrılır.
    """
    global _pool, _client, _available
    if _pool is not None:
        try:
            _pool.disconnect()
        except Exception:
            pass
    _pool = None
    _client = None
    _available = False
    log.info("Redis bağlantısı kapatıldı")

# ================================================================
# BISTBULL TERMINAL V10.0 — RESPONSE ENVELOPE
# Tüm API endpoint'leri için standart response yapısı.
#
# FORMAT:
# {
#   "ok": true,
#   "data": { ... },
#   "meta": {
#     "as_of": "2026-03-25T14:30:00Z",
#     "stale": false,
#     "source": "scan_2026_03_25_143000",
#     "latency_ms": 45,
#     "build_version": "V10.0",
#     "cache_status": "hit"
#   },
#   "error": null
# }
#
# NEDEN:
# V9.1'de her endpoint farklı format dönüyordu.
# Frontend neyin taze neyin bayat olduğunu bilemiyordu.
# Bu envelope ile her response:
# - freshness bilgisi taşır (as_of, stale)
# - cache durumunu gösterir (hit/miss/stale_hit)
# - hata durumunda tutarlı format döner
# - build version ile uyumluluk kontrolü yapılabilir
# ================================================================

from __future__ import annotations

import time
import datetime as dt
from typing import Any, Optional

from fastapi.responses import JSONResponse

from config import RESPONSE_BUILD_VERSION
from utils.helpers import clean_for_json


# ================================================================
# ENVELOPE BUILDERS
# ================================================================
def success(
    data: Any,
    as_of: Optional[str] = None,
    stale: bool = False,
    source: Optional[str] = None,
    cache_status: Optional[str] = None,
    latency_ms: Optional[float] = None,
    scan_id: Optional[str] = None,
    extra_meta: Optional[dict[str, Any]] = None,
    status_code: int = 200,
) -> JSONResponse:
    """
    Başarılı response envelope.

    Args:
        data: Response payload (dict, list, veya herhangi bir JSON-serializable nesne)
        as_of: Verinin üretildiği zaman (ISO format string)
        stale: Veri bayat mı?
        source: Verinin kaynağı (ör: "scan_xxx", "cache", "borsapy")
        cache_status: "hit", "miss", "stale_hit", "l2_restore"
        latency_ms: İşlem süresi (ms)
        scan_id: İlişkili scan ID
        extra_meta: Ek metadata alanları
        status_code: HTTP status code (default 200)
    """
    meta: dict[str, Any] = {
        "build_version": RESPONSE_BUILD_VERSION,
    }

    if as_of is not None:
        meta["as_of"] = as_of
    if stale:
        meta["stale"] = True
    if source is not None:
        meta["source"] = source
    if cache_status is not None:
        meta["cache_status"] = cache_status
    if latency_ms is not None:
        meta["latency_ms"] = round(latency_ms, 1)
    if scan_id is not None:
        meta["scan_id"] = scan_id
    if extra_meta:
        meta.update(extra_meta)

    body = {
        "ok": True,
        "data": clean_for_json(data),
        "meta": meta,
        "error": None,
    }

    return JSONResponse(content=body, status_code=status_code)


def error(
    message: str,
    status_code: int = 500,
    error_code: Optional[str] = None,
    detail: Optional[Any] = None,
    retry_after: Optional[float] = None,
) -> JSONResponse:
    """
    Hata response envelope.

    Args:
        message: Kullanıcıya gösterilecek hata mesajı
        status_code: HTTP status code
        error_code: Programatik hata kodu (ör: "RATE_LIMIT", "CB_OPEN", "NOT_FOUND")
        detail: Ek hata detayı
        retry_after: Kaç saniye sonra tekrar denenebilir (429 için)
    """
    error_body: dict[str, Any] = {
        "message": message,
    }
    if error_code is not None:
        error_body["code"] = error_code
    if detail is not None:
        error_body["detail"] = detail
    if retry_after is not None:
        error_body["retry_after"] = round(retry_after, 1)

    body = {
        "ok": False,
        "data": None,
        "meta": {
            "build_version": RESPONSE_BUILD_VERSION,
        },
        "error": error_body,
    }

    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(int(retry_after))

    return JSONResponse(content=body, status_code=status_code, headers=headers or None)


def not_found(message: str = "Kaynak bulunamadı") -> JSONResponse:
    """404 Not Found envelope."""
    return error(message=message, status_code=404, error_code="NOT_FOUND")


def rate_limited(
    message: str = "Çok fazla istek — lütfen bekleyin",
    retry_after: float = 60,
) -> JSONResponse:
    """429 Too Many Requests envelope."""
    return error(
        message=message,
        status_code=429,
        error_code="RATE_LIMIT",
        retry_after=retry_after,
    )


def service_unavailable(
    message: str = "Servis geçici olarak kullanılamıyor",
    provider: Optional[str] = None,
) -> JSONResponse:
    """503 Service Unavailable envelope (CB open durumunda)."""
    detail = {"provider": provider} if provider else None
    return error(
        message=message,
        status_code=503,
        error_code="CB_OPEN",
        detail=detail,
    )


# ================================================================
# HELPER — timestamp üretimi
# ================================================================
def now_iso() -> str:
    """Şu anki UTC zamanı ISO format string olarak döner."""
    return dt.datetime.now(dt.timezone.utc).isoformat()

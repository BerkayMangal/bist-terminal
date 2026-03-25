# ================================================================
# BISTBULL TERMINAL V10.0 — RESPONSE ENVELOPE (V9.1-UYUMLU)
# Tüm API endpoint'leri için meta-zenginleştirilmiş response.
#
# KRİTİK TASARIM KARARI:
# Frontend (index.html) V9.1 formatını bekliyor:
#   {"items": [...], "asof": "..."}
# V10 envelope {"ok": true, "data": {"items": [...]}} formatı
# frontend'i kırar çünkü d.items → undefined olur.
#
# ÇÖZÜM: FLAT FORMAT
# data dict'i üst seviyeye açılır, meta "_meta" key'i ile eklenir:
#   {"items": [...], "asof": "...", "_meta": {"build_version": "V10.0", "stale": false}}
#
# Frontend d.items → çalışır ✅
# V10 meta bilgisi _meta'da mevcut ✅
# İleride frontend güncellenince nested formata geçilebilir ✅
# ================================================================

from __future__ import annotations

import time
import datetime as dt
from typing import Any, Optional

from fastapi.responses import JSONResponse

from config import RESPONSE_BUILD_VERSION
from utils.helpers import clean_for_json


# ================================================================
# SUCCESS — flat format, V9.1 uyumlu
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
    Başarılı response — V9.1 uyumlu flat format.

    data dict ise → key'leri üst seviyeye açılır + _meta eklenir
    data list ise → {"items": data, "_meta": {...}} olarak sarılır
    data scalar ise → {"value": data, "_meta": {...}} olarak sarılır

    Frontend d.items, d.asof, d.commentary gibi erişimlere dokunmaz.
    Meta bilgisi _meta altında her zaman mevcut.
    """
    # Meta oluştur
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

    # Flat format oluştur
    if isinstance(data, dict):
        # Dict → key'leri üst seviyeye aç
        body = clean_for_json(data)
        body["_meta"] = meta
    elif isinstance(data, list):
        # List → items key'ine sar
        body = {"items": clean_for_json(data), "_meta": meta}
    else:
        # Scalar → value key'ine sar
        body = {"value": clean_for_json(data), "_meta": meta}

    # V9.1 uyumluluk: frontend d.asof erişimi bekliyor
    # as_of parametresi varsa üst seviyeye de "asof" key'i olarak ekle
    if as_of is not None and "asof" not in body:
        body["asof"] = as_of

    return JSONResponse(content=body, status_code=status_code)


# ================================================================
# ERROR RESPONSES — flat format, frontend try/catch uyumlu
# ================================================================
def error(
    message: str,
    status_code: int = 500,
    error_code: Optional[str] = None,
    detail: Optional[Any] = None,
    retry_after: Optional[float] = None,
) -> JSONResponse:
    """
    Hata response — flat format.
    Frontend try/catch ile yakaladığı için body formatı çok kritik değil.
    Ama bazı endpoint'ler d.error kontrolü yapıyor, o yüzden error field'ı üst seviyede.
    """
    body: dict[str, Any] = {
        "error": message,
        "_meta": {"build_version": RESPONSE_BUILD_VERSION},
    }
    if error_code is not None:
        body["error_code"] = error_code
    if detail is not None:
        body["detail"] = detail
    if retry_after is not None:
        body["retry_after"] = round(retry_after, 1)

    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(int(retry_after))

    return JSONResponse(content=body, status_code=status_code, headers=headers or None)


def not_found(message: str = "Kaynak bulunamadı") -> JSONResponse:
    """404 Not Found."""
    return error(message=message, status_code=404, error_code="NOT_FOUND")


def rate_limited(
    message: str = "Çok fazla istek — lütfen bekleyin",
    retry_after: float = 60,
) -> JSONResponse:
    """429 Too Many Requests."""
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
    """503 Service Unavailable (CB open durumunda)."""
    detail = {"provider": provider} if provider else None
    return error(
        message=message,
        status_code=503,
        error_code="CB_OPEN",
        detail=detail,
    )


# ================================================================
# HELPER
# ================================================================
def now_iso() -> str:
    """Şu anki UTC zamanı ISO format string olarak döner."""
    return dt.datetime.now(dt.timezone.utc).isoformat()

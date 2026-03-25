# ================================================================
# BISTBULL TERMINAL V10.0 — STRUCTURED LOGGING
# JSON formatında yapılandırılmış log sistemi.
# Her log satırında: timestamp, level, module, request_id, scan_id,
# ticker, provider, duration_ms, cache_status, stale bilgileri.
# Railway log drain ile uyumlu.
#
# V10.0-FIX1: yfinance iç WebSocket logları bastırıldı.
# "Websocket connected" spam'i production loglarını kirletiyordu.
# ================================================================

from __future__ import annotations

import logging
import json
import time
import uuid
import contextvars
from typing import Any, Optional

from config import BOT_VERSION

# ================================================================
# CONTEXT VARIABLES — request/scan bazlı korelasyon
# ================================================================
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_scan_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("scan_id", default="")


def set_request_id(rid: str) -> None:
    _request_id_var.set(rid)


def get_request_id() -> str:
    return _request_id_var.get("")


def set_scan_id(sid: str) -> None:
    _scan_id_var.set(sid)


def get_scan_id() -> str:
    return _scan_id_var.get("")


def generate_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:12]
    return f"{prefix}{short}" if prefix else short


# ================================================================
# YFINANCE WEBSOCKET FILTER — "Websocket connected" spam'ini engeller
# ================================================================
class YFinanceWebSocketFilter(logging.Filter):
    """
    yfinance'ın dahili WebSocket mesajlarını filtreler.
    Bu mesajlar TradingView bağlantı lifecycle'ından geliyor
    ve production loglarında hiçbir değer taşımıyor.

    Filtrelenen mesaj pattern'leri:
    - "Websocket connected"
    - "Handshake status 429"
    - "- goodbye"
    """

    _BLOCKED_PATTERNS: list[str] = [
        "Websocket connected",
        "Handshake status",
        "- goodbye",
        "WebSocket",
        "websocket",
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern in self._BLOCKED_PATTERNS:
            if pattern in msg:
                return False
        return True


# ================================================================
# JSON FORMATTER — her log satırı tek JSON object
# ================================================================
class JSONFormatter(logging.Formatter):

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "version": BOT_VERSION,
        }

        # Context variables — korelasyon alanları
        req_id = get_request_id()
        scan_id = get_scan_id()
        if req_id:
            log_entry["request_id"] = req_id
        if scan_id:
            log_entry["scan_id"] = scan_id

        # Extra alanlar — log.info("msg", extra={...}) ile gelir
        extra_keys = [
            "ticker", "provider", "phase", "duration_ms",
            "cache_status", "stale", "endpoint", "error_code",
            "error_class", "symbols_count", "success_count",
            "fail_count", "latency_ms", "status_code",
        ]
        for key in extra_keys:
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        # Exception bilgisi
        if record.exc_info and record.exc_info[1]:
            log_entry["error_class"] = record.exc_info[1].__class__.__name__
            log_entry["error"] = str(record.exc_info[1])

        return json.dumps(log_entry, ensure_ascii=False, default=str)


# ================================================================
# SETUP — uygulama başlangıcında bir kere çağrılır
# ================================================================
_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(level)

    # Mevcut handler'ları temizle
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    # JSON handler — stdout'a yazar (Railway bunu yakalar)
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))

    # yfinance WebSocket spam filtresi — root handler'a eklenir
    handler.addFilter(YFinanceWebSocketFilter())

    root.addHandler(handler)

    # Gürültücü kütüphaneleri sustur
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("borsapy").setLevel(logging.WARNING)

    # yfinance — dahili WebSocket ve HTTP loglarını bastır
    # "Websocket connected" ve "Handshake status 429" mesajları buradan gelir
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("yfinance.base").setLevel(logging.WARNING)
    logging.getLogger("yfinance.multi").setLevel(logging.WARNING)
    logging.getLogger("yfinance.utils").setLevel(logging.WARNING)
    logging.getLogger("yfinance.data").setLevel(logging.WARNING)
    logging.getLogger("yfinance.screener").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ================================================================
# TIMER HELPER — duration_ms ölçümü için context manager
# ================================================================
class LogTimer:
    """
    Kullanım:
        with LogTimer() as t:
            do_work()
        log.info("done", extra={"duration_ms": t.ms})
    """

    __slots__ = ("_start", "ms")

    def __init__(self) -> None:
        self._start: float = 0.0
        self.ms: float = 0.0

    def __enter__(self) -> "LogTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        self.ms = round((time.monotonic() - self._start) * 1000, 1)

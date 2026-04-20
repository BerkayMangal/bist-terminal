# ================================================================
# BISTBULL TERMINAL V10.0 — CIRCUIT BREAKER
# Dış kaynak çağrılarını (borsapy, yfinance, AI provider'lar)
# koruyan 3-durumlu devre kesici.
#
# DURUM MAKİNESİ:
#   CLOSED ──[ardışık hata >= threshold]──► OPEN
#     ▲                                        │
#     │                             [recovery_timeout doldu]
#     │                                        ▼
#     └─────[success_threshold kadar başarı]── HALF_OPEN
#
# CLOSED  : Normal çalışma, çağrılar geçer. Hatalar sayılır.
# OPEN    : Devre açık, çağrılar yapılmaz, anında hata döner.
#           recovery_timeout sonra HALF_OPEN'a geçer.
# HALF_OPEN: Sınırlı sayıda test çağrısı yapılır.
#           Başarılı olursa CLOSED'a döner.
#           Başarısız olursa OPEN'a geri döner.
#
# Her dış kaynak (borsapy, yfinance, grok, openai, anthropic)
# ayrı bir CircuitBreaker instance'ına sahiptir.
# ================================================================

from __future__ import annotations

import time
import threading
import logging
from typing import Any, Callable, Optional, TypeVar
from enum import Enum

from config import (
    CB_FAILURE_THRESHOLD,
    CB_RECOVERY_TIMEOUT,
    CB_HALF_OPEN_MAX_CALLS,
    CB_SUCCESS_THRESHOLD,
    CB_BORSAPY_FAILURE_THRESHOLD,
    CB_BORSAPY_RECOVERY_TIMEOUT,
    CB_YFINANCE_FAILURE_THRESHOLD,
    CB_YFINANCE_RECOVERY_TIMEOUT,
    CB_AI_FAILURE_THRESHOLD,
    CB_AI_RECOVERY_TIMEOUT,
)

log = logging.getLogger("bistbull.cb")

T = TypeVar("T")


# ================================================================
# STATES
# ================================================================
class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# ================================================================
# CIRCUIT BREAKER ERROR
# ================================================================
class CircuitBreakerOpen(Exception):
    """Devre açık — çağrı yapılmadı."""

    def __init__(self, name: str, remaining: float) -> None:
        self.name = name
        self.remaining = remaining
        super().__init__(
            f"Circuit breaker '{name}' OPEN — {remaining:.0f}s kaldı"
        )


# ================================================================
# CIRCUIT BREAKER
# ================================================================
class CircuitBreaker:
    """
    Tek bir dış kaynak için devre kesici.

    Kullanım:
        cb = CircuitBreaker("borsapy", failure_threshold=8)

        # Yöntem 1: Decorator
        @cb.protect
        def fetch_data():
            return borsapy.Ticker("THYAO").fast_info

        # Yöntem 2: Manuel
        try:
            cb.before_call()
            result = risky_function()
            cb.on_success()
            return result
        except CircuitBreakerOpen:
            return cached_fallback()
        except Exception as e:
            cb.on_failure()
            raise
    """

    __slots__ = (
        "name",
        "_failure_threshold",
        "_recovery_timeout",
        "_half_open_max",
        "_success_threshold",
        "_state",
        "_failure_count",
        "_success_count",
        "_last_failure_time",
        "_half_open_calls",
        "_lock",
        "_total_calls",
        "_total_failures",
        "_total_successes",
        "_total_rejections",
        "_last_error",
        "_last_success_time",
        "_state_changed_at",
    )

    def __init__(
        self,
        name: str,
        failure_threshold: int = CB_FAILURE_THRESHOLD,
        recovery_timeout: int = CB_RECOVERY_TIMEOUT,
        half_open_max_calls: int = CB_HALF_OPEN_MAX_CALLS,
        success_threshold: int = CB_SUCCESS_THRESHOLD,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max_calls
        self._success_threshold = success_threshold

        # Mutable state
        self._state: CBState = CBState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._last_failure_time: float = 0.0
        self._half_open_calls: int = 0
        self._lock: threading.Lock = threading.Lock()

        # Telemetry
        self._total_calls: int = 0
        self._total_failures: int = 0
        self._total_successes: int = 0
        self._total_rejections: int = 0
        self._last_error: Optional[str] = None
        self._last_success_time: float = 0.0
        self._state_changed_at: float = time.monotonic()

    # ================================================================
    # STATE TRANSITIONS
    # ================================================================
    def _to_open(self) -> None:
        prev = self._state
        self._state = CBState.OPEN
        self._state_changed_at = time.monotonic()
        self._half_open_calls = 0
        self._success_count = 0
        log.warning(
            f"CB [{self.name}] {prev.value} → OPEN "
            f"(failures: {self._failure_count}/{self._failure_threshold})",
            extra={"provider": self.name, "phase": "cb_open"},
        )

    def _to_half_open(self) -> None:
        prev = self._state
        self._state = CBState.HALF_OPEN
        self._state_changed_at = time.monotonic()
        self._half_open_calls = 0
        self._success_count = 0
        log.info(
            f"CB [{self.name}] {prev.value} → HALF_OPEN",
            extra={"provider": self.name, "phase": "cb_half_open"},
        )

    def _to_closed(self) -> None:
        prev = self._state
        self._state = CBState.CLOSED
        self._state_changed_at = time.monotonic()
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        log.info(
            f"CB [{self.name}] {prev.value} → CLOSED (recovered)",
            extra={"provider": self.name, "phase": "cb_closed"},
        )

    # ================================================================
    # CALL LIFECYCLE
    # ================================================================
    def before_call(self) -> None:
        """
        Çağrı öncesi kontrol. Devre açıksa CircuitBreakerOpen fırlatır.
        HALF_OPEN durumda max call sayısı aşıldıysa yine reject eder.
        """
        with self._lock:
            self._total_calls += 1

            if self._state == CBState.CLOSED:
                return

            if self._state == CBState.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self._recovery_timeout:
                    self._to_half_open()
                    self._half_open_calls += 1
                    return
                else:
                    self._total_rejections += 1
                    remaining = self._recovery_timeout - elapsed
                    raise CircuitBreakerOpen(self.name, remaining)

            if self._state == CBState.HALF_OPEN:
                if self._half_open_calls >= self._half_open_max:
                    self._total_rejections += 1
                    raise CircuitBreakerOpen(self.name, 0)
                self._half_open_calls += 1
                return

    def on_success(self) -> None:
        """Çağrı başarılı oldu."""
        with self._lock:
            self._total_successes += 1
            self._last_success_time = time.monotonic()

            if self._state == CBState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._success_threshold:
                    self._to_closed()
            elif self._state == CBState.CLOSED:
                self._failure_count = 0

    def on_failure(self, error: Optional[Exception] = None) -> None:
        """Çağrı başarısız oldu."""
        with self._lock:
            # OPT: Rate limit (429) hataları normal — CB'yi tetiklemesin
            is_rate_limit = error and ('429' in str(error) or 'rate' in str(error).lower() or 'Too Many' in str(error))
            if is_rate_limit:
                self._total_failures += 1
                return  # Rate limit → CB state değiştirme
            self._total_failures += 1
            self._last_failure_time = time.monotonic()
            self._last_error = str(error) if error else None

            if self._state == CBState.HALF_OPEN:
                self._to_open()
            elif self._state == CBState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._to_open()

    # ================================================================
    # PROTECT DECORATOR
    # ================================================================
    def protect(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Fonksiyonu Circuit Breaker ile sarmala.

        @cb_borsapy.protect
        def fetch_raw(symbol):
            return borsapy.Ticker(symbol).fast_info
        """
        def wrapper(*args: Any, **kwargs: Any) -> T:
            self.before_call()
            try:
                result = func(*args, **kwargs)
                self.on_success()
                return result
            except CircuitBreakerOpen:
                raise
            except Exception as e:
                self.on_failure(e)
                raise
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    # ================================================================
    # CALL HELPER — fonksiyonu doğrudan çağır
    # ================================================================
    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Fonksiyonu CB koruması altında çağır.

        result = cb.call(borsapy.Ticker, symbol)
        """
        self.before_call()
        try:
            result = func(*args, **kwargs)
            self.on_success()
            return result
        except CircuitBreakerOpen:
            raise
        except Exception as e:
            self.on_failure(e)
            raise

    # ================================================================
    # STATUS & TELEMETRY
    # ================================================================
    @property
    def state(self) -> str:
        return self._state.value

    @property
    def is_closed(self) -> bool:
        with self._lock:
            if self._state == CBState.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self._recovery_timeout:
                    return False
            return self._state == CBState.CLOSED

    @property
    def is_open(self) -> bool:
        return self._state == CBState.OPEN

    def status(self) -> dict[str, Any]:
        """Tam durum raporu — /api/health ve dashboard için."""
        with self._lock:
            now = time.monotonic()
            state_age = round(now - self._state_changed_at, 1)
            remaining = 0.0
            if self._state == CBState.OPEN and self._last_failure_time > 0:
                remaining = max(0, self._recovery_timeout - (now - self._last_failure_time))

            return {
                "name": self.name,
                "state": self._state.value,
                "state_age_s": state_age,
                "failure_count": self._failure_count,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
                "remaining_s": round(remaining, 1),
                "half_open_calls": self._half_open_calls,
                "half_open_max": self._half_open_max,
                "total_calls": self._total_calls,
                "total_successes": self._total_successes,
                "total_failures": self._total_failures,
                "total_rejections": self._total_rejections,
                "last_error": self._last_error,
                "last_success_ago_s": (
                    round(now - self._last_success_time, 1)
                    if self._last_success_time > 0
                    else None
                ),
                "last_failure_ago_s": (
                    round(now - self._last_failure_time, 1)
                    if self._last_failure_time > 0
                    else None
                ),
            }

    def reset(self) -> None:
        """Manuel reset — debug/admin endpoint için."""
        with self._lock:
            self._to_closed()
            self._total_rejections = 0
            log.info(f"CB [{self.name}] manuel reset yapıldı")


# ================================================================
# GLOBAL INSTANCES — her dış kaynak için ayrı CB
# ================================================================
cb_borsapy = CircuitBreaker(
    name="borsapy",
    failure_threshold=CB_BORSAPY_FAILURE_THRESHOLD,
    recovery_timeout=CB_BORSAPY_RECOVERY_TIMEOUT,
)

cb_yfinance = CircuitBreaker(
    name="yfinance",
    failure_threshold=CB_YFINANCE_FAILURE_THRESHOLD,
    recovery_timeout=CB_YFINANCE_RECOVERY_TIMEOUT,
)

cb_perplexity = CircuitBreaker(
    name="perplexity",
    failure_threshold=CB_AI_FAILURE_THRESHOLD,
    recovery_timeout=CB_AI_RECOVERY_TIMEOUT,
)

cb_grok = CircuitBreaker(
    name="grok",
    failure_threshold=CB_AI_FAILURE_THRESHOLD,
    recovery_timeout=CB_AI_RECOVERY_TIMEOUT,
)

cb_openai = CircuitBreaker(
    name="openai",
    failure_threshold=CB_AI_FAILURE_THRESHOLD,
    recovery_timeout=CB_AI_RECOVERY_TIMEOUT,
)

cb_anthropic = CircuitBreaker(
    name="anthropic",
    failure_threshold=CB_AI_FAILURE_THRESHOLD,
    recovery_timeout=CB_AI_RECOVERY_TIMEOUT,
)

ALL_CIRCUIT_BREAKERS: list[CircuitBreaker] = [
    cb_borsapy,
    cb_yfinance,
    cb_perplexity,
    cb_grok,
    cb_openai,
    cb_anthropic,
]


def all_provider_status() -> dict[str, dict[str, Any]]:
    """Tüm provider'ların CB durumunu döner — /api/health için."""
    return {cb.name: cb.status() for cb in ALL_CIRCUIT_BREAKERS}

# ================================================================
# BISTBULL TERMINAL V10.0 — SCAN COORDINATOR
# Tek aktif scan kuralını uygulayan merkezi orkestrasyon.
#
# TASARIM PRENSİPLERİ:
# 1. Aynı anda sadece 1 scan çalışır (Redis lock veya threading.Lock)
# 2. Scan fazlara ayrılmıştır — her faz bağımsız izlenebilir
# 3. Phase ilerlemesi Redis'e ve WebSocket'e yayınlanır
# 4. Scan tamamlanınca snapshot persist edilir
# 5. Startup'ta önceki stuck scan temizlenir
#
# SCAN FAZLARI:
#   prep → raw_fetch → history_fetch → technical_compute →
#   scoring → snapshot_publish → ai_enrich → done
#
# KULLANIM:
#   coordinator = ScanCoordinator()
#   scan_id = coordinator.start_scan(universe, analyze_fn, history_fn, ...)
#   # veya
#   if coordinator.is_running:
#       return coordinator.current_scan_id  # zaten çalışıyor
# ================================================================

from __future__ import annotations

import time
import threading
import logging
from typing import Any, Callable, Optional

from config import (
    SCAN_PHASES,
    SCAN_MAX_WORKERS,
    REDIS_SCAN_LOCK_KEY,
    REDIS_SCAN_LOCK_TTL,
    CONFIDENCE_MIN,
)
from core import redis_client
from core.cache import (
    set_top10,
    update_scan_status,
    increment_scan_progress,
)
from core.logging_config import generate_id, set_scan_id

log = logging.getLogger("bistbull.scan")


class ScanCoordinator:
    """
    Merkezi scan orkestratörü.

    Sorumlulukları:
    - Tek aktif scan garantisi (Redis distributed lock veya local lock)
    - Phase bazlı ilerleme takibi
    - Scan sonucu snapshot olarak persist etme
    - WebSocket üzerinden canlı progress yayını
    """

    __slots__ = (
        "_local_lock",
        "_running",
        "_current_scan_id",
        "_current_phase",
        "_progress",
        "_total",
        "_started_at",
        "_phase_times",
        "_ws_clients",
        "_ws_lock",
        "_scan_count",
        "_last_scan_duration",
    )

    def __init__(self) -> None:
        self._local_lock: threading.Lock = threading.Lock()
        self._running: bool = False
        self._current_scan_id: Optional[str] = None
        self._current_phase: str = "idle"
        self._progress: int = 0
        self._total: int = 0
        self._started_at: Optional[float] = None
        self._phase_times: dict[str, float] = {}
        self._ws_clients: list[Any] = []
        self._ws_lock: threading.Lock = threading.Lock()
        self._scan_count: int = 0
        self._last_scan_duration: Optional[float] = None

    # ================================================================
    # PROPERTIES
    # ================================================================
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_scan_id(self) -> Optional[str]:
        return self._current_scan_id

    @property
    def current_phase(self) -> str:
        return self._current_phase

    @property
    def progress(self) -> int:
        return self._progress

    @property
    def total(self) -> int:
        return self._total

    # ================================================================
    # LOCK ACQUISITION — Redis first, local fallback
    # ================================================================
    def _acquire_lock(self) -> bool:
        """Scan lock al. Redis varsa distributed, yoksa local."""
        # Redis distributed lock dene
        if redis_client.is_available():
            if redis_client.acquire_lock(REDIS_SCAN_LOCK_KEY, ttl=REDIS_SCAN_LOCK_TTL):
                return True
            return False
        # Local fallback
        return self._local_lock.acquire(blocking=False)

    def _release_lock(self) -> None:
        """Scan lock serbest bırak."""
        if redis_client.is_available():
            redis_client.release_lock(REDIS_SCAN_LOCK_KEY)
        else:
            try:
                self._local_lock.release()
            except RuntimeError:
                pass

    def _extend_lock(self) -> None:
        """Uzun scan'lerde lock TTL'ini uzat."""
        if redis_client.is_available():
            redis_client.extend_lock(REDIS_SCAN_LOCK_KEY, ttl=REDIS_SCAN_LOCK_TTL)

    # ================================================================
    # PHASE MANAGEMENT
    # ================================================================
    def _set_phase(self, phase: str) -> None:
        """Phase değiştir ve her yere yayınla."""
        prev = self._current_phase
        self._current_phase = phase
        self._phase_times[phase] = time.time()

        update_scan_status(
            running=self._running,
            phase=phase,
            progress=self._progress,
            total=self._total,
            scan_id=self._current_scan_id,
            started=self._started_at,
        )

        log.info(
            f"Scan phase: {prev} → {phase}",
            extra={
                "phase": phase,
                "scan_id": self._current_scan_id,
                "progress": self._progress,
                "total": self._total,
            },
        )

        self._broadcast_progress()

    def _increment(self) -> None:
        """İlerleme sayacını artır."""
        self._progress += 1
        increment_scan_progress()
        # Her 10 hissede bir WS broadcast (flood önleme)
        if self._progress % 10 == 0 or self._progress == self._total:
            self._broadcast_progress()

    # ================================================================
    # WEBSOCKET MANAGEMENT
    # ================================================================
    def register_ws(self, ws: Any) -> None:
        """WebSocket client kaydet."""
        with self._ws_lock:
            self._ws_clients.append(ws)

    def unregister_ws(self, ws: Any) -> None:
        """WebSocket client çıkar."""
        with self._ws_lock:
            self._ws_clients = [w for w in self._ws_clients if w is not ws]

    def _broadcast_progress(self) -> None:
        """Tüm WS client'lara progress yayınla."""
        with self._ws_lock:
            if not self._ws_clients:
                return
            msg = self.get_progress()
            dead = []
            for ws in self._ws_clients:
                try:
                    # Bu senkron çağrı — asyncio.run_coroutine_threadsafe gerekebilir
                    # app.py'de async wrapper ile kullanılacak
                    ws._send_queue.append(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._ws_clients.remove(ws)

    # ================================================================
    # PROGRESS STATUS
    # ================================================================
    def get_progress(self) -> dict[str, Any]:
        """Anlık scan durumu — WS ve /api/scan-status için."""
        elapsed = None
        if self._started_at and self._running:
            elapsed = round(time.time() - self._started_at, 1)

        return {
            "running": self._running,
            "scan_id": self._current_scan_id,
            "phase": self._current_phase,
            "progress": self._progress,
            "total": self._total,
            "elapsed_s": elapsed,
            "scan_count": self._scan_count,
            "last_scan_duration_s": self._last_scan_duration,
        }

    # ================================================================
    # MAIN SCAN ENTRY POINT
    # ================================================================
    def start_scan(
        self,
        universe: list[str],
        analyze_fn: Callable[[str], Optional[dict]],
        history_fn: Optional[Callable[[list[str]], dict]] = None,
        cross_fn: Optional[Callable[[dict], Any]] = None,
        ai_enrich_fn: Optional[Callable[[list[dict]], None]] = None,
    ) -> Optional[str]:
        """
        Tam scan çalıştır. Blocking call — background thread'den çağrılmalı.

        Args:
            universe: Hisse listesi (ör: ["THYAO", "AKBNK", ...])
            analyze_fn: Tek hisseyi analiz eden fonksiyon (symbol → dict veya None)
            history_fn: Toplu fiyat geçmişi indiren fonksiyon (symbols → {sym: df})
            cross_fn: Cross Hunter sinyal tarayıcı (history_map → signals)
            ai_enrich_fn: AI zenginleştirme (top items → None, side effect)

        Returns:
            scan_id if started, None if another scan is already running.
        """
        # 1. Lock al
        if not self._acquire_lock():
            log.info(
                "Scan zaten çalışıyor, skip",
                extra={"scan_id": self._current_scan_id},
            )
            return None

        scan_id = generate_id("scan_")
        self._current_scan_id = scan_id
        self._running = True
        self._started_at = time.time()
        self._progress = 0
        self._total = len(universe)
        self._phase_times = {}
        set_scan_id(scan_id)

        try:
            # ---- PHASE: prep ----
            self._set_phase("prep")

            # ---- PHASE: history_fetch ----
            history_map: dict = {}
            if history_fn is not None:
                self._set_phase("history_fetch")
                try:
                    history_map = history_fn(universe)
                    log.info(
                        f"History fetch: {len(history_map)} symbols",
                        extra={"scan_id": scan_id, "symbols_count": len(history_map)},
                    )
                except Exception as e:
                    log.error(f"History fetch hatası: {e}", extra={"scan_id": scan_id})

            # Lock uzat — history fetch uzun sürebilir
            self._extend_lock()

            # ---- PHASE: scoring (raw_fetch + technical + scoring combined) ----
            self._set_phase("scoring")
            ranked: list[dict] = []
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _safe_analyze(ticker: str) -> Optional[dict]:
                try:
                    result = analyze_fn(ticker)
                    self._increment()
                    return result
                except Exception as e:
                    self._increment()
                    log.warning(f"Scan skip {ticker}: {e}")
                    return None

            workers = min(SCAN_MAX_WORKERS, len(universe))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_safe_analyze, t): t for t in universe}
                for future in as_completed(futures):
                    r = future.result()
                    if r and r.get("confidence", 0) >= CONFIDENCE_MIN:
                        ranked.append(r)

            ranked.sort(
                key=lambda x: (x.get("overall", 0), x.get("scores", {}).get("quality", 0)),
                reverse=True,
            )

            # Lock uzat — snapshot publish öncesi
            self._extend_lock()

            # ---- PHASE: snapshot_publish ----
            self._set_phase("snapshot_publish")
            import datetime as dt

            set_top10(dt.datetime.now(dt.timezone.utc), ranked)
            log.info(
                f"Snapshot published: {len(ranked)} hisse",
                extra={
                    "scan_id": scan_id,
                    "success_count": len(ranked),
                    "total": len(universe),
                },
            )

            # ---- PHASE: cross signals (opsiyonel) ----
            if cross_fn is not None and history_map:
                try:
                    cross_fn(history_map)
                except Exception as e:
                    log.warning(f"Cross hunter hatası: {e}", extra={"scan_id": scan_id})

            # ---- PHASE: ai_enrich (opsiyonel) ----
            if ai_enrich_fn is not None and ranked:
                self._set_phase("ai_enrich")
                try:
                    ai_enrich_fn(ranked)
                except Exception as e:
                    log.warning(f"AI enrich hatası: {e}", extra={"scan_id": scan_id})

            # ---- PHASE: done ----
            duration = round(time.time() - self._started_at, 1)
            self._last_scan_duration = duration
            self._scan_count += 1
            self._set_phase("done")

            log.info(
                f"Scan tamamlandı: {len(ranked)}/{len(universe)} hisse, {duration}s",
                extra={
                    "scan_id": scan_id,
                    "success_count": len(ranked),
                    "total": len(universe),
                    "duration_ms": duration * 1000,
                },
            )

            return scan_id

        except Exception as e:
            self._set_phase("error")
            log.error(
                f"Scan hatası: {e}",
                extra={"scan_id": scan_id, "error_class": type(e).__name__},
            )
            raise

        finally:
            self._running = False
            update_scan_status(
                running=False,
                phase=self._current_phase,
                progress=self._progress,
                total=self._total,
                scan_id=scan_id,
            )
            self._release_lock()
            set_scan_id("")

    # ================================================================
    # TELEMETRY
    # ================================================================
    def status(self) -> dict[str, Any]:
        """Tam coordinator durumu — /api/health için."""
        return {
            "running": self._running,
            "scan_id": self._current_scan_id,
            "phase": self._current_phase,
            "progress": self._progress,
            "total": self._total,
            "scan_count": self._scan_count,
            "last_scan_duration_s": self._last_scan_duration,
            "phase_times": {
                phase: round(t, 1)
                for phase, t in self._phase_times.items()
            },
            "ws_clients": len(self._ws_clients),
        }


# ================================================================
# GLOBAL SINGLETON
# ================================================================
scan_coordinator = ScanCoordinator()

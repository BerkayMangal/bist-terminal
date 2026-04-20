# ================================================================
# BISTBULL TERMINAL V10.0 — SIGNAL TRACKER & PAPER TRADE ENGINE
# engine/signal_tracker.py
#
# GÖREV: CrossHunter sinyallerini yakala, giriş fiyatıyla kaydet,
# arka planda TP/SL takibi yap, Track Record istatistiklerini üret.
#
# PERSISTENCE STRATEJİSİ (3 katman, en güvenilirden en az güvenilire):
#   1. Redis  → Railway restart'larda hayatta kalır (en güvenilir)
#   2. signals_log.json → Redis yoksa JSON dosyası (fallback)
#   3. RAM   → Her ikisi de yoksa in-memory (test ortamı)
#
# THREAD SAFETY: Her okuma/yazma threading.Lock ile korunur.
# ATOMIC WRITES: JSON dosyası .tmp → rename ile yazılır (çökme koruması).
#
# SINYAL YAŞAM DÖNGÜSÜ:
#   CrossHunter üretir → log_signals() → status: "active"
#   Background task her 5dk → update_prices() → TP / SL kontrolü
#   Frontend → /api/signals/track-record → get_track_record()
# ================================================================

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import uuid
from typing import Optional

log = logging.getLogger("bistbull.signal_tracker")

# ================================================================
# SABITLER
# ================================================================

# Zaman dilimine göre TP/SL hedefleri
# CrossHunter şu an sadece günlük (1G) data üzerinden çalışır.
# İleride 15m/60m tarama eklenince bu parametreler devreye girer.
TIMEFRAME_PARAMS: dict[str, dict] = {
    "15m":  {"tp_pct": 0.020, "sl_pct": -0.010, "label": "15 Dakika",  "max_age_hours": 8},
    "60m":  {"tp_pct": 0.040, "sl_pct": -0.020, "label": "60 Dakika",  "max_age_hours": 72},
    "1G":   {"tp_pct": 0.030, "sl_pct": -0.020, "label": "Günlük",     "max_age_hours": 336},
}
DEFAULT_TIMEFRAME = "1G"

# signals_log.json konumu: app.py ile aynı dizin
_APP_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
DEFAULT_LOG_PATH = os.path.join(_APP_DIR, "signals_log.json")

# Redis key
REDIS_SIGNALS_KEY = "bb:signals_log"

# Maksimum kayıt sayısı (RAM şişmesini önler)
MAX_SIGNALS_IN_MEMORY = 2000

# Kaç günden eski kapalı pozisyonları sil
PURGE_AFTER_DAYS = 90

# ================================================================
# SIGNAL TRACKER SINIFI
# ================================================================

class SignalTracker:
    """
    CrossHunter sinyallerini takip eden, TP/SL kontrolü yapan,
    Track Record istatistikleri üreten deterministik motor.
    """

    def __init__(self, log_path: str = DEFAULT_LOG_PATH) -> None:
        self._lock = threading.Lock()
        self._log_path = log_path
        self._signals: list[dict] = []
        self._load()

    # ----------------------------------------------------------------
    # PERSISTENCE — LOAD
    # ----------------------------------------------------------------

    def _load(self) -> None:
        """
        Önce Redis'ten yükle, bulamazsa JSON dosyasından yükle.
        Her iki kaynak da yoksa boş başla.
        """
        # 1. Redis dene
        try:
            from core import redis_client
            raw = redis_client.get_json(REDIS_SIGNALS_KEY)
            if raw and isinstance(raw, list):
                self._signals = raw
                log.info(f"SignalTracker: {len(self._signals)} sinyal Redis'ten yüklendi")
                return
        except Exception as e:
            log.debug(f"SignalTracker Redis load atlandı: {e}")

        # 2. JSON dosyasından dene
        try:
            if os.path.exists(self._log_path):
                with open(self._log_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._signals = data
                    log.info(
                        f"SignalTracker: {len(self._signals)} sinyal "
                        f"JSON dosyasından yüklendi ({self._log_path})"
                    )
                    return
        except Exception as e:
            log.warning(f"SignalTracker JSON load hatası: {e}")

        # 3. Boş başla
        self._signals = []
        log.info("SignalTracker: Yeni başlatıldı (kayıt yok)")

    # ----------------------------------------------------------------
    # PERSISTENCE — SAVE
    # ----------------------------------------------------------------

    def _save(self) -> None:
        """
        Mevcut sinyalleri hem Redis'e hem JSON dosyasına yazar.
        Lock altında çağrılır.
        """
        # Memory limiti
        if len(self._signals) > MAX_SIGNALS_IN_MEMORY:
            # En eski kapalı pozisyonları çıkar, aktifler her zaman korunur
            closed = [s for s in self._signals if s["status"] != "active"]
            active = [s for s in self._signals if s["status"] == "active"]
            closed.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            self._signals = active + closed[: MAX_SIGNALS_IN_MEMORY - len(active)]

        # 1. Redis'e yaz
        try:
            from core import redis_client
            redis_client.set_json(REDIS_SIGNALS_KEY, self._signals)
        except Exception as e:
            log.debug(f"SignalTracker Redis save atlandı: {e}")

        # 2. JSON dosyasına atomic write
        try:
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            tmp_path = self._log_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._signals, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp_path, self._log_path)  # atomic rename
        except Exception as e:
            log.warning(f"SignalTracker JSON save hatası: {e}")

    # ----------------------------------------------------------------
    # PUBLIC: SİNYAL KAYIT
    # ----------------------------------------------------------------

    def log_signals(
        self,
        signals: list[dict],
        timeframe: str = DEFAULT_TIMEFRAME,
    ) -> int:
        """
        CrossHunter'dan gelen sinyalleri kaydet.

        Kurallar:
        - Fiyatı olmayan sinyaller atlanır
        - Bearish sinyaller atlanır (short desteklenmez)
        - Aynı (ticker, signal) kombinasyonu zaten aktifse tekrar eklenmez
        - Yeni eklenen her sinyal entry_price, tp, sl ile kaydedilir

        Args:
            signals: cross_hunter.scan_all() çıktısı
            timeframe: "15m", "60m", "1G"

        Returns:
            Yeni kaydedilen sinyal sayısı
        """
        params = TIMEFRAME_PARAMS.get(timeframe, TIMEFRAME_PARAMS[DEFAULT_TIMEFRAME])
        added = 0

        with self._lock:
            # Mevcut aktif (ticker, signal) seti — duplicate kontrolü
            active_keys: set[tuple[str, str]] = {
                (s["ticker"], s["signal"])
                for s in self._signals
                if s["status"] == "active"
            }

            for sig in signals:
                ticker: str = sig.get("ticker", "").strip().upper()
                signal_name: str = sig.get("signal", "").strip()
                price: Optional[float] = sig.get("price")

                # Zorunlu alanlar kontrolü
                if not ticker or not signal_name:
                    continue
                if price is None or not isinstance(price, (int, float)) or price <= 0:
                    continue

                # Bearish sinyalleri takip etme
                if sig.get("signal_type") == "bearish":
                    continue

                # Duplicate aktif sinyal kontrolü
                if (ticker, signal_name) in active_keys:
                    continue

                price = round(float(price), 4)
                tp = round(price * (1.0 + params["tp_pct"]), 4)
                sl = round(price * (1.0 + params["sl_pct"]), 4)

                record: dict = {
                    "id":           str(uuid.uuid4()),
                    "ticker":       ticker,
                    "timeframe":    timeframe,
                    "signal":       signal_name,
                    "signal_type":  sig.get("signal_type", "bullish"),
                    "stars":        int(sig.get("stars", 3)),
                    "category":     sig.get("category", "momentum"),
                    "explanation":  sig.get("explanation", ""),
                    "vol_confirmed": bool(sig.get("vol_confirmed", False)),
                    # Fiyat bilgileri
                    "entry_price":  price,
                    "tp":           tp,
                    "sl":           sl,
                    "tp_pct":       params["tp_pct"],
                    "sl_pct":       params["sl_pct"],
                    # Teknik göstergeler (ek bağlam için)
                    "rsi":          sig.get("rsi"),
                    "vol_ratio":    sig.get("vol_ratio"),
                    "tech_score":   sig.get("tech_score"),
                    # Durum
                    "timestamp":    _now_iso(),
                    "status":       "active",
                    "closed_at":    None,
                    "closed_price": None,
                    "pnl_pct":      None,
                }

                self._signals.append(record)
                active_keys.add((ticker, signal_name))
                added += 1

            if added > 0:
                self._save()

        if added > 0:
            log.info(f"SignalTracker: {added} yeni sinyal kaydedildi (TF={timeframe})")

        return added

    # ----------------------------------------------------------------
    # PUBLIC: FİYAT GÜNCELLEME & TP/SL KONTROLÜ
    # ----------------------------------------------------------------

    def update_prices(self, price_map: dict[str, float]) -> dict[str, int]:
        """
        Aktif sinyallerin anlık fiyatlarını kontrol et.
        TP veya SL'ye ulaşan sinyalleri kapat.

        Args:
            price_map: {ticker: current_price}

        Returns:
            {"tp": tp_count, "sl": sl_count, "still_active": active_count, "no_price": no_data_count}
        """
        counts: dict[str, int] = {"tp": 0, "sl": 0, "still_active": 0, "no_price": 0}
        changed = False
        now_str = _now_iso()

        with self._lock:
            for sig in self._signals:
                if sig["status"] != "active":
                    continue

                cur = price_map.get(sig["ticker"])
                if cur is None or not isinstance(cur, (int, float)) or cur <= 0:
                    counts["no_price"] += 1
                    continue

                cur = round(float(cur), 4)

                if cur >= sig["tp"]:
                    sig["status"]       = "tp"
                    sig["closed_at"]    = now_str
                    sig["closed_price"] = cur
                    sig["pnl_pct"]      = round(
                        (cur - sig["entry_price"]) / sig["entry_price"] * 100, 2
                    )
                    counts["tp"] += 1
                    changed = True
                    log.info(
                        f"TP HIT ✅ {sig['ticker']} | {sig['signal']} | "
                        f"giriş:{sig['entry_price']} → çıkış:{cur} | "
                        f"+{sig['pnl_pct']}%"
                    )

                elif cur <= sig["sl"]:
                    sig["status"]       = "sl"
                    sig["closed_at"]    = now_str
                    sig["closed_price"] = cur
                    sig["pnl_pct"]      = round(
                        (cur - sig["entry_price"]) / sig["entry_price"] * 100, 2
                    )
                    counts["sl"] += 1
                    changed = True
                    log.info(
                        f"SL HIT ❌ {sig['ticker']} | {sig['signal']} | "
                        f"giriş:{sig['entry_price']} → çıkış:{cur} | "
                        f"{sig['pnl_pct']}%"
                    )

                else:
                    counts["still_active"] += 1

            if changed:
                self._save()

        return counts

    # ----------------------------------------------------------------
    # PUBLIC: TRACK RECORD İSTATİSTİKLERİ
    # ----------------------------------------------------------------

    def get_track_record(self, days: int = 30) -> dict:
        """
        Son `days` günün kapsamlı Track Record istatistiklerini döndür.
        Frontend'in /api/signals/track-record endpoint'i bunu kullanır.

        Returns: {
            period_days, total, tp, sl, active,
            win_rate, avg_pnl_pct, best_pnl, worst_pnl,
            active_signals, recent_closed, by_timeframe,
            generated_at
        }
        """
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)

        with self._lock:
            # Son N gün içindeki tüm kayıtlar
            recent = [
                s for s in self._signals
                if _parse_ts(s.get("timestamp")) >= cutoff
            ]

        total      = len(recent)
        tp_count   = sum(1 for s in recent if s["status"] == "tp")
        sl_count   = sum(1 for s in recent if s["status"] == "sl")
        active_count = sum(1 for s in recent if s["status"] == "active")

        # Win rate: sadece kapanan pozisyonlar üzerinden
        closed_count = tp_count + sl_count
        win_rate = round(tp_count / closed_count * 100, 1) if closed_count > 0 else 0.0

        # P&L istatistikleri
        pnls = [s["pnl_pct"] for s in recent if s.get("pnl_pct") is not None]
        avg_pnl  = round(sum(pnls) / len(pnls), 2) if pnls else 0.0
        best_pnl  = round(max(pnls), 2) if pnls else 0.0
        worst_pnl = round(min(pnls), 2) if pnls else 0.0

        # Aktif sinyaller (tarih sırasıyla en yeni önce)
        active_signals = sorted(
            [s for s in recent if s["status"] == "active"],
            key=lambda x: x.get("timestamp", ""),
            reverse=True,
        )

        # Son kapatılan 30 pozisyon
        recent_closed = sorted(
            [s for s in recent if s["status"] in ("tp", "sl")],
            key=lambda x: x.get("closed_at") or x.get("timestamp", ""),
            reverse=True,
        )[:30]

        # Zaman dilimine göre breakdown
        by_timeframe: dict[str, dict] = {}
        for tf in TIMEFRAME_PARAMS:
            tf_sigs = [s for s in recent if s.get("timeframe") == tf]
            tf_tp   = sum(1 for s in tf_sigs if s["status"] == "tp")
            tf_sl   = sum(1 for s in tf_sigs if s["status"] == "sl")
            tf_closed = tf_tp + tf_sl
            by_timeframe[tf] = {
                "total":    len(tf_sigs),
                "tp":       tf_tp,
                "sl":       tf_sl,
                "active":   sum(1 for s in tf_sigs if s["status"] == "active"),
                "win_rate": round(tf_tp / tf_closed * 100, 1) if tf_closed > 0 else 0.0,
            }

        return {
            "period_days":    days,
            "total":          total,
            "tp":             tp_count,
            "sl":             sl_count,
            "active":         active_count,
            "win_rate":       win_rate,
            "avg_pnl_pct":    avg_pnl,
            "best_pnl_pct":   best_pnl,
            "worst_pnl_pct":  worst_pnl,
            "active_signals": active_signals,
            "recent_closed":  recent_closed,
            "by_timeframe":   by_timeframe,
            "generated_at":   _now_iso(),
        }

    # ----------------------------------------------------------------
    # PUBLIC: YARDIMCI
    # ----------------------------------------------------------------

    def get_all_active(self) -> list[dict]:
        """Tüm aktif sinyalleri döndür. Background task için."""
        with self._lock:
            return [s for s in self._signals if s["status"] == "active"]

    def active_count(self) -> int:
        """Aktif sinyal sayısı."""
        with self._lock:
            return sum(1 for s in self._signals if s["status"] == "active")

    def total_count(self) -> int:
        """Toplam kayıt sayısı."""
        with self._lock:
            return len(self._signals)

    def purge_old(self, keep_days: int = PURGE_AFTER_DAYS) -> int:
        """
        Belirtilen günden eski kapalı pozisyonları temizle.
        Aktif sinyaller her zaman korunur.

        Returns: Silinen kayıt sayısı
        """
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)
        with self._lock:
            before = len(self._signals)
            self._signals = [
                s for s in self._signals
                if s["status"] == "active" or _parse_ts(s.get("timestamp")) >= cutoff
            ]
            after = len(self._signals)
            deleted = before - after
            if deleted > 0:
                self._save()
                log.info(f"SignalTracker purge: {deleted} eski kayıt silindi (>{keep_days} gün)")
        return deleted

    def __repr__(self) -> str:
        return (
            f"SignalTracker("
            f"total={self.total_count()}, "
            f"active={self.active_count()}, "
            f"path={self._log_path})"
        )


# ================================================================
# YARDIMCI FONKSİYONLAR
# ================================================================

def _now_iso() -> str:
    """UTC timezone-aware ISO timestamp."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _parse_ts(ts_str: Optional[str]) -> dt.datetime:
    """ISO timestamp stringini timezone-aware datetime'a dönüştür."""
    if not ts_str:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    try:
        # "Z" suffix'i "+00:00" formatına çevir
        normalized = ts_str.replace("Z", "+00:00") if ts_str.endswith("Z") else ts_str
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except (ValueError, AttributeError):
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


# ================================================================
# GLOBAL SİNGLETON
# ================================================================
signal_tracker = SignalTracker()

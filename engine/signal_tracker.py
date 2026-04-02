# ================================================================
# BISTBULL — SIGNAL TRACKER / PAPER TRADE ENGINE
# Sinyal kaydı, TP/SL takibi, başarı oranı hesaplama.
# Persistence: JSON dosya + Redis (varsa).
# ================================================================

from __future__ import annotations

import json
import os
import time
import logging
import datetime as dt
from typing import Optional
from threading import Lock

log = logging.getLogger("bistbull.signal_tracker")

# ================================================================
# TP/SL ORANLARI — zaman dilimine göre
# ================================================================
TP_SL_MAP = {
    "1G":  {"tp_pct": 0.03, "sl_pct": 0.02},   # +3% / -2%
    "60m": {"tp_pct": 0.04, "sl_pct": 0.02},   # +4% / -2%
    "4S":  {"tp_pct": 0.02, "sl_pct": 0.01},   # +2% / -1%
}
DEFAULT_TP_SL = {"tp_pct": 0.03, "sl_pct": 0.02}

# Persistence path
SIGNAL_DB_PATH = os.environ.get("SIGNAL_DB_PATH", "/tmp/bistbull_signals.json")
# Max signal age (days) before cleanup
MAX_SIGNAL_AGE_DAYS = 90


class SignalTracker:
    """
    Paper Trade sinyal takip motoru.

    - log_signals(): Cross Hunter sinyallerini kaydet, TP/SL hesapla
    - check_prices(): Aktif sinyallerin fiyatlarını kontrol et, TP/SL vurduysa kapat
    - get_track_record(): İstatistik döndür (win_rate, P&L, aktif/kapalı listeler)
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._signals: list[dict] = []
        self._load()

    def __repr__(self) -> str:
        active = sum(1 for s in self._signals if s["status"] in ("active", "watch"))
        closed = sum(1 for s in self._signals if s["status"] in ("tp", "sl"))
        return f"<SignalTracker signals={len(self._signals)} active={active} closed={closed}>"

    # ================================================================
    # PERSISTENCE — JSON dosya
    # ================================================================
    def _load(self) -> None:
        """JSON dosyadan yükle."""
        try:
            if os.path.exists(SIGNAL_DB_PATH):
                with open(SIGNAL_DB_PATH, "r") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._signals = data
                    log.info(f"SignalTracker: {len(data)} sinyal yüklendi ({SIGNAL_DB_PATH})")
                    return
        except Exception as e:
            log.warning(f"SignalTracker load failed: {e}")

        # Redis'ten yükle (fallback)
        try:
            from core import redis_client
            if redis_client.is_available():
                raw = redis_client.get("bistbull:signals")
                if raw:
                    self._signals = json.loads(raw)
                    log.info(f"SignalTracker: {len(self._signals)} sinyal Redis'ten yüklendi")
                    self._save_json()  # JSON'a da yaz
                    return
        except Exception:
            pass

        self._signals = []

    def _save(self) -> None:
        """JSON dosyaya + Redis'e kaydet."""
        self._save_json()
        self._save_redis()

    def _save_json(self) -> None:
        try:
            with open(SIGNAL_DB_PATH, "w") as f:
                json.dump(self._signals, f, ensure_ascii=False, default=str)
        except Exception as e:
            log.warning(f"SignalTracker JSON save failed: {e}")

    def _save_redis(self) -> None:
        try:
            from core import redis_client
            if redis_client.is_available():
                redis_client.set(
                    "bistbull:signals",
                    json.dumps(self._signals, ensure_ascii=False, default=str),
                    ex=86400 * MAX_SIGNAL_AGE_DAYS,
                )
        except Exception:
            pass

    # ================================================================
    # LOG SIGNALS — Cross Hunter sinyallerini kaydet
    # ================================================================
    def log_signals(self, new_signals: list[dict], timeframe: str = "1G") -> int:
        """
        Yeni Cross Hunter sinyallerini kaydet.
        Duplicate kontrolü: aynı ticker+signal+timeframe 24 saat içinde tekrar kaydedilmez.
        Returns: kaydedilen sinyal sayısı.
        """
        if not new_signals:
            return 0

        with self._lock:
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            cutoff_24h = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()
            logged = 0

            # Mevcut aktif sinyaller (dedup için)
            recent_keys = set()
            for s in self._signals:
                if s.get("timestamp", "") >= cutoff_24h:
                    recent_keys.add(f"{s['ticker']}:{s['signal']}:{s.get('timeframe', '1G')}")

            tp_sl = TP_SL_MAP.get(timeframe, DEFAULT_TP_SL)

            for sig in new_signals:
                key = f"{sig['ticker']}:{sig['signal']}:{timeframe}"
                if key in recent_keys:
                    continue

                price = sig.get("price")
                if not price or price <= 0:
                    continue

                # Bullish → long (TP yukarı, SL aşağı)
                # Bearish → short mantığı yok, sadece uyarı olarak kaydedilir
                is_bullish = sig.get("signal_type") == "bullish"

                if is_bullish:
                    tp_price = round(price * (1 + tp_sl["tp_pct"]), 2)
                    sl_price = round(price * (1 - tp_sl["sl_pct"]), 2)
                else:
                    # Bearish sinyaller "watch" statüsünde — TP/SL takibi yok
                    tp_price = None
                    sl_price = None

                record = {
                    "ticker": sig["ticker"],
                    "signal": sig["signal"],
                    "signal_type": sig.get("signal_type", "neutral"),
                    "explanation": sig.get("explanation", ""),
                    "stars": sig.get("stars", 1),
                    "vol_confirmed": sig.get("vol_confirmed", False),
                    "category": sig.get("category", "momentum"),
                    "timeframe": timeframe,
                    "entry_price": round(price, 2),
                    "tp": tp_price,
                    "sl": sl_price,
                    "closed_price": None,
                    "pnl_pct": None,
                    "status": "active" if is_bullish else "watch",
                    "timestamp": now,
                    "closed_at": None,
                }

                self._signals.append(record)
                recent_keys.add(key)
                logged += 1

            if logged > 0:
                self._cleanup()
                self._save()

            return logged

    # ================================================================
    # CHECK PRICES — TP/SL kontrolü
    # ================================================================
    def check_prices(self, price_map: dict[str, float]) -> int:
        """
        Aktif sinyallerin fiyatlarını kontrol et.
        price_map: {ticker: current_price}
        Returns: kapatılan sinyal sayısı.
        """
        if not price_map:
            return 0

        with self._lock:
            closed = 0
            now = dt.datetime.now(dt.timezone.utc).isoformat()

            for sig in self._signals:
                if sig["status"] != "active":
                    continue

                ticker = sig["ticker"]
                current = price_map.get(ticker)
                if current is None or current <= 0:
                    continue

                entry = sig["entry_price"]
                tp = sig.get("tp")
                sl = sig.get("sl")

                if tp and current >= tp:
                    sig["status"] = "tp"
                    sig["closed_price"] = round(current, 2)
                    sig["pnl_pct"] = round(((current - entry) / entry) * 100, 2)
                    sig["closed_at"] = now
                    closed += 1
                    log.info(f"🎯 TP HIT: {ticker} {sig['signal']} entry={entry} close={current} +{sig['pnl_pct']}%")

                elif sl and current <= sl:
                    sig["status"] = "sl"
                    sig["closed_price"] = round(current, 2)
                    sig["pnl_pct"] = round(((current - entry) / entry) * 100, 2)
                    sig["closed_at"] = now
                    closed += 1
                    log.info(f"🛑 SL HIT: {ticker} {sig['signal']} entry={entry} close={current} {sig['pnl_pct']}%")

            if closed > 0:
                self._save()

            return closed

    # ================================================================
    # GET TRACK RECORD — İstatistikler
    # ================================================================
    def get_track_record(self, days: int = 30) -> dict:
        """
        Sinyal başarı istatistiklerini döndür.
        Frontend'in beklediği tam format.
        """
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()

        filtered = [s for s in self._signals if s.get("timestamp", "") >= cutoff]

        active_signals = [s for s in filtered if s["status"] in ("active", "watch")]
        closed_signals = [s for s in filtered if s["status"] in ("tp", "sl")]
        tp_signals = [s for s in filtered if s["status"] == "tp"]
        sl_signals = [s for s in filtered if s["status"] == "sl"]

        total = len(filtered)
        tp_count = len(tp_signals)
        sl_count = len(sl_signals)
        active_count = len(active_signals)

        # Win rate — sadece kapanan pozisyonlar
        closed_count = tp_count + sl_count
        win_rate = round((tp_count / closed_count) * 100, 1) if closed_count > 0 else 0.0

        # P&L istatistikleri
        pnls = [s["pnl_pct"] for s in closed_signals if s.get("pnl_pct") is not None]
        avg_pnl = round(sum(pnls) / len(pnls), 2) if pnls else 0.0
        best_pnl = round(max(pnls), 2) if pnls else 0.0
        worst_pnl = round(min(pnls), 2) if pnls else 0.0

        # Zaman dilimine göre breakdown
        by_tf: dict[str, dict] = {}
        for s in filtered:
            tf = s.get("timeframe", "1G")
            if tf not in by_tf:
                by_tf[tf] = {"total": 0, "tp": 0, "sl": 0, "active": 0, "win_rate": 0}
            by_tf[tf]["total"] += 1
            if s["status"] == "tp":
                by_tf[tf]["tp"] += 1
            elif s["status"] == "sl":
                by_tf[tf]["sl"] += 1
            elif s["status"] in ("active", "watch"):
                by_tf[tf]["active"] += 1

        for tf, stats in by_tf.items():
            c = stats["tp"] + stats["sl"]
            stats["win_rate"] = round((stats["tp"] / c) * 100, 1) if c > 0 else 0.0

        return {
            "period_days": days,
            "total": total,
            "tp": tp_count,
            "sl": sl_count,
            "active": active_count,
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl,
            "best_pnl_pct": best_pnl,
            "worst_pnl_pct": worst_pnl,
            "active_signals": sorted(
                active_signals,
                key=lambda x: x.get("timestamp", ""),
                reverse=True,
            ),
            "recent_closed": sorted(
                closed_signals,
                key=lambda x: x.get("closed_at", ""),
                reverse=True,
            )[:30],
            "by_timeframe": by_tf,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    # ================================================================
    # CLEANUP — eski sinyalleri temizle
    # ================================================================
    def _cleanup(self) -> None:
        """MAX_SIGNAL_AGE_DAYS'den eski sinyalleri sil."""
        cutoff = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=MAX_SIGNAL_AGE_DAYS)
        ).isoformat()
        before = len(self._signals)
        self._signals = [s for s in self._signals if s.get("timestamp", "") >= cutoff]
        removed = before - len(self._signals)
        if removed > 0:
            log.info(f"SignalTracker cleanup: {removed} eski sinyal silindi")


# ================================================================
# GLOBAL INSTANCE
# ================================================================
signal_tracker = SignalTracker()

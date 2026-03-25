# ================================================================
# BISTBULL TERMINAL V9.1 — MARKET STATUS
# BIST piyasa saatleri, tatiller, arife günleri
# ================================================================

from __future__ import annotations

import datetime as dt
import zoneinfo

_IST = zoneinfo.ZoneInfo("Europe/Istanbul")

# Statik tatiller (her yıl sabit)
_FIXED_HOLIDAYS_MD: set[tuple[int, int]] = {
    (1, 1),    # Yılbaşı
    (4, 23),   # 23 Nisan
    (5, 1),    # 1 Mayıs
    (5, 19),   # 19 Mayıs
    (7, 15),   # 15 Temmuz
    (8, 30),   # 30 Ağustos
    (10, 29),  # 29 Ekim
}

_FIXED_HOLIDAY_NAMES: dict[tuple[int, int], str] = {
    (1, 1): "Yılbaşı", (4, 23): "23 Nisan", (5, 1): "1 Mayıs",
    (5, 19): "19 Mayıs", (7, 15): "15 Temmuz", (8, 30): "30 Ağustos",
    (10, 29): "29 Ekim",
}

# Dini bayramlar (2025-2027)
_RELIGIOUS_HOLIDAYS: dict[dt.date, str] = {
    dt.date(2025, 3, 30): "Ramazan Bayramı", dt.date(2025, 3, 31): "Ramazan Bayramı",
    dt.date(2025, 4, 1): "Ramazan Bayramı",
    dt.date(2025, 6, 6): "Kurban Bayramı", dt.date(2025, 6, 7): "Kurban Bayramı",
    dt.date(2025, 6, 8): "Kurban Bayramı", dt.date(2025, 6, 9): "Kurban Bayramı",
    dt.date(2026, 3, 19): "Ramazan Bayramı", dt.date(2026, 3, 20): "Ramazan Bayramı",
    dt.date(2026, 3, 21): "Ramazan Bayramı",
    dt.date(2026, 5, 26): "Kurban Bayramı", dt.date(2026, 5, 27): "Kurban Bayramı",
    dt.date(2026, 5, 28): "Kurban Bayramı", dt.date(2026, 5, 29): "Kurban Bayramı",
    dt.date(2027, 3, 9): "Ramazan Bayramı", dt.date(2027, 3, 10): "Ramazan Bayramı",
    dt.date(2027, 3, 11): "Ramazan Bayramı",
    dt.date(2027, 5, 16): "Kurban Bayramı", dt.date(2027, 5, 17): "Kurban Bayramı",
    dt.date(2027, 5, 18): "Kurban Bayramı", dt.date(2027, 5, 19): "Kurban Bayramı",
}

# Arife günleri (yarı gün: 10:00-12:30)
_HALF_DAYS: set[dt.date] = {
    dt.date(2025, 3, 28), dt.date(2025, 6, 5),
    dt.date(2026, 3, 18), dt.date(2026, 5, 25),
    dt.date(2027, 3, 8), dt.date(2027, 5, 14),
}


def _next_open_day(today: dt.date) -> dt.date:
    """Sonraki açık işgünü."""
    check = today + dt.timedelta(days=1)
    for _ in range(30):  # max 30 gün ileri bak
        if (check.weekday() < 5
            and (check.month, check.day) not in _FIXED_HOLIDAYS_MD
            and check not in _RELIGIOUS_HOLIDAYS):
            return check
        check += dt.timedelta(days=1)
    return check


def get_market_status() -> dict:
    """BIST piyasa durumu: open/closed/half_day/pre_market/after_hours + neden."""
    now_ist = dt.datetime.now(_IST)
    today = now_ist.date()
    weekday = today.weekday()

    # Hafta sonu
    if weekday >= 5:
        return {
            "status": "closed",
            "reason": "Hafta sonu",
            "reason_detail": "Cumartesi" if weekday == 5 else "Pazar",
            "next_open": _next_open_day(today).isoformat(),
            "ist_time": now_ist.strftime("%H:%M"),
            "global_open": True,
        }

    # Dini bayram
    if today in _RELIGIOUS_HOLIDAYS:
        bayram = _RELIGIOUS_HOLIDAYS[today]
        return {
            "status": "closed",
            "reason": bayram,
            "reason_detail": f"{bayram} tatili",
            "next_open": _next_open_day(today).isoformat(),
            "ist_time": now_ist.strftime("%H:%M"),
            "global_open": True,
        }

    # Sabit tatil
    if (today.month, today.day) in _FIXED_HOLIDAYS_MD:
        name = _FIXED_HOLIDAY_NAMES.get((today.month, today.day), "Resmî tatil")
        return {
            "status": "closed",
            "reason": name,
            "reason_detail": f"{name} tatili",
            "next_open": _next_open_day(today).isoformat(),
            "ist_time": now_ist.strftime("%H:%M"),
            "global_open": True,
        }

    t = now_ist.hour * 60 + now_ist.minute

    # Yarı gün (arife)
    if today in _HALF_DAYS:
        if t < 600:
            return {"status": "pre_market", "reason": "Arife — yarı gün seans", "reason_detail": "Seans 10:00-12:30", "ist_time": now_ist.strftime("%H:%M"), "global_open": False, "half_day": True}
        if t <= 750:
            return {"status": "open", "reason": "Arife — yarı gün seans", "reason_detail": "Kapanış 12:30", "ist_time": now_ist.strftime("%H:%M"), "global_open": False, "half_day": True}
        return {"status": "closed", "reason": "Arife — seans bitti", "reason_detail": "Yarı gün seans 12:30'da kapandı", "ist_time": now_ist.strftime("%H:%M"), "global_open": True, "half_day": True}

    # Normal gün seans saatleri
    if t < 595:
        return {"status": "pre_market", "reason": "Seans öncesi", "reason_detail": "BIST 10:00'da açılır", "ist_time": now_ist.strftime("%H:%M"), "global_open": False}
    if t <= 1080:
        return {"status": "open", "reason": "Seans açık", "reason_detail": "Sürekli işlem", "ist_time": now_ist.strftime("%H:%M"), "global_open": False}
    return {"status": "after_hours", "reason": "Seans kapandı", "reason_detail": "Kapanış 18:00 — Yarın 10:00'da açılır", "ist_time": now_ist.strftime("%H:%M"), "global_open": True}


def is_scan_worthwhile(has_data: bool) -> bool:
    """Scan yapmaya değer mi?"""
    if not has_data:
        return True
    ms = get_market_status()
    if ms["status"] in ("open", "pre_market"):
        return True
    if ms["status"] == "after_hours":
        now_ist = dt.datetime.now(_IST)
        if now_ist.hour == 18 and now_ist.minute <= 30:
            return True
    return False

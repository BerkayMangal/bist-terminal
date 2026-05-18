# ================================================================
# BISTBULL TERMINAL — RADAR SKOR DOĞRULAMA (EVENT-STUDY)
# research/score_validation.py
#
# Soru: Radar skorunun GERÇEKTEN öngörü değeri var mı? Tek dürüst
# yanıt — yüksek-skorlu hisseler ileride düşük-skorlulardan daha iyi
# getiri sağlıyor mu?
#
# Yöntem: score_history her gün (symbol, snap_date, score, price)
# kaydeder. Yeterince geçmiş birikince, her skor anının `horizon_days`
# sonraki fiyatıyla ileri-getirisi hesaplanır; skor-bandı başına
# ortalama getiri karşılaştırılır.
#
# DÜRÜST NOT: bu bir SCAFFOLD. score_history en az `horizon_days`
# kadar geçmişe sahip olana dek "veri_yetersiz" döner — anlamlı sonuç
# haftalar içinde birikir. Skorlama 2026-05'te defalarca değiştiği
# için yalnız bundan SONRAKİ kayıtlar geçerlidir; geçmişe dönük
# backtest mümkün değil (point-in-time finansal veri yok).
#
# Saf çekirdek (event_study_from_rows) IO'suz — test edilebilir.
# ================================================================

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterable

log = logging.getLogger("bistbull.score_validation")


def _bucket(score: float) -> str:
    """Skoru bant etiketine çevir — radar_grade ile aynı eşikler."""
    if score >= 60:
        return "60+ (güçlü)"
    if score >= 45:
        return "45-60 (orta)"
    if score >= 30:
        return "30-45 (zayıf)"
    return "0-30 (riskli)"


_BUCKET_ORDER = ["60+ (güçlü)", "45-60 (orta)", "30-45 (zayıf)", "0-30 (riskli)"]


def event_study_from_rows(
    rows: Iterable[tuple], horizon_days: int = 20,
) -> dict[str, Any]:
    """Saf event-study çekirdeği — test edilebilir.

    rows: (symbol, snap_date 'YYYY-MM-DD', score, price) dizisi.
    Her skor anı için, aynı hissenin >= horizon_days sonraki İLK
    kaydıyla ileri-getiri eşlenir; skor-bandı başına özetlenir.
    """
    by_sym: dict[str, list[tuple]] = {}
    for row in rows:
        try:
            sym, d, score, price = row[0], row[1], row[2], row[3]
        except (IndexError, TypeError):
            continue
        if score is None or price is None:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        by_sym.setdefault(sym, []).append((d, float(score), price))

    pairs: list[tuple[float, float]] = []  # (base_score, forward_return)
    for recs in by_sym.values():
        recs.sort()
        for i, (bd, bscore, bprice) in enumerate(recs):
            try:
                base = dt.date.fromisoformat(bd)
            except (ValueError, TypeError):
                continue
            for jd, _js, jprice in recs[i + 1:]:
                try:
                    gap = (dt.date.fromisoformat(jd) - base).days
                except (ValueError, TypeError):
                    continue
                if gap >= horizon_days:
                    pairs.append((bscore, jprice / bprice - 1.0))
                    break

    if not pairs:
        return {
            "status": "veri_yetersiz",
            "message": (f"Henüz {horizon_days} gün aralıklı skor+fiyat "
                        "çifti yok — veri birikiyor."),
            "horizon_days": horizon_days,
            "pairs": 0,
            "buckets": [],
        }

    grouped: dict[str, list[float]] = {}
    for score, ret in pairs:
        grouped.setdefault(_bucket(score), []).append(ret)

    buckets = []
    for label in _BUCKET_ORDER:
        rets = grouped.get(label, [])
        if rets:
            srt = sorted(rets)
            buckets.append({
                "bucket": label,
                "n": len(rets),
                "avg_return_pct": round(sum(rets) / len(rets) * 100, 2),
                "median_return_pct": round(srt[len(srt) // 2] * 100, 2),
            })
        else:
            buckets.append({
                "bucket": label, "n": 0,
                "avg_return_pct": None, "median_return_pct": None,
            })

    # Monotonluk: yüksek bant düşük banttan daha iyi getiri sağlıyor mu?
    avgs = [b["avg_return_pct"] for b in buckets if b["avg_return_pct"] is not None]
    monotonic = len(avgs) >= 2 and all(
        avgs[i] >= avgs[i + 1] for i in range(len(avgs) - 1)
    )
    return {
        "status": "ok",
        "horizon_days": horizon_days,
        "pairs": len(pairs),
        "buckets": buckets,
        "monotonic": monotonic,
        "verdict": ("Skor bantları ileri-getiriyle uyumlu (yüksek bant "
                    "daha iyi)." if monotonic else
                    "Bantlar henüz net ayrışmıyor — daha çok veri gerek."),
    }


def run_event_study(horizon_days: int = 20) -> dict[str, Any]:
    """score_history tablosundan event-study çalıştır."""
    try:
        from infra.storage import _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT symbol, snap_date, score, price FROM score_history "
            "WHERE price IS NOT NULL "
            "ORDER BY symbol, snap_date"
        ).fetchall()
        return event_study_from_rows(rows, horizon_days)
    except Exception as e:
        log.warning(f"event study failed: {e}")
        return {"status": "hata", "message": str(e),
                "horizon_days": horizon_days, "pairs": 0, "buckets": []}

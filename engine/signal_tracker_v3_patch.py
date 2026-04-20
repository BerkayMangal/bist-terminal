# ================================================================
# BISTBULL TERMINAL — SIGNAL TRACKER V3 PATCH
# engine/signal_tracker_v3_patch.py
#
# signal_tracker.py'nin kendisi DEĞİŞMEZ — backward-compatible.
# Bu dosya sadece V3 alanlarını track record'a ekleyen
# OPTIONAL patch'i gösterir.
#
# Uygulamak için signal_tracker.py'de get_track_record()'a
# aşağıdaki ek metrikleri ekleyin.
# ================================================================

"""
signal_tracker.py > get_track_record() metoduna EKLENECEKLER:

1. V3 Confirmation Analizi:
   Son 30 günün kapanan pozisyonlarını confirmation_count'a göre grupla
   ve her grup için win rate hesapla.
   
   Bu, filtreleme kalitesini doğrudan ölçer:
   - 4+ onaylı sinyaller >%60 win rate yapıyorsa filtreler çalışıyor
   - 1 onaylı sinyaller de aynı ise filtreler etki etmiyor

2. ADX/Hacim Korelasyonu:
   ADX teyidli vs teyidsiz sinyallerin karşılaştırması.

Değişiklik Yeri: get_track_record() metodunun return dict'ine ekle.
"""


def enhanced_track_record_fields(recent: list[dict]) -> dict:
    """
    signal_tracker.get_track_record()'a eklenecek V3 metrikleri.
    
    Bu fonksiyonu signal_tracker.py içine kopyalayın ve
    get_track_record()'un return dict'ine şu satırı ekleyin:
    
        result.update(enhanced_track_record_fields(recent))
    
    Args:
        recent: get_track_record() içindeki filtered signal listesi
    
    Returns:
        V3 ek metrikleri dict
    """
    closed = [s for s in recent if s.get("status") in ("tp", "sl")]
    
    # ── 1. Confirmation Count Analizi ──
    by_confirmation: dict[str, dict] = {}
    conf_groups: dict[int, list] = {}
    for s in closed:
        cc = s.get("confirmation_count", 0)
        if not isinstance(cc, int):
            cc = 0
        conf_groups.setdefault(cc, []).append(s)
    
    for cc, group in sorted(conf_groups.items()):
        tp_count = sum(1 for s in group if s["status"] == "tp")
        count = len(group)
        pnls = [s["pnl_pct"] for s in group if s.get("pnl_pct") is not None]
        by_confirmation[str(cc)] = {
            "count": count,
            "win_rate": round(tp_count / count * 100, 1) if count > 0 else 0.0,
            "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        }
    
    # ── 2. ADX Teyidi Analizi ──
    adx_confirmed = [s for s in closed if s.get("adx_confirmed")]
    adx_not_confirmed = [s for s in closed if not s.get("adx_confirmed")]
    
    def _win_rate(group):
        if not group:
            return 0.0
        tp = sum(1 for s in group if s["status"] == "tp")
        return round(tp / len(group) * 100, 1)
    
    adx_analysis = {
        "adx_confirmed": {
            "count": len(adx_confirmed),
            "win_rate": _win_rate(adx_confirmed),
        },
        "adx_not_confirmed": {
            "count": len(adx_not_confirmed),
            "win_rate": _win_rate(adx_not_confirmed),
        },
    }
    
    # ── 3. Hacim Teyidi Analizi ──
    vol_confirmed = [s for s in closed if s.get("vol_confirmed")]
    vol_not_confirmed = [s for s in closed if not s.get("vol_confirmed")]
    
    vol_analysis = {
        "vol_confirmed": {
            "count": len(vol_confirmed),
            "win_rate": _win_rate(vol_confirmed),
        },
        "vol_not_confirmed": {
            "count": len(vol_not_confirmed),
            "win_rate": _win_rate(vol_not_confirmed),
        },
    }
    
    # ── 4. Market Regime Analizi ──
    regime_groups: dict[str, list] = {}
    for s in closed:
        regime = s.get("market_regime", "unknown")
        regime_groups.setdefault(regime, []).append(s)
    
    by_regime = {}
    for regime, group in regime_groups.items():
        tp_count = sum(1 for s in group if s["status"] == "tp")
        count = len(group)
        pnls = [s["pnl_pct"] for s in group if s.get("pnl_pct") is not None]
        by_regime[regime] = {
            "count": count,
            "win_rate": round(tp_count / count * 100, 1) if count > 0 else 0.0,
            "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        }
    
    # ── 5. Sinyal Yıldız Performansı ──
    star_groups: dict[int, list] = {}
    for s in closed:
        stars = s.get("stars", 0)
        star_groups.setdefault(stars, []).append(s)
    
    by_stars = {}
    for stars, group in sorted(star_groups.items()):
        tp_count = sum(1 for s in group if s["status"] == "tp")
        count = len(group)
        pnls = [s["pnl_pct"] for s in group if s.get("pnl_pct") is not None]
        by_stars[str(stars)] = {
            "count": count,
            "win_rate": round(tp_count / count * 100, 1) if count > 0 else 0.0,
            "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        }
    
    return {
        "v3_by_confirmation": by_confirmation,
        "v3_adx_analysis": adx_analysis,
        "v3_vol_analysis": vol_analysis,
        "v3_by_regime": by_regime,
        "v3_by_stars": by_stars,
    }


# ================================================================
# SIGNAL TRACKER LOG_SIGNALS PATCH
# ================================================================

"""
signal_tracker.py > log_signals() metodunda V3 alanlarının
kaydedilmesi için record dict'e şu satırları ekleyin:

Mevcut satırlar (zaten var):
    "vol_confirmed": bool(sig.get("vol_confirmed", False)),

YENİ satırlar (ekle):
    "adx_confirmed":      bool(sig.get("adx_confirmed", False)),
    "confirmation_count":  int(sig.get("confirmation_count", 0)),
    "market_regime":       sig.get("market_regime", "unknown"),
    "adx":                 sig.get("adx"),
    "bb_width":            sig.get("bb_width"),
    "atr":                 sig.get("atr"),
"""

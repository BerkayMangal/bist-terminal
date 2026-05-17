# ================================================================
# BISTBULL TERMINAL — TAHTALAB UYARI MOTORU
# engine/tahta_warnings.py
#
# Günlük OHLCV verisinden "tahta davranışı" uyarılarını tespit eder.
#
# TASARIM İLKELERİ:
#  - OR-bazlı eşleşme: her kural BAĞIMSIZ değerlendirilir. Bir hisse
#    en az BİR kuralı tetiklerse uyarı üretilir. Kombine skor YOK.
#  - Look-ahead yok: 20 günlük pencereler bugünü HARİÇ tutar.
#  - Veri dürüstlüğü: veri yoksa uyarı UYDURULMAZ; intraday/kurumsal
#    olay kuralları veri olmadan canlı uyarı üretmez.
#  - AL/SAT önerisi DEĞİL — sadece gözlem/uyarı.
#
# Saf fonksiyonlar — IO/cache yok. Girdi: pandas DataFrame'ler.
# ================================================================

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, asdict
from typing import Any, Optional

from engine.tahta_warning_registry import (
    WARNING_REGISTRY, THRESHOLDS, SEVERITY_RANK,
    get_definition, get_rule_library,
)

log = logging.getLogger("bistbull.tahtalab")

# Bir hissenin değerlendirilebilmesi için gereken asgari günlük bar.
# 20g hacim penceresi (bugün hariç) + önceki günün 20g penceresi → 22.
_MIN_ROWS = 22


# ────────────────────────────────────────────────────────────────
# Uyarı çıktısı — tipli kayıt
# ────────────────────────────────────────────────────────────────
@dataclass
class TahtaWarning:
    ticker: str
    warning_id: str
    label_tr: str
    severity: str
    direction: str
    requires_intraday: bool
    data_available: bool
    explanation_tr: str
    evidence: dict

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TahtaTickerWarnings:
    ticker: str
    warning_count: int
    highest_severity: str
    warnings: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


# Kural başına kullanıcı-yüzü açıklama metni (banlı kelime içermez).
_EXPLANATIONS: dict[str, str] = {
    "weak_pre_limit": "Hisse güçlü yükseldi ama tavan gücü oluşmadı.",
    "base_rebound": (
        "Hisse taban bölgesinden tepki verdi. Satış baskısı kısa "
        "vadede zayıflıyor olabilir."
    ),
    "hold_above_open": (
        "Hisse açılışın üstünde tutunuyor. Gün içi alıcı ilgisi var."
    ),
    "pressure_below_open": (
        "Hisse açılışın altında kalıyor. Gün içi satış baskısı "
        "öne çıkıyor."
    ),
    "split_at_peak": (
        "Bölünme haberi fiyatın çok yükseldiği bir bölgede geldi. "
        "Haber sonrası satış riski artabilir."
    ),
    "weak_continuation": "Dünkü güçlü hareket bugün hacimle desteklenmedi.",
    "close_selloff": (
        "Hisse gün içi kazancının önemli bölümünü kapanışa doğru "
        "geri verdi."
    ),
    "unconfirmed_breakout": (
        "Fiyat direnç üstüne çıktı ama hacim bunu güçlü biçimde "
        "desteklemiyor."
    ),
    "strong_vs_index": "Endeks zayıfken hisse ayakta kaldı.",
}


# ────────────────────────────────────────────────────────────────
# Yardımcılar
# ────────────────────────────────────────────────────────────────
def _sf(v: Any) -> Optional[float]:
    """Güvenli float — None/NaN/Inf → None."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _col(df, name: str) -> Optional[list]:
    """DataFrame'den OHLCV kolonunu float listesi olarak al."""
    for cand in (name, name.lower(), name.upper(), name.capitalize()):
        if cand in df.columns:
            return [_sf(x) for x in df[cand].tolist()]
    return None


def _r(x: Optional[float], n: int = 4) -> Optional[float]:
    return round(x, n) if x is not None else None


def _daily_features(df) -> Optional[dict]:
    """Günlük OHLCV DataFrame'inden tüm türev özellikleri hesapla.

    DataFrame eskiden-yeniye sıralı, son satır = bugün (EOD).
    Yetersiz/bozuk veri → None.
    """
    if df is None or not hasattr(df, "columns") or len(df) < _MIN_ROWS:
        return None
    o = _col(df, "Open"); h = _col(df, "High"); l = _col(df, "Low")
    c = _col(df, "Close"); v = _col(df, "Volume")
    if not all([o, h, l, c, v]):
        return None
    n = len(c)
    # Bugün / dün / önceki gün
    co, ho, lo, cc, vo = o[-1], h[-1], l[-1], c[-1], v[-1]
    po, ph, pl, pc, pv = o[-2], h[-2], l[-2], c[-2], v[-2]
    c2 = c[-3]
    if None in (co, ho, lo, cc, vo, ph, pl, pc, pv, c2):
        return None
    rng = ho - lo
    prng = ph - pl

    def _pos(close, low, rg):
        return (close - low) / rg if rg and rg > 0 else 0.5

    # 20 günlük hacim ortalaması — BUGÜN HARİÇ (look-ahead yok)
    vol_prev20 = [x for x in v[-21:-1] if x is not None and x > 0]
    vol20 = sum(vol_prev20) / len(vol_prev20) if vol_prev20 else None
    # Önceki günün 20g penceresi (önceki gün de hariç)
    vol_prev20b = [x for x in v[-22:-2] if x is not None and x > 0]
    vol20b = sum(vol_prev20b) / len(vol_prev20b) if vol_prev20b else None
    # Son 20 günün en yükseği — BUGÜN HARİÇ (kırılım kuralı için)
    highs_prev20 = [x for x in h[-21:-1] if x is not None]
    rolling_high_20d_prev = max(highs_prev20) if highs_prev20 else None
    # 52 hafta zirve + 60g getiri (bölünme kuralı için)
    highs_all = [x for x in h[-252:] if x is not None]
    high_52w = max(highs_all) if highs_all else None
    return_60d = None
    if n >= 61 and c[-61] not in (None, 0):
        return_60d = cc / c[-61] - 1.0

    feat = {
        "rows": n,
        "open": co, "high": ho, "low": lo, "close": cc, "volume": vo,
        "prev_close": pc, "prev_high": ph, "prev_low": pl,
        "prev_open": po, "prev_volume": pv,
        "return_1d": (cc / pc - 1.0) if pc else 0.0,
        "prev_return_1d": (pc / c2 - 1.0) if c2 else 0.0,
        "close_position": _pos(cc, lo, rng),
        "prev_close_position": _pos(pc, pl, prng),
        "upper_wick_ratio": ((ho - max(co, cc)) / rng) if rng > 0 else 0.0,
        "intraday_high_return_open": (ho / co - 1.0) if co else 0.0,
        "high_from_prev_close": (ho / pc - 1.0) if pc else 0.0,
        "low_from_prev_close": (lo / pc - 1.0) if pc else 0.0,
        "close_to_low_rebound": (cc / lo - 1.0) if lo else 0.0,
        "volume_ratio_20d": (vo / vol20) if vol20 else None,
        "prev_volume_ratio_20d": (pv / vol20b) if vol20b else None,
        "followup_volume_ratio": (vo / pv) if pv else None,
        "rolling_high_20d_prev": rolling_high_20d_prev,
        "high_52w": high_52w,
        "return_60d": return_60d,
        "index_return_1d": None,   # dışarıdan doldurulur
    }
    return feat


# ────────────────────────────────────────────────────────────────
# Günlük kural fonksiyonları — her biri eşleşirse evidence dict döner
# ────────────────────────────────────────────────────────────────
def _rule_weak_pre_limit(f: dict) -> Optional[dict]:
    t = THRESHOLDS["weak_pre_limit"]
    vr = f.get("volume_ratio_20d")
    if vr is None:
        return None
    hi_ret = max(f["intraday_high_return_open"], f["high_from_prev_close"])
    if (hi_ret >= t["intraday_high_return"]
            and f["close_position"] <= t["close_position_max"]
            and f["upper_wick_ratio"] >= t["upper_wick_ratio_min"]
            and vr >= t["volume_ratio_min"]):
        return {
            "intraday_high_return": _r(hi_ret * 100, 2),
            "close_position": _r(f["close_position"], 2),
            "upper_wick_ratio": _r(f["upper_wick_ratio"], 2),
            "volume_ratio": _r(vr, 2),
        }
    return None


def _rule_base_rebound(f: dict) -> Optional[dict]:
    t = THRESHOLDS["base_rebound"]
    vr = f.get("volume_ratio_20d")
    if vr is None:
        return None
    if (f["low_from_prev_close"] <= t["low_return_max"]
            and f["close_position"] >= t["close_position_min"]
            and f["close_to_low_rebound"] >= t["close_to_low_rebound_min"]
            and vr >= t["volume_ratio_min"]):
        return {
            "low_return": _r(f["low_from_prev_close"] * 100, 2),
            "close_position": _r(f["close_position"], 2),
            "close_to_low_rebound": _r(f["close_to_low_rebound"] * 100, 2),
            "volume_ratio": _r(vr, 2),
        }
    return None


def _rule_weak_continuation(f: dict) -> Optional[dict]:
    t = THRESHOLDS["weak_continuation"]
    pvr = f.get("prev_volume_ratio_20d")
    fvr = f.get("followup_volume_ratio")
    if pvr is None or fvr is None:
        return None
    if (f["prev_return_1d"] >= t["prev_return_min"]
            and pvr >= t["prev_volume_ratio_min"]
            and fvr <= t["followup_volume_ratio_max"]
            and f["high"] <= f["prev_high"] * t["high_breach_max"]
            and (f["close"] <= f["open"]
                 or f["close_position"] <= t["close_position_max"])):
        return {
            "prev_return_1d": _r(f["prev_return_1d"] * 100, 2),
            "prev_volume_ratio": _r(pvr, 2),
            "followup_volume_ratio": _r(fvr, 2),
            "close_position": _r(f["close_position"], 2),
        }
    return None


def _rule_close_selloff(f: dict) -> Optional[dict]:
    t = THRESHOLDS["close_selloff"]
    vr = f.get("volume_ratio_20d")
    if vr is None:
        return None
    if (f["upper_wick_ratio"] >= t["upper_wick_ratio_min"]
            and f["close_position"] <= t["close_position_max"]
            and vr >= t["volume_ratio_min"]
            and f["intraday_high_return_open"] > 0):
        return {
            "upper_wick_ratio": _r(f["upper_wick_ratio"], 2),
            "close_position": _r(f["close_position"], 2),
            "volume_ratio": _r(vr, 2),
            "intraday_high_return": _r(f["intraday_high_return_open"] * 100, 2),
        }
    return None


def _rule_unconfirmed_breakout(f: dict) -> Optional[dict]:
    t = THRESHOLDS["unconfirmed_breakout"]
    vr = f.get("volume_ratio_20d")
    rh = f.get("rolling_high_20d_prev")
    if vr is None or rh is None:
        return None
    if f["close"] > rh and vr < t["volume_ratio_max"]:
        return {
            "close": _r(f["close"], 2),
            "resistance_20d": _r(rh, 2),
            "volume_ratio": _r(vr, 2),
        }
    return None


def _rule_strong_vs_index(f: dict) -> Optional[dict]:
    t = THRESHOLDS["strong_vs_index"]
    ir = f.get("index_return_1d")
    vr = f.get("volume_ratio_20d")
    if ir is None or vr is None:
        return None
    if (ir <= t["index_return_max"]
            and f["return_1d"] >= t["stock_return_min"]
            and f["close_position"] >= t["close_position_min"]
            and vr >= t["volume_ratio_min"]):
        return {
            "index_return_1d": _r(ir * 100, 2),
            "return_1d": _r(f["return_1d"] * 100, 2),
            "close_position": _r(f["close_position"], 2),
            "volume_ratio": _r(vr, 2),
        }
    return None


def _rule_split_at_peak(f: dict, corporate_action: Optional[dict]) -> Optional[dict]:
    """Yalnız gerçek bir bölünme/bedelsiz olayı varsa değerlendirilir."""
    if not corporate_action:
        return None
    t = THRESHOLDS["split_at_peak"]
    vr = f.get("volume_ratio_20d")
    h52 = f.get("high_52w")
    r60 = f.get("return_60d")
    if vr is None or h52 in (None, 0) or r60 is None:
        return None
    if (f["close"] >= t["close_to_52w_high_min"] * h52
            and r60 >= t["return_60d_min"]
            and vr >= t["volume_ratio_min"]):
        return {
            "corporate_action": str(corporate_action.get("type", "bölünme")),
            "close_to_52w_high": _r(f["close"] / h52, 2),
            "return_60d": _r(r60 * 100, 2),
            "volume_ratio": _r(vr, 2),
        }
    return None


# Günlük (intraday/kurumsal-olay GEREKTİRMEYEN) kurallar — id → fonksiyon
_DAILY_RULES = {
    "weak_pre_limit": _rule_weak_pre_limit,
    "base_rebound": _rule_base_rebound,
    "weak_continuation": _rule_weak_continuation,
    "close_selloff": _rule_close_selloff,
    "unconfirmed_breakout": _rule_unconfirmed_breakout,
    "strong_vs_index": _rule_strong_vs_index,
}


# ────────────────────────────────────────────────────────────────
# Motor
# ────────────────────────────────────────────────────────────────
class TahtaWarningEngine:
    """OR-bazlı tahta-davranışı uyarı motoru."""

    def get_rule_library(self) -> list[dict]:
        return get_rule_library()

    # ── tek hisse ──────────────────────────────────────────────
    def evaluate_ticker(
        self,
        ticker: str,
        daily_df,
        index_df=None,
        intraday_df=None,
        corporate_actions: Optional[dict] = None,
        index_return_1d: Optional[float] = None,
    ) -> list[TahtaWarning]:
        """Tek hisse için TÜM kuralları BAĞIMSIZ değerlendirir.

        OR-bazlı: kaç kural eşleşirse o kadar uyarı döner. Hiçbiri
        eşleşmezse boş liste.
        """
        feat = _daily_features(daily_df)
        if feat is None:
            return []

        # Endeks günlük getirisi: doğrudan verildiyse onu, yoksa
        # index_df'ten türet.
        if index_return_1d is None and index_df is not None:
            index_return_1d = _index_return(index_df)
        feat["index_return_1d"] = index_return_1d

        out: list[TahtaWarning] = []

        # Günlük kurallar — her biri bağımsız
        for wid, fn in _DAILY_RULES.items():
            try:
                ev = fn(feat)
            except Exception as e:  # bir kural patlasa diğerleri sürer
                log.debug("TahtaLab kural %s (%s) hata: %s", wid, ticker, e)
                ev = None
            if ev is not None:
                out.append(self._mk(ticker, wid, ev))

        # Kurumsal olay kuralı — yalnız gerçek olay varsa
        ca = (corporate_actions or {}).get(ticker) if corporate_actions else None
        if ca:
            try:
                ev = _rule_split_at_peak(feat, ca)
                if ev is not None:
                    out.append(self._mk(ticker, "split_at_peak", ev))
            except Exception as e:
                log.debug("TahtaLab split_at_peak (%s) hata: %s", ticker, e)

        # Intraday kuralları (hold_above_open / pressure_below_open):
        # v1'de intraday veri yok → canlı uyarı üretilmez. intraday_df
        # ileride sağlanırsa scaffold burada genişletilir.
        # (intraday_df parametresi bilinçli olarak korunuyor.)

        return out

    # ── evren ──────────────────────────────────────────────────
    def evaluate_universe(
        self,
        universe_data: dict,
        index_df=None,
        index_return_1d: Optional[float] = None,
        corporate_actions: Optional[dict] = None,
    ) -> dict:
        """Bir hisse evrenini değerlendirir.

        universe_data: {ticker: daily_df}
        Döner: {"warnings_by_ticker": [...], "summary": {...}}
        """
        if index_return_1d is None and index_df is not None:
            index_return_1d = _index_return(index_df)

        all_warnings: list[TahtaWarning] = []

        for ticker, df in (universe_data or {}).items():
            try:
                ws = self.evaluate_ticker(
                    ticker, df, index_df=index_df,
                    corporate_actions=corporate_actions,
                    index_return_1d=index_return_1d,
                )
                all_warnings.extend(ws)
            except Exception as e:
                log.debug("TahtaLab evaluate_ticker (%s) hata: %s", ticker, e)

        # Piyasa modu — hisse-üstü tek uyarı ("PİYASA" başlığı altında)
        try:
            mkt = self._evaluate_market(index_df)
            if mkt is not None:
                all_warnings.append(mkt)
        except Exception as e:
            log.debug("TahtaLab market rule hata: %s", e)

        return self._build(all_warnings)

    # ── piyasa modu ────────────────────────────────────────────
    def _evaluate_market(self, index_df) -> Optional[TahtaWarning]:
        """Piyasa Modu Rotasyonu — endeks serisinden trend/yatay tespiti."""
        if index_df is None or not hasattr(index_df, "columns") or len(index_df) < 22:
            return None
        c = _col(index_df, "Close")
        if not c:
            return None
        closes = [x for x in c if x is not None]
        if len(closes) < 22:
            return None
        last = closes[-1]
        ma20 = sum(closes[-20:]) / 20.0
        ref = closes[-21] if len(closes) >= 21 else closes[0]
        ret_20d = (last / ref - 1.0) if ref else 0.0
        t = THRESHOLDS["market_rotation"]

        mode = None
        if last > ma20 and ret_20d >= t["trending_return_20d_min"]:
            mode = "trending"
            expl = ("Piyasa trendde. Büyük hisseler (BIST 30) "
                    "öncülük ediyor.")
        elif abs(ret_20d) < t["sideways_return_20d_abs_max"]:
            mode = "sideways"
            expl = ("Piyasa yatay seyrediyor. Para büyük hisselerden "
                    "yan tahtalara kayıyor olabilir.")
        if mode is None:
            return None

        d = get_definition("market_rotation")
        return TahtaWarning(
            ticker="PİYASA",
            warning_id="market_rotation",
            label_tr=d.label_tr,
            severity=d.severity_default,
            direction=d.direction,
            requires_intraday=False,
            data_available=True,
            explanation_tr=expl,
            evidence={
                "mode": mode,
                "index_vs_ma20": _r(last / ma20 - 1.0, 4),
                "index_return_20d": _r(ret_20d * 100, 2),
            },
        )

    # ── iç yardımcılar ─────────────────────────────────────────
    def _mk(self, ticker: str, wid: str, evidence: dict) -> TahtaWarning:
        d = get_definition(wid)
        return TahtaWarning(
            ticker=ticker,
            warning_id=wid,
            label_tr=d.label_tr,
            severity=d.severity_default,
            direction=d.direction,
            requires_intraday=d.requires_intraday,
            data_available=True,
            explanation_tr=_EXPLANATIONS.get(wid, d.short_description_tr),
            evidence=evidence,
        )

    def _build(self, warnings: list[TahtaWarning]) -> dict:
        """Uyarıları hisseye göre grupla + özet üret."""
        by_ticker: dict[str, list[TahtaWarning]] = {}
        for w in warnings:
            by_ticker.setdefault(w.ticker, []).append(w)

        groups: list[dict] = []
        for ticker, ws in by_ticker.items():
            ws_sorted = sorted(
                ws, key=lambda x: SEVERITY_RANK.get(x.severity, 0),
                reverse=True,
            )
            highest = ws_sorted[0].severity if ws_sorted else "info"
            groups.append({
                "ticker": ticker,
                "warning_count": len(ws_sorted),
                "highest_severity": highest,
                "warnings": [w.to_dict() for w in ws_sorted],
            })

        # Sırala: önce en yüksek seviye, sonra uyarı sayısı, sonra ad
        groups.sort(key=lambda g: (
            -SEVERITY_RANK.get(g["highest_severity"], 0),
            -g["warning_count"],
            g["ticker"],
        ))

        sev_counts = {"info": 0, "watch": 0, "warning": 0, "high_risk": 0}
        for w in warnings:
            if w.severity in sev_counts:
                sev_counts[w.severity] += 1

        summary = {
            "total_warnings": len(warnings),
            "tickers_with_warnings": len(by_ticker),
            "high_risk": sev_counts["high_risk"],
            "warning": sev_counts["warning"],
            "watch": sev_counts["watch"],
            "info": sev_counts["info"],
        }
        return {"warnings_by_ticker": groups, "summary": summary}


def _index_return(index_df) -> Optional[float]:
    """Endeks DataFrame'inden 1 günlük getiri."""
    if index_df is None or not hasattr(index_df, "columns") or len(index_df) < 2:
        return None
    c = _col(index_df, "Close")
    if not c or len(c) < 2 or c[-1] is None or c[-2] in (None, 0):
        return None
    return c[-1] / c[-2] - 1.0


# Modül seviyesi tekil motor
ENGINE = TahtaWarningEngine()

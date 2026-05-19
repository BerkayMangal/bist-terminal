# ================================================================
# BISTBULL TERMINAL — TAHTALAB UYARI KÜTÜPHANESİ
# engine/tahta_warning_registry.py
#
# TahtaLab, BIST'te sık görülen "tahta davranışı" uyarılarını yakalayan
# bağımsız bir uyarı laboratuvarıdır. AL/SAT önerisi DEĞİLDİR.
#
# Bu dosya 10 uyarı kuralının TİPLİ tanımını ve eşiklerini tutar.
# Her kural birbirinden BAĞIMSIZ değerlendirilir (OR-bazlı eşleşme):
# bir hisse en az BİR kuralı tetiklerse TahtaLab'da görünür.
#
# Saf veri — IO/cache yok.
# ================================================================

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


# ────────────────────────────────────────────────────────────────
# Uyarı tanımı — tipli kayıt
# ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TahtaWarningDefinition:
    warning_id: str
    label_tr: str
    short_description_tr: str
    severity_default: str          # info | watch | warning | high_risk
    direction: str                 # risk | reaction | strength | context
    requires_intraday: bool
    requires_corporate_action: bool
    data_requirements: list[str]
    enabled: bool
    display_order: int
    user_copy_tr: str
    unavailable_reason_tr: Optional[str]
    testability: str                # daily | intraday | corporate_action | market_regime


# ────────────────────────────────────────────────────────────────
# Eşikler — tek yerde, ayarlanabilir. Kural fonksiyonları buradan okur.
# ────────────────────────────────────────────────────────────────
THRESHOLDS: dict[str, dict] = {
    "weak_pre_limit": {
        "intraday_high_return": 0.08,
        "close_position_max": 0.65,
        "upper_wick_ratio_min": 0.25,
        "volume_ratio_min": 1.5,
    },
    "base_rebound": {
        "low_return_max": -0.08,
        "close_position_min": 0.55,
        "close_to_low_rebound_min": 0.03,
        "volume_ratio_min": 1.3,
    },
    "hold_above_open": {
        "above_open_ratio_min": 0.70,
        "early_volume_ratio_min": 1.2,
    },
    "pressure_below_open": {
        "below_open_ratio_min": 0.70,
        "below_vwap_ratio_min": 0.60,
    },
    "split_at_peak": {
        "close_to_52w_high_min": 0.90,
        "return_60d_min": 0.40,
        "volume_ratio_min": 1.5,
    },
    "market_rotation": {
        "trending_return_20d_min": 0.03,
        "sideways_return_20d_abs_max": 0.02,
    },
    "weak_continuation": {
        "prev_return_min": 0.05,
        "prev_volume_ratio_min": 2.0,
        "followup_volume_ratio_max": 0.65,
        # 1.00: bugünkü high dünküyü AŞMADI = zayıf devam. Eski 1.01,
        # dünküden %1 YUKARI yeni bir zirveyi bile "zayıf" sayıyordu —
        # karşılaştırma yanlış taraftaydı (audit H4).
        "high_breach_max": 1.00,
        "close_position_max": 0.50,
    },
    "close_selloff": {
        "upper_wick_ratio_min": 0.35,
        "close_position_max": 0.45,
        "volume_ratio_min": 1.5,
    },
    "unconfirmed_breakout": {
        "volume_ratio_max": 1.2,
        # close direncin en az %0.5 üstünde olmalı — sıfır marjin,
        # düz 20g tavanındaki gün-içi gürültüyü "kırılım" sanıyordu
        # (audit M4).
        "breakout_margin": 0.005,
    },
    "strong_vs_index": {
        "index_return_max": -0.01,
        # hisse gerçekten artıda olmalı — 0.0 düz kapanışı da "güçlü"
        # sayıyordu; hacim en az ortalama olmalı — 0.8 düşük hacimde
        # "göreceli güç" zayıf kanıt (audit M3).
        "stock_return_min": 0.005,
        "close_position_min": 0.60,
        "volume_ratio_min": 1.0,
    },
}


_INTRADAY_UNAVAILABLE = (
    "Gün içi (intraday) veri gerekiyor — şu an mevcut değil, "
    "bu kural canlı uyarı üretmiyor."
)
_CORP_UNAVAILABLE = (
    "KAP / bölünme (bedelsiz) verisi gerekiyor — tetikleyici olay "
    "olmadan bu kural canlı uyarı üretmiyor."
)


# ────────────────────────────────────────────────────────────────
# 10 v1 uyarı kuralı
# ────────────────────────────────────────────────────────────────
WARNING_REGISTRY: list[TahtaWarningDefinition] = [
    TahtaWarningDefinition(
        warning_id="weak_pre_limit",
        label_tr="Tavan Öncesi Yorulma",
        short_description_tr="Güçlü yükseldi ama tavan gücü oluşmadı.",
        severity_default="warning",
        direction="risk",
        requires_intraday=False,
        requires_corporate_action=False,
        data_requirements=["daily_ohlcv"],
        enabled=True,
        display_order=1,
        user_copy_tr=(
            "Hisse gün içinde güçlü yükseldi ama kapanışta tavan gücü "
            "oluşmadı. Geçmişte bu görünümün ardından sık sık geri "
            "çekilme görülmüştür."
        ),
        unavailable_reason_tr=None,
        testability="daily",
    ),
    TahtaWarningDefinition(
        warning_id="base_rebound",
        label_tr="Taban Çözülme Tepkisi",
        short_description_tr="Taban bölgesinden tepki verdi.",
        severity_default="watch",
        direction="reaction",
        requires_intraday=False,
        requires_corporate_action=False,
        data_requirements=["daily_ohlcv"],
        enabled=True,
        display_order=2,
        user_copy_tr=(
            "Hisse taban bölgesine değdi ama orada kilitlenmedi; "
            "kapanışa doğru tepki verdi. Satış baskısı kısa vadede "
            "zayıflıyor olabilir — takip edilmeli."
        ),
        unavailable_reason_tr=None,
        testability="daily",
    ),
    TahtaWarningDefinition(
        warning_id="hold_above_open",
        label_tr="Açılış Üstü Tutunma",
        short_description_tr="Açılışın üstünde tutunuyor.",
        severity_default="info",
        direction="strength",
        requires_intraday=True,
        requires_corporate_action=False,
        data_requirements=["intraday_prices"],
        enabled=True,
        display_order=3,
        user_copy_tr=(
            "Hisse ilk dakikalarda açılış fiyatının üstünde tutunuyor; "
            "gün içi alıcı ilgisi öne çıkıyor."
        ),
        unavailable_reason_tr=_INTRADAY_UNAVAILABLE,
        testability="intraday",
    ),
    TahtaWarningDefinition(
        warning_id="pressure_below_open",
        label_tr="Açılış Altı Baskı",
        short_description_tr="Açılışın altında oyalanıyor.",
        severity_default="watch",
        direction="risk",
        requires_intraday=True,
        requires_corporate_action=False,
        data_requirements=["intraday_prices"],
        enabled=True,
        display_order=4,
        user_copy_tr=(
            "Hisse ilk dakikalarda açılış fiyatının altında kalıyor; "
            "gün içi satış baskısı öne çıkıyor."
        ),
        unavailable_reason_tr=_INTRADAY_UNAVAILABLE,
        testability="intraday",
    ),
    TahtaWarningDefinition(
        warning_id="split_at_peak",
        label_tr="Zirvede Bölünme Riski",
        short_description_tr="Bölünme haberi fiyat zirvedeyken geldi.",
        severity_default="high_risk",
        direction="risk",
        requires_intraday=False,
        requires_corporate_action=True,
        data_requirements=["daily_ohlcv", "corporate_actions"],
        enabled=True,
        display_order=5,
        user_copy_tr=(
            "Bölünme/bedelsiz haberi, fiyatın çok yükseldiği bir bölgede "
            "geldi. Geçmişte bu görünümün ardından haber sonrası satış "
            "baskısı sık görülmüştür."
        ),
        unavailable_reason_tr=_CORP_UNAVAILABLE,
        testability="corporate_action",
    ),
    TahtaWarningDefinition(
        warning_id="market_rotation",
        label_tr="Piyasa Modu Rotasyonu",
        short_description_tr="Piyasa modu değişiyor olabilir.",
        severity_default="info",
        direction="context",
        requires_intraday=False,
        requires_corporate_action=False,
        data_requirements=["index_daily_ohlcv"],
        enabled=True,
        display_order=6,
        user_copy_tr=(
            "Piyasa modu (trend / yatay) değişiyor olabilir. Yatay "
            "piyasada para büyük hisselerden yan tahtalara kayma "
            "eğilimi gösterir — bu bir bağlam notudur."
        ),
        unavailable_reason_tr=(
            "Endeks (BIST 100) günlük veri serisi gerekiyor."
        ),
        testability="market_regime",
    ),
    TahtaWarningDefinition(
        warning_id="weak_continuation",
        label_tr="Devam Alıcısı Zayıf",
        short_description_tr="Dünkü güçlü hareket bugün desteklenmedi.",
        severity_default="warning",
        direction="risk",
        requires_intraday=False,
        requires_corporate_action=False,
        data_requirements=["daily_ohlcv"],
        enabled=True,
        display_order=7,
        user_copy_tr=(
            "Dünkü güçlü ve yüksek hacimli hareket bugün hacimle "
            "desteklenmedi. Geçmişte devam alıcısının zayıf kaldığı bu "
            "görünüm sık sık duraklamayla sonuçlanmıştır."
        ),
        unavailable_reason_tr=None,
        testability="daily",
    ),
    TahtaWarningDefinition(
        warning_id="close_selloff",
        label_tr="Kapanışta Satış Baskısı",
        short_description_tr="Gün içi kazancını kapanışa doğru geri verdi.",
        severity_default="warning",
        direction="risk",
        requires_intraday=False,
        requires_corporate_action=False,
        data_requirements=["daily_ohlcv"],
        enabled=True,
        display_order=8,
        user_copy_tr=(
            "Hisse gün içi kazancının önemli bölümünü kapanışa doğru "
            "geri verdi (uzun üst fitil). Geçmişte kapanış satış "
            "baskısının ardından zayıf seyir sık görülmüştür."
        ),
        unavailable_reason_tr=None,
        testability="daily",
    ),
    TahtaWarningDefinition(
        warning_id="unconfirmed_breakout",
        label_tr="Kırılım Teyitsiz",
        short_description_tr="Direnç üstüne çıktı ama hacim teyit etmiyor.",
        severity_default="watch",
        direction="risk",
        requires_intraday=False,
        requires_corporate_action=False,
        data_requirements=["daily_ohlcv"],
        enabled=True,
        display_order=9,
        user_copy_tr=(
            "Fiyat son 20 günün direnç bölgesinin üstüne çıktı ama "
            "hacim bu hareketi güçlü biçimde teyit etmiyor. Hacimsiz "
            "kırılımlar geçmişte sık sık kalıcı olmamıştır."
        ),
        unavailable_reason_tr=None,
        testability="daily",
    ),
    TahtaWarningDefinition(
        warning_id="strong_vs_index",
        label_tr="Endekse Karşı Güçlü",
        short_description_tr="Endeks zayıfken hisse ayakta kaldı.",
        severity_default="info",
        direction="strength",
        requires_intraday=False,
        requires_corporate_action=False,
        data_requirements=["daily_ohlcv", "index_daily_return"],
        enabled=True,
        display_order=10,
        user_copy_tr=(
            "Endeks (BIST 100) gün içinde zayıfken hisse ayakta kaldı "
            "ve günü güçlü kapattı; göreceli güç öne çıkıyor — bu bir "
            "bağlam notudur."
        ),
        unavailable_reason_tr=(
            "Endeks (BIST 100) günlük getirisi gerekiyor."
        ),
        testability="daily",
    ),
]


# ────────────────────────────────────────────────────────────────
# Erişim yardımcıları
# ────────────────────────────────────────────────────────────────
_BY_ID: dict[str, TahtaWarningDefinition] = {
    d.warning_id: d for d in WARNING_REGISTRY
}

# Geçerli seviye sıralaması — UI rozetleri + en yüksek-seviye hesabı
SEVERITY_RANK: dict[str, int] = {
    "info": 0, "watch": 1, "warning": 2, "high_risk": 3,
}


def get_definition(warning_id: str) -> Optional[TahtaWarningDefinition]:
    """Tek kural tanımını döndür."""
    return _BY_ID.get(warning_id)


def get_rule_library() -> list[dict]:
    """Tüm kuralların display_order'a göre sıralı dict listesi —
    frontend "Kural Kütüphanesi" bölümü bunu kullanır."""
    return [
        asdict(d)
        for d in sorted(WARNING_REGISTRY, key=lambda x: x.display_order)
    ]

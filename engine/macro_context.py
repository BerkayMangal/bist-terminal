# ================================================================
# BISTBULL TERMINAL — MERKEZİ MAKRO BAĞLAM
# engine/macro_context.py
#
# TCMB politika faizi, yıllık enflasyon ve CDS — TEK kaynaktan.
#
# Eskiden bu değerler skorlama motoruna hardcoded'du ve dahası
# TUTARSIZDI:
#   - analysis.py        → policy_rate=37.0, inflation=0.40
#   - scoring.py         → BIST_INFLATION_RATE=0.33
#   - turkey_realities   → inflation = policy_rate*0.8 ≈ 0.296
# Üç ayrı enflasyon değeri. Faiz değişince hiçbiri güncellenmiyordu.
#
# Artık hepsi config.STATIC_RATES'ten okunur. STATIC_RATES tek elle
# güncellenen tablo (Makro sekmesindeki "Faiz & Risk" kartının da
# kaynağı) — orası güncellenince TÜM skorlama yeniden kalibre olur.
#
# Saf okuma — IO/cache yok. STATIC_RATES bir modül-seviyesi liste.
# ================================================================

from __future__ import annotations

from config import STATIC_RATES, BIST_INFLATION_RATE


# STATIC_RATES'te ilgili anahtar bulunamazsa kullanılacak güvenli
# varsayılanlar (yüksek-faiz Türkiye ortamı için makul).
_DEFAULT_POLICY_RATE: float = 37.0          # %
_DEFAULT_INFLATION: float = BIST_INFLATION_RATE  # oran (config'ten)
_DEFAULT_CDS: float = 295.0                 # bps


def _rate(key: str, default: float) -> float:
    """STATIC_RATES içinden `key` satırının `rate` değerini döndür."""
    for row in STATIC_RATES:
        if row.get("key") == key:
            val = row.get("rate")
            try:
                return float(val)
            except (TypeError, ValueError):
                return default
    return default


def get_policy_rate() -> float:
    """TCMB politika faizi, yüzde cinsinden (ör: 37.0)."""
    return _rate("TCMB", _DEFAULT_POLICY_RATE)


def get_inflation_rate() -> float:
    """Yıllık TÜFE, ORAN cinsinden (ör: 0.33).

    STATIC_RATES'te 'CPI_TR' yüzde olarak saklanır; burada orana
    çevrilir. Skorlama motoru (score_growth reel büyüme) bunu kullanır.
    """
    return _rate("CPI_TR", _DEFAULT_INFLATION * 100.0) / 100.0


def get_cds() -> float:
    """Türkiye 5Y CDS, baz puan (bps)."""
    return _rate("CDS_TR", _DEFAULT_CDS)


def get_real_rate() -> float:
    """Reel politika faizi, oran cinsinden.

    Fisher: (1 + nominal faiz) / (1 + enflasyon) - 1.
    Pozitif = faiz enflasyonu yeniyor (TL'yi tutmak mantıklı / sıkı
    para politikası); negatif = enflasyon faizi yiyor.
    """
    nominal = get_policy_rate() / 100.0
    inflation = get_inflation_rate()
    return (1.0 + nominal) / (1.0 + inflation) - 1.0

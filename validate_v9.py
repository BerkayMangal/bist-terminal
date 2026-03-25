#!/usr/bin/env python3
"""
BistBull V9 — Veri Kalitesi Validasyon
Railway'de çalıştır: python validate_v9.py

Çıktı:
1. Her ticker için coverage raporu
2. Kritik metriklerin değerleri
3. Banka vs sanayi ayrımı doğru mu?
4. Fintables cross-check tablosu
"""

import sys
import json

try:
    from data_layer_v9 import (
        compute_metrics_v9,
        fetch_raw_v9,
        diagnose_ticker,
        is_bank,
        BORSAPY_AVAILABLE,
    )
except ImportError:
    print("ERROR: data_layer_v9.py bulunamadı. Aynı dizinde olmalı.")
    sys.exit(1)

if not BORSAPY_AVAILABLE:
    print("ERROR: borsapy kurulu değil. pip install borsapy")
    sys.exit(1)


# ================================================================
# TEST UNIVERSE — çeşitli sektörlerden
# ================================================================
TEST_TICKERS = {
    # Sanayi
    "THYAO": "Havacılık",
    "ASELS": "Savunma",
    "BIMAS": "Perakende",
    "EREGL": "Demir/Çelik",
    "TUPRS": "Enerji/Rafineri",
    "FROTO": "Otomotiv",
    "TCELL": "Telekom",
    "SISE": "Holding/Cam",
    # Bankalar (UFRS)
    "AKBNK": "Banka",
    "GARAN": "Banka",
    "ISCTR": "Banka",
    # Holding
    "KCHOL": "Holding",
    "SAHOL": "Holding",
}

# Fintables'dan elle kontrol edilecek değerler
# Her ticker için yaklaşık beklenen aralıklar
# BU ARALIKLARI FINTABLES'DAN DOLDUR
EXPECTED_RANGES = {
    "THYAO": {
        "pe": (1.5, 8.0),          # genelde düşük F/K
        "roe": (0.20, 0.80),       # yüksek ROE
        "revenue": (100e9, 500e9), # 100B-500B TL
    },
    "AKBNK": {
        "pe": (2.0, 10.0),
        "pb": (0.5, 3.0),
    },
    "ASELS": {
        "pe": (10, 40),
        "revenue": (50e9, 200e9),
    },
}


def validate_all():
    print("=" * 70)
    print("BistBull V9 — Veri Kalitesi Validasyon")
    print("=" * 70)
    
    results = []
    errors = []
    
    for ticker, sector in TEST_TICKERS.items():
        print(f"\n{'─'*50}")
        print(f"  {ticker} ({sector})")
        print(f"{'─'*50}")
        
        # Bank check
        is_ufrs = is_bank(ticker)
        print(f"  UFRS (banka): {'EVET' if is_ufrs else 'HAYIR'}")
        
        try:
            report = diagnose_ticker(ticker)
            
            if "error" in report:
                print(f"  ❌ ERROR: {report['error']}")
                errors.append((ticker, report["error"]))
                continue
            
            coverage = report["coverage"]
            status = "✅" if coverage >= 70 else ("⚠️" if coverage >= 50 else "❌")
            print(f"  {status} Coverage: {coverage}%")
            print(f"  📊 Source: {report.get('data_source')}")
            
            if report.get("foreign_ratio") is not None:
                print(f"  🌍 Yabancı oranı: %{report['foreign_ratio']*100:.1f}")
            if report.get("free_float") is not None:
                print(f"  📈 Halka açıklık: %{report['free_float']*100:.1f}")
            
            # Kritik alanlar
            print(f"  Detay:")
            for field, info in report.get("fields", {}).items():
                status_icon = "  ✓" if info["has_data"] else "  ✗"
                val = info["value"]
                if isinstance(val, (int, float)):
                    if abs(val) >= 1e9:
                        val_str = f"{val/1e9:.2f}B TL"
                    elif abs(val) >= 1e6:
                        val_str = f"{val/1e6:.1f}M TL"
                    elif abs(val) < 1:
                        val_str = f"%{val*100:.1f}"
                    else:
                        val_str = f"{val:.2f}"
                else:
                    val_str = str(val)
                print(f"    {status_icon} {field}: {val_str}")
            
            # Cross-check with expected ranges
            if ticker in EXPECTED_RANGES:
                print(f"  🔍 Fintables cross-check:")
                m = compute_metrics_v9(ticker)
                for metric, (lo, hi) in EXPECTED_RANGES[ticker].items():
                    actual = m.get(metric)
                    if actual is None:
                        print(f"    ⚠️  {metric}: VERİ YOK (beklenen: {lo}-{hi})")
                    elif lo <= actual <= hi:
                        print(f"    ✅ {metric}: {actual:.2f} (beklenen: {lo}-{hi})")
                    else:
                        print(f"    ❌ {metric}: {actual:.2f} ARALIK DIŞI (beklenen: {lo}-{hi})")
            
            results.append((ticker, coverage))
            
        except Exception as e:
            print(f"  ❌ EXCEPTION: {e}")
            errors.append((ticker, str(e)))
    
    # Summary
    print(f"\n{'='*70}")
    print("ÖZET")
    print(f"{'='*70}")
    
    if results:
        avg_coverage = sum(c for _, c in results) / len(results)
        print(f"  Test edilen: {len(results)} / {len(TEST_TICKERS)}")
        print(f"  Ortalama coverage: {avg_coverage:.1f}%")
        
        good = sum(1 for _, c in results if c >= 70)
        warn = sum(1 for _, c in results if 50 <= c < 70)
        bad = sum(1 for _, c in results if c < 50)
        print(f"  ✅ İyi (≥70%): {good}")
        print(f"  ⚠️ Orta (50-70%): {warn}")
        print(f"  ❌ Zayıf (<50%): {bad}")
    
    if errors:
        print(f"\n  HATALAR ({len(errors)}):")
        for ticker, err in errors:
            print(f"    {ticker}: {err}")
    
    print(f"\n{'='*70}")
    print("SONRAKİ ADIMLAR:")
    print("  1. Coverage < 70% olan ticker'lar için satır isimlerini kontrol et")
    print("  2. Fintables.com'dan manuel F/K, PD/DD, ROE değerlerini karşılaştır")
    print("  3. Banka bilanço satır isimleri UFRS formatına uyuyor mu?")
    print("  4. data_layer_v9.py'deki BS_MAP/IS_MAP/CF_MAP'i güncelle")
    print(f"{'='*70}")


if __name__ == "__main__":
    validate_all()

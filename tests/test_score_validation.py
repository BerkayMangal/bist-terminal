# ================================================================
# tests/test_score_validation.py
#
# Radar skor doğrulama (event-study) çekirdek testleri.
# event_study_from_rows saf bir fonksiyon — sentetik satırlarla
# bant ayrımı, ileri-getiri eşleme ve "veri yetersiz" durumu sınanır.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from research.score_validation import event_study_from_rows, _bucket


class TestBucket:
    def test_bucket_boundaries(self):
        assert _bucket(85) == "60+ (güçlü)"
        assert _bucket(60) == "60+ (güçlü)"
        assert _bucket(59.9) == "45-60 (orta)"
        assert _bucket(45) == "45-60 (orta)"
        assert _bucket(30) == "30-45 (zayıf)"
        assert _bucket(29) == "0-30 (riskli)"
        assert _bucket(1) == "0-30 (riskli)"


class TestEventStudy:
    def test_insufficient_data(self):
        # Tek kayıt — ileri-getiri çifti oluşmaz
        r = event_study_from_rows([("X", "2026-05-01", 70, 100)], 20)
        assert r["status"] == "veri_yetersiz"
        assert r["pairs"] == 0

    def test_horizon_respected(self):
        # 10 gün arayla iki kayıt, horizon 20 → çift yok
        rows = [("X", "2026-05-01", 70, 100), ("X", "2026-05-11", 70, 110)]
        assert event_study_from_rows(rows, 20)["status"] == "veri_yetersiz"
        # horizon 10 → çift oluşur
        assert event_study_from_rows(rows, 10)["status"] == "ok"

    def test_forward_return_and_buckets(self):
        rows = [
            ("AAA", "2026-05-01", 75, 100), ("AAA", "2026-05-25", 75, 120),
            ("CCC", "2026-05-01", 35, 100), ("CCC", "2026-05-25", 35, 95),
            ("DDD", "2026-05-01", 20, 100), ("DDD", "2026-05-25", 20, 85),
        ]
        r = event_study_from_rows(rows, 20)
        assert r["status"] == "ok"
        assert r["pairs"] == 3
        by = {b["bucket"]: b for b in r["buckets"]}
        assert by["60+ (güçlü)"]["avg_return_pct"] == 20.0
        assert by["30-45 (zayıf)"]["avg_return_pct"] == -5.0
        assert by["0-30 (riskli)"]["avg_return_pct"] == -15.0

    def test_monotonic_flag(self):
        # Yüksek bant > düşük bant → monotonic True
        rows = [
            ("A", "2026-05-01", 70, 100), ("A", "2026-05-25", 70, 115),
            ("B", "2026-05-01", 20, 100), ("B", "2026-05-25", 20, 90),
        ]
        assert event_study_from_rows(rows, 20)["monotonic"] is True

    def test_null_and_bad_rows_skipped(self):
        rows = [
            ("A", "2026-05-01", None, 100), ("A", "2026-05-25", 70, 120),
            ("B", "2026-05-01", 70, None), ("B", "2026-05-25", 70, 120),
            ("C", "2026-05-01", 70, 0), ("C", "2026-05-25", 70, 120),
        ]
        # Hiçbiri geçerli taban kaydı sağlamaz → çift yok
        assert event_study_from_rows(rows, 20)["status"] == "veri_yetersiz"

    def test_bad_date_does_not_crash(self):
        rows = [("A", "not-a-date", 70, 100), ("A", "2026-05-25", 70, 120)]
        r = event_study_from_rows(rows, 20)
        assert r["status"] in ("ok", "veri_yetersiz")

    def test_only_first_horizon_match_used(self):
        # 3 kayıt: taban + 2 ileri kayıt — yalnız İLK horizon-aşan kullanılır
        rows = [
            ("A", "2026-05-01", 70, 100),
            ("A", "2026-05-25", 70, 110),  # +24g — bu kullanılır
            ("A", "2026-06-20", 70, 200),  # daha sonra — kullanılmaz
        ]
        r = event_study_from_rows(rows, 20)
        b = {x["bucket"]: x for x in r["buckets"]}["60+ (güçlü)"]
        # Taban 05-01 → 05-25 (%10). 05-25 → 06-20 (+%82) ayrı çift.
        assert b["n"] == 2

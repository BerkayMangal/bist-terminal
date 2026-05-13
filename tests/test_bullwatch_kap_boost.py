# ================================================================
# tests/test_bullwatch_kap_boost.py
#
# Tahtacı PR A2 — BullWatch KAP boost engine.
# Verifies that recent operator-signal disclosures translate into a
# bounded sub-score with the right per-tag weighting and decay.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine.bullwatch_kap_boost import (
    compute_kap_boost,
    TAG_WEIGHTS,
    LOOKBACK_DAYS,
)


def _disclosure(idx: int, ticker: str, subject: str,
                days_ago: int = 1) -> dict:
    """Build a kap_disclosures-row-shaped dict for storage mocks."""
    pub = (_dt.datetime.now(_dt.timezone.utc)
           - _dt.timedelta(days=days_ago)).isoformat()
    return {
        "disclosure_index": idx,
        "ticker":           ticker,
        "subject":          subject,
        "publish_date":     pub,
    }


# ── 1. No data paths ───────────────────────────────────────────────


class TestNoData:
    def test_empty_ticker_returns_none(self, monkeypatch):
        score, reasons, meta = compute_kap_boost("")
        assert score is None
        assert reasons == []
        assert meta == {}

    def test_no_rows_returns_none(self, monkeypatch):
        from infra import kap_storage
        monkeypatch.setattr(kap_storage, "get_by_ticker", lambda t, limit=50: [])
        score, reasons, meta = compute_kap_boost("ARCLK")
        assert score is None
        assert reasons == []

    def test_all_rows_outside_window_returns_none(self, monkeypatch):
        """Old disclosures shouldn't keep the score alive forever."""
        from infra import kap_storage
        old_rows = [_disclosure(1, "ARCLK", "Pay Alım Satım Bildirimi",
                                days_ago=60)]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: old_rows)
        score, reasons, meta = compute_kap_boost("ARCLK",
                                                  lookback_days=14)
        assert score is None
        assert meta.get("signals_in_window", 0) == 0


# ── 2. Operator-signal scoring ─────────────────────────────────────


class TestOperatorScoring:
    def test_single_insider_buy(self, monkeypatch):
        from infra import kap_storage
        rows = [_disclosure(1, "KAPLM", "Pay Alım Satım Bildirimi",
                            days_ago=2)]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        score, reasons, meta = compute_kap_boost("KAPLM")
        # 1 INSIDER firing → 0.40 * 1.0 = 0.40
        assert score == pytest.approx(TAG_WEIGHTS["INSIDER"])
        assert meta["dominant_tag"] == "INSIDER"
        assert meta["tag_counts"] == {"INSIDER": 1}
        assert any("içeriden" in r.lower() for r in reasons)

    def test_two_insider_buys_diminishing(self, monkeypatch):
        from infra import kap_storage
        rows = [
            _disclosure(1, "KAPLM", "Pay Alım Satım Bildirimi", days_ago=2),
            _disclosure(2, "KAPLM", "Pay Sahipliği Bildirimi", days_ago=5),
        ]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        score, reasons, meta = compute_kap_boost("KAPLM")
        # 2 INSIDER firings → 0.40 × 1.5 = 0.60 (capped at 1.0)
        assert score == pytest.approx(0.60)
        assert meta["tag_counts"]["INSIDER"] == 2

    def test_three_plus_saturates_at_2x_multiplier(self, monkeypatch):
        from infra import kap_storage
        rows = [
            _disclosure(i, "KAPLM", "Pay Alım Satım Bildirimi", days_ago=i)
            for i in (1, 2, 3, 4, 5)
        ]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        score, reasons, meta = compute_kap_boost("KAPLM")
        # 5 firings → multiplier capped at 2.0 → 0.40 × 2.0 = 0.80
        assert score == pytest.approx(0.80)

    def test_multi_tag_combines(self, monkeypatch):
        """Insider + KAP alert + buyback in the same window —
        weighted sum, capped at 1.0."""
        from infra import kap_storage
        rows = [
            _disclosure(1, "KAPLM", "Pay Alım Satım Bildirimi", days_ago=1),
            _disclosure(2, "KAPLM", "Olağan Dışı Fiyat Hareketi", days_ago=3),
            _disclosure(3, "KAPLM", "Pay Geri Alım Programı", days_ago=5),
        ]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        score, reasons, meta = compute_kap_boost("KAPLM")
        # 0.40 + 0.25 + 0.20 = 0.85
        assert score == pytest.approx(0.85, abs=0.01)
        assert set(meta["tag_counts"].keys()) == {"INSIDER", "KAP_ALERT", "BUYBACK"}

    def test_score_caps_at_one(self, monkeypatch):
        """Heavy combined activity shouldn't exceed 1.0 (BullWatch
        engine contract)."""
        from infra import kap_storage
        rows = [
            _disclosure(i, "KAPLM",
                        s if i % 5 != 0 else "Bedelsiz Sermaye Artırımı",
                        days_ago=i)
            for i, s in enumerate([
                "Pay Alım Satım Bildirimi",
                "Olağan Dışı Fiyat Hareketi",
                "Pay Geri Alım Programı",
                "Finansal Duran Varlık Edinimi",
                "Yönetim Kurulu Üye Değişikliği",
            ] * 3, start=1)
        ]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        score, _, _ = compute_kap_boost("KAPLM")
        assert score is not None
        assert score <= 1.0

    def test_unclassified_only_returns_zero(self, monkeypatch):
        """Plain "Özel Durum Açıklaması (Genel)" without operator pattern
        → engine fires (had data) but contributes 0 (no operator signal).
        This is informative: regular KAP activity without insider /
        regulator / corporate-event content."""
        from infra import kap_storage
        rows = [
            _disclosure(1, "ARCLK", "Özel Durum Açıklaması (Genel)", days_ago=2),
            _disclosure(2, "ARCLK", "Genel Açıklama", days_ago=5),
        ]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        score, reasons, meta = compute_kap_boost("ARCLK")
        assert score == 0.0
        assert reasons == []
        assert meta["tag_counts"] == {}

    def test_window_filtering(self, monkeypatch):
        """In-window insider buy + out-of-window mgmt change — only
        the recent one counts."""
        from infra import kap_storage
        rows = [
            _disclosure(1, "KAPLM", "Pay Alım Satım Bildirimi", days_ago=5),
            _disclosure(2, "KAPLM", "Yönetim Kurulu Üye Değişikliği",
                        days_ago=30),  # outside 14d window
        ]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        score, _, meta = compute_kap_boost("KAPLM", lookback_days=14)
        assert score == pytest.approx(TAG_WEIGHTS["INSIDER"])
        assert "MGMT_CHANGE" not in meta["tag_counts"]


# ── 3. Engine contract — sub_score is [0, 1] or None ───────────────


class TestEngineContract:
    def test_score_in_range(self, monkeypatch):
        """For any realistic input, sub_score is None or in [0, 1]."""
        from infra import kap_storage
        # Realistic high-activity ticker
        rows = [
            _disclosure(1, "X", "Pay Alım Satım Bildirimi", days_ago=1),
            _disclosure(2, "X", "Pay Alım Satım Bildirimi", days_ago=3),
            _disclosure(3, "X", "Olağan Dışı Fiyat Hareketi", days_ago=5),
            _disclosure(4, "X", "Pay Geri Alım Programı", days_ago=7),
        ]
        monkeypatch.setattr(kap_storage, "get_by_ticker",
                            lambda t, limit=50: rows)
        score, _, _ = compute_kap_boost("X")
        assert score is not None
        assert 0.0 <= score <= 1.0

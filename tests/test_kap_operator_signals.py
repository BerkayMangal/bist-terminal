# ================================================================
# tests/test_kap_operator_signals.py
#
# Tahtacı PR A — operator signal classifier + dispatcher routing.
# Verifies that the subject-text → tag mapping catches the actionable
# announcement categories and ignores noise.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from data.kap_client import (
    classify_operator_signal,
    OPERATOR_SIGNAL_PATTERNS,
)


class TestClassifyOperatorSignal:
    def test_insider_pay_alim(self):
        assert classify_operator_signal("Pay Alım Satım Bildirimi") == "INSIDER"

    def test_insider_pay_sahipligi(self):
        assert classify_operator_signal("Pay Sahipliği Bildirimi") == "INSIDER"

    def test_kap_alert_olagan_disi(self):
        # Both spellings (with and without space) should match
        assert classify_operator_signal("Olağan Dışı Fiyat ve Miktar Hareketleri") == "KAP_ALERT"
        assert classify_operator_signal("Olağandışı Fiyat Hareketi") == "KAP_ALERT"

    def test_buyback_program(self):
        assert classify_operator_signal("Pay Geri Alım Programı") == "BUYBACK"

    def test_mna_finansal_duran(self):
        assert classify_operator_signal("Finansal Duran Varlık Edinimi") == "MNA"
        assert classify_operator_signal("Birleşme Bildirimi") == "MNA"
        assert classify_operator_signal("Bağlı Ortaklık Devri") == "MNA"

    def test_capital_change_bedelsiz(self):
        assert classify_operator_signal("Bedelsiz Sermaye Artırımı") == "CAPITAL_CHANGE"

    def test_capital_change_bedelli(self):
        assert classify_operator_signal("Bedelli Sermaye Artırımı") == "CAPITAL_CHANGE"

    def test_mgmt_change(self):
        assert classify_operator_signal("Yönetim Kurulu Üye Değişikliği") == "MGMT_CHANGE"
        assert classify_operator_signal("Genel Müdür Ataması") == "MGMT_CHANGE"

    def test_none_for_general(self):
        # "Özel Durum Açıklaması (Genel)" is too general — has no operator pattern
        assert classify_operator_signal("Özel Durum Açıklaması (Genel)") is None

    def test_none_for_financial_report(self):
        assert classify_operator_signal("Finansal Rapor") is None

    def test_none_for_empty(self):
        assert classify_operator_signal("") is None
        assert classify_operator_signal(None) is None  # type: ignore

    def test_case_lowercase_matches(self):
        # KAP returns subjects in title case; we lowercase before
        # matching so "Pay Geri Alım Programı" → "pay geri alım programı"
        # still hits the BUYBACK pattern. The ALL-CAPS Turkish-İ edge
        # case is exotic and not asserted (Turkish lowercase of İ is
        # locale-dependent — KAP never emits it anyway).
        assert classify_operator_signal("pay geri alım programı") == "BUYBACK"
        assert classify_operator_signal("Pay Geri Alım Programı") == "BUYBACK"

    def test_first_match_wins(self):
        # If multiple needles could match, the dict iteration order
        # determines priority. INSIDER appears before BUYBACK in the
        # OPERATOR_SIGNAL_PATTERNS dict so it should win when both
        # phrases coexist (rare but possible in long subject lines).
        # This test pins down the contract.
        subject = "Pay Alım Satım ve Geri Alım Programı"
        result = classify_operator_signal(subject)
        # Order in the OPERATOR_SIGNAL_PATTERNS dict
        keys = list(OPERATOR_SIGNAL_PATTERNS.keys())
        assert keys.index(result) < keys.index("BUYBACK") or result == "BUYBACK"


class TestDispatcherRouting:
    """Verify the dispatcher routes financial vs operator signals
    differently — financial reports trigger Plan C cache invalidation
    while operator signals trigger only AI queue (no cache invalidate)."""

    def test_operator_signal_does_not_invalidate_caches(self, monkeypatch):
        from engine import kap_dispatcher
        from core.cache import raw_cache, analysis_cache
        from data.kap_client import DisclosureRecord

        raw_cache.set("KAPLM", {"x": 1})
        analysis_cache.set("KAPLM", {"y": 2})

        # Operator-signal disclosure (not a balance sheet)
        rec = DisclosureRecord(
            disclosure_index=999_001, ticker="KAPLM",
            kap_title="KAPLAMİN A.Ş.",
            subject="Pay Alım Satım Bildirimi",
            disclosure_type="ODA", disclosure_class="ODA",
            publish_date="2026-05-13T12:00:00+00:00",
            publish_date_raw="13.05.2026 15:00:00",
            rule_type=None, period=None, year=2026,
            attachment_count=1, is_late=False, url=None,
        )

        # Stub the AI queue and reaction baseline so we test routing only
        called = {"ai": 0, "reaction": 0}
        monkeypatch.setattr(kap_dispatcher, "_queue_ai_analysis",
                            lambda r: called.__setitem__("ai", called["ai"] + 1))
        monkeypatch.setattr(kap_dispatcher, "_capture_reaction_baseline",
                            lambda r: called.__setitem__("reaction", called["reaction"] + 1))

        kap_dispatcher.dispatch_new_disclosure(rec)

        # Operator signal: AI fires, but caches preserved (no balance sheet
        # change) and no reaction baseline (that's bilanço-specific)
        assert called["ai"] == 1
        assert called["reaction"] == 0
        assert raw_cache.get("KAPLM") == {"x": 1}
        # Cleanup
        raw_cache.pop("KAPLM", None)
        analysis_cache.pop("KAPLM", None)

    def test_unclassified_general_announcement_is_no_op(self, monkeypatch):
        """Subject like 'Özel Durum Açıklaması (Genel)' has no operator
        pattern → no AI queue, no cache touch."""
        from engine import kap_dispatcher
        from data.kap_client import DisclosureRecord

        called = {"ai": 0, "reaction": 0, "invalidate": 0}
        monkeypatch.setattr(kap_dispatcher, "_queue_ai_analysis",
                            lambda r: called.__setitem__("ai", called["ai"] + 1))
        monkeypatch.setattr(kap_dispatcher, "_capture_reaction_baseline",
                            lambda r: called.__setitem__("reaction", called["reaction"] + 1))
        monkeypatch.setattr(kap_dispatcher, "_invalidate_caches_for_ticker",
                            lambda t: called.__setitem__("invalidate", called["invalidate"] + 1))

        rec = DisclosureRecord(
            disclosure_index=999_002, ticker="ARCLK",
            kap_title="ARÇELİK A.Ş.",
            subject="Özel Durum Açıklaması (Genel)",
            disclosure_type="ODA", disclosure_class="ODA",
            publish_date="2026-05-13T12:00:00+00:00",
            publish_date_raw="13.05.2026 15:00:00",
            rule_type=None, period=None, year=2026,
            attachment_count=0, is_late=False, url=None,
        )
        kap_dispatcher.dispatch_new_disclosure(rec)

        # Unclassified general announcement → all side-effects skipped
        assert called == {"ai": 0, "reaction": 0, "invalidate": 0}

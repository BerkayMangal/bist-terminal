# ================================================================
# tests/test_diag_fundamentals.py
#
# Veri Tazeliği diagnostic — per-ticker fundamental freshness bundle.
# ================================================================

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine.diag_fundamentals import (
    compute_data_freshness,
    compute_summary,
    _age_status,
)


def _iso_hours_ago(h: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(hours=h)).isoformat()


class _FakeDf:
    """Minimal stand-in for the pandas df borsapy returns."""
    def __init__(self, cols):
        self.columns = list(cols)


@pytest.fixture
def fresh_raw():
    return {
        "_fetched_at": _iso_hours_ago(2.0),
        "source": "borsapy",
        "is_bank": False,
        "_fetch_attempts": 1,
        "financials_q": _FakeDf(["2026Q1", "2025Q4", "2025Q3"]),
    }


# ── Age status bands ────────────────────────────────────────────


class TestAgeStatus:
    def test_unknown_when_none(self):
        assert _age_status(None) == "unknown"

    def test_fresh_under_26h(self):
        assert _age_status(0.1) == "fresh"
        assert _age_status(26.0) == "fresh"

    def test_old_between_26_and_72(self):
        assert _age_status(26.1) == "old"
        assert _age_status(72.0) == "old"

    def test_stale_over_72(self):
        assert _age_status(72.1) == "stale"
        assert _age_status(500.0) == "stale"


# ── compute_data_freshness ──────────────────────────────────────


class TestComputeFreshness:
    def test_empty_ticker(self, monkeypatch):
        out = compute_data_freshness("")
        assert out["age_status"] == "unknown"
        assert "empty ticker" in out["warnings"]

    def test_borsapy_cache_miss(self, monkeypatch):
        import engine.diag_fundamentals as df
        monkeypatch.setattr(df, "_lookup_raw_cache", lambda k: None)
        # Avoid touching KAP storage
        import infra.kap_storage as ks
        monkeypatch.setattr(ks, "get_by_ticker", lambda t, limit=50: [])
        out = compute_data_freshness("FORTE")
        assert out["age_status"] == "unknown"
        assert "borsapy cache miss" in out["warnings"]
        assert out["borsapy"]["fetched_at"] is None

    def test_fresh_borsapy_no_kap(self, monkeypatch, fresh_raw):
        import engine.diag_fundamentals as df
        monkeypatch.setattr(df, "_lookup_raw_cache",
                            lambda k: fresh_raw if "FORTE" in k.upper() else None)
        import infra.kap_storage as ks
        monkeypatch.setattr(ks, "get_by_ticker", lambda t, limit=50: [])
        out = compute_data_freshness("FORTE")
        assert out["age_status"] == "fresh"
        assert out["borsapy"]["age_hours"] is not None
        assert out["borsapy"]["age_hours"] < 3
        assert out["borsapy"]["latest_quarter"] == "2026Q1"
        assert out["borsapy"]["quarterly_available"] is True
        assert "no KAP financial report on record" in out["warnings"]

    def test_stale_borsapy(self, monkeypatch):
        import engine.diag_fundamentals as df
        stale = {
            "_fetched_at": _iso_hours_ago(200.0),   # >72h
            "source": "borsapy", "is_bank": False,
            "_fetch_attempts": 2,
            "financials_q": _FakeDf(["2025Q4"]),
        }
        monkeypatch.setattr(df, "_lookup_raw_cache",
                            lambda k: stale if "LOGO" in k.upper() else None)
        import infra.kap_storage as ks
        monkeypatch.setattr(ks, "get_by_ticker", lambda t, limit=50: [])
        out = compute_data_freshness("LOGO")
        assert out["age_status"] == "stale"
        assert out["borsapy"]["age_hours"] > 100

    def test_gap_flags_borsapy_behind_kap(self, monkeypatch, fresh_raw):
        # Borsapy fetched 50h ago; KAP filed 2h ago → gap = +48h-ish
        stale_raw = dict(fresh_raw)
        stale_raw["_fetched_at"] = _iso_hours_ago(50.0)
        import engine.diag_fundamentals as df
        monkeypatch.setattr(df, "_lookup_raw_cache",
                            lambda k: stale_raw if "FORTE" in k.upper() else None)
        import infra.kap_storage as ks
        recent_kap = {
            "disclosure_type": "FR",
            "subject": "Konsolide Finansal Tablolar",
            "publish_date": _iso_hours_ago(2.0),
            "rule_type": "3 Aylık", "period": 1, "year": 2026,
            "disclosure_index": 99999,
        }
        monkeypatch.setattr(ks, "get_by_ticker",
                            lambda t, limit=50: [recent_kap])
        out = compute_data_freshness("FORTE")
        assert out["kap"]["age_days"] is not None
        # Gap is roughly +2 days (KAP 2h ago, borsapy 50h ago)
        assert out["gap_days"] is not None and out["gap_days"] > 1.5
        # Warning fired
        assert any("borsapy may not yet have ingested" in w
                   for w in out["warnings"])

    def test_quarterly_missing_warning_for_nonbank(self, monkeypatch):
        import engine.diag_fundamentals as df
        raw = {
            "_fetched_at": _iso_hours_ago(1.0),
            "source": "borsapy", "is_bank": False,
            "_fetch_attempts": 1,
            "financials_q": None,  # quarterly NOT available
        }
        monkeypatch.setattr(df, "_lookup_raw_cache",
                            lambda k: raw if "FORTE" in k.upper() else None)
        import infra.kap_storage as ks
        monkeypatch.setattr(ks, "get_by_ticker", lambda t, limit=50: [])
        out = compute_data_freshness("FORTE")
        assert any("quarterly data missing" in w for w in out["warnings"])

    def test_bank_quarterly_missing_not_warned(self, monkeypatch):
        # Banks legitimately lack quarterly cashflow → don't nag
        import engine.diag_fundamentals as df
        raw = {
            "_fetched_at": _iso_hours_ago(1.0),
            "source": "borsapy", "is_bank": True,
            "_fetch_attempts": 1,
            "financials_q": None,
        }
        monkeypatch.setattr(df, "_lookup_raw_cache",
                            lambda k: raw if "GARAN" in k.upper() else None)
        import infra.kap_storage as ks
        monkeypatch.setattr(ks, "get_by_ticker", lambda t, limit=50: [])
        out = compute_data_freshness("GARAN")
        assert not any("quarterly data missing" in w for w in out["warnings"])

    def test_only_financial_subject_picked(self, monkeypatch, fresh_raw):
        # Non-financial subjects in KAP history shouldn't be flagged as
        # the "latest financial report".
        import engine.diag_fundamentals as df
        monkeypatch.setattr(df, "_lookup_raw_cache",
                            lambda k: fresh_raw if "FORTE" in k.upper() else None)
        import infra.kap_storage as ks
        rows = [
            {"disclosure_type": "ODA",
             "subject": "Pay Alım Satım Bildirimi",
             "publish_date": _iso_hours_ago(1.0)},
            {"disclosure_type": "FR",
             "subject": "Sorumluluk Beyanı",       # not a balance sheet
             "publish_date": _iso_hours_ago(5.0)},
            {"disclosure_type": "FR",
             "subject": "Konsolide Finansal Tablolar",
             "publish_date": _iso_hours_ago(48.0),
             "rule_type": "Yıllık", "period": 4, "year": 2025,
             "disclosure_index": 1234},
        ]
        monkeypatch.setattr(ks, "get_by_ticker", lambda t, limit=50: rows)
        out = compute_data_freshness("FORTE")
        assert out["kap"]["disclosure_index"] == 1234
        assert out["kap"]["rule_type"] == "Yıllık"


# ── compute_summary batch ───────────────────────────────────────


class TestComputeSummary:
    def test_empty_universe(self):
        out = compute_summary([])
        assert out["items"] == []
        assert out["summary"]["total"] == 0

    def test_counts_buckets(self, monkeypatch):
        import engine.diag_fundamentals as df

        def _raw_for(k):
            t = (k or "").upper().replace(".IS", "")
            if t == "FRESH":
                return {"_fetched_at": _iso_hours_ago(1.0),
                        "source": "borsapy", "is_bank": False,
                        "financials_q": _FakeDf(["2026Q1"])}
            if t == "OLD":
                return {"_fetched_at": _iso_hours_ago(40.0),
                        "source": "borsapy", "is_bank": False,
                        "financials_q": _FakeDf(["2025Q4"])}
            if t == "STALE":
                return {"_fetched_at": _iso_hours_ago(200.0),
                        "source": "borsapy", "is_bank": False,
                        "financials_q": _FakeDf(["2025Q4"])}
            return None  # MISSING → unknown

        monkeypatch.setattr(df, "_lookup_raw_cache", _raw_for)
        import infra.kap_storage as ks
        monkeypatch.setattr(ks, "get_by_ticker", lambda t, limit=50: [])
        out = compute_summary(["FRESH", "OLD", "STALE", "MISSING"])
        assert out["summary"]["fresh"] == 1
        assert out["summary"]["old"] == 1
        assert out["summary"]["stale"] == 1
        assert out["summary"]["unknown"] == 1
        assert out["summary"]["total"] == 4
        # Row shape sanity
        row = out["items"][0]
        for k in ("ticker", "age_hours", "age_status",
                  "latest_quarter", "quarterly_available",
                  "kap_age_days", "kap_rule_type", "gap_days", "warnings"):
            assert k in row

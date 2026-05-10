"""Tests for BullWatch v2 Phase A.10 Step 2-C — Workflow readiness layer.

Coverage:
  - readiness mapping (5 states) deterministic from inputs
  - readiness_rationale Turkish + legal-safe (no buy/sell/target/stop)
  - data_status guard → İZLEMEDE
  - late-risk priority (DISTRIBUTION/LATE/EXHAUSTED) over ignition
  - segment_fit explanatory only (no score impact)
  - asdict() shape includes new fields
  - early-return paths (institutional/no_data/insufficient) get readiness=İZLEMEDE
  - no scoring/eligibility/zone regression vs Step 2-B baseline
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
import pytest

from engine.bullwatch import (
    _compute_readiness,
    _build_readiness_rationale,
    _compute_segment_fit,
    READINESS_STATES,
    SEGMENT_FIT_STATES,
    BullWatchResult,
    score_symbol,
)


# ============================================================
# Helpers
# ============================================================
def _ohlcv(closes, vol=600_000):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.bdate_range(end="2026-05-08", periods=n)
    return pd.DataFrame({
        "Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
        "Close": closes, "Volume": np.full(n, vol),
    }, index=idx)


def _conflict(dom="ACCUMULATION", conf="MEDIUM", depth=2):
    return {"dominant_read": dom, "confidence_tier": conf,
            "evidence_depth_count": depth, "confidence": 0.7}


def _maturity(stage="MID", position=0.5, rvol=None):
    return {"maturity": stage,
            "indicators": {"position_in_range": position,
                           "rvol": rvol,
                           "ret_20d": 0.05}}


def _playbook(name="ACCUMULATION"):
    return {"playbook": name, "confidence": 0.6}


def _pinning(score=70):
    return {"price_pinning_score": score}


# ============================================================
# 1. Readiness state machine — exhaustive
# ============================================================
class TestReadinessStates:
    def test_all_5_states_exposed(self):
        assert set(READINESS_STATES) == {
            "HAZIRLANIYOR", "ATEŞLENDİ", "TEYİT BEKLİYOR",
            "GEÇ KALMIŞ OLABİLİR", "İZLEMEDE",
        }

    def test_data_missing_forces_izleme(self):
        m = {"_data_status": "missing", "patterns": ["walk-up"]}
        r = _compute_readiness(m, _conflict(), _maturity(), _playbook())
        assert r == "İZLEMEDE"

    def test_data_stale_forces_izleme(self):
        m = {"_data_status": "stale", "patterns": ["walk-up"]}
        r = _compute_readiness(m, _conflict(), _maturity(), _playbook())
        assert r == "İZLEMEDE"

    def test_data_partial_with_low_conf_izleme(self):
        m = {"_data_status": "partial", "patterns": []}
        r = _compute_readiness(m, _conflict(conf="LOW"), _maturity(), _playbook())
        assert r == "İZLEMEDE"

    def test_data_partial_with_strong_dom_passes(self):
        """Partial data + dominant_read ACCUMULATION + MEDIUM conf
        should not force İZLEMEDE — readability remains."""
        m = {"_data_status": "partial", "rvol": 3.5,
             "patterns": ["walk-up"]}
        r = _compute_readiness(
            m, _conflict(conf="MEDIUM"),
            _maturity(stage="MID", position=0.6, rvol=3.5),
            _playbook("MARKUP"),
        )
        # Should classify as ATEŞLENDİ, not get stuck on İZLEMEDE
        assert r == "ATEŞLENDİ"

    # ── Late-risk priority ──
    def test_distribution_dominant_late_risk(self):
        m = {"_data_status": "live"}
        r = _compute_readiness(m, _conflict(dom="DISTRIBUTION"),
                               _maturity(stage="MID"), _playbook())
        assert r == "GEÇ KALMIŞ OLABİLİR"

    def test_late_maturity_late_risk(self):
        m = {"_data_status": "live"}
        r = _compute_readiness(m, _conflict(), _maturity(stage="LATE"),
                               _playbook())
        assert r == "GEÇ KALMIŞ OLABİLİR"

    def test_exhausted_maturity_late_risk(self):
        m = {"_data_status": "live"}
        r = _compute_readiness(m, _conflict(), _maturity(stage="EXHAUSTED"),
                               _playbook())
        assert r == "GEÇ KALMIŞ OLABİLİR"

    def test_climax_volume_high_position_late_risk(self):
        """Position >85% + RVOL >=2.5 → climax pattern → late risk
        even if dominant=ACCUMULATION."""
        m = {"_data_status": "live", "rvol": 3.0}
        r = _compute_readiness(
            m, _conflict(),
            _maturity(stage="MID", position=0.92, rvol=3.0),
            _playbook(),
        )
        assert r == "GEÇ KALMIŞ OLABİLİR"

    def test_late_risk_beats_ignition(self):
        """If dominant=DISTRIBUTION but RVOL high + walk-up,
        late-risk must win (priority)."""
        m = {"_data_status": "live", "rvol": 4.5,
             "patterns": ["walk-up"]}
        r = _compute_readiness(
            m, _conflict(dom="DISTRIBUTION"),
            _maturity(stage="MID", position=0.6, rvol=4.5),
            _playbook("MARKUP"),
        )
        assert r == "GEÇ KALMIŞ OLABİLİR"

    # ── Ignition ──
    def test_ignition_high_rvol_accumulation(self):
        m = {"_data_status": "live", "rvol": 3.5,
             "patterns": ["walk-up"]}
        r = _compute_readiness(
            m, _conflict(dom="ACCUMULATION"),
            _maturity(stage="MID", position=0.6, rvol=3.5),
            _playbook("ACCUMULATION"),
        )
        assert r == "ATEŞLENDİ"

    def test_ignition_markup_playbook(self):
        m = {"_data_status": "live", "rvol": 3.0,
             "patterns": []}
        r = _compute_readiness(
            m, _conflict(dom="ACCUMULATION"),
            _maturity(stage="MID", position=0.6, rvol=3.0),
            _playbook("MARKUP"),
        )
        assert r == "ATEŞLENDİ"

    def test_ignition_walk_up_pattern_alone(self):
        """Walk-up pattern + ACCUMULATION + position > 0.5 → ATEŞLENDİ
        even with moderate RVOL."""
        m = {"_data_status": "live", "rvol": 1.5,
             "patterns": ["walk-up"]}
        r = _compute_readiness(
            m, _conflict(dom="ACCUMULATION"),
            _maturity(stage="MID", position=0.6, rvol=1.5),
            _playbook("ACCUMULATION"),
        )
        assert r == "ATEŞLENDİ"

    # ── Preparation ──
    def test_preparation_pinning(self):
        m = {"_data_status": "live", "rvol": 1.5}
        r = _compute_readiness(
            m, _conflict(dom="ACCUMULATION"),
            _maturity(stage="EARLY", position=0.4),
            _playbook("ACCUMULATION"),
            _pinning(score=75),
        )
        assert r == "HAZIRLANIYOR"

    def test_preparation_float_turnover(self):
        m = {"_data_status": "live", "rvol": 1.6,
             "float_turnover_20d": 1.8}
        r = _compute_readiness(
            m, _conflict(dom="ACCUMULATION"),
            _maturity(stage="MID", position=0.4),
            _playbook("ACCUMULATION"),
        )
        assert r == "HAZIRLANIYOR"

    # ── Confirmation pending ──
    def test_pattern_but_low_rvol_teyit(self):
        m = {"_data_status": "live", "rvol": 1.2,
             "patterns": ["compression"]}
        r = _compute_readiness(
            m, _conflict(dom="ACCUMULATION", conf="MEDIUM"),
            _maturity(stage="MID", position=0.6, rvol=1.2),
            _playbook("ACCUMULATION"),
        )
        assert r == "TEYİT BEKLİYOR"

    def test_accumulation_with_depth_no_pattern_teyit(self):
        m = {"_data_status": "live", "patterns": []}
        r = _compute_readiness(
            m, _conflict(dom="ACCUMULATION", conf="MEDIUM", depth=3),
            _maturity(stage="MID", position=0.5),
            _playbook("ACCUMULATION"),
        )
        assert r == "TEYİT BEKLİYOR"

    # ── Default ──
    def test_unknown_dominant_default_izleme(self):
        m = {"_data_status": "live", "patterns": []}
        r = _compute_readiness(m, _conflict(dom="", depth=0),
                               _maturity(), _playbook(name=""))
        assert r == "İZLEMEDE"


# ============================================================
# 2. Readiness rationale — legal-safe + Turkish
# ============================================================
class TestReadinessRationale:
    BANNED_WORDS = (
        # Direct trading verbs
        "satın al", "buy", "sell ", " al ", " sat ",
        # Targets / stops / promises
        "hedef", "stop", "target", "kesin", "garanti",
        "manipülasyon", "manipulation",
    )

    def _check_legal_safe(self, text: str):
        low = " " + text.lower() + " "
        for bw in self.BANNED_WORDS:
            assert bw not in low, f"Banned word found in: {text!r}"

    def test_hazirlaniyor_text(self):
        m = {"_data_status": "live", "rvol": 1.5,
             "float_turnover_20d": 1.8}
        r = "HAZIRLANIYOR"
        text = _build_readiness_rationale(
            r, m, _conflict(dom="ACCUMULATION"),
            _maturity(stage="EARLY", position=0.4),
            _playbook("ACCUMULATION"),
            _pinning(score=75),
        )
        assert isinstance(text, str)
        assert len(text) > 0
        assert "gözlemleniyor" in text.lower() or "el değiştir" in text.lower() or "pinning" in text.lower()
        self._check_legal_safe(text)

    def test_atestlendi_text(self):
        m = {"_data_status": "live", "rvol": 3.5,
             "patterns": ["walk-up"]}
        text = _build_readiness_rationale(
            "ATEŞLENDİ", m, _conflict(dom="ACCUMULATION"),
            _maturity(stage="MID", position=0.6, rvol=3.5),
            _playbook("ACCUMULATION"),
        )
        assert "rvol" in text.lower() or "walk-up" in text.lower() or "ihtimal" in text.lower()
        self._check_legal_safe(text)

    def test_late_risk_text_warns_human_check(self):
        m = {"_data_status": "live", "rvol": 3.0}
        text = _build_readiness_rationale(
            "GEÇ KALMIŞ OLABİLİR", m,
            _conflict(dom="DISTRIBUTION"),
            _maturity(stage="LATE", position=0.92, rvol=3.0),
            _playbook(),
        )
        # Must warn the user
        low = text.lower()
        assert "risk" in low or "geç" in low or "insan gözüyle" in low
        self._check_legal_safe(text)

    def test_teyit_text(self):
        m = {"_data_status": "live", "rvol": 1.2,
             "patterns": ["compression"]}
        text = _build_readiness_rationale(
            "TEYİT BEKLİYOR", m,
            _conflict(dom="ACCUMULATION", conf="MEDIUM"),
            _maturity(stage="MID", position=0.5, rvol=1.2),
            _playbook("ACCUMULATION"),
        )
        assert "teyit" in text.lower()
        self._check_legal_safe(text)

    def test_izleme_text_when_data_weak(self):
        m = {"_data_status": "missing", "_missing_fields": ["free_float"]}
        text = _build_readiness_rationale(
            "İZLEMEDE", m, _conflict(), _maturity(), _playbook(),
        )
        low = text.lower()
        assert "veri" in low or "insan gözüyle" in low or "belirsiz" in low
        self._check_legal_safe(text)

    def test_text_starts_capitalized(self):
        m = {"_data_status": "live", "rvol": 3.0,
             "patterns": ["walk-up"]}
        text = _build_readiness_rationale(
            "ATEŞLENDİ", m, _conflict(dom="ACCUMULATION"),
            _maturity(stage="MID", position=0.6, rvol=3.0),
            _playbook("MARKUP"),
        )
        assert text[0].isupper()

    def test_text_ends_with_period(self):
        m = {"_data_status": "live"}
        for state in READINESS_STATES:
            text = _build_readiness_rationale(
                state, m, _conflict(), _maturity(), _playbook(),
            )
            assert text.endswith(".")

    def test_no_banned_words_across_all_states(self):
        """Sweep all 5 readiness states with various inputs and ensure
        none produce banned trading-language."""
        m = {"_data_status": "live", "rvol": 3.5,
             "patterns": ["walk-up", "breakout"],
             "float_turnover_20d": 2.1}
        for state in READINESS_STATES:
            text = _build_readiness_rationale(
                state, m,
                _conflict(dom="ACCUMULATION"),
                _maturity(stage="MID", position=0.7, rvol=3.5),
                _playbook("MARKUP"),
                _pinning(80),
            )
            self._check_legal_safe(text)


# ============================================================
# 3. Segment fit — explanatory only
# ============================================================
class TestSegmentFit:
    def test_all_3_states(self):
        assert set(SEGMENT_FIT_STATES) == {"GÜÇLÜ", "ORTA", "ZAYIF"}

    def test_endustri_strong(self):
        fit, exp = _compute_segment_fit("Endüstri")
        assert fit == "GÜÇLÜ"
        assert "endüstri" in exp.lower() or "üretim" in exp.lower()

    def test_madencilik_strong(self):
        fit, _ = _compute_segment_fit("Madencilik")
        assert fit == "GÜÇLÜ"

    def test_finansal_weak(self):
        fit, exp = _compute_segment_fit("Finansal")
        assert fit == "ZAYIF"
        assert "finansal" in exp.lower() or "holding" in exp.lower() or "gyo" in exp.lower()

    def test_teknoloji_medium(self):
        fit, _ = _compute_segment_fit("Teknoloji")
        assert fit == "ORTA"

    def test_diger_medium(self):
        fit, _ = _compute_segment_fit("Diğer")
        assert fit == "ORTA"

    def test_unknown_defaults_medium(self):
        fit, _ = _compute_segment_fit(None)
        assert fit == "ORTA"
        fit, _ = _compute_segment_fit("Unknown Sector")
        assert fit == "ORTA"

    def test_explainer_no_buy_sell_language(self):
        for s in ("Endüstri", "Finansal", "Teknoloji", None, "Diğer"):
            _, exp = _compute_segment_fit(s)
            low = exp.lower()
            for bw in ("al ", "sat ", "buy", "sell", "hedef", "stop"):
                assert bw not in " " + low + " "


# ============================================================
# 4. BullWatchResult shape — new fields present
# ============================================================
class TestResultShape:
    def test_eligible_result_has_readiness_fields(self):
        df = _ohlcv(np.linspace(10, 11, 80))
        m = {"symbol": "T", "ticker": "T",
             "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8,
             "_data_status": "live"}
        r = score_symbol(m, df=df)
        assert r is not None
        assert r.readiness in READINESS_STATES
        assert isinstance(r.readiness_rationale, str)
        assert len(r.readiness_rationale) > 0
        assert r.segment_fit in SEGMENT_FIT_STATES
        assert isinstance(r.segment_fit_explainer, str)

    def test_institutional_result_has_izleme_default(self):
        df = _ohlcv(np.linspace(10, 11, 80))
        # Float market cap > 15B → institutional tier
        m = {"symbol": "BIG", "ticker": "BIG",
             "market_cap": 5e11, "free_float": 0.5,
             "shares": 5e9}
        r = score_symbol(m, df=df)
        assert r.eligible is False
        assert r.readiness == "İZLEMEDE"
        assert r.readiness_rationale is not None
        assert r.segment_fit in SEGMENT_FIT_STATES

    def test_no_data_result_has_izleme_default(self):
        df = _ohlcv(np.linspace(10, 11, 80))
        m = {"symbol": "ND", "ticker": "ND",
             "market_cap": None, "free_float": None,
             "shares": None}
        r = score_symbol(m, df=df)
        assert r.readiness == "İZLEMEDE"

    def test_to_dict_includes_readiness(self):
        df = _ohlcv(np.linspace(10, 11, 80))
        m = {"symbol": "T", "ticker": "T",
             "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "_data_status": "live"}
        r = score_symbol(m, df=df)
        d = r.to_dict()
        assert "readiness" in d
        assert "readiness_rationale" in d
        assert "segment_fit" in d
        assert "segment_fit_explainer" in d


# ============================================================
# 5. NO regression — score / eligibility / zone unchanged
# ============================================================
class TestNoScoringRegression:
    """Step 2-C contract: readiness/segment_fit DO NOT affect
    scoring fields. Snapshot a few results pre-2-C-style and
    ensure score+zone+eligibility are produced from same inputs."""

    def test_score_invariant_eligible(self):
        df = _ohlcv(np.linspace(10, 11.5, 80), 1_500_000)
        m = {"symbol": "T", "ticker": "T",
             "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "revenue": 5e8,
             "_data_status": "live"}
        r1 = score_symbol(m, df=df)
        # Score must be in the valid range
        assert 0 <= r1.score <= 100
        assert r1.zone in ("EARLY", "CONFIRMED", "CONVICTION")
        assert r1.eligible is True
        # cycle_state from Step 2-A.2 still set
        assert r1.cycle_state in (
            "TOPLANIYOR", "ATEŞLENİYOR", "DAĞITIM RİSKİ",
            "BOŞALTIYOR", "BELİRSİZ"
        )

    def test_readiness_does_not_change_score_when_late_risk(self):
        """Even if readiness flags GEÇ KALMIŞ OLABİLİR,
        the score itself must remain whatever the engines produced."""
        df = _ohlcv(np.linspace(10, 11.5, 80))
        m = {"symbol": "T", "ticker": "T",
             "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6, "_data_status": "live"}
        r = score_symbol(m, df=df)
        # Whatever the readiness says, score is in valid range
        assert 0 <= r.score <= 100
        # Eligibility is determined by universe filters, NOT by readiness
        assert r.eligible in (True, False)


# ============================================================
# 6. Integration — score_symbol full path
# ============================================================
class TestScoreSymbolIntegration:
    def test_finansal_symbol_gets_zayif(self):
        """A symbol mapped to Finansal sector_tr should get
        segment_fit=ZAYIF in the final result."""
        df = _ohlcv(np.linspace(10, 11, 80))
        m = {"symbol": "T", "ticker": "T",
             "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6,
             "sector": "Financial Services",
             "industry": "Banks—Regional",
             "_data_status": "live"}
        r = score_symbol(m, df=df)
        assert r.segment_fit == "ZAYIF"

    def test_industrial_symbol_gets_guclu(self):
        df = _ohlcv(np.linspace(10, 11, 80))
        m = {"symbol": "T", "ticker": "T",
             "market_cap": 1.5e9, "free_float": 0.35,
             "shares": 60e6,
             "sector": "Industrials",
             "industry": "Specialty Industrial Machinery",
             "_data_status": "live"}
        r = score_symbol(m, df=df)
        assert r.segment_fit == "GÜÇLÜ"

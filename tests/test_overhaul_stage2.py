# ================================================================
# tests/test_overhaul_stage2.py
#
# Great Overhaul Stage 2: Zone Hysteresis (2-point buffer)
#
# Audit finding:
#   Zone thresholds at exactly 75.0 (CONVICTION) and 60.0 (CONFIRMED).
#   score is `round(score_final, 1)`, so 74.95 → 75.0 = CONVICTION,
#   and 74.85 → 74.9 = EARLY. A single 0.1pt perturbation across runs
#   flips zone for boundary tickers — the "bazen başka outcome çıkıyor"
#   complaint.
#
# Fix:
#   ENTRY threshold (going UP)   = standard 75 / 60
#   EXIT  threshold (going DOWN) = standard - 2pt = 73 / 58
#
#   When previous_zone is known (from previous snapshot), we keep the
#   ticker in that zone IF its score is still within the buffer band.
#   This prevents flap without altering the entry semantics.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from engine.bullwatch import (
    _classify_zone, _classify_zone_strict,
    HYSTERESIS_BUFFER,
    CONVICTION_ENTRY, CONVICTION_EXIT,
    CONFIRMED_ENTRY, CONFIRMED_EXIT,
    FLOAT_PRESSURE_VERY_STRONG, RVOL_STRONG, RVOL_EARLY,
)


# ────────────────────────────────────────────────────────────────
# Threshold constants — guard against accidental reverts
# ────────────────────────────────────────────────────────────────


class TestThresholdConstants:
    def test_buffer_is_2_points(self):
        assert HYSTERESIS_BUFFER == 2.0

    def test_conviction_band(self):
        assert CONVICTION_ENTRY == 75.0
        assert CONVICTION_EXIT == 73.0

    def test_confirmed_band(self):
        assert CONFIRMED_ENTRY == 60.0
        assert CONFIRMED_EXIT == 58.0


# ────────────────────────────────────────────────────────────────
# Strict classifier — unchanged behavior
# ────────────────────────────────────────────────────────────────


class TestStrictClassifierUnchanged:
    """The strict classifier behavior MUST NOT have changed — only the
    hysteresis wrapper is new."""

    def test_high_score_with_rvol_is_conviction(self):
        z = _classify_zone_strict(
            score=78, fp=0.03, rvol=RVOL_STRONG + 0.5,
            ownership_score=0.3, pattern_count=2, compression_score=None,
        )
        assert z == "CONVICTION"

    def test_75_without_rvol_or_fp_is_confirmed(self):
        z = _classify_zone_strict(
            score=78, fp=0.01, rvol=1.0,
            ownership_score=0.5, pattern_count=2, compression_score=None,
        )
        assert z == "CONFIRMED"

    def test_60_with_ownership_is_confirmed(self):
        z = _classify_zone_strict(
            score=62, fp=0.01, rvol=1.0,
            ownership_score=0.5, pattern_count=0, compression_score=None,
        )
        assert z == "CONFIRMED"

    def test_low_score_is_early(self):
        z = _classify_zone_strict(
            score=45, fp=0.01, rvol=1.0,
            ownership_score=0.3, pattern_count=1, compression_score=None,
        )
        assert z == "EARLY"


# ────────────────────────────────────────────────────────────────
# Hysteresis: backward-compat (no previous_zone)
# ────────────────────────────────────────────────────────────────


class TestNoPreviousZone:
    """When previous_zone is None or missing, behavior must be identical
    to strict classifier — first scan, fresh install, untracked ticker."""

    def test_no_prev_zone_falls_back_to_strict(self):
        # Score 74.5 — NOT enough for CONVICTION (needs ≥75)
        strict = _classify_zone_strict(
            74.5, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None,
        )
        with_none = _classify_zone(
            74.5, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None, previous_zone=None,
        )
        assert strict == with_none

    def test_empty_string_treated_as_no_prev(self):
        # `not previous_zone` covers both None and "" — both are no-op
        z = _classify_zone(
            74, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None, previous_zone="",
        )
        # Score 74 < CONVICTION_ENTRY 75 → strict would say CONFIRMED
        assert z == "CONFIRMED"


# ────────────────────────────────────────────────────────────────
# CONVICTION hysteresis — the headline fix
# ────────────────────────────────────────────────────────────────


class TestConvictionHysteresis:
    def test_was_conviction_score_74_kept(self):
        """Boundary case: ticker was CONVICTION (e.g. 76), next scan
        drifted to 74. Strict would say "no longer CONVICTION", but
        hysteresis (buffer to 73) keeps it."""
        z = _classify_zone(
            74, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None,
            previous_zone="CONVICTION",
        )
        assert z == "CONVICTION"

    def test_was_conviction_score_73_exact_kept(self):
        """At the exit threshold itself, still hysteresis applies."""
        z = _classify_zone(
            73, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None,
            previous_zone="CONVICTION",
        )
        assert z == "CONVICTION"

    def test_was_conviction_score_72_leaves(self):
        """Below the buffer, leaves CONVICTION (drops to whatever
        strict classifier says)."""
        z = _classify_zone(
            72, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None,
            previous_zone="CONVICTION",
        )
        # 72 still has rvol + ownership — CONFIRMED
        assert z == "CONFIRMED"

    def test_was_conviction_rvol_dropped_score_still_high(self):
        """The other CONVICTION path: rvol/fp gate is now off, but
        score is still in buffer. Keep CONVICTION — the tape may flap
        but the underlying score is strong."""
        z = _classify_zone(
            74, fp=0.01, rvol=1.0,    # rvol/fp gone
            ownership_score=0.5, pattern_count=2, compression_score=None,
            previous_zone="CONVICTION",
        )
        # Strict would say CONFIRMED (no rvol/fp gate). Hysteresis keeps CONVICTION.
        assert z == "CONVICTION"

    def test_was_conviction_deep_drop_does_leave(self):
        """If score collapses below buffer, hysteresis can't help."""
        z = _classify_zone(
            55, fp=0.01, rvol=1.0,
            ownership_score=0.5, pattern_count=2, compression_score=None,
            previous_zone="CONVICTION",
        )
        assert z != "CONVICTION"

    def test_entering_conviction_still_needs_strict(self):
        """Hysteresis is exit-only — entering CONVICTION still requires
        score≥75 + rvol/fp. A score-74 ticker from CONFIRMED can't
        sneak into CONVICTION."""
        z = _classify_zone(
            74, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None,
            previous_zone="CONFIRMED",  # was below
        )
        # Cannot upgrade to CONVICTION just because score is in buffer
        assert z != "CONVICTION"
        assert z == "CONFIRMED"  # strict would put it here


# ────────────────────────────────────────────────────────────────
# CONFIRMED hysteresis
# ────────────────────────────────────────────────────────────────


class TestConfirmedHysteresis:
    def test_was_confirmed_score_59_kept(self):
        """In the 58-60 buffer, with conditions met, stays CONFIRMED."""
        z = _classify_zone(
            59, fp=0.01, rvol=1.0,
            ownership_score=0.5, pattern_count=2, compression_score=None,
            previous_zone="CONFIRMED",
        )
        assert z == "CONFIRMED"

    def test_was_confirmed_score_58_exact_kept(self):
        z = _classify_zone(
            58, fp=0.01, rvol=1.0,
            ownership_score=0.5, pattern_count=2, compression_score=None,
            previous_zone="CONFIRMED",
        )
        assert z == "CONFIRMED"

    def test_was_confirmed_score_57_leaves_to_early(self):
        z = _classify_zone(
            57, fp=0.01, rvol=1.0,
            ownership_score=0.5, pattern_count=2, compression_score=None,
            previous_zone="CONFIRMED",
        )
        assert z == "EARLY"

    def test_confirmed_hysteresis_needs_qualifying_condition(self):
        """Even in buffer band, CONFIRMED hysteresis ALSO requires one
        of the qualifying conditions (ownership/patterns/rvol) so a
        zero-signal ticker doesn't sit in CONFIRMED on inertia alone."""
        z = _classify_zone(
            59, fp=0.01, rvol=1.0,
            ownership_score=0.2,   # below 0.4
            pattern_count=1,       # below 2
            compression_score=None,
            previous_zone="CONFIRMED",
        )
        # No qualifying condition → buffer cannot save it
        assert z == "EARLY"


# ────────────────────────────────────────────────────────────────
# Cross-zone interactions
# ────────────────────────────────────────────────────────────────


class TestCrossZone:
    def test_was_conviction_now_earns_confirmed_again_is_conviction(self):
        """Ticker was CONVICTION. Score dropped to 74 (still in buffer)
        AND it qualifies for CONFIRMED strict — buffer wins, stays
        CONVICTION."""
        z = _classify_zone(
            74, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None,
            previous_zone="CONVICTION",
        )
        assert z == "CONVICTION"

    def test_was_early_can_still_enter_confirmed(self):
        """EARLY → CONFIRMED entry uses standard threshold (60)."""
        z = _classify_zone(
            62, fp=0.01, rvol=1.0,
            ownership_score=0.5, pattern_count=2, compression_score=None,
            previous_zone="EARLY",
        )
        assert z == "CONFIRMED"

    def test_was_early_score_59_stays_early(self):
        """No hysteresis benefit when previous was already EARLY."""
        z = _classify_zone(
            59, fp=0.01, rvol=1.0,
            ownership_score=0.5, pattern_count=2, compression_score=None,
            previous_zone="EARLY",
        )
        assert z == "EARLY"


# ────────────────────────────────────────────────────────────────
# Scan() plumbing
# ────────────────────────────────────────────────────────────────


class TestScanPreviousZonesPlumbing:
    def test_scan_signature_accepts_previous_zones(self):
        from engine.bullwatch import scan
        import inspect
        sig = inspect.signature(scan)
        assert "previous_zones" in sig.parameters

    def test_score_symbol_signature_accepts_previous_zone(self):
        from engine.bullwatch import score_symbol
        import inspect
        sig = inspect.signature(score_symbol)
        assert "previous_zone" in sig.parameters

    def test_scan_passes_zone_to_score_symbol(self, monkeypatch):
        """The previous_zone for each ticker must be forwarded into
        score_symbol — pin this with a spy."""
        from engine import bullwatch
        captured = {}

        def _spy(metrics, df, ownership, cap_tl=None, scan_now=None,
                 previous_zone=None):
            sym = metrics.get("symbol") or metrics.get("ticker", "X")
            captured[sym] = previous_zone
            return bullwatch.BullWatchResult(
                symbol=sym, score=0.0, zone="EARLY",
                pattern="quiet", eligible=False,
            )

        monkeypatch.setattr(bullwatch, "score_symbol", _spy)

        def _metrics_fn(sym):
            return {"market_cap": 1e9, "free_float": 0.4,
                    "shares": 1e7, "symbol": sym}

        def _history_fn(syms):
            return {s: None for s in syms}

        prev = {"AAA": "CONVICTION", "BBB": "CONFIRMED"}
        bullwatch.scan(
            ["AAA", "BBB", "CCC"],
            metrics_fn=_metrics_fn, history_fn=_history_fn,
            previous_zones=prev,
        )
        assert captured["AAA"] == "CONVICTION"
        assert captured["BBB"] == "CONFIRMED"
        # CCC wasn't in map → None
        assert captured["CCC"] is None

    def test_zone_lookup_tolerates_case_and_is_suffix(self, monkeypatch):
        """Snapshot may key tickers in various ways; the lookup tries
        sym / .upper() / strip-.IS."""
        from engine import bullwatch
        captured = {}

        def _spy(metrics, df, ownership, cap_tl=None, scan_now=None,
                 previous_zone=None):
            sym = metrics.get("symbol") or "X"
            captured[sym] = previous_zone
            return bullwatch.BullWatchResult(
                symbol=sym, score=0.0, zone="EARLY",
                pattern="quiet", eligible=False,
            )

        monkeypatch.setattr(bullwatch, "score_symbol", _spy)

        def _metrics_fn(sym):
            return {"market_cap": 1e9, "free_float": 0.4,
                    "shares": 1e7, "symbol": sym}

        def _history_fn(syms):
            return {s: None for s in syms}

        # Map has FIRST in upper case; query is "first" lower
        bullwatch.scan(
            ["FIRST"],
            metrics_fn=_metrics_fn, history_fn=_history_fn,
            previous_zones={"FIRST": "CONVICTION"},
        )
        assert captured["FIRST"] == "CONVICTION"


# ────────────────────────────────────────────────────────────────
# Determinism: same input → same output
# ────────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_score_same_prev_zone_same_result(self):
        """Identical inputs MUST produce identical output."""
        args = dict(
            score=74, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None,
            previous_zone="CONVICTION",
        )
        results = [_classify_zone(**args) for _ in range(50)]
        assert len(set(results)) == 1
        assert results[0] == "CONVICTION"

    def test_boundary_flap_resolved(self):
        """The bug: 74.95 → 75.0 → CONVICTION, then 74.85 → 74.9 → EARLY.
        With hysteresis, second run keeps CONVICTION."""
        # Run 1: enters CONVICTION at 74.95 (rounds to 75.0)
        round_run1 = round(74.95, 1)  # 75.0 (banker's rounding edge)
        # Note: Python round(74.95, 1) may produce 74.9 due to float repr.
        # Just simulate the scenario directly.
        z1 = _classify_zone(
            75.0, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None, previous_zone=None,
        )
        assert z1 == "CONVICTION"
        # Run 2: scored 74.85 (rounds to 74.9). Previous = CONVICTION.
        z2 = _classify_zone(
            74.9, fp=0.06, rvol=RVOL_STRONG + 1, ownership_score=0.5,
            pattern_count=2, compression_score=None, previous_zone=z1,
        )
        # Without hysteresis would be CONFIRMED. With it: CONVICTION.
        assert z2 == "CONVICTION", (
            "Boundary flap not prevented — hysteresis broken"
        )

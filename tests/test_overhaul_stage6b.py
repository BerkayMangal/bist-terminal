# ================================================================
# tests/test_overhaul_stage6b.py
#
# Great Overhaul Stage 6b: Full BIST universe via auto-discovery.
#
# User feedback: "bana soyle yapalim … senden bistteki her hisseyi
# cekmeni isterim arakdas". The previous FULL_BIST static dedup was
# 437 tickers; borsapy.Screener().run() returns ~591 — a true picture
# of the BIST equity board. Combined with Stage 6a (cache) and Stage 8
# (Railway Pro), expanding the universe is now feasible.
#
# Strategy: auto-discover at import time; if borsapy is offline, fall
# back to the static dedup so app boots cleanly. Union with static
# so any hand-curated ticker not in the screener's view still works.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Discovery contract
# ────────────────────────────────────────────────────────────────


class TestDiscoveryFunction:
    def test_function_exists(self):
        from config import _discover_full_bist_universe
        assert callable(_discover_full_bist_universe)

    def test_returns_list_on_success(self):
        """In the live test environment borsapy is importable AND
        produces a real screener. We assert basic shape only."""
        from config import _discover_full_bist_universe
        result = _discover_full_bist_universe()
        assert isinstance(result, list)
        if result:  # only sanity-check shape when we got something back
            assert all(isinstance(s, str) for s in result)
            assert all(s.isalnum() for s in result), (
                "Discovery returned non-alphanumeric tickers"
            )

    def test_returns_empty_on_borsapy_missing(self, monkeypatch):
        """If borsapy is somehow absent (CI without dep), discovery
        must return [] cleanly — never raise."""
        from config import _discover_full_bist_universe
        # Force the borsapy import inside the function to fail
        import builtins
        real_import = builtins.__import__
        def _no_borsapy(name, *a, **kw):
            if name == "borsapy":
                raise ImportError("simulated missing borsapy")
            return real_import(name, *a, **kw)
        monkeypatch.setattr(builtins, "__import__", _no_borsapy)
        result = _discover_full_bist_universe()
        assert result == []

    def test_returns_empty_on_screener_exception(self, monkeypatch):
        """Screener API breakage must not crash the import chain."""
        import config
        # Patch the function used inside _discover at call time
        class _BrokenScreener:
            def run(self):
                raise RuntimeError("screener API changed")
        class _FakeBp:
            Screener = _BrokenScreener
        import sys as _sys
        monkeypatch.setitem(_sys.modules, "borsapy", _FakeBp)
        result = config._discover_full_bist_universe()
        assert result == []


# ────────────────────────────────────────────────────────────────
# FULL_BIST end state
# ────────────────────────────────────────────────────────────────


class TestFullBistExpansion:
    def test_full_bist_is_a_list_of_strings(self):
        from config import FULL_BIST
        assert isinstance(FULL_BIST, list)
        assert all(isinstance(t, str) for t in FULL_BIST)

    def test_full_bist_larger_than_static(self):
        """Discovery + static union should exceed the old 437 ceiling
        when borsapy is reachable. In strictly offline test envs we
        still expect at least the static dedup size."""
        from config import FULL_BIST, _FULL_BIST_STATIC, _FULL_BIST_SOURCE
        assert len(FULL_BIST) >= len(_FULL_BIST_STATIC)
        if _FULL_BIST_SOURCE == "borsapy_screener+static":
            # Live discovery — should be appreciably bigger than 437
            assert len(FULL_BIST) >= 500, (
                f"Auto-discovery returned only {len(FULL_BIST)} symbols "
                "— borsapy.Screener may be returning fewer than expected"
            )

    def test_full_bist_dedupe(self):
        from config import FULL_BIST
        assert len(FULL_BIST) == len(set(FULL_BIST)), (
            "FULL_BIST has duplicate tickers — dict.fromkeys dedupe broken?"
        )

    def test_static_universe_subset_of_full_bist(self):
        """Whatever discovery returns, the static dedup must remain a
        subset. Hand-curated tickers can't be silently dropped just
        because borsapy doesn't list them."""
        from config import FULL_BIST, _FULL_BIST_STATIC
        missing = set(_FULL_BIST_STATIC) - set(FULL_BIST)
        assert not missing, (
            f"{len(missing)} static tickers vanished from FULL_BIST: "
            f"{sorted(missing)[:10]}"
        )

    def test_source_indicator_set(self):
        from config import _FULL_BIST_SOURCE
        assert _FULL_BIST_SOURCE in (
            "borsapy_screener+static", "static_only",
        )


# ────────────────────────────────────────────────────────────────
# Ticker shape — no .IS suffix, no junk
# ────────────────────────────────────────────────────────────────


class TestTickerShape:
    def test_no_suffix_anywhere(self):
        from config import FULL_BIST
        for t in FULL_BIST:
            assert ".IS" not in t, f"Ticker leaked .IS suffix: {t}"
            assert ".E" not in t, f"Ticker leaked .E suffix: {t}"

    def test_length_in_band(self):
        from config import FULL_BIST
        for t in FULL_BIST:
            assert 2 <= len(t) <= 6, (
                f"Ticker length out of band (2-6): {t!r}"
            )

    def test_uppercase_alphanumeric(self):
        from config import FULL_BIST
        for t in FULL_BIST:
            assert t == t.upper(), f"Non-uppercase ticker: {t!r}"
            assert t.isalnum(), f"Non-alphanumeric ticker: {t!r}"

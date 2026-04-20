"""Phase 4.4 cross-sectional ranking tests."""

from __future__ import annotations

import threading
from datetime import date, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def p44_db(tmp_path, monkeypatch):
    """Fresh DB + universe loaded."""
    db = tmp_path / "p44.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
    import infra.storage
    infra.storage._local = threading.local()
    infra.storage.DB_PATH = str(db)
    from infra.storage import init_db
    init_db()
    from infra.pit import load_universe_history_csv
    load_universe_history_csv()
    return db


def _seed_linear(symbol: str, start: date, n_days: int,
                 start_close: float, delta: float):
    """Seed n_days of weekday bars with linear close movement."""
    from infra.pit import save_price
    d = start
    i = 0
    while i < n_days:
        if d.weekday() < 5:
            px = start_close + i * delta
            save_price(symbol, d, "synthetic",
                       open_=px, high=px * 1.005, low=px * 0.995,
                       close=px, volume=1e6)
            i += 1
        d += timedelta(days=1)


def _seed_ratio_near_high(symbol: str, start: date, n_days: int,
                           max_price: float, final_ratio: float):
    """Rally up to max_price over first 2/3, then settle at ratio × max.

    Used to give each symbol a controlled close/252d_high ratio at
    the end date."""
    from infra.pit import save_price
    d = start
    i = 0
    rise_bars = int(n_days * 0.67)
    start_price = max_price * 0.5
    while i < n_days:
        if d.weekday() < 5:
            if i < rise_bars:
                px = start_price + (max_price - start_price) * (i / rise_bars)
            else:
                px = max_price * final_ratio
            save_price(symbol, d, "synthetic",
                       open_=px, high=px * 1.01, low=px * 0.99,
                       close=px, volume=1e6)
            i += 1
        d += timedelta(days=1)


# ========== Signal-strength primitives ==========

class TestSignalStrengthPrimitives:
    def test_52w_high_breakout_strength_ordering(self, p44_db):
        """Higher close-to-high ratio yields higher strength value."""
        from research.ranking import signal_strength
        _seed_ratio_near_high("HIGHSYM", date(2021, 1, 4), 400, 100.0, 0.99)
        _seed_ratio_near_high("MIDSYM",  date(2021, 1, 4), 400, 100.0, 0.70)
        _seed_ratio_near_high("LOWSYM",  date(2021, 1, 4), 400, 100.0, 0.50)

        end = date(2022, 7, 20)  # well into the level phase
        hi = signal_strength("HIGHSYM", "52W High Breakout", end, "synthetic")
        mi = signal_strength("MIDSYM", "52W High Breakout", end, "synthetic")
        lo = signal_strength("LOWSYM", "52W High Breakout", end, "synthetic")
        assert hi is not None and mi is not None and lo is not None
        assert hi > mi > lo
        assert hi <= 1.0 and lo >= 0.0

    def test_52w_insufficient_history_returns_none(self, p44_db):
        from research.ranking import signal_strength
        # Only 50 bars, need 252
        _seed_linear("SHORT", date(2023, 1, 3), 50, 100.0, 0.0)
        s = signal_strength("SHORT", "52W High Breakout",
                            date(2023, 1, 3) + timedelta(days=60), "synthetic")
        assert s is None

    def test_rsi_oversold_inverts_overbought(self, p44_db):
        """Synthetic steady-decline -> RSI low -> oversold strength high,
        overbought strength 0."""
        from research.ranking import signal_strength
        _seed_linear("DECLINE", date(2023, 1, 3), 40, 100.0, -0.5)
        end = date(2023, 1, 3) + timedelta(days=60)
        over = signal_strength("DECLINE", "RSI Asiri Alim", end, "synthetic")
        under = signal_strength("DECLINE", "RSI Asiri Satim", end, "synthetic")
        assert over == 0.0 or over is None
        if under is not None:
            assert under > 0.0

    def test_trend_cross_strength_zero_when_mas_equal(self, p44_db):
        """Flat series -> MA50 == MA200 -> Golden/Death strength = 0."""
        from research.ranking import signal_strength
        _seed_linear("FLAT", date(2022, 1, 3), 220, 100.0, 0.0)
        end = date(2022, 1, 3) + timedelta(days=320)
        s = signal_strength("FLAT", "Golden Cross", end, "synthetic")
        # MA50 and MA200 on a flat series are identical -> strength 0
        assert s == 0.0

    def test_macd_bullish_requires_positive_histogram(self, p44_db):
        """MACD Bullish on a DOWNTREND should give strength 0
        (histogram is negative in a downtrend)."""
        from research.ranking import signal_strength
        _seed_linear("DOWN", date(2023, 1, 3), 60, 100.0, -0.3)
        end = date(2023, 1, 3) + timedelta(days=85)
        s = signal_strength("DOWN", "MACD Bullish Cross", end, "synthetic")
        assert s == 0.0 or s is None

    def test_bollinger_upper_requires_breach(self, p44_db):
        """On a flat series, close is near MA20 -> not past the upper band."""
        from research.ranking import signal_strength
        _seed_linear("FLATBB", date(2023, 1, 3), 30, 100.0, 0.0)
        end = date(2023, 1, 3) + timedelta(days=45)
        s = signal_strength("FLATBB", "BB Ust Band Kirilim", end, "synthetic")
        assert s == 0.0 or s is None

    def test_unknown_signal_returns_none(self, p44_db):
        from research.ranking import signal_strength
        _seed_linear("ANY", date(2023, 1, 3), 30, 100.0, 0.1)
        s = signal_strength("ANY", "No Such Signal",
                            date(2023, 1, 3) + timedelta(days=45), "synthetic")
        assert s is None

    def test_all_registered_signals_callable(self):
        """Every entry in STRENGTH_FUNCTIONS must be callable with the
        standard (symbol, as_of, source=None) signature."""
        from research.ranking import STRENGTH_FUNCTIONS
        for name, fn in STRENGTH_FUNCTIONS.items():
            assert callable(fn), f"{name} not callable"


# ========== cs_rank_pct ==========

class TestCsRankPct:
    def _seed_five(self, p44_db):
        """5 BIST30 symbols with controlled 52W-high ratios."""
        syms_ratios = [
            ("THYAO", 0.99), ("AKBNK", 0.80), ("BIMAS", 0.60),
            ("ARCLK", 0.50), ("ASELS", 0.95),
        ]
        for sym, ratio in syms_ratios:
            _seed_ratio_near_high(sym, date(2021, 1, 4), 400, 100.0, ratio)
        return syms_ratios

    def test_top_symbol_gets_rank_1(self, p44_db):
        from research.ranking import cs_rank_pct
        self._seed_five(p44_db)
        r = cs_rank_pct("THYAO", "52W High Breakout",
                        date(2022, 7, 20), price_source="synthetic")
        assert r == 1.0

    def test_bottom_symbol_gets_rank_0(self, p44_db):
        from research.ranking import cs_rank_pct
        self._seed_five(p44_db)
        r = cs_rank_pct("ARCLK", "52W High Breakout",
                        date(2022, 7, 20), price_source="synthetic")
        assert r == 0.0

    def test_middle_symbol_gets_fractional_rank(self, p44_db):
        from research.ranking import cs_rank_pct
        self._seed_five(p44_db)
        r = cs_rank_pct("AKBNK", "52W High Breakout",
                        date(2022, 7, 20), price_source="synthetic")
        assert r is not None and 0 < r < 1

    def test_rank_order_matches_strength_order(self, p44_db):
        """ASELS=0.95 should outrank AKBNK=0.80 (from the seed ratios)."""
        from research.ranking import cs_rank_pct
        self._seed_five(p44_db)
        asels = cs_rank_pct("ASELS", "52W High Breakout",
                            date(2022, 7, 20), price_source="synthetic")
        akbnk = cs_rank_pct("AKBNK", "52W High Breakout",
                            date(2022, 7, 20), price_source="synthetic")
        assert asels > akbnk

    def test_symbol_not_in_universe_returns_none(self, p44_db):
        """A symbol that's not a BIST30 member on as_of -> None."""
        from research.ranking import cs_rank_pct
        # UNKNOWN_TICKER is never in SECTOR_MAP or universe_history
        r = cs_rank_pct("UNKNOWN_TICKER", "52W High Breakout",
                        date(2022, 7, 20), price_source="synthetic")
        assert r is None

    def test_insufficient_other_symbols_returns_none(self, p44_db):
        """If <3 symbols have data, cross-sectional rank is unreliable
        and returns None."""
        from research.ranking import cs_rank_pct
        # Seed only one symbol
        _seed_ratio_near_high("THYAO", date(2021, 1, 4), 400, 100.0, 0.90)
        r = cs_rank_pct("THYAO", "52W High Breakout",
                        date(2022, 7, 20), price_source="synthetic")
        assert r is None


# ========== Modulation factor ==========

class TestModulationFactor:
    def test_above_cutoff_returns_one(self):
        from research.ranking import modulation_factor
        assert modulation_factor(0.7) == 1.0
        assert modulation_factor(0.85) == 1.0
        assert modulation_factor(1.0) == 1.0

    def test_below_cutoff_returns_zero(self):
        from research.ranking import modulation_factor
        assert modulation_factor(0.3) == 0.0
        assert modulation_factor(0.15) == 0.0
        assert modulation_factor(0.0) == 0.0

    def test_middle_linear_ramp(self):
        from research.ranking import modulation_factor
        # rank=0.5 -> (0.5-0.3)/(0.7-0.3) = 0.5
        assert modulation_factor(0.5) == pytest.approx(0.5)
        # rank=0.4 -> (0.4-0.3)/(0.7-0.3) = 0.25
        assert modulation_factor(0.4) == pytest.approx(0.25)
        # rank=0.6 -> 0.75
        assert modulation_factor(0.6) == pytest.approx(0.75)

    def test_none_rank_returns_one(self):
        """Missing rank doesn't penalize -- defer to the calibrated weight."""
        from research.ranking import modulation_factor
        assert modulation_factor(None) == 1.0


# ========== apply_cs_rank_modulation ==========

class TestApplyRankModulation:
    def _seed_five(self, p44_db):
        syms_ratios = [
            ("THYAO", 0.99), ("AKBNK", 0.80), ("BIMAS", 0.60),
            ("ARCLK", 0.50), ("ASELS", 0.95),
        ]
        for sym, ratio in syms_ratios:
            _seed_ratio_near_high(sym, date(2021, 1, 4), 400, 100.0, ratio)

    def test_top_event_keeps_full_weight(self, p44_db):
        self._seed_five(p44_db)
        from research.ranking import apply_cs_rank_modulation
        out = apply_cs_rank_modulation(
            [{"symbol": "THYAO", "signal": "52W High Breakout",
              "as_of": "2022-07-20", "calibrated_weight": 1.5}],
            price_source="synthetic",
        )
        assert out[0]["modulated_weight"] == 1.5
        assert out[0]["cs_rank_pct"] == 1.0

    def test_bottom_event_zeroed(self, p44_db):
        self._seed_five(p44_db)
        from research.ranking import apply_cs_rank_modulation
        out = apply_cs_rank_modulation(
            [{"symbol": "ARCLK", "signal": "52W High Breakout",
              "as_of": "2022-07-20", "calibrated_weight": 1.5}],
            price_source="synthetic",
        )
        assert out[0]["modulated_weight"] == 0.0

    def test_preserves_other_event_fields(self, p44_db):
        self._seed_five(p44_db)
        from research.ranking import apply_cs_rank_modulation
        out = apply_cs_rank_modulation(
            [{"symbol": "THYAO", "signal": "52W High Breakout",
              "as_of": "2022-07-20", "calibrated_weight": 1.5,
              "extra_field": "preserved"}],
            price_source="synthetic",
        )
        assert out[0]["extra_field"] == "preserved"
        # New fields added
        assert "cs_rank_pct" in out[0]
        assert "modulation_factor" in out[0]

    def test_none_calibrated_weight_yields_none_modulated(self, p44_db):
        """If calibrated_weight is None, modulated_weight is also None."""
        self._seed_five(p44_db)
        from research.ranking import apply_cs_rank_modulation
        out = apply_cs_rank_modulation(
            [{"symbol": "THYAO", "signal": "52W High Breakout",
              "as_of": "2022-07-20", "calibrated_weight": None}],
            price_source="synthetic",
        )
        assert out[0]["modulated_weight"] is None

    def test_caches_per_date_sym_signal(self, p44_db):
        """Two events with same (symbol, signal, date) should hit cache."""
        self._seed_five(p44_db)
        from research.ranking import apply_cs_rank_modulation
        events = [
            {"symbol": "THYAO", "signal": "52W High Breakout",
             "as_of": "2022-07-20", "calibrated_weight": 1.0},
            {"symbol": "THYAO", "signal": "52W High Breakout",
             "as_of": "2022-07-20", "calibrated_weight": 1.0},
        ]
        out = apply_cs_rank_modulation(events, price_source="synthetic")
        # Both rows have the same rank (confirms cache correctness)
        assert out[0]["cs_rank_pct"] == out[1]["cs_rank_pct"]

    def test_missing_fields_not_crash(self, p44_db):
        from research.ranking import apply_cs_rank_modulation
        out = apply_cs_rank_modulation(
            [{"calibrated_weight": 0.5}],
            price_source="synthetic",
        )
        # Missing symbol/signal/as_of -> no rank, factor=1.0, modulated=0.5
        assert out[0]["cs_rank_pct"] is None
        assert out[0]["modulation_factor"] == 1.0
        assert out[0]["modulated_weight"] == 0.5


# ========== KR-006 prevention: display field correctness ==========

class TestDisplayFieldCorrectness:
    """All rank/strength/modulation values are dimensionless fractions
    in [0, 1]. Direct value assertions guard against scale bugs in the
    KR-006 vein (percent-vs-fraction, 100x scaling)."""

    def test_rank_is_in_unit_interval(self, p44_db):
        from research.ranking import cs_rank_pct
        syms_ratios = [("THYAO", 0.99), ("AKBNK", 0.80), ("BIMAS", 0.60)]
        for sym, ratio in syms_ratios:
            _seed_ratio_near_high(sym, date(2021, 1, 4), 400, 100.0, ratio)
        for sym, _ in syms_ratios:
            r = cs_rank_pct(sym, "52W High Breakout",
                            date(2023, 6, 15), price_source="synthetic")
            if r is not None:
                assert 0.0 <= r <= 1.0

    def test_modulation_is_in_unit_interval(self):
        from research.ranking import modulation_factor
        for r in (0.0, 0.15, 0.3, 0.5, 0.7, 0.85, 1.0):
            f = modulation_factor(r)
            assert 0.0 <= f <= 1.0

    def test_strength_values_in_unit_interval(self, p44_db):
        """Every signal's strength returns values in [0, 1] or None."""
        from research.ranking import signal_strength, STRENGTH_FUNCTIONS
        # Seed a mixed-behavior symbol: decline then rally (so most
        # signals see SOME non-zero strength)
        from infra.pit import save_price
        d = date(2022, 1, 3)
        i = 0
        while i < 280:
            if d.weekday() < 5:
                if i < 140:
                    px = 100.0 - i * 0.2
                else:
                    px = 72.0 + (i - 140) * 0.3
                save_price("TESTSYM", d, "synthetic",
                           open_=px, high=px * 1.01, low=px * 0.99,
                           close=px, volume=1e6)
                i += 1
            d += timedelta(days=1)

        end = date(2022, 1, 3) + timedelta(days=310)
        for name in STRENGTH_FUNCTIONS:
            s = signal_strength("TESTSYM", name, end, price_source="synthetic")
            if s is not None:
                assert 0.0 <= s <= 1.0, f"{name}: strength {s} outside [0,1]"

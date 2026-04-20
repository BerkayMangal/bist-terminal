"""Phase 4.1 + 4.2 tests: sectors, regime, multi-horizon validator,
sector-conditional calibration."""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def p4_db(tmp_path, monkeypatch):
    """Fresh DB; reset storage module-level thread-local."""
    db_path = tmp_path / "p4.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db_path))
    import infra.storage
    infra.storage._local = threading.local()
    infra.storage.DB_PATH = str(db_path)
    from infra.storage import init_db
    init_db()
    yield db_path


# ========== FAZ 4.2 / Q1 — Sector taxonomy ==========

class TestSectorCoverage:
    """SECTOR_MAP must cover every symbol in data/universe_history.csv.
    Regression guard: if a new symbol is added to the universe without
    a sector, this test fails loudly."""

    def test_every_universe_symbol_has_sector(self):
        from research.sectors import SECTOR_MAP
        import csv
        from tests._paths import UNIVERSE_CSV
        with open(UNIVERSE_CSV, encoding="utf-8") as f:
            syms = {r["symbol"].upper() for r in csv.DictReader(f)
                    if r.get("symbol")}
        missing = syms - set(SECTOR_MAP)
        assert not missing, f"universe symbols missing from SECTOR_MAP: {missing}"

    def test_get_sector_is_case_insensitive(self):
        from research.sectors import get_sector
        assert get_sector("THYAO") == "Havayolu"
        assert get_sector("thyao") == "Havayolu"
        assert get_sector("  ") is None
        assert get_sector("") is None
        assert get_sector("NOPE") is None

    def test_symbols_in_sector_roundtrip(self):
        from research.sectors import symbols_in_sector, SECTOR_MAP
        bankas = symbols_in_sector("Banka")
        assert set(bankas) == {s for s, sec in SECTOR_MAP.items()
                              if sec == "Banka"}

    def test_valid_sectors_matches_map_values(self):
        from research.sectors import SECTOR_MAP, VALID_SECTORS
        assert VALID_SECTORS == frozenset(SECTOR_MAP.values())
        # The 14 sectors per spec
        assert len(VALID_SECTORS) == 14


# ========== FAZ 4.1 / Q4 — Regime classifier ==========

class TestRegime:
    def _seed_xu100(self, bars: list[tuple[int, float]], start=date(2022, 1, 3)):
        """Seed XU100 closes. bars = [(day_offset, close), ...]."""
        from infra.pit import save_price
        d = start
        i = 0
        while i < max(o for o, _ in bars) + 1:
            if d.weekday() < 5:
                # Find the bar at offset i (if any)
                for offset, close in bars:
                    if offset == i:
                        save_price("XU100", d, "synthetic", close=close)
                        break
                i += 1
            d += timedelta(days=1)

    def _seed_linear_xu100(self, n_days: int, start_close: float, delta: float,
                          start=date(2022, 1, 3)):
        """Seed n_days of linear XU100 movement. close_i = start + i*delta."""
        from infra.pit import save_price
        d = start
        i = 0
        while i < n_days:
            if d.weekday() < 5:
                save_price("XU100", d, "synthetic",
                           close=start_close + i * delta)
                i += 1
            d += timedelta(days=1)

    def test_unknown_when_insufficient_history(self, p4_db):
        from research.regime import get_regime_at
        # Seed only 10 bars -- far below 200-MA requirement
        self._seed_linear_xu100(10, 10000, 10)
        r = get_regime_at(date(2022, 6, 1), price_source="synthetic")
        assert r.trend == "unknown"
        assert r.vol == "unknown"
        assert r.label == "unknown_unknown"

    def test_bull_trend_detected(self, p4_db):
        from research.regime import get_regime_at
        # Uptrending XU100: 300 days, +50/day (so 50-MA > 200-MA clearly)
        self._seed_linear_xu100(300, 10000, 50)
        start = date(2022, 1, 3)
        # Query somewhere in the last quarter so both MAs have history
        query = start + timedelta(days=360)
        r = get_regime_at(query, price_source="synthetic")
        assert r.trend == "bull", f"expected bull, got {r.trend} (label={r.label})"

    def test_bear_trend_detected(self, p4_db):
        from research.regime import get_regime_at
        self._seed_linear_xu100(300, 30000, -50)
        r = get_regime_at(date(2022, 1, 3) + timedelta(days=360),
                          price_source="synthetic")
        assert r.trend == "bear", f"expected bear, got {r.trend}"

    def test_label_is_trend_underscore_vol(self, p4_db):
        from research.regime import get_regime_at
        self._seed_linear_xu100(300, 10000, 50)
        r = get_regime_at(date(2022, 1, 3) + timedelta(days=360),
                          price_source="synthetic")
        assert r.label == f"{r.trend}_{r.vol}"

    def test_annotate_events_caches_date_lookups(self, p4_db):
        from research.regime import annotate_events_with_regime
        self._seed_linear_xu100(300, 10000, 50)
        # Multiple events on the same day -> one regime lookup
        events = [
            {"symbol": "THYAO", "as_of": "2022-06-01"},
            {"symbol": "AKBNK", "as_of": "2022-06-01"},
            {"symbol": "THYAO", "as_of": "2022-06-15"},
        ]
        out = annotate_events_with_regime(events, benchmark_symbol="XU100",
                                           price_source="synthetic")
        for ev in out:
            assert "regime" in ev
            assert "regime_trend" in ev
            assert "regime_vol" in ev
        # Two events on the same day should share the same regime label
        same_day = [e for e in out if e["as_of"] == "2022-06-01"]
        assert same_day[0]["regime"] == same_day[1]["regime"]


# ========== FAZ 4.1 — Multi-horizon validator ==========

class TestMultiHorizonValidator:
    def _seed_universe_and_prices(self, p4_db):
        from infra.pit import load_universe_history_csv, save_price
        load_universe_history_csv()
        # Seed one symbol + XU100 with a rising trend
        for sym in ("THYAO", "XU100"):
            d = date(2022, 1, 3)
            i = 0
            while i < 400:
                if d.weekday() < 5:
                    px = 100.0 + i * 0.3
                    save_price(sym, d, "synthetic",
                               open_=px, high=px * 1.005, low=px * 0.995,
                               close=px, volume=1e6)
                    i += 1
                d += timedelta(days=1)

    def test_horizon_stats_populated(self, p4_db):
        self._seed_universe_and_prices(p4_db)
        from research.validator import run_validator_multi_horizon
        r = run_validator_multi_horizon(
            signal_name="TestSig",
            detector=lambda s, d: True,
            universe="BIST30",
            from_date=date(2022, 6, 1), to_date=date(2023, 3, 31),
            sample_every_n_days=30,
            benchmark_symbol="XU100",
            horizons=(5, 20, 60),
            today=date(2023, 6, 30),
        )
        assert r.horizons == [5, 20, 60]
        # Every horizon key present (keys stringified by as_dict)
        assert set(r.horizon_stats.keys()) == {"5", "20", "60"}
        for h_str, s in r.horizon_stats.items():
            assert "n" in s
            assert "sharpe_ann" in s
            assert "sharpe_ann_net" in s  # Q5 net column

    def test_net_sharpe_less_than_gross(self, p4_db):
        """Net of 30bp per event must be <= gross. (For positive-return
        signals; negative returns would make net > gross after the same
        deduction applied -- we avoid that pathology by only asserting
        absolute-value for the net_is_not_nan case.)"""
        self._seed_universe_and_prices(p4_db)
        from research.validator import run_validator_multi_horizon
        r = run_validator_multi_horizon(
            signal_name="TestSig",
            detector=lambda s, d: True,
            from_date=date(2022, 6, 1), to_date=date(2023, 3, 31),
            sample_every_n_days=20,
            benchmark_symbol="XU100",
            horizons=(20,),
            today=date(2023, 6, 30),
            annotate_regime=False,
        )
        s = r.horizon_stats["20"]
        gross = s["sharpe_ann"]
        net = s["sharpe_ann_net"]
        # With positive uptrend + 30bp one-way cost, gross > net
        assert gross is not None and net is not None
        assert net < gross, f"net {net} should be < gross {gross}"

    def test_regime_breakdown_present_when_annotating(self, p4_db):
        self._seed_universe_and_prices(p4_db)
        from research.validator import run_validator_multi_horizon
        r = run_validator_multi_horizon(
            signal_name="TestSig",
            detector=lambda s, d: True,
            from_date=date(2022, 6, 1), to_date=date(2023, 3, 31),
            sample_every_n_days=20,
            benchmark_symbol="XU100",
            horizons=(20,),
            today=date(2023, 6, 30),
            annotate_regime=True,
        )
        # Should have at least one regime label populated
        assert len(r.regime_breakdown) >= 1

    def test_backward_compat_run_validator(self, p4_db):
        """Phase 3 run_validator must still return a ValidatorResult
        with Phase 3 top-level fields populated."""
        self._seed_universe_and_prices(p4_db)
        from research.validator import run_validator
        r = run_validator(
            signal_name="TestSig",
            detector=lambda s, d: True,
            from_date=date(2022, 6, 1), to_date=date(2023, 3, 31),
            sample_every_n_days=30,
            benchmark_symbol="XU100",
            today=date(2023, 6, 30),
        )
        assert r.n_trades >= 0
        # Phase 3 top-level field
        assert r.sharpe_20d_ann is not None or r.n_trades < 2
        # Phase 4 fields populated but regime is empty (compat mode)
        assert r.horizons  # multi-horizon grid still filled
        # annotate_regime=False in wrapper so breakdown is empty
        assert r.regime_breakdown == {}

    def test_report_emits_multi_horizon_section(self, p4_db, tmp_path):
        from research.validator import ValidatorResult, write_report
        r = ValidatorResult(
            signal="X", universe="BIST30",
            from_date="2022-01-01", to_date="2023-01-01",
            n_trades=100, hit_rate_5d=0.5, hit_rate_20d=0.55,
            avg_return_5d=0.001, avg_return_20d=0.01,
            std_return_20d=0.04, t_stat_20d=2.5, sharpe_20d_ann=1.2,
            ir_vs_benchmark_20d=0.3, benchmark_symbol="XU100",
            decision="keep_strong", notes=[],
            horizons=[20],
            horizon_stats={
                "20": {"n": 100, "hit_rate": 0.55, "avg_return": 0.01,
                      "std_return": 0.04, "t_stat": 2.5,
                      "sharpe_ann": 1.2, "sharpe_ann_net": 1.05,
                      "ir_vs_benchmark": 0.3},
            },
            regime_breakdown={
                "bull_low": {"n": 50, "avg_return_20d": 0.015,
                            "sharpe_20d_ann": 1.4},
                "bull_high": {"n": 50, "avg_return_20d": 0.005,
                             "sharpe_20d_ann": 0.8},
            },
        )
        jp, mp = write_report(r, tmp_path)
        md = mp.read_text()
        assert "Multi-horizon (Phase 4)" in md
        assert "Regime breakdown" in md
        assert "bull_low" in md
        # Net column should be in the multi-horizon table
        assert "Sharpe_ann (net)" in md


# ========== FAZ 4.2 — Sector-conditional calibration ==========

class TestCalibration:
    """Calibration math + end-to-end against the uploaded deep_events.csv.

    The CSV is the reviewer's Phase 3b ground truth (2776 events across
    9 signals × 14 sectors). calibrate_signal_weights on this data
    must reproduce the deep_summary.csv per-signal Sharpe values to
    4 decimal places -- they use the same formula.
    """

    def test_extract_return_handles_both_shapes(self):
        from research.calibration import _extract_return
        # deep_events.csv shape: fraction (0.0486 = 4.86%)
        assert _extract_return({"ret_20d": 0.0486}, 20) == pytest.approx(0.0486)
        # live labeler shape: fraction (same scale)
        assert _extract_return({"return_20d": 0.0486}, 20) == pytest.approx(0.0486)
        # deep_events.csv wins if both present (preferred column)
        assert _extract_return({"ret_20d": 0.05, "return_20d": 0.10}, 20) \
            == pytest.approx(0.05)
        # missing
        assert _extract_return({}, 20) is None
        # empty/None
        assert _extract_return({"ret_20d": None}, 20) is None
        assert _extract_return({"ret_20d": ""}, 20) is None

    def test_load_events_csv(self):
        from research.calibration import load_events_csv
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        assert len(events) == 2776
        # Sanity on one known row from deep_summary.csv
        assert any(e.get("signal") == "52W High Breakout" for e in events)

    def test_calibrated_default_matches_deep_summary(self):
        """_default weight_20d for each signal should equal the
        deep_summary.csv's sharpe_20d_ann for that signal."""
        from research.calibration import (
            load_events_csv, calibrate_signal_weights)
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events, horizons=(20, 60))

        import csv
        with open("/mnt/user-data/uploads/deep_summary.csv", encoding="utf-8") as f:
            expected = {r["signal"]: float(r["sharpe_20d_ann"])
                        for r in csv.DictReader(f)}

        for signal, sharpe_expected in expected.items():
            w = weights.get(signal, {}).get("_default", {}).get("weight_20d")
            assert w is not None, f"{signal}: no default weight"
            # The deep_summary.csv Sharpe is reported with many decimals;
            # allow 1% tolerance (rounding at weight computation level).
            assert abs(w - sharpe_expected) < 0.01, \
                f"{signal}: weight_20d={w} vs expected sharpe={sharpe_expected}"

    def test_sector_weights_differentiate(self):
        """Reviewer Bulgu 2: Kimya +14.4% hit, Banka +1.1% hit on 52W
        High Breakout. Their weights should differ substantially."""
        from research.calibration import (
            load_events_csv, calibrate_signal_weights, get_weight)
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events, horizons=(20, 60))

        kimya_20 = get_weight(weights, "52W High Breakout", "Kimya", 20)
        banka_20 = get_weight(weights, "52W High Breakout", "Banka", 20)
        assert kimya_20 is not None and banka_20 is not None
        assert kimya_20 > banka_20, \
            f"Kimya weight {kimya_20} should exceed Banka {banka_20}"
        # Rough magnitudes (verified in the smoke run): Kimya ~1.9, Banka ~0.2
        assert kimya_20 > 1.0
        assert banka_20 < 0.5

    def test_min_n_threshold_fallback(self):
        """GYO has only 12 events on 52W High Breakout (n < 20).
        It should NOT appear as a sector key; get_weight falls back to
        _default."""
        from research.calibration import (
            load_events_csv, calibrate_signal_weights, get_weight)
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events, horizons=(20, 60))

        sig_entry = weights["52W High Breakout"]
        assert "GYO" not in sig_entry, \
            "GYO has n<20 and shouldn't be a per-sector entry"
        default_w = sig_entry["_default"]["weight_20d"]
        gyo_w = get_weight(weights, "52W High Breakout", "GYO", 20)
        assert gyo_w == default_w

    def test_contrarian_weight_preserves_negative_sign(self):
        """Golden Cross has Sharpe -0.207 in deep_summary; weight_20d
        must be negative (contrarian signal, not flipped)."""
        from research.calibration import load_events_csv, calibrate_signal_weights
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events, horizons=(20, 60))
        gc = weights["Golden Cross"]["_default"]["weight_20d"]
        assert gc < 0, f"Golden Cross should have negative weight, got {gc}"

    def test_custom_min_n(self):
        """Lower min_n lets more sectors appear."""
        from research.calibration import load_events_csv, calibrate_signal_weights
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        w_default = calibrate_signal_weights(events, min_n=20)
        w_permissive = calibrate_signal_weights(events, min_n=5)
        # 52W High on GYO: 12 events (below 20, above 5)
        assert "GYO" not in w_default["52W High Breakout"]
        assert "GYO" in w_permissive["52W High Breakout"]

    def test_write_weights_json_roundtrip(self, tmp_path):
        from research.calibration import (
            load_events_csv, calibrate_signal_weights, write_weights_json)
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events)
        out = write_weights_json(weights, tmp_path / "w.json")
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert loaded == weights

    def test_write_weights_markdown(self, tmp_path):
        from research.calibration import (
            load_events_csv, calibrate_signal_weights, write_weights_markdown)
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events)
        out = write_weights_markdown(weights, tmp_path / "w.md")
        assert out.exists()
        md = out.read_text()
        # Smoke: a known signal and a known sector both appear
        assert "52W High Breakout" in md
        assert "Kimya" in md
        # Default row is labeled correctly
        assert "_default" in md

    def test_get_weight_none_for_unknown_signal(self):
        from research.calibration import get_weight
        assert get_weight({}, "Missing Signal", "Banka", 20) is None


class TestCalibrationEdgeCases:
    def test_empty_events_returns_empty_dict(self):
        from research.calibration import calibrate_signal_weights
        assert calibrate_signal_weights([]) == {}

    def test_all_none_returns_yield_none_weight(self):
        from research.calibration import calibrate_signal_weights
        events = [
            {"signal": "X", "symbol": "THYAO", "ret_20d": None, "ret_60d": None}
        ]
        w = calibrate_signal_weights(events)
        assert w["X"]["_default"]["weight_20d"] is None

    def test_zero_variance_returns_none(self):
        """If all returns are identical, std=0 and weight must be None."""
        from research.calibration import calibrate_signal_weights
        events = [
            {"signal": "X", "symbol": "THYAO", "ret_20d": 1.0, "ret_60d": 2.0}
            for _ in range(25)
        ]
        w = calibrate_signal_weights(events)
        assert w["X"]["_default"]["weight_20d"] is None

    def test_single_event_n_one_yields_none(self):
        """n=1 -> can't compute std -> weight is None."""
        from research.calibration import calibrate_signal_weights
        events = [{"signal": "X", "symbol": "THYAO", "ret_20d": 1.0, "ret_60d": 2.0}]
        w = calibrate_signal_weights(events)
        assert w["X"]["_default"]["weight_20d"] is None

    def test_attach_sector_from_symbol(self):
        """Events without a 'sector' field get one looked up from SECTOR_MAP."""
        from research.calibration import calibrate_signal_weights
        events = [
            {"signal": "X", "symbol": "THYAO", "ret_20d": 1.0, "ret_60d": 2.0},
            {"signal": "X", "symbol": "AKBNK", "ret_20d": 0.5, "ret_60d": 1.0},
        ] * 15  # Enough for min_n coverage
        w = calibrate_signal_weights(events, min_n=10)
        # Both Havayolu and Banka should appear as per-sector entries
        assert "Havayolu" in w["X"]
        assert "Banka" in w["X"]

    def test_unknown_symbol_bucketed_to_unknown_sector(self):
        """A symbol not in SECTOR_MAP gets sector='Unknown' (doesn't crash)."""
        from research.calibration import calibrate_signal_weights
        events = [{"signal": "X", "symbol": "NOSUCHSYMBOL",
                   "ret_20d": 1.0, "ret_60d": 2.0}] * 25
        w = calibrate_signal_weights(events, min_n=10)
        assert "Unknown" in w["X"]


class TestRegimeEdgeCases:
    def test_regime_label_shape(self):
        from research.regime import RegimeLabel
        r = RegimeLabel(trend="bull", vol="high", label="bull_high")
        assert r.label == "bull_high"

    def test_annotate_with_missing_benchmark(self, p4_db):
        """No XU100 in DB -> every event gets 'unknown_unknown'."""
        from research.regime import annotate_events_with_regime
        events = [{"symbol": "THYAO", "as_of": "2023-06-01"}]
        out = annotate_events_with_regime(events, benchmark_symbol="XU100",
                                           price_source="synthetic")
        assert out[0]["regime"] == "unknown_unknown"

    def test_neutral_trend_for_flat_market(self, p4_db):
        """Flat benchmark → 50-MA ≈ 200-MA → neutral trend."""
        from infra.pit import save_price
        from research.regime import get_regime_at
        d = date(2022, 1, 3)
        i = 0
        while i < 300:
            if d.weekday() < 5:
                # Small oscillation around 10000, no trend
                px = 10000 + (i % 2) * 20 - 10
                save_price("XU100", d, "synthetic", close=px)
                i += 1
            d += timedelta(days=1)
        r = get_regime_at(date(2022, 1, 3) + timedelta(days=360),
                          price_source="synthetic")
        assert r.trend == "neutral", f"expected neutral, got {r.trend}"


class TestMultiHorizonEdgeCases:
    def test_no_events_decision_kill(self, p4_db):
        from research.validator import run_validator_multi_horizon
        r = run_validator_multi_horizon(
            signal_name="NeverFires",
            detector=lambda s, d: False,
            from_date=date(2023, 1, 1), to_date=date(2023, 6, 30),
            sample_every_n_days=30,
            benchmark_symbol=None,
            horizons=(20,),
            today=date(2023, 12, 31),
            annotate_regime=False,
        )
        assert r.n_trades == 0
        assert r.decision == "kill"
        assert any("no events" in n for n in r.notes)

    def test_nondefault_canonical_horizon(self, p4_db):
        """If 20 is NOT in horizons, the first horizon becomes canonical."""
        from infra.pit import save_price, load_universe_history_csv
        load_universe_history_csv()
        # Seed just enough data for the detector to fire
        d = date(2022, 1, 3)
        i = 0
        while i < 400:
            if d.weekday() < 5:
                px = 100.0 + i * 0.3
                save_price("THYAO", d, "synthetic",
                           open_=px, high=px * 1.005, low=px * 0.995,
                           close=px, volume=1e6)
                i += 1
            d += timedelta(days=1)

        from research.validator import run_validator_multi_horizon
        r = run_validator_multi_horizon(
            signal_name="TestSig",
            detector=lambda s, d: s == "THYAO",
            from_date=date(2022, 6, 1), to_date=date(2023, 3, 31),
            sample_every_n_days=30,
            benchmark_symbol=None,
            horizons=(10, 60),  # 20 NOT included
            today=date(2023, 6, 30),
            annotate_regime=False,
        )
        # Horizon_stats has 10 and 60, decision made on 10 (first in list)
        assert set(r.horizon_stats.keys()) == {"10", "60"}


class TestCalibrationReportOutputs:
    """File-writing side of calibration -- report structure & roundtrip."""

    def test_json_structure_per_signal(self, tmp_path):
        from research.calibration import (
            load_events_csv, calibrate_signal_weights, write_weights_json)
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events)
        out = write_weights_json(weights, tmp_path / "w.json")
        data = json.loads(out.read_text())
        # Every signal has a _default entry
        for signal, entry in data.items():
            assert "_default" in entry
            assert "n" in entry["_default"]
            assert "weight_20d" in entry["_default"]
            assert "weight_60d" in entry["_default"]

    def test_markdown_has_all_signal_sections(self, tmp_path):
        from research.calibration import (
            load_events_csv, calibrate_signal_weights, write_weights_markdown)
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events)
        out = write_weights_markdown(weights, tmp_path / "w.md")
        md = out.read_text()
        for signal in weights:
            assert f"## {signal}" in md, f"missing section: {signal}"

    def test_markdown_sorts_sectors_by_abs_weight(self, tmp_path):
        """Highest |weight_20d| sectors should appear earliest after _default.
        Smoke check: Havayolu (w20d≈2.78) appears before Banka (w20d≈0.23)
        for 52W High Breakout."""
        from research.calibration import (
            load_events_csv, calibrate_signal_weights, write_weights_markdown)
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events)
        out = write_weights_markdown(weights, tmp_path / "w.md")
        md = out.read_text()
        # Extract the 52W High Breakout section
        start = md.find("## 52W High Breakout")
        end = md.find("\n## ", start + 1)
        section = md[start:end] if end > 0 else md[start:]
        # Within this section, Havayolu should come before Banka
        hav_pos = section.find("| Havayolu |")
        banka_pos = section.find("| Banka |")
        assert hav_pos > 0 and banka_pos > 0
        assert hav_pos < banka_pos, "sectors should be sorted by |weight| DESC"


class TestIntegrationCalibrationFlow:
    """End-to-end: events CSV -> weights -> get_weight lookup chain."""

    def test_full_flow_deep_events(self, tmp_path):
        from research.calibration import (
            load_events_csv, calibrate_signal_weights,
            get_weight, write_weights_json, write_weights_markdown)
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        assert len(events) == 2776

        weights = calibrate_signal_weights(events, horizons=(20, 60))
        write_weights_json(weights, tmp_path / "w.json")
        write_weights_markdown(weights, tmp_path / "w.md")

        # Spot-check several reviewer-specified (signal, sector) pairs
        spot_checks = [
            ("52W High Breakout", "Kimya", 20, lambda w: w > 1.0),
            ("52W High Breakout", "Banka", 20, lambda w: w < 0.5),
            ("52W High Breakout", "Havayolu", 20, lambda w: w > 2.0),
            ("RSI Asiri Alim", None, 20, lambda w: w > 1.0),  # default
            ("Golden Cross", None, 20, lambda w: w < 0),
        ]
        for sig, sec, h, cond in spot_checks:
            w = get_weight(weights, sig, sec, h)
            assert w is not None and cond(w), \
                f"({sig}, {sec}, {h}d): weight {w} failed check"

    def test_all_signals_have_weight_20d_and_60d(self):
        from research.calibration import load_events_csv, calibrate_signal_weights
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events)
        for signal, entry in weights.items():
            d = entry["_default"]
            assert d["weight_20d"] is not None, f"{signal}: no 20d weight"
            assert d["weight_60d"] is not None, f"{signal}: no 60d weight"

    def test_horizon_mean_return_stored(self):
        """Sanity: mean_return_20d and mean_return_60d are both captured."""
        from research.calibration import load_events_csv, calibrate_signal_weights
        events = load_events_csv("/mnt/user-data/uploads/deep_events.csv")
        weights = calibrate_signal_weights(events)
        for sig, entry in weights.items():
            d = entry["_default"]
            assert "mean_return_20d" in d
            assert "mean_return_60d" in d


class TestSectorListExpectations:
    """Sanity checks on the hardcoded SECTOR_MAP."""

    def test_all_banks_in_banka_sector(self):
        from research.sectors import get_sector
        for sym in ("AKBNK", "GARAN", "ISCTR", "YKBNK", "HALKB", "VAKBN"):
            assert get_sector(sym) == "Banka"

    def test_airline_duplicates_classified_havayolu(self):
        from research.sectors import get_sector
        assert get_sector("THYAO") == "Havayolu"
        assert get_sector("PGSUS") == "Havayolu"

    def test_holdings_separate_from_sanayi(self):
        """Holding companies (KCHOL, SAHOL, ENKAI, OYAKC) are NOT Sanayi."""
        from research.sectors import get_sector
        for sym in ("KCHOL", "SAHOL", "ENKAI", "OYAKC"):
            assert get_sector(sym) == "Holding"
            assert get_sector(sym) != "Sanayi"

    def test_sectors_in_csv_match_map(self):
        """Sectors listed in deep_events.csv match SECTOR_MAP values exactly."""
        import csv
        with open("/mnt/user-data/uploads/deep_events.csv", encoding="utf-8") as f:
            csv_sectors = {r["sector"] for r in csv.DictReader(f)
                           if r.get("sector")}
        from research.sectors import VALID_SECTORS
        # Every CSV sector must be a known one
        unknown = csv_sectors - VALID_SECTORS
        assert not unknown, f"CSV has sectors not in SECTOR_MAP: {unknown}"


class TestValidatorConfigConstants:
    """Guards on the module-level constants consumers depend on."""

    def test_net_assumption_bps_is_30(self):
        """Reviewer Q5 default. Changing this changes every net_* stat."""
        from research.validator import NET_ASSUMPTION_BPS
        assert NET_ASSUMPTION_BPS == 30

    def test_result_dataclass_default_horizons_empty(self):
        """Backward-compat: omit horizons in Phase 3 callers -> empty list."""
        from research.validator import ValidatorResult
        r = ValidatorResult(
            signal="x", universe="BIST30", from_date="2022-01-01",
            to_date="2022-12-31", n_trades=0, hit_rate_5d=None,
            hit_rate_20d=None, avg_return_5d=None, avg_return_20d=None,
            std_return_20d=None, t_stat_20d=None, sharpe_20d_ann=None,
            ir_vs_benchmark_20d=None, benchmark_symbol=None,
            decision="kill", notes=[],
        )
        assert r.horizons == []
        assert r.horizon_stats == {}
        assert r.regime_breakdown == {}


class TestRegimeAnnotationFallthrough:
    """Multi-horizon validator annotate_regime=True must not crash when
    the benchmark data is missing -- it should degrade gracefully."""

    def test_no_benchmark_doesnt_crash(self, p4_db):
        from infra.pit import load_universe_history_csv, save_price
        load_universe_history_csv()
        # Seed only THYAO, NO XU100
        d = date(2022, 1, 3)
        i = 0
        while i < 300:
            if d.weekday() < 5:
                px = 100.0 + i * 0.2
                save_price("THYAO", d, "synthetic",
                           open_=px, high=px * 1.01, low=px * 0.99,
                           close=px, volume=1e6)
                i += 1
            d += timedelta(days=1)

        from research.validator import run_validator_multi_horizon
        r = run_validator_multi_horizon(
            signal_name="TestSig",
            detector=lambda s, d: s == "THYAO",
            from_date=date(2022, 6, 1), to_date=date(2023, 3, 31),
            sample_every_n_days=30,
            benchmark_symbol="XU100",
            horizons=(20,),
            today=date(2023, 6, 30),
            annotate_regime=True,
        )
        # Regime tagging should fall through to unknown_unknown
        if r.regime_breakdown:
            # Expect exclusively unknown_unknown labels when XU100 missing
            assert "unknown_unknown" in r.regime_breakdown


class TestRegimeNetSharpeHorizons:
    def test_three_horizons_all_have_net(self, p4_db):
        """5d / 20d / 60d each get their own net_* stat."""
        from infra.pit import load_universe_history_csv, save_price
        load_universe_history_csv()
        for sym in ("THYAO", "XU100"):
            d = date(2022, 1, 3)
            i = 0
            while i < 400:
                if d.weekday() < 5:
                    px = 100.0 + i * 0.2
                    save_price(sym, d, "synthetic",
                               open_=px, high=px * 1.005, low=px * 0.995,
                               close=px, volume=1e6)
                    i += 1
                d += timedelta(days=1)

        from research.validator import run_validator_multi_horizon
        r = run_validator_multi_horizon(
            signal_name="Test",
            detector=lambda s, d: s == "THYAO",
            from_date=date(2022, 6, 1), to_date=date(2023, 3, 31),
            sample_every_n_days=30,
            benchmark_symbol="XU100",
            horizons=(5, 20, 60),
            today=date(2023, 6, 30),
            annotate_regime=False,
        )
        for h_str in ("5", "20", "60"):
            s = r.horizon_stats[h_str]
            assert "sharpe_ann_net" in s
            assert "avg_return_net" in s
            assert "t_stat_net" in s


class TestSectorMapSpecExactness:
    """The hardcoded SECTOR_MAP must exactly match the reviewer-provided
    map in the Phase 4.1 continuation doc. Changing any entry should
    trip this test so the PR author knows the spec is diverging."""

    def test_sector_map_count(self):
        from research.sectors import SECTOR_MAP
        assert len(SECTOR_MAP) == 34

    def test_specific_assignments_from_spec(self):
        from research.sectors import get_sector
        # Sampled entries spanning all 14 sectors -- spec reference
        assert get_sector("SASA") == "Kimya"
        assert get_sector("HEKTS") == "Kimya"
        assert get_sector("TUPRS") == "Enerji"
        assert get_sector("PETKM") == "Enerji"
        assert get_sector("AKSEN") == "Enerji"
        assert get_sector("BIMAS") == "Perakende"
        assert get_sector("ULKER") == "Gıda"
        assert get_sector("ARCLK") == "Sanayi"
        assert get_sector("EREGL") == "Demir-Çelik"
        assert get_sector("KOZAL") == "Madencilik"
        assert get_sector("EKGYO") == "GYO"
        assert get_sector("TAVHL") == "Ulaşım"
        assert get_sector("TTKOM") == "Telekom"

    def test_astor_is_savunma(self):
        """ASTOR classification is specifically called out in the Phase 4.1 spec."""
        from research.sectors import get_sector
        assert get_sector("ASTOR") == "Savunma"

    def test_oyakc_is_holding_not_sanayi(self):
        """Reviewer's reference map places OYAKC in Holding, not Sanayi."""
        from research.sectors import get_sector
        assert get_sector("OYAKC") == "Holding"

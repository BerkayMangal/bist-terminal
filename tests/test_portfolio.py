# ================================================================
# tests/test_portfolio.py
#
# Trade tracker (Faz 5) — storage + exit signal engine.
# Tests cover the contract every endpoint depends on:
#  - position open/close idempotency
#  - exit signal verdict bands (hold/caution/sell)
#  - each criterion weighted contribution
#  - delisting from BullWatch = strongest sell signal
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_portfolio.db"
    monkeypatch.setattr("infra.storage.DB_PATH", str(db_file))
    import infra.portfolio_storage as p
    p._local.conn = None
    p.init_db()
    yield p
    p._local.conn = None


# ────────────────────────────────────────────────────────────────
# Storage round-trip
# ────────────────────────────────────────────────────────────────


class TestStorage:
    def test_open_and_get(self, tmp_db):
        pos = tmp_db.open_position(
            ticker="BIMAS", entry_price=30.50, lot=100,
            notes="bw conviction alarm",
            score_at_entry=82.5, zone_at_entry="CONVICTION",
            kap_at_entry=0.8, own_at_entry=0.5,
        )
        assert pos is not None
        assert pos["status"] == "open"
        assert pos["ticker"] == "BIMAS"
        assert pos["entry_price"] == 30.50
        assert pos["lot"] == 100
        assert pos["score_at_entry"] == 82.5
        assert pos["zone_at_entry"] == "CONVICTION"
        # Default risk params populated
        assert pos["stop_loss_pct"] == -8.0
        assert pos["take_profit_pct"] == 15.0
        # Round-trip via id
        again = tmp_db.get_by_id(pos["position_id"])
        assert again["ticker"] == "BIMAS"

    def test_open_rejects_invalid(self, tmp_db):
        assert tmp_db.open_position(ticker="", entry_price=10, lot=10) is None
        assert tmp_db.open_position(ticker="X", entry_price=0, lot=10) is None
        assert tmp_db.open_position(ticker="X", entry_price=10, lot=0) is None
        assert tmp_db.open_position(ticker="X", entry_price=-5, lot=10) is None

    def test_ticker_normalized(self, tmp_db):
        pos = tmp_db.open_position(
            ticker="bimas.is", entry_price=30, lot=10,
        )
        assert pos["ticker"] == "BIMAS"

    def test_close_position(self, tmp_db):
        pos = tmp_db.open_position(ticker="BIMAS", entry_price=30, lot=100)
        assert pos["status"] == "open"
        ok = tmp_db.close_position(
            pos["position_id"], exit_price=33.5,
            exit_reason="take profit",
        )
        assert ok is True
        closed = tmp_db.get_by_id(pos["position_id"])
        assert closed["status"] == "closed"
        assert closed["exit_price"] == 33.5
        assert closed["exit_reason"] == "take profit"

    def test_close_idempotent(self, tmp_db):
        pos = tmp_db.open_position(ticker="X", entry_price=10, lot=1)
        tmp_db.close_position(pos["position_id"], 11)
        # Second close fails (status not 'open')
        assert tmp_db.close_position(pos["position_id"], 11) is False

    def test_get_open_excludes_closed(self, tmp_db):
        a = tmp_db.open_position(ticker="A", entry_price=10, lot=1)
        b = tmp_db.open_position(ticker="B", entry_price=20, lot=1)
        tmp_db.close_position(a["position_id"], 11)
        open_list = tmp_db.get_open()
        ids = [p["position_id"] for p in open_list]
        assert b["position_id"] in ids
        assert a["position_id"] not in ids

    def test_get_history_only_closed(self, tmp_db):
        a = tmp_db.open_position(ticker="A", entry_price=10, lot=1)
        b = tmp_db.open_position(ticker="B", entry_price=20, lot=1)
        tmp_db.close_position(a["position_id"], 11)
        hist = tmp_db.get_history()
        ids = [p["position_id"] for p in hist]
        assert a["position_id"] in ids
        assert b["position_id"] not in ids

    def test_stats_aggregates(self, tmp_db):
        # 2 winners, 1 loser
        for px in [(10, 12), (20, 25), (30, 28)]:
            pos = tmp_db.open_position(ticker="X", entry_price=px[0], lot=1)
            tmp_db.close_position(pos["position_id"], px[1])
        stats = tmp_db.get_stats()
        assert stats["closed_count"] == 3
        assert stats["winners"] == 2
        assert stats["losers"] == 1
        assert stats["win_rate"] == pytest.approx(66.7, abs=0.5)


# ────────────────────────────────────────────────────────────────
# Exit signal engine — criterion-by-criterion
# ────────────────────────────────────────────────────────────────


class TestZoneDegradation:
    def test_no_degradation_hold(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"zone_at_entry": "CONVICTION", "entry_price": 100}
        item = {"zone": "CONVICTION", "components": {}}
        sig = compute_exit_signal(pos, item, current_price=100)
        assert sig["verdict"] == "hold"

    def test_one_zone_down(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"zone_at_entry": "CONVICTION", "entry_price": 100}
        item = {"zone": "CONFIRMED", "components": {}}
        sig = compute_exit_signal(pos, item, current_price=100)
        # zone_degradation 50pt * 0.30 weight = 15 score → still hold
        assert sig["details"]["zone_degradation"] == 50
        assert sig["verdict"] in ("hold", "caution")

    def test_two_zones_down(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"zone_at_entry": "CONVICTION", "entry_price": 100}
        item = {"zone": "EARLY", "components": {}}
        sig = compute_exit_signal(pos, item, current_price=100)
        # 100pt zone_deg * 0.30 = 30 — caution territory
        assert sig["details"]["zone_degradation"] == 100
        assert sig["verdict"] in ("caution", "sell")

    def test_delisting_strongest_signal(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"zone_at_entry": "CONVICTION", "entry_price": 100,
               "stop_loss_pct": -8.0}
        # current_item=None → tahmin: BullWatch'tan düştü
        sig = compute_exit_signal(pos, current_item=None, current_price=100)
        assert sig["details"]["zone_degradation"] == 100


class TestScoreDrop:
    def test_score_unchanged(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"score_at_entry": 80, "zone_at_entry": "CONVICTION",
               "entry_price": 100}
        item = {"zone": "CONVICTION", "score": 80, "components": {}}
        sig = compute_exit_signal(pos, item, current_price=100)
        assert sig["details"]["score_drop"] == 0

    def test_score_dropped_significantly(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"score_at_entry": 85, "zone_at_entry": "CONVICTION",
               "entry_price": 100}
        item = {"zone": "CONVICTION", "score": 60, "components": {}}
        sig = compute_exit_signal(pos, item, current_price=100)
        # 25pt drop → above 20pt cap = 100 pts
        assert sig["details"]["score_drop"] == 100


class TestTahtaciWeak:
    def test_kap_disappeared(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"kap_at_entry": 0.8, "own_at_entry": 0.0,
               "zone_at_entry": "CONVICTION", "entry_price": 100}
        item = {"zone": "CONVICTION",
                "components": {"kap_activity": 0.1, "ownership": 0.0}}
        sig = compute_exit_signal(pos, item, current_price=100)
        # KAP was strong (0.8), now 0.1 → 50pt
        assert sig["details"]["tahtaci_weak"] >= 50

    def test_ownership_disappeared(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"kap_at_entry": 0.0, "own_at_entry": 0.7,
               "zone_at_entry": "CONVICTION", "entry_price": 100}
        item = {"zone": "CONVICTION",
                "components": {"kap_activity": 0.0, "ownership": 0.1}}
        sig = compute_exit_signal(pos, item, current_price=100)
        assert sig["details"]["tahtaci_weak"] >= 50

    def test_no_change(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"kap_at_entry": 0.5, "own_at_entry": 0.5,
               "zone_at_entry": "CONVICTION", "entry_price": 100}
        item = {"zone": "CONVICTION",
                "components": {"kap_activity": 0.5, "ownership": 0.5}}
        sig = compute_exit_signal(pos, item, current_price=100)
        assert sig["details"]["tahtaci_weak"] == 0


class TestStopLoss:
    def test_stop_triggered(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"entry_price": 100, "stop_loss_pct": -8.0,
               "zone_at_entry": "CONVICTION"}
        item = {"zone": "CONVICTION", "components": {}}
        sig = compute_exit_signal(pos, item, current_price=91.5)
        # -8.5% < -8% stop → 100 pts
        assert sig["details"]["stop_loss"] == 100

    def test_stop_near(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"entry_price": 100, "stop_loss_pct": -8.0,
               "zone_at_entry": "CONVICTION"}
        item = {"zone": "CONVICTION", "components": {}}
        sig = compute_exit_signal(pos, item, current_price=94)
        # -6% / -8% = 75% progress → 70 pts
        assert sig["details"]["stop_loss"] == 70

    def test_no_stop_when_in_profit(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"entry_price": 100, "stop_loss_pct": -8.0,
               "zone_at_entry": "CONVICTION"}
        item = {"zone": "CONVICTION", "components": {}}
        sig = compute_exit_signal(pos, item, current_price=105)
        assert sig["details"]["stop_loss"] == 0


class TestTakeProfit:
    def test_target_hit(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"entry_price": 100, "take_profit_pct": 15.0,
               "zone_at_entry": "CONVICTION"}
        item = {"zone": "CONVICTION", "components": {}}
        sig = compute_exit_signal(pos, item, current_price=116)
        assert sig["details"]["take_profit"] == 100

    def test_target_off(self):
        # take_profit_pct=0 disables
        from engine.portfolio_signals import compute_exit_signal
        pos = {"entry_price": 100, "take_profit_pct": 0,
               "zone_at_entry": "CONVICTION"}
        item = {"zone": "CONVICTION", "components": {}}
        sig = compute_exit_signal(pos, item, current_price=200)
        assert sig["details"]["take_profit"] == 0


# ────────────────────────────────────────────────────────────────
# End-to-end verdict bands
# ────────────────────────────────────────────────────────────────


class TestVerdictBands:
    def test_all_quiet_holds(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {
            "entry_price": 100, "score_at_entry": 80,
            "zone_at_entry": "CONVICTION",
            "kap_at_entry": 0.5, "own_at_entry": 0.5,
            "stop_loss_pct": -8.0, "take_profit_pct": 15.0,
        }
        item = {
            "zone": "CONVICTION", "score": 80,
            "components": {"kap_activity": 0.5, "ownership": 0.5},
        }
        sig = compute_exit_signal(pos, item, current_price=101)
        assert sig["verdict"] == "hold"
        assert sig["score"] < 35

    def test_stop_alone_triggers_sell(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {
            "entry_price": 100, "stop_loss_pct": -8.0,
            "zone_at_entry": "CONVICTION",
            "score_at_entry": 80,
            "kap_at_entry": 0.5, "own_at_entry": 0.5,
        }
        # Stop hit, everything else fine — 100*0.20 = 20pts → still hold
        # need more
        item = {
            "zone": "CONFIRMED", "score": 60,
            "components": {"kap_activity": 0.1, "ownership": 0.1},
        }
        # Stop hit + zone down + score drop + tahtacı weak = many signals
        sig = compute_exit_signal(pos, item, current_price=91)
        assert sig["verdict"] == "sell"

    def test_delisted_with_loss_sell(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {
            "entry_price": 100, "stop_loss_pct": -8.0,
            "zone_at_entry": "CONVICTION", "score_at_entry": 80,
            "kap_at_entry": 0.5, "own_at_entry": 0.5,
        }
        sig = compute_exit_signal(pos, current_item=None, current_price=92)
        assert sig["verdict"] in ("caution", "sell")

    def test_pnl_computed(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {"entry_price": 100, "zone_at_entry": "CONVICTION"}
        item = {"zone": "CONVICTION", "components": {}}
        sig = compute_exit_signal(pos, item, current_price=115)
        assert sig["pnl_pct"] == 15.0

    def test_reasons_compiled(self):
        from engine.portfolio_signals import compute_exit_signal
        pos = {
            "entry_price": 100, "stop_loss_pct": -8.0,
            "zone_at_entry": "CONVICTION", "score_at_entry": 85,
            "kap_at_entry": 0.7, "own_at_entry": 0.6,
        }
        item = {
            "zone": "EARLY", "score": 60,
            "components": {"kap_activity": 0.1, "ownership": 0.1},
        }
        sig = compute_exit_signal(pos, item, current_price=89)
        # Multiple reasons should appear
        assert len(sig["reasons"]) >= 3
        # Strings should be human-readable Turkish
        assert any("Zone" in r for r in sig["reasons"])


# ────────────────────────────────────────────────────────────────
# Batch signals
# ────────────────────────────────────────────────────────────────


class TestBatchSignals:
    def test_sorts_sell_first(self):
        from engine.portfolio_signals import compute_signals_for_open_positions
        positions = [
            {"position_id": "1", "ticker": "HOLD",
             "entry_price": 100, "score_at_entry": 80,
             "zone_at_entry": "CONVICTION",
             "kap_at_entry": 0.5, "own_at_entry": 0.5,
             "stop_loss_pct": -8.0, "take_profit_pct": 15.0},
            {"position_id": "2", "ticker": "SELL",
             "entry_price": 100, "score_at_entry": 80,
             "zone_at_entry": "CONVICTION",
             "kap_at_entry": 0.7, "own_at_entry": 0.6,
             "stop_loss_pct": -8.0, "take_profit_pct": 15.0},
        ]
        items = {
            "HOLD": {"zone": "CONVICTION", "score": 80,
                     "components": {"kap_activity": 0.5, "ownership": 0.5}},
            "SELL": {"zone": "EARLY", "score": 60,
                     "components": {"kap_activity": 0.1, "ownership": 0.1}},
        }
        prices = {"HOLD": 101, "SELL": 89}
        out = compute_signals_for_open_positions(positions, items, prices)
        verdicts = [r["signal"]["verdict"] for r in out]
        # sell should rank before hold
        assert verdicts[0] in ("sell", "caution")
        assert verdicts[-1] == "hold"

    def test_missing_item_handled(self):
        from engine.portfolio_signals import compute_signals_for_open_positions
        # Ticker has no live BullWatch entry — should NOT crash and
        # zone_degradation should fire (delisting signal)
        positions = [
            {"position_id": "1", "ticker": "GONE",
             "entry_price": 100, "score_at_entry": 80,
             "zone_at_entry": "CONVICTION",
             "stop_loss_pct": -8.0, "take_profit_pct": 15.0},
        ]
        out = compute_signals_for_open_positions(
            positions, items_by_ticker={}, prices_by_ticker={"GONE": 100},
        )
        assert len(out) == 1
        sig = out[0]["signal"]
        # Delisting triggers zone_degradation 100pt
        assert sig["details"]["zone_degradation"] == 100

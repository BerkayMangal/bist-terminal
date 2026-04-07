import pytest, os, sqlite3
os.environ["BISTBULL_DB_PATH"] = "/tmp/test_delta.db"

# Clean slate
if os.path.exists("/tmp/test_delta.db"):
    os.remove("/tmp/test_delta.db")

from storage import _get_conn, init_db
from engine.delta import save_daily_snapshot, compute_delta, watchlist_changes, get_movers

# Init tables
init_db()

def _analysis(**o):
    a = {"overall": 65, "deger": 65, "ivme": 55, "risk_score": -5, "fa_score": 60, "decision": "İZLE"}
    a.update(o); return a

class TestDelta:
    def test_save_snapshot(self):
        save_daily_snapshot("TEST.IS", _analysis())
        conn = _get_conn()
        row = conn.execute("SELECT * FROM score_history WHERE symbol='TEST.IS'").fetchone()
        assert row is not None

    def test_no_delta_first_day(self):
        r = compute_delta("NEWSTOCK.IS", _analysis())
        assert r == {} or "delta" not in r  # no history yet

    def test_delta_with_history(self):
        conn = _get_conn()
        conn.execute("INSERT OR REPLACE INTO score_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     ("DELTA.IS", "2026-03-25", 60, 50, -3, 55, 50, "BEKLE"))
        conn.commit()
        r = compute_delta("DELTA.IS", _analysis(overall=70, ivme=60, risk_score=-8))
        assert "delta" in r
        assert r["delta"]["score_7d"] == 10.0

    def test_what_changed_score_up(self):
        conn = _get_conn()
        conn.execute("INSERT OR REPLACE INTO score_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     ("WC.IS", "2026-03-25", 50, 40, 0, 45, 40, "BEKLE"))
        conn.commit()
        r = compute_delta("WC.IS", _analysis(overall=60, ivme=55, risk_score=-5))
        assert any("arttı" in w for w in r.get("what_changed", []))

    def test_what_changed_momentum_down(self):
        conn = _get_conn()
        conn.execute("INSERT OR REPLACE INTO score_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     ("MD.IS", "2026-03-25", 65, 70, -5, 60, 70, "AL"))
        conn.commit()
        r = compute_delta("MD.IS", _analysis(overall=65, ivme=55, risk_score=-5))
        assert any("zayıf" in w for w in r.get("what_changed", []))

    def test_watchlist_changes_empty(self):
        assert watchlist_changes([]) == []

    def test_movers_empty(self):
        m = get_movers()
        assert "gainers" in m and "losers" in m

    def test_never_crashes(self):
        save_daily_snapshot("X", {})
        assert isinstance(compute_delta("X", {}), dict)

    def test_max_3_what_changed(self):
        conn = _get_conn()
        conn.execute("INSERT OR REPLACE INTO score_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     ("M3.IS", "2026-03-25", 30, 30, 5, 25, 30, "KAÇIN"))
        conn.commit()
        r = compute_delta("M3.IS", _analysis(overall=70, ivme=70, risk_score=-10))
        assert len(r.get("what_changed", [])) <= 3

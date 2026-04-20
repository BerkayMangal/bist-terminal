# Phase 2: KR-001 closed via migration 003_score_history.
# score_history table now exists; the xfail markers from Phase 0 are removed.
# If a later change breaks the schema again, these tests will fail loudly.
import os;os.environ["BISTBULL_DB_PATH"]="/tmp/test_delta2.db"
if os.path.exists("/tmp/test_delta2.db"):os.remove("/tmp/test_delta2.db")
from infra.storage import _get_conn,init_db;from engine.delta import save_daily_snapshot,compute_delta,watchlist_changes,get_movers
init_db()

class TestDelta:
 def test_save(self):save_daily_snapshot("T.IS",{"overall":65,"ivme":55,"risk_score":-5,"fa_score":60,"decision":"İZLE"});r=_get_conn().execute("SELECT * FROM score_history WHERE symbol='T.IS'").fetchone();assert r is not None
 def test_no_history(self):assert compute_delta("NEW.IS",{"overall":65})=={}
 def test_with_history(self):
  _get_conn().execute("INSERT OR REPLACE INTO score_history(symbol,snap_date,score,momentum,risk,fa_score,ivme,decision) VALUES(?,?,?,?,?,?,?,?)",("D.IS","2026-03-25",60,50,-3,55,50,"BEKLE"));_get_conn().commit()
  r=compute_delta("D.IS",{"overall":70,"ivme":60,"risk_score":-8});assert r.get("delta",{}).get("score_7d")==10.0
 def test_what_changed(self):
  _get_conn().execute("INSERT OR REPLACE INTO score_history(symbol,snap_date,score,momentum,risk,fa_score,ivme,decision) VALUES(?,?,?,?,?,?,?,?)",("W.IS","2026-03-25",50,40,0,45,40,"BEKLE"));_get_conn().commit()
  r=compute_delta("W.IS",{"overall":60,"ivme":55,"risk_score":-5});assert any("skor" in w.lower() or "güçlendi" in w for w in r.get("what_changed",[]))
 def test_movers(self):m=get_movers();assert "gainers" in m
 def test_crash(self):save_daily_snapshot("X",{});assert isinstance(compute_delta("X",{}),dict)

from __future__ import annotations
import logging; from datetime import date, timedelta
log = logging.getLogger("bistbull.delta")
def _sf(v,d=0):
 if v is None: return d
 try: return float(v)
 except: return d
def save_daily_snapshot(symbol,a,scoring_version=None):
 try: _save(symbol,a,scoring_version)
 except Exception as e: log.debug(f"delta save failed: {e}")
def compute_delta(symbol,a):
 try: return _compute(symbol,a)
 except Exception as e: log.debug(f"delta compute failed: {e}"); return {}
def _save(symbol,a,scoring_version=None):
 from infra.storage import _get_conn
 conn=_get_conn(); today=date.today().isoformat()
 # Phase 2: migration 003 added scoring_version to the PK triple so Phase 4
 # calibrated scoring can coexist with v13. Default value 'v13_handpicked'
 # kicks in via the column DEFAULT. ON CONFLICT target must match the full
 # PK, otherwise sqlite raises 'ON CONFLICT clause does not match any
 # PRIMARY KEY or UNIQUE constraint'.
 # Phase 4.9: scoring_version can be explicitly passed to write distinct
 # rows for V13 and calibrated on the same date (A/B telemetry). None =>
 # the column DEFAULT takes over (v13_handpicked) for backward compat.
 if scoring_version is None:
  conn.execute("INSERT INTO score_history(symbol,snap_date,score,momentum,risk,fa_score,ivme,decision) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,snap_date,scoring_version) DO UPDATE SET score=excluded.score,momentum=excluded.momentum,risk=excluded.risk,fa_score=excluded.fa_score,ivme=excluded.ivme,decision=excluded.decision",
   (symbol.upper(),today,_sf(a.get("overall") or a.get("deger")),_sf(a.get("ivme")),_sf(a.get("risk_score")),_sf(a.get("fa_score")),_sf(a.get("ivme")),a.get("decision","")))
 else:
  conn.execute("INSERT INTO score_history(symbol,snap_date,score,momentum,risk,fa_score,ivme,decision,scoring_version) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,snap_date,scoring_version) DO UPDATE SET score=excluded.score,momentum=excluded.momentum,risk=excluded.risk,fa_score=excluded.fa_score,ivme=excluded.ivme,decision=excluded.decision",
   (symbol.upper(),today,_sf(a.get("overall") or a.get("deger")),_sf(a.get("ivme")),_sf(a.get("risk_score")),_sf(a.get("fa_score")),_sf(a.get("ivme")),a.get("decision",""),scoring_version))
 conn.commit()
def _compute(symbol,a):
 from infra.storage import _get_conn
 conn=_get_conn(); wa=(date.today()-timedelta(days=7)).isoformat()
 row=conn.execute("SELECT score,momentum,risk FROM score_history WHERE symbol=? AND snap_date<=? ORDER BY snap_date DESC LIMIT 1",(symbol.upper(),wa)).fetchone()
 if row is None: return {}
 ds=round(_sf(a.get("overall") or a.get("deger"))-_sf(row[0]),1)
 dm=round(_sf(a.get("ivme"))-_sf(row[1]),1)
 dr=round(_sf(a.get("risk_score"))-_sf(row[2]),1)
 wc=[]
 if abs(ds)>=2: wc.append(f"Son 7 günde skor {'+'if ds>0 else ''}{ds:.0f}")
 if abs(dm)>=3: wc.append("Momentum güçlendi" if dm>0 else "Momentum zayıfladı")
 if abs(dr)>=2: wc.append("Risk azaldı" if dr<0 else "Risk arttı")
 if not wc: wc.append("Önemli bir değişiklik yok")
 return {"delta":{"score_7d":ds,"momentum_7d":dm,"risk_7d":dr},"what_changed":wc[:3]}
def watchlist_changes(symbols):
 try:
  from infra.storage import _get_conn
  conn=_get_conn(); wa=(date.today()-timedelta(days=7)).isoformat(); ch=[]
  for s in symbols:
   rows=conn.execute("SELECT snap_date,score FROM score_history WHERE symbol=? AND snap_date>=? ORDER BY snap_date ASC",(s.upper(),wa)).fetchall()
   if len(rows)<2: continue
   d=_sf(rows[-1][1])-_sf(rows[0][1])
   if abs(d)>=3: ch.append({"symbol":s,"delta":round(d,1),"text":f"{s}: {'+'if d>0 else ''}{d:.0f}"})
  return ch
 except: return []
def get_movers():
 try:
  from infra.storage import _get_conn
  conn=_get_conn(); today=date.today().isoformat(); wa=(date.today()-timedelta(days=7)).isoformat()
  rows=conn.execute("SELECT h1.symbol,h2.score-h1.score AS d,h2.score FROM score_history h1 JOIN score_history h2 ON h1.symbol=h2.symbol WHERE h1.snap_date=(SELECT MIN(snap_date) FROM score_history WHERE snap_date>=? AND symbol=h1.symbol) AND h2.snap_date=? ORDER BY d DESC",(wa,today)).fetchall()
  if not rows: return {"gainers":[],"losers":[]}
  return {"gainers":[{"symbol":r[0],"delta":round(r[1],1),"score":round(r[2],1)} for r in rows[:3] if r[1]>0],"losers":[{"symbol":r[0],"delta":round(r[1],1),"score":round(r[2],1)} for r in reversed(rows[-3:]) if r[1]<0]}
 except: return {"gainers":[],"losers":[]}

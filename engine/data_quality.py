from __future__ import annotations
import logging
from typing import Any
log = logging.getLogger("bistbull.data_quality")
_EXTREME = {"pe":{"low":-500,"high":500,"label":"Aşırı F/K"},"roe":{"low":-200,"high":200,"label":"Aşırı ROE"},"pb":{"low":0,"high":50,"label":"Aşırı PD/DD"}}
_MISSING_CRITICAL = ["pe","roe","net_income","revenue","market_cap"]
def assess_data_quality(metrics,scores_imputed=None):
 try: return _assess(metrics,scores_imputed or [])
 except Exception as e: log.warning(f"data_quality failed: {e}"); return {"grade":"U","anomalies":[],"missing_fields":[],"imputed_dimensions":[],"anomaly_count":0,"missing_count":0}
def build_decision_context(health,confidence,is_hype,scores_imputed=None):
 try: return _ctx(health,confidence,is_hype,scores_imputed or [])
 except: return {"reliability":"unknown","caveats":[]}
def _assess(m,imputed):
 anomalies,missing=[],[]
 for k,b in _EXTREME.items():
  v=m.get(k)
  if v is not None:
   try:
    v=float(v)
    if v<b["low"] or v>b["high"]: anomalies.append({"type":"extreme_value","field":k,"value":v,"label":b["label"]})
   except: pass
 for gf in ("revenue_growth","earnings_growth"):
  v=m.get(gf)
  if v is not None:
   try:
    if abs(float(v))>5.0: anomalies.append({"type":"growth_jump","field":gf,"value":v,"label":f"Şüpheli büyüme ({gf})"})
   except: pass
 for f in _MISSING_CRITICAL:
  if m.get(f) is None: missing.append(f)
 sev=len(anomalies)+len(missing)+len(imputed)
 grade="A" if sev==0 else "B" if sev<=2 else "C" if sev<=5 else "D"
 if anomalies or missing: log.info(f"data_quality: grade={grade} anomalies={len(anomalies)} missing={len(missing)} imputed={len(imputed)}")
 return {"grade":grade,"anomalies":anomalies,"missing_fields":missing,"imputed_dimensions":imputed,"anomaly_count":len(anomalies),"missing_count":len(missing)}
def _ctx(health,confidence,is_hype,imputed):
 caveats=[]
 if health.get("grade") in ("C","D"): caveats.append("Veri kalitesi düşük")
 if confidence<50: caveats.append("Güven skoru düşük")
 if is_hype: caveats.append("Hype tespit edildi")
 if len(imputed)>=3: caveats.append(f"{len(imputed)} boyut tahmini")
 g=health.get("grade","?")
 rel="high" if g=="A" and confidence>=70 else "medium" if g in ("A","B") and confidence>=50 else "low"
 return {"reliability":rel,"caveats":caveats}

from __future__ import annotations
import logging; from datetime import date; from typing import Optional
log = logging.getLogger("bistbull.valuation")
_DISCOUNT=0.38; _TG=0.04; _YRS=5
def build_valuation_layer(m,a):
 try: return _build(m,a)
 except Exception as e: log.warning(f"valuation failed: {e}"); return _empty()
def _sf(v,d=0):
 if v is None: return d
 try: return float(v)
 except: return d
def _clamp(v,lo,hi): return max(lo,min(hi,v))
def _build(m,a):
 price=_sf(m.get("price")); mc=_sf(m.get("market_cap")); rev=_sf(m.get("revenue")); ebitda=_sf(m.get("ebitda"))
 ni=_sf(m.get("net_income")); fcf=_sf(m.get("free_cf")); td=_sf(m.get("total_debt")); cash=_sf(m.get("cash"))
 nd=td-cash; sh=(mc/price) if mc and price>0 else None; pe=m.get("pe"); pb=m.get("pb")
 rg=m.get("revenue_growth"); nm=m.get("net_margin"); gfv=m.get("graham_fv")
 vi={"revenue":rev or None,"ebitda":ebitda or None,"net_income":ni or None,"free_cf":fcf or None,"net_debt":round(nd) if nd else None,"shares_outstanding":round(sh) if sh else None,"last_price":price or None,"market_cap":mc or None}
 gr=_clamp(_sf(rg,0.10),-0.3,1.0); ma=_clamp(_sf(nm,0.10),0.01,0.50)
 assumptions={"growth_rate":round(gr,4),"discount_rate":_DISCOUNT,"margin_assumption":round(ma,4),"method":"dcf" if fcf>0 or ni>0 else "graham" if gfv else "multiples"}
 val=_range(m,gr,ma,sh,nd); vc=_conf(m,val)
 vdh={k:("missing" if vi.get(k) is None else "ok") for k in ("revenue","ebitda","net_income","free_cf","net_debt","shares_outstanding")}
 vdc={"financial_period":f"{date.today().year} Q{max(1,(date.today().month-1)//3)}","market_data_date":date.today().isoformat(),"freshness":"daily"}
 vctx=_sector(m,a); vr=_risks(m)
 vs={"bull_case":"büyüme hızlanırsa","base_case":"mevcut trend devam ederse","risk_case":"büyüme yavaşlarsa"}
 return {"valuation":val,"valuation_confidence":vc,"valuation_assumptions":assumptions,"valuation_inputs":vi,"valuation_data_context":vdc,"valuation_data_health":vdh,"valuation_context":vctx,"valuation_risks":vr,"valuation_scenarios":vs}
def _dcf(cf,g,d):
 if d<=_TG: d=_TG+0.05
 tot=0.0; pcf=cf
 for y in range(1,_YRS+1): pcf*=(1+g); tot+=pcf/((1+d)**y)
 tv=pcf*(1+_TG)/(d-_TG); tot+=tv/((1+d)**_YRS); return tot
def _range(m,gr,ma,sh,nd):
 price=_sf(m.get("price")); fcf=_sf(m.get("free_cf")); ni=_sf(m.get("net_income")); rev=_sf(m.get("revenue")); gfv=_sf(m.get("graham_fv"))
 ev=None; meth="none"
 if fcf>0 and sh and sh>0: ev=_dcf(fcf,gr,_DISCOUNT); meth="dcf_fcf"
 if ev is None and ni>0 and sh and sh>0: ev=_dcf(ni*0.7,gr,_DISCOUNT); meth="dcf_earnings"
 if ev is None and rev>0 and ma>0 and sh and sh>0: ev=_dcf(rev*ma*0.7,gr,_DISCOUNT); meth="dcf_revenue"
 if ev is None or not sh or sh<=0:
  if gfv>0 and price>0: return {"bear_case":round(gfv*0.75,2),"base_case":round(gfv,2),"bull_case":round(gfv*1.25,2),"range":f"{gfv*0.75:.0f}–{gfv*1.25:.0f} TL","currency":m.get("currency","TRY"),"method":"graham","vs_price":round((gfv/price-1)*100,1)}
  return {"method":"unavailable"}
 eq=ev-nd; eq=max(eq,ev*0.1); b=eq/sh; return {"bear_case":round(b*0.6,2),"base_case":round(b,2),"bull_case":round(b*1.4,2),"range":f"{b*0.6:.0f}–{b*1.4:.0f} TL","currency":m.get("currency","TRY"),"method":meth,"vs_price":round((b/price-1)*100,1) if price>0 else None}
def _conf(m,val):
 meth=val.get("method","unavailable")
 if meth=="unavailable": return {"level":"low","reason":"yeterli veri yok"}
 sc=sum(1 for k in ("revenue","net_income","free_cf","ebitda") if m.get(k) is not None)
 if m.get("market_cap") and m.get("price"): sc+=1
 if meth=="dcf_fcf": sc+=2; r="FCF bazlı DCF"
 elif meth=="dcf_earnings": sc+=1; r="kâr bazlı DCF"
 else: r="Graham"
 return {"level":"high" if sc>=6 else "medium" if sc>=3 else "low","reason":r}
def _sector(m,a):
 ctx={}; pe=m.get("pe"); pb=m.get("pb")
 if pb is not None:
  try:
   if float(pb)<1.0: ctx["pb_note"]="defter değerinin altında"
   elif float(pb)>5.0: ctx["pb_note"]="defter değerinin çok üstünde"
  except: pass
 return ctx
def _risks(m):
 r=[]
 try:
  rg=_sf(m.get("revenue_growth"))
  if rg>0.30: r.append("büyüme sürdürülebilir olmayabilir")
 except: pass
 try:
  if _sf(m.get("net_margin"))<0.05: r.append("düşük marjlar baskı altında")
 except: pass
 try:
  if _sf(m.get("debt_equity"))>2.0: r.append("yüksek kaldıraç riski")
 except: pass
 if not r: r.append("makro koşullar değişirse varsayımlar geçersiz kalabilir")
 return r[:3]
def _empty():
 return {"valuation":{"method":"unavailable"},"valuation_confidence":{"level":"low","reason":"hesaplanamadı"},"valuation_assumptions":{},"valuation_inputs":{},"valuation_data_context":{},"valuation_data_health":{},"valuation_context":{},"valuation_risks":[],"valuation_scenarios":{}}

# ================================================================
# BISTBULL TERMINAL — ECONOMIC CALENDAR
# engine/calendar.py
# TÜM TARİHLER DOĞRULANMIŞ. Son doğrulama: 11 Nisan 2026
# ================================================================

from __future__ import annotations
import datetime as dt, logging
from typing import Optional
from dataclasses import dataclass, asdict

log = logging.getLogger("bistbull.calendar")

@dataclass
class EconEvent:
    date: str; time: str; title: str; country: str; importance: str
    expected_impact: str; description: str; flag: str = ""
    def to_dict(self):
        d = asdict(self)
        d["is_today"] = self.date == dt.date.today().isoformat()
        d["is_past"] = self.date < dt.date.today().isoformat()
        return d

RECURRING_EVENTS: list[EconEvent] = [
    # NİSAN 2026
    EconEvent("2026-04-22","14:00","TCMB Faiz Kararı","TR","high","unknown","Piyasa sabit tutulmasını bekliyor.","🇹🇷"),
    EconEvent("2026-04-29","21:00","Fed FOMC Faiz Kararı","US","high","unknown","Fed sabit bekleniyor. İran savaşı belirsizliği.","🇺🇸"),
    EconEvent("2026-04-30","14:45","ECB Faiz Kararı","EU","high","unknown","Artırım ihtimali %26 — İran enflasyon riski.","🇪🇺"),
    # MAYIS 2026
    EconEvent("2026-05-01","15:30","ABD Tarım Dışı İstihdam","US","high","unknown","ABD istihdam — dolar ve EM akışını etkiler.","🇺🇸"),
    EconEvent("2026-05-04","10:00","Türkiye TÜFE (Nisan)","TR","high","unknown","Enflasyon trendi — TCMB Haziran hamlesini belirler.","🇹🇷"),
    EconEvent("2026-05-12","15:30","ABD TÜFE (Nisan)","US","high","unknown","ABD enflasyonu — Fed beklentilerini şekillendirir.","🇺🇸"),
    # HAZİRAN 2026
    EconEvent("2026-06-03","10:00","Türkiye TÜFE (Mayıs)","TR","high","unknown","Dezenflasyon trendi sürüyor mu?","🇹🇷"),
    EconEvent("2026-06-05","15:30","ABD Tarım Dışı İstihdam","US","high","unknown","İstihdam — risk iştahı barometresi.","🇺🇸"),
    EconEvent("2026-06-11","14:00","TCMB Faiz Kararı","TR","high","unknown","İndirim ihtimali tartışılacak.","🇹🇷"),
    EconEvent("2026-06-11","14:45","ECB Faiz Kararı","EU","high","unknown","Artırım en olası tarih.","🇪🇺"),
    EconEvent("2026-06-17","21:00","Fed FOMC Faiz Kararı","US","high","unknown","Fed Haziran + dot plot.","🇺🇸"),
    # TEMMUZ 2026
    EconEvent("2026-07-23","14:00","TCMB Faiz Kararı","TR","high","unknown","Yaz öncesi kritik toplantı.","🇹🇷"),
    EconEvent("2026-07-23","14:45","ECB Faiz Kararı","EU","medium","unknown","ECB Temmuz.","🇪🇺"),
    EconEvent("2026-07-29","21:00","Fed FOMC Faiz Kararı","US","high","unknown","Fed Temmuz.","🇺🇸"),
    # EYLÜL 2026
    EconEvent("2026-09-10","14:00","TCMB Faiz Kararı","TR","high","unknown","TCMB Eylül.","🇹🇷"),
    EconEvent("2026-09-10","14:45","ECB Faiz Kararı","EU","medium","unknown","ECB Eylül.","🇪🇺"),
    EconEvent("2026-09-16","21:00","Fed FOMC Faiz Kararı","US","high","unknown","Fed Eylül.","🇺🇸"),
    # EKİM 2026
    EconEvent("2026-10-22","14:00","TCMB Faiz Kararı","TR","high","unknown","TCMB Ekim.","🇹🇷"),
    EconEvent("2026-10-28","21:00","Fed FOMC Faiz Kararı","US","high","unknown","Fed Ekim.","🇺🇸"),
    EconEvent("2026-10-29","14:45","ECB Faiz Kararı","EU","medium","unknown","ECB Ekim.","🇪🇺"),
    # ARALIK 2026
    EconEvent("2026-12-09","21:00","Fed FOMC Faiz Kararı","US","high","unknown","Fed yılsonu + dot plot.","🇺🇸"),
    EconEvent("2026-12-10","14:00","TCMB Faiz Kararı","TR","high","unknown","TCMB yılsonu.","🇹🇷"),
    EconEvent("2026-12-17","14:45","ECB Faiz Kararı","EU","medium","unknown","ECB yılsonu.","🇪🇺"),
]

MANUAL_EVENTS: list[EconEvent] = []

def get_all_events():
    all_ev = RECURRING_EVENTS + MANUAL_EVENTS
    all_ev.sort(key=lambda e: (e.date, e.time)); return all_ev

def get_this_week_events():
    today = dt.date.today(); end = today + dt.timedelta(days=7)
    return [e for e in get_all_events() if today.isoformat() <= e.date <= end.isoformat()]

def get_next_important_event():
    today_s = dt.date.today().isoformat()
    for e in get_all_events():
        if e.date >= today_s and e.importance == "high": return e
    return None

def get_upcoming_events(days=14):
    today = dt.date.today(); end = today + dt.timedelta(days=days)
    return [e for e in get_all_events() if today.isoformat() <= e.date <= end.isoformat()]

def format_event_for_action(event):
    days = {0:"Pazartesi",1:"Salı",2:"Çarşamba",3:"Perşembe",4:"Cuma",5:"Cumartesi",6:"Pazar"}
    try:
        d = dt.datetime.strptime(event.date, "%Y-%m-%d")
        return f"{days.get(d.weekday(),'')} {event.title}"
    except: return event.title

def get_calendar_summary():
    week = get_this_week_events(); upcoming = get_upcoming_events(14)
    nxt = get_next_important_event()
    return {
        "this_week": [e.to_dict() for e in week],
        "upcoming_14d": [e.to_dict() for e in upcoming],
        "next_important": nxt.to_dict() if nxt else None,
        "next_important_label": format_event_for_action(nxt) if nxt else None,
        "total_events": len(get_all_events()), "as_of": dt.date.today().isoformat(),
    }

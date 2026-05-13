# ================================================================
# BISTBULL TERMINAL — KAP DISCLOSURE FEED CLIENT
# data/kap_client.py
#
# Thin wrapper around the `pykap` library — see audit recon for the
# rationale (kap-client v1.1.1 fetch_companies() returned 0 in our env;
# pykap v0.2.0 worked first try).
#
# Exposes two operations we actually use:
#   list_disclosures(ticker, days)         — recent disclosures for one ticker
#   list_expected_disclosures(ticker)      — forward-looking calendar entries
#
# Both return a normalized dict shape (see DisclosureRecord) so the rest
# of the pipeline doesn't depend on pykap field names.
# ================================================================

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, asdict
from typing import Any, Optional

log = logging.getLogger("bistbull.kap_client")

# Type taxonomy reused across the pipeline. Subset of pykap's
# VALID_DISCLOSURE_TYPES, narrowed to what we care about.
DISCLOSURE_TYPE_FINANCIAL = "FR"        # Finansal Rapor (balance sheet)
DISCLOSURE_TYPE_OPERATING = "FAR"        # Faaliyet Raporu (operating review)
DISCLOSURE_TYPE_SPECIAL = "ODA"          # Özel Durum Açıklaması

# Subjects we treat as "balance sheet released" for cache invalidation.
# Other subject lines (e.g. "Sorumluluk Beyanı") get ignored at that
# downstream gate even when the disclosure_type is FR.
FINANCIAL_REPORT_SUBJECTS = {
    "finansal rapor",
    "konsolide finansal tablolar",
    "konsolide olmayan finansal tablolar",
}


@dataclass
class DisclosureRecord:
    """Normalized KAP disclosure event.

    Field types intentionally simple (str/int/None) so this round-trips
    cleanly through JSON / Redis / SQLite without converters.
    """
    disclosure_index: int            # monotonic KAP id — primary key + dedup
    ticker: str                      # BIST ticker (uppercased, dot-stripped)
    kap_title: str                   # company display name
    subject: str                     # human-readable disclosure subject
    disclosure_type: str             # FR / FAR / ODA / ...
    disclosure_class: str            # FR / ... (often same as type)
    publish_date: str                # ISO8601 UTC
    publish_date_raw: str            # raw "22.04.2026 18:30:24" from KAP
    rule_type: Optional[str]         # "Yıllık" / "3 Aylık" / "6 Aylık" / "9 Aylık"
    period: Optional[int]            # 1..4
    year: Optional[int]
    attachment_count: int
    is_late: bool
    url: Optional[str]               # KAP detail page

    def is_financial_report(self) -> bool:
        """True iff this disclosure is a quarterly/annual balance sheet
        release — the kind that should invalidate scoring caches."""
        if self.disclosure_type != DISCLOSURE_TYPE_FINANCIAL:
            return False
        subj = (self.subject or "").lower().strip()
        return any(s in subj for s in FINANCIAL_REPORT_SUBJECTS)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_kap_datetime(raw: str) -> Optional[str]:
    """Convert KAP's '22.04.2026 18:30:24' (Europe/Istanbul) into ISO8601
    UTC. Returns None when unparseable so the rest of the pipeline can
    decide how to handle it."""
    if not raw:
        return None
    try:
        # KAP times are Europe/Istanbul (UTC+3, no DST since 2016).
        local = _dt.datetime.strptime(raw.strip(), "%d.%m.%Y %H:%M:%S")
        tz = _dt.timezone(_dt.timedelta(hours=3))
        return local.replace(tzinfo=tz).astimezone(_dt.timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _normalize_disclosure(raw: dict, fallback_ticker: Optional[str] = None) -> Optional[DisclosureRecord]:
    """Map pykap's dict → DisclosureRecord. Returns None when the row
    can't be normalized (missing index / unparseable date / blank ticker)."""
    if not isinstance(raw, dict):
        return None
    try:
        idx = int(raw.get("disclosureIndex") or 0)
    except (TypeError, ValueError):
        return None
    if idx <= 0:
        return None

    pub_iso = _parse_kap_datetime(str(raw.get("publishDate") or ""))
    if pub_iso is None:
        return None

    # KAP's stockCodes is comma-separated for filings that span multiple
    # tickers (mostly holding/affiliate releases). For our pipeline we
    # use the first ticker as primary — the dispatcher fans out further
    # to related tickers if needed.
    stock_codes = raw.get("stockCodes") or ""
    if not stock_codes and fallback_ticker:
        ticker = fallback_ticker.upper().replace(".IS", "")
    else:
        ticker = str(stock_codes).split(",")[0].strip().upper().replace(".IS", "")
    if not ticker:
        return None

    return DisclosureRecord(
        disclosure_index=idx,
        ticker=ticker,
        kap_title=str(raw.get("kapTitle") or "").strip(),
        subject=str(raw.get("subject") or "").strip(),
        disclosure_type=str(raw.get("disclosureType") or "").strip().upper(),
        disclosure_class=str(raw.get("disclosureClass") or "").strip().upper(),
        publish_date=pub_iso,
        publish_date_raw=str(raw.get("publishDate") or "").strip(),
        rule_type=(str(raw["ruleType"]).strip() if raw.get("ruleType") else None),
        period=(int(raw["period"]) if raw.get("period") is not None else None),
        year=(int(raw["year"]) if raw.get("year") is not None else None),
        attachment_count=int(raw.get("attachmentCount") or 0),
        is_late=bool(raw.get("isLate") or False),
        url=None,  # pykap doesn't surface a detail URL; we synthesize it later if needed
    )


# ── Public API ─────────────────────────────────────────────────────


def bist_company_tickers() -> list[str]:
    """Cached company ticker list from KAP. pykap caches internally so
    repeat calls are cheap."""
    try:
        from pykap.bist_company_list import bist_company_list
        cos = bist_company_list() or []
        return [str(t).upper().strip() for t in cos if t]
    except Exception as exc:
        log.warning("KAP company list fetch failed: %r", exc)
        return []


def list_disclosures(
    ticker: str,
    *,
    days: int = 7,
    disclosure_type: str = DISCLOSURE_TYPE_FINANCIAL,
) -> list[DisclosureRecord]:
    """Fetch recent disclosures for a single ticker over the past `days`.

    Errors are logged and swallowed — returns [] so a single broken
    ticker can't kill the polling loop.
    """
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        return []
    try:
        from pykap.bist import BISTCompany
        comp = BISTCompany(ticker=sym)
        end = _dt.date.today()
        start = end - _dt.timedelta(days=max(1, days))
        rows = comp.get_historical_disclosure_list(
            fromdate=start, todate=end,
            disclosure_type=disclosure_type,
        ) or []
    except Exception as exc:
        log.debug("KAP list_disclosures %s: %r", sym, exc)
        return []

    out: list[DisclosureRecord] = []
    for row in rows:
        rec = _normalize_disclosure(row, fallback_ticker=sym)
        if rec is not None:
            out.append(rec)
    # Sort newest first by disclosure_index (monotonic)
    out.sort(key=lambda r: r.disclosure_index, reverse=True)
    return out


# ── Operator signal classification (Tahtacı PR A1) ─────────────────
#
# KAP "Özel Durum Açıklaması" tipinin subject metinleri serbest, ama
# operatör imzası taşıyan iyi tanımlı bir alt-küme var. Tahtacı (BIST
# operatör) tracking için bu sinyalleri kategorize edip BullWatch'a
# besleriz.
#
# Tag → açıklama → tipik skor boost önerisi (engine.bullwatch_kap_boost'ta uygulanır):
#   INSIDER          Pay Alım/Satım Bildirimi, Pay Sahipliği Bildirimi  → +15
#   KAP_ALERT        Olağan Dışı Fiyat ve Miktar Hareketleri              → +10
#   BUYBACK          Pay Geri Alım Programı                                → +12
#   MNA              Finansal Duran Varlık Edinimi, Birleşme, Devralma   → +12
#   CAPITAL_CHANGE   Sermaye Artırımı (bedelsiz pozitif, bedelli karışık) → +5
#   MGMT_CHANGE      Yönetim Kurulu Kararı / Yönetici Değişikliği          → +3
#   GENERAL          Özel Durum Açıklaması (Genel) — serbest metin         → 0 (sınıflandırma yok)
OPERATOR_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "INSIDER":        ("pay alım satım bildirim", "pay sahipliği bildirim",
                       "değişen pay sahipliği", "yönetim kurulu üyesi pay"),
    "KAP_ALERT":      ("olağan dışı fiyat", "olağandışı fiyat",
                       "olağan dışı miktar"),
    "BUYBACK":        ("pay geri alım", "geri alım program", "pay alımı programı"),
    "MNA":            ("finansal duran varlık edinim", "birleşme",
                       "devralma", "bağlı ortaklık devri", "satın alma"),
    "CAPITAL_CHANGE": ("sermaye artırım", "bedelsiz sermaye",
                       "bedelli sermaye", "sermaye azaltım"),
    "MGMT_CHANGE":    ("yönetim kurulu", "yönetici atama",
                       "genel müdür", "yönetici değişiklik"),
}


def classify_operator_signal(subject: str) -> Optional[str]:
    """Map a free-text KAP subject to one of the OPERATOR_SIGNAL_PATTERNS
    tags, or None if no operator-relevant pattern matches.

    Used by:
      - engine.kap_dispatcher to decide AI queue priority
      - engine.bullwatch_kap_boost to compute the per-ticker score lift
    """
    if not subject:
        return None
    s = subject.lower().strip()
    for tag, needles in OPERATOR_SIGNAL_PATTERNS.items():
        for needle in needles:
            if needle in s:
                return tag
    return None


def list_general_announcements(
    ticker: str,
    *,
    days: int = 14,
) -> list[DisclosureRecord]:
    """Pull ALL Özel Durum (ODA) announcements for one ticker over the
    past `days`, bypassing pykap's whitelist-restricted subject filter.

    Implementation: call the same `/api/disclosure/members/byCriteria`
    endpoint pykap uses internally, but with no subject filter and
    `disclosureClass='ODA'`. Discovered via pykap source code reading
    + KAP recon.

    Errors are logged and swallowed — empty list on any failure path
    so the dispatcher never crashes on a flaky KAP server.
    """
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        return []
    try:
        from pykap.bist import BISTCompany
        import requests
        import datetime as _dt
        comp = BISTCompany(ticker=sym)
        end = _dt.date.today()
        start = end - _dt.timedelta(days=max(1, days))
        body = {
            "fromDate":   str(start),
            "toDate":     str(end),
            "disclosureClass":  "ODA",
            "subjectList":      [],
            "mkkMemberOidList": [comp.company_id],
            "inactiveMkkMemberOidList": [],
            "bdkMemberOidList": [],
            "fromSrc":          False,
            "disclosureIndexList": [],
        }
        r = requests.post(
            "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria",
            json=body, timeout=20,
        )
        r.raise_for_status()
        rows = r.json() or []
    except Exception as exc:
        log.debug("KAP general announcements %s: %r", sym, exc)
        return []

    out: list[DisclosureRecord] = []
    for row in rows:
        rec = _normalize_disclosure(row, fallback_ticker=sym)
        if rec is not None:
            out.append(rec)
    out.sort(key=lambda r: r.disclosure_index, reverse=True)
    return out


def list_expected_disclosures(ticker: str) -> list[dict[str, Any]]:
    """Forward-looking calendar — what disclosures are expected for a
    ticker over the upcoming reporting periods.

    Cached in Redis with a 12-hour TTL. KAP's expected-disclosure schedule
    updates at most once a day (when the company publishes its yearly
    plan), so re-fetching on every Bilançolar page open was 30+ pykap
    HTTP calls per visit. The cache turns the second-and-later visits
    into single-Redis-roundtrip operations.
    """
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        return []
    # Try Redis cache first
    cache_key = f"bb:kap:calendar:{sym}"
    try:
        from core import redis_client
        import json as _json
        client = redis_client.get_client()
        if client is not None:
            raw = client.get(cache_key)
            if raw:
                try:
                    return _json.loads(raw)
                except (_json.JSONDecodeError, TypeError):
                    pass  # corrupted cache entry → re-fetch
    except Exception as exc:
        log.debug("KAP calendar cache read %s: %r", sym, exc)
    # Cache miss — fetch from KAP and persist
    try:
        from pykap.bist import BISTCompany
        comp = BISTCompany(ticker=sym)
        rows = comp.get_expected_disclosure_list(count=20) or []
    except Exception as exc:
        log.debug("KAP list_expected_disclosures %s: %r", sym, exc)
        return []
    rows = list(rows)
    # Write through to cache (12h TTL — calendar updates yearly, but we
    # want a buffer for occasional plan revisions).
    try:
        from core import redis_client
        import json as _json
        client = redis_client.get_client()
        if client is not None and rows:
            client.set(
                cache_key,
                _json.dumps(rows, ensure_ascii=False, default=str),
                ex=12 * 3600,
            )
    except Exception as exc:
        log.debug("KAP calendar cache write %s: %r", sym, exc)
    return rows

# ================================================================
# BISTBULL TERMINAL — KAP DISPATCHER
# engine/kap_dispatcher.py
#
# Side-effect router for newly-detected disclosure events. Called by
# engine.kap_feed once per new DisclosureRecord. Side effects:
#
#   1. Cache invalidation (Plan C) — when a balance sheet drops,
#      raw_cache + analysis_cache + bullwatch metrics-cache entries
#      for that ticker are removed so the next scoring read picks up
#      the fresh data.
#
#   2. AI analysis queue (wire is here; processor lands in Faz 3).
#
#   3. Snapshot-store soft-mark (the cold BullWatch snapshot itself
#      still lives until its refresh cycle, but we drop the in-memory
#      mirror in api/bullwatch._CACHE so the next request reads from
#      Redis — and Redis is now stale by intention).
#
# All hooks are best-effort: any individual failure is logged and
# swallowed. The feed loop must NEVER stop because invalidation
# blew up on one cache.
# ================================================================

from __future__ import annotations

import logging
from typing import Optional

from data.kap_client import DisclosureRecord

log = logging.getLogger("bistbull.kap_dispatcher")


def dispatch_new_disclosure(rec: DisclosureRecord) -> None:
    """Entry point: route side effects for one fresh disclosure event."""
    log.info(
        "KAP dispatch: %s [%s/%s] %s",
        rec.ticker, rec.disclosure_type, rec.rule_type, rec.subject,
    )
    # Branch 1 — Financial report (balance sheet drop)
    if rec.is_financial_report():
        _invalidate_caches_for_ticker(rec.ticker)
        _queue_ai_analysis(rec)
        _capture_reaction_baseline(rec)
        return
    # Branch 2 — Operator signal (insider buy, KAP warning, M&A, buyback, …)
    # These don't invalidate fundamental caches (no new balance sheet),
    # but they ARE the tahtacı imzaları BullWatch should boost on, and
    # they're juicy AI analysis fodder.
    from data.kap_client import classify_operator_signal
    op_tag = classify_operator_signal(rec.subject)
    if op_tag is not None:
        log.info(
            "KAP operator signal: %s [%s] %s",
            rec.ticker, op_tag, rec.subject,
        )
        # AI analyzes operator signals too — the prompt template
        # already handles non-bilanço subjects gracefully.
        _queue_ai_analysis(rec)


# ── Faz 4: Reaction tracker baseline ────────────────────────────────


def _capture_reaction_baseline(rec: DisclosureRecord) -> None:
    """Snapshot the day's close right after detection so the daily
    reaction refresh has a baseline. Runs in a daemon thread to avoid
    blocking the feed loop on borsapy."""
    import threading
    def _go():
        try:
            from engine.kap_reactions import capture_reference_price
            px = capture_reference_price(
                rec.ticker, rec.disclosure_index, rec.publish_date,
            )
            if px is not None:
                log.info(
                    "KAP reaction baseline: %s/%s @ %.2f",
                    rec.ticker, rec.disclosure_index, px,
                )
        except Exception as exc:
            log.debug("reaction baseline failed for %s/%s: %r",
                      rec.ticker, rec.disclosure_index, exc)
    threading.Thread(
        target=_go, daemon=True,
        name=f"kap-react-{rec.disclosure_index}",
    ).start()


# ── Plan C: cache invalidation ─────────────────────────────────────


def _invalidate_caches_for_ticker(ticker: str) -> list[str]:
    """Drop every cached scoring artifact for `ticker` so the next read
    triggers a fresh borsapy fetch — which now sees the just-released
    balance sheet.

    Returns the list of cache layers that were touched (for telemetry /
    testing). Empty list means nothing was found to invalidate.

    Audit fix (Stage 3): now covers history_cache + uses the thread-safe
    _cache_update helper for the api.bullwatch in-mem mirror so concurrent
    scan + invalidation can't see partial state.
    """
    sym = (ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        return []

    touched: list[str] = []

    # Raw + analysis L1/L2 caches (engine/analysis.py path)
    try:
        from core.cache import raw_cache, analysis_cache, tech_cache, history_cache
        for cache, name in (
            (raw_cache, "raw_cache"),
            (analysis_cache, "analysis_cache"),
            (tech_cache, "tech_cache"),
            # history_cache holds daily delta snapshots — Stage 3 audit fix
            (history_cache, "history_cache"),
        ):
            try:
                # SafeCache supports both .delete(key) and .pop(key, None).
                if hasattr(cache, "delete"):
                    cache.delete(sym)
                    touched.append(name)
                elif hasattr(cache, "pop"):
                    cache.pop(sym, None)
                    touched.append(name)
            except Exception as exc:
                log.debug("invalidate %s on %s: %r", sym, name, exc)
    except Exception as exc:
        log.warning("invalidate core.cache import failed: %r", exc)

    # BullWatch's separate Redis-backed metrics cache (12h fresh /
    # 7d stale). Same ticker key.
    try:
        from data import bullwatch_cache
        if hasattr(bullwatch_cache, "invalidate"):
            bullwatch_cache.invalidate(sym)
            touched.append("bullwatch_cache")
        else:
            # Fall back to raw Redis delete on the well-known key.
            from core import redis_client
            client = redis_client.get_client()
            if client is not None:
                client.delete(f"bullwatch:metrics:v3:{sym}")
                touched.append("bullwatch_cache_raw")
    except Exception as exc:
        log.debug("invalidate bullwatch_cache %s: %r", sym, exc)

    # BullWatch in-memory mirror (api/bullwatch._CACHE) — drop the items
    # entry only; the snapshot store keeps the cold copy until its own
    # refresh cycle. We don't kill the snapshot directly because the UI
    # would blank; instead we let the next refresh loop write the fresh
    # snapshot atomically.
    try:
        from api import bullwatch as _bw
        # Stage 3 audit fix: use thread-safe snapshot for read, and
        # thread-safe _cache_update for write. Eski direct-dict access
        # concurrent scan ile yarış kondisyonu yaratabiliyordu.
        snap = _bw._cache_snapshot() if hasattr(_bw, "_cache_snapshot") else _bw._CACHE
        items = (snap.get("items") or {}).get("items") or []
        if any((it.get("symbol") or "").upper() == sym for it in items):
            if hasattr(_bw, "_cache_update"):
                _bw._cache_update(stale_after=0.0)
            else:
                _bw._CACHE["stale_after"] = 0.0
            touched.append("bullwatch_inmem_mirror")
    except Exception as exc:
        log.debug("invalidate api.bullwatch mirror %s: %r", sym, exc)

    log.info("Plan C: invalidated %d cache layers for %s (%s)",
             len(touched), sym, ", ".join(touched) if touched else "—")
    return touched


# ── Faz 3: AI analysis pipeline ─────────────────────────────────────


def _queue_ai_analysis(rec: DisclosureRecord) -> None:
    """Schedule an AI analysis for a freshly-detected balance sheet.

    Runs in a daemon thread so the feed loop never blocks waiting for
    Grok. Failures are logged; the disclosure stays in the DB without
    an `ai_summary` and the next manual /api/kap/disclosure/{idx}/analyze
    POST can retry on demand.
    """
    import threading
    t = threading.Thread(
        target=_run_ai_analysis,
        args=(rec,),
        daemon=True,
        name=f"kap-ai-{rec.disclosure_index}",
    )
    t.start()
    log.info(
        "KAP AI: queued for %s/%s",
        rec.ticker, rec.disclosure_index,
    )


def _run_ai_analysis(rec: DisclosureRecord) -> None:
    """Actually call the AI and persist the result. Sync; expected to
    run in a daemon thread launched by _queue_ai_analysis."""
    try:
        from ai.service import generate_kap_disclosure_analysis
        from infra import kap_storage

        # Pull fresh metrics + scoring for richer context. Plan C just
        # invalidated the caches for this ticker, so analyze_symbol will
        # re-fetch the new balance sheet.
        metrics: dict = {}
        analysis: dict = {}
        try:
            from engine.analysis import analyze_symbol
            analysis = analyze_symbol(rec.ticker) or {}
            metrics = analysis.get("metrics") or {}
        except Exception as exc:
            log.debug("KAP AI %s: analyze_symbol unavailable: %r", rec.ticker, exc)

        text = generate_kap_disclosure_analysis(
            rec.to_dict(), metrics=metrics, analysis=analysis,
        )
        if not text:
            log.info(
                "KAP AI: no analysis produced for %s/%s",
                rec.ticker, rec.disclosure_index,
            )
            return
        kap_storage.save_ai_summary(rec.disclosure_index, text)
        log.info(
            "KAP AI: persisted analysis for %s/%s (%d chars)",
            rec.ticker, rec.disclosure_index, len(text),
        )
    except Exception as exc:
        log.warning(
            "KAP AI: pipeline failed for %s/%s: %r",
            rec.ticker, rec.disclosure_index, exc,
        )

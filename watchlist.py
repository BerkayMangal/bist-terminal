# ================================================================
# BISTBULL TERMINAL — WATCHLIST (Phase 7)
# engine/watchlist.py
#
# Watchlist CRUD with enrichment from analysis cache.
# No AI, no new scoring, pure data assembly.
# ================================================================

from __future__ import annotations

import logging
from typing import Optional

from config import UNIVERSE
from infra.storage import watchlist_add, watchlist_remove, watchlist_list

log = logging.getLogger("bistbull.watchlist")

# Valid symbols: UNIVERSE tickers (bare, without .IS)
_VALID_SYMBOLS = {t.upper() for t in UNIVERSE}


def validate_symbol(symbol: str) -> Optional[str]:
    """Normalize and validate a symbol. Returns uppercase ticker or None."""
    s = symbol.upper().replace(".IS", "").strip()
    if s in _VALID_SYMBOLS:
        return s
    return None


def add(user_id: str, symbol: str) -> dict:
    """Add symbol to watchlist. Returns status dict."""
    clean = validate_symbol(symbol)
    if not clean:
        return {"ok": False, "error": f"Gecersiz sembol: {symbol}"}
    added = watchlist_add(user_id, clean)
    if added:
        return {"ok": True, "symbol": clean, "action": "added"}
    return {"ok": True, "symbol": clean, "action": "already_exists"}


def remove(user_id: str, symbol: str) -> dict:
    """Remove symbol from watchlist. Returns status dict."""
    clean = validate_symbol(symbol)
    if not clean:
        return {"ok": False, "error": f"Gecersiz sembol: {symbol}"}
    removed = watchlist_remove(user_id, clean)
    if removed:
        return {"ok": True, "symbol": clean, "action": "removed"}
    return {"ok": True, "symbol": clean, "action": "not_found"}


def get_symbols(user_id: str) -> list[str]:
    """Get bare symbol list for user."""
    items = watchlist_list(user_id)
    return [it["symbol"] for it in items]


def get_enriched(user_id: str, analysis_cache, cross_signals: list[dict]) -> list[dict]:
    """Get watchlist with current analysis + signal data.

    Args:
        user_id: user identifier
        analysis_cache: SafeCache with analysis results keyed by symbol (.IS suffix)
        cross_signals: latest enriched cross signals from signal_engine

    Returns:
        List of enriched watchlist items with current scores, signals, explanation summary.
    """
    symbols = get_symbols(user_id)
    if not symbols:
        return []

    # Index cross signals by ticker
    sig_by_ticker: dict[str, list[dict]] = {}
    for sig in cross_signals:
        t = sig.get("ticker", "")
        sig_by_ticker.setdefault(t, []).append(sig)

    result = []
    for sym in symbols:
        full_sym = sym + ".IS"
        analysis = analysis_cache.get(full_sym) if analysis_cache else None

        item = {"symbol": sym, "has_data": analysis is not None}

        if analysis:
            item["overall"] = analysis.get("overall")
            item["confidence"] = analysis.get("confidence")
            item["fa_score"] = analysis.get("fa_score")
            item["ivme"] = analysis.get("ivme")
            item["entry_label"] = analysis.get("entry_label")
            item["decision"] = analysis.get("decision")
            item["risk_score"] = analysis.get("risk_score")
            item["style"] = analysis.get("style")
            item["price"] = analysis.get("metrics", {}).get("price")
            item["pe"] = analysis.get("metrics", {}).get("pe")

            # Explanation summary
            exp = analysis.get("explanation")
            if exp:
                item["summary"] = exp.get("summary", "")
                item["top_positive"] = [d.get("name", "") for d in exp.get("top_positive_drivers", [])[:2]]
                item["top_negative"] = [d.get("name", "") for d in exp.get("top_negative_drivers", [])[:2]]
            else:
                item["summary"] = ""
                item["top_positive"] = analysis.get("positives", [])[:2]
                item["top_negative"] = analysis.get("negatives", [])[:2]
        else:
            item["overall"] = None
            item["summary"] = "Analiz verisi henuz mevcut degil."

        # Active signals
        sigs = sig_by_ticker.get(sym, [])
        if sigs:
            item["signals"] = [
                {
                    "signal": s.get("signal"),
                    "signal_quality": s.get("signal_quality"),
                    "signal_confidence": s.get("signal_confidence"),
                    "stars": s.get("stars"),
                }
                for s in sigs[:5]
            ]
        else:
            item["signals"] = []

        result.append(item)

    return result

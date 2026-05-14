# ================================================================
# BISTBULL TERMINAL — PORTFOLIO ENDPOINTS
# api/portfolio.py
#
# Açık pozisyonlar + exit signals + close action.
#
#   POST /api/portfolio/positions          yeni pozisyon aç
#   GET  /api/portfolio/positions          açık + signal
#   POST /api/portfolio/positions/{id}/close  manuel kapat
#   GET  /api/portfolio/positions/{id}     tek pozisyon (signal dahil)
#   GET  /api/portfolio/history            kapalı pozisyonlar
#   GET  /api/portfolio/stats              counts + win rate
# ================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.response_envelope import success, error

log = logging.getLogger("bistbull.portfolio_api")
router = APIRouter()


class OpenPositionRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=12)
    entry_price: float = Field(..., gt=0)
    lot: float = Field(..., gt=0)
    notes: Optional[str] = Field(default=None, max_length=500)
    stop_loss_pct: Optional[float] = Field(default=-8.0, ge=-50.0, le=0.0)
    take_profit_pct: Optional[float] = Field(default=15.0, ge=0.0, le=200.0)


class ClosePositionRequest(BaseModel):
    exit_price: float = Field(..., gt=0)
    exit_reason: Optional[str] = Field(default=None, max_length=200)


def _current_bw_items() -> dict[str, dict[str, Any]]:
    """Live BullWatch items, keyed by upper-case symbol."""
    out: dict[str, dict[str, Any]] = {}
    try:
        from api.bullwatch import _CACHE, _read_snapshot_payload
        items = ((_CACHE.get("items") or {}).get("items")) or []
        if not items:
            snap = _read_snapshot_payload(limit=500)
            items = (snap or {}).get("items") or []
        for it in items:
            sym = (it.get("symbol") or "").upper()
            if sym:
                out[sym] = it
    except Exception as exc:
        log.debug("bw items lookup failed: %r", exc)
    return out


def _current_prices(
    tickers: list[str],
    *,
    timeout_per_ticker: float = 3.0,
    max_workers: int = 4,
) -> dict[str, float]:
    """Cheap last-price fetch via borsapy fast_info. PARALLEL — eski
    serial loop her ticker için 3-10s sürüyordu ve /api/portfolio/positions
    endpoint'inin client tarafında timeout etmesine neden oluyordu.

    Per-ticker timeout: borsapy bazen yanıt vermez; 3s'de kessin.
    """
    out: dict[str, float] = {}
    if not tickers:
        return out
    try:
        import borsapy as bp
    except Exception:
        return out
    syms = [
        (t or "").upper().strip().replace(".IS", "")
        for t in tickers if t
    ]
    syms = [s for s in syms if s]
    if not syms:
        return out

    def _fetch_one(sym: str) -> tuple[str, Optional[float]]:
        try:
            tk = bp.Ticker(sym)
            fi = tk.fast_info
            lp = getattr(fi, "last_price", None)
            return sym, float(lp) if lp is not None else None
        except Exception:
            return sym, None

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(max_workers, len(syms))) as pool:
        futures = {pool.submit(_fetch_one, s): s for s in syms}
        for fut in as_completed(futures, timeout=timeout_per_ticker * 2):
            try:
                sym, price = fut.result(timeout=timeout_per_ticker)
                if price is not None:
                    out[sym] = price
            except Exception:
                continue
    return out


@router.post("/api/portfolio/positions")
async def api_portfolio_open(req: OpenPositionRequest):
    """Yeni pozisyon aç. Eğer ticker o anda BullWatch listesindeyse,
    score/zone/pattern/kap/ownership snapshot'ı da kaydedilir — exit
    signal engine bu baseline'a karşı kontrol yapar."""
    from infra import portfolio_storage
    sym = (req.ticker or "").upper().strip().replace(".IS", "")
    if not sym:
        return error("empty ticker", status_code=400)

    # Snapshot BullWatch context if available
    bw_items = _current_bw_items()
    bw_item = bw_items.get(sym)
    components = (bw_item or {}).get("components") or {}

    def _go():
        return portfolio_storage.open_position(
            ticker=sym,
            entry_price=req.entry_price,
            lot=req.lot,
            notes=req.notes,
            score_at_entry=(bw_item or {}).get("score") if bw_item else None,
            zone_at_entry=(bw_item or {}).get("zone") if bw_item else None,
            pattern_at_entry=(bw_item or {}).get("pattern") if bw_item else None,
            kap_at_entry=components.get("kap_activity") if bw_item else None,
            own_at_entry=components.get("ownership") if bw_item else None,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct,
        )

    pos = await asyncio.to_thread(_go)
    if not pos:
        return error("position open failed", status_code=500)
    return success({"position": pos},
                   extra_meta={"endpoint": "portfolio.open"})


@router.get("/api/portfolio/positions")
async def api_portfolio_list(refresh_prices: bool = False):
    """Açık pozisyonlar + her biri için güncel exit signal.

    refresh_prices DEFAULT FALSE (audit fix) — borsapy fiyat fetch
    network'e bağlı bekletiyordu, kullanıcı UI'de "+ Aldım sonra
    portföyde gözükmedi" diyordu. Artık list call lokalde anında
    döner; price refresh ayrı endpoint'te (POST .../refresh-prices).
    """
    from infra import portfolio_storage
    from engine.portfolio_signals import compute_signals_for_open_positions

    positions = await asyncio.to_thread(portfolio_storage.get_open, 200)
    items = _current_bw_items()
    prices: dict[str, float] = {}
    # Cheap path: only use prices from live BullWatch cache (no extra
    # network fetch). User can hit explicit refresh-prices endpoint
    # if they want fresh live prices.
    for sym, it in items.items():
        lp = (it.get("metrics") or {}).get("last_price")
        if lp is not None:
            try:
                prices[sym] = float(lp)
            except (TypeError, ValueError):
                pass
    # Optional: explicit refresh_prices=true → do the slow borsapy fetch
    if refresh_prices and positions:
        tickers = list({(p.get("ticker") or "").upper() for p in positions})
        live_prices = await asyncio.to_thread(_current_prices, tickers)
        prices.update(live_prices)
    enriched = await asyncio.to_thread(
        compute_signals_for_open_positions,
        positions, items, prices,
    )
    return success(
        {"items": enriched, "count": len(enriched)},
        extra_meta={"endpoint": "portfolio.list"},
    )


@router.post("/api/portfolio/positions/refresh-prices")
async def api_portfolio_refresh_prices():
    """Tüm açık pozisyonlar için borsapy'den fresh fiyat çek. Yavaş
    olabilir (her ticker 1-3s) — kullanıcı isterse manuel tetikler."""
    from infra import portfolio_storage
    from engine.portfolio_signals import compute_signals_for_open_positions
    positions = await asyncio.to_thread(portfolio_storage.get_open, 200)
    if not positions:
        return success(
            {"items": [], "count": 0, "refreshed": 0},
            extra_meta={"endpoint": "portfolio.refresh_prices"},
        )
    tickers = list({(p.get("ticker") or "").upper() for p in positions})
    prices = await asyncio.to_thread(_current_prices, tickers)
    items = _current_bw_items()
    enriched = await asyncio.to_thread(
        compute_signals_for_open_positions,
        positions, items, prices,
    )
    return success(
        {"items": enriched, "count": len(enriched),
         "refreshed": len(prices)},
        extra_meta={"endpoint": "portfolio.refresh_prices"},
    )


@router.get("/api/portfolio/positions/{position_id}")
async def api_portfolio_one(position_id: str):
    from infra import portfolio_storage
    from engine.portfolio_signals import compute_exit_signal
    pos = portfolio_storage.get_by_id(position_id)
    if not pos:
        return error("position not found", status_code=404)
    items = _current_bw_items()
    bw_item = items.get((pos.get("ticker") or "").upper())
    price = None
    if pos.get("ticker"):
        prices = _current_prices([pos["ticker"]])
        price = prices.get(pos["ticker"].upper())
    sig = compute_exit_signal(pos, current_item=bw_item, current_price=price)
    return success(
        {"position": pos, "signal": sig},
        extra_meta={"endpoint": "portfolio.one"},
    )


@router.post("/api/portfolio/positions/{position_id}/close")
async def api_portfolio_close(position_id: str, req: ClosePositionRequest):
    from infra import portfolio_storage
    ok = await asyncio.to_thread(
        portfolio_storage.close_position,
        position_id, req.exit_price, exit_reason=req.exit_reason,
    )
    if not ok:
        return error("close failed (position missing or already closed)",
                     status_code=404)
    return success(
        {"ok": True, "position": portfolio_storage.get_by_id(position_id)},
        extra_meta={"endpoint": "portfolio.close"},
    )


@router.get("/api/portfolio/history")
async def api_portfolio_history(
    limit: int = Query(50, ge=1, le=500),
    ticker: Optional[str] = None,
):
    from infra import portfolio_storage
    items = portfolio_storage.get_history(limit=limit, ticker=ticker)
    return success(
        {"items": items, "count": len(items)},
        extra_meta={"endpoint": "portfolio.history"},
    )


@router.get("/api/portfolio/stats")
async def api_portfolio_stats():
    from infra import portfolio_storage
    return success(
        {"stats": portfolio_storage.get_stats()},
        extra_meta={"endpoint": "portfolio.stats"},
    )

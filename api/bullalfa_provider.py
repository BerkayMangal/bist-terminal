# ================================================================
# BISTBULL TERMINAL — BULLALFA v1.4
# api/bullalfa_provider.py
#
# Production data provider — wires the BullAlfa orchestrator to the
# existing repo's data layer (NO modifications to that data layer):
#
#   OHLCV          <- engine.technical.batch_download_history
#   Fundamentals   <- data.bullwatch_cache.cached_compute_metrics
#   Universe       <- config.UNIVERSE + UNIVERSE_EXTRA + UNIVERSE_EXTENDED
#   Macro          <- engine.macro_decision.compute_regime
#   Market status  <- utils.market_status.get_market_status
#   Benchmarks     <- XU100, XBANK, XHOLD, XGMYO (best-effort)
#
# Defensive: every external call wrapped in try/except.
# ================================================================

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

import pandas as pd

from api.bullalfa import ScanContext, TickerInputs

log = logging.getLogger("bistbull.bullalfa_provider")

_BENCH_TICKERS: dict[str, str] = {
    "XU100":  "XU100.IS",
    "XBANK":  "XBANK.IS",
    "XHOLD":  "XHOLD.IS",
    "XGMYO":  "XGMYO.IS",
}

_METRICS_MAX_WORKERS = 16
_UNIVERSE_CAP: Optional[int] = None


def _load_universe() -> list[str]:
    universe: list[str] = []
    seen: set[str] = set()
    for const_name in ("UNIVERSE", "UNIVERSE_EXTRA", "UNIVERSE_EXTENDED"):
        try:
            mod = __import__("config", fromlist=[const_name])
            tickers = getattr(mod, const_name, [])
            for t in tickers:
                if t and t not in seen:
                    seen.add(t)
                    universe.append(t)
        except Exception as exc:
            log.warning("failed to load %s from config: %r", const_name, exc)
    if _UNIVERSE_CAP is not None:
        universe = universe[:_UNIVERSE_CAP]
    log.info("bullalfa: universe size = %d", len(universe))
    return universe


def _fetch_history_batch(symbols: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    try:
        from engine.technical import batch_download_history
    except Exception as exc:
        log.error("engine.technical not importable: %r", exc)
        return {}
    try:
        return batch_download_history(symbols, period=period, interval="1d") or {}
    except Exception as exc:
        log.warning("batch_download_history failed: %r", exc)
        return {}


def _fetch_metrics_one(symbol: str) -> dict[str, Any]:
    try:
        from data.bullwatch_cache import cached_compute_metrics
        return cached_compute_metrics(symbol) or {}
    except Exception as exc:
        log.debug("cached_compute_metrics(%s) failed: %r", symbol, exc)
        try:
            from data.providers import compute_metrics_v9
            return compute_metrics_v9(symbol) or {}
        except Exception as exc2:
            log.debug("compute_metrics_v9(%s) failed: %r", symbol, exc2)
            return {}


def _fetch_metrics_parallel(symbols: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not symbols:
        return out
    with ThreadPoolExecutor(max_workers=_METRICS_MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_metrics_one, s): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                out[sym] = fut.result()
            except Exception as exc:
                log.debug("metrics fetch crashed for %s: %r", sym, exc)
                out[sym] = {}
    return out


def _fetch_benchmarks() -> dict[str, pd.DataFrame]:
    syms = list(_BENCH_TICKERS.values())
    try:
        from engine.technical import batch_download_history
        raw = batch_download_history(syms, period="1y", interval="1d") or {}
    except Exception as exc:
        log.warning("benchmark batch fetch failed: %r", exc)
        return {}
    out: dict[str, pd.DataFrame] = {}
    for name, full_sym in _BENCH_TICKERS.items():
        if full_sym in raw and raw[full_sym] is not None and len(raw[full_sym]) > 0:
            out[name] = raw[full_sym]
        elif name in raw and raw[name] is not None and len(raw[name]) > 0:
            out[name] = raw[name]
    log.info("bullalfa: %d/%d benchmarks fetched", len(out), len(_BENCH_TICKERS))
    return out


def _pick_bench_df(metrics: dict[str, Any], benches: dict[str, pd.DataFrame]) -> Optional[pd.DataFrame]:
    sector = str(metrics.get("sector") or "").lower()
    industry = str(metrics.get("industry") or "").lower()
    haystack = f"{sector} {industry}"
    # NOTE: explicit None check, not `or` — pandas DataFrames raise
    # ValueError when treated as truthy.
    fallback = benches.get("XU100")
    if any(k in haystack for k in ("real estate", "reit", "gayrimenkul")):
        bench = benches.get("XGMYO")
        return bench if bench is not None else fallback
    if any(k in haystack for k in ("conglomerate", "holding", "diversified financial")):
        bench = benches.get("XHOLD")
        return bench if bench is not None else fallback
    if "bank" in haystack:
        bench = benches.get("XBANK")
        return bench if bench is not None else fallback
    return fallback


def _build_macro_result() -> Optional[dict[str, Any]]:
    macro_cache = None
    for mod_path in ("cache", "core.cache"):
        try:
            mod = __import__(mod_path, fromlist=["macro_cache"])
            macro_cache = getattr(mod, "macro_cache", None)
            if macro_cache is not None:
                break
        except Exception:
            continue
    if macro_cache is None:
        log.debug("macro_cache module not found")
        return None
    try:
        macro_data = macro_cache.get("macro_all")
    except Exception as exc:
        log.debug("macro_cache.get failed: %r", exc)
        return None
    if not macro_data or not macro_data.get("items"):
        return None
    try:
        from engine.macro_signals import build_engine_inputs
        from engine.macro_decision import compute_regime
        try:
            from config import STATIC_RATES
        except Exception:
            STATIC_RATES = []  # type: ignore
        inputs = build_engine_inputs(
            macro_data.get("items", []),
            macro_data.get("rates", STATIC_RATES),
            macro_data.get("timestamp"),
        )
        result = compute_regime(inputs)
        d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        regime = str(d.get("regime") or "").lower().replace(" ", "_")
        if regime in {"risk_on", "neutral", "risk_off"}:
            d["regime"] = regime
        return d
    except Exception as exc:
        log.warning("compute_regime failed: %r", exc)
        return None


def _build_market_status() -> Optional[dict[str, Any]]:
    try:
        from utils.market_status import get_market_status
        return get_market_status()
    except Exception as exc:
        log.debug("get_market_status failed: %r", exc)
        return None


def _ticker_inputs_for(
    symbol: str,
    *,
    hist_map: dict[str, pd.DataFrame],
    metrics_map: dict[str, dict[str, Any]],
    benches: dict[str, pd.DataFrame],
) -> Optional[TickerInputs]:
    hist = (hist_map.get(symbol)
            or hist_map.get(f"{symbol}.IS")
            or hist_map.get(symbol.replace(".IS", "")))
    if hist is None or len(hist) == 0:
        return None
    metrics = metrics_map.get(symbol) or metrics_map.get(f"{symbol}.IS") or {}
    sector_raw = metrics.get("sector") or None
    industry_raw = metrics.get("industry") or None
    bench_df = _pick_bench_df(metrics, benches)
    days_listed = len(hist) if hist is not None else None
    return TickerInputs(
        ticker=symbol.replace(".IS", "").upper(),
        hist_df=hist,
        bench_df=bench_df,
        metrics=metrics,
        sector_raw=sector_raw,
        industry_raw=industry_raw,
        tech_pre=None,
        days_listed=days_listed,
        halted_today=False,
    )


async def production_scan_provider() -> tuple[ScanContext, list[TickerInputs]]:
    return await asyncio.to_thread(_run_scan_provider_sync)


def _run_scan_provider_sync() -> tuple[ScanContext, list[TickerInputs]]:
    universe = _load_universe()
    if not universe:
        log.warning("bullalfa: empty universe — returning empty scan")
        return ScanContext(), []
    log.info("bullalfa: fetching OHLCV for %d tickers...", len(universe))
    hist_map = _fetch_history_batch(universe)
    log.info("bullalfa: fetching benchmarks...")
    benches = _fetch_benchmarks()
    log.info("bullalfa: fetching metrics for %d tickers (parallel)...", len(universe))
    metrics_map = _fetch_metrics_parallel(universe)
    log.info("bullalfa: building TickerInputs...")
    inputs: list[TickerInputs] = []
    for sym in universe:
        ti = _ticker_inputs_for(sym, hist_map=hist_map,
                                metrics_map=metrics_map, benches=benches)
        if ti is not None:
            inputs.append(ti)
    log.info("bullalfa: scan provider built %d/%d ticker inputs",
             len(inputs), len(universe))
    ctx = ScanContext(
        macro_result=_build_macro_result(),
        market_status=_build_market_status(),
        isotonic_fits=None,
    )
    return ctx, inputs


async def production_ticker_provider(ticker: str) -> tuple[ScanContext, TickerInputs]:
    return await asyncio.to_thread(_run_ticker_provider_sync, ticker)


def _run_ticker_provider_sync(ticker: str) -> tuple[ScanContext, TickerInputs]:
    sym = ticker.upper().strip()
    hist_map = _fetch_history_batch([sym])
    metrics_map = {sym: _fetch_metrics_one(sym)}
    benches = _fetch_benchmarks()
    ti = _ticker_inputs_for(sym, hist_map=hist_map,
                            metrics_map=metrics_map, benches=benches)
    if ti is None:
        ti = TickerInputs(
            ticker=sym, hist_df=pd.DataFrame(),
            bench_df=None, metrics=metrics_map.get(sym, {}),
            sector_raw=None, industry_raw=None,
            short_history=True,
        )
    ctx = ScanContext(
        macro_result=_build_macro_result(),
        market_status=_build_market_status(),
        isotonic_fits=None,
    )
    return ctx, ti


__all__ = [
    "production_scan_provider",
    "production_ticker_provider",
]

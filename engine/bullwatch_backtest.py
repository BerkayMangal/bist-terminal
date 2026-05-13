"""BullWatch alarm backtest analytics.

Aggregates the immutable high-conviction alarm history (see
`engine.bullwatch_alerts` + `infra.bullwatch_alerts_storage`) into
performance breakdowns the user can use to calibrate trust in the
engine:

  • overall win rate (1d / 1w / 1m horizons)
  • by score band, by zone, by sector, by pattern
  • vs BIST100 baseline over the same windows
  • fake-pump detector: 1d-positive but 1w-negative
  • histogram for return distribution

All numbers are derived from the SAME data the Alarmlar page already
shows — this module just slices it.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

log = logging.getLogger("bullwatch.backtest")


# Win threshold — alarm "won" the horizon if return > this.
# We use 0 (any positive return) for the headline number; the UI can
# also call with stricter thresholds (e.g. +3%, +5%) for filtered views.
DEFAULT_WIN_THRESHOLD = 0.0

HORIZONS = (
    ("1d", "reaction_1d_pct"),
    ("1w", "reaction_1w_pct"),
    ("1m", "reaction_1m_pct"),
)

SCORE_BANDS = (
    ("75-80", 75.0, 80.0),
    ("80-85", 80.0, 85.0),
    ("85-90", 85.0, 90.0),
    ("90+",   90.0, 999.0),
)


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _alert_score(alert: dict) -> float:
    return _safe_float(alert.get("score_at_alarm")) or 0.0


def _score_band(score: float) -> str:
    for label, lo, hi in SCORE_BANDS:
        if lo <= score < hi:
            return label
    return "75-80" if score >= 75 else "below"


def _bucket_stats(returns: list[float], threshold: float = DEFAULT_WIN_THRESHOLD) -> dict[str, Any]:
    """Aggregate a list of percentage returns into a stat bundle."""
    if not returns:
        return {
            "n": 0,
            "win_rate": None,
            "mean": None,
            "median": None,
            "p25": None,
            "p75": None,
            "best": None,
            "worst": None,
        }
    n = len(returns)
    wins = sum(1 for r in returns if r > threshold)
    sorted_r = sorted(returns)

    def _pct(p: float) -> float:
        if n == 1:
            return sorted_r[0]
        idx = int(round(p * (n - 1)))
        return sorted_r[max(0, min(n - 1, idx))]

    return {
        "n": n,
        "win_rate": wins / n,
        "mean": sum(returns) / n,
        "median": _pct(0.50),
        "p25": _pct(0.25),
        "p75": _pct(0.75),
        "best": max(returns),
        "worst": min(returns),
    }


def _collect_alerts(since_days: int) -> list[dict]:
    try:
        from infra import bullwatch_alerts_storage as storage
    except Exception as exc:
        log.warning("storage import failed: %r", exc)
        return []
    try:
        return storage.get_recent(limit=500, since_days=since_days)
    except Exception as exc:
        log.warning("get_recent failed: %r", exc)
        return []


def _bist100_baseline(since_days: int) -> dict[str, dict[str, Any]]:
    """Same-window baseline for BIST100 (XU100) so each alarm's reaction
    can be compared against "did the market just move?". Returns a dict
    keyed by horizon → {return_pct: float | None}.

    Network-free fallback: if the fetcher fails, all values are None and
    the dashboard shows "—" rather than crashing.
    """
    out = {"1d": None, "1w": None, "1m": None}
    try:
        from data.market_data import fetch_xu100_recent_pct
        # Expected to return {"1d": float, "1w": float, "1m": float}.
        # If the helper doesn't exist yet, the import fails fast and we
        # return the empty baseline.
        return fetch_xu100_recent_pct(since_days=since_days) or out
    except ImportError:
        return out
    except Exception as exc:
        log.debug("baseline fetch failed: %r", exc)
        return out


def compute_backtest(
    since_days: int = 90,
    win_threshold: float = DEFAULT_WIN_THRESHOLD,
) -> dict[str, Any]:
    """Main entry. Returns a fully-formed dict ready to ship to the UI.

    Schema:
        {
            "since_days": 90,
            "as_of": "2026-...",
            "total_alerts": 142,
            "overall": {
                "1d": _bucket_stats(...),
                "1w": _bucket_stats(...),
                "1m": _bucket_stats(...),
            },
            "by_score_band": [
                {"band": "75-80", "n": 42, "1d": {...}, "1w": {...}, "1m": {...}},
                ...
            ],
            "by_sector": [...],
            "by_pattern": [...],          # top patterns
            "fake_pump": {
                "count": 8,
                "share": 0.18,
                "samples": [
                    {"ticker": "ABCDE", "alarmed_at": "...",
                     "score": 78, "1d_pct": 4.2, "1w_pct": -3.1}, ...
                ],
            },
            "histogram_1d": [
                {"bucket": "-10..-5%", "count": 3}, ...
            ],
            "baseline": {"1d": 0.4, "1w": 1.1, "1m": -2.3},
        }
    """
    alerts = _collect_alerts(since_days)
    out: dict[str, Any] = {
        "since_days": since_days,
        "as_of": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "total_alerts": len(alerts),
        "win_threshold": win_threshold,
        "overall": {},
        "by_score_band": [],
        "by_zone": [],
        "by_sector": [],
        "by_pattern": [],
        "fake_pump": {"count": 0, "share": 0.0, "samples": []},
        "histogram_1d": [],
        "baseline": _bist100_baseline(since_days),
    }
    if not alerts:
        return out

    # ---- Per-horizon aggregates ----------------------------------
    per_horizon: dict[str, list[float]] = {h: [] for h, _ in HORIZONS}
    for a in alerts:
        for h, col in HORIZONS:
            v = _safe_float(a.get(col))
            if v is not None:
                per_horizon[h].append(v)
    out["overall"] = {
        h: _bucket_stats(per_horizon[h], win_threshold) for h, _ in HORIZONS
    }

    # ---- By score band -------------------------------------------
    band_buckets: dict[str, dict[str, list[float]]] = {
        label: {h: [] for h, _ in HORIZONS} for label, _, _ in SCORE_BANDS
    }
    band_counts: dict[str, int] = {label: 0 for label, _, _ in SCORE_BANDS}
    for a in alerts:
        band = _score_band(_alert_score(a))
        if band not in band_buckets:
            continue
        band_counts[band] += 1
        for h, col in HORIZONS:
            v = _safe_float(a.get(col))
            if v is not None:
                band_buckets[band][h].append(v)
    for label, _, _ in SCORE_BANDS:
        out["by_score_band"].append({
            "band": label,
            "n": band_counts[label],
            "1d": _bucket_stats(band_buckets[label]["1d"], win_threshold),
            "1w": _bucket_stats(band_buckets[label]["1w"], win_threshold),
            "1m": _bucket_stats(band_buckets[label]["1m"], win_threshold),
        })

    # ---- By zone -------------------------------------------------
    zone_buckets: dict[str, dict[str, list[float]]] = {}
    for a in alerts:
        z = (a.get("zone_at_alarm") or "UNKNOWN").upper()
        zone_buckets.setdefault(z, {h: [] for h, _ in HORIZONS})
        for h, col in HORIZONS:
            v = _safe_float(a.get(col))
            if v is not None:
                zone_buckets[z][h].append(v)
    for z, by_h in sorted(zone_buckets.items()):
        # n = number of alerts in this zone (use 1w bucket as proxy when
        # not all reactions filled; fall back to 1d, then 1m)
        n = max(len(by_h["1w"]), len(by_h["1d"]), len(by_h["1m"]))
        out["by_zone"].append({
            "zone": z,
            "n": n,
            "1d": _bucket_stats(by_h["1d"], win_threshold),
            "1w": _bucket_stats(by_h["1w"], win_threshold),
            "1m": _bucket_stats(by_h["1m"], win_threshold),
        })

    # ---- By sector -----------------------------------------------
    sector_buckets: dict[str, dict[str, list[float]]] = {}
    sector_counts: dict[str, int] = {}
    for a in alerts:
        sec = a.get("sector_tr") or "Diğer"
        sector_buckets.setdefault(sec, {h: [] for h, _ in HORIZONS})
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
        for h, col in HORIZONS:
            v = _safe_float(a.get(col))
            if v is not None:
                sector_buckets[sec][h].append(v)
    # Sort by alert volume desc, top 10
    top_sectors = sorted(sector_counts.items(), key=lambda kv: -kv[1])[:10]
    for sec, _ in top_sectors:
        by_h = sector_buckets[sec]
        out["by_sector"].append({
            "sector": sec,
            "n": sector_counts[sec],
            "1d": _bucket_stats(by_h["1d"], win_threshold),
            "1w": _bucket_stats(by_h["1w"], win_threshold),
            "1m": _bucket_stats(by_h["1m"], win_threshold),
        })

    # ---- By pattern (split on " + ") -----------------------------
    pattern_buckets: dict[str, dict[str, list[float]]] = {}
    pattern_counts: dict[str, int] = {}
    for a in alerts:
        raw = a.get("pattern_at_alarm") or ""
        parts = [p.strip() for p in raw.split("+") if p.strip()]
        if not parts:
            continue
        for p in parts:
            pattern_buckets.setdefault(p, {h: [] for h, _ in HORIZONS})
            pattern_counts[p] = pattern_counts.get(p, 0) + 1
            for h, col in HORIZONS:
                v = _safe_float(a.get(col))
                if v is not None:
                    pattern_buckets[p][h].append(v)
    top_patterns = sorted(pattern_counts.items(), key=lambda kv: -kv[1])[:8]
    for p, _ in top_patterns:
        by_h = pattern_buckets[p]
        out["by_pattern"].append({
            "pattern": p,
            "n": pattern_counts[p],
            "1d": _bucket_stats(by_h["1d"], win_threshold),
            "1w": _bucket_stats(by_h["1w"], win_threshold),
            "1m": _bucket_stats(by_h["1m"], win_threshold),
        })

    # ---- Fake pump detector --------------------------------------
    # Alarm fired, 1d positive (>=+3%), then 1w negative (<=-2%).
    # That's an operator pump-and-fade pattern.
    fakes: list[dict[str, Any]] = []
    fake_eligible = 0
    for a in alerts:
        r1d = _safe_float(a.get("reaction_1d_pct"))
        r1w = _safe_float(a.get("reaction_1w_pct"))
        if r1d is None or r1w is None:
            continue
        fake_eligible += 1
        if r1d >= 3.0 and r1w <= -2.0:
            fakes.append({
                "ticker": a.get("ticker"),
                "alarmed_at": a.get("alarmed_at"),
                "score": _alert_score(a),
                "1d_pct": r1d,
                "1w_pct": r1w,
                "sector": a.get("sector_tr"),
                "pattern": a.get("pattern_at_alarm"),
            })
    # Show worst (most negative 1w) first, top 8 samples.
    fakes.sort(key=lambda x: x["1w_pct"])
    out["fake_pump"] = {
        "count": len(fakes),
        "share": (len(fakes) / fake_eligible) if fake_eligible else 0.0,
        "samples": fakes[:8],
    }

    # ---- Histogram for 1d returns --------------------------------
    bins = [(-100, -10), (-10, -5), (-5, -2), (-2, 0),
            (0, 2), (2, 5), (5, 10), (10, 100)]
    bin_labels = [
        "<-10%", "-10..-5%", "-5..-2%", "-2..0%",
        "0..2%", "2..5%", "5..10%", ">10%",
    ]
    counts = [0] * len(bins)
    for v in per_horizon["1d"]:
        for i, (lo, hi) in enumerate(bins):
            if lo <= v < hi:
                counts[i] += 1
                break
    out["histogram_1d"] = [
        {"bucket": bin_labels[i], "count": counts[i]} for i in range(len(bins))
    ]

    return out

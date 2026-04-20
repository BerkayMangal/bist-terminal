"""Sector-conditional signal weight calibration (Phase 4 FAZ 4.2).

Reviewer spec Q2: dual-horizon (20d + 60d) weights per signal.
Reviewer spec FAZ 4.2 weight schema:

    {
      "52W High Breakout": {
        "Kimya":    {"weight_20d": 2.1, "weight_60d": 3.2, "n": 23},
        "Banka":    {"weight_20d": 0.2, "weight_60d": 0.5, "n": 114},
        ...
        "_default": {"weight_20d": 1.0, "weight_60d": 1.5, "n": 662}
      }, ...
    }

Weight formula: annualized Sharpe-like ratio
    weight = (mean_return / std_return) * sqrt(252 / horizon_days)

Unit-free, comparable across signals, negative allowed (contrarian).

Sample threshold: (signal, sector) pairs with n < MIN_N fall back to
the _default weight (which pools all sectors). MIN_N = 20 per Q3:
"n<20 için single-point weight yok".

Golden Cross per spec: global Sharpe = -0.21 → weight -0.21 (contrarian
signal, sign preserved -- signal fires a sell opportunity, not a buy).
"""

from __future__ import annotations

import csv
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

log = logging.getLogger("bistbull.calibration")


# Minimum sample threshold for a (signal, sector) weight.
# Below this -> fall back to _default (all-sectors-pooled) weight.
# Reviewer Q3 explicitly set this to 20.
MIN_N = 20


@dataclass
class SectorWeight:
    """Per-sector weight entry with both horizons."""
    sector: str
    n: int
    weight_20d: Optional[float]
    weight_60d: Optional[float]
    mean_return_20d: Optional[float]
    mean_return_60d: Optional[float]
    std_return_20d: Optional[float]
    std_return_60d: Optional[float]


def _sharpe_weight(returns: Iterable[float], horizon_days: int) -> Optional[float]:
    """Annualized Sharpe-style weight. None if < 2 observations or std=0."""
    r = [x for x in returns if x is not None]
    if len(r) < 2:
        return None
    m = sum(r) / len(r)
    if len(r) == 1:
        return None
    var = sum((x - m) ** 2 for x in r) / (len(r) - 1)
    s = math.sqrt(var)
    if s == 0:
        return None
    return round((m / s) * math.sqrt(252.0 / horizon_days), 4)


def _mean_or_none(returns: Iterable[float]) -> Optional[float]:
    r = [x for x in returns if x is not None]
    if not r:
        return None
    return round(sum(r) / len(r), 6)


def _std_or_none(returns: Iterable[float]) -> Optional[float]:
    r = [x for x in returns if x is not None]
    if len(r) < 2:
        return None
    m = sum(r) / len(r)
    var = sum((x - m) ** 2 for x in r) / (len(r) - 1)
    return round(math.sqrt(var), 6)


def _extract_return(event: dict, horizon_days: int) -> Optional[float]:
    """Return horizon return from an event dict; support multiple column
    naming conventions.

    The calibration training data can arrive in two shapes:
      (A) Phase 3b deep_events.csv -- 'ret_20d' as percent (e.g. 4.86)
      (B) Live labeler output -- 'return_20d' as fraction (e.g. 0.0486)

    We try (A) first (reviewer's deep_events.csv is the documented
    training source) and divide by 100 to normalize to fraction. Then
    fall back to (B) which is already a fraction. If both are present,
    (A) wins -- the CSV is the reviewer-verified ground truth.
    """
    # (A) deep_events.csv shape: ret_{N}d in PERCENT
    if f"ret_{horizon_days}d" in event:
        v = event[f"ret_{horizon_days}d"]
        if v is None or v == "":
            return None
        try:
            return float(v) / 100.0
        except (TypeError, ValueError):
            return None
    # (B) live labeler shape: return_{N}d already a fraction
    if f"return_{horizon_days}d" in event:
        v = event[f"return_{horizon_days}d"]
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return None


def load_events_csv(path: Union[str, Path]) -> list[dict]:
    """Load a deep_events.csv-shaped file into a list of event dicts.

    Expected columns: signal, symbol, date, year, sector, entry,
                      ret_5d, ret_20d, ret_60d, excess_20d
    Returns a list of dicts ready for calibrate_signal_weights.
    Blank/invalid rows are skipped.
    """
    path = Path(path)
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("signal") or not row.get("symbol"):
                continue
            # Coerce numeric columns
            ev = dict(row)
            for k in ("ret_5d", "ret_20d", "ret_60d", "excess_20d", "entry"):
                v = ev.get(k)
                if v is None or v == "":
                    ev[k] = None
                else:
                    try:
                        ev[k] = float(v)
                    except (TypeError, ValueError):
                        ev[k] = None
            try:
                ev["year"] = int(ev["year"]) if ev.get("year") else None
            except (TypeError, ValueError):
                ev["year"] = None
            out.append(ev)
    return out


def _attach_sector(events: list[dict]) -> list[dict]:
    """If events don't already have a 'sector' field, look it up from
    research.sectors.SECTOR_MAP. Returns events (mutated in place)."""
    try:
        from research.sectors import get_sector
    except ImportError:
        return events
    for ev in events:
        if not ev.get("sector"):
            sym = ev.get("symbol", "")
            ev["sector"] = get_sector(sym) or "Unknown"
    return events


def calibrate_signal_weights(
    events: list[dict],
    horizons: tuple[int, ...] = (20, 60),
    min_n: int = MIN_N,
) -> dict:
    """Build the {signal: {sector: {weight_20d, weight_60d, n}}} map.

    For each signal:
      - _default: pool ALL events for this signal (every sector), compute
        the unconditional weight. Always present.
      - Each sector with n >= min_n: its own conditional weight.
      - Sectors with n < min_n: NOT added (caller falls back to _default).

    Weight = annualized Sharpe ratio per horizon. Sign preserved
    (contrarian signals get negative weight). The `n` field at the
    per-sector level is the sample size; the `_default` entry's `n` is
    the total across all sectors.

    Returns the dict directly; serialize with json.dumps(..., indent=2).
    """
    _attach_sector(events)

    # Group by signal -> sector
    from collections import defaultdict
    by_signal: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for ev in events:
        sig = ev.get("signal")
        sec = ev.get("sector")
        if not (sig and sec):
            continue
        by_signal[sig][sec].append(ev)

    out: dict = {}
    for signal, sector_map in by_signal.items():
        all_events_for_signal: list[dict] = []
        for evs in sector_map.values():
            all_events_for_signal.extend(evs)

        entry: dict = {}

        # _default: pooled across all sectors
        default_entry = {"n": len(all_events_for_signal)}
        for h in horizons:
            rets = [_extract_return(e, h) for e in all_events_for_signal]
            default_entry[f"weight_{h}d"] = _sharpe_weight(rets, h)
            default_entry[f"mean_return_{h}d"] = _mean_or_none(rets)
            default_entry[f"std_return_{h}d"] = _std_or_none(rets)
        entry["_default"] = default_entry

        # Per-sector (only if n >= min_n)
        for sector in sorted(sector_map.keys()):
            evs = sector_map[sector]
            if len(evs) < min_n:
                continue
            sec_entry: dict = {"n": len(evs)}
            for h in horizons:
                rets = [_extract_return(e, h) for e in evs]
                sec_entry[f"weight_{h}d"] = _sharpe_weight(rets, h)
                sec_entry[f"mean_return_{h}d"] = _mean_or_none(rets)
                sec_entry[f"std_return_{h}d"] = _std_or_none(rets)
            entry[sector] = sec_entry

        out[signal] = entry

    return out


def get_weight(
    weights: dict,
    signal: str,
    sector: Optional[str],
    horizon_days: int,
) -> Optional[float]:
    """Look up the (signal, sector, horizon) weight from a calibrated dict.

    Fallback chain per reviewer spec Q2 and FAZ 4.2:
      1. (signal, sector) -> weight_{N}d if present
      2. (signal, _default) -> weight_{N}d
      3. None (signal not in weights -- caller decides default)
    """
    sig_entry = weights.get(signal)
    if not sig_entry:
        return None
    key = f"weight_{horizon_days}d"
    if sector and sector in sig_entry:
        w = sig_entry[sector].get(key)
        if w is not None:
            return w
    default = sig_entry.get("_default", {})
    return default.get(key)


def write_weights_json(weights: dict, out_path: Union[str, Path]) -> Path:
    """Write the calibrated weights to reports/phase_4_weights.json."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(weights, indent=2, ensure_ascii=False))
    return out_path


def write_weights_markdown(weights: dict, out_path: Union[str, Path]) -> Path:
    """Human-readable calibration weights report.

    Layout: one section per signal with a sector table (and _default
    row), columns n / weight_20d / weight_60d / mean_20d / mean_60d.
    Sectors are sorted by |weight_20d| descending to surface the
    strongest conditional weights first.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _fmt(v, pct=False, n=3):
        if v is None: return "—"
        if pct: return f"{v*100:.2f}%"
        return f"{v:+.{n}f}"

    lines = ["# Phase 4 Calibrated Weights\n",
             "Annualized Sharpe-style weight per (signal, sector, horizon)."
             " `_default` pools all sectors for that signal. Per-sector "
             f"entries require n ≥ {MIN_N}; under-sampled sectors fall "
             "back to `_default`. Negative weights = contrarian signals.\n"]

    for signal in sorted(weights.keys()):
        entry = weights[signal]
        lines.append(f"\n## {signal}")
        default = entry.get("_default", {})
        lines.append(f"\n**Total events:** {default.get('n', 0)}  "
                     f"·  **Default weight 20d:** "
                     f"{_fmt(default.get('weight_20d'))}  "
                     f"·  **Default weight 60d:** "
                     f"{_fmt(default.get('weight_60d'))}\n")

        lines.append("| Sector | n | weight_20d | weight_60d | mean_20d | mean_60d |")
        lines.append("|---|---|---|---|---|---|")
        # Default row first
        lines.append(
            f"| _default | {default.get('n', 0)} "
            f"| {_fmt(default.get('weight_20d'))} "
            f"| {_fmt(default.get('weight_60d'))} "
            f"| {_fmt(default.get('mean_return_20d'), pct=True)} "
            f"| {_fmt(default.get('mean_return_60d'), pct=True)} |"
        )
        # Then per-sector rows sorted by |weight_20d| DESC
        sector_rows = [(s, e) for s, e in entry.items() if s != "_default"]
        def _key(item):
            w = item[1].get("weight_20d")
            return -abs(w) if w is not None else 0.0
        for sector, sec_entry in sorted(sector_rows, key=_key):
            lines.append(
                f"| {sector} | {sec_entry.get('n', 0)} "
                f"| {_fmt(sec_entry.get('weight_20d'))} "
                f"| {_fmt(sec_entry.get('weight_60d'))} "
                f"| {_fmt(sec_entry.get('mean_return_20d'), pct=True)} "
                f"| {_fmt(sec_entry.get('mean_return_60d'), pct=True)} |"
            )

    out_path.write_text("\n".join(lines) + "\n")
    return out_path

"""VIOP UOA (Unusual Options Activity) engine.

Detects volume anomalies in VIOP option/future contracts. The mechanic:
each contract has a rolling N-day baseline (mean+stdev) computed from
its `viop_snapshots` history; today's volume z-score against that
baseline ranks the most-unusual activity.

This is BistBull's BIST analog of Unusual Whales / Cheddar Flow.

Why z-score (not just ratio): when baseline volume is tiny (illiquid
contract on quiet days), even small absolute moves blow up the ratio.
Z-score normalizes by the variability — only flagging the moves that
are abnormal relative to the contract's OWN history.

Output sorted by `score` (z-score) desc. UI shows top N per page.
"""
from __future__ import annotations

import datetime as _dt
import logging
import math
from typing import Any, Optional

log = logging.getLogger("bistbull.viop_uoa")

# Minimum baseline days. Below this, z-score is too noisy — we still
# show the contract but mark its score 'tentative' so the UI can dim it.
MIN_BASELINE_DAYS = 5
# Minimum baseline TL volume — contracts that average <500 TL/day get
# excluded so we don't surface "today's only trade" noise.
MIN_BASELINE_AVG_TL = 500.0
# Floor for stdev — avoids divide-by-zero when baseline is dead-flat.
STDEV_FLOOR = 1.0


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list[float], mean: Optional[float] = None) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean if mean is not None else _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def compute_uoa(
    history: list[dict[str, Any]],
    today_volume_tl: Optional[float] = None,
) -> dict[str, Any]:
    """Compute UOA stats for ONE contract.

    `history` is the rows returned by viop_storage.get_history(code)
    sorted newest-first. The newest row's volume_tl is "today" unless
    `today_volume_tl` is provided explicitly. Prior rows form the
    baseline.

    Returns:
        {
          "score":              float | None,    # z-score
          "baseline_days":      int,
          "baseline_avg_tl":    float | None,
          "baseline_stdev_tl":  float | None,
          "today_tl":           float | None,
          "ratio":              float | None,    # today / avg
          "tentative":          bool,            # n < MIN_BASELINE_DAYS
          "eligible":           bool,            # passed avg-TL floor
        }
    """
    out: dict[str, Any] = {
        "score": None,
        "baseline_days": 0,
        "baseline_avg_tl": None,
        "baseline_stdev_tl": None,
        "today_tl": None,
        "ratio": None,
        "tentative": True,
        "eligible": False,
    }
    if not history:
        return out

    # Newest first; "today" is index 0 unless override
    today_row = history[0]
    today_tl = (
        today_volume_tl
        if today_volume_tl is not None
        else today_row.get("volume_tl")
    )
    out["today_tl"] = float(today_tl) if today_tl is not None else None

    baseline_rows = history[1:]
    base_vols = [
        float(r["volume_tl"])
        for r in baseline_rows
        if r.get("volume_tl") is not None
    ]
    out["baseline_days"] = len(base_vols)
    if not base_vols:
        return out

    avg = _mean(base_vols)
    sd = _stdev(base_vols, mean=avg)
    out["baseline_avg_tl"] = round(avg, 2)
    out["baseline_stdev_tl"] = round(sd, 2)

    out["tentative"] = len(base_vols) < MIN_BASELINE_DAYS
    out["eligible"] = avg >= MIN_BASELINE_AVG_TL

    if out["today_tl"] is not None:
        out["ratio"] = round(out["today_tl"] / max(avg, 1.0), 2)
        # z-score with stdev floor to keep ranking stable on dead-flat
        # baselines (otherwise a single trade on a quiet contract spikes
        # to +inf).
        denom = max(sd, STDEV_FLOOR, avg * 0.10)
        out["score"] = round((out["today_tl"] - avg) / denom, 2)
    return out


def get_today_anomalies(
    kind: Optional[str] = None,
    min_score: float = 2.0,
    include_tentative: bool = False,
    baseline_days: int = 30,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return today's top UOA-flagged contracts, sorted by z-score desc.

    Each row carries the parent snapshot fields PLUS the UOA bundle:
        {
          ...snapshot fields...,
          "uoa": { score, baseline_days, baseline_avg_tl, ratio, ... },
        }
    """
    try:
        from infra import viop_storage
    except Exception as exc:
        log.warning("uoa storage import: %r", exc)
        return []

    today_rows = viop_storage.get_today(kind=kind, limit=500)
    out: list[dict[str, Any]] = []
    for row in today_rows:
        code = row.get("code")
        if not code:
            continue
        hist = viop_storage.get_history(code, days=baseline_days)
        uoa = compute_uoa(hist, today_volume_tl=row.get("volume_tl"))
        if uoa["score"] is None:
            continue
        if not include_tentative and uoa["tentative"]:
            continue
        if not uoa["eligible"]:
            continue
        if uoa["score"] < min_score:
            continue
        row_with_uoa = dict(row)
        row_with_uoa["uoa"] = uoa
        out.append(row_with_uoa)
    out.sort(key=lambda r: -(r["uoa"]["score"] or 0))
    return out[: max(1, limit)]


def get_summary(
    baseline_days: int = 30,
    min_score: float = 2.0,
) -> dict[str, Any]:
    """Top-line UOA stats for the VIOP tab banner.

    Returns:
        {
            "as_of": ISO,
            "snap_date": "YYYY-MM-DD",
            "n_options_anomalous": int,
            "n_futures_anomalous": int,
            "top_underlying": [{"underlying", "score_max", "n"}, ...],
            "min_score": float,
            "baseline_days": int,
        }
    """
    anomalies = get_today_anomalies(
        min_score=min_score,
        include_tentative=False,
        baseline_days=baseline_days,
        limit=200,
    )
    out: dict[str, Any] = {
        "as_of": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "snap_date": None,
        "n_options_anomalous": 0,
        "n_futures_anomalous": 0,
        "top_underlying": [],
        "min_score": min_score,
        "baseline_days": baseline_days,
    }
    if anomalies:
        out["snap_date"] = anomalies[0].get("snap_date")
    per_underlying: dict[str, dict[str, Any]] = {}
    for a in anomalies:
        kind = a.get("kind")
        if kind == "option":
            out["n_options_anomalous"] += 1
        elif kind == "future":
            out["n_futures_anomalous"] += 1
        u = a.get("underlying")
        if u:
            entry = per_underlying.setdefault(
                u, {"underlying": u, "score_max": 0.0, "n": 0}
            )
            entry["n"] += 1
            sc = (a.get("uoa") or {}).get("score") or 0
            entry["score_max"] = max(entry["score_max"], sc)
    top = sorted(
        per_underlying.values(),
        key=lambda e: (-e["score_max"], -e["n"]),
    )[:10]
    out["top_underlying"] = top
    return out

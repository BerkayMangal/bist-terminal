# ================================================================
# BISTBULL TERMINAL — DELTA & CHANGE TRACKING
# engine/delta.py
#
# Saves daily score snapshots, computes 7-day deltas,
# generates "what changed" text. Never crashes.
# ================================================================
from __future__ import annotations
import logging
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger("bistbull.delta")


def save_daily_snapshot(symbol: str, analysis: dict) -> None:
    """Save today's scores. Silently skips on error."""
    try:
        _save(symbol, analysis)
    except Exception as e:
        log.debug(f"delta snapshot save failed for {symbol}: {e}")


def compute_delta(symbol: str, analysis: dict) -> dict:
    """Compute delta from stored history. Never raises."""
    try:
        return _compute(symbol, analysis)
    except Exception as e:
        log.debug(f"delta compute failed for {symbol}: {e}")
        return {}


# ── Internal ─────────────────────────────────────────────────────

def _save(symbol: str, a: dict) -> None:
    from storage import _get_conn
    conn = _get_conn()
    today = date.today().isoformat()
    score = a.get("overall") or a.get("deger") or 0
    momentum = a.get("ivme") or 0
    risk = a.get("risk_score") or 0
    fa = a.get("fa_score") or 0
    ivme = a.get("ivme") or 0
    decision = a.get("decision") or ""
    conn.execute(
        """INSERT INTO score_history (symbol, snap_date, score, momentum, risk, fa_score, ivme, decision)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol, snap_date) DO UPDATE SET
             score=excluded.score, momentum=excluded.momentum, risk=excluded.risk,
             fa_score=excluded.fa_score, ivme=excluded.ivme, decision=excluded.decision""",
        (symbol.upper(), today, score, momentum, risk, fa, ivme, decision),
    )
    conn.commit()


def _compute(symbol: str, a: dict) -> dict:
    from storage import _get_conn
    conn = _get_conn()
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()

    row = conn.execute(
        "SELECT score, momentum, risk, fa_score, ivme, decision FROM score_history WHERE symbol = ? AND snap_date <= ? ORDER BY snap_date DESC LIMIT 1",
        (symbol.upper(), week_ago),
    ).fetchone()

    if row is None:
        return {}

    prev_score = row[0] or 0
    prev_momentum = row[1] or 0
    prev_risk = row[2] or 0

    cur_score = a.get("overall") or a.get("deger") or 0
    cur_momentum = a.get("ivme") or 0
    cur_risk = a.get("risk_score") or 0

    d_score = round(cur_score - prev_score, 1)
    d_momentum = round(cur_momentum - prev_momentum, 1)
    d_risk = round(cur_risk - prev_risk, 1)

    delta = {
        "score_7d": d_score,
        "momentum_7d": d_momentum,
        "risk_7d": d_risk,
    }

    what_changed = _what_changed(d_score, d_momentum, d_risk)

    return {"delta": delta, "what_changed": what_changed}


def _what_changed(d_score: float, d_momentum: float, d_risk: float) -> list[str]:
    items: list[str] = []

    if abs(d_score) >= 2:
        if d_score > 0:
            items.append(f"Son 7 günde skor +{d_score:.0f} arttı")
        else:
            items.append(f"Son 7 günde skor {d_score:.0f} düştü")

    if abs(d_momentum) >= 3:
        if d_momentum > 0:
            items.append("Momentum güçlendi")
        else:
            items.append("Momentum zayıfladı")

    if abs(d_risk) >= 2:
        if d_risk < 0:
            items.append("Risk azaldı")
        else:
            items.append("Risk arttı")

    if not items and (abs(d_score) > 0 or abs(d_momentum) > 0):
        items.append("Önemli bir değişiklik yok")

    return items[:3]


# ── Watchlist delta (for "2 değişiklik var" hook) ────────────────

def watchlist_changes(symbols: list[str]) -> list[dict]:
    """Return list of symbols with notable score changes."""
    try:
        return _wl_changes(symbols)
    except Exception:
        return []


def _wl_changes(symbols: list[str]) -> list[dict]:
    from storage import _get_conn
    conn = _get_conn()
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    changes = []

    for sym in symbols:
        rows = conn.execute(
            "SELECT snap_date, score, decision FROM score_history WHERE symbol = ? AND snap_date >= ? ORDER BY snap_date ASC",
            (sym.upper(), week_ago),
        ).fetchall()
        if len(rows) < 2:
            continue
        first = rows[0]
        last = rows[-1]
        d = (last[1] or 0) - (first[1] or 0)
        if abs(d) >= 3:
            changes.append({
                "symbol": sym,
                "delta": round(d, 1),
                "direction": "up" if d > 0 else "down",
                "text": f"{sym}: skor {'+'if d>0 else ''}{d:.0f}",
            })

    return changes


# ── Leaderboard (biggest movers) ─────────────────────────────────

def get_movers() -> dict:
    """Return top 3 gainers and losers by 7d score change."""
    try:
        return _movers()
    except Exception:
        return {"gainers": [], "losers": []}


def _movers() -> dict:
    from storage import _get_conn
    conn = _get_conn()
    today = date.today().isoformat()
    week_ago = (today - timedelta(days=7)).isoformat()

    rows = conn.execute(
        """SELECT h1.symbol,
                  h2.score - h1.score AS delta,
                  h2.score AS current_score,
                  h2.decision
           FROM score_history h1
           JOIN score_history h2 ON h1.symbol = h2.symbol
           WHERE h1.snap_date = (SELECT MIN(snap_date) FROM score_history WHERE snap_date >= ? AND symbol = h1.symbol)
             AND h2.snap_date = ?
           ORDER BY delta DESC""",
        (week_ago, today),
    ).fetchall()

    if not rows:
        return {"gainers": [], "losers": []}

    gainers = [{"symbol": r[0], "delta": round(r[1], 1), "score": round(r[2], 1)} for r in rows[:3] if r[1] > 0]
    losers = [{"symbol": r[0], "delta": round(r[1], 1), "score": round(r[2], 1)} for r in rows[-3:] if r[1] < 0]
    losers.reverse()

    return {"gainers": gainers, "losers": losers}

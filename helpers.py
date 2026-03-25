# ================================================================
# BISTBULL TERMINAL V9.1 — HELPERS
# Saf yardımcı fonksiyonlar. Side-effect SIFIR.
# ================================================================

from __future__ import annotations

import math
import datetime as dt
from typing import Any, Optional

import numpy as np
import pandas as pd


def safe_num(x: Any) -> Optional[float]:
    """Herhangi bir değeri güvenli float'a çevir. None/NaN/Inf → None."""
    try:
        if x is None:
            return None
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def fmt_num(x: Any, digits: int = 2) -> str:
    """Sayıyı okunabilir formata çevir: 1.5B, 3.2M, 1,234, 12.50"""
    v = safe_num(x)
    if v is None:
        return "N/A"
    abs_v = abs(v)
    if abs_v >= 1e9:
        return f"{v / 1e9:.2f}B"
    if abs_v >= 1e6:
        return f"{v / 1e6:.2f}M"
    if abs_v >= 1e3:
        return f"{v:,.0f}"
    return f"{v:.{digits}f}"


def fmt_pct(x: Any, digits: int = 1) -> str:
    """Oranı yüzde formatına çevir: 0.15 → '15.0%'"""
    v = safe_num(x)
    if v is None:
        return "N/A"
    return f"{v * 100:.{digits}f}%"


def normalize_symbol(ticker: str) -> str:
    """Ticker'ı Yahoo Finance formatına çevir: 'THYAO' → 'THYAO.IS'"""
    t = (ticker or "").strip().upper().replace(" ", "")
    if t.endswith(".IS"):
        return t
    if "." in t:
        return t
    return f"{t}.IS"


def base_ticker(text: str) -> str:
    """Yahoo Finance sembolünü düz ticker'a çevir: 'THYAO.IS' → 'THYAO'"""
    return (text or "").strip().upper().replace(".IS", "")


def pick_row_pair(
    df: Optional[pd.DataFrame],
    names: list[str],
) -> tuple[Optional[float], Optional[float]]:
    """DataFrame'den bir satırın cari ve önceki değerini çek.
    Birden fazla olası satır ismi dener (ilk bulunan kazanır).
    Returns: (current_value, previous_value)
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None, None
    for name in names:
        if name in df.index:
            try:
                s = df.loc[name]
                if isinstance(s, pd.DataFrame):
                    s = s.iloc[:, 0]
                s = pd.to_numeric(s, errors="coerce").dropna()
                if s.empty:
                    continue
                cur = safe_num(s.iloc[0])
                prev = safe_num(s.iloc[1]) if len(s) > 1 else None
                return cur, prev
            except Exception:
                continue
    return None, None


def growth(cur: Any, prev: Any) -> Optional[float]:
    """Büyüme oranı hesapla. prev=0 veya None → None."""
    c = safe_num(cur)
    p = safe_num(prev)
    if c is None or p in (None, 0):
        return None
    return (c - p) / abs(p)


def avg(values: list) -> Optional[float]:
    """None olmayan değerlerin ortalaması. Hepsi None → None."""
    valid = [safe_num(v) for v in values if safe_num(v) is not None]
    if not valid:
        return None
    return float(sum(valid) / len(valid))


def score_higher(x: Any, bad: float, ok: float, good: float, great: float) -> Optional[float]:
    """Yüksek = iyi olan metrikler için 5-100 arası skor.
    x <= bad → 5, x >= great → 100, arada lineer interpolasyon.
    """
    v = safe_num(x)
    if v is None:
        return None
    if v <= bad:
        return 5.0
    if v >= great:
        return 100.0
    if v <= ok:
        return 5 + (v - bad) * (35 / max(ok - bad, 1e-9))
    if v <= good:
        return 40 + (v - ok) * (35 / max(good - ok, 1e-9))
    return 75 + (v - good) * (25 / max(great - good, 1e-9))


def score_lower(x: Any, great: float, good: float, ok: float, bad: float) -> Optional[float]:
    """Düşük = iyi olan metrikler için 5-100 arası skor.
    x <= great → 100, x >= bad → 5, arada lineer interpolasyon.
    """
    v = safe_num(x)
    if v is None:
        return None
    if v <= great:
        return 100.0
    if v >= bad:
        return 5.0
    if v <= good:
        return 100 - (v - great) * (25 / max(good - great, 1e-9))
    if v <= ok:
        return 75 - (v - good) * (35 / max(ok - good, 1e-9))
    return 40 - (v - ok) * (35 / max(bad - ok, 1e-9))


def clean_for_json(obj: Any) -> Any:
    """Recursively clean NaN/Inf and non-serializable types for JSON output."""
    if isinstance(obj, dict):
        return {
            k: clean_for_json(v)
            for k, v in obj.items()
            if k != "df" and not isinstance(v, pd.DataFrame)
        }
    if isinstance(obj, (list, tuple)):
        return [clean_for_json(i) for i in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 4)
    if isinstance(obj, (np.integer, np.int64)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64)):
        v = float(obj)
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 4)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, dt.datetime):
        return obj.isoformat()
    return obj

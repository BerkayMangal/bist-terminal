# ================================================================
# BISTBULL TERMINAL — V13 VERDICT ENGINE
# engine/verdict.py
#
# "Grandma Test": One-sentence deterministic verdict explaining
# WHY the score is high or low. No AI, no randomness.
#
# Input: full V13 analysis result
# Output: single Turkish sentence with numbers
#
# Saf fonksiyon. IO/Cache SIFIR. Crash ETMEZ.
# ================================================================

from __future__ import annotations
import logging
from typing import Optional

log = logging.getLogger("bistbull.verdict")


def _fmt(v, suffix: str = "", decimals: int = 0) -> str:
    """Safe format for display."""
    if v is None:
        return "?"
    try:
        f = float(v)
        if decimals == 0:
            return f"{f:.0f}{suffix}"
        return f"{f:.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "?"


def build_verdict(r: dict) -> str:
    """
    V13 Grandma Test: tek cümle, Türkçe, rakam bazlı.
    
    Öncelik sırası:
    1. Fatal risk varsa → risk açıkla
    2. Akademik filtre çok kötüyse → ROE vs Ke / büyüme tuzağı açıkla
    3. Türkiye filtresi çok kötüyse → borç/döviz açıkla
    4. FA çok iyiyse → pozitif açıkla
    5. Genel durum
    """
    try:
        m = r.get("metrics", {})
        scores = r.get("scores", {})
        v13 = r.get("v13", {})
        acad = v13.get("academic", {})
        turkey = v13.get("turkey", {})
        final = v13.get("final_score", r.get("overall", 50))
        fa_pure = r.get("fa_score", 50)
        ticker = r.get("ticker", "?")

        roe = m.get("roe")
        pe = m.get("pe")
        nd_ebitda = m.get("net_debt_ebitda")
        fcf_margin = m.get("fcf_margin")
        rev_growth = m.get("revenue_growth")

        # ── Priority 1: Fatal risks ──
        fatal_triggers = r.get("v11", {}).get("fatal_risks", [])
        if fatal_triggers:
            if "negative_equity" in fatal_triggers:
                return (
                    f"{ticker}: Özsermaye negatif — şirket teknik olarak batık durumda. "
                    f"F/K {_fmt(pe)} ucuz görünse bile bilanço çökmüş."
                )
            if "fake_profit_critical" in fatal_triggers:
                return (
                    f"{ticker}: Kâr gösteriyor ama kasa boş (nakit akışı negatif). "
                    f"Faiz karşılama {_fmt(m.get('interest_coverage'), 'x', 1)} — borç servisini karşılayamıyor."
                )
            if "debt_distress" in fatal_triggers:
                return (
                    f"{ticker}: Borç stresi — NB/FAVÖK {_fmt(nd_ebitda, 'x', 1)}, "
                    f"faiz karşılama {_fmt(m.get('interest_coverage'), 'x', 1)}. "
                    f"Yüksek faiz ortamında sürdürülemez."
                )
            return f"{ticker}: Ciddi risk sinyalleri tespit edildi ({', '.join(fatal_triggers[:2])}). Skor {_fmt(final)} bu yüzden düşük."

        # ── Priority 2: Academic layer verdict ──
        acad_grade = acad.get("composite_grade", "?")
        acad_filters = acad.get("filters", {})

        vc = acad_filters.get("value_creation", {})
        gt = acad_filters.get("growth_trap", {})

        if acad_grade in ("D", "F"):
            # Value creation failure
            if vc.get("grade") in ("D", "F") and roe is not None:
                ke = vc.get("components", {}).get("ke", 0.58)
                return (
                    f"{ticker}: F/K {_fmt(pe)} ucuz görünüyor ama ROE %{_fmt(roe, '', 0) if roe and roe < 1 else _fmt(roe)} "
                    f"mevduat faizinin (%37) altında. "
                    f"Paranı bankaya koysaydın {max(0.37 / max(roe, 0.01), 1):.1f}x daha fazla kazanırdın."
                )
            # Growth trap
            if gt.get("is_trap", False):
                return (
                    f"{ticker}: Büyüme tuzağı — gelir %{_fmt(rev_growth, '', 0) if rev_growth and rev_growth < 1 else _fmt(rev_growth)} artıyor "
                    f"ama FCF negatif (marj %{_fmt(fcf_margin, '', 1) if fcf_margin and abs(fcf_margin) < 1 else _fmt(fcf_margin)}). "
                    f"Nakit yakarak büyüyor, yüksek faiz ortamında tehlikeli."
                )
            return (
                f"{ticker}: Akademik filtre olumsuz ({acad_grade}). "
                f"{acad.get('summary', 'Detay hesaplanamadı.')}"
            )

        # ── Priority 3: Turkey filter verdict ──
        turkey_grade = turkey.get("composite_grade", "?")
        turkey_mult = turkey.get("composite_multiplier", 1.0)

        if turkey_grade in ("D", "F") and turkey_mult < 0.85:
            turkey_filters = turkey.get("filters", {})
            rate_res = turkey_filters.get("rate_resistance", {})
            fx = turkey_filters.get("fx_shield", {})

            parts = []
            if rate_res.get("grade") in ("D", "F") and nd_ebitda is not None and nd_ebitda > 2:
                eff_cost = rate_res.get("components", {}).get("effective_cost", 0)
                parts.append(f"efektif borç maliyeti FAVÖK'ün %{_fmt(eff_cost * 100)}si")
            if fx.get("grade") in ("D", "F"):
                parts.append("döviz geliri düşük, kur riski yüksek")

            detail = " ve ".join(parts) if parts else "makro koşullar olumsuz"
            return (
                f"{ticker}: Türkiye filtresi skoru %{_fmt(turkey_mult * 100 - 100, '', 0)} düşürdü — "
                f"{detail}. Ham FA {_fmt(fa_pure)} → Ayarlı {_fmt(fa_pure * turkey_mult, '', 0)}."
            )

        # ── Priority 4: Strong stock ──
        if final >= 70:
            strong_dims = [k for k, v in scores.items() if v >= 70 and k in ("quality", "value", "balance", "earnings")]
            if roe is not None and roe > 0.30:
                return (
                    f"{ticker}: Güçlü temel (skor {_fmt(final)}). ROE %{_fmt(roe * 100 if roe < 1 else roe)} "
                    f"sermaye maliyetinin üstünde, "
                    f"{'bilanço sağlam' if scores.get('balance', 0) >= 60 else 'ama bilanço takip edilmeli'}. "
                    f"{'Nakit akışı kârı destekliyor.' if scores.get('earnings', 0) >= 60 else ''}"
                )
            if strong_dims:
                return (
                    f"{ticker}: Skor {_fmt(final)} — "
                    f"{', '.join(strong_dims)} boyutlarında güçlü. "
                    f"F/K {_fmt(pe)}, PD/DD {_fmt(m.get('pb'), '', 1)}."
                )

        # ── Priority 5: Mid-range ──
        if final >= 45:
            weak_dims = [k for k, v in scores.items() if v < 40 and k in ("quality", "value", "balance", "earnings", "growth")]
            if weak_dims:
                return (
                    f"{ticker}: Orta skor ({_fmt(final)}). "
                    f"{', '.join(weak_dims)} zayıf. "
                    f"F/K {_fmt(pe)}, ROE %{_fmt(roe * 100 if roe and roe < 1 else roe)}."
                )
            return f"{ticker}: Dengeli profil (skor {_fmt(final)}). Öne çıkan güçlü veya zayıf yön yok."

        # ── Priority 6: Weak stock ──
        problems = []
        if scores.get("quality", 50) < 35:
            problems.append(f"düşük kârlılık (ROE %{_fmt(roe * 100 if roe and roe < 1 else roe)})")
        if scores.get("balance", 50) < 35:
            problems.append(f"borç riski (NB/FAVÖK {_fmt(nd_ebitda, 'x', 1)})")
        if scores.get("earnings", 50) < 35:
            problems.append("kâr kalitesi zayıf")
        if not problems:
            problems.append("birden fazla boyut zayıf")

        return (
            f"{ticker}: Düşük skor ({_fmt(final)}) — {', '.join(problems[:2])}. "
            f"{'Büyüme tuzağı riski var.' if gt.get('is_trap', False) else 'Temel iyileşme gerekiyor.'}"
        )

    except Exception as e:
        log.warning(f"verdict build failed: {e}")
        return f"{r.get('ticker', '?')}: Değerleme özeti hesaplanamadı."


def build_verdict_short(r: dict) -> str:
    """Ultra-short verdict for radar table tooltip (max 60 chars)."""
    try:
        v13 = r.get("v13", {})
        final = v13.get("final_score", r.get("overall", 50))
        acad = v13.get("academic", {})
        turkey = v13.get("turkey", {})

        acad_grade = acad.get("composite_grade", "?")
        turkey_grade = turkey.get("composite_grade", "?")

        if final >= 70:
            return f"Güçlü temel ({acad_grade}) | TR filtre: {turkey_grade}"
        elif final >= 45:
            return f"Orta ({acad_grade}) | TR filtre: {turkey_grade}"
        else:
            return f"Zayıf ({acad_grade}) | TR filtre: {turkey_grade}"
    except Exception:
        return "Özet hesaplanamadı"

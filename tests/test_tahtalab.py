# ================================================================
# tests/test_tahtalab.py
#
# TahtaLab — BIST tahta davranışı uyarı laboratuvarı testleri.
#
# Kapsam: kural kütüphanesi, OR-bazlı bağımsız eşleşme, hisseye göre
# gruplama, özet sayımları, intraday/kurumsal-olay kurallarının veri
# yokken susması, sentetik OHLCV üzerinde günlük kurallar, API şeması,
# frontend nav, yasak kelime kontrolü.
# ================================================================

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest

from engine.tahta_warning_registry import (
    WARNING_REGISTRY, get_rule_library, get_definition, THRESHOLDS,
)
from engine.tahta_warnings import TahtaWarningEngine, ENGINE


# ────────────────────────────────────────────────────────────────
# Sentetik OHLCV yardımcıları
# ────────────────────────────────────────────────────────────────
def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _flat_rows(n: int = 25, price: float = 100.0, vol: float = 1000.0) -> list[dict]:
    """Düz, sıkıcı geçmiş — hiçbir kuralı tetiklemez."""
    return [
        {"Open": price, "High": price + 1, "Low": price - 1,
         "Close": price, "Volume": vol}
        for _ in range(n)
    ]


def _boring_df() -> pd.DataFrame:
    return _df(_flat_rows(26))


def _weak_pre_limit_df() -> pd.DataFrame:
    """Güçlü tepe + zayıf kapanış + yüksek hacim."""
    rows = _flat_rows(25)
    rows.append({"Open": 100, "High": 110, "Low": 100, "Close": 105,
                 "Volume": 2000})
    return _df(rows)


def _base_rebound_df() -> pd.DataFrame:
    """Taban bölgesine değip tepki — düşük dip, güçlü kapanış."""
    rows = _flat_rows(25)
    rows.append({"Open": 95, "High": 100, "Low": 91, "Close": 98,
                 "Volume": 1600})
    return _df(rows)


def _close_selloff_df() -> pd.DataFrame:
    """Uzun üst fitil, kapanışta geri verme, yüksek hacim."""
    rows = _flat_rows(25)
    rows.append({"Open": 100, "High": 110, "Low": 100, "Close": 102,
                 "Volume": 2000})
    return _df(rows)


def _unconfirmed_breakout_df() -> pd.DataFrame:
    """Direnç üstüne çıkış ama hacim teyitsiz — TEK kural tetiklenir."""
    rows = _flat_rows(25, price=100, vol=1000)  # 20g tepe ~101
    rows.append({"Open": 101, "High": 103, "Low": 101, "Close": 102.5,
                 "Volume": 1000})  # vol_ratio ~1.0 < 1.2
    return _df(rows)


def _weak_continuation_df() -> pd.DataFrame:
    """Dün güçlü+yüksek hacim, bugün zayıf hacim."""
    rows = _flat_rows(23, price=100, vol=1000)
    # dün: +8% güçlü, çok yüksek hacim
    rows.append({"Open": 101, "High": 120, "Low": 100, "Close": 108,
                 "Volume": 10000})
    # bugün: hacim çok düşük, tepe aşılamadı, zayıf kapanış
    rows.append({"Open": 110, "High": 121, "Low": 110, "Close": 113,
                 "Volume": 3000})
    return _df(rows)


def _triple_df() -> pd.DataFrame:
    """Aynı gün 3 kuralı tetikler: weak_pre_limit + close_selloff +
    weak_continuation (OR-bazlı eşleşmenin kanıtı)."""
    return _weak_continuation_df()


def _split_peak_df() -> pd.DataFrame:
    """52h zirveye yakın + güçlü 60g yükseliş (>%40) + yüksek hacim."""
    rows = []
    # ilk 10 satır düşük taban (~65) — 60g öncesi referans
    for _ in range(10):
        rows.append({"Open": 65, "High": 65.5, "Low": 64.5, "Close": 65,
                     "Volume": 1000})
    # 54 satır kademeli yükseliş 65 → ~100
    for i in range(54):
        p = 65.0 + (i + 1) * 0.65
        rows.append({"Open": p, "High": p + 0.5, "Low": p - 0.5,
                     "Close": p, "Volume": 1000})
    # bugün — zirve, yüksek hacim
    rows.append({"Open": 100, "High": 101, "Low": 99.5, "Close": 100.5,
                 "Volume": 2000})
    return _df(rows)


# ════════════════════════════════════════════════════════════════
# 1. Kayıt: 10 kural
# ════════════════════════════════════════════════════════════════
class TestRegistry:
    def test_exactly_ten_rules(self):
        assert len(WARNING_REGISTRY) == 10

    def test_rule_library_returns_ten(self):
        lib = get_rule_library()
        assert len(lib) == 10

    def test_all_warning_ids_unique(self):
        ids = [d.warning_id for d in WARNING_REGISTRY]
        assert len(ids) == len(set(ids))

    def test_required_rule_ids_present(self):
        ids = {d.warning_id for d in WARNING_REGISTRY}
        expected = {
            "weak_pre_limit", "base_rebound", "hold_above_open",
            "pressure_below_open", "split_at_peak", "market_rotation",
            "weak_continuation", "close_selloff", "unconfirmed_breakout",
            "strong_vs_index",
        }
        assert ids == expected

    def test_every_rule_has_required_fields(self):
        for d in WARNING_REGISTRY:
            assert d.severity_default in ("info", "watch", "warning", "high_risk")
            assert d.direction in ("risk", "reaction", "strength", "context")
            assert isinstance(d.requires_intraday, bool)
            assert isinstance(d.requires_corporate_action, bool)
            assert isinstance(d.data_requirements, list)
            assert isinstance(d.display_order, int)
            assert d.label_tr and d.user_copy_tr

    def test_rule_library_sorted_by_display_order(self):
        orders = [r["display_order"] for r in get_rule_library()]
        assert orders == sorted(orders)


# ════════════════════════════════════════════════════════════════
# 2. Her kural BAĞIMSIZ değerlendirilir
# ════════════════════════════════════════════════════════════════
class TestRulesIndependent:
    def test_each_daily_rule_fires_on_its_own_scenario(self):
        cases = {
            "weak_pre_limit": _weak_pre_limit_df(),
            "base_rebound": _base_rebound_df(),
            "close_selloff": _close_selloff_df(),
            "unconfirmed_breakout": _unconfirmed_breakout_df(),
            "weak_continuation": _weak_continuation_df(),
        }
        for rule_id, df in cases.items():
            ws = ENGINE.evaluate_ticker("TEST", df)
            ids = {w.warning_id for w in ws}
            assert rule_id in ids, f"{rule_id} kendi senaryosunda tetiklenmedi"

    def test_strong_vs_index_fires_with_index_return(self):
        rows = _flat_rows(25)
        rows.append({"Open": 100, "High": 103, "Low": 100, "Close": 102,
                     "Volume": 1200})
        ws = ENGINE.evaluate_ticker("TEST", _df(rows), index_return_1d=-0.02)
        assert "strong_vs_index" in {w.warning_id for w in ws}

    def test_strong_vs_index_silent_without_index(self):
        rows = _flat_rows(25)
        rows.append({"Open": 100, "High": 103, "Low": 100, "Close": 102,
                     "Volume": 1200})
        ws = ENGINE.evaluate_ticker("TEST", _df(rows))  # index yok
        assert "strong_vs_index" not in {w.warning_id for w in ws}


# ════════════════════════════════════════════════════════════════
# 3. OR-bazlı eşleşme
# ════════════════════════════════════════════════════════════════
class TestOrMatching:
    def test_one_rule_one_warning(self):
        ws = ENGINE.evaluate_ticker("TEST", _unconfirmed_breakout_df())
        assert len(ws) == 1
        assert ws[0].warning_id == "unconfirmed_breakout"

    def test_three_rules_three_warnings(self):
        ws = ENGINE.evaluate_ticker("TEST", _triple_df())
        ids = {w.warning_id for w in ws}
        assert len(ws) == 3
        assert ids == {"weak_pre_limit", "close_selloff", "weak_continuation"}

    def test_zero_rules_ticker_omitted(self):
        result = ENGINE.evaluate_universe({"BORING": _boring_df()})
        tickers = {g["ticker"] for g in result["warnings_by_ticker"]}
        assert "BORING" not in tickers

    def test_zero_rules_empty_list(self):
        ws = ENGINE.evaluate_ticker("BORING", _boring_df())
        assert ws == []


# ════════════════════════════════════════════════════════════════
# 4. Hisseye göre gruplama
# ════════════════════════════════════════════════════════════════
class TestGrouping:
    def test_warnings_grouped_by_ticker(self):
        universe = {
            "AAA": _weak_pre_limit_df(),
            "BBB": _unconfirmed_breakout_df(),
            "CCC": _boring_df(),
        }
        result = ENGINE.evaluate_universe(universe)
        groups = {g["ticker"]: g for g in result["warnings_by_ticker"]}
        assert "AAA" in groups and "BBB" in groups
        assert "CCC" not in groups            # uyarısız hisse listede yok
        assert groups["BBB"]["warning_count"] == 1

    def test_group_has_count_and_highest_severity(self):
        result = ENGINE.evaluate_universe({"AAA": _triple_df()})
        g = result["warnings_by_ticker"][0]
        assert g["ticker"] == "AAA"
        assert g["warning_count"] == 3
        assert g["highest_severity"] in ("info", "watch", "warning", "high_risk")
        assert len(g["warnings"]) == 3


# ════════════════════════════════════════════════════════════════
# 5. Özet sayımları
# ════════════════════════════════════════════════════════════════
class TestSummary:
    def test_summary_has_all_keys(self):
        result = ENGINE.evaluate_universe({"AAA": _weak_pre_limit_df()})
        s = result["summary"]
        for k in ("total_warnings", "tickers_with_warnings", "high_risk",
                  "warning", "watch", "info"):
            assert k in s

    def test_summary_counts_consistent(self):
        universe = {
            "AAA": _triple_df(),              # 3 uyarı
            "BBB": _unconfirmed_breakout_df(),  # 1 uyarı (watch)
            "CCC": _boring_df(),              # 0
        }
        result = ENGINE.evaluate_universe(universe)
        s = result["summary"]
        assert s["total_warnings"] == 4
        assert s["tickers_with_warnings"] == 2
        # Seviye sayıları toplamı = toplam uyarı
        assert s["high_risk"] + s["warning"] + s["watch"] + s["info"] \
            == s["total_warnings"]


# ════════════════════════════════════════════════════════════════
# 6. Intraday kuralları veri yokken susar
# ════════════════════════════════════════════════════════════════
class TestIntradayInactive:
    def test_intraday_rules_never_emit_without_intraday(self):
        # Hangi df olursa olsun intraday kuralları canlı uyarı vermez
        for df in (_weak_pre_limit_df(), _triple_df(), _boring_df()):
            ws = ENGINE.evaluate_ticker("TEST", df, intraday_df=None)
            ids = {w.warning_id for w in ws}
            assert "hold_above_open" not in ids
            assert "pressure_below_open" not in ids

    def test_intraday_rules_in_library_marked(self):
        for rid in ("hold_above_open", "pressure_below_open"):
            d = get_definition(rid)
            assert d.requires_intraday is True
            assert d.unavailable_reason_tr  # neden gösterilebilir


# ════════════════════════════════════════════════════════════════
# 7. Kurumsal-olay kuralı kanıt olmadan susar
# ════════════════════════════════════════════════════════════════
class TestCorporateActionGated:
    def test_split_silent_without_corporate_action(self):
        ws = ENGINE.evaluate_ticker("TEST", _split_peak_df(),
                                    corporate_actions=None)
        assert "split_at_peak" not in {w.warning_id for w in ws}

    def test_split_emits_with_corporate_action(self):
        ca = {"TEST": {"type": "bedelsiz"}}
        ws = ENGINE.evaluate_ticker("TEST", _split_peak_df(),
                                    corporate_actions=ca)
        assert "split_at_peak" in {w.warning_id for w in ws}

    def test_split_rule_marked_corporate(self):
        d = get_definition("split_at_peak")
        assert d.requires_corporate_action is True


# ════════════════════════════════════════════════════════════════
# 8. Günlük kurallar sentetik OHLCV'de doğru tetiklenir
# ════════════════════════════════════════════════════════════════
class TestDailyRulesSynthetic:
    def test_weak_pre_limit_evidence(self):
        ws = ENGINE.evaluate_ticker("TEST", _weak_pre_limit_df())
        w = next(w for w in ws if w.warning_id == "weak_pre_limit")
        assert w.severity == "warning"
        assert "volume_ratio" in w.evidence
        assert w.data_available is True

    def test_base_rebound_severity(self):
        ws = ENGINE.evaluate_ticker("TEST", _base_rebound_df())
        w = next(w for w in ws if w.warning_id == "base_rebound")
        assert w.severity == "watch"
        assert w.direction == "reaction"

    def test_insufficient_rows_no_warnings(self):
        short = _df(_flat_rows(5))
        assert ENGINE.evaluate_ticker("TEST", short) == []

    def test_market_rotation_sideways(self):
        # Yatay endeks: 22 bar düz → ret_20d ≈ 0 → sideways
        idx = _df(_flat_rows(24, price=1000, vol=0))
        result = ENGINE.evaluate_universe({}, index_df=idx)
        mkt = [g for g in result["warnings_by_ticker"] if g["ticker"] == "PİYASA"]
        assert mkt and mkt[0]["warnings"][0]["warning_id"] == "market_rotation"


# ════════════════════════════════════════════════════════════════
# 9. API şeması
# ════════════════════════════════════════════════════════════════
class TestApiSchema:
    def test_build_payload_shape(self):
        from api.tahtalab import _build_payload
        p = _build_payload()
        for k in ("asof", "data_status", "summary", "warnings_by_ticker",
                  "rules"):
            assert k in p
        assert isinstance(p["warnings_by_ticker"], list)
        assert len(p["rules"]) == 10
        for k in ("daily_available", "intraday_available",
                  "corporate_actions_available"):
            assert k in p["data_status"]
        # v1: intraday + kurumsal olay verisi yok
        assert p["data_status"]["intraday_available"] is False
        assert p["data_status"]["corporate_actions_available"] is False

    def test_router_has_both_routes(self):
        from api.tahtalab import router
        paths = {r.path for r in router.routes}
        assert "/api/tahtalab" in paths
        assert "/api/tahtalab/{ticker}" in paths


# ════════════════════════════════════════════════════════════════
# 10. Frontend nav TahtaLab içeriyor
# ════════════════════════════════════════════════════════════════
class TestFrontend:
    @pytest.fixture(scope="class")
    def terminal_js(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "static",
                               "terminal.js"), "r", encoding="utf-8") as fh:
            return fh.read()

    @pytest.fixture(scope="class")
    def index_html(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "index.html"),
                  "r", encoding="utf-8") as fh:
            return fh.read()

    def test_pages_array_has_tahtalab(self, terminal_js):
        assert "id:'tahtalab'" in terminal_js
        assert "TahtaLab" in terminal_js

    def test_render_function_exists(self, terminal_js):
        assert "function renderTahtaLabPage" in terminal_js
        assert "if(id==='tahtalab')renderTahtaLabPage()" in terminal_js

    def test_page_div_present(self, index_html):
        assert 'id="pg-tahtalab"' in index_html


# ════════════════════════════════════════════════════════════════
# 11. TahtaLab kullanıcı-yüzü kopyada yasak kelime yok
# ════════════════════════════════════════════════════════════════
class TestNoBannedWords:
    BANNED = ["sinyal", "hedef", "garanti", "kesin", "manipülasyon",
              "tahtacı yaptı", "para kazandırır"]

    def _tahtalab_copy(self) -> str:
        base = os.path.join(os.path.dirname(__file__), "..")
        parts = []
        for rel in ("engine/tahta_warning_registry.py",
                    "engine/tahta_warnings.py"):
            with open(os.path.join(base, rel), "r", encoding="utf-8") as fh:
                parts.append(fh.read())
        # terminal.js — yalnız TahtaLab render bölümü
        with open(os.path.join(base, "static", "terminal.js"),
                  "r", encoding="utf-8") as fh:
            js = fh.read()
        a = js.find("// ===== TAHTALAB")
        b = js.find("function renderMakroPage(){const pg")
        parts.append(js[a:b])
        return "\n".join(parts).lower()

    def test_no_banned_words_in_copy(self):
        copy = self._tahtalab_copy()
        for word in self.BANNED:
            assert word not in copy, f"yasak kelime bulundu: {word!r}"

    def test_disclaimer_phrase_allowed(self):
        # "al/sat önerisi değildir" yalnız açıklama/disclaimer'da serbest
        copy = self._tahtalab_copy()
        assert "al/sat önerisi" in copy  # disclaimer mevcut


# ════════════════════════════════════════════════════════════════
# Ek — motor sağlamlığı
# ════════════════════════════════════════════════════════════════
class TestEngineRobustness:
    def test_engine_handles_none_df(self):
        assert ENGINE.evaluate_ticker("TEST", None) == []

    def test_engine_handles_empty_universe(self):
        result = ENGINE.evaluate_universe({})
        assert result["summary"]["total_warnings"] == 0
        assert result["warnings_by_ticker"] == []

    def test_get_rule_library_is_serializable(self):
        import json
        json.dumps(ENGINE.get_rule_library())

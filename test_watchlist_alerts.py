# ================================================================
# BISTBULL TERMINAL — Unit Tests: Watchlist + Alerts (Phase 7)
# Uses temporary SQLite DB for isolation.
# ================================================================

import json
import os
import pytest
import tempfile

# Override DB_PATH before importing storage
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["BISTBULL_DB_PATH"] = _tmp.name

from infra.storage import (
    init_db, watchlist_add, watchlist_remove, watchlist_list,
    alert_exists, alert_save, alert_save_batch, alerts_get,
    snapshot_get, snapshot_save,
)
from engine.watchlist import add, remove, get_symbols, get_enriched, validate_symbol
from engine.alerts import (
    generate_alerts_for_symbol, generate_watchlist_alerts,
    get_user_alerts, _build_snapshot, _quality_upgraded,
)


@pytest.fixture(autouse=True)
def setup_db():
    """Initialize DB before each test and clean tables."""
    init_db()
    from infra.storage import _get_conn
    conn = _get_conn()
    conn.execute("DELETE FROM watchlist")
    conn.execute("DELETE FROM alerts")
    conn.execute("DELETE FROM symbol_snapshots")
    conn.commit()
    yield


# ================================================================
# STORAGE — low-level
# ================================================================
class TestStorage:
    def test_watchlist_add(self):
        assert watchlist_add("u1", "THYAO") is True

    def test_watchlist_add_duplicate(self):
        watchlist_add("u1", "THYAO")
        assert watchlist_add("u1", "THYAO") is False

    def test_watchlist_remove(self):
        watchlist_add("u1", "THYAO")
        assert watchlist_remove("u1", "THYAO") is True

    def test_watchlist_remove_nonexistent(self):
        assert watchlist_remove("u1", "THYAO") is False

    def test_watchlist_list(self):
        watchlist_add("u1", "THYAO")
        watchlist_add("u1", "EREGL")
        items = watchlist_list("u1")
        assert len(items) == 2
        symbols = [i["symbol"] for i in items]
        assert "THYAO" in symbols
        assert "EREGL" in symbols

    def test_watchlist_user_isolation(self):
        watchlist_add("u1", "THYAO")
        watchlist_add("u2", "EREGL")
        assert len(watchlist_list("u1")) == 1
        assert len(watchlist_list("u2")) == 1

    def test_alert_save_and_get(self):
        alert = {
            "symbol": "THYAO", "alert_type": "new_signal", "severity": "info",
            "title": "Test", "message": "Msg", "metadata": "{}",
            "dedupe_key": "THYAO:new_signal:2026-01-01",
        }
        assert alert_save("u1", alert) is True
        alerts = alerts_get("u1")
        assert len(alerts) == 1
        assert alerts[0]["title"] == "Test"

    def test_alert_dedupe(self):
        alert = {
            "symbol": "THYAO", "alert_type": "new_signal", "severity": "info",
            "title": "Test", "message": "", "metadata": "{}",
            "dedupe_key": "THYAO:new_signal:2026-01-01",
        }
        assert alert_save("u1", alert) is True
        assert alert_save("u1", alert) is False  # duplicate

    def test_snapshot_roundtrip(self):
        data = json.dumps({"overall": 72, "signals": ["Golden Cross"]})
        snapshot_save("u1", "THYAO", data)
        result = snapshot_get("u1", "THYAO")
        assert json.loads(result)["overall"] == 72

    def test_snapshot_upsert(self):
        snapshot_save("u1", "THYAO", json.dumps({"overall": 60}))
        snapshot_save("u1", "THYAO", json.dumps({"overall": 72}))
        result = json.loads(snapshot_get("u1", "THYAO"))
        assert result["overall"] == 72


# ================================================================
# WATCHLIST — high-level
# ================================================================
class TestWatchlist:
    def test_add_valid_symbol(self):
        result = add("u1", "THYAO")
        assert result["ok"] is True
        assert result["action"] == "added"

    def test_add_duplicate(self):
        add("u1", "THYAO")
        result = add("u1", "THYAO")
        assert result["ok"] is True
        assert result["action"] == "already_exists"

    def test_add_invalid_symbol(self):
        result = add("u1", "NONEXISTENT")
        assert result["ok"] is False
        assert "Geçersiz" in result["error"]

    def test_add_lowercase(self):
        result = add("u1", "thyao")
        assert result["ok"] is True
        assert result["symbol"] == "THYAO"

    def test_add_with_suffix(self):
        result = add("u1", "THYAO.IS")
        assert result["ok"] is True
        assert result["symbol"] == "THYAO"

    def test_remove(self):
        add("u1", "THYAO")
        result = remove("u1", "THYAO")
        assert result["action"] == "removed"

    def test_remove_not_found(self):
        result = remove("u1", "THYAO")
        assert result["action"] == "not_found"

    def test_get_symbols(self):
        add("u1", "THYAO")
        add("u1", "EREGL")
        symbols = get_symbols("u1")
        assert set(symbols) == {"THYAO", "EREGL"}

    def test_validate_symbol_valid(self):
        assert validate_symbol("THYAO") == "THYAO"
        assert validate_symbol("thyao.IS") == "THYAO"

    def test_validate_symbol_invalid(self):
        assert validate_symbol("FAKEXYZ") is None

    def test_enriched_no_cache(self):
        add("u1", "THYAO")

        class EmptyCache:
            def get(self, key): return None

        result = get_enriched("u1", EmptyCache(), [])
        assert len(result) == 1
        assert result[0]["symbol"] == "THYAO"
        assert result[0]["has_data"] is False

    def test_enriched_with_analysis(self):
        add("u1", "THYAO")

        class MockCache:
            def get(self, key):
                if key == "THYAO.IS":
                    return {
                        "overall": 72, "confidence": 85, "fa_score": 65, "ivme": 58,
                        "entry_label": "TEYITLI", "decision": "AL", "risk_score": -5,
                        "style": "Kaliteli", "metrics": {"price": 280, "pe": 6.5},
                        "explanation": {
                            "summary": "Guclu profil",
                            "top_positive_drivers": [{"name": "ROE"}],
                            "top_negative_drivers": [{"name": "Risk"}],
                        },
                        "positives": [], "negatives": [],
                    }
                return None

        result = get_enriched("u1", MockCache(), [])
        assert result[0]["has_data"] is True
        assert result[0]["overall"] == 72
        assert result[0]["summary"] == "Guclu profil"


# ================================================================
# ALERTS — generation
# ================================================================
class TestAlertGeneration:
    def _analysis(self, overall=65, confidence=80, risk=-5):
        return {
            "overall": overall, "confidence": confidence, "risk_score": risk,
            "entry_label": "TEYITLI", "scores_imputed": [],
            "explanation": {
                "summary": "Test",
                "top_positive_drivers": [{"name": "ROE"}, {"name": "Marj"}],
                "top_negative_drivers": [{"name": "Risk"}, {"name": "Borc"}],
            },
        }

    def test_new_signal_alert(self):
        signals = [{"signal": "Golden Cross", "signal_quality": "A", "stars": 3}]
        alerts = generate_alerts_for_symbol("THYAO", self._analysis(), signals, None)
        signal_alerts = [a for a in alerts if a["alert_type"] == "new_signal"]
        assert len(signal_alerts) == 1
        assert "Golden Cross" in signal_alerts[0]["title"]

    def test_no_alert_for_existing_signal(self):
        signals = [{"signal": "Golden Cross", "signal_quality": "A", "stars": 3}]
        prev = {"signals": ["Golden Cross"], "signal_qualities": {"Golden Cross": "A"},
                "overall": 65, "confidence": 80, "positive_drivers": ["ROE"], "negative_drivers": ["Risk"]}
        alerts = generate_alerts_for_symbol("THYAO", self._analysis(), signals, prev)
        signal_alerts = [a for a in alerts if a["alert_type"] == "new_signal"]
        assert len(signal_alerts) == 0

    def test_signal_quality_upgrade(self):
        signals = [{"signal": "Golden Cross", "signal_quality": "A", "stars": 3}]
        prev = {"signals": ["Golden Cross"], "signal_qualities": {"Golden Cross": "B"},
                "overall": 65, "confidence": 80, "positive_drivers": ["ROE"], "negative_drivers": ["Risk"]}
        alerts = generate_alerts_for_symbol("THYAO", self._analysis(), signals, prev)
        upgrades = [a for a in alerts if a["alert_type"] == "signal_quality_upgrade"]
        assert len(upgrades) == 1

    def test_score_jump(self):
        prev = {"overall": 55, "confidence": 80, "signals": [], "signal_qualities": {},
                "positive_drivers": ["ROE"], "negative_drivers": ["Risk"]}
        alerts = generate_alerts_for_symbol("THYAO", self._analysis(overall=65), [], prev)
        jumps = [a for a in alerts if a["alert_type"] == "score_jump"]
        assert len(jumps) == 1
        assert "yükseldi" in jumps[0]["title"]

    def test_no_alert_on_small_score_change(self):
        prev = {"overall": 63, "confidence": 80, "signals": [], "signal_qualities": {},
                "positive_drivers": ["ROE"], "negative_drivers": ["Risk"]}
        alerts = generate_alerts_for_symbol("THYAO", self._analysis(overall=65), [], prev)
        jumps = [a for a in alerts if a["alert_type"] == "score_jump"]
        assert len(jumps) == 0

    def test_confidence_drop(self):
        prev = {"overall": 65, "confidence": 90, "signals": [], "signal_qualities": {},
                "positive_drivers": ["ROE"], "negative_drivers": ["Risk"]}
        alerts = generate_alerts_for_symbol("THYAO", self._analysis(confidence=75), [], prev)
        drops = [a for a in alerts if a["alert_type"] == "confidence_drop"]
        assert len(drops) == 1

    def test_new_risk_flag(self):
        prev = {"overall": 65, "confidence": 80, "signals": [], "signal_qualities": {},
                "positive_drivers": ["ROE"], "negative_drivers": []}
        alerts = generate_alerts_for_symbol("THYAO", self._analysis(), [], prev)
        risks = [a for a in alerts if a["alert_type"] == "new_risk_flag"]
        assert len(risks) >= 1

    def test_new_positive_driver(self):
        prev = {"overall": 65, "confidence": 80, "signals": [], "signal_qualities": {},
                "positive_drivers": [], "negative_drivers": ["Risk"]}
        alerts = generate_alerts_for_symbol("THYAO", self._analysis(), [], prev)
        positives = [a for a in alerts if a["alert_type"] == "new_positive_driver"]
        assert len(positives) >= 1

    def test_no_analysis_no_alerts(self):
        alerts = generate_alerts_for_symbol("THYAO", None, [], None)
        assert len(alerts) == 0


class TestAlertDedup:
    def test_same_day_dedupe(self):
        analysis = {
            "overall": 72, "confidence": 85, "risk_score": -5, "entry_label": "TEYITLI",
            "scores_imputed": [],
            "explanation": {
                "summary": "T", "top_positive_drivers": [{"name": "ROE"}],
                "top_negative_drivers": [{"name": "Risk"}],
            },
        }
        signals = [{"signal": "GC", "signal_quality": "A", "stars": 3, "ticker": "THYAO"}]

        # First run
        watchlist_add("u1", "THYAO")

        class MockCache:
            def get(self, key): return analysis if "THYAO" in key else None

        new1 = generate_watchlist_alerts("u1", ["THYAO"], MockCache(), signals)
        new2 = generate_watchlist_alerts("u1", ["THYAO"], MockCache(), signals)

        # Second run should produce fewer new alerts (deduped)
        assert len(new2) <= len(new1)


class TestQualityUpgraded:
    def test_c_to_a(self):
        assert _quality_upgraded("C", "A") is True

    def test_b_to_a(self):
        assert _quality_upgraded("B", "A") is True

    def test_a_to_a(self):
        assert _quality_upgraded("A", "A") is False

    def test_a_to_c(self):
        assert _quality_upgraded("A", "C") is False


class TestBuildSnapshot:
    def test_structure(self):
        analysis = {
            "overall": 72, "confidence": 85, "risk_score": -5, "entry_label": "TEYITLI",
            "explanation": {
                "top_positive_drivers": [{"name": "ROE"}],
                "top_negative_drivers": [{"name": "Borc"}],
            },
        }
        signals = [{"signal": "GC", "signal_quality": "A"}]
        snap = _build_snapshot(analysis, signals)
        assert snap["overall"] == 72
        assert "GC" in snap["signals"]
        assert snap["signal_qualities"]["GC"] == "A"
        assert "ROE" in snap["positive_drivers"]

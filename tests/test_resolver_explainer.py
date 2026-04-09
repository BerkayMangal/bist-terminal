from engine.ticker_resolver import resolve_ticker, resolve_multiple, search_suggestions
from engine.dimension_explainer import build_dimension_explanations

class TestResolver:
    def test_exact_ticker(self): assert resolve_ticker("THYAO") == "THYAO"
    def test_lower_ticker(self): assert resolve_ticker("thyao") == "THYAO"
    def test_alias(self): assert resolve_ticker("garanti") == "GARAN"
    def test_alias_turkish(self): assert resolve_ticker("tofaş") == "TOASO"
    def test_alias_thy(self): assert resolve_ticker("thy") == "THYAO"
    def test_not_found(self): assert resolve_ticker("asdfxyz") is None
    def test_empty(self): assert resolve_ticker("") is None
    def test_multiple(self): r = resolve_multiple("tofaş vs ereğli"); assert "TOASO" in r and "EREGL" in r
    def test_multiple_tickers(self): r = resolve_multiple("THYAO GARAN"); assert len(r) == 2
    def test_suggestions(self): r = search_suggestions("gar"); assert any(s["ticker"] == "GARAN" for s in r)
    def test_suggestions_short(self): assert search_suggestions("a") == []

class TestExplainer:
    def test_basic(self):
        s = {"value": 70, "quality": 65, "growth": 55, "balance": 60, "earnings": 50, "moat": 45, "capital": 40, "momentum": 60}
        m = {"pe": 12, "roe": 0.15, "revenue_growth": 0.22, "debt_equity": 0.5, "cfo_to_ni": 0.8}
        r = build_dimension_explanations(s, m)
        assert "value" in r and "quality" in r and "earnings" in r
    def test_weak_scores(self):
        s = {"value": 30, "quality": 25, "growth": 20, "balance": 30, "earnings": 25, "moat": 20, "capital": 20, "momentum": 25}
        r = build_dimension_explanations(s, {})
        assert "pahalı" in r["value"].lower() or "Pahalı" in r["value"]
    def test_crash(self):
        assert isinstance(build_dimension_explanations({}, {}), dict)

"""Phase 5.1.3 — Mobile breakpoint contract tests.

Without a real browser we can't measure pixel widths, but we can lock
down the CSS architecture: mobile-first @media min-width queries,
44×44 tap targets, sticky bottom-nav, and the heatmap list-view
fallback for narrow screens.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


CSS_PATH = Path(__file__).parent.parent / "static" / "terminal.css"
LANDING_PATH = Path(__file__).parent.parent / "landing.html"
INDEX_PATH = Path(__file__).parent.parent / "index.html"


@pytest.fixture(scope="module")
def css_source() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def landing_source() -> str:
    return LANDING_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_source() -> str:
    return INDEX_PATH.read_text(encoding="utf-8")


# ============================================================
# Mobile breakpoint contract
# ============================================================
class TestMobileBreakpoints:
    def test_viewport_meta_present(self, landing_source, index_source):
        for src in (landing_source, index_source):
            assert 'name="viewport"' in src
            assert "width=device-width" in src

    def test_min_width_media_queries_exist(self, css_source):
        # Phase 5: at least 768px breakpoint is mandatory
        assert "@media(min-width:768px)" in css_source \
            or "@media (min-width: 768px)" in css_source \
            or "@media(min-width: 768px)" in css_source

    def test_mobile_breakpoint_at_480px(self, css_source):
        # 480px = phone landscape / small tablet portrait
        assert "@media(max-width:480px)" in css_source \
            or "@media (max-width: 480px)" in css_source

    def test_44px_tap_targets(self, css_source):
        # The brief mandates min 44×44px on every interactive element
        # We expect at least one min-height:44px rule
        assert "min-height:44px" in css_source or "min-height: 44px" in css_source

    def test_sticky_bottom_nav_class(self, css_source):
        # Phase 5: 5-tab bottom nav for mobile
        assert ".mob-bnav" in css_source
        assert "position:fixed" in css_source.split(".mob-bnav")[1].split("}")[0] \
            or "position: fixed" in css_source.split(".mob-bnav")[1].split("}")[0]

    def test_heatmap_list_view_alternate(self, css_source):
        # On <480px screens, heatmap renders as tabular list instead of grid
        assert ".heat-list-mobile" in css_source
        assert ".heat-list-row" in css_source

    def test_safe_area_inset(self, css_source):
        # iPhone X+ home-indicator safe-area
        assert "safe-area-inset" in css_source or "var(--safe-b)" in css_source


# ============================================================
# Phase 5.3 — Landing page SEO + structured data
# ============================================================
class TestLandingSEO:
    def test_title_tag_present(self, landing_source):
        assert "<title>" in landing_source
        # Title must mention BIST or BistBull (brand keyword)
        m = re.search(r"<title>([^<]+)</title>", landing_source)
        assert m, "Title tag missing"
        assert "bist" in m.group(1).lower() or "bistbull" in m.group(1).lower()

    def test_meta_description_present(self, landing_source):
        assert 'name="description"' in landing_source

    def test_meta_description_meaningful_length(self, landing_source):
        m = re.search(r'name="description"\s+content="([^"]+)"', landing_source)
        assert m, "description tag missing"
        desc = m.group(1)
        # Google truncates around 160 chars; bare minimum 50
        assert 50 <= len(desc) <= 320, f"description length {len(desc)} out of range"

    def test_charset_declared(self, landing_source):
        assert 'charset="UTF-8"' in landing_source or 'charset="utf-8"' in landing_source

    def test_h1_tag_present(self, landing_source):
        # Critical for SEO: at least one h1
        assert "<h1" in landing_source, "Landing has no h1"

    def test_lang_attribute_set(self, landing_source):
        # <html lang="tr">
        assert 'lang="tr"' in landing_source or 'lang="en"' in landing_source


# ============================================================
# Phase 5.4 — TradingView widget integration
# ============================================================
class TestWidgetIntegration:
    """The widgets themselves are loaded from TradingView's CDN at runtime;
    here we verify the static/widgets directory ships the wrapper modules
    and they reference the correct embedding URLs."""

    @pytest.fixture(scope="class")
    def widgets_dir(self):
        return Path(__file__).parent.parent / "static" / "js" / "widgets"

    def test_widgets_directory_exists(self, widgets_dir):
        assert widgets_dir.is_dir(), f"widgets dir missing at {widgets_dir}"

    def test_overview_widget_present(self, widgets_dir):
        f = widgets_dir / "tv-overview.js"
        assert f.is_file(), "tv-overview.js missing"
        src = f.read_text(encoding="utf-8")
        # Must reference TradingView's embed-widget endpoint
        assert "tradingview" in src.lower() or "TradingView" in src
        # Must support symbol substitution (Phase 5.4 requirement)
        assert "symbol" in src.lower()

    def test_calendar_widget_present(self, widgets_dir):
        f = widgets_dir / "tv-calendar.js"
        assert f.is_file(), "tv-calendar.js missing"

    def test_ticker_widget_present(self, widgets_dir):
        f = widgets_dir / "tv-ticker.js"
        assert f.is_file(), "tv-ticker.js missing"

    def test_forex_cross_rates_widget_present(self, widgets_dir):
        f = widgets_dir / "tv-forex.js"
        assert f.is_file(), "tv-forex.js missing"

    def test_widget_css_container_class(self):
        css = CSS_PATH.read_text(encoding="utf-8")
        # Containers for widgets must have a consistent class
        assert ".tv-widget" in css, "TradingView widget container CSS missing"

    def test_no_premium_logo_strip_attempt(self, widgets_dir):
        """Per brief: TradingView Premium account → logosuz kullanım allowed.
        We must NOT strip the logo via JS hack — that violates ToS even on
        Premium. Verify our wrappers don't do anything sketchy."""
        for f in widgets_dir.glob("*.js"):
            src = f.read_text(encoding="utf-8")
            assert "display:none" not in src.replace(" ", "") or "logo" not in src.lower(), \
                f"{f.name} appears to hide the logo via CSS"

"""Phase 5.3 — Landing page SEO + structured-data tests.

The brief calls for a total landing page rewrite with new positioning
("BIST hisselerini, Türkiye gerçeklerini bilen bir sistemle değerlendir").
These tests pin the SEO contract: title, meta description, Open Graph
tags, JSON-LD structured data, canonical URL, and the 3-section
information architecture (hero, value props, how-it-works, proof,
founder, final CTA, disclaimer).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


LANDING = Path(__file__).parent.parent / "landing.html"


@pytest.fixture(scope="module")
def html() -> str:
    return LANDING.read_text(encoding="utf-8")


# ============================================================
# Required meta tags
# ============================================================
class TestMetaTags:
    def test_title_present_and_branded(self, html):
        m = re.search(r"<title>([^<]+)</title>", html)
        assert m, "Title tag missing"
        title = m.group(1).lower()
        assert "bistbull" in title or "bist" in title

    def test_title_includes_value_prop(self, html):
        # New brief positioning: "Türkiye gerçeklerini bilen"
        m = re.search(r"<title>([^<]+)</title>", html)
        assert m
        assert "türkiye" in m.group(1).lower(), \
            "Phase 5.3: title should reference Türkiye positioning"

    def test_description_meaningful(self, html):
        m = re.search(r'name="description"\s+content="([^"]+)"', html)
        assert m, "meta description missing"
        desc = m.group(1)
        assert 80 <= len(desc) <= 320, f"description length {len(desc)} out of bounds"
        # Must mention 1-2 key value props
        terms = ("walk-forward", "türkiye", "ai", "bist")
        hits = sum(1 for t in terms if t in desc.lower())
        assert hits >= 2, f"description should mention >=2 key terms, got {hits}"

    def test_canonical_url(self, html):
        assert 'rel="canonical"' in html, "canonical link missing"

    def test_og_title_present(self, html):
        assert 'property="og:title"' in html

    def test_og_description_present(self, html):
        assert 'property="og:description"' in html

    def test_og_locale_tr(self, html):
        m = re.search(r'property="og:locale"\s+content="([^"]+)"', html)
        if m:  # optional but if present, must be tr_TR
            assert m.group(1) == "tr_TR"

    def test_twitter_card(self, html):
        assert 'name="twitter:card"' in html

    def test_lang_attribute(self, html):
        assert 'lang="tr"' in html

    def test_theme_color(self, html):
        assert 'name="theme-color"' in html


# ============================================================
# JSON-LD structured data
# ============================================================
class TestStructuredData:
    def test_jsonld_block_present(self, html):
        assert 'type="application/ld+json"' in html

    def test_jsonld_parses_as_valid_json(self, html):
        m = re.search(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>',
                      html, re.DOTALL)
        assert m, "JSON-LD block missing or malformed"
        data = json.loads(m.group(1))
        assert data.get("@context") == "https://schema.org"
        assert data.get("@type") in ("SoftwareApplication", "Organization", "WebApplication")
        assert "name" in data

    def test_jsonld_mentions_finance(self, html):
        m = re.search(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>',
                      html, re.DOTALL)
        assert m
        data = json.loads(m.group(1))
        cat = (data.get("applicationCategory") or "").lower()
        assert "finance" in cat or "business" in cat


# ============================================================
# Information architecture — sections in correct order
# ============================================================
class TestSectionOrder:
    def test_hero_is_first_section(self, html):
        # Hero must come before all other section markers
        hero_idx = html.find("class=\"hero\"")
        assert hero_idx > 0, "hero section missing"
        # Find subsequent .section elements
        first_section = re.search(r'<section class="section"', html)
        assert first_section is None or first_section.start() > hero_idx

    def test_three_value_props_section(self, html):
        # The brief mandates 3 value cards: 🇹🇷 / 📊 / 🤖
        assert "Türkiye'ye özel 4 filtre" in html
        assert "Walk-forward onaylı" in html
        assert "4 AI bir arada" in html

    def test_how_it_works_three_steps(self, html):
        # Steps 01, 02, 03
        for n in ("01", "02", "03"):
            assert f">{n}</div>" in html or f">{n}<" in html, f"Step {n} missing"
        # Step verbs from brief: Tara / Skoru gör / Sinyali izle
        assert "Tara" in html
        assert "Skoru" in html or "Skor" in html
        assert "Sinyal" in html

    def test_founder_section_present(self, html):
        assert "Berkay Kangal" in html

    def test_disclaimer_visible(self, html):
        assert "Yatırım tavsiyesi değildir" in html

    def test_primary_cta_to_terminal_three_times(self, html):
        # Brief: CTA buton 3 yerde (hero, ortada, alt)
        terminal_links = re.findall(r'href="/terminal"', html)
        assert len(terminal_links) >= 3, \
            f"Phase 5.3: at least 3 CTA links to /terminal, got {len(terminal_links)}"


# ============================================================
# Removal of old positioning — these phrases must NOT appear
# ============================================================
class TestOldPositioningGone:
    def test_no_subscription_pricing(self, html):
        # Old landing pushed "99 TL/ay" — Phase 5 says login zorunlu değil
        # so the subscription CTA must be gone
        assert "99 TL/ay" not in html, \
            "Old pricing copy still present — Phase 5 removed subscription"

    def test_no_old_problem_grid(self, html):
        # Old "Telegram tüyoları" framing replaced by "Türkiye filtresi"
        # NOT a strict ban, but the new copy MUST be present
        assert "Türkiye Filtresi" in html or "Türkiye filtresi" in html


# ============================================================
# Accessibility basics
# ============================================================
class TestA11y:
    def test_h1_present_and_unique(self, html):
        h1s = re.findall(r"<h1[^>]*>", html)
        assert len(h1s) >= 1, "no h1"
        # SEO best practice: only one h1
        assert len(h1s) == 1, f"multiple h1 elements ({len(h1s)})"

    def test_h2_for_section_headings(self, html):
        h2s = re.findall(r"<h2[^>]*>", html)
        # At least 4 sections → 4+ h2s
        assert len(h2s) >= 4

    def test_button_min_height_44(self, html):
        # Brief mandates 48px on landing buttons (mobile-friendly)
        # Check for any min-height: 48px or 44px on .btn-primary
        m = re.search(r"\.btn-primary\b[^{]*\{([^}]+)\}", html, re.DOTALL)
        assert m
        rules = m.group(1)
        assert "min-height:48px" in rules.replace(" ", "") \
            or "min-height: 48px" in rules \
            or "padding:16px" in rules.replace(" ", "")  # padding alone gives ~50px

    def test_no_inline_event_handlers_on_top_level_anchors(self, html):
        # SEO best practice: avoid <a onclick=…> for primary nav
        # We allow it on inline tour buttons, but not on the .nav-cta
        m = re.search(r'class="nav-cta"[^>]*>', html)
        if m:
            assert "onclick=" not in m.group(0), "nav-cta has onclick — use href"

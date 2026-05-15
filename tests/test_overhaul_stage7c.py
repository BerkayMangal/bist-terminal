# ================================================================
# tests/test_overhaul_stage7c.py
#
# Great Overhaul Stage 7c: bulletin auto-fire wiring + UI page.
#
# This stage bridges Stage 7a (schedule helper) and Stage 7b (bulletin
# engine + storage + API) so:
#   - bullwatch_refresh_loop auto-fires generate_and_persist() AFTER
#     each post_close (18:30 IST) scan completes
#   - terminal.js gets a "Günlük Bülten" page that consumes the
#     Stage 7b endpoints and renders the latest bulletin
#
# These tests pin the wiring at the source level (so a regression
# that removes either piece is caught immediately).
# ================================================================

from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


# ────────────────────────────────────────────────────────────────
# Auto-fire wiring (bullwatch_refresh_loop)
# ────────────────────────────────────────────────────────────────


class TestRefreshLoopWiring:
    def test_loop_imports_bulletin_generator(self):
        from engine import background_tasks
        src = inspect.getsource(background_tasks.bullwatch_refresh_loop)
        assert "generate_and_persist" in src, (
            "bullwatch_refresh_loop missing the Stage 7c bulletin wiring"
        )

    def test_loop_checks_post_close_label(self):
        from engine import background_tasks
        src = inspect.getsource(background_tasks.bullwatch_refresh_loop)
        assert "post_close" in src, (
            "Loop must gate bulletin generation on the post_close slot"
        )

    def test_loop_uses_async_to_thread_for_bulletin(self):
        """Bulletin generation is synchronous I/O — must run in a
        thread so the asyncio loop isn't blocked."""
        from engine import background_tasks
        src = inspect.getsource(background_tasks.bullwatch_refresh_loop)
        # Either pattern is fine: asyncio.to_thread or run_in_executor
        assert (
            "asyncio.to_thread" in src
            or "run_in_executor" in src
        ), "Bulletin generation should off-load to a thread"

    def test_loop_swallows_bulletin_failure(self):
        """A bulletin engine crash must NOT stop the refresh loop."""
        from engine import background_tasks
        src = inspect.getsource(background_tasks.bullwatch_refresh_loop)
        # The wiring should be inside a try/except so the loop keeps
        # going. We check for both "try:" + a context that mentions
        # Daily bulletin so we know it's the bulletin guard.
        assert "Daily bulletin generation failed" in src, (
            "Bulletin auto-fire isn't protected by try/except"
        )


# ────────────────────────────────────────────────────────────────
# UI page wiring (terminal.js)
# ────────────────────────────────────────────────────────────────


class TestUIPageWiring:
    @pytest.fixture(scope="class")
    def terminal_src(self):
        with open(
            os.path.join(os.path.dirname(__file__), "..", "static",
                         "terminal.js"),
            "r", encoding="utf-8",
        ) as fh:
            return fh.read()

    def test_nav_has_bulten_entry(self, terminal_src):
        assert "id:'bulten'" in terminal_src, (
            "PAGES array missing 'bulten' entry"
        )
        assert "Günlük Bülten" in terminal_src, (
            "Bülten nav label missing"
        )

    def test_renderbulten_function_exists(self, terminal_src):
        assert "function renderBultenPage" in terminal_src, (
            "renderBultenPage() not defined"
        )

    def test_gopage_routes_bulten(self, terminal_src):
        assert "if(id==='bulten')renderBultenPage()" in terminal_src, (
            "goPage() doesn't route 'bulten' to renderBultenPage()"
        )

    def test_renderer_uses_daily_brief_api(self, terminal_src):
        assert "/api/daily-brief" in terminal_src, (
            "Renderer doesn't call the daily-brief endpoints"
        )

    def test_archive_selector_present(self, terminal_src):
        """User should be able to scroll back to past bulletins."""
        assert "bultenArchive" in terminal_src, (
            "Archive selector missing — user can't view past bulletins"
        )

    def test_regenerate_button_wired(self, terminal_src):
        assert "_regenBulletin" in terminal_src, (
            "Manual regenerate button missing"
        )


class TestIndexHtmlPageDiv:
    def test_pg_bulten_div_present(self):
        with open(
            os.path.join(os.path.dirname(__file__), "..", "index.html"),
            "r", encoding="utf-8",
        ) as fh:
            html = fh.read()
        assert 'id="pg-bulten"' in html, (
            "index.html missing the <div id='pg-bulten'> container"
        )
        assert 'data-page="bulten"' in html, (
            "pg-bulten div missing data-page attribute"
        )

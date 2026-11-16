"""Pages + trail (ui/nav.py): a pushed page shows the trail from the tab it
was opened from, Escape goes up one level, and ``1``–``7`` close the pages
before switching tab (they used to switch the hidden tab underneath)."""

from __future__ import annotations

import asyncio

from conftest import StubClient

from qobit.qobuz.models import Album
from qobit.ui.screens.album_detail import AlbumScreen


async def _get_album(self, album_id):
    return {"id": album_id, "title": "Dry", "artist": {"name": "PJ Harvey"}}


def _labels(app):
    return [c.label for c in app.nav_trail()]


def test_page_trail_escape_and_tab_keys(ui_app, monkeypatch):
    app = ui_app
    monkeypatch.setattr(StubClient, "get_album", _get_album, raising=False)
    album = Album(id="5", title="Dry", artist="PJ Harvey", year=None, tracks_count=0)

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("7")
            await pilot.pause()
            app.push_screen(AlbumScreen(album))
            await pilot.pause()
            assert _labels(app) == ["Search", "Dry"]
            # The page's own tab bar lights the tab it came from.
            assert app.screen.query_one("PageTabs").active == "page-tab-search"
            # Escape closes the page.
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert app.base_tab() == "search"
            # A tab key on a page closes it, then switches.
            app.push_screen(AlbumScreen(album))
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert app.base_tab() == "albums"
            assert _labels(app) == ["Albums"]

    asyncio.run(run())

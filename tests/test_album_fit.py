"""The album panel keeps its tracklist on screen at any terminal height, in
all three places it shows: the album page (Search, `g`), the Albums tab and
the Artists tab. At 32 rows the page and the Artists tab once had no room
for a single track: big art plus an artist header above it. The playlist
page shares the fit (it showed no tracks at 28 rows)."""

from __future__ import annotations

import asyncio

import pytest
from album_pages import ALBUM, open_album, stub_album_client, wait_until

from qobit.ui.screens.album_detail import (
    ART_MAX,
    ART_MIN,
    MIN_TRACK_ROWS,
    PANEL_WITH_ARTIST_MIN,
    fit_artist_header,
)
from qobit.ui.screens.playlist_detail import PlaylistScreen
from qobit.ui.widgets.art import Art


@pytest.mark.parametrize("where", ["page", "albums", "artists"])
@pytest.mark.parametrize("rows", [24, 32, 45])
def test_tracklist_stays_visible(ui_app, where, rows):
    stub_album_client(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, rows)) as pilot:
            panel, header = await open_album(ui_app, pilot, where)
            tracklist = panel.query_one(".detail-tracklist")
            assert tracklist.size.height >= MIN_TRACK_ROWS
            if not panel.query_one(".detail-header").has_class("-no-art"):
                # The tracklist never gets less than the art.
                assert tracklist.size.height >= panel.query_one(".detail-meta").size.height
            # The artist header only stays while the panel keeps its minimum.
            if header.display:
                assert panel.outer_size.height >= PANEL_WITH_ARTIST_MIN

    asyncio.run(run())


def test_artists_tab_header_comes_back_with_the_artist(ui_app):
    stub_album_client(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 32)) as pilot:
            _panel, header = await open_album(ui_app, pilot, "artists")
            assert not header.display  # given to the album at 32 rows
            view = ui_app.screen_stack[0].query_one("#view-artists")
            view._show_artist_content()
            await pilot.pause(0.2)
            assert header.display

    asyncio.run(run())


def test_fit_artist_header_is_stable(ui_app):
    """Re-running the decision after it took effect gives the same answer."""
    stub_album_client(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 32)) as pilot:
            panel, header = await open_album(ui_app, pilot, "page")
            for _ in range(3):
                before = header.display
                fit_artist_header(header, panel)
                await pilot.pause(0.1)
                assert header.display == before

    asyncio.run(run())


def test_tall_terminal_gets_full_art(ui_app):
    stub_album_client(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 70)) as pilot:
            panel, _ = await open_album(ui_app, pilot, "albums")
            assert not panel.query_one(".detail-header").has_class("-no-art")
            assert panel.query_one(".detail-meta").size.height == ART_MAX

    asyncio.run(run())


def _focused(app) -> str:
    w = app.focused
    return " ".join(sorted(w.classes)) if w is not None else ""


@pytest.mark.parametrize("where", ["page", "albums", "artists"])
def test_tab_and_click_reach_the_tracklist(ui_app, where):
    """The header's scroll column took focus (Tab, or a click on the art or
    info) with no visible cue; the tracks were one more Tab away."""
    stub_album_client(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            panel, _ = await open_album(ui_app, pilot, where)
            chain = []
            for _ in range(6):
                await pilot.press("tab")
                chain.append(ui_app.focused)
            assert all(not w.has_class("detail-meta") for w in chain if w is not None)
            assert any(w is not None and w.has_class("detail-tracklist") for w in chain)

            ui_app.screen.query_one("Tabs").focus()  # away from the tracklist
            await pilot.click(panel.query_one(".detail-title"))
            assert "detail-tracklist" in _focused(ui_app)

    asyncio.run(run())


def _stub_playlist(app) -> None:
    async def playlist(*_a, **_k):
        return {"id": "p1", "name": "Late Night", "tracks": ALBUM["tracks"], "tracks_count": 14}

    app._client.get_playlist = playlist


@pytest.mark.parametrize("rows", [24, 28, 32, 45])
def test_playlist_tracks_stay_visible(ui_app, rows):
    stub_album_client(ui_app)
    _stub_playlist(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, rows)) as pilot:
            await ui_app.push_screen(PlaylistScreen("p1"))
            screen = ui_app.screen
            tracklist = screen.query_one(".detail-tracklist")
            await wait_until(pilot, lambda: tracklist.highlighted_child is not None)
            # Two-row playlist rows: at least half of MIN_TRACK_ROWS tracks.
            assert tracklist.size.height >= MIN_TRACK_ROWS
            assert ART_MIN <= screen.query_one(".detail-meta").size.height <= ART_MAX or (
                screen.query_one(".detail-header").has_class("-no-art")
            )
            # The playlist's own art, not the transport's, got the initials.
            assert screen.query_one(".detail-art", Art).placeholder == "Late Night"
            assert screen.query_one("#tb-art", Art).placeholder != "Late Night"
            assert screen.name is None  # the title isn't stored over Textual's `name`

    asyncio.run(run())


def test_playlist_header_click_selects_the_tracks(ui_app):
    stub_album_client(ui_app)
    _stub_playlist(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            await ui_app.push_screen(PlaylistScreen("p1"))
            await pilot.pause(0.3)
            screen = ui_app.screen
            for _ in range(4):
                await pilot.press("tab")
                assert not (ui_app.focused and ui_app.focused.has_class("detail-meta"))
            screen.query_one("Tabs").focus()
            await pilot.click(screen.query_one(".detail-title"))
            assert "detail-tracklist" in _focused(ui_app)

    asyncio.run(run())

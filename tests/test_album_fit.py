"""The album panel keeps its tracklist on screen at any terminal height, in
all three places it shows: the album page (Search, `g`), the Albums tab and
the Artists tab. At 32 rows the page and the Artists tab once had no room
for a single track: big art plus an artist header above it."""

from __future__ import annotations

import asyncio

import pytest

from qobit.qobuz.models import Album, Artist
from qobit.ui.screens.album_detail import (
    ART_MAX,
    MIN_TRACK_ROWS,
    PANEL_WITH_ARTIST_MIN,
    AlbumDetailPanel,
    AlbumScreen,
    fit_artist_header,
)
from qobit.ui.screens.artist_detail import ArtistHeader

ALBUM = {
    "id": "a1",
    "title": "Kind of Blue",
    "release_date_original": "1959-08-17",
    "artist": {"id": "r1", "name": "Miles Davis"},
    "tracks_count": 14,
    "tracks": {
        "items": [
            {"id": i, "title": f"Track {i}", "duration": 300, "track_number": i}
            for i in range(1, 15)
        ]
    },
}
ARTIST = {"id": "r1", "name": "Miles Davis", "biography": {"content": "Trumpeter."}}


def _stub(app) -> None:
    c = app._client

    async def album(*_a, **_k):
        return ALBUM

    async def artist(*_a, **_k):
        return ARTIST

    async def empty(*_a, **_k):
        return []

    async def preview(*_a, **_k):
        return [{"id": "s1", "name": "Similar"}], 1

    c.get_album = album
    c.get_artist_detail = artist
    c.get_artist_top_tracks = empty
    c.get_similar_artists = empty
    c.get_similar_artists_preview = preview


async def _open(app, pilot, where: str) -> tuple[AlbumDetailPanel, ArtistHeader]:
    album = Album.from_api(ALBUM)
    if where == "page":
        await app.push_screen(AlbumScreen(album))
        await pilot.pause(0.3)
        screen = app.screen
        return screen.query_one(AlbumDetailPanel), screen.query_one(ArtistHeader)
    base = app.screen_stack[0]
    if where == "albums":
        app.action_switch_tab("albums")
        await pilot.pause(0.2)
        view = base.query_one("#view-albums")
    else:
        app.action_switch_tab("artists")
        await pilot.pause(0.2)
        view = base.query_one("#view-artists")
        view._open_artist(Artist.from_api(ARTIST))
        await pilot.pause(0.2)
    view._open_album(album)
    await pilot.pause(0.3)
    return view.query_one("#album-panel", AlbumDetailPanel), view.query_one(ArtistHeader)


@pytest.mark.parametrize("where", ["page", "albums", "artists"])
@pytest.mark.parametrize("rows", [24, 32, 45])
def test_tracklist_stays_visible(ui_app, where, rows):
    _stub(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, rows)) as pilot:
            panel, header = await _open(ui_app, pilot, where)
            tracklist = panel.query_one(".detail-tracklist")
            assert tracklist.size.height >= MIN_TRACK_ROWS
            if not panel.has_class("-no-art"):
                # The tracklist never gets less than the art.
                assert tracklist.size.height >= panel.query_one(".detail-meta").size.height
            # The artist header only stays while the panel keeps its minimum.
            if header.display:
                assert panel.outer_size.height >= PANEL_WITH_ARTIST_MIN

    asyncio.run(run())


def test_artists_tab_header_comes_back_with_the_artist(ui_app):
    _stub(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 32)) as pilot:
            _panel, header = await _open(ui_app, pilot, "artists")
            assert not header.display  # given to the album at 32 rows
            view = ui_app.screen_stack[0].query_one("#view-artists")
            view._show_artist_content()
            await pilot.pause(0.2)
            assert header.display

    asyncio.run(run())


def test_fit_artist_header_is_stable(ui_app):
    """Re-running the decision after it took effect gives the same answer."""
    _stub(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 32)) as pilot:
            panel, header = await _open(ui_app, pilot, "page")
            for _ in range(3):
                before = header.display
                fit_artist_header(header, panel)
                await pilot.pause(0.1)
                assert header.display == before

    asyncio.run(run())


def test_tall_terminal_gets_full_art(ui_app):
    _stub(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 70)) as pilot:
            panel, _ = await _open(ui_app, pilot, "albums")
            assert not panel.has_class("-no-art")
            assert panel.query_one(".detail-meta").size.height == ART_MAX

    asyncio.run(run())

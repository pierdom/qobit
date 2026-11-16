"""The ♥ on a favourite album's title and on a followed artist's header, and
that it follows a toggle from the `i` menu without reopening the page."""

from __future__ import annotations

import asyncio

import pytest
from test_album_fit import ALBUM, ARTIST, _open, _stub
from textual.widgets import Label

from qobit.qobuz.models import Album, Artist
from qobit.ui.screens.search import ICON_FAV


def _favourites(app, albums: bool, artists: bool) -> list:
    calls: list = []
    c = app._client

    async def fav_albums():
        return [ALBUM] if albums else []

    async def fav_artists():
        return [ARTIST] if artists else []

    async def set_favorite(kind, item_id, on):
        calls.append((kind, item_id, on))

    c.get_all_favorite_albums = fav_albums
    c.get_all_favorite_artists = fav_artists
    c.set_favorite = set_favorite
    return calls


def _album_title(panel) -> str:
    return str(panel.query_one(".detail-title", Label).content)


def _artist_title(header) -> str:
    return str(header.query_one(".ah-bio-section").border_title)


@pytest.mark.parametrize("where", ["page", "albums"])
@pytest.mark.parametrize("fav", [True, False])
def test_hearts_show_favourites(ui_app, where, fav):
    _stub(ui_app)
    _favourites(ui_app, albums=fav, artists=fav)

    async def run():
        async with ui_app.run_test(size=(130, 50)) as pilot:
            panel, header = await _open(ui_app, pilot, where)
            await pilot.pause(0.3)
            assert (ICON_FAV in _album_title(panel)) is fav
            assert "Kind of Blue" in _album_title(panel)
            assert (ICON_FAV in _artist_title(header)) is fav
            assert "MILES DAVIS" in _artist_title(header)

    asyncio.run(run())


def test_hearts_follow_a_toggle(ui_app):
    _stub(ui_app)
    calls = _favourites(ui_app, albums=True, artists=True)

    async def run():
        async with ui_app.run_test(size=(130, 50)) as pilot:
            panel, header = await _open(ui_app, pilot, "page")
            await pilot.pause(0.3)
            assert ICON_FAV in _album_title(panel)

            await ui_app.toggle_favorite_album(Album.from_api(ALBUM))
            await ui_app.toggle_follow_artist(Artist.from_api(ARTIST))
            await pilot.pause(0.1)
            assert ICON_FAV not in _album_title(panel)
            assert ICON_FAV not in _artist_title(header)

            await ui_app.toggle_favorite_album(Album.from_api(ALBUM))
            await pilot.pause(0.1)
            assert ICON_FAV in _album_title(panel)
            assert calls == [("album", "a1", False), ("artist", "r1", False), ("album", "a1", True)]

    asyncio.run(run())


def test_artist_header_bio_is_plain_text(ui_app):
    """The artist page showed the API's HTML (<p>…) as text; the Now
    Playing bio already stripped it."""
    _stub(ui_app)
    _favourites(ui_app, albums=False, artists=False)
    bio = "<p>Trumpeter &amp; composer.<br/>Born in <b>Alton</b>, Illinois.</p>"
    c = ui_app._client

    async def artist(*_a, **_k):
        return {**ARTIST, "biography": {"content": bio}}

    c.get_artist_detail = artist

    async def run():
        async with ui_app.run_test(size=(130, 50)) as pilot:
            _panel, header = await _open(ui_app, pilot, "page")
            await pilot.pause(0.3)
            text = str(header.query_one(".ah-bio", Label).content)
            assert "<" not in text and "&amp;" not in text
            assert text == "Trumpeter & composer. Born in Alton, Illinois."

    asyncio.run(run())

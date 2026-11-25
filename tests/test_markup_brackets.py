"""Capitalised [bracketed] text from Qobuz ("[PIAS] Recordings", "Album
[Remastered]") is shown as it is: Textual reads `[Word]` as a style tag, and
rich's escape() only covers lowercase tags, so it used to vanish."""

from __future__ import annotations

import asyncio
import copy

import album_pages
from album_pages import open_album, stub_album_client
from textual.app import App
from textual.content import Content
from textual.widgets import Label

from qobit.qobuz.models import Album, Playlist
from qobit.ui._utils import markup_escape
from qobit.ui.app import QobitApp
from qobit.ui.screens.cards import AlbumCard
from qobit.ui.screens.playlists import PlaylistCard

TITLES = ["[PIAS] Recordings", "Abbey Road [Remastered]", "a [live] b", "[x/y] [/]", "plain"]


def test_markup_escape_keeps_every_bracket():
    for t in TITLES:
        assert Content.from_markup(markup_escape(t)).plain == t


class _Host(App):
    get_theme_variable_defaults = QobitApp.get_theme_variable_defaults

    def __init__(self, widget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self):
        yield self._widget


def _texts(widget, selector: str) -> str:
    async def run() -> str:
        async with _Host(widget).run_test():
            return str(widget.query_one(selector, Label).render())

    return asyncio.run(run())


def test_album_card_shows_bracketed_title_and_artist():
    album = Album(
        id="1", title="Abbey Road [Remastered]", artist="[PIAS] Band", year=1969, tracks_count=17
    )
    assert _texts(AlbumCard(album), ".card-title").strip() == "Abbey Road [Remastered]"
    assert _texts(AlbumCard(album), ".card-artist").strip() == "[PIAS] Band"


def test_playlist_card_shows_bracketed_name():
    pl = Playlist(id="1", name="Best Of [Live] 2024", owner="me", tracks_count=3)
    assert _texts(PlaylistCard(pl), ".card-name").strip() == "Best Of [Live] 2024"


def test_album_page_shows_bracketed_label_version_and_description(ui_app, monkeypatch):
    stub_album_client(ui_app)
    album = copy.deepcopy(album_pages.ALBUM)
    album.update(
        title="Mr. Beast",
        version="Deluxe [Remastered]",
        label={"id": 1, "name": "[PIAS] Recordings"},
        description="<p>Out on [PIAS] Recordings</p>",
    )

    async def served(*_a, **_k):
        return album

    ui_app._client.get_album = served

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            panel, _ = await open_album(ui_app, pilot, "page")
            for selector, want in (
                (".ap-version", "Deluxe [Remastered]"),
                (".ap-badges", "[PIAS] Recordings"),
                (".detail-desc", "Out on [PIAS] Recordings"),
            ):
                assert want in str(panel.query_one(selector, Label).render()), selector

    asyncio.run(run())

"""Shared by the album/artist page UI tests: a 14-track album, its artist,
a client stub that serves them, and opening the album in each place it shows
(the album page, the Albums tab, the Artists tab)."""

from __future__ import annotations

from qobit.qobuz.models import Album, Artist
from qobit.ui.screens.album_detail import AlbumDetailPanel, AlbumScreen
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


def stub_album_client(app) -> None:
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


async def open_album(app, pilot, where: str) -> tuple[AlbumDetailPanel, ArtistHeader]:
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

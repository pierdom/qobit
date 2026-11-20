"""Shared by the album/artist page UI tests: a 14-track album, its artist,
a client stub that serves them, and opening the album in each place it shows
(the album page, the Albums tab, the Artists tab)."""

from __future__ import annotations

import time

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
    """Open the album where asked; returns once its tracks are listed and
    the layout has settled (the panel fits itself on resize)."""
    album = Album.from_api(ALBUM)
    if where == "page":
        await app.push_screen(AlbumScreen(album))
        host = app.screen
    else:
        base = app.screen_stack[0]
        app.action_switch_tab(where)
        host = base.query_one(f"#view-{where}")
        await wait_until(pilot, lambda: host.display)
        if where == "artists":
            host._open_artist(Artist.from_api(ARTIST))
            await wait_until(pilot, lambda: host._view == "artist")
        host._open_album(album)
    panel = host.query_one(AlbumDetailPanel)
    tracks = len(ALBUM["tracks"]["items"])
    await wait_until(pilot, lambda: len(panel.query_one(".detail-tracklist").children) == tracks)
    await pilot.pause(0.1)  # one more layout pass for the fit
    return panel, host.query_one(ArtistHeader)


async def wait_until(pilot, condition, timeout: float = 5.0) -> None:
    """Poll the UI until ``condition()`` holds: fixed pauses flaked on slow
    CI runners (a menu not open yet after 0.3 s)."""
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    code = getattr(condition, "__code__", None)
    where = f"{code.co_filename.rsplit('/', 1)[-1]}:{code.co_firstlineno}" if code else condition
    raise AssertionError(f"condition at {where} not met within {timeout} s")


async def quiet(pilot, still: float = 0.3, timeout: float = 8.0) -> None:
    """Wait until the layout has stopped changing for ``still`` seconds (the
    footer fit and the pane widths settle in later passes, and a fixed pause
    was too short on loaded CI runners). Widget geometry, not the screen's
    text: the playing track's meter keeps that changing."""
    last, since = None, time.monotonic()
    end = since + timeout
    while time.monotonic() < end:
        await pilot.pause(0.05)
        screen = pilot.app.screen_stack[0]
        now = [(w.id, w.region, w.display) for w in screen.walk_children()]
        if now != last:
            last, since = now, time.monotonic()
        elif time.monotonic() - since >= still:
            return

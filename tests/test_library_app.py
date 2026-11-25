"""The app's side of the library: the toggles flash what Library raises, and a
Change redraws what shows the list (the tabs, the hearts, the Playback rows)."""

from __future__ import annotations

import asyncio

from qobit.library import Change
from qobit.qobuz.models import Album, Artist, Track
from qobit.ui.screens.albums import AlbumsView
from qobit.ui.screens.artists import ArtistsView
from qobit.ui.screens.playlists import PlaylistsView
from qobit.ui.screens.tracks import TracksView


def _track(i: int) -> Track:
    return Track(id=str(i), title=f"T{i}", artist="A", album="B", album_id="1", duration=100)


def _stub(app, fail: bool = False) -> list:
    calls: list = []

    async def empty():
        return []

    async def set_favorite(kind, item_id, on):
        if fail:
            from qobit.qobuz.client import QobuzError

            raise QobuzError("no")
        calls.append((kind, item_id, on))

    c = app._client
    c.get_all_favorite_tracks = empty
    c.get_all_favorite_albums = empty
    c.get_all_favorite_artists = empty
    c.get_all_user_playlists = empty
    c.set_favorite = set_favorite
    return calls


def _spy(monkeypatch, obj, name: str) -> list:
    seen: list = []
    monkeypatch.setattr(obj, name, lambda *a, **k: seen.append(a))
    return seen


def test_favouriting_a_track_patches_the_tab_and_redraws_the_hearts(ui_app, monkeypatch):
    calls = _stub(ui_app)

    async def body():
        async with ui_app.run_test(size=(120, 30)):
            tab = ui_app.screen.query_one(TracksView)
            patched = _spy(monkeypatch, tab, "favorite_changed")
            before = ui_app.queue_version
            assert await ui_app.toggle_favorite(_track(7)) is True
            assert calls == [("track", "7", True)]
            assert patched == [("7", True)]
            assert ui_app.queue_version == before + 1  # the hearts in every track list

    asyncio.run(body())


def test_a_failed_toggle_flashes_the_reason_and_changes_nothing(ui_app, monkeypatch):
    _stub(ui_app, fail=True)
    flashes: list[str] = []
    monkeypatch.setattr(ui_app, "_flash_status", flashes.append)

    async def body():
        async with ui_app.run_test(size=(120, 30)):
            before = ui_app.queue_version
            assert await ui_app.toggle_favorite(_track(7)) is False
            await ui_app.toggle_favorite_album(
                Album(id="a", title="X", artist="A", year=2000, tracks_count=1)
            )
            await ui_app.toggle_follow_artist(Artist(id="r", name="Y"))
            assert flashes == [
                "Couldn't update favourite",
                "Couldn't update favourite",
                "Couldn't update followed artists",
            ]
            assert ui_app.queue_version == before

    asyncio.run(body())


def test_album_and_artist_toggles_flash_the_new_state(ui_app, monkeypatch):
    _stub(ui_app)
    flashes: list[str] = []
    monkeypatch.setattr(ui_app, "_flash_status", flashes.append)

    async def body():
        async with ui_app.run_test(size=(120, 30)):
            album = Album(id="a", title="X", artist="A", year=2000, tracks_count=1)
            await ui_app.toggle_favorite_album(album)
            await ui_app.toggle_favorite_album(album)
            await ui_app.toggle_follow_artist(Artist(id="r", name="Y"))
            await ui_app.toggle_follow_artist(Artist(id="r", name="Y"))
            assert flashes == [
                "Added to favourites: X",
                "Removed from favourites: X",
                "Following Y",
                "Unfollowed Y",
            ]

    asyncio.run(body())


def test_each_kind_of_change_redraws_what_shows_it(ui_app, monkeypatch):
    _stub(ui_app)

    async def body():
        async with ui_app.run_test(size=(120, 30)):
            base = ui_app.screen_stack[0]
            tracks = _spy(monkeypatch, base.query_one(TracksView), "library_changed")
            albums = _spy(monkeypatch, base.query_one(AlbumsView), "favorites_changed")
            artists = _spy(monkeypatch, base.query_one(ArtistsView), "favorites_changed")
            playlists = _spy(monkeypatch, base.query_one(PlaylistsView), "library_changed")
            hearts = _spy(monkeypatch, ui_app, "_refresh_hearts")
            tiles = _spy(monkeypatch, ui_app, "_refresh_followed_tiles")
            version = ui_app.queue_version

            ui_app._on_library_change(Change("tracks", "refresh"))
            assert len(tracks) == 1 and ui_app.queue_version == version + 1
            ui_app._on_library_change(Change("albums", "refresh"))
            assert len(albums) == 1 and len(hearts) == 1
            ui_app._on_library_change(Change("artists", "toggle", "r", True))
            assert len(artists) == 1 and len(hearts) == 2 and len(tiles) == 1
            ui_app._on_library_change(Change("playlists", "refresh"))
            assert len(playlists) == 1

    asyncio.run(body())

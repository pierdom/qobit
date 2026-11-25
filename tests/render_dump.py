"""Dev tool, not a test (pytest doesn't collect it by name): dump what the app
renders, the text and style of every cell, for a few scenes and themes, so a
refactor that must not change the look can be checked by a diff.

    git stash / checkout the old code, then:
    CSSDUMP_OUT=/tmp/before uv run pytest tests/render_dump.py -q
    (the new code)
    CSSDUMP_OUT=/tmp/after uv run pytest tests/render_dump.py -q
    diff -rq /tmp/before /tmp/after

28 files: Playback, Tracks, the Albums, Artists and Playlists grids, the Mixes
tab and an artist page (releases, top tracks, similar), in cyberdeck, ayu-light
and two transparent variants. Animations are off
(the wordmark and the meter move); two runs of the same code are identical.
A scene not covered here (a new screen) belongs in this file before the refactor.
"""

import asyncio
import copy
import dataclasses
import json
import os
from pathlib import Path

import album_pages
import pytest
from album_pages import quiet, wait_until

from qobit.qobuz.models import Track

OUT = os.environ.get("CSSDUMP_OUT")
pytestmark = pytest.mark.skipif(not OUT, reason="CSSDUMP_OUT not set")

FAVS = [
    {
        "id": i,
        "title": f"Title {i}",
        "performer": {"name": f"Artist {i % 5}"},
        "album": {"title": f"Album {i % 7}"},
        "duration": 200 + i,
    }
    for i in range(40)
]


def _albums() -> list[dict]:
    out = []
    for i in range(10):
        a = copy.deepcopy(album_pages.ALBUM)
        a.update(id=f"a{i}", title=f"Album number {i}")
        out.append(a)
    return out


def _dump(app) -> list:
    return [
        [[seg.text, str(seg.style)] for seg in strip]
        for strip in app.screen._compositor.render_strips()
    ]


ARTISTS = [{"id": f"r{i}", "name": f"Artist {i}", "albums_count": i + 1} for i in range(10)]
PLAYLISTS = [
    {
        "id": 100 + i,
        "name": f"Playlist {i}",
        "owner": {"name": "me"},
        "tracks_count": 10 + i,
        "duration": 3000 + i * 60,
    }
    for i in range(7)
]


def _stub(app) -> None:
    async def tracks():
        return FAVS

    async def albums():
        return _albums()

    app._client.get_all_favorite_tracks = tracks
    app._client.get_all_favorite_albums = albums
    album_pages.stub_album_client(app)

    async def artists():
        return ARTISTS

    async def playlists():
        return PLAYLISTS

    async def artist_page(*_a, **_k):
        albums = [
            {**album_pages.ALBUM, "id": f"a{i}", "title": f"Album {i}", "hires_streamable": i % 2}
            for i in range(6)
        ]
        return {**album_pages.ARTIST, "albums": {"items": albums}}

    async def top_tracks(*_a, **_k):
        return [{**FAVS[i], "performer": {"name": "Miles Davis", "id": "r1"}} for i in range(3)]

    app._client.get_all_favorite_artists = artists
    app._client.get_all_user_playlists = playlists
    app._client.get_artist_detail = artist_page
    app._client.get_artist_top_tracks = top_tracks
    app._poll_player = lambda: None  # no mpv: the play state is set by hand
    app.gapless_queue.maybe_prefetch = lambda: None


async def _scene(app, pilot, name: str) -> None:
    if name == "playback":
        ts = [
            dataclasses.replace(
                Track.from_api(FAVS[i]),
                duration=200 + i,
                bit_depth=24 if i % 2 else 16,
                sampling_rate=96 if i % 2 else 44.1,
            )
            for i in range(8)
        ]
        app._timeline, app._cursor = ts, 3
        app.now_playing = ts[3]
        app.is_playing, app.is_paused = True, False
        app.playback_dur, app.playback_pos = 203.0, 80.0
        app.queue_version += 1
    elif name == "tracks":
        app.action_switch_tab("tracks")
        lv = app.screen.query_one("#fav-tracks")
        await wait_until(pilot, lambda: lv.highlighted_child and app.focused is lv, timeout=15)
        lv.index = 2
    elif name == "albums":
        app.action_switch_tab("albums")
        await wait_until(pilot, lambda: len(list(app.screen.query(".grid-card"))) >= 6, timeout=15)
    elif name == "artists":
        app.action_switch_tab("artists")
        await wait_until(pilot, lambda: len(list(app.screen.query(".grid-card"))) >= 6, timeout=15)
    elif name == "playlists":
        app.action_switch_tab("playlists")
        await wait_until(pilot, lambda: len(list(app.screen.query(".grid-card"))) >= 6, timeout=15)
    elif name == "artist-page":
        from qobit.ui.screens.artist_detail import ArtistScreen
        from qobit.ui.screens.cards import AlbumCard

        await app.push_screen(ArtistScreen("r1"))
        await wait_until(pilot, lambda: len(app.screen.query(AlbumCard)) == 6, timeout=15)
    elif name == "mixes":
        app.action_switch_tab("mixes")
        await wait_until(pilot, lambda: app.base_tab() == "mixes", timeout=10)
    await quiet(pilot)
    await pilot.pause(0.3)
    await quiet(pilot)


@pytest.mark.parametrize(
    "scene", ["playback", "tracks", "albums", "mixes", "artists", "playlists", "artist-page"]
)
@pytest.mark.parametrize(
    "variant", ["cyberdeck", "ayu-light", "transparent", "ayu-mirage-transparent"]
)
def test_dump(ui_app, scene, variant):
    _stub(ui_app)

    async def run():
        async with ui_app.run_test(size=(160, 44)) as pilot:
            ui_app.animations = False
            await pilot.pause(0.5)
            if variant == "ayu-light":
                ui_app.theme = "ayu-light"
            elif variant == "transparent":
                ui_app.action_toggle_background()
            elif variant == "ayu-mirage-transparent":
                ui_app.theme = "ayu-mirage"
                ui_app.action_toggle_background()
            await quiet(pilot)
            await _scene(ui_app, pilot, scene)
            Path(OUT).mkdir(parents=True, exist_ok=True)
            Path(OUT, f"{scene}-{variant}.json").write_text(json.dumps(_dump(ui_app)))

    asyncio.run(run())

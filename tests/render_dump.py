"""Dev tool, not a test (pytest doesn't collect it by name): dump what the app
renders, the text and style of every cell, for a few scenes and themes, so a
refactor that must not change the look can be checked by a diff.

    git stash / checkout the old code, then:
    CSSDUMP_OUT=/tmp/before uv run pytest tests/render_dump.py -q
    (the new code)
    CSSDUMP_OUT=/tmp/after uv run pytest tests/render_dump.py -q
    diff -rq /tmp/before /tmp/after

16 files: Playback, Tracks, an Albums grid with a selected card and the Mixes
tab, in cyberdeck, ayu-light and two transparent variants. Animations are off
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


def _stub(app) -> None:
    async def tracks():
        return FAVS

    async def albums():
        return _albums()

    app._client.get_all_favorite_tracks = tracks
    app._client.get_all_favorite_albums = albums
    album_pages.stub_album_client(app)
    app._poll_player = lambda: None  # no mpv: the play state is set by hand
    app._maybe_prefetch = lambda: None


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
    elif name == "mixes":
        app.action_switch_tab("mixes")
        await wait_until(pilot, lambda: app.base_tab() == "mixes", timeout=10)
    await quiet(pilot)
    await pilot.pause(0.3)
    await quiet(pilot)


@pytest.mark.parametrize("scene", ["playback", "tracks", "albums", "mixes"])
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

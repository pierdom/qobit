"""The Playback list is reconciled with the timeline, not rebuilt
(QueueView._reconcile): after any change its rows match the timeline, a track
ending remounts nothing, and a cursor the user moved stays put.

Runs the real QobitApp headless with a stub client (no network, no mpv, no
media keys, no images)."""

from __future__ import annotations

import asyncio
import random

import pytest

import qobit.ui.app as app_module
from qobit.qobuz.models import Track
from qobit.ui.screens import queue as queue_module
from qobit.ui.screens.queue import QueueView, SectionHeader, TimelineRow


class _NoOpMediaKeys:
    def __init__(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass

    def set_artwork(self, *args, **kwargs):
        pass

    def close(self):
        pass


class _StubClient:
    def __init__(self, *args, **kwargs):
        pass

    def restore_session(self, *args, **kwargs):
        return True

    def is_closed(self):
        return False

    async def close(self):
        pass

    async def aclose(self):
        pass

    async def get_all_favorite_tracks(self):
        return []

    async def get_all_favorite_albums(self):
        return []

    async def get_all_favorite_artists(self):
        return []


async def _no_image(url):
    return None


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(app_module, "MediaKeys", _NoOpMediaKeys)
    monkeypatch.setattr(app_module, "QobuzClient", _StubClient)
    monkeypatch.setattr(app_module, "set_saved_theme", lambda *_: None)
    monkeypatch.setattr(queue_module, "fetch_image", _no_image)
    from qobit.ui.widgets import transport

    monkeypatch.setattr(transport, "fetch_image", _no_image)
    return app_module.QobitApp(connect=False)


POOL = [
    Track(id=str(i), title=f"T{i}", artist="A", album="B", album_id="1", duration=60)
    for i in range(40)
]


def _expected(app):
    out = []
    history, queue, now = app._history, app._play_queue, app.now_playing
    if history:
        out.append(("H", "Recently Played", len(history)))
        out += [("history", t.id, i, 0) for i, t in enumerate(history)]
    if now:
        out.append(("now", now.id, len(history), 0))
    if queue:
        out.append(("H", "Up Next", len(queue)))
        base = len(history) + (1 if now else 0)
        out += [("next", t.id, base + n - 1, n) for n, t in enumerate(queue, 1)]
    return out


def _actual(lv):
    out = []
    for c in lv.children:
        if isinstance(c, SectionHeader):
            out.append(("H", c.section, c._count))
        elif isinstance(c, TimelineRow):
            number = c._number if c.role == "next" else 0
            out.append((c.role, c.track.id, c.timeline_index, number))
        else:
            out.append(("other",))
    return out


def test_reconcile_tracks_the_timeline(app):
    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            lv = app.query_one("#queue-list")
            view = app.query_one(QueueView)

            async def settle():
                app.queue_version += 1
                await pilot.pause()
                # The list's own reconcile workers (other app workers live as
                # long as the app).
                for worker in [w for w in app.workers if w.node is view]:
                    await worker.wait()
                await pilot.pause()

            app._timeline = POOL[:12]
            app._cursor = 4
            app.now_playing = POOL[4]
            await settle()
            assert _actual(lv) == _expected(app)
            lv.focus()
            await pilot.pause()
            assert lv.highlighted_child.role == "now"

            # A track ends: the same rows, nothing remounted; the cursor follows.
            before = {id(c) for c in lv.children}
            app._cursor = 5
            app.now_playing = app._timeline[5]
            await settle()
            assert _actual(lv) == _expected(app)
            assert {id(c) for c in lv.children} == before
            assert lv.highlighted_child.track is app.now_playing

            # A row the user moved to in Up Next keeps the cursor.
            await pilot.press("down", "down", "down")
            held = lv.highlighted_child
            app._cursor = 6
            app.now_playing = app._timeline[6]
            await settle()
            assert lv.highlighted_child is held
            assert sum(1 for c in lv.children if c.highlighted) == 1

            # Appends, repeats of one track, removals, jumps.
            rnd = random.Random(3)
            for _ in range(30):
                op = rnd.choice(["jump", "append", "remove", "insert"])
                if op == "jump":
                    app._cursor = rnd.randrange(len(app._timeline))
                    app.now_playing = app._timeline[app._cursor]
                elif op == "append":
                    app._timeline += rnd.sample(POOL, 3)
                elif op == "remove" and app._cursor + 1 < len(app._timeline):
                    del app._timeline[rnd.randrange(app._cursor + 1, len(app._timeline))]
                elif op == "insert":
                    app._timeline.insert(app._cursor + 1, rnd.choice(POOL))
                await settle()
                assert _actual(lv) == _expected(app), op
                assert sum(1 for c in lv.children if c.highlighted) <= 1

            # Empty, then filled again.
            app._timeline, app._cursor, app.now_playing = [], -1, None
            await settle()
            assert _actual(lv) == [("other",)]
            app._timeline, app._cursor, app.now_playing = POOL[:3], 0, POOL[0]
            await settle()
            assert _actual(lv) == _expected(app)

    asyncio.run(run())

"""The Playback list on a slow machine: it took seconds to appear at startup
and every redraw cost in proportion to the rows mounted.

- It waited for the whole favourites library before drawing a row.
- It mounted every timeline row (100 played + a whole playlist in Up Next);
  Textual maps every mounted widget on each cursor move and repaint.
- Each beat of the playing row's meter (and each cursor move) relaid out the
  whole screen: ``Label.update`` does a layout pass unless told not to."""

from __future__ import annotations

import asyncio

from album_pages import wait_until
from textual.widget import Widget

from qobit.qobuz.models import Track
from qobit.ui import _now_playing
from qobit.ui.screens.queue import _UP_NEXT_PAGE, TimelineRow

TRACKS = [
    Track(id=str(i), title=f"T{i}", artist="A", album="B", album_id="1", duration=60)
    for i in range(400)
]


def _timeline(app, cursor: int = 50) -> None:
    app._timeline = list(TRACKS)
    app._cursor = cursor


def _rows(lv) -> list[TimelineRow]:
    return [c for c in lv.children if isinstance(c, TimelineRow)]


def test_queue_draws_before_the_favourites_load(ui_app):
    release = asyncio.Event()

    async def slow_favourites():
        await release.wait()
        return [{"id": 3, "title": "T3", "performer": {"name": "A"}, "album": {"title": "B"}}]

    ui_app._client.get_all_favorite_tracks = slow_favourites

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            _timeline(ui_app, cursor=10)
            ui_app.queue_version += 1
            lv = ui_app.query_one("#queue-list")
            await wait_until(pilot, lambda: _rows(lv))
            assert not any(r._favorite for r in _rows(lv))  # no hearts yet
            release.set()
            await wait_until(pilot, lambda: any(r._favorite for r in _rows(lv)))
            assert [r.track.id for r in _rows(lv) if r._favorite] == ["3"]

    asyncio.run(run())


def test_long_queue_mounts_a_page_and_end_reaches_the_last(ui_app):
    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            _timeline(ui_app)
            ui_app.queue_version += 1
            lv = ui_app.query_one("#queue-list")
            await wait_until(pilot, lambda: _rows(lv))
            await pilot.pause(0.2)
            # The played rows, the playing one and a page of Up Next.
            assert len(_rows(lv)) <= 51 + _UP_NEXT_PAGE
            assert len([r for r in lv.rows if isinstance(r, TimelineRow)]) == 400
            lv.focus()
            await pilot.press("end")
            await wait_until(pilot, lambda: lv.highlighted_child is not None)
            await wait_until(pilot, lambda: lv.highlighted_child.track is TRACKS[-1])

    asyncio.run(run())


def test_the_beat_and_the_cursor_skip_the_layout_pass(ui_app, monkeypatch):
    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            _timeline(ui_app)
            ui_app.now_playing = TRACKS[50]
            ui_app.queue_version += 1
            lv = ui_app.query_one("#queue-list")
            await wait_until(pilot, lambda: lv.highlighted_child is not None)
            lv.focus()
            await pilot.pause(0.2)

            layouts: list[str] = []
            refresh = Widget.refresh

            def spy(self, *a, layout=False, **k):
                if layout:
                    layouts.append(type(self).__name__)
                return refresh(self, *a, layout=layout, **k)

            monkeypatch.setattr(Widget, "refresh", spy)
            for _ in range(5):
                _now_playing.tick(True)
            await pilot.press("up", "up")
            await pilot.pause(0.1)
            assert layouts == []

    asyncio.run(run())

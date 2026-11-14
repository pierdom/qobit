"""ConnectPlayContext (phase 2): the TUI's queue actions while qobit is the
active Qobuz Connect renderer become server commands with the right
queue_item_ids. The mirror is built through the real queue_changed path;
the renderer's command methods are replaced by recorders."""

from __future__ import annotations

import asyncio

import pytest

import qobit.ui.app as app_module
from qobit.connect.proto import qconnect_pb2 as qp
from qobit.connect.queue import ServerQueue
from qobit.qobuz.models import Track
from qobit.ui.play_context import ConnectPlayContext


class _NoOpMediaKeys:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def _track(tid: int) -> Track:
    return Track(id=str(tid), title=f"T{tid}", artist="A", album="B", album_id="1", duration=100)


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr(app_module, "MediaKeys", _NoOpMediaKeys)
    app = app_module.QobitApp()
    c = ConnectPlayContext(app)
    app._connect = c
    c.renderer.active = True
    calls: list[tuple] = []
    for name in (
        "queue_load",
        "queue_insert",
        "queue_add",
        "queue_remove",
        "queue_replace_upcoming",
    ):
        monkeypatch.setattr(c.renderer, name, lambda *a, _n=name: calls.append((_n, *a)))

    async def fake_track(tid: int) -> Track:
        return _track(tid)

    monkeypatch.setattr(c, "_track", fake_track)
    c.calls = calls
    app._flash_status = lambda msg, secs=3.0: setattr(app, "_last_flash", msg)
    return c


def _mirror(c: ConnectPlayContext, tracks: list[int], current: int, autoplay: list[int] = ()):
    """Server queue: tracks get item ids 10, 11, …; autoplay 50, 51, …"""
    q = ServerQueue()
    q.set_state(
        qp.SrvrCtrlQueueState(
            queue_version=qp.QueueVersion(major=3, minor=1),
            tracks=[
                qp.QueueTrackRef(queue_item_id=10 + i, track_id=t) for i, t in enumerate(tracks)
            ],
            autoplay_tracks=[
                qp.QueueTrackRef(queue_item_id=50 + i, track_id=t) for i, t in enumerate(autoplay)
            ],
        )
    )
    c.renderer.item_id = 10 + current
    asyncio.run(c.queue_changed(q, 10 + current))


def test_mirror_is_the_play_context_with_cursor_on_current(ctx):
    _mirror(ctx, [1, 2, 3, 4], current=1)
    assert [t.id for t in ctx.app._timeline] == ["1", "2", "3", "4"]
    assert ctx.app._cursor == 1 and ctx.app._ctx is ctx


def test_play_next_inserts_after_the_current_item(ctx):
    _mirror(ctx, [1, 2, 3], current=1)
    assert ctx.app.queue_next(_track(99)) is True
    assert ctx.calls == [("queue_insert", [99], 11)]


def test_add_to_queue_appends(ctx):
    _mirror(ctx, [1, 2], current=0)
    ctx.app.queue_last(_track(99))
    assert ctx.calls == [("queue_add", [99])]


def test_remove_targets_the_exact_row_even_when_the_song_is_queued_twice(ctx):
    _mirror(ctx, [1, 7, 2, 7], current=0)  # song 7 at item 11 and item 13
    second_seven = ctx.app._timeline[3]
    assert second_seven is not ctx.app._timeline[1]  # one object per row
    assert ctx.app.remove_from_queue(second_seven) is True
    assert ctx.calls == [("queue_remove", [13])]


def test_autoplay_rows_are_read_only(ctx):
    _mirror(ctx, [1, 2], current=0, autoplay=[88])
    auto_row = ctx.app._timeline[2]
    assert ctx.app.remove_from_queue(auto_row) is False
    assert ctx.calls == [] and "Autoplay" in ctx.app._last_flash


def test_clear_up_next_spares_autoplay_and_clear_history_takes_the_past(ctx):
    _mirror(ctx, [1, 2, 3, 4], current=1, autoplay=[88])
    ctx.app.clear_queue()
    ctx.app.clear_history()
    assert ctx.calls == [("queue_remove", [12, 13]), ("queue_remove", [10])]


def test_playing_from_the_library_loads_a_new_server_queue(ctx):
    _mirror(ctx, [1, 2], current=0)
    asyncio.run(ctx.load(_track(5), [_track(6), _track(7)], 0.0))
    assert ctx.calls == [("queue_load", [5, 6, 7], 0)]


def test_reselecting_the_current_track_resumes_instead_of_reloading(ctx, monkeypatch):
    _mirror(ctx, [1, 2], current=0)
    toggled = []

    async def fake_toggle():
        toggled.append(True)

    monkeypatch.setattr(ctx.renderer, "toggle_pause", fake_toggle)
    ctx.renderer.state = qp.PLAYING_STATE_PAUSED
    asyncio.run(ctx.load(_track(1), None, 0.0))
    assert toggled == [True] and ctx.calls == []


def test_song_radio_replaces_up_next(ctx):
    _mirror(ctx, [1, 2, 3], current=0)
    assert ctx.replace_upcoming([_track(41), _track(42)]) is True
    assert ctx.calls == [("queue_replace_upcoming", [41, 42])]

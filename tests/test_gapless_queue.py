"""GaplessQueue without the app: a fake host, a fake player and a fake stream
resolver. test_gapless.py runs the same behaviour inside the real QobitApp."""

from __future__ import annotations

import asyncio

from qobit.qobuz.models import StreamUrl, Track
from qobit.ui.gapless import GaplessQueue


def _track(name: str) -> Track:
    return Track(id=name, title=name, artist="A", album="B", album_id="1", duration=200)


def _stream(name: str, sample: bool = False) -> StreamUrl:
    return StreamUrl(f"url-{name}", 6, 44100.0, 16, sample=sample)


class Player:
    def __init__(self):
        self.gen, self.running = 1, True
        self.appended: list[str] = []
        self.cleared: list[int] = []

    def snapshot(self):
        return self.gen, self.running

    def append(self, url, gen):
        if gen != self.gen:
            return False
        self.appended.append(url)
        return True

    def clear_next(self, gen):
        self.cleared.append(gen)


class Streams:
    def __init__(self):
        self.sample: set[str] = set()
        self.asked: list[str] = []

    async def resolve(self, track_id):
        self.asked.append(track_id)
        return _stream(track_id, sample=track_id in self.sample)


class Host:
    def __init__(self):
        self.gapless = self.is_local = self.is_playing = True
        self.is_paused = self.advance_busy = self.radio_mode = False
        self.playback_pos, self.playback_dur = 175.0, 200.0
        self.timeline = [_track("A"), _track("B"), _track("C")]
        self.cursor = 0
        self.now_playing = self.timeline[0]
        self.tasks: list = []
        self.stepped: list[str] = []
        self.advanced = 0

    def next_track(self):
        i = self.cursor + 1
        return self.timeline[i] if i < len(self.timeline) else None

    def extend_timeline(self, tracks):
        self.timeline.extend(tracks)

    def step_to_next(self, track, stream):
        self.cursor += 1
        self.stepped.append(stream.url)

    def run_worker(self, work, *, group, exclusive=False, thread=False):
        self.tasks.append((group, thread, work))

    async def settle(self):
        """Run what was handed to run_worker (threads inline)."""
        while self.tasks:
            _, _, work = self.tasks.pop(0)
            result = work()
            if asyncio.iscoroutine(result):
                await result


def _make():
    host, player, streams = Host(), Player(), Streams()

    async def radio(seed):
        return [_track("R1")]

    def advance():
        host.advanced += 1

    q = GaplessQueue(
        host, player=lambda: player, streams=streams, fetch_radio=radio, advance=advance
    )
    return host, player, streams, q


def _run(coro):
    asyncio.run(coro)


def test_it_waits_for_the_end_to_be_near_and_queues_once():
    async def body():
        host, player, _, q = _make()
        host.playback_pos = 100.0  # 100 s left
        q.maybe_prefetch()
        assert host.tasks == []
        host.playback_pos = 175.0
        q.maybe_prefetch()
        q.maybe_prefetch()  # a second tick while the first is under way
        assert len(host.tasks) == 1
        await host.settle()
        assert player.appended == ["url-B"] and q.next_up[0] == "B"
        q.maybe_prefetch()  # already queued
        assert host.tasks == []

    _run(body())


def test_each_reason_not_to_prefetch_holds():
    for attr, value in [
        ("gapless", False),
        ("is_local", False),
        ("is_playing", False),
        ("is_paused", True),
        ("playback_dur", 0.0),
    ]:
        host, player, _, q = _make()
        setattr(host, attr, value)
        q.maybe_prefetch()
        assert host.tasks == [], attr
    host, player, _, q = _make()
    player.running = False
    q.maybe_prefetch()
    assert host.tasks == []


def test_a_preview_is_remembered_and_not_queued():
    async def body():
        host, player, streams, q = _make()
        streams.sample.add("B")
        q.maybe_prefetch()
        await host.settle()
        assert q.next_up is None and q.failed == "B" and player.appended == []
        q.maybe_prefetch()
        await host.settle()
        assert streams.asked == ["B"]  # not asked again

    _run(body())


def test_endless_radio_refills_when_the_queue_ran_out():
    async def body():
        host, player, _, q = _make()
        host.radio_mode = True
        host.cursor = 2
        host.now_playing = host.timeline[2]
        q.maybe_prefetch()
        await host.settle()
        assert [t.id for t in host.timeline][-1] == "R1" and player.appended == ["url-R1"]

    _run(body())


def test_a_queue_edit_drops_what_is_queued_and_clears_it_in_mpv():
    async def body():
        host, player, _, q = _make()
        q.maybe_prefetch()
        await host.settle()
        host.timeline.insert(1, _track("X"))  # the next track is no longer B
        q.check()
        assert q.next_up is None
        await host.settle()
        assert player.cleared == [1]

    _run(body())


def test_an_edit_that_keeps_the_next_track_keeps_it_queued():
    async def body():
        host, player, _, q = _make()
        q.maybe_prefetch()
        await host.settle()
        host.timeline.append(_track("D"))
        q.check()
        assert q.next_up[0] == "B" and player.cleared == []

    _run(body())


def test_when_mpv_moves_on_the_cursor_follows_or_the_ordinary_way_runs():
    async def body():
        host, _, _, q = _make()
        q.next_up = ("B", _stream("B"))
        q.on_transition()
        assert host.stepped == ["url-B"] and host.cursor == 1 and q.next_up is None
        # Edited just as mpv moved on: not the queued track any more.
        q.next_up = ("C", _stream("C"))
        host.timeline.insert(2, _track("X"))
        q.on_transition()
        assert host.advanced == 1 and host.stepped == ["url-B"]
        # An advance already in flight, or Connect: leave it alone.
        q.next_up = ("X", _stream("X"))
        host.advance_busy = True
        q.on_transition()
        assert host.advanced == 1 and q.next_up is None

    _run(body())


def test_a_new_mpv_forgets_the_queue_and_the_failed_track():
    _, _, _, q = _make()
    q.next_up, q.failed = ("B", _stream("B")), "C"
    q.reset()
    assert q.next_up is None and q.failed is None

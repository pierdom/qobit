"""LyricsController without the app: a fake host that runs workers on the
current loop and records timers, and a fake lyrics service."""

from __future__ import annotations

import asyncio

from qobit.lyrics import Line, Lyrics, Query, Result
from qobit.qobuz.models import Track
from qobit.ui import lyrics_controller as lc
from qobit.ui.lyrics_controller import LyricsController

SYNCED = Lyrics([Line("a", 10.0), Line("b", 20.0)], synced=True, source="X")


def _track(name: str) -> Track:
    return Track(id=name, title=name, artist="A", album="B", album_id="1", duration=200)


class Timer:
    def __init__(self, delay, callback):
        self.delay, self.callback, self.stopped = delay, callback, False

    def stop(self):
        self.stopped = True


class Service:
    def __init__(self, **answers: Result):
        self.answers = answers
        self.offsets: list[float] = []
        self.rejected: list[Query] = []

    async def lookup(self, query: Query) -> Result:
        return self.answers.get(query.title, Result("none"))

    async def set_offset(self, query: Query, offset: float) -> None:
        self.offsets.append(offset)

    async def reject(self, query: Query) -> None:
        self.rejected.append(query)

    async def aclose(self) -> None:
        pass


class Host:
    def __init__(self):
        self.now_playing = None
        self.playback_pos, self.playback_dur = 0.0, 200.0
        self.is_playing, self.is_paused = True, False
        self.is_attached = self.is_running = self.lyrics_enabled = True
        self.lyrics_status, self.lyrics_line, self.lyrics_version = "idle", -1, 0
        self.tasks: list[asyncio.Task] = []
        self.timers: list[Timer] = []
        self.flashes: list[str] = []
        self.next: Track | None = None

    def run_worker(self, work, *, group, exclusive):
        self.tasks.append(asyncio.ensure_future(work()))

    def set_timer(self, delay, callback):
        self.timers.append(Timer(delay, callback))
        return self.timers[-1]

    async def settle(self):
        await asyncio.gather(*self.tasks)
        self.tasks.clear()


def _make(**answers):
    host, service = Host(), Service(**answers)
    ctl = LyricsController(
        host, next_track=lambda: host.next, flash=host.flashes.append, service=service
    )
    return host, service, ctl


def _run(coro):
    asyncio.run(coro)


def test_a_new_track_is_looked_up_and_the_line_follows_the_position():
    async def body():
        host, _, ctl = _make(A=Result("found", SYNCED))
        host.now_playing = _track("A")
        ctl.track_changed(host.now_playing)
        assert host.lyrics_status == "loading" and host.lyrics_version == 1
        await host.settle()
        assert host.lyrics_status == "found" and ctl.lyrics is SYNCED
        host.playback_pos = 19.0  # 19.3 with the lead: still line "a"
        ctl.update_line()
        assert host.lyrics_line == 0
        host.playback_pos = 19.8
        ctl.update_line()
        assert host.lyrics_line == 1

    _run(body())


def test_no_track_off_and_not_running_ask_nobody():
    async def body():
        host, _, ctl = _make(A=Result("found", SYNCED))
        ctl.track_changed(None)
        assert host.lyrics_status == "idle"
        host.lyrics_enabled = False
        ctl.track_changed(_track("A"))
        assert host.lyrics_status == "off"
        host.lyrics_enabled, host.is_running = True, False
        ctl.track_changed(_track("A"))
        assert host.lyrics_status == "idle" and host.tasks == []

    _run(body())


def test_an_answer_for_another_track_is_dropped():
    async def body():
        host, _, ctl = _make(A=Result("found", SYNCED))
        host.now_playing = _track("A")
        ctl.track_changed(host.now_playing)
        host.now_playing = _track("B")  # moved on before the answer
        await host.settle()
        assert ctl.lyrics is None and host.lyrics_status == "loading"

    _run(body())


def test_a_failed_lookup_is_retried_a_few_times_then_left():
    async def body():
        host, _, ctl = _make(A=Result("failed"))
        host.now_playing = _track("A")
        ctl.track_changed(host.now_playing)
        for n in range(lc.RETRIES):
            await host.settle()
            assert host.lyrics_status == "failed" and len(host.timers) == n + 1
            host.timers[-1].callback()  # the delay passed
            assert host.lyrics_status == "loading"
        await host.settle()
        assert len(host.timers) == lc.RETRIES  # no fourth retry

    _run(body())


def test_a_new_track_stops_the_pending_retry():
    async def body():
        host, _, ctl = _make(A=Result("failed"))
        host.now_playing = _track("A")
        ctl.track_changed(host.now_playing)
        await host.settle()
        timer = host.timers[0]
        ctl.track_changed(None)
        assert timer.stopped

    _run(body())


def test_nudges_save_the_latest_offset_and_reject_removes_the_lyrics():
    async def body():
        host, svc, ctl = _make(A=Result("found", SYNCED))
        host.now_playing = _track("A")
        ctl.track_changed(host.now_playing)
        await host.settle()
        ctl.nudge(lc.STEP)
        ctl.nudge(lc.STEP)
        await host.settle()
        assert ctl.offset == 1.0 and svc.offsets[-1] == 1.0
        assert host.flashes[-1] == "Lyrics +1.0 s"
        ctl.reject()
        await host.settle()
        assert ctl.lyrics is None and host.lyrics_status == "none" and len(svc.rejected) == 1
        ctl.reject()  # nothing left to reject
        assert len(svc.rejected) == 1

    _run(body())


def test_the_next_track_is_fetched_once_near_the_end():
    async def body():
        host, _, ctl = _make(N=Result("found", SYNCED))
        host.next = _track("N")
        host.playback_pos = 100.0
        ctl.maybe_prefetch()  # 100 s left: too early
        assert host.tasks == []
        host.playback_pos = 160.0
        ctl.maybe_prefetch()
        ctl.maybe_prefetch()
        assert len(host.tasks) == 1 and ctl.prefetched == "N"
        host.is_paused = True
        host.next = _track("M")
        ctl.maybe_prefetch()  # paused: nothing
        assert len(host.tasks) == 1
        await host.settle()

    _run(body())

"""Gapless playback in the app: late in a track the next one is queued in mpv
(`_maybe_prefetch`), and when mpv goes on to it by itself the cursor and the
transport follow (`_on_gapless_transition`) with nothing restarted.

Every way the queued track can stop being the right one is covered: the queue
edited, a restart of mpv, an unplayable track, Qobuz Connect, a pause. Runs
the real QobitApp headless with a fake player (no mpv here: tests/
test_gapless_player.py has the real one)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from album_pages import wait_until

from qobit.qobuz.models import StreamUrl, Track


def _track(name: str) -> Track:
    return Track(id=name, title=name, artist="A", album="B", album_id="1", duration=200)


def _stream(name: str, sample: bool = False) -> StreamUrl:
    return StreamUrl(f"url-{name}", 6, 44100.0, 16, sample=sample)


class FakePlayer:
    def __init__(self) -> None:
        self.gen = 1
        self.running = True
        self.appended: list[str] = []
        self.cleared: list[int] = []
        self.played: list[str] = []

    def snapshot(self) -> tuple[int, bool]:
        return self.gen, self.running

    def append(self, url: str, gen: int) -> bool:
        if gen != self.gen:
            return False
        self.appended.append(url)
        return True

    def clear_next(self, gen: int) -> None:
        self.cleared.append(gen)

    def play(self, url: str, start: float = 0.0) -> None:
        self.played.append(url)
        self.gen += 1  # what stop() does
        self.running = True

    def stop(self) -> None:
        self.gen += 1
        self.running = False

    def get_property(self, _name: str):
        return None

    def __getattr__(self, name: str):  # set_volume, seek, ...: not under test
        return lambda *a, **k: None


@pytest.fixture
def gapless(ui_app, monkeypatch):
    """The app with a fake player, a stub stream resolver and the queue
    A, B, C; ``start()`` has A playing 100 s into 200 (not yet late enough)."""
    player = FakePlayer()
    ui_app._player = player
    monkeypatch.setattr(ui_app, "_poll_player", lambda: None)  # sees no mpv: would end the track
    resolved: list[str] = []
    unplayable: set[str] = set()

    async def resolve(track_id: str):
        resolved.append(track_id)
        return _stream(track_id, sample=track_id in unplayable)

    monkeypatch.setattr(ui_app, "_resolve_stream", resolve)
    verified: list[int] = []
    monkeypatch.setattr(ui_app, "_verify_signal", lambda gen, stream: verified.append(gen))
    monkeypatch.setattr(ui_app, "_fetch_now_playing_album", lambda track: None)
    a, b, c = _track("A"), _track("B"), _track("C")

    def start() -> None:
        """The state of a track playing: the app resets some of it at mount."""
        ui_app._timeline = [a, b, c]
        ui_app._cursor = 0
        ui_app.now_playing = a
        ui_app.is_playing = True
        ui_app.is_paused = False
        ui_app.playback_dur = 200.0
        ui_app.playback_pos = 100.0

    return SimpleNamespace(
        app=ui_app,
        player=player,
        resolved=resolved,
        unplayable=unplayable,
        verified=verified,
        start=start,
    )


def _connect() -> SimpleNamespace:
    """Qobuz Connect, active (and stoppable when the app exits)."""
    return SimpleNamespace(active=True, session=SimpleNamespace(stop=lambda: None))


def _run(body):
    asyncio.run(body())


async def _late(g, pilot, pos: float = 175.0) -> None:
    g.app.playback_pos = pos  # within the last 30 s: what the poll does each tick
    await pilot.pause(0.2)


def test_nothing_is_queued_until_late_in_the_track(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            await _late(gapless, pilot, 150.0)  # 50 s left
            assert gapless.player.appended == [] and gapless.resolved == []
            await _late(gapless, pilot, 175.0)  # 25 s left
            await wait_until(pilot, lambda: gapless.player.appended == ["url-B"])
            assert gapless.app._next_up[0] == "B"

    _run(body)


def test_it_is_queued_once_not_every_tick(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            for pos in (172.0, 173.0, 174.0, 175.0, 176.0):
                await _late(gapless, pilot, pos)
            assert gapless.player.appended == ["url-B"]
            assert gapless.resolved == ["B"]

    _run(body)


def test_a_paused_track_queues_nothing(gapless):
    """The stream URL lasts minutes: paused near the end it could expire."""

    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            gapless.app.is_paused = True
            await _late(gapless, pilot)
            assert gapless.player.appended == []
            gapless.app.is_paused = False
            await _late(gapless, pilot, 176.0)
            await wait_until(pilot, lambda: gapless.player.appended == ["url-B"])

    _run(body)


def test_an_unplayable_next_track_is_left_to_the_natural_end(gapless):
    """A preview-only track isn't queued, and isn't asked for again each tick:
    the end of this track skips it with the usual notice."""
    gapless.unplayable.add("B")

    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            for pos in (172.0, 174.0, 176.0):
                await _late(gapless, pilot, pos)
            assert gapless.player.appended == []
            assert gapless.resolved == ["B"]
            assert gapless.app._prefetch_failed == "B"

    _run(body)


def test_the_last_track_queues_nothing(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            gapless.app._cursor = 2
            gapless.app.now_playing = gapless.app._timeline[2]
            await _late(gapless, pilot)
            assert gapless.player.appended == [] and gapless.app._next_up is None

    _run(body)


def test_endless_radio_refills_before_the_end_so_the_next_track_is_queued(gapless, monkeypatch):
    async def radio(seed: Track) -> list[Track]:
        return [_track("R1"), _track("R2")]

    monkeypatch.setattr(gapless.app, "_fetch_radio", radio)

    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            app = gapless.app
            app.radio_mode = True
            app._cursor = 2
            app.now_playing = app._timeline[2]
            await _late(gapless, pilot)
            await wait_until(pilot, lambda: gapless.player.appended == ["url-R1"])
            assert [t.id for t in app._timeline] == ["A", "B", "C", "R1", "R2"]

    _run(body)


def test_qobuz_connect_keeps_its_own_track_ends(gapless):
    """The server owns the queue under Connect: nothing is queued in mpv."""

    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            gapless.app._connect = _connect()
            await _late(gapless, pilot)
            assert gapless.player.appended == [] and gapless.resolved == []

    _run(body)


# ── the queue changes under it ──────────────────────────────────────────────


async def _queued(g, pilot) -> None:
    await _late(g, pilot)
    await wait_until(pilot, lambda: g.player.appended == ["url-B"])


def test_removing_the_queued_track_takes_it_out_of_mpv_and_queues_the_new_next(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            app = gapless.app
            await _queued(gapless, pilot)
            del app._timeline[1]  # B is removed from Up Next
            app.queue_version += 1
            assert app._next_up is None
            await wait_until(pilot, lambda: gapless.player.cleared == [gapless.player.gen])
            await _late(gapless, pilot, 176.0)
            await _late(gapless, pilot, 176.0)  # the poll's next tick
            await wait_until(pilot, lambda: gapless.player.appended == ["url-B", "url-C"])
            assert app._next_up[0] == "C"

    _run(body)


def test_an_edit_that_leaves_the_next_track_alone_keeps_it_queued(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            app = gapless.app
            await _queued(gapless, pilot)
            app._timeline.append(_track("D"))  # added at the end
            app.queue_version += 1  # as does every heart toggle
            await pilot.pause(0.3)
            assert app._next_up[0] == "B" and gapless.player.cleared == []

    _run(body)


def test_a_new_track_started_by_the_user_forgets_the_queued_one(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            app = gapless.app
            await _queued(gapless, pilot)
            assert await app._play_stream(app._timeline[2]) == "ok"
            assert gapless.player.played == ["url-C"]
            assert app._next_up is None  # a new mpv: nothing is queued in it

    _run(body)


def test_a_player_restarted_while_the_stream_resolved_gets_nothing(gapless, monkeypatch):
    """The resolve takes a while; meanwhile the user started another track
    (a new mpv, same socket): the old answer must not queue into it."""
    release = asyncio.Event
    gate = {}

    async def slow(track_id: str):
        gate["ev"] = release()
        await gate["ev"].wait()
        return _stream(track_id)

    monkeypatch.setattr(gapless.app, "_resolve_stream", slow)

    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            await _late(gapless, pilot)
            await wait_until(pilot, lambda: "ev" in gate)
            gapless.player.gen += 1  # mpv stopped and started again
            gate["ev"].set()
            await pilot.pause(0.3)
            assert gapless.player.appended == [] and gapless.app._next_up is None

    _run(body)


# ── mpv goes on by itself ───────────────────────────────────────────────────


def test_the_move_onto_the_queued_track_restarts_nothing(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            app = gapless.app
            await _queued(gapless, pilot)
            version = app.queue_version
            app._on_gapless_transition()  # what the poll calls when mpv moves on
            assert app._cursor == 1 and app.now_playing.id == "B"
            assert app._last_stream.url == "url-B"
            assert app.quality_label == _stream("B").quality_badge
            assert app.queue_version == version + 1
            assert app.is_playing and not app.is_paused
            assert app._next_up is None
            assert gapless.player.played == []  # mpv was not restarted
            assert gapless.player.cleared == []
            assert len(gapless.verified) == 1  # the signal lamp checks the new stream

    _run(body)


def test_the_next_track_is_queued_again_behind_the_one_now_playing(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            app = gapless.app
            await _queued(gapless, pilot)
            app._on_gapless_transition()
            app.playback_dur = 200.0
            await _late(gapless, pilot, 10.0)
            assert gapless.player.appended == ["url-B"]  # far from the end of B
            await _late(gapless, pilot, 180.0)
            await _late(gapless, pilot, 176.0)  # the poll's next tick
            await wait_until(pilot, lambda: gapless.player.appended == ["url-B", "url-C"])

    _run(body)


def test_a_move_the_queue_no_longer_expects_plays_the_right_track_the_ordinary_way(
    gapless, monkeypatch
):
    advanced: list[int] = []
    monkeypatch.setattr(gapless.app, "_advance_queue", lambda: advanced.append(1))

    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            app = gapless.app
            app._next_up = ("B", _stream("B"))
            del app._timeline[1]  # the queue was edited just as mpv moved on
            app._on_gapless_transition()
            assert advanced == [1]
            assert app._cursor == 0 and app.now_playing.id == "A"  # left to the advance
            await pilot.pause(0)

    _run(body)


def test_a_move_under_connect_changes_nothing(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            app = gapless.app
            app._next_up = ("B", _stream("B"))
            app._connect = _connect()
            app._on_gapless_transition()
            assert app._cursor == 0 and app.now_playing.id == "A"
            await pilot.pause(0)

    _run(body)


def test_off_queues_nothing(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            gapless.app.gapless = False
            await _late(gapless, pilot, 175.0)
            await pilot.pause(0.3)
            assert gapless.player.appended == [] and gapless.resolved == []

    _run(body)


def test_switching_off_takes_the_queued_track_out_again(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            await _late(gapless, pilot, 175.0)
            await wait_until(pilot, lambda: gapless.player.appended == ["url-B"])
            gapless.app.action_toggle_gapless()
            assert gapless.app._next_up is None and gapless.app.gapless is False
            await wait_until(pilot, lambda: gapless.player.cleared == [gapless.player.gen])
            await _late(gapless, pilot, 176.0)
            assert gapless.player.appended == ["url-B"]  # not queued again

    _run(body)


def test_the_transport_title_carries_a_tag_while_on(gapless):
    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            bar = gapless.app.screen_stack[0].query_one("TransportBar")
            assert "GAPLESS" in bar.border_title
            gapless.app.action_toggle_gapless()
            await pilot.pause(0.1)
            assert "GAPLESS" not in bar.border_title
            gapless.app.action_toggle_gapless()
            gapless.app.connect_state = "active"  # the server owns track ends
            await pilot.pause(0.1)
            assert "GAPLESS" not in bar.border_title

    _run(body)


def test_the_flag_and_the_saved_setting(monkeypatch):
    from qobit import config
    from qobit.ui.app import QobitApp

    assert config.get_gapless() is True
    config.set_gapless(False)
    assert QobitApp(connect=False).gapless is False  # the saved setting
    assert QobitApp(connect=False, gapless=True).gapless is True  # the flag wins
    config.set_gapless(True)
    assert QobitApp(connect=False, gapless=False).gapless is False


def test_shuffling_while_a_track_is_lined_up_swaps_it(gapless, monkeypatch):
    import qobit.ui.play_context as pc

    monkeypatch.setattr(pc.random, "shuffle", lambda x: x.reverse())  # B, C -> C, B

    async def body():
        async with gapless.app.run_test(size=(120, 30)) as pilot:
            gapless.start()
            await _late(gapless, pilot, 175.0)
            await wait_until(pilot, lambda: gapless.player.appended == ["url-B"])
            gapless.app.action_toggle_shuffle()  # the next track is now C
            await wait_until(pilot, lambda: gapless.player.cleared == [gapless.player.gen])
            await _late(gapless, pilot, 176.0)  # the poll's next tick
            await wait_until(pilot, lambda: gapless.player.appended == ["url-B", "url-C"])
            assert gapless.app._next_up[0] == "C"

    _run(body)

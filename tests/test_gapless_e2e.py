"""The whole chain on a real mpv: the app's poll thread, the prefetch, mpv
going on by itself and the transport following. Three short tracks play
through one mpv process, in order, and the end of the last still ends it.
Skipped without mpv."""

from __future__ import annotations

import asyncio
import math
import shutil
import struct
import time
import wave

import pytest

from qobit.audio.player import MpvPlayer
from qobit.qobuz.models import StreamUrl, Track


def _tone(path, freq: float, seconds: float) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        frames = bytearray()
        for i in range(int(44100 * seconds)):
            v = int(6000 * math.sin(2 * math.pi * freq * i / 44100))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))


@pytest.fixture
def rig(ui_app, monkeypatch, tmp_path):
    """The app on a real mpv (null audio) and three 2 s tracks, A, B, C."""
    if shutil.which("mpv") is None:
        pytest.skip("mpv isn't installed")
    files = {}
    for name, freq in (("A", 330), ("B", 440), ("C", 550)):
        files[name] = tmp_path / f"{name}.wav"
        _tone(files[name], freq, 2.0)

    player = MpvPlayer()
    player._sock = tmp_path / "mpv.sock"
    flags = player._flags
    monkeypatch.setattr(player, "_flags", lambda: [*flags(), "--ao=null"])
    ui_app._player = player

    async def resolve(track_id: str):
        return StreamUrl(str(files[track_id]), 6, 44100.0, 16)

    monkeypatch.setattr(ui_app, "_resolve_stream", resolve)
    monkeypatch.setattr(ui_app, "_verify_signal", lambda gen, stream: None)
    monkeypatch.setattr(ui_app, "_fetch_now_playing_album", lambda track: None)
    return ui_app, player


def _track(name: str) -> Track:
    return Track(id=name, title=name, artist="X", album="Y", album_id="1", duration=2)


async def _play_through(app, player, pilot, after_queued=None) -> list[tuple[str, int | None]]:
    """Play A (then B, C) to the end; the order of tracks and the mpv each
    was heard in."""
    app.play_track(_track("A"), queue=[_track("B"), _track("C")])
    seen: list[tuple[str, int | None]] = []
    end = time.monotonic() + 25
    while time.monotonic() < end:
        await pilot.pause(0.05)
        if after_queued is not None and app._next_up is not None:
            after_queued()
            after_queued = None
        now = app.now_playing.id if app.now_playing else None
        pid = player._proc.pid if player._proc is not None else None
        if now and (not seen or seen[-1][0] != now):
            seen.append((now, pid))
        if now == seen[-1][0] == app._timeline[-1].id and not app.is_playing:
            break  # the last track ended
    return seen


def test_three_tracks_play_through_one_mpv(rig):
    app, player = rig

    async def run():
        async with app.run_test(size=(120, 30)) as pilot:
            return await _play_through(app, player, pilot)

    seen = asyncio.run(run())
    assert [name for name, _ in seen] == ["A", "B", "C"]
    assert len({pid for _, pid in seen}) == 1  # one mpv for all three
    assert app._cursor == 2 and not app.is_playing


def test_a_track_removed_after_it_was_queued_in_mpv_is_skipped(rig):
    """B is already in mpv's playlist when it's removed from Up Next: it must
    be taken out again, and C played after A."""
    app, player = rig

    def remove_b() -> None:
        del app._timeline[1]
        app.queue_version += 1

    async def run():
        async with app.run_test(size=(120, 30)) as pilot:
            return await _play_through(app, player, pilot, after_queued=remove_b)

    seen = asyncio.run(run())
    assert [name for name, _ in seen] == ["A", "C"]
    assert [t.id for t in app._timeline] == ["A", "C"]

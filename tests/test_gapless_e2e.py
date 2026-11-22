"""The whole chain on a real mpv: the app's poll thread, the prefetch, mpv
going on by itself and the transport following. Three short tracks play
through one mpv process, in order, and the end of the last still ends it.
Skipped without mpv."""

from __future__ import annotations

import asyncio
import math
import shutil
import struct
import subprocess
import time
import traceback
import wave
from pathlib import Path

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
    """The app on a real mpv (null audio) and three 4 s tracks, A, B, C."""
    if shutil.which("mpv") is None:
        pytest.skip("mpv isn't installed")
    files = {}
    for name, freq in (("A", 330), ("B", 440), ("C", 550)):
        files[name] = tmp_path / f"{name}.wav"
        _tone(files[name], freq, 4.0)  # long enough for a slow runner to queue the next

    player = MpvPlayer()
    player._sock = tmp_path / "mpv.sock"
    flags = player._flags
    monkeypatch.setattr(player, "_flags", lambda: [*flags(), "--ao=null", "--msg-level=all=info"])
    # Each mpv's own output and exit code, printed when the test fails.
    real_popen = subprocess.Popen
    procs: list[tuple[subprocess.Popen, Path]] = []

    def popen(cmd, **kw):
        log = tmp_path / f"mpv{len(procs)}.log"
        kw["stderr"] = open(log, "wb")  # noqa: SIM115  # lives with the process
        kw["stdout"] = kw["stderr"]
        proc = real_popen(cmd, **kw)
        procs.append((proc, log))
        return proc

    monkeypatch.setattr("qobit.audio.player.subprocess.Popen", popen)

    real_stop = player.stop

    def stop():
        """Who ended the track: the caller's stack, in the failure report."""
        stack = traceback.extract_stack()[-7:-1]
        frames = [f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}" for f in stack]
        LOG.append(f"{time.monotonic() - _T0[0]:5.2f}s stop() <- " + " < ".join(reversed(frames)))
        real_stop()

    monkeypatch.setattr(player, "stop", stop)

    real_advance = ui_app._advance_queue

    def advance():
        """Why the next track was asked for: what the player and the app were
        doing then (running? a gapless entry queued? mpv's exit code?)."""
        gen, running = player.snapshot()
        proc = player._proc
        queued = ui_app._next_up[0] if ui_app._next_up else None
        code = proc.poll() if proc else "no proc"
        LOG.append(
            f"{time.monotonic() - _T0[0]:5.2f}s advance: gen={gen} running={running} "
            f"returncode={code} next_up={queued} is_playing={ui_app.is_playing} "
            f"pos={ui_app.playback_pos:.2f} dur={ui_app.playback_dur:.2f}"
        )
        return real_advance()

    monkeypatch.setattr(ui_app, "_advance_queue", advance)

    real_get = player.get_property
    last_entry = [object()]

    def get_property(prop):
        """What the poll sees as the playlist position, each time it changes."""
        value = real_get(prop)
        if prop == "playlist-pos" and value != last_entry[0]:
            last_entry[0] = value
            LOG.append(f"{time.monotonic() - _T0[0]:5.2f}s playlist-pos reads {value!r}")
        return value

    monkeypatch.setattr(player, "get_property", get_property)

    real_transition = ui_app._on_gapless_transition

    def transition():
        queued = ui_app._next_up[0] if ui_app._next_up else None
        LOG.append(f"{time.monotonic() - _T0[0]:5.2f}s gapless transition, next_up={queued}")
        return real_transition()

    monkeypatch.setattr(ui_app, "_on_gapless_transition", transition)
    LOG_SOURCES.clear()
    LOG_SOURCES.append(procs)
    ui_app._player = player

    async def resolve(track_id: str):
        return StreamUrl(str(files[track_id]), 6, 44100.0, 16)

    monkeypatch.setattr(ui_app, "_resolve_stream", resolve)
    monkeypatch.setattr(ui_app, "_verify_signal", lambda gen, stream: None)
    monkeypatch.setattr(ui_app, "_fetch_now_playing_album", lambda track: None)
    return ui_app, player


def _track(name: str) -> Track:
    return Track(id=name, title=name, artist="X", album="Y", album_id="1", duration=2)


LOG: list[str] = []  # what happened when, printed when an assertion fails
_T0 = [0.0]  # when _play_through started
LOG_SOURCES: list[list] = []  # the mpv processes of this test (see the rig)


def _mpv_report() -> str:
    out = []
    for proc, log in LOG_SOURCES[0] if LOG_SOURCES else []:
        try:
            text = log.read_text(errors="replace")[-1500:]
        except OSError:
            text = "(no log)"
        out.append(f"--- mpv pid {proc.pid} exit code {proc.poll()}\n{text}")
    return "\n".join(out)


async def _play_through(app, player, pilot, after_queued=None) -> list[tuple[str, int | None]]:
    """Play A (then B, C) to the end; the order of tracks and the mpv each
    was heard in."""
    LOG.clear()
    _T0[0] = time.monotonic()
    app.play_track(_track("A"), queue=[_track("B"), _track("C")])
    seen: list[tuple[str, int | None]] = []
    start = time.monotonic()
    end = start + 25
    last = None
    while time.monotonic() < end:
        await pilot.pause(0.05)
        state = (
            app.now_playing.id if app.now_playing else None,
            player._proc.pid if player._proc is not None else None,
            app._next_up[0] if app._next_up else None,
            app.is_playing,
        )
        if state != last:
            last = state
            LOG.append(f"{time.monotonic() - start:5.2f}s now/pid/next_up/playing={state}")
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
    assert len({pid for _, pid in seen}) == 1, "\n".join(LOG) + "\n" + _mpv_report()
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

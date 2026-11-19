"""The mpv side of gapless playback: the flags that keep it bit-perfect, the
append that queues the next track in the same mpv, and (when mpv is
installed) a real mpv moving from one track to the next without a restart."""

from __future__ import annotations

import json
import math
import shutil
import struct
import time
import wave

import pytest

import qobit.audio.player as player_module
from qobit.audio.player import EntryWatch, MpvPlayer

# ── flags and the guard (no mpv) ───────────────────────────────────────────


def test_gapless_never_resamples():
    """ "yes" keeps the first track's rate and resamples the rest: that would
    break bit-perfect. "weak" reopens the device when the format changes."""
    flags = MpvPlayer()._flags()
    assert "--gapless-audio=weak" in flags
    assert "--gapless-audio=yes" not in flags
    assert "--prefetch-playlist=yes" in flags  # the next stream opened early


class _Conn:
    """A stand-in for mpv's IPC socket: records what is sent, answers ok."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._reply = b""

    def sendall(self, data: bytes) -> None:
        msg = json.loads(data)
        self.sent.append(msg)
        self._reply += json.dumps({"request_id": msg["request_id"], "error": "success"}).encode()
        self._reply += b"\n"

    def recv(self, _n: int) -> bytes:
        out, self._reply = self._reply, b""
        return out

    def close(self) -> None:
        pass


@pytest.fixture
def wired(monkeypatch, tmp_path):
    p = MpvPlayer()
    p._sock = tmp_path / "mpv.sock"
    p._conn = _Conn()
    return p


def test_append_queues_the_url_behind_the_playing_track(wired):
    wired._stop_gen = 3
    assert wired.append("https://x/next.flac", 3) is True
    assert wired._conn.sent[0]["command"] == ["loadfile", "https://x/next.flac", "append"]


def test_append_to_a_restarted_mpv_is_dropped(wired):
    """The socket path is the same for every mpv qobit starts: a late append
    meant for the old one must not queue a stale track in the new one."""
    wired._stop_gen = 4  # stopped and started again since the caller looked
    assert wired.append("https://x/next.flac", 3) is False
    assert wired._conn.sent == []
    wired.clear_next(3)
    assert wired._conn.sent == []


def test_clear_next_keeps_the_playing_entry(wired):
    wired._stop_gen = 1
    wired.clear_next(1)
    assert wired._conn.sent[0]["command"] == ["playlist-clear"]


def test_entry_watch_sees_mpv_move_on_within_one_process():
    w = EntryWatch()
    assert w.moved_on(1, 0) is False  # first look
    assert w.moved_on(1, 0) is False
    assert w.moved_on(1, 1) is True  # went on to the queued track
    assert w.moved_on(1, 1) is False
    assert w.moved_on(1, None) is False  # between entries
    assert w.moved_on(1, 2) is True


def test_entry_watch_starts_afresh_for_a_new_process():
    w = EntryWatch()
    w.moved_on(1, 0)
    w.moved_on(1, 1)
    assert w.moved_on(2, 0) is False  # another mpv: index 0 again is not a move
    assert w.moved_on(2, 1) is True
    w.reset()
    assert w.moved_on(2, 5) is False


# ── a real mpv ─────────────────────────────────────────────────────────────


def _tone(path, freq: float, seconds: float = 3.0) -> None:
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
def mpv_player(monkeypatch, tmp_path):
    """The real MpvPlayer and mpv, to a null audio output."""
    if shutil.which("mpv") is None:
        pytest.skip("mpv isn't installed")
    p = MpvPlayer()
    p._sock = tmp_path / "mpv.sock"
    flags = p._flags
    monkeypatch.setattr(p, "_flags", lambda: [*flags(), "--ao=null"])
    yield p
    p.stop()


def _wait(condition, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_mpv_goes_on_to_the_appended_track_in_the_same_process(mpv_player, tmp_path):
    first, second = tmp_path / "a.wav", tmp_path / "b.wav"
    _tone(first, 440, 2.0)
    _tone(second, 660, 2.0)
    mpv_player.play(str(first))
    gen, running = mpv_player.snapshot()
    assert running
    assert _wait(lambda: mpv_player.get_property("time-pos") is not None)
    # mpv accepted the flags (an unknown one would have made it exit).
    assert mpv_player.get_property("gapless-audio") == "weak"

    pid = mpv_player._proc.pid
    assert mpv_player.append(str(second), gen)
    assert mpv_player.get_property("playlist-count") == 2

    watch = EntryWatch()
    moved = []
    assert _wait(
        lambda: (
            moved.append(watch.moved_on(gen, mpv_player.get_property("playlist-pos"))) or any(moved)
        ),
        timeout=15,
    )
    assert mpv_player._proc.pid == pid  # the same mpv: not stopped and started again
    assert mpv_player.snapshot() == (gen, True)


def test_a_cleared_next_track_is_not_played(mpv_player, tmp_path):
    first, second = tmp_path / "a.wav", tmp_path / "b.wav"
    _tone(first, 440, 3.0)
    _tone(second, 660, 3.0)
    mpv_player.play(str(first))
    gen, _ = mpv_player.snapshot()
    assert _wait(lambda: mpv_player.get_property("time-pos") is not None)
    assert mpv_player.append(str(second), gen)
    mpv_player.clear_next(gen)
    assert mpv_player.get_property("playlist-count") == 1
    assert mpv_player.get_property("playlist-pos") == 0


def test_the_end_of_the_last_track_still_ends_mpv(mpv_player, tmp_path):
    """Nothing appended: mpv exits at the end as before, which is what the
    app's natural-end handling (advance, endless radio) relies on."""
    only = tmp_path / "a.wav"
    _tone(only, 440, 1.0)
    mpv_player.play(str(only))
    gen, _ = mpv_player.snapshot()
    assert _wait(lambda: mpv_player.snapshot() == (gen, False), timeout=15)


def test_mpv_play_flags_still_name_no_url(monkeypatch, tmp_path):
    """Unchanged by gapless: the stream URL isn't in argv."""
    seen = {}

    class _Proc:
        def __init__(self, cmd, **_kw):
            seen["cmd"] = cmd

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(player_module.subprocess, "Popen", _Proc)
    p = MpvPlayer()
    p._sock = tmp_path / "mpv.sock"
    p.play("https://secret.example/stream.flac?sig=abc")
    assert not any("secret.example" in arg for arg in seen["cmd"])

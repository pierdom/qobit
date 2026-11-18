import contextlib
import json
import platform
import socket
import subprocess
import threading
import time
from pathlib import Path

from .._fs import private_dir, write_private

_SOCK_PATH = Path.home() / ".local" / "share" / "qobit" / "mpv.sock"
STREAM_PLAYLIST = "stream.m3u"  # next to the socket; see play()
_SYSTEM = platform.system()


class EntryWatch:
    """Sees mpv go on to the next playlist entry by itself (a gapless move):
    its ``playlist-pos`` rises within one process. The poll feeds it each
    tick; a new process (another stop generation) starts afresh."""

    def __init__(self) -> None:
        self._gen: int | None = None
        self._entry: int | None = None

    def moved_on(self, gen: int, entry: object) -> bool:
        if gen != self._gen:
            self._gen, self._entry = gen, None
        if not isinstance(entry, int):
            return False
        moved = self._entry is not None and entry > self._entry
        self._entry = entry
        return moved

    def reset(self) -> None:
        self._gen = self._entry = None


class MpvPlayer:
    def __init__(self, audio_device: str | None = None, volume: int | None = None) -> None:
        self._device = audio_device
        # None = bit-perfect default: no --volume flag at all, mpv plays at its
        # own unity gain. Only ever set to a non-None value by the caller when
        # the user has explicitly opted into software volume (see
        # QobitApp.action_toggle_software_volume) — this class never decides
        # that on its own.
        self._volume = volume
        self._proc: subprocess.Popen | None = None
        self._sock = _SOCK_PATH
        # incremented on every stop(); lets _poll_player distinguish natural end from forced stop
        self._stop_gen: int = 0
        # Guards _stop_gen + _proc together so snapshot() can never observe a
        # half-done stop() (new gen while the old process still looks alive) —
        # that mix made the poll thread mistake a stop for a natural end.
        self._state_lock = threading.Lock()
        # One IPC connection reused for the life of the mpv process, instead of
        # reconnecting per call — _poll_player alone was opening/closing a fresh
        # socket 6x/second. Guarded by a lock since the poll thread and the main
        # thread (pause/seek actions) can both call _ipc concurrently.
        self._conn: socket.socket | None = None
        self._conn_lock = threading.Lock()
        self._recv_buf: bytes = b""
        self._request_id: int = 0

    # --- public API ---

    def play(self, url: str, start: float = 0.0) -> None:
        self.stop()
        # 0700: anyone who can reach mpv's IPC socket can make it run programs.
        private_dir(self._sock.parent)
        if self._sock.exists():
            self._sock.unlink()

        cmd = ["mpv", *self._flags()]
        if start > 0:
            cmd.append(f"--start={start:.3f}")
        # The URL goes in a private one-line playlist, not argv: any local
        # user can read argv (ps), and a stream URL is signed for minutes and
        # names the Qobuz account. Never a positional argument either: a
        # string starting with "--" would be read as an mpv option.
        write_private(self.playlist_path, url + "\n")
        cmd.append(f"--playlist={self.playlist_path}")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self._state_lock:
            self._proc = proc

    def stop(self) -> None:
        # Bump the generation and drop the handle atomically, *then* terminate:
        # from this point snapshot() reports (new gen, not running).
        with self._state_lock:
            self._stop_gen += 1
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._close_conn()
        with contextlib.suppress(OSError):
            self.playlist_path.unlink()  # the signed URL: gone with the player

    def append(self, url: str, gen: int) -> bool:
        """Queue ``url`` after the current track in the running mpv, which then
        plays it with no gap (it keeps the output device open when the format
        matches, and has opened the stream by then: ``--prefetch-playlist``).
        ``gen`` is the ``snapshot()`` generation of the mpv the caller means: a
        player that was stopped and restarted meanwhile gets nothing (the
        socket path is the same, so the new mpv would take a stale track). The
        URL goes over the IPC socket (0700), not argv. False if refused."""
        result = self._ipc(["loadfile", url, "append"], only_gen=gen)
        return bool(result and result.get("error") == "success")

    def clear_next(self, gen: int) -> None:
        """Take what ``append`` queued out of mpv again (the queue changed)."""
        self._ipc(["playlist-clear"], only_gen=gen)  # keeps the playing entry

    @property
    def playlist_path(self) -> Path:
        """The one-line playlist mpv reads the stream URL from (0600, in the
        0700 socket directory)."""
        return self._sock.parent / STREAM_PLAYLIST

    def snapshot(self) -> tuple[int, bool]:
        """``(stop_gen, running)`` read atomically — what end-of-track detection
        must use instead of reading the two separately."""
        with self._state_lock:
            return self._stop_gen, self._proc is not None and self._proc.poll() is None

    def pause_toggle(self) -> None:
        self._cmd(["cycle", "pause"])

    def set_paused(self, paused: bool) -> None:
        """Explicit pause state (Connect commands are absolute, not toggles)."""
        self._cmd(["set_property", "pause", paused])

    def set_volume(self, value: int) -> None:
        """Apply a software volume level (0-100) to the running track and
        remember it for the next play() call."""
        self._volume = value
        self._cmd(["set_property", "volume", value])

    def reset_volume(self) -> None:
        """Back to unity gain on the running track, and stop passing
        --volume at all on future plays (the true bit-perfect default)."""
        self._volume = None
        self._cmd(["set_property", "volume", 100])

    def seek(self, delta: float) -> None:
        self._cmd(["seek", delta, "relative"])

    def seek_to(self, position: float) -> None:
        self._cmd(["seek", position, "absolute"])

    def get_property(self, prop: str) -> object:
        result = self._ipc(["get_property", prop])
        if result and result.get("error") == "success":
            return result.get("data")
        return None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # --- internals ---

    def _flags(self) -> list[str]:
        flags = [
            "--no-video",
            "--really-quiet",
            f"--input-ipc-server={self._sock}",
            "--af-clr",
            "--audio-pitch-correction=no",
            # Gapless between tracks with the same format; a new sample rate
            # or depth reopens the device. Never "yes": that keeps the first
            # track's parameters and resamples the rest, and a user's own
            # mpv.conf could say so: bit-perfect first.
            "--gapless-audio=weak",
            # Open the next playlist entry's stream early (the next track is a
            # network stream: opened only at the boundary it leaves a gap).
            "--prefetch-playlist=yes",
            # Don't auto-load mpv scripts. The system mpv-mpris plugin would
            # otherwise publish a second MPRIS player exposing the tagless
            # stream URL — wrong artist/title/album/art in the DE. qobit owns
            # the MPRIS surface via audio/media_keys.py instead.
            "--load-scripts=no",
        ]
        if self._device:
            flags.append(f"--audio-device={self._device}")
        # Exclusive mode on macOS prevents the system mixer from resampling.
        # On Linux with alsa/hw: the hw plugin itself refuses to resample.
        if _SYSTEM == "Darwin" and self._device and "coreaudio" in self._device:
            flags.append("--audio-exclusive=yes")
        # Only ever present when the user opted into software volume (see
        # QobitApp.action_toggle_software_volume) — omitting the flag entirely
        # at unity gain, rather than passing --volume=100, keeps the default,
        # never-touched-this-feature path identical to before it existed.
        if self._volume is not None:
            flags.append(f"--volume={self._volume}")
            # mpv's default volume-max (130) permits amplification above
            # unity; cap it at exactly 100 so this is only ever attenuation,
            # never clipping-risk gain, regardless of what value is passed.
            flags.append("--volume-max=100")
        return flags

    def _close_conn(self) -> None:
        with self._conn_lock:
            self._close_conn_locked()

    def _close_conn_locked(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None
        self._recv_buf = b""

    def _connect_locked(self) -> socket.socket | None:
        """Open a fresh IPC connection. Caller must hold _conn_lock."""
        # Wait up to 3 s for the IPC socket to appear after mpv starts.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self._sock.exists():
                break
            time.sleep(0.05)
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(str(self._sock))
        except Exception:
            return None
        self._conn = s
        self._recv_buf = b""
        return s

    def _recv_line_locked(self, s: socket.socket) -> bytes | None:
        """Read one newline-delimited message, buffering any extra bytes
        (mpv may batch several JSON lines, or an event may split across
        recv() calls)."""
        while b"\n" not in self._recv_buf:
            chunk = s.recv(4096)
            if not chunk:
                return None
            self._recv_buf += chunk
        line, self._recv_buf = self._recv_buf.split(b"\n", 1)
        return line

    def _ipc(self, cmd: list, only_gen: int | None = None) -> dict | None:
        """One command and its answer. ``only_gen``: send only while that is
        still the stop generation, i.e. to the mpv the caller meant."""
        with self._conn_lock:
            self._request_id += 1
            req_id = self._request_id
            msg = json.dumps({"command": cmd, "request_id": req_id}) + "\n"
            # One retry on a fresh connection if the reused one turns out dead
            # (e.g. mpv exited between calls) or desynced.
            for _attempt in range(2):
                s = self._conn if self._conn is not None else self._connect_locked()
                if s is None:
                    return None
                if only_gen is not None and self._stop_gen != only_gen:
                    return None  # stopped (and maybe restarted) meanwhile
                try:
                    s.sendall(msg.encode())
                    # mpv shares this connection for unsolicited events (seek,
                    # pause, start-file, ...); skip any line that isn't the
                    # response to *this* request.
                    while True:
                        line = self._recv_line_locked(s)
                        if line is None:
                            raise OSError("mpv IPC connection closed")
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if obj.get("request_id") == req_id and "error" in obj:
                            return obj
                except Exception:
                    self._close_conn_locked()
                    continue
            return None

    def _cmd(self, cmd: list) -> None:
        self._ipc(cmd)

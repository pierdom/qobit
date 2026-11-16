import contextlib
import json
import platform
import socket
import subprocess
import threading
import time
from pathlib import Path

from .._fs import private_dir

_SOCK_PATH = Path.home() / ".local" / "share" / "qobit" / "mpv.sock"
_SYSTEM = platform.system()


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
        cmd.append(url)
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

    def _ipc(self, cmd: list) -> dict | None:
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

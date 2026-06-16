import json
import platform
import socket
import subprocess
import time
from pathlib import Path

_SOCK_PATH = Path.home() / ".local" / "share" / "qobit" / "mpv.sock"
_SYSTEM = platform.system()


class MpvPlayer:
    def __init__(self, audio_device: str | None = None) -> None:
        self._device = audio_device
        self._proc: subprocess.Popen | None = None
        self._sock = _SOCK_PATH

    # --- public API ---

    def play(self, url: str) -> None:
        self.stop()
        self._sock.parent.mkdir(parents=True, exist_ok=True)
        if self._sock.exists():
            self._sock.unlink()

        cmd = ["mpv"] + self._flags() + [url]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def pause_toggle(self) -> None:
        self._cmd(["cycle", "pause"])

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
        ]
        if self._device:
            flags.append(f"--audio-device={self._device}")
        # Exclusive mode on macOS prevents the system mixer from resampling.
        # On Linux with alsa/hw: the hw plugin itself refuses to resample.
        if _SYSTEM == "Darwin" and self._device and "coreaudio" in self._device:
            flags.append("--audio-exclusive=yes")
        return flags

    def _ipc(self, cmd: list) -> dict | None:
        # Wait up to 3 s for the IPC socket to appear after mpv starts.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self._sock.exists():
                break
            time.sleep(0.05)

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2.0)
                s.connect(str(self._sock))
                msg = json.dumps({"command": cmd}) + "\n"
                s.sendall(msg.encode())
                data = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n" in data:
                        break
            return json.loads(data.split(b"\n")[0])
        except Exception:
            return None

    def _cmd(self, cmd: list) -> None:
        self._ipc(cmd)

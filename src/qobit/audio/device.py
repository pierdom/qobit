import re
import subprocess
from dataclasses import dataclass


@dataclass
class DeviceInfo:
    id: str
    description: str


def list_devices() -> list[DeviceInfo]:
    """Return audio output devices available to mpv.

    mpv --audio-device=help prints lines like:
      'alsa/hw:CARD=DAC,DEV=0' (USB DAC)
      'coreaudio/AppleUSBAudioEngine:...' (USB DAC)
    We parse those and return them as-is so callers can pass the id straight
    back to mpv via --audio-device=<id>.
    """
    try:
        result = subprocess.run(
            ["mpv", "--audio-device=help"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout + result.stderr
    except FileNotFoundError:
        raise RuntimeError("mpv not found — install mpv and ensure it is in $PATH")
    except subprocess.TimeoutExpired:
        return []

    devices: list[DeviceInfo] = []
    for line in output.splitlines():
        m = re.match(r"\s+'([^']+)'\s+\((.+)\)\s*$", line)
        if m:
            device_id = m.group(1)
            description = m.group(2)
            if device_id == "auto":
                continue
            devices.append(DeviceInfo(id=device_id, description=description))

    return devices

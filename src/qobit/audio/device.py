import re
import subprocess
from dataclasses import dataclass


@dataclass
class DeviceInfo:
    id: str
    description: str
    bit_perfect: bool = False


def list_devices() -> list[DeviceInfo]:
    """Return audio output devices available to mpv.

    mpv --audio-device=help prints lines like:
      'alsa/front:CARD=DAC,DEV=0' (USB DAC)
      'pipewire/alsa_output...' (USB DAC)
      'coreaudio/AppleUSBAudioEngine:...' (USB DAC)
    We parse those and return them as-is so callers can pass the id straight
    back to mpv via --audio-device=<id>.

    On Linux we additionally **synthesize a raw `alsa/hw:CARD=...,DEV=...`
    entry** for every ALSA card mpv reports. mpv never lists the raw `hw:`
    PCM (only `sysdefault`/`front`/`surround`/`iec958` wrappers), yet `hw:` is
    the only device that bypasses PipeWire/PulseAudio and the ALSA `plug`/`dmix`
    resamplers — i.e. the only bit-perfect path. Those synthesized entries are
    flagged `bit_perfect=True` and sorted to the top so the picker offers them
    first. `coreaudio/...` devices are flagged bit-perfect on macOS for the
    same reason (exclusive-mode capable, no system mixer).
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
    # (CARD, DEV) -> human description, collected from the ALSA wrapper PCMs so
    # we can synthesize the raw hw: device that mpv itself never lists.
    alsa_cards: dict[tuple[str, str], str] = {}

    for line in output.splitlines():
        m = re.match(r"\s+'([^']+)'\s+\((.+)\)\s*$", line)
        if not m:
            continue
        device_id = m.group(1)
        description = m.group(2)
        if device_id == "auto":
            continue
        devices.append(
            DeviceInfo(
                id=device_id,
                description=description,
                bit_perfect=device_id.startswith("coreaudio/"),
            )
        )

        if device_id.startswith("alsa/"):
            card = re.search(r"CARD=([^,]+)", device_id)
            if card:
                dev = re.search(r"DEV=(\d+)", device_id)
                key = (card.group(1), dev.group(1) if dev else "0")
                # Strip the trailing "/<pcm-name>" qualifier mpv appends to the
                # description (e.g. ".../Front output") for a cleaner card name.
                alsa_cards.setdefault(key, description.split("/")[0].strip())

    synthesized: list[DeviceInfo] = []
    for (card, dev), desc in alsa_cards.items():
        hw_id = f"alsa/hw:CARD={card},DEV={dev}"
        if any(d.id == hw_id for d in devices):
            continue
        synthesized.append(
            DeviceInfo(id=hw_id, description=f"{desc} — bit-perfect (direct hw)", bit_perfect=True)
        )

    # Bit-perfect devices first, preserving discovery order within each group.
    return synthesized + devices

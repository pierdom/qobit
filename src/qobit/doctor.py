"""``qobit doctor``: what stands between the FLAC file and the DAC.

It reads the machine (mpv, the audio device, the sound server, mpv.conf, the
terminal) and reports what would stop the output being bit-perfect, with the
text to paste to fix it. It never writes anything and never needs a login.

UI-agnostic: every check works on a ``Probe`` (the real machine, or canned
answers in the tests) and returns ``Check`` rows; ``render`` makes the report.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .audio.device import DeviceInfo, parse_devices

Status = Literal["ok", "info", "warn", "fail"]

# The rates a PCM DAC plays. DSD rates are left out: qobit streams PCM FLAC.
RATE_LADDER = (44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000)
MIN_MPV = (0, 34)  # --prefetch-playlist (gapless) arrived in 0.34


@dataclass
class Check:
    status: Status
    title: str
    detail: str = ""
    fix: str = ""  # what to do: commands or config to paste


class Probe:
    """The machine, as the checks see it (replaced by canned answers in tests)."""

    def __init__(self) -> None:
        self.system = platform.system()
        self.home = Path.home()

    def run(self, cmd: list[str], timeout: float = 5.0) -> str | None:
        """stdout and stderr together; None if the command is missing or hangs."""
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout + r.stderr

    def read(self, path: str | Path) -> str | None:
        try:
            return Path(path).read_text(errors="replace")
        except OSError:
            return None

    def glob(self, pattern: str) -> list[str]:
        return sorted(glob.glob(pattern))

    def writable(self, path: str | Path) -> bool:
        return os.access(path, os.W_OK)

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def env(self, name: str) -> str | None:
        return os.environ.get(name)


# ── parsers (plain text in, facts out) ───────────────────────────────────────


def parse_mpv_version(text: str) -> tuple[int, int] | None:
    m = re.search(r"mpv\s+v?(\d+)\.(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_mpv_conf(text: str) -> list[tuple[int, str, str]]:
    """``(line, option, value)`` of the options mpv applies to everything: those
    before the first ``[profile]`` header. ``--no-x`` and bare ``x`` mean yes/no."""
    out: list[tuple[int, str, str]] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            break  # a profile: applied only when asked for
        key, sep, value = line.partition("=")
        key = key.strip().removeprefix("--")
        value = value.strip().strip("\"'") if sep else "yes"
        if key.startswith("no-"):
            key, value = key[3:], "no"
        out.append((n, key, value))
    return out


@dataclass
class Card:
    num: int
    id: str  # the short name ALSA's CARD= uses
    name: str


def parse_asound_cards(text: str) -> list[Card]:
    cards = []
    for m in re.finditer(r"^\s*(\d+)\s+\[(\S*)\s*\]:\s+(.*)$", text, re.M):
        cards.append(Card(int(m.group(1)), m.group(2), m.group(3).strip()))
    return cards


@dataclass
class Capabilities:
    rates: list[int]
    bits: list[int]


def parse_stream(text: str) -> Capabilities | None:
    """The playback rates and sample sizes of a USB card (``/proc/asound/cardN/
    stream*``). A continuous range ("8000 - 96000") is expanded to the ladder."""
    play = text.split("Playback:", 1)
    if len(play) < 2:
        return None
    body = play[1].split("Capture:", 1)[0]
    rates: set[int] = set()
    for line in re.findall(r"Rates?:\s*(.+)", body):
        rng = re.match(r"(\d+)\s*-\s*(\d+)", line)
        if rng and "," not in line:
            lo, hi = int(rng.group(1)), int(rng.group(2))
            rates |= {r for r in RATE_LADDER if lo <= r <= hi}
        else:
            rates |= {int(r) for r in re.findall(r"\d+", line)}
    bits = {int(b) for b in re.findall(r"Bits:\s*(\d+)", body)}
    if not bits:
        bits = {int(b) for b in re.findall(r"Format:\s*[SU](\d+)", body)}
    return Capabilities(sorted(rates), sorted(bits)) if rates else None


def parse_pw_settings(text: str) -> dict[str, str]:
    """``pw-metadata -n settings`` lines: update: id:0 key:'x' value:'y' ..."""
    return dict(re.findall(r"key:'([^']*)' value:'([^']*)'", text))


def parse_rates(value: str) -> list[int]:
    return [int(r) for r in re.findall(r"\d+", value)]


def parse_pcm_status(text: str) -> tuple[str, int | None]:
    """``(state, owner pid)`` of a playback substream's ``status`` file."""
    state = re.search(r"state:\s*(\w+)", text)
    owner = re.search(r"owner_pid\s*:\s*(\d+)", text)
    return (state.group(1) if state else text.strip().split()[0] if text.strip() else "?"), (
        int(owner.group(1)) if owner else None
    )


def device_kind(device_id: str | None) -> str:
    """alsa-hw (raw hardware: exclusive), alsa (a plug/dmix wrapper), coreaudio,
    pipewire, pulse, auto, or other."""
    if not device_id:
        return "auto"
    if device_id.startswith("alsa/hw:"):
        return "alsa-hw"
    for kind in ("alsa", "coreaudio", "pipewire", "pulse"):
        if device_id.startswith(kind + "/"):
            return kind
    return "other"


def alsa_card_name(device_id: str) -> str | None:
    m = re.search(r"CARD=([^,]+)", device_id)
    return m.group(1) if m else None


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _khz(hz: int) -> str:
    return f"{hz / 1000:g}"


def _rates_text(rates: list[int]) -> str:
    return ", ".join(_khz(r) for r in rates) + " kHz"


def dropin_text(rates: list[int]) -> str:
    """The PipeWire drop-in for a DAC (see contrib/pipewire)."""
    joined = " ".join(str(r) for r in rates)
    return f"context.properties = {{\n    default.clock.allowed-rates = [ {joined} ]\n}}\n"


# ── the checks ───────────────────────────────────────────────────────────────


def check_mpv(p: Probe) -> tuple[list[Check], list[DeviceInfo]]:
    """mpv itself, and the devices it lists."""
    if p.which("mpv") is None:
        return [
            Check(
                "fail",
                "mpv is not installed",
                "qobit plays through mpv.",
                "Install it: brew install mpv (macOS), or your package manager (apt, dnf, "
                "pacman install mpv).",
            )
        ], []
    checks: list[Check] = []
    version = parse_mpv_version(p.run(["mpv", "--version"]) or "")
    if version is None:
        checks.append(Check("warn", "mpv answered, but not with a version"))
    elif version < MIN_MPV:
        checks.append(
            Check(
                "warn",
                f"mpv {version[0]}.{version[1]} is old",
                "Gapless playback needs 0.34 or newer; older mpv opens the next track late.",
                "Upgrade mpv.",
            )
        )
    else:
        checks.append(Check("ok", f"mpv {version[0]}.{version[1]}"))
    listing = p.run(["mpv", "--audio-device=help"])
    devices = parse_devices(listing) if listing else []
    if not devices:
        checks.append(
            Check(
                "warn",
                "mpv lists no audio devices",
                "Is a sound server running, and a DAC plugged in?",
            )
        )
    return checks, devices


def check_device(device: str | None, listed: list[DeviceInfo], system: str) -> list[Check]:
    kind = device_kind(device)
    exclusive = "alsa/hw:CARD=..." if system != "Darwin" else "coreaudio/..."
    if device is None:
        return [
            Check(
                "warn",
                "No output device chosen",
                "mpv uses the system default, which may mix and resample.",
                f"Run: qobit set-device and pick one marked ♪ ({exclusive}).",
            )
        ]
    out: list[Check] = []
    if listed and not any(d.id == device for d in listed):
        out.append(
            Check(
                "fail",
                f"The saved device is not there: {device}",
                "Unplugged, renamed, or the sound server restarted.",
                "Run: qobit devices  (to see what is there), then qobit set-device.",
            )
        )
        return out
    text = {
        "alsa-hw": ("ok", "Raw hardware output (alsa/hw:): bit-perfect, exclusive."),
        "coreaudio": ("ok", "CoreAudio, in exclusive mode (qobit turns it on): bit-perfect."),
        "alsa": (
            "warn",
            "An ALSA wrapper device (plug/dmix/front): it can resample and mix.",
        ),
        "pipewire": ("info", "PipeWire: bit-perfect only if it follows each track's rate."),
        "pulse": ("info", "PulseAudio: bit-perfect only if it follows each track's rate."),
        "other": ("info", "Device type unknown to qobit."),
    }[kind]
    fix = ""
    if text[0] == "warn":
        fix = "Pick the alsa/hw:CARD=... device (♪) with: qobit set-device"
    out.append(Check(text[0], f"Output: {device}", text[1], fix))  # type: ignore[arg-type]
    return out


def check_software_volume(enabled: bool) -> list[Check]:
    if not enabled:
        return [Check("ok", "Software volume is off", "No gain is applied to the samples.")]
    return [
        Check(
            "warn",
            "Software volume is on",
            "Below 100% it changes the samples (DSP): not bit-perfect.",
            "Turn it off: Ctrl+P → Software volume, and use the DAC's own knob.",
        )
    ]


# mpv.conf options that change the samples, and options qobit overrides anyway.
_BREAKS = {
    "audio-samplerate": "resamples everything to one rate",
    "audio-format": "forces one sample format",
    "audio-channels": "up- or down-mixes the channels",
    "audio-normalize-downmix": "scales the samples when mixing down",
    "replaygain": "applies gain to the samples",
    "replaygain-preamp": "applies gain to the samples",
    "replaygain-fallback": "applies gain to the samples",
}
_OVERRIDDEN = {
    "af": "qobit clears audio filters (--af-clr)",
    "af-add": "qobit clears audio filters (--af-clr)",
    "gapless-audio": "qobit sets it to weak so gapless never resamples",
    "audio-device": "the device saved with qobit set-device wins",
    "audio-pitch-correction": "qobit turns it off",
}


def _mpv_conf_paths(p: Probe) -> list[str]:
    paths = []
    for base in (
        p.env("MPV_HOME"),
        f"{p.env('XDG_CONFIG_HOME')}/mpv" if p.env("XDG_CONFIG_HOME") else None,
        f"{p.home}/.config/mpv",
        f"{p.home}/Library/Preferences/mpv" if p.system == "Darwin" else None,
        f"{p.home}/.mpv",
        "/etc/mpv" if p.system != "Darwin" else None,
    ):
        if base:
            for name in ("mpv.conf", "config"):
                if (path := f"{base}/{name}") not in paths:
                    paths.append(path)
    return paths


def check_mpv_conf(p: Probe) -> list[Check]:
    """qobit starts mpv with your config: an option there can break the signal."""
    found = [(path, text) for path in _mpv_conf_paths(p) if (text := p.read(path)) is not None]
    if not found:
        return [Check("ok", "No mpv.conf", "mpv adds nothing of its own.")]
    out: list[Check] = []
    clean = True
    for path, text in found:
        for n, key, value in parse_mpv_conf(text):
            where = f"{path.replace(str(p.home), '~')}:{n}  {key}={value}"
            if key == "volume" and value not in ("100", "100.0"):
                out.append(
                    Check(
                        "fail",
                        f"mpv.conf sets the volume ({where})",
                        "Below 100 it changes the samples.",
                        "Remove the line (use the DAC's own volume).",
                    )
                )
                clean = False
            elif key == "mute" and value == "yes":
                out.append(Check("fail", f"mpv.conf mutes mpv ({where})", fix="Remove the line."))
                clean = False
            elif key in _BREAKS and value not in ("no", "0", "auto"):
                out.append(
                    Check(
                        "fail",
                        f"mpv.conf {_BREAKS[key]} ({where})",
                        "qobit starts mpv with your config, so this applies.",
                        "Remove the line.",
                    )
                )
                clean = False
            elif key == "ao" and value:
                out.append(
                    Check(
                        "warn",
                        f"mpv.conf picks the audio output ({where})",
                        "It can differ from the device saved in qobit.",
                        "Remove it unless you need it.",
                    )
                )
                clean = False
            elif key in _OVERRIDDEN:
                out.append(Check("info", f"mpv.conf {key} is ignored ({where})", _OVERRIDDEN[key]))
    if clean and not any(c.status == "fail" for c in out):
        out.insert(0, Check("ok", "mpv.conf changes nothing that matters"))
    return out


# ── Linux: the DAC, ALSA and PipeWire ────────────────────────────────────────


@dataclass
class Dac:
    card: Card
    caps: Capabilities | None


def _cards(p: Probe) -> list[Card]:
    return parse_asound_cards(p.read("/proc/asound/cards") or "")


def _capabilities(p: Probe, card: Card) -> Capabilities | None:
    for path in p.glob(f"/proc/asound/card{card.num}/stream*"):
        caps = parse_stream(p.read(path) or "")
        if caps:
            return caps
    return None


def find_dac(p: Probe, device: str | None) -> Dac | None:
    """The sound card the saved device plays on: named in an alsa/ id, else
    matched by name (PipeWire node names carry it), else the only USB card."""
    cards = _cards(p)
    if not cards:
        return None
    card = None
    if device and (name := alsa_card_name(device)):
        card = next((c for c in cards if c.id == name), None)
    if card is None and device:
        plain = _plain(device)
        card = next(
            (c for c in cards if len(_plain(c.id)) > 3 and _plain(c.id) in plain),
            None,
        )
    if card is None:
        usb = [c for c in cards if _capabilities(p, c) is not None]
        card = usb[0] if len(usb) == 1 else None
    return Dac(card, _capabilities(p, card)) if card else None


def check_alsa(p: Probe, device: str | None, dac: Dac | None) -> list[Check]:
    if dac is None:
        return [
            Check(
                "info",
                "Could not tell which sound card the device is",
                "Rates and exclusive use are not checked.",
            )
        ]
    out: list[Check] = []
    caps = dac.caps
    if caps:
        bits = "/".join(str(b) for b in caps.bits) + "-bit" if caps.bits else ""
        out.append(
            Check("info", f"DAC: {dac.card.name}", f"{bits} {_rates_text(caps.rates)}".strip())
        )
    else:
        out.append(Check("info", f"Sound card: {dac.card.name}"))
    if device_kind(device) != "alsa-hw":
        return out
    node = f"/dev/snd/pcmC{dac.card.num}D0p"
    if p.read("/proc/asound/cards") is not None and not p.writable(node):
        out.append(
            Check(
                "fail",
                f"No access to {node}",
                "alsa/hw: opens the card directly.",
                "Add yourself to the audio group: sudo usermod -aG audio $USER, then log in again.",
            )
        )
    for status in p.glob(f"/proc/asound/card{dac.card.num}/pcm*p/sub*/status"):
        state, pid = parse_pcm_status(p.read(status) or "")
        if state == "RUNNING":
            who = (p.read(f"/proc/{pid}/comm") or "").strip() if pid else ""
            out.append(
                Check(
                    "warn",
                    f"The DAC is in use{f' by {who}' if who else ''}",
                    "A raw hw: device is exclusive: qobit cannot open it while another "
                    "program plays on it (or PipeWire holds it).",
                    "Stop that program, or use the PipeWire device with rate following "
                    "(docs/audio.md, option A).",
                )
            )
            break
    return out


def check_pipewire(p: Probe, device: str | None, dac: Dac | None) -> list[Check]:
    """Does the sound server follow each track's rate, or resample?"""
    kind = device_kind(device)
    if kind not in ("pipewire", "pulse", "auto", "other"):
        return []
    settings_text = p.run(["pw-metadata", "-n", "settings"])
    pactl = p.run(["pactl", "info"]) or ""
    if settings_text is None or "key:" not in settings_text:
        if "PipeWire" in pactl:
            return [
                Check(
                    "info",
                    "PipeWire is running, but pw-metadata is missing",
                    "Its sample rates were not checked.",
                    "Install pipewire-bin (or the package with pw-metadata) to check them.",
                )
            ]
        return []
    s = parse_pw_settings(settings_text)
    allowed = parse_rates(s.get("clock.allowed-rates", ""))
    force = int(s.get("clock.force-rate", "0") or 0)
    base = int(s.get("clock.rate", "0") or 0)
    out: list[Check] = []
    if force:
        return [
            Check(
                "fail",
                f"PipeWire is forced to {_khz(force)} kHz",
                "Every track is resampled to that rate.",
                "Remove clock.force-rate from your PipeWire config, then restart it: "
                "systemctl --user restart pipewire pipewire-pulse wireplumber",
            )
        ]
    wanted = [r for r in (dac.caps.rates if dac and dac.caps else RATE_LADDER) if r in RATE_LADDER]
    missing = [r for r in wanted if r not in allowed] if allowed else []
    if not allowed or (len(allowed) == 1 and allowed[0] == base):
        allowed_text = _rates_text(allowed) if allowed else f"only {_khz(base)} kHz"
        missing = missing or wanted
    else:
        allowed_text = _rates_text(allowed)
    if missing:
        what = "your DAC" if dac and dac.caps else "a typical DAC"
        out.append(
            Check(
                "warn" if kind in ("pipewire", "pulse") else "info",
                f"PipeWire allows {allowed_text}",
                f"Tracks at {_rates_text(missing)} are resampled before they reach {what}.",
                "Create ~/.config/pipewire/pipewire.conf.d/10-allowed-rates.conf with:\n\n"
                + "".join(f"    {line}\n" for line in dropin_text(wanted).splitlines())
                + "\nthen: systemctl --user restart pipewire pipewire-pulse wireplumber",
            )
        )
    else:
        out.append(Check("ok", f"PipeWire follows the rate ({_rates_text(allowed)})"))
    return out


# ── macOS ────────────────────────────────────────────────────────────────────


def check_macos(device: str | None, listed: list[DeviceInfo]) -> list[Check]:
    """Exclusive mode (no system mixer) only exists on coreaudio/ devices."""
    has_coreaudio = any(d.id.startswith("coreaudio/") for d in listed)
    if has_coreaudio and device_kind(device) != "coreaudio":
        return [
            Check(
                "warn",
                "The saved device is not a CoreAudio one",
                "Exclusive mode (no system mixer) only applies to coreaudio/ devices.",
                "Run: qobit set-device and pick a coreaudio/ one (♪).",
            )
        ]
    return []


# ── the terminal ─────────────────────────────────────────────────────────────


def check_terminal(p: Probe) -> list[Check]:
    out: list[Check] = []
    colour = (p.env("COLORTERM") or "").lower()
    if colour in ("truecolor", "24bit"):
        out.append(Check("ok", "True colour terminal"))
    else:
        out.append(
            Check(
                "warn",
                "The terminal does not say it has true colour",
                f"COLORTERM is {colour or 'not set'}: the theme's colours may be off.",
                "Use a true colour terminal (kitty, Ghostty, WezTerm, iTerm2, ...).",
            )
        )
    term = (p.env("TERM") or "").lower()
    program = (p.env("TERM_PROGRAM") or "").lower()
    kitty = "kitty" in term or p.env("KITTY_WINDOW_ID") or "ghostty" in (term + program)
    if p.env("TMUX"):
        passthrough = (p.run(["tmux", "show", "-gv", "allow-passthrough"]) or "").strip()
        if kitty and passthrough not in ("on", "all"):
            out.append(
                Check(
                    "info",
                    "tmux hides the Kitty graphics from qobit",
                    "Cover art falls back to coloured blocks.",
                    "Add to tmux.conf: set -g allow-passthrough on",
                )
            )
        else:
            out.append(Check("info", "Running in tmux"))
    elif kitty:
        out.append(Check("ok", "Kitty graphics: cover art is sharp"))
    else:
        out.append(Check("info", "Cover art: coloured blocks", "Sharp art needs kitty or Ghostty."))
    return out


# ── all of it ────────────────────────────────────────────────────────────────


def run_checks(
    p: Probe | None = None, device: str | None = None, software_volume: bool = False
) -> list[tuple[str, list[Check]]]:
    """The report's sections: (heading, checks)."""
    p = p or Probe()
    mpv_checks, listed = check_mpv(p)
    sections = [("mpv", mpv_checks)]
    if any(c.status == "fail" and "not installed" in c.title for c in mpv_checks):
        return sections
    sections.append(
        ("Output", check_device(device, listed, p.system) + check_software_volume(software_volume))
    )
    sections.append(("mpv config", check_mpv_conf(p)))
    if p.system == "Linux":
        dac = find_dac(p, device)
        sections.append(
            ("DAC and sound server", check_alsa(p, device, dac) + check_pipewire(p, device, dac))
        )
    elif p.system == "Darwin":
        sections.append(("DAC", check_macos(device, listed)))
    sections.append(("Terminal", check_terminal(p)))
    return [(h, c) for h, c in sections if c]


_MARK = {"ok": "✓", "info": "·", "warn": "!", "fail": "✗"}


def render(sections: list[tuple[str, list[Check]]]) -> str:
    lines: list[str] = []
    todo: list[Check] = []
    for heading, checks in sections:
        lines.append(f"\n{heading}")
        for c in checks:
            lines.append(f"  {_MARK[c.status]} {c.title}")
            if c.detail:
                lines.append(f"      {c.detail}")
            if c.fix and c.status in ("warn", "fail"):
                todo.append(c)
    if todo:
        lines.append("\nTo fix")
        for n, c in enumerate(todo, 1):
            lines.append(f"  {n}. {c.title}")
            lines.extend(f"     {line}" if line else "" for line in c.fix.splitlines())
    fails = sum(c.status == "fail" for _, cs in sections for c in cs)
    warns = sum(c.status == "warn" for _, cs in sections for c in cs)
    lines.append(
        "\n"
        + (
            "Nothing in the way of bit-perfect output."
            if not fails and not warns
            else f"{fails} problem{'s' * (fails != 1)}, {warns} warning{'s' * (warns != 1)}."
        )
    )
    return "\n".join(lines) + "\n"


def exit_code(sections: list[tuple[str, list[Check]]]) -> int:
    return 1 if any(c.status == "fail" for _, cs in sections for c in cs) else 0

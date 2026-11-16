import asyncio
import contextlib
import re
from dataclasses import dataclass
from pathlib import Path

from .player import MpvPlayer


@dataclass
class VerifyResult:
    expected_rate: float
    actual_rate: float | None
    expected_bit_depth: int
    actual_format: str | None  # mpv format string, e.g. "s16", "s32", "floatp"
    bit_perfect: bool
    rate_match: bool
    error: str | None = None

    def summary(self) -> str:
        if self.error:
            return f"? ({self.error})"
        if self.bit_perfect:
            khz = self.expected_rate / 1000
            khz_str = f"{int(khz)}" if khz == int(khz) else f"{khz:.1f}"
            return f"✓ {self.expected_bit_depth}/{khz_str} kHz bit-perfect"
        return f"✗ resampled — expected {self.expected_rate:.0f} Hz, got {self.actual_rate} Hz"


async def check_bit_perfect(
    player: MpvPlayer,
    expected_sample_rate: float,
    expected_bit_depth: int,
    wait_secs: float = 2.0,
) -> VerifyResult:
    """Query mpv's actual output rate and compare to the stream metadata."""
    await asyncio.sleep(wait_secs)

    actual_rate_raw = player.get_property("audio-params/samplerate")
    actual_format = player.get_property("audio-params/format")

    if actual_rate_raw is None:
        return VerifyResult(
            expected_rate=expected_sample_rate,
            actual_rate=None,
            expected_bit_depth=expected_bit_depth,
            actual_format=None,
            bit_perfect=False,
            rate_match=False,
            error="mpv not responding — device may have rejected the stream",
        )

    actual_rate = float(actual_rate_raw)
    rate_match = abs(actual_rate - expected_sample_rate) < 1.0

    # mpv may use s32 as the container format for 24-bit content; that's fine.
    # Flag a mismatch only if we see an obvious downsample (e.g. s16 for 24-bit source).
    actual_format_str = str(actual_format) if actual_format else ""
    bit_match = not (expected_bit_depth >= 24 and actual_format_str in ("s16", "u16"))

    return VerifyResult(
        expected_rate=expected_sample_rate,
        actual_rate=actual_rate,
        expected_bit_depth=expected_bit_depth,
        actual_format=actual_format_str,
        bit_perfect=rate_match and bit_match,
        rate_match=rate_match,
    )


# ── what the DAC actually runs at (Linux) ────────────────────────────────────
#
# mpv's audio-params say what mpv hands its audio output: they catch resampling
# or a bit-depth cut inside mpv, not a sound server resampling behind it (a
# PipeWire graph that didn't follow the track's rate). On Linux the ALSA
# hw_params of the open playback streams say what the hardware itself runs at.

_ASOUND = Path("/proc/asound")


def alsa_playback_params(root: Path = _ASOUND) -> list[tuple[int, str]]:
    """(rate in Hz, ALSA format such as ``S24_3LE``) of every open playback
    stream. Empty when there's no /proc/asound (macOS) or nothing is open."""
    out: list[tuple[int, str]] = []
    for params in sorted(root.glob("card*/pcm*p/sub*/hw_params")):
        try:
            text = params.read_text()
        except OSError:
            continue
        fields = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
        rate, fmt = fields.get("rate", "").strip(), fields.get("format", "").strip()
        if rate and fmt:
            with contextlib.suppress(ValueError):
                out.append((int(rate.split()[0]), fmt))
    return out


def alsa_format_bits(fmt: str) -> int | None:
    """Sample bits an ALSA format carries: S16_LE → 16, S24_3LE → 24, S32_LE
    and FLOAT_LE → 32 (a 24-bit file plays bit-exact in a 32-bit container)."""
    m = re.match(r"[SU](\d+)", fmt)
    if m:
        return int(m.group(1))
    return 32 if fmt.startswith("FLOAT") else None


@dataclass
class Signal:
    """The verdict the transport shows. ``state``: ``ok`` (bit-perfect as far
    as qobit can see), ``resampled``, ``reduced`` (fewer bits than the file),
    or ``unknown`` (mpv didn't answer). ``detail`` names what differs."""

    state: str
    detail: str = ""
    dac_checked: bool = False  # the DAC's own rate was read (Linux)


def _khz(hz: float) -> str:
    khz = hz / 1000
    return f"{int(khz)}" if khz == int(khz) else f"{khz:.1f}"


async def check_signal(
    player: MpvPlayer, sample_rate: float, bit_depth: int, wait_secs: float = 2.0
) -> Signal:
    """mpv's output, then (Linux) the DAC's: does each match the file?"""
    mpv = await check_bit_perfect(player, sample_rate, bit_depth, wait_secs)
    if mpv.error:
        return Signal("unknown")
    if not mpv.rate_match:
        return Signal("resampled", f"{_khz(sample_rate)} → {_khz(mpv.actual_rate or 0)} kHz")
    if not mpv.bit_perfect:
        return Signal("reduced", f"{bit_depth} → 16-BIT")
    hw = alsa_playback_params()
    rates = {rate for rate, _ in hw}
    if len(rates) != 1:  # nothing open (macOS), or several devices: can't tell
        return Signal("ok")
    rate = rates.pop()
    if abs(rate - sample_rate) >= 1.0:
        return Signal("resampled", f"{_khz(sample_rate)} → {_khz(rate)} kHz", dac_checked=True)
    bits = [b for _, fmt in hw if (b := alsa_format_bits(fmt)) is not None]
    if bits and min(bits) < bit_depth:
        return Signal("reduced", f"{bit_depth} → {min(bits)}-BIT", dac_checked=True)
    return Signal("ok", dac_checked=True)

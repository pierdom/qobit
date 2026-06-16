import asyncio
from dataclasses import dataclass

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
    if expected_bit_depth >= 24 and actual_format_str in ("s16", "u16"):
        bit_match = False
    else:
        bit_match = True

    return VerifyResult(
        expected_rate=expected_sample_rate,
        actual_rate=actual_rate,
        expected_bit_depth=expected_bit_depth,
        actual_format=actual_format_str,
        bit_perfect=rate_match and bit_match,
        rate_match=rate_match,
    )

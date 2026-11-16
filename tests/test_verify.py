"""The bit-perfect lamp's verdict (audio/verify.check_signal): mpv's output
first, then, on Linux, the rate and format the DAC actually runs at."""

from __future__ import annotations

import asyncio

import pytest

from qobit.audio import verify


class _FakeMpv:
    def __init__(self, rate: float | None, fmt: str | None):
        self._props = {"audio-params/samplerate": rate, "audio-params/format": fmt}

    def get_property(self, name: str):
        return self._props.get(name)


def _hw(root, card: int, rate: int, fmt: str) -> None:
    d = root / f"card{card}" / "pcm0p" / "sub0"
    d.mkdir(parents=True)
    (d / "hw_params").write_text(
        f"access: RW_INTERLEAVED\nformat: {fmt}\nsubformat: STD\nchannels: 2\n"
        f"rate: {rate} ({rate}/1)\nperiod_size: 1024\n"
    )


def test_alsa_params_skip_closed_streams(tmp_path):
    _hw(tmp_path, 0, 96000, "S24_3LE")
    closed = tmp_path / "card1" / "pcm0p" / "sub0"
    closed.mkdir(parents=True)
    (closed / "hw_params").write_text("closed\n")
    assert verify.alsa_playback_params(tmp_path) == [(96000, "S24_3LE")]


@pytest.mark.parametrize(
    ("fmt", "bits"),
    [("S16_LE", 16), ("S24_3LE", 24), ("S24_LE", 24), ("S32_LE", 32), ("FLOAT_LE", 32)],
)
def test_alsa_format_bits(fmt, bits):
    assert verify.alsa_format_bits(fmt) == bits


_REAL_PARAMS = verify.alsa_playback_params


def _dac(monkeypatch, root) -> None:
    """Read hw_params from the fake /proc/asound tree under ``root``."""
    monkeypatch.setattr(verify, "alsa_playback_params", lambda: _REAL_PARAMS(root))


def test_ok_when_mpv_and_dac_match(monkeypatch, tmp_path):
    _hw(tmp_path, 0, 96000, "S32_LE")
    _dac(monkeypatch, tmp_path)
    sig = asyncio.run(verify.check_signal(_FakeMpv(96000, "s32"), 96000.0, 24, wait_secs=0))
    assert (sig.state, sig.dac_checked) == ("ok", True)


def test_dac_resampled_behind_mpv(monkeypatch, tmp_path):
    # mpv hands over 96 kHz, but the sound server runs the DAC at 48 kHz.
    _hw(tmp_path, 0, 48000, "S32_LE")
    _dac(monkeypatch, tmp_path)
    sig = asyncio.run(verify.check_signal(_FakeMpv(96000, "s32"), 96000.0, 24, wait_secs=0))
    assert sig.state == "resampled"
    assert sig.detail == "96 → 48 kHz"


def test_dac_with_fewer_bits(monkeypatch, tmp_path):
    _hw(tmp_path, 0, 96000, "S16_LE")
    _dac(monkeypatch, tmp_path)
    sig = asyncio.run(verify.check_signal(_FakeMpv(96000, "s32"), 96000.0, 24, wait_secs=0))
    assert (sig.state, sig.detail) == ("reduced", "24 → 16-BIT")


def test_mpv_resampling(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "alsa_playback_params", list)
    sig = asyncio.run(verify.check_signal(_FakeMpv(48000, "s32"), 44100.0, 16, wait_secs=0))
    assert (sig.state, sig.detail) == ("resampled", "44.1 → 48 kHz")


def test_no_dac_info_trusts_mpv(monkeypatch):
    monkeypatch.setattr(verify, "alsa_playback_params", list)
    sig = asyncio.run(verify.check_signal(_FakeMpv(44100, "s16"), 44100.0, 16, wait_secs=0))
    assert (sig.state, sig.dac_checked) == ("ok", False)


def test_mpv_silent_is_unknown(monkeypatch):
    monkeypatch.setattr(verify, "alsa_playback_params", list)
    sig = asyncio.run(verify.check_signal(_FakeMpv(None, None), 44100.0, 16, wait_secs=0))
    assert sig.state == "unknown"

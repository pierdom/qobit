# qobit

Bit-perfect Qobuz client with a terminal UI (TUI). Linux and macOS.

Hi-res FLAC with no resampling and no DSP in the signal path — output device
sample rate and bit depth matched to each track — is the core, non-negotiable
requirement.

## Requirements

- Python 3.11+
- [mpv](https://mpv.io/) in `$PATH`
- A valid Qobuz subscription (Hi-Res tier for 24-bit content)
- A terminal that supports the [Kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/):
  kitty, Ghostty, or iTerm2 3.5+

## Installation

```bash
uv tool install .
```

Or for development:

```bash
uv sync
uv run qobit
```

## Configuration

Credentials are read from environment variables first, then from
`~/.config/qobit/config.json`:

```bash
export QOBUZ_EMAIL="you@example.com"
export QOBUZ_PASSWORD="yourpassword"
```

Or run `qobit auth` to be prompted and save to the config file.

## Audio device setup

List available devices and pick your DAC:

```bash
qobit devices       # list mpv audio device IDs
qobit set-device    # interactive picker — saves to config
```

The selected device ID is stored in `~/.config/qobit/config.json` under
`audio_device`. On Linux, prefer `alsa/hw:CARD=<name>,DEV=0` for direct
ALSA access (bypasses PulseAudio/PipeWire). On macOS, use
`coreaudio/<device>` — qobit enables exclusive/hog mode automatically.

## Usage

```bash
qobit play "Daft Punk Random Access Memories"   # search and play first result
qobit play "Kind of Blue" --quality FLAC_24_96  # prefer specific quality
qobit auth                                       # test authentication
qobit devices                                    # list output devices
qobit set-device                                 # pick and save output device
```

The TUI (`qobit` with no arguments) is coming in Phase 2.

## Quality tiers

| Flag          | Format            |
|---------------|-------------------|
| `FLAC_24_192` | FLAC 24-bit/192kHz (default, falls back if unavailable) |
| `FLAC_24_96`  | FLAC 24-bit/96kHz |
| `FLAC_CD`     | FLAC 16-bit/44.1kHz |
| `MP3_320`     | MP3 320kbps |

## Bit-perfect verification

After playback starts, qobit queries mpv's actual output sample rate via IPC
and compares it to the stream metadata. A `✓ 24/192 bit-perfect` badge means
the device is receiving exactly the bits from the file with no resampling.

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).

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
uv sync --group dev
uv run qobit
```

## First run

Authenticate once — this opens a browser to Qobuz and saves the session:

```bash
qobit auth
```

Pick your output device (required for bit-perfect output):

```bash
qobit devices       # list mpv audio device IDs
qobit set-device    # interactive picker — saves to config
```

Then launch the TUI:

```bash
qobit
```

## TUI navigation

| Key | Action |
|-----|--------|
| `1`–`5` | Switch tab (Playlists / Tracks / Artists / Albums / Search) |
| `Escape` | Back / focus tab bar |
| `Enter` | Play track / open detail screen |
| `Space` | Pause / resume |
| `[` / `]` | Seek −10 s / +10 s |
| `q` | Quit |

Within the Artist detail screen the Albums & EPs grid is navigated with arrow
keys.

## CLI commands

```bash
qobit play "Daft Punk Random Access Memories"   # search and play first result
qobit play "Kind of Blue" --quality FLAC_24_96  # prefer specific quality
qobit auth                                       # authenticate / refresh session
qobit devices                                    # list output devices
qobit set-device                                 # pick and save output device
```

## Audio device setup

On Linux, prefer `alsa/hw:CARD=<name>,DEV=0` for direct ALSA access (bypasses
PulseAudio/PipeWire). On macOS, use `coreaudio/<device-uid>` — qobit enables
Core Audio exclusive/hog mode automatically.

## Quality tiers

| Flag | Format |
|------|--------|
| `FLAC_24_192` | FLAC 24-bit / 192 kHz (default, falls back if unavailable) |
| `FLAC_24_96` | FLAC 24-bit / 96 kHz |
| `FLAC_CD` | FLAC 16-bit / 44.1 kHz |
| `MP3_320` | MP3 320 kbps |

Fallback order: FLAC_24_192 → FLAC_24_96 → FLAC_CD.

## Bit-perfect verification

After playback starts, qobit queries mpv's actual output sample rate via IPC
and compares it to the stream metadata. A `✓ 24/192 bit-perfect` badge in the
CLI means the device is receiving exactly the bits from the file with no
resampling.

## Config paths

```
~/.config/qobit/config.json       credentials, audio_device, theme
~/.local/share/qobit/mpv.sock     IPC socket (created per playback session)
```

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).

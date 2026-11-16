<div align="center">

<img src="https://raw.githubusercontent.com/pierdom/qobit/main/docs/assets/qobit-mark.svg" alt="qobit" width="96" height="96">

# qobit

[![CI](https://github.com/pierdom/qobit/actions/workflows/ci.yml/badge.svg)](https://github.com/pierdom/qobit/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/pierdom/qobit?sort=semver&color=E6B450)](https://github.com/pierdom/qobit/releases)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](https://github.com/pierdom/qobit/blob/main/LICENSE)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fpierdom%2Fqobit%2Fmain%2Fpyproject.toml)](https://github.com/pierdom/qobit/blob/main/pyproject.toml)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Textual](https://img.shields.io/badge/TUI-Textual-5A4FCF)](https://textual.textualize.io)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS-555)](#install)

A bit-perfect Qobuz client with a terminal UI, for Linux and macOS.

</div>

> [!NOTE]
> qobit is an unofficial project. It is not affiliated with, endorsed by or
> sponsored by Qobuz. See [Disclaimer](#disclaimer).

qobit plays hi-res FLAC from Qobuz through mpv with no resampling and no DSP.
The output device runs at each track's own sample rate and bit depth, and the
DAC receives exactly the bits from the file. You browse your library and
control playback from a keyboard-driven terminal UI with cover art.

<div align="center">

<img src="https://raw.githubusercontent.com/pierdom/qobit/main/screenshots/queue_page.png" width="49%" alt="Playback tab with the queue and the now-playing panel"> <img src="https://raw.githubusercontent.com/pierdom/qobit/main/screenshots/artist_view.png" width="49%" alt="Artist page with bio, top tracks and albums">

</div>

## Features

- Bit-perfect output through PipeWire rate-following or exclusive ALSA `hw:` /
  CoreAudio devices, with a live lamp that checks it on every track (on Linux,
  down to the rate the DAC actually runs at). Tracks your account can only
  preview (30-second MP3s) are skipped.
- Favourite tracks, artists and albums, and your playlists, with sorting and a
  live filter. Add favourites and follow artists from any list with `i`.
- A queue you can move back and forth in without losing tracks. It's restored
  when you restart qobit.
- Song radio and personal DailyQ / WeeklyQ mixes from Qobuz recommendations.
- Artist pages with top tracks, your favourites by that artist, albums and
  similar artists.
- Cover art and artist bios, drawn with the Kitty graphics protocol (see
  [Terminal](#terminal)).
- Themes, including Ayu Dark, Mirage and Light.
- OS media keys and now-playing widgets (MPRIS on Linux, Now Playing on macOS).
- Qobuz Connect (off by default): qobit shows up as a device in the Qobuz apps,
  and you can control it from there.

## Install

Requirements: a Qobuz subscription (Hi-Res for 24-bit), Python 3.11+,
[uv](https://docs.astral.sh/uv/), [mpv](https://mpv.io/), and a terminal that
supports the Kitty graphics protocol (see [Terminal](#terminal)).

Install mpv and uv first:

```bash
# macOS (Homebrew)
brew install mpv uv
# Debian / Ubuntu
sudo apt install mpv
# Fedora
sudo dnf install mpv
# Arch
sudo pacman -S mpv
```

(On Linux, get uv from your package manager or the
[installer](https://docs.astral.sh/uv/getting-started/installation/).)

Then:

```bash
uv tool install qobit   # or: pipx install qobit
qobit auth              # log in once; opens the browser, prints the link
qobit set-device        # choose the audio output
qobit                   # start the TUI
```

Upgrade with `uv tool upgrade qobit`. To run the latest code from `main`
instead: `uv tool install git+https://github.com/pierdom/qobit`.

For bit-perfect output, choose the device as described in
[docs/audio.md](https://github.com/pierdom/qobit/blob/main/docs/audio.md).

## Terminal

qobit draws all images (cover art, artist photos) with the
[Kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/).
There is no fallback for other terminals.

| Terminal | Images |
|---|---|
| [kitty](https://sw.kovidgoyal.net/kitty/) | works |
| [Ghostty](https://ghostty.org) | works |
| [WezTerm](https://wezterm.org), Konsole | implement the protocol; not tested with qobit |
| Inside tmux | needs `set -g allow-passthrough on`; not tested with qobit |

> [!WARNING]
> In other terminals (GNOME Terminal, iTerm2, macOS Terminal, Alacritty,
> Windows Terminal, VS Code, …) every image shows up as blocks of garbled
> characters, so qobit looks very bad. Playback still works, but use one of the
> terminals above.

## Usage

| Key | Action | Key | Action |
|---|---|---|---|
| `1`–`7` | switch tab | `Space` | pause / resume |
| `/` | filter | `[` `]` | seek 10 s |
| `s` `r` | sort, reverse | `R` | song radio |
| `i` | menu: queue, favourite, follow | `g` | go to artist / album |
| `f` | favourite | `Ctrl+P` | command palette |

All keys, and how each tab works, are in [docs/usage.md](https://github.com/pierdom/qobit/blob/main/docs/usage.md).
From the command line:

```bash
qobit play "Kind of Blue"   # search, play the first result, check the output format
qobit devices               # list audio outputs
qobit --connect-on          # start with Qobuz Connect enabled for this run
```

## Documentation

| | |
|---|---|
| Keys, tabs, radio, mixes, Qobuz Connect, files | [docs/usage.md](https://github.com/pierdom/qobit/blob/main/docs/usage.md) |
| Audio setup and bit-perfect output | [docs/audio.md](https://github.com/pierdom/qobit/blob/main/docs/audio.md) |
| PipeWire configuration | [contrib/pipewire](https://github.com/pierdom/qobit/blob/main/contrib/pipewire/README.md) |
| Qobuz Connect protocol notes | [docs/qobuz-connect.md](https://github.com/pierdom/qobit/blob/main/docs/qobuz-connect.md) |
| Qobuz Connect implementation | [docs/qobuz-connect-design.md](https://github.com/pierdom/qobit/blob/main/docs/qobuz-connect-design.md) |
| Changes | [CHANGELOG.md](https://github.com/pierdom/qobit/blob/main/CHANGELOG.md) |
| Contributing | [CONTRIBUTING.md](https://github.com/pierdom/qobit/blob/main/CONTRIBUTING.md) |
| Reporting a security issue | [SECURITY.md](https://github.com/pierdom/qobit/blob/main/SECURITY.md) |
| Notes for working on the code | [CLAUDE.md](https://github.com/pierdom/qobit/blob/main/CLAUDE.md) |

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Why this exists

This is a personal project. I wanted a Qobuz client that looks good on a
cyberdeck build, with large cover art and a terminal UI.

qobit builds on other people's work:

- [qbz](https://github.com/vicrodh/qbz) by vicrodh, a full-featured Qobuz
  desktop player and the main source and inspiration for qobit, including its
  bit-perfect approach. qbz has been discontinued and its code is no longer
  available.
- [qobine](https://github.com/SofusA/qobine) by SofusA, a lighter terminal
  client for Qobuz.
- [qobuz-connect](https://github.com/ciaens/qobuz-connect) and
  [qobuz-proxy](https://github.com/leolobato/qobuz-proxy), whose protocol work
  is the basis of qobit's Qobuz Connect support.

## Disclaimer

qobit is an independent, unofficial project. It is not affiliated with Qobuz in
any way, and Qobuz does not endorse or sponsor it. The Qobuz name is only used
to say which service qobit works with.

qobit is not a replacement for the official Qobuz apps. It is a small terminal
player for a narrow audience: people who want bit-perfect playback from a
keyboard-driven terminal UI. It does far less than the official apps, so use
them for everything else.

It talks to the same API as the Qobuz web player. You need your own Qobuz
subscription, qobit only plays what your account can stream, and you are
responsible for using it within Qobuz's terms of service.

qobit is free software, provided as is, without any warranty. See sections 15
and 16 of the [GPL-3.0 license](https://github.com/pierdom/qobit/blob/main/LICENSE). It is an early-stage personal
project: use it at your own risk.

## License

GPL-3.0. See [LICENSE](https://github.com/pierdom/qobit/blob/main/LICENSE).

<div align="center">

<img src="docs/assets/qobit-mark.svg" alt="qobit" width="96" height="96">

# qobit

[![CI](https://github.com/pierdom/qobit/actions/workflows/ci.yml/badge.svg)](https://github.com/pierdom/qobit/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/pierdom/qobit?sort=semver&color=F9BD2F)](https://github.com/pierdom/qobit/releases)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fpierdom%2Fqobit%2Fmain%2Fpyproject.toml)](pyproject.toml)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Textual](https://img.shields.io/badge/TUI-Textual-5A4FCF)](https://textual.textualize.io)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS-555)](#quick-start)
[![Qobuz Connect](https://img.shields.io/badge/Qobuz%20Connect-renderer-F9BD2F)](#qobuz-connect)

**Bit-perfect Qobuz in the terminal: hi-res FLAC straight to your DAC, a keyboard-driven library with big cover art, and Qobuz Connect so the Qobuz app — or an AI agent — can drive it.**

</div>

qobit plays your Qobuz library through mpv with no resampling and no DSP: the
DAC receives exactly the bits from the file, at each track's own sample rate.
Around that sits a Textual TUI built for a cyberdeck — Kitty-protocol cover
art, a rich now-playing panel, song radio and personal mixes — and a Qobuz
Connect renderer that puts it in the official apps' device picker.

<div align="center">

<img src="screenshots/queue_page.png" width="49%" alt="Playback tab: queue timeline and Now Playing panel"> <img src="screenshots/artist_view.png" width="49%" alt="Artist page: bio, top tracks, discography">

<sub>Playback tab &nbsp;·&nbsp; artist page &nbsp;·&nbsp; more in <a href="screenshots/">screenshots/</a></sub>

</div>

## Why qobit

**Bit-perfect by design.** PipeWire rate-following or exclusive
`hw:`/CoreAudio output, no software gain unless you ask for it, previews never
played in place of the real file, and `qobit play` checks mpv's actual output
format against the stream. → [Audio setup](docs/audio.md)

**Qobuz Connect.** qobit shows up next to your phone and speakers in the Qobuz
apps. Pick it there and the app is the remote; edit the queue from either side
and both stay in sync, through Qobuz's own servers.

**A library you drive from the keyboard.** Favourite tracks, artists, albums
and playlists with live filters and sorting, a timeline queue you can walk back
and forth without losing tracks, song radio, DailyQ/WeeklyQ mixes, and a queue
that survives restarts.

## How it works

```
  Qobuz app / AI agent ──Connect──►  Qobuz cloud  ──commands──►  qobit (TUI)
                                                                     │ getFileUrl
                                                                     ▼
                                  hi-res FLAC ─────────────────►  mpv ──► DAC
                                                       (no resampling, no DSP)
```

Connect carries only commands and queue state; qobit fetches and plays the
stream itself, so remote control never touches the signal path.

## Quick start

You need a Qobuz subscription (Hi-Res tier for 24-bit), Python 3.11+,
[uv](https://docs.astral.sh/uv/), [mpv](https://mpv.io/), and a terminal with
the [Kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/)
(kitty, Ghostty, iTerm2 3.5+). On Linux, media-key support (MPRIS) compiles
against system headers, so install those first:

```bash
sudo apt install mpv build-essential pkg-config python3-dev libcairo2-dev libgirepository-2.0-dev  # Debian/Ubuntu 24.04+
sudo dnf install mpv gcc pkgconf-pkg-config python3-devel cairo-devel cairo-gobject-devel gobject-introspection-devel  # Fedora 40+
sudo pacman -S --needed mpv base-devel pkgconf cairo gobject-introspection  # Arch
```

Then:

```bash
git clone https://github.com/pierdom/qobit && cd qobit
uv tool install .
qobit auth          # log in once (opens the browser)
qobit set-device    # pick the output — see docs/audio.md for the bit-perfect options
qobit               # launch the TUI
```

## Qobuz Connect

Turn it on from the command palette (`Ctrl+P` → *Qobuz Connect*) or for one run
with `qobit --connect-on`. qobit appears in the device picker of the Qobuz apps;
the transport bar shows `[ ⇄ CONNECT · ACTIVE ]` once it's the playing device.

- Play, pause, seek, skip and pick music from the app — or from qobit, including
  play next, add to queue, remove and song radio. Both show the same queue.
- The app's volume slider is ignored on purpose: software gain would alter the
  bits.
- With [qobuz-mcp](https://github.com/pierdom/qobuz-mcp), an AI agent can do the
  same — *"play Mezzanine on qobit and queue three similar tracks"*.

Unofficial: Qobuz licenses Connect to hardware makers only; qobit speaks the
community-documented protocol. More in [docs/usage.md](docs/usage.md#qobuz-connect).

## Keys

| | | | |
|---|---|---|---|
| `1`–`7` | tabs | `Space` | pause / resume |
| `/` | filter | `[` `]` | seek ∓10 s |
| `s` `r` | sort / reverse | `R` | song radio |
| `i` | track menu | `g` | go to artist / album |
| `f` | favourite | `Ctrl+P` | command palette |

Everything else, tab by tab: [docs/usage.md](docs/usage.md).

## CLI

```bash
qobit play "Kind of Blue" --quality FLAC_24_96   # search, play, verify bit-perfect
qobit devices | set-device | auth | clear-cache
qobit --connect-on                                # TUI with Qobuz Connect for this run
```

## Docs

| | |
|---|---|
| Every key, each tab, radio, mixes, Connect, files | [docs/usage.md](docs/usage.md) |
| Bit-perfect output: PipeWire, exclusive `hw:`/CoreAudio, quality, verification | [docs/audio.md](docs/audio.md) |
| PipeWire rate-following drop-in | [contrib/pipewire](contrib/pipewire/README.md) |
| Qobuz Connect protocol, reverse-engineered and verified | [docs/qobuz-connect.md](docs/qobuz-connect.md) |
| How qobit shares the queue with the Connect server | [docs/qobuz-connect-design.md](docs/qobuz-connect-design.md) |
| Release notes | [CHANGELOG.md](CHANGELOG.md) |
| Guide for AI coding agents working on this repo | [CLAUDE.md](CLAUDE.md) |

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

CI runs lint and tests on every push and pull request.

## Technologies

- **Python 3.11+**, **[Textual](https://textual.textualize.io)** and
  **textual-image** (Kitty graphics protocol) for the TUI
- **[mpv](https://mpv.io/)** over JSON IPC for playback
- **httpx** for the Qobuz API; **websockets** + **protobuf** for Qobuz Connect
- **MPRIS** (pydbus) on Linux, **MPNowPlayingInfoCenter** (PyObjC) on macOS
- **uv**, **ruff**, **pytest**

## Credits and disclaimer

[qbz](https://github.com/vicrodh/qbz) by vicrodh pioneered the bit-perfect
strategy qobit uses; [qobine](https://github.com/SofusA/qobine) is a leaner
terminal client. The Connect work builds on
[qobuz-connect](https://github.com/ciaens/qobuz-connect) and
[qobuz-proxy](https://github.com/leolobato/qobuz-proxy).

qobit is unofficial and not affiliated with Qobuz. It uses the API the Qobuz
web player uses, needs your own subscription, and plays only what your account
is entitled to. Early-stage: expect rough edges. Use at your own risk and within
Qobuz's terms of service.

GPL-3.0 — see [LICENSE](LICENSE).

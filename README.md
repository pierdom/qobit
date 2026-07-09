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

Pick your output device (see [Audio device setup](#audio-device-setup) for
bit-perfect vs. convenience trade-offs):

```bash
qobit devices       # list mpv audio device IDs
qobit set-device    # interactive picker — saves to config
```

Then launch the TUI:

```bash
qobit
```

## Keyboard shortcuts

### Navigation

| Key | Action |
|-----|--------|
| `1` | Tracks tab |
| `2` | Artists tab |
| `3` | Albums tab |
| `4` | Playlists tab |
| `5` | Search tab |
| `6` | Queue tab |
| `Escape` | Go back / clear filter / focus tab bar |
| `↑` `↓` `←` `→` | Move cursor in grids and lists |
| `PageUp` `PageDown` | Jump the selection one page in long lists and grids (Tracks, Queue, Artists, Albums) |
| `Home` `End` | Jump the selection to the first / last item |
| `f` | Favourite / unfavourite the highlighted track (in any track list) |
| `i` | Open the context menu for the highlighted track (Play next / Add to queue / Play radio / Go to artist / Go to album; plus Remove from queue on Up Next rows) |
| `Enter` | Open selected item or play selected track |

### Playback

| Key | Action |
|-----|--------|
| `Space` | Pause / resume |
| `[` | Seek back 10 s |
| `]` | Seek forward 10 s |
| `R` | Start a song radio from the current track |
| `q` | Quit |

Click anywhere on the progress bar in the transport strip to seek to that position.
The `Now Playing` status sits on the top-left of the transport border; the
current track's audio resolution (e.g. `24-bit · 192 kHz`) is shown on the
bottom-right.

On launch, qobit restores the last track you were playing into the transport
bar at the position you left off, paused. Press `Space` (or click the transport
bar) to resume from there.

Tracks already in your favourites show a `♥` in the Queue, album, and artist
track lists (the Tracks tab is omitted — everything there is a favourite).
Press `f` on the highlighted track to favourite or unfavourite it: the `♥`
appears/disappears. Favouriting a track anywhere adds it to the Tracks tab;
unfavouriting removes it there.

### Library pages (Tracks, Artists, Albums, Playlists)

| Key | Action |
|-----|--------|
| `s` | Cycle sort key (Date Added → Artist/Name → Title/Album → Year → …) |
| `r` | Reverse sort direction |
| `/` | Open live filter — type to narrow results |
| `Escape` | Close filter / clear query / go back |

The current filter query is shown in the border subtitle: `/ query_` while
typing, `⌕ query` when the filter is closed but results are still narrowed.
Press `Escape` again from the filtered view to clear the query and restore all
items.

### Queue

The Queue tab is split in two. On the **left** is a timeline: **Recently
Played** (tracks you've already heard this session, oldest first, `↺`) flows
into the **now-playing track** (accent-highlighted, with a ▶/⏸ icon) and on into
**Up Next**. The cursor opens on the now-playing track, so `PageUp` walks back
into history and `PageDown` down the queue. Selecting a Recently Played track
replays it as a one-off without disturbing Up Next; selecting an Up Next track
plays it and re-queues the rest.

On the **right** is the **Now Playing** panel — the focal point of the page:
large album art, title and artist, `album · year`, `genre · label`, and the
artist's biography filling the rest of the space (scroll it with the mouse
wheel). The stream resolution sits on the panel's bottom-right border. Genre,
label, year and bio load from Qobuz the moment a track starts. Playback
position and seeking live in the always-on transport bar at the bottom of every
screen, which mirrors the same track as a mini-player.

On **narrow terminals** (under ~100 columns) the Now Playing panel is dropped
and the timeline takes the full width.

| Key | Action |
|-----|--------|
| `c` | Clear the Up Next queue |
| `X` | Clear Recently Played |
| `f` | Favourite / unfavourite the highlighted track (or the now-playing track when the Now Playing panel is focused) |

The OS **previous** media key is context-aware: within the first 3 seconds of a
track it restarts it, otherwise it steps back to the previous track in history.

### Radio

Press `R` while a track is playing to start a **song radio** — Qobuz's
similar-track recommendations, seeded from the current track plus what you've
recently heard. It fills Up Next with ~50 suggestions (replacing the current
queue). Enable **Endless radio** from the command palette (`Ctrl+P` →
*Endless radio*) to keep playback going forever: when the queue empties, qobit
automatically refills it with fresh suggestions based on the last track. The
setting persists across launches.

### Search

| Key | Action |
|-----|--------|
| `/` | Focus the search input from anywhere on the Search tab |
| `Enter` | Submit search |
| `↑` `↓` | Move through results |
| `Enter` | Play track / open album / open artist |

## CLI commands

```bash
qobit play "Daft Punk Random Access Memories"   # search and play first result
qobit play "Kind of Blue" --quality FLAC_24_96  # prefer specific quality
qobit auth                                       # authenticate / refresh session
qobit devices                                    # list output devices
qobit set-device                                 # pick and save output device
qobit clear-cache                                # delete cached cover art from disk
```

## Audio device setup

qobit's goal is that the DAC receives exactly the bits from the FLAC — no
resampling, no DSP. *How* you reach that depends on whether you want the DAC to
stay a normal, shareable system device with a volume slider, or to be handed to
qobit exclusively. Pick a device with `qobit set-device` (switch any time).

| Option | DAC stays in system device list | Volume | Sample rate | Bit-perfect |
|---|---|---|---|---|
| **A. PipeWire rate-following** *(recommended)* | ✅ always | ✅ system volume | follows each track | ✅ transparent for ≤24-bit at 100% volume |
| **B. Exclusive `alsa/hw:` / `coreaudio:`** | ❌ vanishes while qobit plays | knob only | follows each track | ✅ guaranteed, always |
| **C. Plain PipeWire/Pulse** | ✅ always | ✅ system volume | resampled to a fixed clock (often 48 kHz) | ❌ |

### Option A — PipeWire rate-following (recommended on Linux)

Point qobit at the ordinary `pipewire/...` device for your DAC, then let
PipeWire switch its graph clock to match each track instead of resampling to one
fixed rate. By default most distros lock PipeWire to a single rate (e.g.
`clock.allowed-rates = [ 48000 ]`); unlock the full ladder your DAC supports:

`~/.config/pipewire/pipewire.conf.d/10-allowed-rates.conf`
```
context.properties = {
    # list the rates your DAC actually supports (see: cat /proc/asound/card*/stream0)
    default.clock.allowed-rates = [ 44100 48000 88200 96000 176400 192000 352800 384000 ]
}
```
then reload PipeWire:
```bash
systemctl --user restart pipewire pipewire-pulse wireplumber
```

Now when qobit is the only thing playing, PipeWire reclocks the device to the
file's rate (verify with `cat /proc/asound/card*/pcm0p/sub0/hw_params` during
playback — `rate:` should match the track). The DAC stays visible to the whole
system, your normal volume keys work, and for ≤24-bit content at 100% volume the
signal is bit-transparent (PipeWire's internal float32 represents 24-bit
exactly). Caveats: it stops being bit-exact if another app mixes in audio, if
you drop volume below 100%, or for the rare 32-bit-int source — for streaming
FLAC, none of those apply.

### Option B — exclusive hardware (guaranteed bit-perfect, DAC goes solo)

Pick a device flagged `♪` in `qobit devices` / `qobit set-device` (sorted to the
top):

- **Linux:** a raw `alsa/hw:CARD=<name>,DEV=0` entry. It talks straight to the
  hardware, bypassing PipeWire/PulseAudio, so mpv negotiates the file's native
  rate with the DAC. (mpv only ever lists the
  `sysdefault`/`front`/`surround`/`iec958` wrappers, so qobit synthesizes the raw
  `hw:` entry these don't expose.)
- **macOS:** `coreaudio/<device-uid>` — qobit enables Core Audio exclusive/hog
  mode automatically.

This is the strongest guarantee — nothing can touch the stream — but it's
**exclusive**: while qobit plays, the DAC disappears from the system device list
and no other app can use it. **Volume comes from the DAC, not qobit:** use the
unit's volume knob, or your system mixer if the DAC exposes a hardware volume
control (many R2R/desktop DACs are knob-only and expose none). qobit has no
in-app volume keys.

### Option C — plain PipeWire/Pulse (fallback)

A `pipewire/...` or `pulse/...` device with no rate config change: volume and
system sharing work, but everything is resampled to PipeWire's fixed clock and
converted through float32 — not bit-perfect. This is just Option A without the
`allowed-rates` drop-in; configure Option A instead.

> **Which should I pick?** Dedicated DAC and you want zero compromise with the
> unit's knob for volume → **B**. Everyday desktop where the DAC is also your
> system output and you want a volume slider → **A** (audibly identical to B for
> streaming FLAC). **C** only if you can't edit PipeWire config.

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
~/.config/qobit/config.json            credentials, audio_device, theme
~/.local/share/qobit/mpv.sock          IPC socket (created per playback session)
~/.local/share/qobit/player_state.json last track + position (resumed on launch)
~/.cache/qobit/images/                 cached cover art (safe to delete; honours XDG_CACHE_HOME)
```

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).

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

The Queue tab is a top-to-bottom timeline: **Recently Played** (tracks you've
already heard this session, oldest first, `↺`) flows into **Now Playing**, then
**Up Next**. The cursor opens on the current track, so `PageUp` walks back into
history and `PageDown` into the queue. Selecting a Recently Played track replays
it as a one-off without disturbing Up Next; selecting an Up Next track plays it
and re-queues the rest.

The **Now Playing** card shows the full picture for the current track: title,
album, release year, stream resolution and duration, plus genre and label
(loaded from Qobuz the moment a track starts).

| Key | Action |
|-----|--------|
| `c` | Clear the Up Next queue |
| `X` | Clear Recently Played |
| `f` | Favourite / unfavourite the highlighted track |

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
~/.config/qobit/config.json            credentials, audio_device, theme
~/.local/share/qobit/mpv.sock          IPC socket (created per playback session)
~/.local/share/qobit/player_state.json last track + position (resumed on launch)
~/.cache/qobit/images/                 cached cover art (safe to delete; honours XDG_CACHE_HOME)
```

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).

# qobit — developer guide

## Non-negotiable invariant

The DAC receives exactly the bits from the FLAC file — no resampling, no DSP.
Every design decision must preserve this. mpv is the playback engine; audio
routing choices ensure the system mixer never touches the signal.

## Project shape

Python 3.11+, Textual for the UI, mpv for audio, packaged with uv.

## Module layout

```
src/qobit/
├── __init__.py              version string
├── __main__.py              CLI entry point + TUI launcher (qobit with no args)
├── config.py                credentials + device config (env → file → prompt)
├── store.py                 JSON persistence for runtime state
├── qobuz/
│   ├── client.py            QobuzClient (reverse-engineers app_id/secret from
│   │                        web bundle; email/password + OAuth flows)
│   └── models.py            typed dataclasses: Track, Album, Artist, Playlist,
│                            StreamUrl
├── audio/
│   ├── device.py            enumerate mpv audio devices
│   ├── player.py            mpv subprocess wrapper with bit-perfect flags
│   └── verify.py            post-play rate/format verification via mpv IPC
└── ui/
    ├── app.py               QobitApp — reactive state, transport controls,
    │                        session restore, tab routing
    ├── screens/
    │   ├── search.py        SearchView + shared item widgets (TrackItem,
    │   │                    AlbumItem, ArtistItem, PlaylistItem, SectionHeader)
    │   ├── album_detail.py  AlbumScreen — track list for a single album
    │   ├── artist_detail.py ArtistScreen — bio + top tracks + album grid +
    │   │                    inline album detail panel
    │   ├── playlist_detail.py PlaylistScreen — track list for a playlist
    │   ├── albums.py        AlbumsView — favourite albums library tab
    │   ├── artists.py       ArtistsView — favourite artists library tab
    │   ├── tracks.py        TracksView — favourite tracks library tab
    │   └── playlists.py     PlaylistsView — user playlists library tab
    └── widgets/
        └── transport.py     TransportBar — progress bar + click-to-seek,
                             self-wires to QobitApp reactives on mount
```

## Current state

### Done

- **Phase 1 (CLI)**: `qobit auth`, `qobit devices`, `qobit set-device`,
  `qobit play <query>` with quality fallback and bit-perfect verification.
- **Auth**: Browser OAuth flow (primary) + email/password fallback. Session
  saved to `~/.config/qobit/config.json` and restored on next launch.
- **TUI shell**: 5-tab layout (Playlists / Tracks / Artists / Albums / Search),
  transport bar with click-to-seek, pause/seek bindings, escape-to-nav.
- **Library tabs**: Playlists, Tracks, Artists, Albums each load the user's
  Qobuz favourites and navigate to detail screens.
- **SearchView**: Free-text search across tracks/albums/artists in parallel;
  results shown in a sectioned list; selecting plays a track, opens an album,
  or opens an artist.
- **AlbumScreen**: Full track list with numbering and durations; selecting a
  track plays it and pops the screen.
- **PlaylistScreen**: Full track list; selecting plays and pops.
- **ArtistScreen**: Biography, popularity-ranked top tracks (from `artist/page`
  endpoint), Albums & EPs grid with album art (Kitty protocol), keyboard arrow
  navigation, reverse-chronological sort, dimmed-accent borders for unfocused
  panels. Clicking or pressing Enter on an AlbumCard opens an inline album
  detail panel (ContentSwitcher, no screen push) showing art, metadata, and
  full track list. Escape navigates back to the artist view before popping the
  screen.
- **TransportBar**: Shows now-playing label, progress bar, time counter; reacts
  to playback state reactives via self-wiring `watch()` calls on mount so any
  instance placed anywhere is always live; click-to-seek.
- **Transparent background**: Toggle via command palette (`Draw theme
  background`); preserved across sessions.
- **Bit-perfect flags**: `--af-clr`, `--audio-pitch-correction=no`,
  `--audio-exclusive=yes` on macOS with CoreAudio device.

### Work in progress

**ArtistScreen** (`ui/screens/artist_detail.py`)
- Missing: playing a top track should enqueue the remaining top tracks
  (Play Next — see below).
- Missing: context menu on AlbumCard / track rows (add to queue, play album).
- UI still rough: biography text needs better overflow handling.

### Not yet started / needs design

**Play Next queue** — see dedicated section below; this is the next major
feature.

**Search UI overhaul** — the current flat sectioned list is functional but
minimal. Needs a proper two-column or card-based layout, richer result rows
(album art thumbnails, genre tags), and a way to distinguish quality tiers.
Possible direction: tracks on the left, albums + artists on the right.

**AlbumScreen redesign** — currently a bare header + flat track list. Needs:
album art (Kitty), richer header (artist name clickable → ArtistScreen, year,
genre, label, format badge), "Play all" / "Play from here" queue actions,
per-track format badge (Hi-Res / CD).

**PlaylistScreen redesign** — same issues as AlbumScreen: no art, minimal
header, no queue actions.

**TracksView, AlbumsView, ArtistsView, PlaylistsView** — all are minimal
placeholder implementations (fetch favourites, render a flat list, navigate to
detail). Need the same treatment as the detail screens: richer rows, art where
applicable, sort and filter options.

**TrackScreen** (individual track detail) — does not exist. Would show full
metadata: composer, performer, label, format, related albums. Likely a modal
overlay rather than a full pushed screen.

**Context menus** — no right-click or action menu anywhere. Every selection
immediately plays or navigates. Need a way to "Add to Play Next", "Open
album", "Open artist" from any track row without immediately playing.

## Play Next queue (design)

This is the next major feature. The app maintains a queue of tracks that play
automatically after the current one finishes.

### State (in `QobitApp`)

```python
_play_queue: list[Track] = []   # tracks waiting to play after current
```

`QobitApp.play_track()` currently plays immediately and discards any queue.
New API:

```python
def play_track(self, track: Track, queue: list[Track] = []) -> None:
    ...  # plays track now, replaces _play_queue with queue

def enqueue_next(self, tracks: list[Track]) -> None:
    ...  # prepend to _play_queue (play after current track)

def enqueue_last(self, tracks: list[Track]) -> None:
    ...  # append to _play_queue (play later)
```

### End-of-track detection

`MpvPlayer._poll_player` (thread in `QobitApp`) already detects `running`
False. The gap: it can't tell whether mpv exited naturally (track ended) or
was killed by `stop()`.

Fix: add `_stopping: bool = False` to `MpvPlayer`. Set it in `stop()`, clear
it after the process terminates. When `_poll_player` sees `running` go False
without `_stopping` set → natural end → call `self.call_from_thread(
self._advance_queue)`.

`_advance_queue` pops the first item from `_play_queue` and calls
`play_track(track)`.

### Queue population rules

| User action | Plays now | Queue after |
|---|---|---|
| Select track from Search results | that track | tracks below it in the visible list |
| Select track from AlbumScreen | that track | remaining album tracks in order |
| Select track from PlaylistScreen | that track | remaining playlist tracks |
| Select track from ArtistScreen top-tracks | that track | remaining top tracks below it |
| "Play all" on AlbumScreen | first album track | all remaining album tracks |

Selecting anything from a list means: play this position, queue the rest — the
same mental model as every music player.

### Transport bar extensions (follow-on)

Once the queue exists:
- Next track button / `n` binding → `action_next()` (skip to first in queue)
- Queue indicator badge (`+3`) when `_play_queue` is non-empty
- Optional queue screen (modal) showing upcoming tracks with reorder/remove

## Textual patterns used in this codebase

- `@work` / `@work(thread=True)` for all async I/O and blocking IPC.
- `reactive` + `watch_*` for transport state (now_playing, is_playing, etc.).
- `DEFAULT_CSS` on each widget/screen for colocation of styles.
- `ContentSwitcher` for tab panel switching without re-mounting. Also used
  within ArtistScreen to switch between the artist view and inline album detail
  panel without pushing a new screen.
- `Screen` push/pop for detail views (AlbumScreen, PlaylistScreen, ArtistScreen).
- Container-owns-cursor pattern (AlbumGrid): the container holds focus, tracks
  `_cursor: int`, applies `-selected` CSS class to the active child. Same
  pattern as Textual's own `DataTable`. Necessary because `ScrollableContainer`
  has `can_focus=True`, so Tab targets the container, not its children.
- `$accent 40%` for dimmed-but-themed borders on unfocused panels; full
  `$accent` on `:focus`. Avoids invisible `$panel` borders.
- **TransportBar self-wiring**: `on_mount` calls `self.watch(app, "reactive",
  callback, init=True)` for each QobitApp reactive. Any TransportBar instance
  placed anywhere in the widget tree is automatically live — no
  `sync_transport_bar()` calls needed from screens.
- **ListView highlight**: `QobitApp._on_list_highlighted` catches all
  `ListView.Highlighted` events app-wide and manages a `-hl` CSS class on the
  highlighted item's child Labels. `Label.-hl { color: $accent }` in app CSS
  colours the selected row. All list item widgets (TrackItem, AlbumItem, etc.)
  use `.primary` / `.secondary` CSS classes instead of Rich inline markup so
  that the `-hl` override works correctly.
- **Custom back navigation**: ArtistScreen overrides `action_navigate_back`
  (bound to Escape with `priority=True`) to pop the inline album panel before
  popping the screen. The `escape` binding in BINDINGS calls this action rather
  than `app.pop_screen` directly.

## Qobuz API layer

`client.py` reverse-engineers app_id and signing secrets from Qobuz's web
player bundle on login; the working values are cached in config. Streaming URLs
expire in ~10 minutes — re-fetch on track start, not on app start. Quality
fallback order: FLAC_24_192 → FLAC_24_96 → FLAC_CD.

`get_artist_page` calls `artist/page` (popularity-ranked `top_tracks`) and
`artist/get` (biography, image, albums) concurrently via `asyncio.gather`, then
merges them: `detail["tracks"] = {"items": items}`. `Track.from_api` handles
both `performer.name` (string, from `artist/get`) and `artist.name.display`
(nested dict, from `artist/page`) artist name formats.

## Image protocol

Kitty graphics protocol only (no iTerm2 IIP, no sixel). Implemented via
`textual-image`. Target terminals: kitty, Ghostty, iTerm2 3.5+.

`textual_image.widget.TGPImage` is the widget; images are fetched
asynchronously in `@work` workers. `get_cell_size()` from
`textual_image._terminal` is used to compute pixel/cell ratios for correct
image aspect ratios.

## Config paths

```
~/.config/qobit/config.json       credentials, audio_device, theme, oauth session
~/.local/share/qobit/mpv.sock     IPC socket (re-created on each play call)
```

## Development

```bash
uv sync --group dev
uv run qobit auth          # authenticate once
uv run qobit devices       # check audio device list
uv run qobit play "test"   # smoke test CLI playback
uv run qobit               # launch TUI
uv run ruff check .
uv run ruff format .
uv run textual console     # Textual dev console (separate terminal)
```

## Reference repos

- `pierdom/picast` — audio player TUI template (Raw Rich, not Textual — useful
  for transport bar patterns)
- `pierdom/tuidash` — Textual patterns: `@work(thread=True)`, `reactive` +
  `watch_*`, `DEFAULT_CSS`, `ContentSwitcher`
- `vicrodh/qbz` — bit-perfect audio reference (Rust; ALSA/CoreAudio strategy
  maps directly to mpv flags)
- `pierdom/qobuz-mcp` — original source of `client.py`

## Phases

1. CLI slice: auth → search → stream URL → bit-perfect play → verify **✓**
2. Textual TUI shell + search + album/playlist detail + transport bar **✓**
3. Album art via textual-image (Kitty protocol) **✓** (ArtistScreen; needed in AlbumScreen/PlaylistScreen)
4. Library tabs: playlists/tracks/artists/albums (placeholder quality) **✓**
5. ArtistScreen polish: popularity top tracks, inline album detail panel **✓**
6. **Play Next queue + end-of-track auto-advance** ← next
7. UI overhaul: Search, AlbumScreen, PlaylistScreen
8. Context menus + TrackScreen detail overlay
9. MPRIS (Linux), macOS MediaRemote, PipeWire ReserveDevice1
10. Gapless playback, ReplayGain, CMAF fallback

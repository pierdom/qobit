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
    ├── _images.py           Shared image fetch layer: asyncio.Semaphore(6) caps
    │                        concurrent HTTP fetches; module-level URL→PIL cache
    │                        prevents re-downloading the same image within a session
    ├── screens/
    │   ├── search.py        SearchView + shared item widgets (TrackItem,
    │   │                    AlbumItem, ArtistItem, PlaylistItem)
    │   ├── album_detail.py  AlbumDetailPanel (shared: art + metadata +
    │   │                    tracklist widget); AlbumScreen (legacy push screen)
    │   ├── artist_detail.py ArtistHeader (shared: image + bio widget);
    │   │                    ArtistCard + ArtistGrid (shared: artist tile grid);
    │   │                    AlbumCard + AlbumGrid (shared: album tile grid);
    │   │                    ArtistScreen — top tracks + album grid +
    │   │                    inline album detail panel
    │   ├── playlist_detail.py PlaylistScreen — track list for a playlist
    │   ├── albums.py        AlbumsView — favourite albums grid + inline
    │   │                    album detail with artist image/bio
    │   ├── artists.py       ArtistsView — favourite artists grid + inline
    │   │                    artist detail (bio + top tracks + album grid) +
    │   │                    inline album detail; mirrors AlbumsView aesthetic
    │   ├── tracks.py        TracksView — favourite tracks: sortable list
    │   │                    (Date Added/Artist/Title/Album), full pagination,
    │   │                    lazy load, in-border live filter (/)
    │   └── playlists.py     PlaylistsView — user playlists library tab
    └── widgets/
        └── transport.py     TransportBar — album art + label + album +
                             progress bar + click-to-seek; _TransportContent
                             inner widget owns render() and mouse seek;
                             self-wires to QobitApp reactives on mount
```

## Current state

### Done

- **Phase 1 (CLI)**: `qobit auth`, `qobit devices`, `qobit set-device`,
  `qobit play <query>` with quality fallback and bit-perfect verification.
- **Auth**: Browser OAuth flow (primary) + email/password fallback. Session
  saved to `~/.config/qobit/config.json` and restored on next launch.
- **TUI shell**: 5-tab layout (Tracks / Artists / Albums / Playlists / Search),
  transport bar with click-to-seek, pause/seek bindings, escape-to-nav.
  Tabs 1–5 mapped to keyboard shortcuts in the same order.
- **Library tabs**: Tracks, Artists, Albums, Playlists each load the user's
  Qobuz favourites. All library tabs **lazy-load** on first `on_show` (not
  `on_mount`) so startup is instant and tabs don't race each other on open.
- **SearchView**: Free-text search across tracks/albums/artists in parallel;
  results shown in three bordered sections (Artists / Tracks / Albums) with
  dimmed-accent borders at rest and full-accent on focus; focus auto-moves to
  the first non-empty section after a search; "/" refocuses the search input
  from any result list. Selecting plays a track, opens an album, or opens an
  artist.
- **AlbumScreen**: Full track list with numbering and durations; selecting a
  track plays it and pops the screen.
- **PlaylistScreen**: Full track list; selecting plays and pops.
- **ArtistScreen**: Biography, popularity-ranked top tracks (from `artist/page`
  endpoint), Albums & EPs grid with album art (Kitty protocol), keyboard arrow
  navigation, reverse-chronological sort, dimmed-accent borders for unfocused
  panels. Clicking or pressing Enter on an AlbumCard opens an inline album
  detail panel (ContentSwitcher, no screen push) showing art, metadata, and
  full track list. Escape navigates back to the artist view before popping the
  screen and restoring the source tab. Progressive loading: `_load_detail()`
  and `_load_tracks()` run as concurrent `@work` workers so bio/image/albums
  and top tracks populate independently as each API call returns.
- **AlbumsView**: Favourite albums in a sortable responsive tile grid
  (AlbumGrid, `tile_min_width=33`), showing album art, title, artist, year.
  Sort by Date Added / Artist / Album / Year; `s` cycles sort key, `r` reverses
  direction. `/` enters in-border live filter mode (title + artist); subtitle
  cycles through `/ query_` (typing), `⌕ query` (filter closed, results
  active), and the sort indicator. Selecting an album switches inline
  (ContentSwitcher) to a full album detail view: ArtistHeader (image +
  biography) above AlbumDetailPanel (art, metadata, track list). Escape walks
  back: album detail → grid → clear filter.
- **ArtistsView**: Mirrors AlbumsView aesthetic. Favourite artists in a
  sortable tile grid (ArtistGrid, `tile_min_width=33`) of ArtistCards (image +
  name + album count). Sort by Date Added / Name; same `s`/`r` bindings as
  AlbumsView. `/` in-border filter (artist name). Selecting an artist switches
  inline (ContentSwitcher) to a 3-level detail view: ArtistHeader above a
  nested ContentSwitcher that shows either (a) Top Tracks ListView + Albums &
  EPs AlbumGrid, or (b) AlbumDetailPanel when an album is selected. Escape
  navigates back through all levels: album detail → artist detail → grid →
  clear filter.
- **Shared widgets**: `AlbumDetailPanel` (`album_detail.py`) — reusable art +
  metadata + tracklist panel used by ArtistScreen, AlbumsView, ArtistsView.
  `ArtistHeader` (`artist_detail.py`) — reusable image + biography header used
  by ArtistScreen, AlbumsView, ArtistsView. `AlbumCard` + `AlbumGrid` and
  `ArtistCard` + `ArtistGrid` all live in `artist_detail.py` and are shared
  across screens. HTML tags stripped from album descriptions via `_strip_html`.
- **Image performance** (`ui/_images.py`): All image fetches go through a
  shared `fetch_image(url)` coroutine. A module-level `asyncio.Semaphore(6)`
  caps concurrent HTTP connections; a `dict[url → PIL.Image]` cache prevents
  re-downloading the same image within a session. Widget code calls
  `fetch_image` from a `@work` worker and sets `TGPImage.image` on return.
- **Session restore**: `QobuzClient.restore_session()` called in
  `QobitApp.__init__()` (not `on_mount`) so credentials are available before
  any child widget worker fires.
- **TracksView**: Favourite tracks in a sortable dense list (FavTrackRow:
  artist — title / album · duration). Full pagination via
  `get_all_favorite_tracks()`. Sort by Date Added / Artist / Title / Album;
  `s`/`r` bindings. `/` in-border live filter (title + artist + album); same
  two-state subtitle as AlbumsView. Opens on app startup. `_render_version`
  counter prevents stale mount workers from overwriting newer results.
- **TransportBar**: Horizontal layout — album art (TGPImage, hidden until
  playing) on the left, `_TransportContent` (artist/title + album + progress
  bar via `render()`) on the right. Art fetched via shared `fetch_image()`
  cache. Height 6 (4 content lines). `_TransportContent` owns the mouse-seek
  handlers so click coordinates are local with no offset arithmetic. Self-wires
  to QobitApp reactives on mount.
- **Transparent background**: Toggle via command palette (`Draw theme
  background`); preserved across sessions.
- **Bit-perfect flags**: `--af-clr`, `--audio-pitch-correction=no`,
  `--audio-exclusive=yes` on macOS with CoreAudio device.

### Work in progress

**ArtistScreen / AlbumsView / ArtistsView**
- Missing: playing a top track / album track should enqueue the rest
  (Play Next — see below).
- Missing: context menu on AlbumCard / ArtistCard / track rows (add to queue,
  play album, open artist).
- UI still rough: biography text needs better overflow handling.

### Not yet started / needs design

**Play Next queue** — see dedicated section below; this is the next major
feature.

**Search UI overhaul** — the current three-section layout is functional but
minimal. Needs richer result rows (album art thumbnails, genre tags) and a way
to distinguish quality tiers. Possible direction: tracks on the left, albums +
artists on the right.

**AlbumScreen redesign** — currently a bare header + flat track list. Needs:
album art (Kitty), richer header (artist name clickable → ArtistScreen, year,
genre, label, format badge), "Play all" / "Play from here" queue actions,
per-track format badge (Hi-Res / CD).

**PlaylistScreen redesign** — same issues as AlbumScreen: no art, minimal
header, no queue actions.

**PlaylistsView** — still a minimal placeholder (flat list, no art, no sort).
Needs the same treatment as AlbumsView / ArtistsView.

**AlbumsView / ArtistsView / TracksView** — grids/list + inline detail + filter
now done; still missing: "Play all" / "Play from here" actions, per-track
format badges.

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
  within ArtistScreen, AlbumsView, and ArtistsView to switch between views
  without pushing a new screen. ArtistsView uses a nested ContentSwitcher (one
  for grid vs artist-detail, one for artist-content vs album-detail).
- `Screen` push/pop for detail views (AlbumScreen, PlaylistScreen, ArtistScreen).
- Container-owns-cursor pattern (AlbumGrid, ArtistGrid): the container holds
  focus, tracks `_cursor: int`, applies `-selected` CSS class to the active
  child. Same pattern as Textual's own `DataTable`. Necessary because
  `ScrollableContainer` has `can_focus=True`, so Tab targets the container,
  not its children.
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
- **Custom back navigation**: `ArtistScreen.action_navigate_back` (Escape,
  `priority=True`) pops the inline album panel before popping the screen; on
  full back-out calls `app.action_switch_tab(self._source.lower())` before
  `pop_screen()` to restore the originating tab. `AlbumsView.action_navigate_back`
  and `ArtistsView.action_navigate_back` return `bool` — `True` if they
  consumed the event, `False` to let `QobitApp.action_focus_tabs` fall through
  to the tab bar. The app-level handler checks
  `hasattr(active_view, "action_navigate_back")` and short-circuits on `True`,
  so any view can hook into Escape without touching `app.py`.
- **Shared widget pattern**: `AlbumDetailPanel` and `ArtistHeader` use `.ap-*`
  / `.ah-*` CSS class selectors (not IDs) so multiple instances can coexist in
  the widget tree without selector conflicts.
- **Lazy load pattern**: Library tabs call `_load()` inside `on_show` behind a
  `_loaded: bool` guard rather than `on_mount`. This prevents all tabs from
  racing at startup; each tab fetches only when the user first opens it.
- **Batch mount pattern**: Cards and list rows are collected into a list first,
  then mounted with a single `await container.mount(*items)` call. Mounting
  one item at a time triggers N layout passes; batching collapses them to one.
- **Context-aware sort bindings**: `s` / `r` in `app.py` call
  `action_cycle_sort` / `action_toggle_reverse`, which iterate over
  (TracksView, AlbumsView, ArtistsView) and delegate to whichever is currently
  displayed. Each view guards its sort actions against filter mode (`_filter_active`).
- **In-border live filter**: `/` binding on TracksView, AlbumsView, ArtistsView
  enters filter mode. Keystrokes are intercepted in `on_key` (bubbles from the
  focused grid/list since grids don't consume printable chars). The query is
  displayed in `border_subtitle`: `/ query_` while typing, `⌕ query` when the
  filter bar is "closed" but results remain active. `action_navigate_back`
  handles filter teardown as the last level before returning `False`. Opening a
  detail view sets `_filter_active = False` but preserves `_filter_query` so
  the `⌕` indicator is still shown on return. `_render_version` counter on each
  view prevents stale `@work` mount workers from overwriting a newer result.

## Qobuz API layer

`client.py` reverse-engineers app_id and signing secrets from Qobuz's web
player bundle on login; the working values are cached in config. Streaming URLs
expire in ~10 minutes — re-fetch on track start, not on app start. Quality
fallback order: FLAC_24_192 → FLAC_24_96 → FLAC_CD.

ArtistScreen and ArtistsView use two separate client methods that fire
concurrently: `get_artist_detail` calls `artist/get` (biography, image, albums
in reverse-chronological order); `get_artist_top_tracks` calls `artist/page`
(popularity-ranked top tracks). `Track.from_api` handles both `performer.name`
(string, from `artist/get`) and `artist.name.display` (nested dict, from
`artist/page`) artist name formats. The legacy `get_artist_page` method still
exists (merges both into one call) but is no longer used by the UI.

Paginated helpers: `get_all_favorite_tracks()`, `get_all_favorite_albums()`,
and `get_all_favorite_artists()` fetch the complete favourites list by issuing
parallel requests for each 50-item page after the first.

`Track`, `Album`, and `Artist` all carry a `favorited_at: int | None` field
(Unix timestamp) populated from the favourites API; used for Date Added sorting.

## Image protocol

Kitty graphics protocol only (no iTerm2 IIP, no sixel). Implemented via
`textual-image`. Target terminals: kitty, Ghostty, iTerm2 3.5+.

`textual_image.widget.TGPImage` is the widget. All HTTP fetches go through
`ui/_images.fetch_image(url)`: semaphore-limited (6 concurrent), cached for
the session. `get_cell_size()` from `textual_image._terminal` is used to
compute pixel/cell ratios for correct image aspect ratios.

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
6. AlbumsView + ArtistsView: sortable tile grids + inline detail panels;
   shared image cache + semaphore; lazy load + batch mount **✓**
7. TracksView: sortable paginated list + album art in TransportBar +
   in-border live filter; same filter pattern rolled out to Albums + Artists **✓**
8. **Play Next queue + end-of-track auto-advance** ← next
9. UI overhaul: Search, AlbumScreen, PlaylistScreen, PlaylistsView
9. Context menus + TrackScreen detail overlay
10. MPRIS (Linux), macOS MediaRemote, PipeWire ReserveDevice1
11. Gapless playback, ReplayGain, CMAF fallback

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
├── store.py                 JSON persistence for runtime state (DATA_DIR);
│                            holds player_state.json (last track + position)
├── qobuz/
│   ├── client.py            QobuzClient (reverse-engineers app_id/secret from
│   │                        web bundle; email/password + OAuth flows)
│   └── models.py            typed dataclasses: Track, Album, Artist, Playlist,
│                            StreamUrl
├── audio/
│   ├── device.py            enumerate mpv audio devices
│   ├── media_keys.py        OS media controls — macOS MPRemoteCommandCenter +
│   │                        MPNowPlayingInfoCenter; Linux MPRIS2 via pydbus;
│   │                        silently degrades when optional deps absent
│   ├── player.py            mpv subprocess wrapper with bit-perfect flags;
│   │                        _stop_gen counter for race-safe end-of-track detection
│   └── verify.py            post-play rate/format verification via mpv IPC
└── ui/
    ├── app.py               QobitApp — reactive state, transport controls,
    │                        session restore, tab routing, play queue
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
    │   ├── playlists.py     PlaylistsView — user playlists library tab
    │   └── queue.py         QueueView — NowPlayingRow (accent header, ▶/⏸ icon)
    │                        + QueueTrackRow list; watches queue_version +
    │                        now_playing + is_paused; _render_version guards
    └── widgets/
        ├── lists.py         PagedListView — ListView subclass binding PageUp/
        │                    PageDown/Home/End to move the highlighted selection
        │                    (scrolling to follow it) rather than scrolling past
        │                    a stationary cursor. Used by Tracks + Queue lists.
        └── transport.py     TransportBar — album art + label + album +
                             progress bar; clicking title/album row toggles
                             pause, clicking progress row seeks; _TransportContent
                             inner widget owns render() and mouse handlers;
                             self-wires to QobitApp reactives on mount
```

## Current state

### Done

- **Phase 1 (CLI)**: `qobit auth`, `qobit devices`, `qobit set-device`,
  `qobit play <query>` with quality fallback and bit-perfect verification,
  `qobit clear-cache` (deletes the on-disk cover-art cache via
  `ui/_images.clear_disk_cache`).
- **Auth**: Browser OAuth flow (primary) + email/password fallback. Session
  saved to `~/.config/qobit/config.json` and restored on next launch.
- **TUI shell**: 6-tab layout (Tracks / Artists / Albums / Playlists / Search /
  Queue), transport bar with click-to-seek, pause/seek bindings, escape-to-nav.
  Tabs 1–6 mapped to keyboard shortcuts in the same order.
- **Footer**: Context-aware — `s Sort`, `r Rev`, `/ Filter` appear only on
  sortable/filterable pages (Tracks, Albums, Artists); `Space Pause` always
  visible. Achieved by defining these bindings at the view level rather than
  app level so Textual's footer only shows them when the relevant view is active.
- **Library tabs**: Tracks, Artists, Albums, Playlists each load the user's
  Qobuz favourites. All library tabs **lazy-load** on first `on_show` (not
  `on_mount`) so startup is instant and tabs don't race each other on open.
- **SearchView**: Free-text search across tracks/albums/artists in parallel;
  results shown in three bordered sections (Artists / Tracks / Albums) with
  dimmed-accent borders at rest and full-accent on focus; focus auto-moves to
  the first non-empty section after a search; "/" refocuses the search input
  from any result list. Selecting plays a track, opens an album, or opens an
  artist. Selecting a track queues remaining visible tracks below it.
- **AlbumScreen**: Full track list with numbering and durations; selecting a
  track plays it (remaining tracks queued) and pops the screen.
- **PlaylistScreen**: Full track list; selecting plays (remaining tracks queued)
  and pops.
- **ArtistScreen**: Biography, popularity-ranked top tracks (from `artist/page`
  endpoint), Albums & EPs grid with album art (Kitty protocol), keyboard arrow
  navigation, reverse-chronological sort, dimmed-accent borders for unfocused
  panels. Clicking or pressing Enter on an AlbumCard opens an inline album
  detail panel (ContentSwitcher, no screen push) showing art, metadata, and
  full track list. Escape navigates back to the artist view before popping the
  screen and restoring the source tab. Progressive loading: `_load_detail()`
  and `_load_tracks()` run as concurrent `@work` workers so bio/image/albums
  and top tracks populate independently as each API call returns. Selecting a
  top track queues remaining top tracks; selecting an album track queues
  remaining album tracks.
- **AlbumsView**: Favourite albums in a sortable responsive tile grid
  (AlbumGrid, `tile_min_width=33`), showing album art, title, artist, year.
  Sort by Date Added / Artist / Album / Year; `s` cycles sort key, `r` reverses
  direction. `/` enters in-border live filter mode (title + artist); subtitle
  cycles through `/ query_` (typing), `⌕ query` (filter closed, results
  active), and the sort indicator. Selecting an album switches inline
  (ContentSwitcher) to a full album detail view: ArtistHeader (image +
  biography) above AlbumDetailPanel (art, metadata, track list). Escape walks
  back: album detail → grid → clear filter. Selecting a track queues remaining
  album tracks.
- **ArtistsView**: Mirrors AlbumsView aesthetic. Favourite artists in a
  sortable tile grid (ArtistGrid, `tile_min_width=33`) of ArtistCards (image +
  name + album count). Sort by Date Added / Name; same `s`/`r` bindings as
  AlbumsView. `/` in-border filter (artist name). Selecting an artist switches
  inline (ContentSwitcher) to a 3-level detail view: ArtistHeader above a
  nested ContentSwitcher that shows either (a) Top Tracks ListView + Albums &
  EPs AlbumGrid, or (b) AlbumDetailPanel when an album is selected. Escape
  navigates back through all levels: album detail → artist detail → grid →
  clear filter. Track selection queues remaining tracks in context.
- **Shared widgets**: `AlbumDetailPanel` (`album_detail.py`) — reusable art +
  metadata + tracklist panel used by ArtistScreen, AlbumsView, ArtistsView.
  `AlbumDetailPanel.TrackSelected` carries a `queue: list[Track]` with the
  remaining tracks so all parent handlers can pass it to `play_track`.
  `ArtistHeader` (`artist_detail.py`) — reusable image + biography header used
  by ArtistScreen, AlbumsView, ArtistsView. `AlbumCard` + `AlbumGrid` and
  `ArtistCard` + `ArtistGrid` all live in `artist_detail.py` and are shared
  across screens. HTML tags stripped from album descriptions via `_strip_html`.
- **Image performance** (`ui/_images.py`): All image fetches go through a
  shared `fetch_image(url)` coroutine. A module-level `asyncio.Semaphore(6)`
  caps concurrent HTTP connections; an in-memory `dict[url → PIL.Image]` cache
  prevents re-downloading the same image within a session. Widget code calls
  `fetch_image` from a `@work` worker and sets `TGPImage.image` on return.
  Three further layers keep image-rich pages responsive:
  - **Shared `httpx.AsyncClient`** (`_get_client()`, closed via `close_client()`
    in `QobitApp.on_unmount`) so the ~50 fetches a grid fires reuse
    connections/TLS instead of each building a fresh pool.
  - **Downscale + RGB-normalise on fetch** (`_normalize`): source art (`mega`
    ~1500px, `large` ~600px) is capped at `_MAX_EDGE=600` (largest on-screen
    use ≈320px) and colour-converted *once*. textual-image otherwise re-resizes
    the full source and re-converts mode on every repaint, and re-transmits the
    whole image to the terminal on each widget `render()`.
  - **On-disk cache** (`~/.cache/qobit/images/{sha1}.jpg`, normalised JPEG q85):
    relaunching doesn't re-download the library's art. Disk read/decode and the
    network decode/normalise both run via `asyncio.to_thread` so a grid of
    covers never stalls keystrokes. Corrupt/unwritable cache degrades silently.
- **Session restore**: `QobuzClient.restore_session()` called in
  `QobitApp.__init__()` (not `on_mount`) so credentials are available before
  any child widget worker fires.
- **Playback state restore**: `_save_player_state()` persists the now-playing
  track (`dataclasses.asdict`) + position to `player_state.json` on every track
  change, on a ~5s throttle from the poll thread, and on `on_unmount`.
  `_restore_player_state()` (in `on_mount`) reloads it into the transport bar
  **paused and unloaded** — it does *not* spawn mpv (which on macOS would grab
  the audio device in exclusive/hog mode while idle, and avoids surprise audio).
  The track+position are held in `_pending_resume`; the next `action_pause`
  (Space / transport click) calls `play_track(track, start=position)`, and
  `MpvPlayer.play(url, start=)` passes `--start=` so mpv seeks on load. Stream
  URLs expire, so only metadata+position are stored and the stream is re-fetched
  on resume. `_restoring` guards the watcher from saving mid-restore.
- **TracksView**: Favourite tracks in a sortable dense list (FavTrackRow:
  artist — title / album · duration). Full pagination via
  `get_all_favorite_tracks()`. Sort by Date Added / Artist / Title / Album;
  `s`/`r` bindings. `/` in-border live filter (title + artist + album); same
  two-state subtitle as AlbumsView. Opens on app startup. `_render_version`
  counter prevents stale mount workers from overwriting newer results.
  Selecting a track queues remaining visible tracks below it.
- **TransportBar**: Horizontal layout — album art (TGPImage, hidden until
  playing) on the left, `_TransportContent` (artist/title + album + progress
  bar via `render()`) on the right. Art fetched via shared `fetch_image()`
  cache. Height 6 (4 content lines). `_TransportContent` owns the mouse
  handlers: clicking the title/album rows (y < 2) toggles pause; clicking the
  progress bar row (y >= 2) seeks to that position. Self-wires to QobitApp
  reactives on mount.
- **Play Next queue**: `QobitApp` maintains `_play_queue: list[Track]` and a
  `queue_version: reactive[int]`. `play_track(track, queue=None)` plays
  immediately and sets the queue if provided. `_advance_queue()` pops the next
  track and plays it. End-of-track detection uses `_stop_gen: int` on
  `MpvPlayer` — incremented on every `stop()` call; the poll thread records the
  gen when `running=True` and compares it on transition to `False`; mismatch
  means `stop()` was called (skip advance), match means natural end (advance).
  `QueueView` tab shows `NowPlayingRow` (accent styling, live ▶/⏸ icon) at the
  top followed by `QueueTrackRow` items; selecting a queue item plays it with
  remaining items re-queued.
- **OS media controls** (`audio/media_keys.py`): macOS — registers
  `togglePlayPauseCommand`, `nextTrackCommand`, `previousTrackCommand` with
  `MPRemoteCommandCenter`; updates `MPNowPlayingInfoCenter` with title, artist,
  album, position, rate; artwork fetched async via shared `fetch_image()` and
  converted PIL → NSData → NSImage → `MPMediaItemArtwork`. Linux — full MPRIS2
  implementation via pydbus; GLib main loop in daemon thread; `Metadata`
  property includes `mpris:artUrl` (HTTPS, clients fetch themselves). The
  backend declares a `signal()` attribute for **every** `<signal>` in the
  introspection XML (`Seeked` + `PropertiesChanged`) — pydbus's `bus.publish()`
  raises `AttributeError` for any missing one, which `MediaKeys.__init__`
  swallows, so a missing signal silently kills the whole backend. `update()`
  emits `PropertiesChanged` (marshalled onto the GLib thread via
  `GLib.idle_add`) whenever the track or play/pause state changes, so the DE
  re-reads `Metadata` / `PlaybackStatus` instead of showing the first track it
  ever saw. Both backends degrade silently when optional deps
  (`pyobjc-framework-MediaPlayer` / `pydbus` + `PyGObject`) are absent.
  Installed via `pip install qobit[macos]` or `qobit[linux]`. Note: the Linux
  extra needs both `pydbus` and `PyGObject` (`gi`), which under `uv` must live
  in the project venv — the system `python-gobject` is not visible to
  `uv run`.
- **Transparent background**: Toggle via command palette (`Draw theme
  background`); preserved across sessions.
- **Theme persistence**: The active Textual theme (changed via the command
  palette `Change theme`) is saved to `config.json` (`theme` key) and restored
  on next launch. `QobitApp.watch_theme` persists via `set_saved_theme`, gated
  behind a `_theme_ready` flag so the default theme set during init doesn't
  clobber the saved one before `on_mount` applies it.
- **Unified scrollbars**: A global `* { scrollbar-* }` block in `QobitApp.CSS`
  gives every scrollable widget on every page the same design — 1-cell thin,
  track blended into `$surface`, thumb `$surface-lighten-2` (→ `$accent` while
  dragging).
- **Bit-perfect flags**: `--af-clr`, `--audio-pitch-correction=no`,
  `--audio-exclusive=yes` on macOS with CoreAudio device. Also
  `--load-scripts=no` so mpv doesn't auto-load the system `mpv-mpris` plugin —
  it would otherwise publish a *second* MPRIS player exposing the tagless
  stream URL (wrong artist/title/album/art). qobit owns the MPRIS surface via
  `audio/media_keys.py`.

### Work in progress

**ArtistScreen / AlbumsView / ArtistsView**
- Missing: context menu on AlbumCard / ArtistCard / track rows (add to queue,
  play album, open artist).
- UI still rough: biography text needs better overflow handling.

### Not yet started / needs design

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

**Queue management** — currently no way to reorder or remove items from the
queue. QueueView is display-only (can skip forward by selecting an item).

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
- **Async image-worker guard**: every `@work` art fetcher (AlbumCard/ArtistCard
  `_fetch_art`, ArtistHeader/AlbumDetailPanel/PlaylistScreen/TransportBar) does
  `if img is not None and self.is_mounted:` then a `try … except NoMatches`
  around `self.query_one(TGPImage).image = img`. The worker resumes after an
  `await` that may outlive the widget (app teardown, sort/reload remount), so
  the target `TGPImage` can be gone — without the guard Textual raises
  `NoMatches`. Faster disk/memory cache hits make the resume timing less
  predictable, so any new art-fetch worker must replicate this guard.
- **TransportBar self-wiring**: `on_mount` calls `self.watch(app, "reactive",
  callback, init=True)` for each QobitApp reactive. Any TransportBar instance
  placed anywhere in the widget tree is automatically live — no
  `sync_transport_bar()` calls needed from screens.
- **Paged list navigation**: `PagedListView` (`ui/widgets/lists.py`) is a
  `ListView` subclass for long lists (Tracks, Queue). Plain `ListView` only
  binds up/down/enter, so PageUp/PageDown fall through to `VerticalScroll` and
  move the viewport while the selection stays put. `PagedListView` binds
  PageUp/PageDown/Home/End to set `index` by a page (viewport height ÷ uniform
  row height) and top-aligns the scroll, so the highlight follows the visible
  page. Setting `index` reuses Textual's `watch_index` (scroll-to + highlight)
  and still emits `ListView.Highlighted`, so the app-level `-hl` styling works.
  The card grids get the same behaviour via the `_GridNav` mixin
  (`artist_detail.py`), shared by AlbumGrid and ArtistGrid: PageUp/PageDown move
  the cursor by visible-rows × `_cols` (row pitch measured from card geometry)
  and Home/End jump to the first/last card, each top-aligning the scroll.
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
- **Favourites cache + heart glyph**: `QobitApp.ensure_favorite_tracks()`
  fetches the raw favourite-track list exactly once (double-checked locking via
  `_fav_lock`) and caches it; `ensure_favorite_ids()` derives the id set from
  it. TracksView's list load and the `♥` (`ICON_FAV`) indicator in the other
  track lists share this single fetch (warmed at mount via `_warm_favorite_ids`).
  Track-list builders (QueueTrackRow/NowPlayingRow, ArtistTrackRow, album
  `TrackRow`) `await app.ensure_favorite_ids()` in their `@work` builder and
  pass a `favorite: bool` to the row. The row's primary label is built with
  `Content.assemble(text, ("  ♥", "$accent"))` — these labels are Textual
  *content*-markup labels, so the track text is passed as a literal `Content`
  part (no markup parsing → titles with `[`/`]` can't corrupt or crash the
  render) while the heart gets an `$accent` span. The Tracks tab shows no
  heart — everything there is already a favourite.
- **Lazy load pattern**: Library tabs call `_load()` inside `on_show` behind a
  `_loaded: bool` guard rather than `on_mount`. This prevents all tabs from
  racing at startup; each tab fetches only when the user first opens it.
- **Batch mount pattern**: Cards and list rows are collected into a list first,
  then mounted with a single `await container.mount(*items)` call. Mounting
  one item at a time triggers N layout passes; batching collapses them to one.
- **Context-aware BINDINGS**: `s` / `r` / `/` are declared in each view's own
  `BINDINGS` list (not at app level), so the Footer only shows them when that
  view is active. `on_key` stops propagation for printable chars when
  `_filter_active` so the `s`/`r` bindings don't fire during filter typing.
- **In-border live filter**: `/` binding on TracksView, AlbumsView, ArtistsView
  enters filter mode. Keystrokes are intercepted in `on_key` (bubbles from the
  focused grid/list since grids don't consume printable chars). The query is
  displayed in `border_subtitle`: `/ query_` while typing, `⌕ query` when the
  filter bar is "closed" but results remain active. `action_navigate_back`
  handles filter teardown as the last level before returning `False`. Opening a
  detail view sets `_filter_active = False` but preserves `_filter_query` so
  the `⌕` indicator is still shown on return. `_render_version` counter on each
  view prevents stale `@work` mount workers from overwriting a newer result.
  **Performance**: filter keystrokes are debounced via a `_filter_timer`
  (`_schedule_filter`, ~0.12–0.18s) so a burst of typing coalesces into one
  pass. The image grids (AlbumsView, ArtistsView) **filter by hide/show**, not
  remount: `_apply_filter` computes the matching id set and calls
  `AlbumGrid/ArtistGrid.filter_cards(ids)`, which toggles `card.display` on
  already-mounted cards (so the Kitty images are never re-transmitted). The
  grids' cursor navigation (`_visible_cards`) operates only over visible cards.
  Remount (`_render_grid`) is reserved for data load and sort, and re-applies
  the active filter afterwards. TracksView (no images) still debounced-remounts.
- **Queue version pattern**: `queue_version: reactive[int]` on `QobitApp` is
  incremented whenever `_play_queue` changes. Widgets that need to re-render
  when the queue changes (e.g. `QueueView`) watch this reactive. Combined with a
  local `_render_version` counter they guard against stale `@work` workers that
  fire after a newer version has already started.
- **`_stop_gen` for end-of-track**: `MpvPlayer._stop_gen: int` is incremented
  on every `stop()` call. The poll thread records the gen when it last saw
  `running=True`; when `running` goes False it compares: equal gen means
  natural end → advance queue; different gen means stop was called → skip.
  Race-safe because even if the thread checks after `stop()` completes the gen
  will have changed.
- **OS media key callbacks**: `MediaKeys` is constructed in `QobitApp.__init__`
  with `lambda: self.call_from_thread(...)` callbacks so ObjC/GLib threads
  safely post events onto the Textual event loop. `watch_now_playing`,
  `watch_is_playing`, `watch_is_paused` reactive watchers on `QobitApp` drive
  `_media_keys.update(...)` so the OS Now Playing display stays in sync.

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

`Album.from_api` backfills its own `image_url` onto each nested track whose
`image_url` is unset. Track items inside an album payload carry no `album`
object of their own, so `Track.from_api` can't resolve their cover — without
this backfill, playing a track from the album view (or its play queue) would
leave the transport bar and OS media-control art unchanged.

## Image protocol

Kitty graphics protocol only (no iTerm2 IIP, no sixel). Implemented via
`textual-image`. Target terminals: kitty, Ghostty, iTerm2 3.5+.

`textual_image.widget.TGPImage` is the widget. All HTTP fetches go through
`ui/_images.fetch_image(url)`: semaphore-limited (6 concurrent), cached for
the session. `get_cell_size()` from `textual_image._terminal` is used to
compute pixel/cell ratios for correct image aspect ratios.

## Config paths

```
~/.config/qobit/config.json            credentials, audio_device, theme, oauth session
~/.local/share/qobit/mpv.sock          IPC socket (re-created on each play call)
~/.local/share/qobit/player_state.json last track + position (restored on launch)
~/.cache/qobit/images/                 on-disk cover-art cache (qobit clear-cache)
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
8. Play Next queue + end-of-track auto-advance + Queue tab **✓**
9. OS media controls: macOS MPRemoteCommandCenter + MPNowPlayingInfoCenter;
   Linux MPRIS2 via pydbus; artwork via shared image cache **✓**
10. UI overhaul: Search, AlbumScreen, PlaylistScreen, PlaylistsView
11. Context menus + TrackScreen detail overlay
12. Gapless playback, ReplayGain, CMAF fallback

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
│   ├── device.py            enumerate mpv audio devices; synthesizes raw
│   │                        alsa/hw:CARD=…,DEV=… entries mpv never lists and
│   │                        flags them (+ coreaudio) bit_perfect, sorted first
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
    │   ├── context_menu.py  TrackContextMenu — ModalScreen popup for one track
    │   │                    (Play next / Add to queue / Play radio / Go to
    │   │                    artist / Go to album; + Remove from queue when
    │   │                    in_queue=True); opened app-wide with `i`
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
    │   └── queue.py         QueueView — responsive two-pane: left TrackListView
    │                        timeline (Recently Played HistoryTrackRow →
    │                        NowPlayingListRow (accent, live ▶/⏸) → Up Next
    │                        QueueTrackRow, full-width SectionHeader dividers with
    │                        counts; cursor parks on the now-playing row) + right
    │                        NowPlayingHero (big art + rich metadata + scrollable
    │                        artist bio; self-wires to app reactives like
    │                        TransportBar; f favourites now-playing). Below
    │                        _NARROW_BELOW=100 cols the hero is dropped (.-narrow)
    │                        and the list goes full-width. watches queue_version +
    │                        now_playing; _render_version guards. c clears queue,
    │                        X history
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

Phases 1–9 complete (CLI, TUI shell, library tabs, queue, OS media controls).

## Roadmap

- Context menus: `i` opens a per-track popup (done for track rows); still todo on album/artist cards
- AlbumScreen / PlaylistScreen / PlaylistsView redesign (art, rich header, queue actions, format badges)
- Search UI overhaul (art thumbnails, quality badges, better layout)
- TrackScreen detail overlay (composer, performer, related albums)
- Queue reorder / single-item remove
- Gapless playback, ReplayGain, CMAF fallback

## Key non-obvious patterns

**`_stop_gen` for end-of-track** — `MpvPlayer._stop_gen` increments on every `stop()` call. The poll thread records the gen when `running=True`; on transition to `False`, equal gen = natural end (advance queue), different gen = stop was called (skip). Race-safe because even a post-`stop()` check will see the changed gen.

**Async image-worker guard** — every `@work` art fetcher does `if img is not None and self.is_mounted:` then `try: self.query_one(TGPImage).image = img / except NoMatches: pass`. Workers resume after an `await` that may outlive the widget (teardown, sort/reload remount); without the guard Textual raises `NoMatches`. Must replicate in any new art-fetch worker.

**`_render_version` counter** — each view holds `_render_version: int`, incremented before starting a `@work` load. The worker captures the version at start and discards results if it changed by the time it returns — prevents stale workers from overwriting newer results.

**Filter by hide/show, not remount (image grids)** — `AlbumGrid/ArtistGrid.filter_cards(ids)` toggles `card.display` on already-mounted cards so Kitty images are never re-transmitted. `_visible_cards` skips hidden cards during cursor navigation. Remount is reserved for data load and sort only.

**`Content.assemble` for safe label text** — track rows use `Content.assemble(text, ("  ♥", "$accent"))` rather than Rich markup strings. Titles containing `[`/`]` corrupt markup-parsed labels; `Content` parts are literal, so they can't crash the render.

**Favourites single source of truth** — `QobitApp.ensure_favorite_tracks()` fetches exactly once (double-checked `_fav_lock`). `ensure_favorite_ids()` derives the id set from it. `toggle_favorite` updates both caches and surgically patches `TracksView` so no tab ever re-fetches.

**TransportBar / NowPlayingHero self-wiring** — `on_mount` calls `self.watch(app, "reactive", callback, init=True)` for each `QobitApp` reactive. Any instance placed anywhere in the widget tree is automatically live with no `sync_*` calls from screens. Textual allows one label per border edge (`border_title` top-left, `border_subtitle` bottom-right).

**Container-owns-cursor (grids)** — `AlbumGrid`/`ArtistGrid` hold focus; the container tracks `_cursor: int` and toggles `-selected` on child cards. `ScrollableContainer` has `can_focus=True` so Tab targets the container, not its children — children can't hold focus themselves.

**Custom back navigation** — views implement `action_navigate_back() -> bool` (Escape, `priority=True`). Return `True` if the event was consumed (panel pop, filter teardown), `False` to let `QobitApp.action_focus_tabs` fall through. The app checks `hasattr(active_view, "action_navigate_back")` before dispatching.

**Track context menu resolves via focus** — `i` is a single app-level binding
(`QobitApp.action_track_menu`). It reads `self.focused`; if it's a `ListView`
whose `highlighted_child` exposes a `.track`, it pushes `TrackContextMenu`. This
covers every track list (search, tracks, queue, album/artist/playlist panels)
with no per-widget binding — every track row already exposes `.track`. The modal
is decoupled: it `dismiss()`es the chosen action id and `_on_track_menu`
dispatches (queue_next/queue_last/remove_from_queue/_start_radio/_open_artist/
_open_album). "Remove from queue" is offered only when the highlighted row is a
`QueueTrackRow`, and removes by identity so the exact queued instance goes. "Go to
album" builds a lean `Album` from the track (panel re-fetches the rest); "Go to
artist" needs a `get_track` round-trip since `Track` carries no `artist_id`.

**Queue version pattern** — `queue_version: reactive[int]` on `QobitApp` is bumped whenever `_play_queue` or `_history` changes. Widgets watch this to re-render. Combined with a local `_render_version` they guard against stale workers.

**`_flash_status` vs `status_msg`** — `status_msg` drives the transport bar's main `artist — title` label; setting it for transient notices would erase now-playing info until the next track. `_flash_status` sets it temporarily and auto-clears (~3 s).

**Session restore order** — `QobuzClient.restore_session()` is called in `QobitApp.__init__()`, not `on_mount`, so credentials are available before any child widget worker fires.

**Linux MPRIS2 signal attribute** — pydbus `bus.publish()` raises `AttributeError` for any `<signal>` in the introspection XML that lacks a matching `signal()` attribute on the object. The backend must declare `signal()` for every signal (`Seeked`, `PropertiesChanged`); a missing one silently kills the whole backend since `MediaKeys.__init__` swallows the error.

**Bit-perfect flags** — macOS: `--af-clr`, `--audio-pitch-correction=no`, `--audio-exclusive=yes`, `--load-scripts=no` (prevents system `mpv-mpris` from publishing a second tagless MPRIS player). Linux: no `--audio-exclusive`; bit-perfect is device selection — PipeWire rate-following (recommended, requires `default.clock.allowed-rates` drop-in) or exclusive `hw:` ALSA. **Don't add mpv `set volume` IPC without a non-bit-perfect mode gate** — software gain corrupts the signal on `hw:` output.

## Qobuz API layer

`client.py` reverse-engineers `app_id` and signing secrets from the web player bundle on login; cached in config. Streaming URLs expire ~10 min — re-fetch on track start. Quality fallback: FLAC_24_192 → FLAC_24_96 → FLAC_CD.

`get_artist_detail` (`artist/get`) and `get_artist_top_tracks` (`artist/page`) fire concurrently. `Track.from_api` handles both `performer.name` (string) and `artist.name.display` (nested dict) formats. The legacy `get_artist_page` still exists but is unused.

`get_all_favorite_tracks/albums/artists()` issue parallel 50-item page requests after the first. `Track`, `Album`, `Artist` carry `favorited_at: int | None` for Date Added sorting.

`Album.from_api` backfills its own `image_url` onto nested tracks whose `image_url` is unset — without this, cover art is missing in the transport bar and OS media controls when playing tracks from an album payload.

**Song radio** (`get_dynamic_suggestions`): `POST dynamic/suggest`, JSON body sent as `text/plain;charset=UTF-8` (same quirk as OAuth `user/login`). Body fields: `limit`, `listened_tracks_ids` (int ids), `track_to_analysed` (`[{track_id, artist_id, label_id, genre_id}]`). Response: `{"tracks": {"items": [...]}}`. Reverse-engineered from HAR; not in official docs.

## Image protocol

Kitty graphics protocol only (no iTerm2 IIP, no sixel). `textual_image.widget.TGPImage`. All fetches via `ui/_images.fetch_image(url)`: semaphore-limited (6 concurrent), in-memory cached, normalised to `_MAX_EDGE=600` JPEG on first fetch, on-disk cached at `~/.cache/qobit/images/`.

## Config paths

```
~/.config/qobit/config.json            credentials, audio_device, theme, radio_mode, oauth session
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

- `pierdom/picast` — audio player TUI template (Raw Rich, not Textual — useful for transport bar patterns)
- `pierdom/tuidash` — Textual patterns: `@work(thread=True)`, `reactive` + `watch_*`, `DEFAULT_CSS`, `ContentSwitcher`
- `vicrodh/qbz` — bit-perfect audio reference (Rust; ALSA/CoreAudio strategy maps directly to mpv flags)
- `pierdom/qobuz-mcp` — original source of `client.py`

## Phases

1–9 complete. Remaining:

10. UI overhaul: Search, AlbumScreen, PlaylistScreen, PlaylistsView
11. Context menus + TrackScreen detail overlay
12. Gapless playback, ReplayGain, CMAF fallback

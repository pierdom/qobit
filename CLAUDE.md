# qobit: developer guide

## Non-negotiable invariant

The DAC receives exactly the bits from the FLAC file: no resampling, no DSP.
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
├── connect/                 Qobuz Connect renderer — UI-agnostic (no Textual):
│   ├── __init__.py          device_info(): stable per-host uuid, COMPUTER,
│   │                        VOLUME_REMOTE_CONTROL_NOT_ALLOWED (bit-perfect)
│   ├── proto/               protoc output (committed; ruff-excluded) + .proto
│   ├── wire.py              varint framing, handshake, Payload/Batch (pure)
│   ├── session.py           ConnectSession: token mint per socket, join,
│   │                        registration, keepalive, reconnect backoff
│   ├── queue.py             ServerQueue: read-only mirror of the server queue
│   │                        (LOADED fast path + QUEUE_STATE snapshots)
│   └── renderer.py          ConnectRenderer: playback state machine over a
│                            PlayerPort; reports RNDR_SRVR_STATE_UPDATED
├── store.py                 JSON persistence for runtime state (DATA_DIR);
│                            holds player_state.json (last track + position)
│                            and queue_state.json (timeline + cursor)
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
    ├── play_context.py      Who may write _timeline/_cursor: LocalPlayContext
    │                        (pre-Connect logic, moved verbatim) and
    │                        ConnectPlayContext (Connect-active: mirror +
    │                        PlayerPort adapter; edits → server commands)
    ├── themes.py            qobit's themes (cyberdeck, ayu-dark/-mirage/-light),
    │                        all registered in QobitApp.__init__; colours only —
    │                        the UI has no hard-coded colours, $accent is ours
    ├── _images.py           Shared image fetch layer: asyncio.Semaphore(6) caps
    │                        concurrent HTTP fetches; module-level URL→PIL cache
    │                        prevents re-downloading the same image within a session
    ├── screens/
    │   ├── context_menu.py  TrackContextMenu — ModalScreen popup for one track
    │   │                    (Play next / Add to queue / Play radio / Go to
    │   │                    artist / Go to album; + Remove from queue when
    │   │                    in_queue=True); opened app-wide with `i`.
    │   │                    GoToMenu — artist/album of the playing track (`g`)
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
    │   ├── mixes.py         MixesView — DailyQ/WeeklyQ: two MixCards (d/w) +
    │   │                    a track list; generated via QobitApp.generate_mix()
    │   │                    (history + favourites seeds → get_dynamic_suggestions)
    │   │                    and cached a day/ISO-week at a time in
    │   │                    mixes_cache.json keyed by _cache_key(kind)
    │   └── queue.py         QueueView (the "Playback" tab, key 1) — responsive two-pane: left TrackListView
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

Phases 1–9 complete (CLI, TUI shell, library tabs, queue, OS media controls),
plus Qobuz Connect phases 1–2 (renderer, queue mirror, queue edits from qobit).
User docs: README (overview) → `docs/usage.md` (keys, tabs) and `docs/audio.md`
(bit-perfect setup); keep feature detail there, not in the README.

## Roadmap

- Context menus: `i` opens a per-track popup (done for track rows); still todo on album/artist cards
- AlbumScreen / PlaylistScreen / PlaylistsView redesign (art, rich header, queue actions, format badges)
- Search UI overhaul (art thumbnails, quality badges, better layout)
- TrackScreen detail overlay (composer, performer, related albums)
- Queue reorder (single-item remove + free skip back/forth via timeline+cursor done)
- Gapless playback, ReplayGain, CMAF fallback
- Qobuz Connect: **phases 1–2 done** (renderer; queue mirror; local queue edits as
  server commands). Next: phase 3, autoplay↔radio (incl. editing autoplay rows,
  endless-radio refill while active), shuffle/repeat UI, play reporting; phase 4
  qobit as controller + LAN discovery.
  Protocol: `docs/qobuz-connect.md`; design: `docs/qobuz-connect-design.md`; original
  throwaway spike: `spikes/qobuz_connect/` (standalone, not imported by `src/`)

## Known issues / in progress

**Grid card selection highlight on Ghostty: likely root cause found (2026-11-14), awaiting the
user's visual confirmation.** Three earlier rounds fixed the *card's* `.-selected` background,
which was correct but invisible: under `Screen.-transparent *` (ansi_default for every widget)
the card's own children (`.card-info` and its Labels) painted the terminal default over the
tint everywhere except the 1-col gap beside the image. Every earlier check inspected the card's
own computed colour, never what was drawn on top of it. Found by rendering screenshots
(`app.save_screenshot` → `rsvg-convert` → PNG) with the user's real config. Fix: one shared
`.grid-card` selection style in `QobitApp.CSS` (tint + accent text, children `background:
transparent` when selected; three-class selectors out-rank `Screen.-transparent *` in both modes)
and no outline (child Labels paint over the parent's outline cells, so it only showed on cards
with spare rows). **Lesson: verify UI by looking at a rendered screenshot, not computed styles.**

Secondary, smaller finding from the same investigation, not yet fixed: **screens pushed after
`action_toggle_background()` runs never receive the `-transparent` CSS class**: the toggle only loops
`self.screen_stack` at call time, so `TrackContextMenu`, `AlbumScreen`, `ArtistScreen`, `PlaylistScreen`,
etc. pushed *afterward* stay opaque regardless of the setting. Not reported by the user; found
incidentally while testing fix #2 above.

## Key non-obvious patterns

**Timeline + cursor (the play context)**: `QobitApp` holds one ordered
`_timeline: list[Track]` plus `_cursor: int` (index of the now-playing track,
-1 when nothing is loaded). There is **no** separate history/queue list: they're
read-only slice-properties (`_history == _timeline[:_cursor]`, `_play_queue ==
_timeline[_cursor+1:]`) kept for the Queue UI, which reads them unchanged.
Invariant: `now_playing == _timeline[_cursor]`, set in `_do_play` (which no
longer pushes history; the cursor *is* the history boundary). Transitions:
`_advance_queue` → `_cursor += 1` (radio refill extends the timeline at the end);
`action_previous` → `jump_to(_cursor-1)`; **selecting any Queue row →
`jump_to(index)`** which just moves the cursor, so skipping back/forth never
drops tracks (the old behaviour sliced `rows[idx+1:]` and silently discarded
everything before the picked row). `play_track(t, queue=…)` launches a *new*
context: it keeps the past (`_timeline[:_cursor+1]`), splices in `[t]+queue`, and
drops the old Up Next. That's deliberate: it's a fresh library selection, not a
skip. `_trim_history()` bounds the past to `_HISTORY_MAX`. Queue rows carry a
`timeline_index` tagged in `_refresh_list` (rendered order == timeline order),
which `_on_queue_selected` passes to `jump_to`. `jump_to` is `@work` because
sync action handlers call it.

**Play contexts (Qobuz Connect seam)**: every `_timeline`/`_cursor` *write*
goes through `QobitApp._ctx` (`ui/play_context.py`): `LocalPlayContext` normally,
`ConnectPlayContext` while qobit is the **active** Connect renderer (phase 2:
its mutators send `CTRL_SRVR_QUEUE_*` commands (load/insert/add/remove) and
return at once; the timeline changes only when the server echoes, through the
same resync path as an edit from the phone. Commands go out **one at a time**:
each carries the queue version it was based on, so the next waits for its
predecessor's echo (matched by `action_uuid`, 10 s timeout); an echo's version is
noted *before* the next is built, and the next is sent only *after* the echo
message is fully applied: a LOADED echo must update the queue model before an
edit queued behind it (menu "Play radio" = load + replace) picks its targets.
`queue_replace_upcoming` therefore resolves its item ids at send time. A
QUEUE_ERROR drops pending commands and re-syncs. Mirror rows are distinct
`Track` objects (`dataclasses.replace`) so identity-based remove works for a
song queued twice; autoplay rows are read-only for now). App methods
(`play_track`, `jump_to`, `queue_next/last`, `remove_from_queue`, `clear_queue/
history`, `_advance_queue`, `_start_radio`) are thin delegators returning
whether the edit happened; readers (Queue tab, hero, transport) are untouched.
Any new timeline mutator must be added to **both** contexts. Rule while active:
*server owns the queue, renderer owns playback*. Queue structure is a mirror
rebuilt only from server events (LOADED, or an ASK_FOR_QUEUE_STATE snapshot for
every other edit), while play/pause/seek/jump/natural-end act on mpv at once and
are reported. Transport actions (`action_pause`, `_seek_to`, seek keys) check
`self._ctx is not self._local_ctx` to route through the renderer. Protocol
facts that each cost a bug: `SET_STATE.current_track` reloads unless it names
the item already held *in the same queue major version* (item ids restart at 0
per queue, and the server's activation handover can re-send the current item
after a queue load already started it, and reloading then stopped playback);
picking a song in an app is *only* `QUEUE_TRACKS_LOADED`; the renderer starts
`tracks[queue_position]` itself. After each track starts, the renderer sends `RNDR_SRVR_FILE_AUDIO_QUALITY_CHANGED`
+ `DEVICE_AUDIO_QUALITY_CHANGED` (Hz, bit depth, level from format_id 5/6/7/27
→ MP3/CD/HIRES_L1/L2, qobuz-proxy's proven mapping) from `app._last_stream`;
identical file/device values since output is bit-perfect; without them the
apps show "unknown quality". `_play_stream()` returns "ok"/"sample"/"none"/"auth" and never skips;
skip policy belongs to the caller (`_do_play` locally, the renderer in Connect).

**`_stop_gen` for end-of-track**: `MpvPlayer._stop_gen` increments on every `stop()` call. The poll thread records the gen when running; on transition to not running, equal gen = natural end (advance queue), different gen = stop was called (skip). **Always read both via `MpvPlayer.snapshot()`**: `stop()` bumps the gen and drops `_proc` together under `_state_lock` *before* terminating mpv, so a snapshot is only ever (old gen, running) or (new gen, not running). Reading `running` and `_stop_gen` separately was a real race (stop landing between the reads looked like a natural end and skipped a track). It showed up once Qobuz Connect reloads made stop-then-play frequent.

**Async image-worker guard**: every `@work` art fetcher does `if img is not None and self.is_attached:` then `try: self.query_one(TGPImage).image = img / except NoMatches: pass`. Workers resume after an `await` that may outlive the widget (teardown, sort/reload remount). Use `is_attached` (still in the DOM), **not** `is_mounted`: Textual sets `is_mounted` only after the whole mount sequence, and a fetch started during mount that hits the image cache returns before that, so an `is_mounted` guard silently dropped the art. That's why a screen opened while a track was playing (e.g. the `g` album page) showed no transport-bar art until the next track. Must replicate in any new art-fetch worker.

**`_render_version` counter**: each view holds `_render_version: int`, incremented before starting a `@work` load. The worker captures the version at start and discards results if it changed by the time it returns, so stale workers can't overwrite newer results.

**Visual consistency rules** (aesthetic sweep, 2026-11-15; check every UI change
against a rendered screenshot):
- All borders and dividers are `round`; no `heavy`, `solid` or double lines (the
  queue's section dividers are drawn with `─`).
- qobit's own chrome uses `$accent`, including the active-tab underline, list
  highlights and the progress bar. Rich `Text` styles don't resolve `$variables`
  (`style="$accent"` silently renders the default colour): read
  `app.theme_variables["accent"]` instead, as `TransportBar` does.
- Never name a CSS class `primary`, `secondary`, `accent`, `success`, `warning`
  or `error` on a Label: Textual 8 ships those as Label variants with their own
  colour and background (all track titles turned blue that way). Track rows use
  `row-title` / `row-meta`.
- List highlight targets Textual 8's `-highlight` class (not the old
  `--highlight`): `$accent 35%` in a focused list, `12%` in an unfocused one,
  labels transparent. Grid cards use the same tint via `.grid-card`.
- Numbered lists (queue, album tracklist, top tracks) show the number only;
  unnumbered lists (Tracks, Search) show `♪`. Secondary lines use `  ·  `.
- The three library grids share `LIBRARY_TILE_MIN_W` (40), `grid-gutter: 1 2`
  and `grid-rows` = `GRID_CARD_IMG_H`.

**Grid tile size**: the Albums, Artists and Playlists tabs share one image
height, `artist_detail.GRID_CARD_IMG_H` (5 rows); the CSS heights carry a
`/* GRID_CARD_IMG_H */` marker. Each card's text column must fit in that many
rows (album: title 3 + artist + year; playlist: name 2 + owner + meta + date).
Playlist art uses `images300` (the API's `images` are 50 px thumbnails). Selection
is one shared style for all three: cards carry the `grid-card` class and labels
`card-primary` / `card-secondary`; the rules live once in `QobitApp.CSS`. Never
add per-card `.-selected` rules back.

**Filter by hide/show, not remount (image grids)**: `AlbumGrid/ArtistGrid.filter_cards(ids)` toggles `card.display` on already-mounted cards so Kitty images are never re-transmitted. `_visible_cards` skips hidden cards during cursor navigation. Remount is reserved for data load and sort only.

**`Content.assemble` for safe label text**: track rows use `Content.assemble(text, heart_suffix(favorite))` (`ui/_now_playing.py`) rather than Rich markup strings. Titles containing `[`/`]` corrupt markup-parsed labels; `Content` parts are literal, so they can't crash the render. `heart_suffix()` is the one place the `♥` tuple is built; every *TrackRow class across screens/ calls it instead of hand-rolling `(f"  {ICON_FAV}", "$accent")`.

**Favourites single source of truth**: `QobitApp.ensure_favorite_tracks()` fetches exactly once (double-checked `_fav_lock`). `ensure_favorite_ids()` derives the id set from it. `toggle_favorite` updates both caches and surgically patches `TracksView` so no tab ever re-fetches.

**TransportBar / NowPlayingHero self-wiring**: `on_mount` calls `self.watch(app, "reactive", callback, init=True)` for each `QobitApp` reactive. Any instance placed anywhere in the widget tree is automatically live with no `sync_*` calls from screens. Textual allows one label per border edge (`border_title` top-left, `border_subtitle` bottom-right).

**Container-owns-cursor (grids)**: `AlbumGrid`/`ArtistGrid` hold focus; the container tracks `_cursor: int` and toggles `-selected` on child cards. `ScrollableContainer` has `can_focus=True` so Tab targets the container; children can't hold focus themselves.

**Custom back navigation**: views implement `action_navigate_back() -> bool` (Escape, `priority=True`). Return `True` if the event was consumed (panel pop, filter teardown), `False` to let `QobitApp.action_focus_tabs` fall through. The app checks `hasattr(active_view, "action_navigate_back")` before dispatching.

**Go-to popup (`g`)**: `QobitApp.action_goto_menu` opens `GoToMenu` (a
`TrackContextMenu` subclass restricted to the artist/album options; `g` closes
it) for `now_playing`, or the paused track restored from the last session.
It dispatches through the same `_on_track_menu`, so navigation, breadcrumb and
Escape target behave exactly like the `i` menu's "Go to …".

**Track context menu resolves via focus**: `i` is a single app-level binding
(`QobitApp.action_track_menu`). It reads `self.focused`; if it's a `ListView`
whose `highlighted_child` exposes a `.track`, it pushes `TrackContextMenu`. This
covers every track list (search, tracks, queue, album/artist/playlist panels)
with no per-widget binding, since every track row already exposes `.track`. The modal
is decoupled: it `dismiss()`es the chosen action id and `_on_track_menu`
dispatches (queue_next/queue_last/remove_from_queue/_start_radio/_open_artist/
_open_album). "Remove from queue" is offered only when the highlighted row is a
`QueueTrackRow`, and removes by identity so the exact queued instance goes. "Go to
album" builds a lean `Album` from the track (panel re-fetches the rest); "Go to
artist" needs a `get_track` round-trip since `Track` carries no `artist_id`.
Both push their detail screen with the active tab from `_current_tab()` →
`(tab id, display name)`: the name is the breadcrumb (`source=`), the id is
`ArtistScreen`'s Escape target (`back_tab=`). Never derive a tab id from a
display name. Names aren't ids (the Playback tab's id is `queue`), and a bad
id crashes Escape with `No Tab with id …`.

**One Connect device per machine**: the device uuid is derived from the
hostname, so two qobits on one host *are the same device* to Qobuz: when either
leaves, the server drops the device for both while the survivor's socket stays
open (verified live: the device vanishes from every app, the "client
disappears" report). Hence `connect.acquire_instance_lock` (flock on
`DATA_DIR/connect.lock`, released by the OS on crash) in `_start_connect`: a
second instance doesn't join. And `ConnectSession` rejoins when it sees
`SRVR_CTRL_REMOVE_RENDERER` for its own id (rate-limited to one per 10 s, so two
clients can't flap). Test harnesses that build `QobitApp()` read the user's real
config. Pass `connect=False` unless Connect is under test, or they evict the
user's running qobit.

**Top bar**: `_TABS` (app.py) is the single source of truth: `(id, name)` in
left-to-right order. Labels (`[N] Name`) and the `1`–`7` bindings are generated
from it, so reordering is a one-line change. Below `_TABBAR_FULL_WIDTH` (90
cols, computed from the labels) `_fit_tab_labels` switches to compact `N Name`
so the last tab isn't clipped. The Playback tab keeps the internal id `queue`,
the view `QueueView` / `#view-queue`; it's renamed in the UI only.

**Queue version pattern**: `queue_version: reactive[int]` on `QobitApp` is bumped whenever `_timeline`/`_cursor` changes. Widgets watch this to re-render. Combined with a local `_render_version` they guard against stale workers.

**`_flash_status` vs `status_msg`**: `status_msg` drives the transport bar's main `artist — title` label; setting it for transient notices would erase now-playing info until the next track. `_flash_status` sets it temporarily and auto-clears (~3 s).

**Queue persistence**: the timeline + cursor survive restarts:
`watch_queue_version` debounces (1 s) a `_save_queue_state()` to
`queue_state.json` (and `on_unmount` saves once more); `_restore_player_state`
rebuilds `_timeline`/`_cursor` from it. Position stays in `player_state.json`
(rewritten every ~5 s while playing) so the queue file is only written when the
queue changes. If the two disagree (cursor track ≠ last-played track, e.g.
killed between saves) restore falls back to the single last-played track,
never a mismatched queue. Any new timeline mutator gets this for free as long
as it bumps `queue_version` (it must anyway). The app opens on the Playback tab.

**Session restore order**: `QobuzClient.restore_session()` is called in `QobitApp.__init__()`, not `on_mount`, so credentials are available before any child widget worker fires.

**Linux MPRIS2 signal attribute**: pydbus `bus.publish()` raises `AttributeError` for any `<signal>` in the introspection XML that lacks a matching `signal()` attribute on the object. The backend must declare `signal()` for every signal (`Seeked`, `PropertiesChanged`); a missing one silently kills the whole backend since `MediaKeys.__init__` swallows the error.

**Bit-perfect flags**: macOS: `--af-clr`, `--audio-pitch-correction=no`, `--audio-exclusive=yes`, `--load-scripts=no` (prevents system `mpv-mpris` from publishing a second tagless MPRIS player). Linux: no `--audio-exclusive`; bit-perfect comes from device selection: PipeWire rate-following (recommended, requires `default.clock.allowed-rates` drop-in) or exclusive `hw:` ALSA. **Don't add mpv `set volume` IPC without a non-bit-perfect mode gate**: software gain corrupts the signal on `hw:` output.

## Qobuz API layer

`client.py` reverse-engineers `app_id` and signing secrets from the web player bundle on login; cached in config. Streaming URLs expire after ~10 min; re-fetch on track start. Quality fallback: FLAC_24_192 → FLAC_24_96 → FLAC_CD, walked by `QobitApp._resolve_stream`. **Previews:** for tracks the account can't stream, `getFileUrl` returns `"sample": true` (30 s MP3, `TrackRestrictedByPurchaseCredentials`) at *every* quality, so a fallback never escapes it. `StreamUrl.sample` flags it, `_resolve_stream` stops at the first one (never cached), and `_do_play` refuses to play it: it flashes and skips forward (or stops if nothing is queued). Never play a sample: it's lossy and breaks the bit-perfect guarantee.

`get_artist_detail` (`artist/get`) and `get_artist_top_tracks` (`artist/page`) fire concurrently. `Track.from_api` handles both `performer.name` (string) and `artist.name.display` (nested dict) formats. `get_artist` is a third, lighter `artist/get` call (`albums_limit` instead of `limit`, no cache) used only by `_fetch_now_playing_bio` to pull a radio-seed track's artist bio.

`get_all_favorite_tracks/albums/artists()` issue parallel 50-item page requests after the first. `Track`, `Album`, `Artist` carry `favorited_at: int | None` for Date Added sorting.

`Album.from_api` backfills its own `image_url` onto nested tracks whose `image_url` is unset. Without this, cover art is missing in the transport bar and OS media controls when playing tracks from an album payload.

**Song radio** (`get_dynamic_suggestions`): `POST dynamic/suggest`, JSON body sent as `text/plain;charset=UTF-8` (same quirk as OAuth `user/login`). Body fields: `limit`, `listened_tracks_ids` (int ids), `track_to_analysed` (`[{track_id, artist_id, label_id, genre_id}]`). Response: `{"tracks": {"items": [...]}}`. Reverse-engineered from HAR; not in official docs.

`generate_mix()` (`QobitApp`) is the same `dynamic/suggest` call reused for DailyQ/WeeklyQ: seeds up to 50 tracks from recent history + favourites, enriches a spread of up to 12 with `track_to_analysed` metadata, and `mixes.py` caches the result in `mixes_cache.json` keyed by day (`daily`) or ISO year-week (`weekly`) so reopening the tab within the same period doesn't regenerate.

## Image protocol

Kitty graphics protocol only (no iTerm2 IIP, no sixel). `textual_image.widget.TGPImage`. All fetches via `ui/_images.fetch_image(url)`: semaphore-limited (6 concurrent), in-memory cached, normalised to `_MAX_EDGE=600` JPEG on first fetch, on-disk cached at `~/.cache/qobit/images/`.

## Config paths

```
~/.config/qobit/config.json            credentials, audio_device, theme, radio_mode, connect_enabled, oauth session
~/.local/share/qobit/mpv.sock          IPC socket (re-created on each play call)
~/.local/share/qobit/player_state.json last track + position (restored on launch)
~/.local/share/qobit/queue_state.json  timeline + cursor (restored on launch)
~/.local/share/qobit/mixes_cache.json  DailyQ/WeeklyQ tracks, keyed by day/ISO-week
~/.cache/qobit/images/                 on-disk cover-art cache (qobit clear-cache)
```

## Development

```bash
uv sync --group dev
uv run qobit auth          # authenticate once
uv run qobit devices       # check audio device list
uv run qobit play "test"   # smoke test CLI playback
uv run qobit               # launch TUI
uv run ruff check .        # rules in pyproject.toml; spikes/ and proto/ excluded
uv run ruff format .
uv run pytest              # unit tests — models, config/store, client auth,
                            # image cache, app timeline+cursor logic,
                            # Qobuz Connect wire/queue/renderer (fake player)
uv run textual console     # Textual dev console (separate terminal)
```

**Releasing**: semver tags `vX.Y.Z` with a GitHub release per tag (first
three cut 2026-11-14: v0.1.0 baseline, v0.1.1, v0.2.0). The version lives only in
`pyproject.toml` (`__version__` reads installed metadata). To release: bump it,
add a `CHANGELOG.md` entry, `uv lock`, commit `chore(release): X.Y.Z`, annotated
tag, push main + tag, `gh release create vX.Y.Z --notes-file <that entry>`.
Feature merges → minor bump; fix-only merges → patch bump.

Tests live in `tests/`; `conftest.py`'s autouse fixture redirects `config.py`/
`store.py`/`_images.py`'s real paths (under `~/.config` and `~/.local/share`)
to a tmp dir for every test. Never add a test that touches those modules'
module-level path constants without going through it, or a test run can read
or overwrite a developer's real saved session. Widget/screen rendering isn't
covered by this suite (no Kitty-protocol terminal in CI); that's exercised
manually via `textual console` / a tmux-driven smoke run instead.

## Reference repos

- `pierdom/picast`: audio player TUI template (raw Rich, not Textual; useful for transport bar patterns)
- `pierdom/tuidash`: Textual patterns: `@work(thread=True)`, `reactive` + `watch_*`, `DEFAULT_CSS`, `ContentSwitcher`
- `vicrodh/qbz`: bit-perfect audio reference (Rust; ALSA/CoreAudio strategy maps directly to mpv flags)
- `pierdom/qobuz-mcp`: original source of `client.py`

## Phases

1–9 complete, plus Qobuz Connect phases 1–2 (see Roadmap for Connect phases
3–4). Remaining:

10. UI overhaul: Search, AlbumScreen, PlaylistScreen, PlaylistsView
11. Context menus + TrackScreen detail overlay
12. Gapless playback, ReplayGain, CMAF fallback

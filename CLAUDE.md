# qobit: developer guide

## Non-negotiable invariant

The DAC receives exactly the bits from the FLAC file: no resampling, no DSP.
mpv plays; device routing keeps the system mixer away from the signal. Never
add software gain (mpv `set volume`) or DSP unless it's opt-in and off by
default: on `hw:` output it corrupts the samples.

Python 3.11+, Textual 8 for the UI, mpv for audio, packaged with uv. User docs:
README (overview), `docs/usage.md` (keys, tabs), `docs/audio.md` (bit-perfect
setup); keep feature detail there, not in the README.

## Module layout

```
src/qobit/
├── __main__.py          CLI + TUI launcher (qobit with no args)
├── config.py            credentials + device config (env → file → prompt)
├── store.py             JSON runtime state in DATA_DIR (player/queue state)
├── qobuz/client.py      QobuzClient: app_id/secrets from the web bundle, OAuth,
│                        every API call (see "Qobuz API")
├── qobuz/models.py      Track, Album, Artist, Playlist, StreamUrl
├── audio/device.py      mpv devices + synthesized alsa/hw:CARD=…,DEV=… entries,
│                        bit-perfect ones flagged and sorted first
├── audio/player.py      mpv subprocess + IPC, bit-perfect flags, _stop_gen
├── audio/verify.py      bit-perfect check: mpv IPC + (Linux) ALSA hw_params
├── audio/media_keys.py  macOS Now Playing / Linux MPRIS2 (pydbus); degrades
│                        silently without the optional deps
├── connect/             Qobuz Connect renderer, UI-agnostic (no Textual):
│                        wire.py framing, session.py join/keepalive/reconnect,
│                        queue.py server-queue mirror, renderer.py state machine
│                        over a PlayerPort; proto/ is generated (ruff-excluded)
└── ui/
    ├── app.py           QobitApp: reactive state, transport, tabs, restore
    ├── nav.py           TABS, breadcrumb trail (Breadcrumb), a page's tab bar
    ├── play_context.py  LocalPlayContext / ConnectPlayContext: the only
    │                    writers of _timeline/_cursor
    ├── themes.py        cyberdeck + ayu-dark/-mirage/-light; qobit-* variables
    ├── _images.py       image fetch: 6 concurrent, memory + disk cache
    ├── _now_playing.py  row/view mixins for the ▶/⏸ marker, heart_suffix()
    ├── widgets/lists.py PagedListView (page keys move the cursor; lazy rows)
    ├── widgets/transport.py  TransportBar (art, title, progress; click to
    │                    pause/seek)
    └── screens/
        queue.py         Playback tab (id `queue`): timeline list + NowPlayingHero
                         (hidden below 100 cols); c clears queue, X history
        tracks.py        favourite tracks: sort, live filter (/)
        artists.py       favourite artists grid + inline artist and album detail
        albums.py        favourite albums grid + inline album detail
        playlists.py     user playlists grid
        mixes.py         DailyQ/WeeklyQ cards (d/w or Tab/←→/Enter) + tracks
        search.py        SearchView + TrackItem/AlbumItem/ArtistItem/PlaylistItem
        album_detail.py  AlbumDetailPanel (shared) + AlbumScreen
        artist_detail.py ArtistHeader, ArtistTracks (Top Tracks + the
                         user's favourites by the artist), AlbumCard/AlbumGrid,
                         ArtistCard/ArtistGrid, _GridNav (all shared), ArtistScreen
        playlist_detail.py PlaylistScreen
        context_menu.py  ContextMenu (`i`), GoToMenu (`g`)
```

## Playback and queue

**Timeline + cursor.** `QobitApp` holds one `_timeline: list[Track]` and
`_cursor` (index of the now-playing track, -1 if none). History and Up Next are
read-only slices (`_history`, `_play_queue`). Invariant: `now_playing ==
_timeline[_cursor]`. `_advance_queue` moves the cursor (radio refill extends
the end); `action_previous` and selecting any Playback row call
`jump_to(index)`, which only moves the cursor, so skipping never drops tracks.
`play_track(t, queue=…)` keeps the past, splices in `[t]+queue` and drops the
old Up Next (a new selection, not a skip). `_trim_history()` bounds the past.
Queue rows are `TimelineRow`s carrying `role` (history/now/next) and
`timeline_index`; `QueueView._reconcile` updates the list in place (rows
matched by track id, reconfigured, moved; only new tracks mounted, leftovers
removed last; one reconcile at a time under a lock). Never clear and remount
it: that flickered and lost the scroll position. `tests/test_queue_reconcile.py`
checks it against the timeline. `jump_to`/`play_track` are `@work`.

**`queue_version`** is bumped on every timeline/cursor change; widgets watch it.
Any new timeline mutator must bump it and exist in **both** play contexts.

**Persistence.** `watch_queue_version` debounces (1 s) a save to
`queue_state.json`; position lives in `player_state.json` (every ~5 s while
playing). If the two disagree, restore falls back to the last-played track
alone. The restored track shows paused, with art and album details fetched at
startup. The app opens on the Playback tab.

**Play contexts (Qobuz Connect).** Timeline writes go through `QobitApp._ctx`:
`LocalPlayContext`, or `ConnectPlayContext` while qobit is the active Connect
renderer. Rule while active: the server owns the queue, the renderer owns
playback. Connect mutators send `CTRL_SRVR_QUEUE_*` commands and return; the
timeline changes only on the server's echo. Commands go one at a time (each
carries the queue version; the next waits for its predecessor's echo, matched
by `action_uuid`, 10 s timeout, and is built only after the echo is applied).
QUEUE_ERROR drops pending commands and resyncs. Mirror rows are distinct
`Track` objects so remove-by-identity works; autoplay rows are read-only.
Transport actions check `self._ctx is not self._local_ctx` to go through the
renderer. Protocol facts that each cost a bug:
- `SET_STATE.current_track` reloads unless it names the item already held in
  the same queue major version (item ids restart at 0 per queue).
- Picking a song in an app is only `QUEUE_TRACKS_LOADED`; the renderer starts
  `tracks[queue_position]` itself.
- After each track starts, send `RNDR_SRVR_FILE_AUDIO_QUALITY_CHANGED` and
  `DEVICE_AUDIO_QUALITY_CHANGED` (format_id 5/6/7/27 → MP3/CD/HIRES_L1/L2,
  same values for both), or the apps show "unknown quality".

Protocol: `docs/qobuz-connect.md`; design: `docs/qobuz-connect-design.md`;
`spikes/` is the original throwaway experiment (not imported, not linted).

**One Connect device per machine.** The device uuid comes from the hostname,
so two qobits on one host are one device; when either leaves, Qobuz drops both.
`connect.acquire_instance_lock` (flock on `DATA_DIR/connect.lock`) stops a
second instance joining, and `ConnectSession` rejoins on
`SRVR_CTRL_REMOVE_RENDERER` for its own id (at most once per 10 s).

**`_play_stream()`** returns "ok", "sample", "none", "auth" or "stale" and never
skips; skip policy is the caller's (`_do_play` locally, the renderer in
Connect). It bumps `_play_gen`; a call overtaken while resolving its URL returns
"stale" without touching mpv (picking A then B fast could play A under B's
cursor). Connect treats "stale" as handled, not unplayable.

**End of track (`_stop_gen`).** `stop()` bumps `_stop_gen` and drops `_proc`
together under `_state_lock`. The poll loop reads both via
`MpvPlayer.snapshot()`: same gen + not running = natural end (advance), new gen
= stopped. Reading them separately raced and skipped tracks.

**Bit-perfect flags.** macOS: `--af-clr`, `--audio-pitch-correction=no`,
`--audio-exclusive=yes`, `--load-scripts=no` (else the system mpv-mpris
publishes a second player). Linux: no `--audio-exclusive`; bit-perfect comes
from the device: PipeWire rate-following (needs the
`default.clock.allowed-rates` drop-in in `contrib/pipewire`) or exclusive
`hw:` ALSA.

## UI patterns

**Workers.**
- Guard results after an `await` with `self.is_attached`, never `is_mounted`:
  Textual sets `is_mounted` only after the whole mount sequence, so a fetch
  that hits a cache finishes first and the result is dropped (go-to pages lost
  their artist header that way, only in a real Kitty terminal). Art fetchers
  also wrap `query_one(TGPImage)` in `suppress(NoMatches)`.
- A widget refilled with different content (transport/hero art, inline album
  panel, artist header, an artist's top tracks, media-key art) runs its fetch
  as `@work(exclusive=True, group="<method name>")` so a newer request cancels
  the stale one. One group per method.
- Views bump `_render_version` before a load; the worker discards its result if
  it changed.

**Lazy mounting, no caps.** Mounting slows as a container grows (rows ~4.5 s
per 2000, cards ~6 s per 1000). `PagedListView.set_rows` (200 rows) and
`_GridNav.set_cards` (60 cards) mount a first page and the rest as the cursor
or scroll nears the end; End mounts everything. Whole-list code reads
`PagedListView.rows` (mounted + backlog). A hard 200 cap once hid most of a
library: don't add one back.

**Grids.** The container holds focus and `_cursor`, toggling `-selected` on
cards; `selected_card()` returns the current one. Filtering hides/shows cards
(`filter_cards`) instead of remounting, so Kitty images aren't re-sent; it
mounts the lazy backlog first. Albums/Artists/Playlists share
`GRID_CARD_IMG_H` (5 rows, CSS marked `/* GRID_CARD_IMG_H */`),
`LIBRARY_TILE_MIN_W` (32) and `grid-gutter: 1 2`; each card's text must fit in
5 rows. Playlist art uses `images300` (`images` are 50 px).

**Tabs.** `nav.TABS` is the single source of truth `(id, name)`; labels
(`[N] Name`, compact `N Name` below 90 cols) and the `1`–`7` keys derive from
it. The Playback tab's id is `queue`. On activation the app calls the view's
`activate()` (first load + focus); don't rely on `on_show` (Textual stopped
sending Show after End on a long list). Views implement `action_navigate_back()
-> bool` for Escape (True = consumed) and `can_navigate_back()`: Escape has two
app bindings, a footer-visible `back` that `App.check_action` enables only when
something can go back, then the hidden `focus_tabs` it falls through to (a
disabled action passes the key to the next binding; `-`/`=` use the same trick
for the software-volume hint). Views hide keys that don't apply with
`check_action` returning False (Sort/Filter while a detail view is open) and
call `refresh_bindings()` when that state changes without a focus change.

**The `i` menu** is one app binding (`action_track_menu`, a worker). From the
focused widget it collects a track (a list row's `.track`), an album (a grid's
selected card, a Search `AlbumItem`, or the enclosing
`AlbumDetailPanel.album`) and an artist (a card/`ArtistItem`, or an ancestor's
`page_artist`: `ArtistScreen`, `ArtistsView` with an artist open). Any mix
works; nothing to act on, no menu. `ContextMenu` dismisses with an action id
and `_on_track_menu` dispatches. "Remove from queue" appears only on Up Next
rows (removes by identity). **`g`** opens `GoToMenu` for the playing (or
restored) track through the same dispatch. "Go to artist" needs `get_track`
(Track has no artist id); "Go to album" passes a lean `Album`.

**Artist tracks.** `ArtistTracks` is shared by ArtistScreen and the Artists
tab: Top Tracks plus "Your favourites" (favourite tracks where
`Track.by_artist(id)` matches the performer or the album artist; they differ
on compilations and soundtracks). Two columns from
`ARTIST_TRACKS_COLUMNS_MIN_W` (120) of panel width, else one panel with `t`
switching; no favourites, no second list. It refreshes on `queue_version`
(bumped by every favourite toggle). Border titles take style markup
(`[underline]`, `[not bold]`, `[dim]`) but don't resolve `$variables`.

**Similar artists.** `ArtistReleases` wraps the album grid (keeping the
host's id) and an `ArtistGrid` of similar artists behind `t` (the arrows move
the grid cursor, so no arrow switch here). The full list
(`artist/getSimilarArtists`, paged) loads on first switch; whether to offer it
comes from the preview in `artist/page`. `ArtistHeader(similar=True)` lists
that preview beside the bio from `ARTIST_HEADER_SIMILAR_MIN_W` (128, compared
with the terminal width). Both switching panels derive from `_TitleSwitch`
(click on a name in the title → `action_switch`) and build the title with
`switch_title()`: the shown view underlined like the active top-bar tab, the
other at normal weight (`dim` made it unreadable on an unfocused panel).
`clicked_label()` maps a click to a name (the title starts 3 cells in).
`artist/page` is cached per artist and shared in flight
(`client.get_artist_page`). Opening an artist from any of these goes through
`QobitApp.open_artist`. Don't name widget methods or attributes after Textual
internals: an `ArtistCard._name()` helper shadowed `Widget._name` and crashed
every tile.

**Navigation: pages + trail** (`nav.py`). Tabs open details inline (Albums,
Artists); Search, `g`, `i` and Similar push pages (AlbumScreen, ArtistScreen,
PlaylistScreen). Every level reports `nav_levels()` (its own labels, outermost
first; a tab view only those below the tab) and `nav_to(depth)`;
`QobitApp.nav_trail()` joins tab name + view levels + each page's levels, and
`Breadcrumb` renders it (clickable crumbs; hidden at a tab's top level). Call
`app.nav_changed()` whenever a level opens, closes or learns its title. Pages
compose `page_header(app)` (a `PageTabs` bar with the origin tab lit, then the
trail); its tab ids are prefixed `page-tab-` (a page's own `#albums` grid took
the Albums tab's style). `1`–`7` and tab clicks call `action_switch_tab`, which
closes pages first; Escape (`action_back`) goes up one level. The app's own
`screen`-relative queries can hit a page: read the main screen through
`screen_stack[0]` (`base_tab()`, `_base_view()`). Never derive a tab id from a
display name.

**Favourites.** `ensure_favorite_tracks/albums/artists()` fetch once each (one
lock per list, loaded in parallel at startup); a failed load isn't cached.
`toggle_favorite`, `toggle_favorite_album` and `toggle_follow_artist` update
the caches and patch the Tracks/Albums/Artists tabs, so nothing re-fetches.

**Self-wiring widgets.** TransportBar and NowPlayingHero `watch()` the app's
reactives on mount, so any instance anywhere is live. Now-playing art shows
whenever a track is loaded (`-has-track`), not only while playing.

**Identity.**
- Colour roles: `$accent` = chrome (borders, focus, now playing);
  `qobit-select` = selected row/card background; `qobit-hires` and
  `qobit-heart` = signal; `qobit-readout` = the transport's status tag and
  time (LED readout); `qobit-bar-start`/`-end` = the progress gradient. They
  live in `Theme.variables`; other themes get
  `QobitApp.get_theme_variable_defaults()` (accent everywhere, selection
  accent 35%, HI-RES = `$warning`: built-ins' `$secondary` can be unreadable).
  So `$qobit-*` is safe in CSS and Content styles; Rich Text and border titles
  need `theme_color()`. A test app that borrows `QobitApp.CSS` must also
  borrow `get_theme_variable_defaults`, or the stylesheet won't parse.
- Textual ignores a theme's `boost=` when it also sets `panel` (`$boost` came
  out transparent): set it in `variables={"boost": …}`. Cyberdeck also sets
  `text-muted` (cyan-tinted, 10.7:1) and `footer-key-foreground` (amber).
  Check a palette change with contrast numbers on the pairs actually drawn
  (text on the selection tint too), not by eye alone.
- The lamp: `_play_stream` starts `_verify_signal` → `audio.verify.check_signal`
  → `app.signal`; `widgets.transport.signal_parts/signal_readout` render it for
  the transport and the Now Playing panel. Software volume below 100% overrides
  it (mpv can't see DSP).
- The playing row's bar meter: `_now_playing.playing_icon()`; rows that show it
  register with `track_live()`, and the app's beat timer calls `tick()`.
- Cover art is `widgets.art.Art` (a TGPImage with an initials placeholder): use
  it for any new image and set `.placeholder` when the subject changes.
- Panel titles go through `_utils.readout()` (uppercase, same length).
- `nav.Wordmark` sits on the Screen's `overlay` layer, docked right with height
  1, so it shares the tab bar's row; it hides below `Wordmark.MIN_WIDTH`.

**Status line.** `status_msg` is the transport's `artist — title` line; use
`_flash_status()` for transient notices (auto-clears after ~3 s).

**Labels.** Track rows build text with `Content.assemble(...)`, not markup
(titles with `[`/`]` break markup); any Label fed API text (album, artist,
breadcrumb, bio) is `markup=False`. `heart_suffix()` is the only place the ♥
part is built. Row titles go through `playing_content(lead, text, …, width=)`:
a fixed gutter (`GUTTER_ICON`/`GUTTER_NUM`/`GUTTER_NUM_LONG`) where ▶/⏸
replaces the lead, and `meta_indent(width)` for the second line, one width per
list (the Playback timeline uses `GUTTER_NUM` for all its rows). `.row-title`
and `.row-meta` never wrap (ellipsis, in `QobitApp.CSS`). Empty and failed
lists/grids use `_utils.status_line()`; a loading tab sets its border subtitle
to `loading…`. Resolution is `models.quality_text()` (`24-BIT · 96 kHz`).

**Cursor follows playback** only if it was on the playing row (or nowhere):
`NowPlayingViewMixin` and the Playback list's rebuild keep a cursor the user
moved.

**Visual rules** (check every UI change on a rendered screenshot, never on
computed styles: they misled three times):
- Borders and dividers are `round`; no heavy, solid or double lines.
- The app inset is `Screen { padding: 0 1 }`; `ModalScreen { padding: 0 }`
  undoes it, or Textual draws the page behind a modal one column right.
- Never tint behind a modal (`ModalScreen { background: transparent }`): Kitty
  images are placeholder cells whose text colour encodes the image id, and a
  tint changes it, so every image behind the menu vanished.
- qobit's chrome uses `$accent`. Rich `Text` doesn't resolve `$variables`: read
  `app.theme_variables["accent"]`.
- Backgrounds: page sections paint none (no bands, no panel fills, no
  footer bar, no Input fill, no scrollbar track): panels are set apart by
  their round borders, so the theme background (or the terminal's, with it
  off) shows through and both modes look alike. The only fills: state (the
  selection `$qobit-select`, the now-playing row, a focused mix card) and
  floating layers (the `i`/`g` menu, the command palette). A new fill needs a
  matching `Screen.-transparent` rule in `QobitApp.CSS` (and its Labels
  `transparent`), or it shows in one mode only. Check with a background audit
  of rendered strips in both modes, not by reading CSS.
- Lists are `background: transparent` (Textual's `$surface` boxes them).
- Selection colours are measured per theme, never assumed:
  `themes.qobit_variable_defaults` computes `qobit-select` / `-select-text`
  (first of accent, foreground, black/white at 4.5:1 on the selection),
  `-select-dim` (unfocused, 10%), `-now` (the playing row's tint) and
  `-accent-text` (accent moved just enough to read on the page and the
  tint). ANSI themes can't blend: they get Textual's block-cursor pair and
  no tints. All text on a selection takes `$qobit-select-text`: a
  highlighted row's title drops its span colours (`plain_if_highlighted`,
  rows' `watch_highlighted`), selected cards too (`select_card()` →
  `refresh_selected()`); no `dim` style on card text (CSS gives the muted
  colour). The list highlight targets Textual 8's `-highlight` class. Grid
  cards share `.grid-card` in `QobitApp.CSS`; never add per-card
  `.-selected` rules. `tests/test_themes.py` holds every theme, Textual's
  included, to 4.5:1.
- Never give a Label the classes `primary`, `secondary`, `accent`, `success`,
  `warning` or `error` (Textual 8 Label variants). Rows use `row-title` /
  `row-meta`.
- Numbered lists show the number only; unnumbered ones show `♪`. Secondary
  lines use `  ·  `.

**MPRIS2.** pydbus `publish()` fails unless every `<signal>` in the XML has a
matching `signal()` attribute (`Seeked`, `PropertiesChanged`), and
`MediaKeys.__init__` swallows the error.

## Qobuz API

- `client.py` extracts `app_id` and signing secrets from the web player bundle
  at login (cached in config). `restore_session()` runs in `QobitApp.__init__`
  so credentials exist before any worker.
- Every HTTP call goes through `QobuzClient._send`, which turns network errors
  into `QobuzError`. A `@work` that raises closes the app, so never call
  `self._http` directly.
- `_get_paged(endpoint, key, page, …)` fetches every page of an embedded list
  (4 requests at a time). Endpoints cap silently (`artist/get` returned 100 of
  291 albums; `playlist/get` stops at 500): use it for any long list.
- Stream URLs expire after ~10 min; re-fetch on track start. Quality fallback
  FLAC_24_192 → 24_96 → CD (`_resolve_stream`). Tracks the account can't stream
  return `"sample": true` (30 s MP3) at every quality: `StreamUrl.sample` flags
  it and qobit never plays it (flash + skip).
- `Album.from_api` backfills its id, title and image onto nested tracks, which
  carry none (needed for art and "Go to album").
- About 1 in 10 artists has no photo (`image: null` everywhere, incl.
  `artist/page`). `Artist.from_api` falls back to the newest release's cover,
  picked by `released_at` (the API's album order and sort parameter are
  unreliable); Artists-tab cards fetch the full record lazily for it.
- The API sends `null` for missing objects: parse with `data.get(k) or {}`,
  never `data.get(k, {})`.
- `Track.from_api` accepts both `performer.name` and `artist.name.display`.
  `get_artist` (light, uncached) only feeds the hero's bio.
- `set_favorite(kind, id, on)` adds/removes a track, album or artist (follow =
  favourite artist).
- Song radio: `POST dynamic/suggest`, JSON sent as `text/plain;charset=UTF-8`,
  fields `limit`, `listened_tracks_ids`, `track_to_analysed` (`[{track_id,
  artist_id, label_id, genre_id}]`). `generate_mix()` reuses it for
  DailyQ/WeeklyQ, cached per day / ISO week in `mixes_cache.json`.

Images: Kitty graphics protocol only (`textual_image` `TGPImage`). All fetches
go through `ui/_images.fetch_image(url)`: 6 concurrent, normalised to 600 px
JPEG, cached in memory and in `~/.cache/qobit/images/`.

## Files

```
~/.config/qobit/config.json            credentials, device, theme, radio, Connect, session
~/.local/share/qobit/player_state.json last track + position
~/.local/share/qobit/queue_state.json  timeline + cursor
~/.local/share/qobit/mixes_cache.json  DailyQ / WeeklyQ
~/.local/share/qobit/mpv.sock          mpv IPC socket (fixed path)
~/.cache/qobit/images/                 cover-art cache (qobit clear-cache)
```

## Development

```bash
uv sync --group dev
uv run qobit auth / devices / play "test" / qobit
uv run ruff check . && uv run ruff format .   # rules in pyproject.toml
uv run pytest
```

- Tests: `conftest.py` redirects config/store/image paths to a tmp dir; never
  touch those modules' path constants outside it. UI rendering isn't in CI.
- Harnesses that build `QobitApp()` read the real config: pass `connect=False`
  (or they evict the user's running qobit), use a scratch `store.DATA_DIR`,
  never start mpv (the socket path is shared), stub `set_saved_theme`.
- Verify UI headless: `app.save_screenshot()` → `rsvg-convert` → PNG (Kitty
  images show as garbage there; real captures need a Kitty-protocol terminal).
- Releasing: version only in `pyproject.toml`. Bump, CHANGELOG entry, `uv lock`,
  commit `chore(release): X.Y.Z`, merge to main, annotated tag `vX.Y.Z`, push,
  `gh release create` with the entry. Features → minor, fixes only → patch.

## Roadmap

- `i` menu on playlists; Search, AlbumScreen, PlaylistScreen and PlaylistsView
  redesign (art, richer headers, quality badges); TrackScreen detail overlay.
- Queue reorder; gapless playback, ReplayGain, CMAF fallback.
- Qobuz Connect phase 3 (autoplay ↔ radio, shuffle/repeat UI, play reporting),
  phase 4 (qobit as controller, LAN discovery).

## References

- `vicrodh/qbz`: qobit's main source and inspiration, incl. the bit-perfect
  approach. Discontinued; its code is no longer available, so the approach
  lives in `audio/` and `docs/audio.md`.
- `pierdom/qobuz-mcp`: original source of `client.py`.
- `pierdom/tuidash`: Textual patterns (`@work`, reactives, `ContentSwitcher`).
- `pierdom/picast`: Rich audio-player TUI (transport bar patterns).

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
├── config.py            session + device/settings config (env → file)
├── token_store.py       the session token in the system keyring (else config.json)
├── _fs.py               private_dir (0700) / write_private (atomic, 0600)
├── store.py             JSON runtime state in DATA_DIR (player/queue state)
├── account_data.py      what an account leaves on disk; erase (logout), the
│                        running-TUI lock (qobit.lock)
├── library_cache.py     the library lists on disk (shown at start, refreshed)
├── qobuz/client.py      QobuzClient: app_id/secrets from the web bundle, OAuth,
│                        every API call (see "Qobuz API")
├── qobuz/oauth.py       login link + redirect server + pasted-address LoginPrompt
├── qobuz/models.py      Track, Album, Artist, Playlist, StreamUrl
├── audio/device.py      mpv devices + synthesized alsa/hw:CARD=…,DEV=… entries,
│                        bit-perfect ones flagged and sorted first
├── audio/player.py      mpv subprocess + IPC, bit-perfect flags, _stop_gen
├── audio/verify.py      bit-perfect check: mpv IPC + (Linux) ALSA hw_params
├── audio/media_keys.py  OS media keys: macOS Now Playing (pyobjc); degrades
│                        silently without the optional deps
├── audio/mpris.py       Linux MPRIS2 (dbus-fast) on its own loop + thread
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
    ├── _filter.py       TypeToFilter: `/` filter shared by Tracks/Albums/
    │                    Artists/Playlists (Enter keeps results, Esc clears)
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
        login.py         LoginScreen: login dialog over QobuzClient.login_oauth
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

**`_play_stream()`** returns "ok", "sample", "unavailable", "none", "auth" or
"stale" and never skips; skip policy is the caller's (`_do_play` locally, the
renderer in Connect). "unavailable": every quality answered with restriction
codes and no URL (`StreamUrl.from_api` raises `NoStreamError`; a bare
`data["url"]` once closed the app); skipped like "sample". "none" is request
failures and is never auto-skipped (a flaky network would race through the
queue). It bumps `_play_gen`; a call overtaken while resolving its URL returns
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
per 2000, cards ~6 s per 1000). `PagedListView.set_rows` (100 rows) and
`_GridNav.set_cards` (60 cards) mount a first page and the rest as the cursor
or scroll nears the end; End mounts everything. The Playback list mounts
through the playing row plus `_UP_NEXT_PAGE` (its reconcile puts the rest
in the `PagedListView` backlog), and draws before the favourites load
(hearts come with a second reconcile). Every mounted widget costs on each
cursor move and repaint, not just at mount: Textual maps them all. Whole-list code reads
`PagedListView.rows` (mounted + backlog). A hard 200 cap once hid most of a
library: don't add one back.

**Album panel height.** `AlbumDetailPanel` and the playlist page's
`PlaylistPanel` size themselves from their own height
(`album_detail.fit_detail_panel`, on resize and on load): the tracklist keeps
half the panel and at least `MIN_TRACK_ROWS`, the art gets the rest
(`ART_MIN`–`ART_MAX`), and below `ART_MIN` the art goes (`-no-art` on the
`.detail-header`; the album's tracklist goes two columns when wide,
`-two-col` on it). The classes sit on the parts because scoped
`DEFAULT_CSS` can't match a class on the host widget itself. The album panel posts
`Resized`; each host with an `ArtistHeader` above it (AlbumScreen, Albums,
Artists while an album is open) calls `fit_artist_header`, which decides
from the panel's height *with* the header so it can't oscillate. Never key
this on the terminal height: three hosts with different chrome broke that.
`tests/test_album_fit.py` checks all three at 24/32/45 rows, and the playlist page.

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
sending Show after End on a long list). A view focuses through
`_utils.focus_later(self, widget.focus)`, never a bare
`call_after_refresh(focus)`: a busy view's deferred focus ran after the
user had switched tabs and pulled the focus to its hidden list. Views implement `action_navigate_back()
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
works; nothing to act on, no menu. An album with no track (a grid card)
also gets "Go to artist" (`album_artist`, resolving a lean album's artist
id via `get_album`), unless an artist page is already the subject. Lists
filled by a view get a cursor (`_place_initial_cursor`: the playing row,
else the first): focused with no row, the menu found no track at all. `ContextMenu` dismisses with an action id
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

**Hearts on pages.** `AlbumDetailPanel` (after the title, `heart_suffix`) and
`ArtistHeader` (bio panel's border title; `theme_color(HEART)`, redrawn on a
theme change) show ♥ from `is_favorite_album` / `is_followed_artist` (cache
reads), after awaiting `ensure_favorite_*` in a `_load_heart` worker. The
album/artist toggles call `_refresh_hearts()`, which walks every screen.

**Favourites.** `ensure_favorite_tracks/albums/artists()` and
`ensure_user_playlists()` load once each (one lock per list, the favourites
in parallel at startup); a failed load isn't cached.
`toggle_favorite`, `toggle_favorite_album` and `toggle_follow_artist` update
the caches and patch the Tracks/Albums/Artists tabs, so nothing re-fetches.

**Library on disk** (`library_cache.py`: `~/.cache/qobit/library/<kind>.json`
(`$XDG_CACHE_HOME` honoured, like the covers; a cache, not state: Qobuz has
it all), one file per list so a toggle rewrites one list; each names its
account and a format `VERSION`, a mismatch reads as empty; nothing kept in
memory there; logout erases it through `library_cache.clear()`, not
`ACCOUNT_FILES`, which is DATA_DIR only).
`QobitApp._load_library` returns the disk copy at once and starts
`_refresh_library`, which fetches again and, only if the list differs
(dataclass `==`), saves it, replaces it and calls `_library_changed`
(Tracks `library_changed`, Albums/Artists `favorites_changed`, Playlists
`library_changed`: each redraws and puts the cursor back with
`select_where`, which mounts only through that item). Unchanged: nothing is
written. A toggle calls `_library_edited` (saves, and bumps
`_library_edits` so a refresh started before it is dropped); a refresh is
also dropped after an account change. Saves run in a thread worker and
carry `library_cache.epoch()` from when their list was asked for (an erase
calls `cancel_saves()` first: a save begun before logout is dropped instead
of writing the old account's library back) and a `ticket()` from when they
were decided (a pool thread finishing late can't overwrite a newer list).
`_load_library` refuses while logged out, and `action_logout` clears the
token before `_erase_account` (the rebuilt views ask for the library at
once; with the token still set they fetched and saved it again). Models
round-trip through `dataclasses.asdict`: a model field change that old
entries can't fill needs a `VERSION` bump. `qobit clear-cache` leaves the
library alone while a TUI runs (it would write it back).

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
  it for any new image and set `.placeholder` when the subject changes;
  `square_width(image, rows)` sizes it square. Query a page's own art by
  class (`.detail-art`): a bare `query_one(Art)` can find the transport's.
- Panel titles go through `_utils.readout()` (uppercase, same length).
- `nav.Wordmark` sits on the Screen's `overlay` layer, docked right with height
  1, so it shares the tab bar's row; it hides below `Wordmark.MIN_WIDTH`.
  While playing (not paused) it cycles the progress bar's gradient along its
  letters, `◢◤` as one unit (`WORDMARK_UNITS`; `wordmark_palette`: bar
  start/end, else accent text ↔ HI-RES; ANSI themes stay static) on its own
  ~12 fps timer, paused otherwise; the phase comes from `time.monotonic()`
  so every page's copy is in step. Textual's
  `Content ==` compares text only: tests compare `.spans`.
- The progress bar's tip (the partial block) gets a background
  (`_tip_background`: theme background + `TIP_TINT` of the accent, the
  colour the dim `░` track reads as: Textual's `DIM_FACTOR` × the shade's
  ~25% coverage; measured on rendered cells, `dim` is not 50%) so its bare part isn't a dark gap. Not
  with the theme background off (the terminal's colour is unknown) or on
  ANSI themes. A `─` track was tried and rejected for its look.

**Slow machines** (a Raspberry Pi 4 is a target). The theme is set in
`QobitApp.__init__`, before anything mounts (in `on_mount` it restyled every
widget: 30% of startup). `app.animations` (config `animations`, palette,
off with `TEXTUAL_ANIMATIONS=none`) gates the Wordmark's frame timer and the
beat meter: the Wordmark at 12 fps was most of the idle CPU while playing.

**Status line.** `status_msg` is the transport's `artist — title` line; use
`_flash_status()` for transient notices (auto-clears after ~3 s).

**Layout passes.** `Label.update()` relays out the whole screen unless
passed `layout=False` (~70 ms with 400 queue rows). Redraws that keep the
size (a row title: one line, nowrap; a card's colours on selection; the
beat's meter) pass `layout=False`; `tests/test_queue_speed.py` checks the
beat and the cursor. Grids list their cards from `self.children`, not a
`query()` (it walked every widget in every card on each move).

**Restyles.** Textual restyles a widget *and everything inside it* when its
focus or classes change. `PagedListView` and the grids override
`watch_has_focus` to restyle themselves (and a list's highlighted row)
only; a class that styles only its own widget (a container's border) goes
through `_utils.set_own_class`. Every screen uses `nav.QobitFooter`
(rebuilds its keys once per refresh, only if they changed; Textual's did 4–5
times per tab switch). `_YearLine` rebuilds only on a real width change:
Textual sends every widget a Resize each time its tab is shown.
`tests/test_tab_switch_speed.py`.

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
- qobit's chrome uses `$accent` for lines (borders, rules); text in the accent
  colour uses `$qobit-accent-text` (the accent where it reads, darker or
  lighter where it doesn't: light themes). Unfocused titles and subtitles are
  `$qobit-muted`, never a faded accent (`$accent 40%` was 2.6:1): a rule
  that colours a border title colours its subtitle too (unset, it takes
  the faded border colour; `tests/test_panel_css.py`); no text
  colour with an alpha (`$x 60%`): use a variable that is readable as is.
  Rich `Text` doesn't resolve `$variables`: read `app.theme_variables[...]`.
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
  colour). Secondary text is `$qobit-muted` (computed, readable on the page,
  the tints and the menu surface), never Textual's `$text-muted`: that is
  "auto 60%", picked against the widget's background, which with the theme
  background off is the terminal's, assumed dark (white-on-white muted text
  in light themes). The defaults use the theme's generated colour system
  (`theme.to_color_system().generate()`), not its raw fields. Any rule that
  fills a row in transparent mode must not out-rank the selection: the
  `Screen.-transparent ListView… -highlight` rules carry an extra type
  selector for that (the playing row once lost its highlight and kept black
  text). Widgets that resolve theme colours to values in Python (transport
  readout, Now Playing lamp) redraw on a theme change with
  `call_after_refresh`: the `theme` reactive fires before `theme_variables`
  is recomputed. Footer text sits on the page (no bar): keys
  `$qobit-readout`, descriptions `$foreground`, whatever the theme sets for
  its own bar. The list highlight targets Textual 8's `-highlight` class. Grid
  cards share `.grid-card` in `QobitApp.CSS`; never add per-card
  `.-selected` rules. `tests/test_themes.py` holds every theme, Textual's
  included, to 4.5:1.
- Never give a Label the classes `primary`, `secondary`, `accent`, `success`,
  `warning` or `error` (Textual 8 Label variants). Rows use `row-title` /
  `row-meta`.
- Numbered lists show the number only; unnumbered ones show `♪`. Secondary
  lines use `  ·  `.

**MPRIS2** (`audio/mpris.py`, dbus-fast). The bus runs on its own asyncio
loop in a daemon thread: the transport callbacks must arrive off the app's
thread (the app wraps them in `call_from_thread`), and `update()` hands a
`PlayerState` snapshot to the loop with `call_soon_threadsafe`; interface
state is only touched there. Connecting never blocks startup; any failure
leaves media keys off. A second qobit takes `org.mpris.MediaPlayer2.qobit.
instance<pid>`. D-Bus signatures are `dbus_fast.annotations` types
(`DBusStr`, …); with `from __future__ import annotations` a `@dbus_method`
must not annotate `-> None` (read as the string "None": TypeError at import).
`tests/test_mpris.py` runs a private `dbus-daemon`, never the session bus.

## Qobuz API

- `client.py` extracts `app_id` and signing secrets from the web player bundle
  at login (cached in config). `restore_session()` runs in `QobitApp.__init__`
  so credentials exist before any worker.
- Login is browser OAuth only (`login_oauth`, `qobit auth`). The
  email/password route stored the password in plaintext, sent it in a URL and
  couldn't stream: it's gone, and `purge_legacy_credentials()` scrubs it at
  startup. `login_oauth(show_url=…)` hands the `LoginPrompt` out before
  anything else (link always shown: SSH, no display); the browser opens only
  with a display (else `webbrowser` starts lynx in the terminal). The code
  comes back through `qobuz/oauth.py`: a loopback-only server (127.0.0.1 on
  an OS-picked port, then ::1 on the same one; one code) or
  `LoginPrompt.submit(pasted address)` (CLI: stdin via `add_reader`, never a
  blocking `input()` thread, which would hang exit). Qobuz drops OAuth's
  `state` (the redirect carries only `code_autorisation`), so the redirect
  URL has a random secret path, `localhost:<port>/<secret>`, and a code is
  taken only on it (anything else 404s): that is the login-CSRF guard.
- The TUI logs in itself: `QobitApp.request_login(reason)` pushes
  `LoginScreen` (once: `_login_open`), at startup without a token and from
  `client.on_auth_expired`, which `_send` calls on any 401 to a token-bearing
  API request (not the login endpoints). Code that needs a session runs from
  `_go_online()` (startup or after a login); `_reload_after_login()` clears
  the favourites caches and every view's `_loaded`, then re-activates the
  current tab. Tests: `StubClient` carries a token so `ui_app` starts logged
  in. Save a session with `save_oauth_session(*client.session(),
  account=client.account)`.
- Logout erases the account (`QobitApp._erase_account`): stop, leave
  Connect, `LocalPlayContext.reset()` (local only: the server's queue is the
  account's), clear reactives with saves suppressed (`_restoring`) and the
  queue-save timer stopped, drop caches, `account_data.erase_account_data()`,
  pop pages, then `App.recompose()` rebuilds every view empty. Also run when
  a *different* account logs in (names compared). A new file that holds
  account activity goes in `account_data.ACCOUNT_FILES` (a cache file in
  `~/.cache` gets erased explicitly, like the library), or logout leaves it
  behind. Every TUI holds a shared flock on `DATA_DIR/qobit.lock`;
  `qobit logout` refuses while one runs.
- Esc on a pushed screen: `action_back` first calls the screen's
  `action_navigate_back()` (True = handled). A modal whose caller needs its
  dismiss callback must close itself there: a bare `pop_screen()` skips the
  callback (the login dialog stayed "open" forever that way).
- Text from Qobuz is untrusted (public playlists are user-written) and
  Textual passes ESC to the terminal (verified: OSC 52 reached it). Every
  response goes through `client._json()` → `sanitize.clean_json`; never call
  `r.json()` directly. Server text shown elsewhere (Connect errors) goes
  through `sanitize.clean`; `store.load` cleans state files too.
- Credentials are `secret.Secret` (masked repr: Textual's crash screen
  prints locals). Wrap any new token/JWT/code where it enters. The session
  token is sent as `X-User-Auth-Token`, never a query parameter.
- mpv gets the stream URL from `DATA_DIR/stream.m3u` (0600, deleted on
  stop), never argv: argv is world-readable and a URL starting with `--`
  would be an option.
- The token goes through `token_store` (via `config.save_oauth_session` /
  `get_oauth_session`, never directly): system keyring when usable (session
  marked `"token_store": "keyring"`, no token in the file), else the file.
  Linux without `DBUS_SESSION_BUS_ADDRESS` skips the keyring; every keyring
  call silences fd 2 (GLib warns there while probing, garbling the TUI).
  conftest's autouse `fake_keyring` replaces `token_store._keyring` and
  `enabled`: a test must never reach the real Keychain.
- Everything qobit writes under CONFIG_DIR / DATA_DIR goes through
  `_fs.write_private` (0600 from the first byte, in a 0700 dir): the token
  lives in config.json and DATA_DIR holds mpv's IPC socket (its `run`
  command executes programs). Never write there with `write_text`.
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

Images: `widgets.art.Art` draws per `_images.image_mode()`: "kitty"
(textual-image's TGP renderable), "blocks" (`art.Blocks`, coloured half
blocks) or "off" (the initials tile). The setting (`IMAGE_MODES`: config
`images`, `qobit --images`, palette "Images: …") resolves "auto" from
textual-image's terminal query at import (Kitty if it chose TGP, else
blocks). `Art` keeps its renderable until the image, size or mode changes:
textual-image rebuilt it on every render, so each repaint re-encoded and
re-sent the whole image (20 sends for 10 grid cursor moves;
`tests/test_art_modes.py`). It frees its Kitty image on unmount. Grid cards pass
`Art(rows=…)`: the image is kept at that height in pixels (`fit_image`;
`Art.show(img)` shrinks it in a thread), not the 600 px cover (cards held
two full copies each: 1.1 GB after scrolling 300 albums). The memory cache
holds 48 covers (~1.1 MB each decoded); misses come from the disk cache. All
fetches go through `ui/_images.fetch_image(url)`: none with images off
(`terminal=False` for the OS media widget's art), 6 concurrent, normalised
to 600 px JPEG, cached in memory and in `~/.cache/qobit/images/`.

## Files

```
~/.config/qobit/config.json            session (token: keyring if any), device, theme, radio, Connect
~/.local/share/qobit/player_state.json last track + position
~/.local/share/qobit/queue_state.json  timeline + cursor
~/.local/share/qobit/mixes_cache.json  DailyQ / WeeklyQ
~/.local/share/qobit/mpv.sock          mpv IPC socket (fixed path)
~/.local/share/qobit/stream.m3u        stream URL for mpv (0600, while playing)
~/.local/share/qobit/qobit.lock        held (shared) by every running TUI
~/.cache/qobit/images/                 cover-art cache (qobit clear-cache)
~/.cache/qobit/library/                favourites + playlists, last fetched (library_cache)
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
- README screenshots (`screenshots/`, all six): `uv run --with matplotlib python
  scripts/screenshots.py` (log in first: `qobit auth`). It runs the real app
  headless on your session (paths in a temp dir), draws the screen cell by
  cell with DejaVu Sans Mono (block and box characters drawn, not typeset)
  and pastes the covers over their cells: cyberdeck, Mogwai "playing" past
  75% (play state set by hand, no audio), Ty Segall's page and album. Not a
  terminal capture. Re-run it after a UI change.
- Verify UI headless: `app.save_screenshot()` → `rsvg-convert` → PNG (Kitty
  images show as garbage there; real captures need a Kitty-protocol terminal).
  The PNG collapses or widens spaces between differently styled runs
  ("HI-RES24-BIT", "Kind of Blue♥" that were fine): check spacing on the
  rendered text, `screen._compositor.render_strips()` → `strip.text`.
- Releasing: version only in `pyproject.toml`. Bump, CHANGELOG entry, `uv lock`,
  commit `chore(release): X.Y.Z`, merge to main, annotated tag `vX.Y.Z`, push
  the tag. `.github/workflows/release.yml` then runs CI, checks the tag matches
  the version, publishes to PyPI (trusted publishing, `pypi` environment) and
  creates the GitHub release from the CHANGELOG entry: don't run
  `gh release create` by hand. Features → minor, fixes only → patch.
- The sdist is an explicit include list in `pyproject.toml`; a new top-level
  file it needs goes there. README links are absolute (it's PyPI's page too).

## Roadmap

- `i` menu on playlists; Search, AlbumScreen, PlaylistScreen and PlaylistsView
  redesign (art, richer headers, quality badges); TrackScreen detail overlay.
- Next: gapless playback.
- Queue reorder; ReplayGain, CMAF fallback.
- Qobuz Connect phase 3 (autoplay ↔ radio, shuffle/repeat UI, play reporting),
  phase 4 (qobit as controller, LAN discovery).

## References

- `vicrodh/qbz`: qobit's main source and inspiration, incl. the bit-perfect
  approach. Discontinued; its code is no longer available, so the approach
  lives in `audio/` and `docs/audio.md`.
- `pierdom/qobuz-mcp`: original source of `client.py`.
- `pierdom/tuidash`: Textual patterns (`@work`, reactives, `ContentSwitcher`).
- `pierdom/picast`: Rich audio-player TUI (transport bar patterns).

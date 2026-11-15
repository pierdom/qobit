from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from functools import lru_cache

from rich.color import Color as _RichColor
from rich.color import ColorType as _ColorType
from rich.style import Style as _RichStyle
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.css.query import NoMatches
from textual.filter import NO_DIM, dim_color
from textual.filter import ANSIToTruecolor as _ANSIToTruecolor
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Footer, Label, ListView, Tab, Tabs
from textual.worker import get_current_worker

from ..audio.media_keys import MediaKeys
from ..audio.player import MpvPlayer
from ..config import (
    get_audio_device,
    get_connect_enabled,
    get_oauth_session,
    get_radio_mode,
    get_saved_theme,
    get_saved_volume,
    get_software_volume_enabled,
    get_transparent_background,
    set_connect_enabled,
    set_radio_mode,
    set_saved_theme,
    set_saved_volume,
    set_software_volume_enabled,
    set_transparent_background,
)
from ..qobuz.client import AuthExpiredError, QobuzClient, QobuzError
from ..qobuz.models import Album, Artist, StreamUrl, Track
from ..store import load as load_state
from ..store import save as save_state
from .play_context import ConnectPlayContext, LocalPlayContext
from .screens.albums import AlbumsView
from .screens.artists import ArtistsView
from .screens.mixes import MixesView
from .screens.playlists import PlaylistsView
from .screens.queue import QueueView
from .screens.search import SearchView
from .screens.tracks import TracksView
from .themes import CYBERDECK_THEME, QOBIT_THEMES
from .widgets.transport import TransportBar


class _TransparentANSIToTruecolor(_ANSIToTruecolor):
    """ANSIToTruecolor that preserves ansi_default backgrounds so the
    terminal's own background (transparency/blur) shows through."""

    @lru_cache(1024)  # noqa: B019 — as in Textual's ANSIToTruecolor; lives as long as the app
    def truecolor_style(self, style: _RichStyle, background: _RichColor) -> _RichStyle:
        terminal_theme = self._terminal_theme
        changed = False

        color = style.color
        if color is not None and color.triplet is None:
            color = _RichColor.from_triplet(color.get_truecolor(terminal_theme, foreground=True))
            changed = True

        bgcolor = style.bgcolor
        keep_default_bg = bgcolor is not None and bgcolor.type == _ColorType.DEFAULT
        if bgcolor is not None and bgcolor.triplet is None and not keep_default_bg:
            bgcolor = _RichColor.from_triplet(
                bgcolor.get_truecolor(terminal_theme, foreground=False)
            )
            changed = True

        if style.dim and color is not None:
            if bgcolor is not None and bgcolor.triplet is not None:
                dim_bg = bgcolor
            elif background.triplet is not None:
                dim_bg = background
            else:
                dim_bg = _RichColor.from_triplet(
                    _RichColor.default().get_truecolor(terminal_theme, foreground=False)
                )
            color = dim_color(dim_bg, color)
            style += NO_DIM
            changed = True

        return style + _RichStyle.from_color(color, bgcolor) if changed else style


class QobitCommands(Provider):
    def _commands(self) -> list[tuple[str, object, str]]:
        app: QobitApp = self.app  # type: ignore[assignment]
        return [
            (
                "Draw theme background",
                app.action_toggle_background,
                "Toggle between theme background and terminal transparency",
            ),
            (
                f"Endless radio: turn {'off' if app.radio_mode else 'on'}",
                app.action_toggle_radio_mode,
                "Auto-fill Up Next with Qobuz song-radio suggestions when the queue runs dry",
            ),
            (
                f"Qobuz Connect: turn {'off' if app.connect_state != 'off' else 'on'}",
                app.action_toggle_connect,
                "Show qobit in the Qobuz apps' device picker and play what they send "
                "(bit-perfect, through mpv). While active, the queue is controlled from the app.",
            ),
            (
                f"Software volume: turn {'off' if app.software_volume_enabled else 'on'}",
                app.action_toggle_software_volume,
                "-/= to adjust once on. Below 100% this is DSP — the DAC no longer "
                "receives the file's exact bits. OFF (default) restores unity gain.",
            ),
        ]

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for label, callback, help_text in self._commands():
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), callback, help=help_text)

    async def discover(self) -> Hits:
        for label, callback, help_text in self._commands():
            yield Hit(0, label, callback, help=help_text)


# qobit's signature theme — the whole point of this app is looking right on a
# cyberdeck, so it ships its own neon-noir palette rather than leaning on a
# borrowed Textual built-in. $accent drives essentially every highlight,
# border-on-focus, and now-playing state in qobit's own CSS (118 uses) — that
# token carries the electric-cyan "this is live" read; $primary (magenta)
# shows up in Textual's own chrome (the command palette, default widget
# focus) for a two-tone neon identity rather than a single accent colour.
# Registered in __init__ and set as the default in on_mount; still an
# ordinary theme choice a user can switch away from via the command palette.

_PLAYER_STATE_FILE = "player_state.json"
# The whole play context (timeline + cursor), so the Playback tab shows the same
# queue after a restart. Separate from player_state.json: that one is rewritten
# every few seconds for the position; this one only when the queue changes.
_QUEUE_STATE_FILE = "queue_state.json"
_QUEUE_SAVE_DELAY = 1.0  # debounce — queue_version bumps in bursts
# One Connect device per machine (see connect.acquire_instance_lock).
_CONNECT_LOCK_FILE = "connect.lock"

# Most recently-played tracks kept in the session history (oldest dropped first).
_HISTORY_MAX = 100
# Pressing "previous" past this many seconds into a track restarts it; before
# it, steps back to the previous track in history (standard media convention).
_PREV_RESTART_THRESHOLD = 3.0

# Top bar, left to right: (tab id, display name). The bar shows "[N] Name" and
# the number keys follow this order — both are generated from this list. Ids are
# internal and stable (the Playback tab keeps the id "queue" / QueueView); names
# are display-only — never derive an id from a name.
_TABS = [
    ("queue", "Playback"),
    ("tracks", "Tracks"),
    ("artists", "Artists"),
    ("albums", "Albums"),
    ("playlists", "Playlists"),
    ("mixes", "Mixes"),
    ("search", "Search"),
]


def _tab_label(n: int, name: str, compact: bool) -> str:
    return f"{n} {name}" if compact else f"[{n}] {name}"


# Columns the full "[N] Name" bar needs: the bar's 1-col leading offset, then
# each label plus Tab's 1-col padding on both sides (measured: 90). Narrower
# terminals get the compact "N Name" labels instead of a clipped last tab.
_TABBAR_FULL_WIDTH = 1 + sum(
    len(_tab_label(n, name, False)) + 2 for n, (_, name) in enumerate(_TABS, 1)
)


class QobitApp(App[None]):
    TITLE = "qobit"
    COMMANDS = App.COMMANDS | {QobitCommands}
    CSS = """
    Screen {
        layout: vertical;
        /* The whole app's horizontal inset lives here, not on individual
           children's margin. Textual's vertical-layout arrangement
           subtracts a margined child's *own* left+right margin from the
           width basis it uses to resolve `1fr` for every sibling in the
           same layout — so giving ContentSwitcher's content views and
           TransportBar their own independent horizontal margins made them
           drift out of alignment with each other (and with Tabs/Footer,
           which had no margin and so weren't inset at all). Padding on
           the shared Screen ancestor has no such cross-sibling effect, and
           it now insets Tabs/Footer identically to everything else.
        */
        padding: 0 1;
    }
    /* Unified scrollbar design across every scrollable widget / page. */
    * {
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
        scrollbar-background: $surface;
        scrollbar-background-hover: $surface;
        scrollbar-background-active: $surface;
        scrollbar-color: $surface-lighten-2;
        scrollbar-color-hover: $surface-lighten-2;
        scrollbar-color-active: $accent;
    }
    Tabs {
        dock: top;
    }
    ContentSwitcher {
        height: 1fr;
    }
    SearchView, PlaylistsView, TracksView, ArtistsView, AlbumsView, MixesView, QueueView {
        height: 1fr;
        margin-bottom: 1;
    }
    /* List highlight: the accent tint, like the grid cards' selection.
       Textual 8 marks the row with `-highlight` (it was `--highlight` in
       older Textual; a rule on the old name silently stopped matching and
       every list fell back to Textual's solid primary-colour cursor).
       Focused list: full tint. Unfocused list: a faint tint, so the Search
       tab's three lists don't all look selected. Labels go transparent so
       the row's tint shows behind the text under Screen.-transparent too
       (same fix as .grid-card). */
    ListView > ListItem.-highlight {
        color: $foreground;
        background: $accent 12%;
        text-style: none;
    }
    ListView:focus > ListItem.-highlight {
        color: $foreground;
        background: $accent 35%;
        text-style: none;
    }
    ListItem.-highlight > Label {
        background: transparent;
    }
    /* Active-tab underline in the accent colour too (Textual draws it with
       $block-cursor-background, i.e. the theme's primary). */
    Tabs Underline > .underline--bar {
        color: $accent;
    }
    /* The footer scrolls horizontally when its key hints don't fit (narrow
       terminals), and its scrollbar then covers the hints. Hide it: the
       hints that fit stay visible. */
    Footer {
        scrollbar-size-horizontal: 0;
    }
    Label.-hl {
        color: $accent;
    }
    /* Grid card selection — one style for every library grid tile
       (AlbumCard / ArtistCard / PlaylistCard all carry .grid-card), matching
       a highlighted list row: accent tint + accent text, primary line bold.
       Lives here because App.CSS outranks every widget's DEFAULT_CSS.

       The tint must cover the text block, so the card's children are made
       see-through when selected. Under transparency, Screen.-transparent *
       below paints *every* widget ansi_default — including .card-info and
       its Labels, which then covered the card's tint everywhere except the
       1-col gap beside the image (the long-standing "selection looks wrong
       on Ghostty" issue: earlier fixes restored the *card's* background,
       which was correct but hidden). These selectors carry three classes,
       out-ranking that rule's one class + one type in both modes.

       No outline: a 5-row tile's text fills .card-info, and child Labels
       paint over the parent's outline cells, so it only ever showed on cards
       with spare rows (Artists) — the visible inconsistency. */
    .grid-card.-selected {
        background: $accent 35%;
    }
    .grid-card.-selected .card-info,
    .grid-card.-selected .card-info > Label {
        background: transparent;
    }
    .grid-card.-selected .card-primary {
        color: $accent;
        text-style: bold;
    }
    .grid-card.-selected .card-secondary {
        color: $accent;
    }
    /* Selection/status backgrounds that must survive transparency mode.
       Textual gives this App.CSS stylesheet an unconditional priority layer
       over *every* widget's own DEFAULT_CSS — a widget's rule can never
       out-rank an App.CSS rule by being more specific, no matter how it's
       written (confirmed empirically, not assumed). So the only place an
       override can actually win against Screen.-transparent * below is
       here, scoped under it so it only applies in transparent mode (the
       widgets' own rules already supply the same values the rest of the
       time). These are UI *state* — selection, now-playing, a divider, a
       modal's legibility — not page background, and losing them silently
       under transparency is worse than the terminal not showing through
       there specifically. */
    Screen.-transparent MixCard.-selected {
        background: $boost;
    }
    Screen.-transparent NowPlayingListRow {
        background: $accent 8%;
    }
    Screen.-transparent SectionHeader {
        background: $surface;
    }
    Screen.-transparent,
    Screen.-transparent * {
        background: ansi_default;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "pause", "Pause"),
        Binding("[", "seek_back", "◀10s"),
        Binding("]", "seek_fwd", "10s▶"),
        Binding("R", "start_radio", "Radio"),
        Binding("i", "track_menu", "Menu", show=False),
        Binding("g", "goto_menu", "Go to", show=False),
        # No-op (with an explanatory flash) until software volume is turned on
        # via the command palette — see action_toggle_software_volume.
        Binding("minus", "volume_down", "Vol-"),
        Binding("equals_sign", "volume_up", "Vol+"),
        # 1-7 match the tab bar's "[N]" labels (generated from _TABS); escape
        # brings focus to Tabs from anywhere.
        *[
            Binding(str(n), f"switch_tab('{tid}')", name, show=False)
            for n, (tid, name) in enumerate(_TABS, 1)
        ],
        # priority=True so this fires even when an Input has focus
        Binding("escape", "focus_tabs", "Nav", show=False, priority=True),
    ]

    now_playing: reactive[Track | None] = reactive(None)
    # Full album of the now-playing track (year/genre/label/hi-res). Fetched
    # async by _fetch_now_playing_album so the Now Playing block can show rich
    # metadata the lean Track model doesn't carry. None until it arrives.
    now_playing_album: reactive[Album | None] = reactive(None)
    # Biography of the now-playing artist, fetched async by _fetch_now_playing_bio
    # so the Queue's Now Playing hero can fill its space with the artist story.
    now_playing_bio: reactive[str] = reactive("")
    is_playing: reactive[bool] = reactive(False)
    is_paused: reactive[bool] = reactive(False)
    playback_pos: reactive[float] = reactive(0.0)
    playback_dur: reactive[float] = reactive(0.0)
    status_msg: reactive[str] = reactive("")
    queue_version: reactive[int] = reactive(0)
    quality_label: reactive[str] = reactive("")
    # Endless radio: when on, _advance_queue refills an empty queue with Qobuz
    # song-radio suggestions. Reactive so the transport indicator stays live.
    radio_mode: reactive[bool] = reactive(False)
    # Software volume (see action_toggle_software_volume): off by default,
    # since this is the one control that can take the signal path out of
    # bit-perfect. `volume` is meaningless while disabled; TransportBar only
    # surfaces it when software_volume_enabled and volume != 100.
    software_volume_enabled: reactive[bool] = reactive(False)
    volume: reactive[int] = reactive(100)
    # Qobuz Connect: "off" | "connecting" | "ready" (visible in the apps) |
    # "active" (the apps' chosen device — server owns the queue) | "offline".
    connect_state: reactive[str] = reactive("off")

    def __init__(self, connect: bool | None = None) -> None:
        """``connect`` overrides the saved Qobuz Connect setting for this run
        (``qobit --connect-on``); None = use the saved setting."""
        super().__init__()
        for theme in QOBIT_THEMES:
            self.register_theme(theme)
        self._connect_wanted = get_connect_enabled() if connect is None else connect
        self._connect: ConnectPlayContext | None = None
        self._connect_lock: object | None = None  # flock'd file while Connect is on
        self._local_ctx = LocalPlayContext(self)
        self._client = QobuzClient()
        # Software volume is loaded here too (not just into the `volume` /
        # `software_volume_enabled` reactives in on_mount) so the very first
        # play() of the session already carries --volume, instead of racing
        # the reactives' later assignment.
        _sw_vol_on = get_software_volume_enabled()
        self._player = MpvPlayer(
            audio_device=get_audio_device(),
            volume=get_saved_volume() if _sw_vol_on else None,
        )
        # The play context is one ordered timeline plus a cursor at the
        # now-playing track. Everything behind the cursor is "Recently Played",
        # everything ahead is "Up Next". Selecting a queue row just moves the
        # cursor, so skipping back and forth never drops tracks. Persisted to
        # queue_state.json (see _save_queue_state); the past is trimmed to
        # _HISTORY_MAX to bound memory.
        self._timeline: list[Track] = []
        self._cursor: int = -1
        # A restored-but-not-yet-loaded track: (track, position). Set on launch
        # from the persisted player state; resumed on the next play/pause press.
        self._pending_resume: tuple[Track, float] | None = None
        self._restoring: bool = False
        self._last_state_save: float = 0.0
        # Favourite tracks, fetched once and shared by TracksView (full list)
        # and the heart glyph in other track lists (id set). None = not loaded.
        # Kept in sync by toggle_favorite so all views stay current.
        self._fav_tracks: list[Track] | None = None
        self._fav_ids: set[str] | None = None
        self._fav_lock = asyncio.Lock()
        # Favourite albums / followed artists: fetched once, shared by the
        # Albums and Artists tabs and the `i` menu, kept in sync by the toggles.
        self._fav_albums: list[Album] | None = None
        self._fav_artists: list[Artist] | None = None
        self._fav_albums_lock = asyncio.Lock()  # one lock per list: they load in parallel
        self._fav_artists_lock = asyncio.Lock()
        # Prevents two concurrent _advance_queue calls from both seeing an empty
        # queue and both fetching radio + starting playback.
        self._advance_lock = asyncio.Lock()
        # Maps track_id → last successful stream quality so _do_play tries the
        # known-good format first instead of always starting at FLAC_24_192.
        self._track_quality_cache: dict[str, str] = {}
        # Bumped by every _play_stream call: a call whose stream resolved after
        # a newer one started is stale and must not touch the player.
        self._play_gen = 0
        # The stream _play_stream last started (format/rate/depth) — the Connect
        # renderer reports it to the apps.
        self._last_stream: StreamUrl | None = None
        self._flash_timer: object | None = None
        self._queue_save_timer: object | None = None
        self._media_keys = MediaKeys(
            on_play_pause=lambda: self.call_from_thread(self.action_pause),
            on_next=lambda: self.call_from_thread(self._advance_queue),
            on_previous=lambda: self.call_from_thread(self.action_previous),
        )
        app_id, token, secrets = get_oauth_session()
        if app_id and token and secrets:
            self._client.restore_session(app_id, token, secrets)

    def compose(self) -> ComposeResult:
        yield Tabs(
            *[Tab(_tab_label(n, name, False), id=tid) for n, (tid, name) in enumerate(_TABS, 1)],
            id="nav-tabs",
            active="queue",
        )
        with ContentSwitcher(initial="view-queue"):
            yield PlaylistsView(id="view-playlists")
            yield TracksView(id="view-tracks")
            yield ArtistsView(id="view-artists")
            yield AlbumsView(id="view-albums")
            yield MixesView(id="view-mixes")
            yield SearchView(id="view-search")
            yield QueueView(id="view-queue")
        yield TransportBar()
        yield Footer()

    async def on_mount(self) -> None:
        self._poll_player()
        saved_theme = get_saved_theme()
        if saved_theme and saved_theme in self.available_themes:
            self.theme = saved_theme
        else:
            # No saved preference (first launch, or an invalid/removed theme
            # name) — default to qobit's own look rather than a generic
            # Textual built-in. Still just an ordinary theme choice: `^p` →
            # Theme switches away from it like any other.
            self.theme = CYBERDECK_THEME.name
        # Persist any subsequent theme changes (skip the initial/default ones).
        self._theme_ready = True
        if get_transparent_background():
            self.action_toggle_background()
        self.radio_mode = get_radio_mode()
        self.software_volume_enabled = _sw_vol_on = get_software_volume_enabled()
        self.volume = get_saved_volume() if _sw_vol_on else 100
        self._fit_tab_labels(self.size.width)
        self._restore_player_state()
        if self._connect_wanted:
            self._start_connect()
        self.set_interval(5.0, self._connect_tick)
        self._warm_favorite_ids()
        self._prune_image_cache()

    @work(thread=True)
    def _prune_image_cache(self) -> None:
        from ._images import prune_disk_cache

        prune_disk_cache()

    def watch_theme(self, theme: str) -> None:
        if getattr(self, "_theme_ready", False):
            set_saved_theme(theme)

    # ── tab switching ─────────────────────────────────────────────────────────

    @on(Tabs.TabActivated, "#nav-tabs")
    def _on_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None:
            return
        self.query_one(ContentSwitcher).current = f"view-{event.tab.id}"
        if event.tab.id == "search":
            self._focus_search_input()
        # Tell the view directly rather than relying on its Show event: after
        # End on a long list Textual sometimes never sent Show to the next tab,
        # which then never loaded.
        view = self.query_one(f"#view-{event.tab.id}")
        if hasattr(view, "activate"):
            self.call_after_refresh(view.activate)

    def on_resize(self, event: events.Resize) -> None:
        self._fit_tab_labels(event.size.width)

    def _fit_tab_labels(self, width: int) -> None:
        compact = width < _TABBAR_FULL_WIDTH
        if compact == getattr(self, "_tabs_compact", None):
            return
        self._tabs_compact = compact
        for n, (tid, name) in enumerate(_TABS, 1):
            # Not composed yet on the first resize; on_mount runs this again.
            with contextlib.suppress(Exception):
                self.query_one(f"#{tid}", Tab).label = _tab_label(n, name, compact)

    def action_switch_tab(self, tab_id: str) -> None:
        self.query_one("#nav-tabs", Tabs).active = tab_id

    def action_focus_tabs(self) -> None:
        if len(self.screen_stack) > 1:
            screen = self.screen
            if hasattr(screen, "action_navigate_back"):
                screen.action_navigate_back()
            else:
                self.pop_screen()
        else:
            cs = self.query_one(ContentSwitcher)
            if cs.current:
                try:
                    active = self.query_one(f"#{cs.current}")
                    if hasattr(active, "action_navigate_back") and active.action_navigate_back():
                        return
                except Exception:
                    pass
            self.query_one("#nav-tabs", Tabs).focus()

    _hl_item: object = None

    @on(ListView.Highlighted)
    def _on_list_highlighted(self, event: ListView.Highlighted) -> None:
        # Only touch the previously highlighted row, not every row in the list —
        # a full query(Label) over a long list on each arrow press is what made
        # the track list feel laggy.
        prev = self._hl_item
        if prev is not None and prev is not event.item:
            for label in prev.query(Label):
                label.remove_class("-hl")
        if event.item:
            for label in event.item.query(Label):
                label.add_class("-hl")
        self._hl_item = event.item

    def _focus_search_input(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#search-input").focus()

    # ── favourite tracks (heart glyph in track lists) ─────────────────────────

    async def ensure_favorite_tracks(self) -> list[Track]:
        """Favourite tracks, fetched exactly once and cached as `Track`s.

        Double-checked locking serialises concurrent first callers (TracksView's
        list load, the mount-time heart warm, the QueueView refresh, each
        track-list builder) so the paginated favourites endpoint is hit once.
        Raises on fetch failure (without caching) so callers can retry."""
        if self._fav_tracks is not None:
            return self._fav_tracks
        async with self._fav_lock:
            if self._fav_tracks is None:
                raw = await self._client.get_all_favorite_tracks()
                self._fav_tracks = [Track.from_api(r) for r in raw]
            return self._fav_tracks

    async def ensure_favorite_ids(self) -> set[str]:
        """Set of favourited track ids, derived from the shared favourites
        cache. A failed fetch gives an empty set (no hearts) but isn't cached,
        so the next call retries; caching it hid every favourite for the rest
        of the session after one failed request at startup."""
        if self._fav_ids is None:
            try:
                tracks = await self.ensure_favorite_tracks()
            except Exception:
                return set()
            self._fav_ids = {t.id for t in tracks}
        return self._fav_ids

    async def toggle_favorite(self, track: Track) -> bool:
        """Add/remove a track from the user's Qobuz favourites, keep the shared
        caches in sync, and refresh the Tracks tab. Returns the new favourite
        state (unchanged on API error)."""
        tid = str(track.id)
        ids = await self.ensure_favorite_ids()  # also loads _fav_tracks
        if self._fav_ids is None:  # couldn't load them: don't guess the state
            self._flash_status("Couldn't load favourites")
            return False
        try:
            if tid in ids:
                await self._client.set_favorite("track", tid, False)
                ids.discard(tid)
                if self._fav_tracks is not None:
                    self._fav_tracks = [t for t in self._fav_tracks if t.id != tid]
                new_state = False
            else:
                await self._client.set_favorite("track", tid, True)
                ids.add(tid)
                if self._fav_tracks is not None and not any(t.id == tid for t in self._fav_tracks):
                    added = dataclasses.replace(track, favorited_at=int(time.time()))
                    self._fav_tracks.append(added)
                new_state = True
        except Exception:
            self._flash_status("Couldn't update favourite")
            return tid in ids
        with contextlib.suppress(Exception):
            self.query_one(TracksView).favorite_changed(tid, new_state)
        # Refresh the Queue so hearts on history/now-playing/up-next rows update.
        self.queue_version += 1
        return new_state

    @work
    async def _warm_favorite_ids(self) -> None:
        """Load the favourites caches up front: track hearts, and the album and
        artist state the `i` menu labels its toggles with (it would otherwise
        wait on the first fetch before opening)."""
        await asyncio.gather(
            self.ensure_favorite_ids(),
            self.ensure_favorite_albums(),
            self.ensure_favorite_artists(),
            return_exceptions=True,  # a failed list is fetched again on first use
        )

    # ── favourite albums and followed artists ────────────────────────────────

    async def ensure_favorite_albums(self) -> list[Album]:
        """Favourite albums, fetched once. Raises on failure (nothing cached)."""
        async with self._fav_albums_lock:
            if self._fav_albums is None:
                raw = await self._client.get_all_favorite_albums()
                self._fav_albums = [Album.from_api(r) for r in raw]
            return self._fav_albums

    async def ensure_favorite_artists(self) -> list[Artist]:
        """Followed (favourite) artists, fetched once. Raises on failure."""
        async with self._fav_artists_lock:
            if self._fav_artists is None:
                raw = await self._client.get_all_favorite_artists()
                self._fav_artists = [Artist.from_api(r) for r in raw]
            return self._fav_artists

    async def toggle_favorite_album(self, album: Album) -> None:
        albums = await self.ensure_favorite_albums()
        on = not any(a.id == album.id for a in albums)
        try:
            await self._client.set_favorite("album", album.id, on)
        except QobuzError:
            self._flash_status("Couldn't update favourite")
            return
        if on:
            albums.append(dataclasses.replace(album, favorited_at=int(time.time())))
            self._flash_status(f"Added to favourites: {album.title}")
        else:
            albums[:] = [a for a in albums if a.id != album.id]
            self._flash_status(f"Removed from favourites: {album.title}")
        with contextlib.suppress(Exception):
            self.query_one(AlbumsView).favorites_changed()

    async def toggle_follow_artist(self, artist: Artist) -> None:
        artists = await self.ensure_favorite_artists()
        on = not any(a.id == artist.id for a in artists)
        try:
            await self._client.set_favorite("artist", artist.id, on)
        except QobuzError:
            self._flash_status("Couldn't update followed artists")
            return
        if on:
            artists.append(dataclasses.replace(artist, favorited_at=int(time.time())))
            self._flash_status(f"Following {artist.name}")
        else:
            artists[:] = [a for a in artists if a.id != artist.id]
            self._flash_status(f"Unfollowed {artist.name}")
        with contextlib.suppress(Exception):
            self.query_one(ArtistsView).favorites_changed()

    # ── seek from mouse click on bar ─────────────────────────────────────────

    def on_transport_bar_seek_to(self, event: TransportBar.SeekTo) -> None:
        self._seek_to(event.position)

    def _seek_to(self, position: float) -> None:
        """Absolute seek. Through the renderer while Connect-active so the apps'
        progress bars follow."""
        if self._ctx is not self._local_ctx:
            self.run_worker(self._connect.renderer.seek(position))  # type: ignore[union-attr]
        else:
            self._player.seek_to(position)

    # ── player poll (thread — IPC blocks) ────────────────────────────────────

    @work(thread=True)
    def _poll_player(self) -> None:
        worker = get_current_worker()
        _seen_gen: int = 0
        while not worker.is_cancelled:
            # One atomic read: a stop() racing this loop can't make a forced
            # stop look like a natural end (see MpvPlayer.snapshot).
            gen, running = self._player.snapshot()
            if running:
                _seen_gen = gen
                pos = self._player.get_property("time-pos")
                dur = self._player.get_property("duration")
                paused = self._player.get_property("pause")
                if pos is not None:
                    self.call_from_thread(setattr, self, "playback_pos", float(pos))
                if dur is not None:
                    self.call_from_thread(setattr, self, "playback_dur", float(dur))
                if paused is not None:
                    self.call_from_thread(setattr, self, "is_paused", bool(paused))
                if not self.is_playing:
                    self.call_from_thread(setattr, self, "is_playing", True)
                # Persist position periodically so a crash/kill loses ≤5s.
                now = time.monotonic()
                if now - self._last_state_save > 5.0:
                    self._last_state_save = now
                    self.call_from_thread(self._save_player_state)
            elif self.is_playing:
                natural_end = gen == _seen_gen
                self.call_from_thread(setattr, self, "is_playing", False)
                self.call_from_thread(setattr, self, "is_paused", False)
                self.call_from_thread(setattr, self, "playback_pos", 0.0)
                self.call_from_thread(setattr, self, "quality_label", "")
                if natural_end:
                    self.call_from_thread(self._advance_queue)
            time.sleep(0.5)

    # ── timeline slices (read-only views onto _timeline for the Queue UI) ────

    @property
    def _history(self) -> list[Track]:
        """Recently Played: everything behind the cursor."""
        return self._timeline[: self._cursor] if self._cursor >= 0 else []

    @property
    def _play_queue(self) -> list[Track]:
        """Up Next: everything ahead of the cursor."""
        return self._timeline[self._cursor + 1 :] if self._cursor >= 0 else []

    def _trim_history(self) -> None:
        """Bound the past to _HISTORY_MAX tracks by dropping the oldest and
        pulling the cursor back to match."""
        if self._cursor > _HISTORY_MAX:
            drop = self._cursor - _HISTORY_MAX
            del self._timeline[:drop]
            self._cursor -= drop

    # ── play (async worker — shares event loop with httpx) ───────────────────

    @work
    async def play_track(
        self, track: Track, queue: list[Track] | None = None, start: float = 0.0
    ) -> None:
        if self._ctx is self._local_ctx:
            self._pending_resume = None
        await self._ctx.load(track, queue, start)

    @work
    async def jump_to(self, index: int) -> None:
        """Move the cursor to any position on the timeline and play it. This is
        how the Playback tab skips back and forth without dropping tracks."""
        await self._ctx.jump_to(index)

    # Timeline mutators delegate to the active play context (ui/play_context.py):
    # local by default; while qobit is the active Qobuz Connect renderer the
    # server owns the queue and these send it commands (the timeline updates on
    # the server's echo). They return whether an edit was made/sent.

    def clear_queue(self) -> bool:
        return self._ctx.clear_queue()

    def clear_history(self) -> bool:
        return self._ctx.clear_history()

    def queue_next(self, track: Track) -> bool:
        """Insert a track just after the now-playing one so it plays next."""
        return self._ctx.queue_next(track)

    def queue_last(self, track: Track) -> bool:
        """Append a track to the tail of Up Next."""
        return self._ctx.queue_last(track)

    def remove_from_queue(self, track: Track) -> bool:
        """Drop a track from Up Next (positions ahead of the cursor). Matches by
        identity first so the exact queued instance is removed even if the same
        track sits at several positions; falls back to the first id match ahead
        of the cursor. Returns whether it removed."""
        return self._ctx.remove(track)

    @property
    def _ctx(self) -> LocalPlayContext | ConnectPlayContext:
        """Who owns the timeline right now."""
        if self._connect is not None and self._connect.active:
            return self._connect
        return self._local_ctx

    @work
    async def _advance_queue(self) -> None:
        async with self._advance_lock:
            await self._ctx.advance()

    # ── song radio (dynamic/suggest) ─────────────────────────────────────────

    def _flash_status(self, msg: str, secs: float = 3.0) -> None:
        """Show a transient status message, then revert. status_msg drives the
        transport's main label (TransportBar._on_status_msg), so a *persistent*
        message would hide the artist — title until the next track loads. Auto-
        clearing lets the player fall back to the now-playing track."""
        if self._flash_timer is not None:
            self._flash_timer.stop()  # type: ignore[union-attr]
        self.status_msg = msg
        self._flash_timer = self.set_timer(secs, lambda: self._clear_flash(msg))

    def _clear_flash(self, msg: str) -> None:
        if self.status_msg == msg:  # only clear if nothing newer replaced it
            self.status_msg = ""

    def action_start_radio(self) -> None:
        seed = self.now_playing or (self._pending_resume[0] if self._pending_resume else None)
        if seed is None:
            self._flash_status("Play something to start radio")
            return
        # No pre-message: keep the current track visible in the player while the
        # suggestions load; _start_radio flashes the result.
        self._start_radio(seed)

    @work
    async def _start_radio(self, seed: Track) -> None:
        suggestions = await self._fetch_radio(seed)
        if not suggestions:
            self._flash_status("No radio suggestions available")
            return
        # Replace Up Next with the fresh station, keeping the current track.
        if self._ctx.replace_upcoming(suggestions):
            self._flash_status(f"Radio: {len(suggestions)} tracks queued")

    def action_toggle_radio_mode(self) -> None:
        self.radio_mode = not self.radio_mode
        set_radio_mode(self.radio_mode)
        self._flash_status(f"Endless radio {'on' if self.radio_mode else 'off'}")

    # ── software volume (off by default — see config.get_software_volume_enabled) ──

    _VOLUME_STEP = 5

    def action_toggle_software_volume(self) -> None:
        self.software_volume_enabled = not self.software_volume_enabled
        set_software_volume_enabled(self.software_volume_enabled)
        if self.software_volume_enabled:
            # Re-enabling resumes at unity gain, not whatever was last dialed
            # in — turning the feature on should never by itself degrade the
            # signal; only an explicit volume_down after this does that.
            self.volume = 100
            self._player._volume = 100
            self._flash_status("Software volume on — bit-perfect while at 100%")
        else:
            self.volume = 100
            set_saved_volume(100)
            if self._player.running:
                self._player.reset_volume()
            else:
                self._player._volume = None
            self._flash_status("Software volume off — bit-perfect restored")

    def action_volume_down(self) -> None:
        self._adjust_volume(-self._VOLUME_STEP)

    def action_volume_up(self) -> None:
        self._adjust_volume(self._VOLUME_STEP)

    def _adjust_volume(self, delta: int) -> None:
        if not self.software_volume_enabled:
            self._flash_status("Software volume is off — turn it on from the command palette (^p)")
            return
        self.volume = max(0, min(100, self.volume + delta))
        set_saved_volume(self.volume)
        if self._player.running:
            self._player.set_volume(self.volume)
        else:
            self._player._volume = self.volume

    @staticmethod
    def _as_int(value: object) -> int | None:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    async def _analysed_entry(self, seed: Track) -> dict | None:
        """Build one `track_to_analysed` entry (artist/label/genre ids) for the
        seed. Reuses the already-fetched now_playing_album when it matches;
        otherwise pulls the ids from a single track/get."""
        artist_id = label_id = genre_id = None
        album = self.now_playing_album
        if album is not None and self.now_playing is not None and self.now_playing.id == seed.id:
            artist_id, label_id, genre_id = album.artist_id, album.label_id, album.genre_id
        else:
            try:
                data = await self._client.get_track(str(seed.id))
            except QobuzError:
                return None
            alb = data.get("album") or {}
            artist = alb.get("artist") or data.get("performer") or {}
            artist_id = artist.get("id")
            label_id = (alb.get("label") or {}).get("id")
            genre_id = (alb.get("genre") or {}).get("id")
        artist_id, label_id, genre_id = (
            self._as_int(artist_id),
            self._as_int(label_id),
            self._as_int(genre_id),
        )
        if artist_id is None and genre_id is None:
            return None
        return {
            "track_id": self._as_int(seed.id),
            "artist_id": artist_id,
            "label_id": label_id,
            "genre_id": genre_id,
        }

    async def _fetch_radio(self, seed: Track) -> list[Track]:
        """Fetch song-radio suggestions seeded from `seed` + recent history.
        Returns parsed Tracks with the seed window de-duplicated out. Best-effort:
        any failure yields an empty list and the caller leaves the queue intact."""
        listened: list[int] = []
        for track in [seed, *reversed(self._history)]:
            tid = self._as_int(track.id)
            if tid is not None and tid not in listened:
                listened.append(tid)
            if len(listened) >= 10:
                break

        entry = await self._analysed_entry(seed)
        try:
            resp = await self._client.get_dynamic_suggestions(listened, [entry] if entry else [])
        except QobuzError:
            return []

        seen = {str(i) for i in listened}
        out: list[Track] = []
        for item in (resp.get("tracks") or {}).get("items") or []:
            try:
                track = Track.from_api(item)
            except Exception:
                continue
            if str(track.id) in seen:
                continue
            seen.add(str(track.id))
            out.append(track)
        return out

    async def generate_mix(self) -> list[Track]:
        """Generate a mix from history + favourites via dynamic/suggest. DailyQ
        and WeeklyQ use the same recipe; MixesView only caches them for a day
        and a week respectively.

        Seeds up to 50 tracks from recent history (most-recent-first) topped up
        with favourites.  A spread of up to 12 seeds is enriched with
        artist/label/genre metadata for the `track_to_analysed` payload.  Returns
        an empty list when there are no seeds or the API fails."""
        fav_tracks: list[Track] = []
        with contextlib.suppress(Exception):
            fav_tracks = await self.ensure_favorite_tracks()

        seen: set[str] = set()
        seeds: list[Track] = []
        for t in [*reversed(self._history), *fav_tracks]:
            if t.id not in seen:
                seen.add(t.id)
                seeds.append(t)
            if len(seeds) >= 50:
                break

        if not seeds:
            return []

        listened: list[int] = [tid for t in seeds if (tid := self._as_int(t.id)) is not None]

        analyse_count = min(12, len(seeds))
        if analyse_count > 1:
            step = (len(seeds) - 1) / (analyse_count - 1)
            picks = [seeds[round(i * step)] for i in range(analyse_count)]
        else:
            picks = seeds[:analyse_count]

        analysed_results = await asyncio.gather(
            *[self._analysed_entry(t) for t in picks], return_exceptions=True
        )
        analysed = [e for e in analysed_results if isinstance(e, dict)]

        try:
            resp = await self._client.get_dynamic_suggestions(listened, analysed)
        except QobuzError:
            return []

        seed_ids = {str(i) for i in listened}
        out: list[Track] = []
        seen2: set[str] = set(seed_ids)
        for item in (resp.get("tracks") or {}).get("items") or []:
            try:
                track = Track.from_api(item)
            except Exception:
                continue
            if track.id in seen2:
                continue
            seen2.add(track.id)
            out.append(track)
        return out

    async def _resolve_stream(self, track_id: str) -> StreamUrl | None:
        """Walk the quality fallback chain and return the first stream Qobuz
        hands back, or None. A preview (``stream.sample``) is returned as-is
        and ends the walk: Qobuz returns the same preview at every quality, so
        trying the rest would only cost round-trips. Raises AuthExpiredError."""
        # Prefer the quality that worked last time for this track; fall through to
        # the full list on first play or if the cached quality fails.
        all_qualities = ["FLAC_24_192", "FLAC_24_96", "FLAC_CD"]
        cached = self._track_quality_cache.get(track_id)
        if cached:
            qualities = [cached] + [q for q in all_qualities if q != cached]
        else:
            qualities = all_qualities
        for quality in qualities:
            try:
                data = await self._client.get_streaming_url(track_id, quality)
            except AuthExpiredError:
                raise
            except QobuzError:
                continue
            stream = StreamUrl.from_api(data)
            if stream.sample:
                return stream
            self._track_quality_cache[track_id] = quality
            if len(self._track_quality_cache) > 1000:
                del self._track_quality_cache[next(iter(self._track_quality_cache))]
            return stream
        return None

    async def _do_play(self, track: Track, start: float = 0.0) -> None:
        """Local-context playback: start ``track``; on a preview, skip forward."""
        result = await self._play_stream(track, start)
        if result == "sample":
            # Never play a preview: it's a 30 s MP3, not the FLAC. Treat the track
            # as unavailable — skip forward like the official apps do, or stop
            # if nothing is queued after it.
            self._flash_status(f"Not streamable (preview only): {track.display_title}")
            if self._cursor + 1 < len(self._timeline) or self.radio_mode:
                self._advance_queue()
            elif self._player.running:
                self._player.stop()

    async def _play_stream(self, track: Track, start: float = 0.0) -> str:
        """Resolve and start ``track`` in mpv. Returns "ok", "sample" (preview
        only — nothing started), "none" (no stream), "auth" (session expired)
        or "stale" (another track was requested while this one resolved; it
        took over, nothing started). Shared by both play contexts; skipping
        policy is the caller's."""
        self._play_gen += 1
        gen = self._play_gen
        self.status_msg = f"Loading {track.display_title}…"
        try:
            stream = await self._resolve_stream(str(track.id))
        except AuthExpiredError:
            self.status_msg = "Session expired — run: qobit auth"
            return "auth"
        if gen != self._play_gen:
            # Picking A then B quickly: if A's URL arrives last it would stop B
            # and play A while the queue's cursor says B.
            return "stale"

        if stream is None:
            self.status_msg = "No stream available"
            return "none"

        if stream.sample:
            self.status_msg = ""
            return "sample"

        # History is derived from the cursor position, not pushed here: the
        # caller (advance/previous/jump/play_track) has already placed the
        # cursor. _do_play just loads and plays the resolved track.
        if self._player.running:
            self._player.stop()
        self._player.play(stream.url, start=start)
        self.now_playing = track
        self.now_playing_album = None
        self.now_playing_bio = ""
        self.quality_label = stream.quality_badge
        self._last_stream = stream
        self.playback_pos = start
        self.playback_dur = 0.0
        self.is_playing = True
        self.is_paused = False
        self.status_msg = ""
        self._fetch_now_playing_album(track)
        return "ok"

    @work
    async def _fetch_now_playing_album(self, track: Track) -> None:
        """Pull the full album for the now-playing track so the Queue's Now
        Playing block can show year/genre/label/hi-res. Best-effort: a failure
        just leaves the extra metadata blank."""
        if not track.album_id:
            return
        try:
            data = await self._client.get_album(track.album_id)
        except QobuzError:
            return
        # Bail if the track changed while the album was loading.
        if self.now_playing is None or self.now_playing.id != track.id:
            return
        album = Album.from_api(data)
        self.now_playing_album = album
        if album.artist_id:
            self._fetch_now_playing_bio(track, album.artist_id)

    @work
    async def _fetch_now_playing_bio(self, track: Track, artist_id: str) -> None:
        """Pull the now-playing artist's biography for the Now Playing hero.
        Best-effort: a failure or missing bio just leaves the space empty.
        Uses albums_limit=1 since only the bio is needed."""
        try:
            data = await self._client.get_artist(artist_id, albums_limit=1)
        except QobuzError:
            return
        if self.now_playing is None or self.now_playing.id != track.id:
            return
        self.now_playing_bio = Artist.from_api(data).biography or ""

    # ── media key sync ───────────────────────────────────────────────────────

    def watch_now_playing(self, track: Track | None) -> None:
        self._media_keys.update(
            track, self.is_playing, self.is_paused, self.playback_pos, self.playback_dur
        )
        if track and track.image_url:
            self._fetch_media_key_art(track.image_url)
        self._save_player_state()

    # ── player state persistence (survive restarts) ──────────────────────────

    def _save_player_state(self) -> None:
        if self._restoring:
            return
        track = self.now_playing
        if track is None:
            return
        with contextlib.suppress(Exception):
            save_state(
                _PLAYER_STATE_FILE,
                {
                    "track": dataclasses.asdict(track),
                    "position": float(self.playback_pos),
                    "duration": float(self.playback_dur),
                },
            )

    def _restore_player_state(self) -> None:
        """Reload the last session: the whole queue (timeline + cursor) and the
        last track + position, shown paused and unloaded. The next play/pause
        press resumes from that position."""
        data = load_state(_PLAYER_STATE_FILE)
        raw = data.get("track")
        track: Track | None = None
        if isinstance(raw, dict):
            try:
                track = Track(**raw)
            except (TypeError, ValueError):
                track = None
        timeline, cursor = self._load_queue_state()
        if track is not None and not (
            0 <= cursor < len(timeline) and timeline[cursor].id == track.id
        ):
            # The two files disagree (e.g. killed between saves): trust the
            # last-played track over a stale queue rather than mismatch them.
            timeline, cursor = [track], 0
        if not timeline:
            return
        self._restoring = True
        self._timeline, self._cursor = timeline, cursor
        if track is not None:
            position = float(data.get("position") or 0.0)
            self.playback_dur = float(data.get("duration") or track.duration or 0.0)
            self.playback_pos = position
            self.is_paused = True
            self.now_playing = track
            self._pending_resume = (track, position)
        self.queue_version += 1
        self._restoring = False

    def _load_queue_state(self) -> tuple[list[Track], int]:
        data = load_state(_QUEUE_STATE_FILE)
        raw = data.get("timeline")
        cursor = data.get("cursor", -1)
        if not isinstance(raw, list) or not isinstance(cursor, int):
            return [], -1
        tracks: list[Track] = []
        for item in raw:
            try:
                tracks.append(Track(**item))
            except (TypeError, ValueError):
                return [], -1  # one bad row → the indexes can't be trusted
        if not -1 <= cursor < len(tracks):
            return [], -1
        return tracks, cursor

    def _save_queue_state(self) -> None:
        if self._restoring:
            return
        with contextlib.suppress(Exception):
            save_state(
                _QUEUE_STATE_FILE,
                {
                    "timeline": [dataclasses.asdict(t) for t in self._timeline],
                    "cursor": self._cursor,
                },
            )

    def watch_queue_version(self, _: int) -> None:
        # Every timeline/cursor change bumps queue_version (local edits, Connect
        # mirror rebuilds alike) — persist shortly after the burst settles.
        if self._restoring or not self.is_running:
            return
        if self._queue_save_timer is not None:
            self._queue_save_timer.stop()  # type: ignore[union-attr]
        self._queue_save_timer = self.set_timer(_QUEUE_SAVE_DELAY, self._save_queue_state)

    @work
    async def _fetch_media_key_art(self, url: str) -> None:
        from ._images import fetch_image

        image = await fetch_image(url)
        if image is not None:
            self._media_keys.set_artwork(image)

    def watch_is_playing(self, playing: bool) -> None:
        self._media_keys.update(
            self.now_playing, playing, self.is_paused, self.playback_pos, self.playback_dur
        )

    def watch_is_paused(self, paused: bool) -> None:
        self._media_keys.update(
            self.now_playing, self.is_playing, paused, self.playback_pos, self.playback_dur
        )

    # ── actions ──────────────────────────────────────────────────────────────

    def action_pause(self) -> None:
        if self._ctx is not self._local_ctx:
            self.run_worker(self._connect.renderer.toggle_pause())  # type: ignore[union-attr]
            return
        if self._player.running:
            self._player.pause_toggle()
        elif self._pending_resume is not None:
            # Resume the track restored from the last session, from where it left off.
            track, position = self._pending_resume
            self.play_track(track, start=position)

    def action_previous(self) -> None:
        # Within the first few seconds, step the cursor back one track;
        # otherwise (or at the start of the timeline) restart the current track.
        if (
            self._player.running and self.playback_pos > _PREV_RESTART_THRESHOLD
        ) or self._cursor <= 0:
            self._seek_to(0.0)
            return
        self.jump_to(self._cursor - 1)

    # ── track context menu ───────────────────────────────────────────────────

    def action_goto_menu(self) -> None:
        """``g``: go to the artist or album of the track that's playing (or the
        paused one restored from the last session)."""
        from .screens.context_menu import GoToMenu

        track = self.now_playing or (self._pending_resume[0] if self._pending_resume else None)
        if track is None:
            self._flash_status("Nothing is playing")
            return
        self.push_screen(GoToMenu(track), lambda action: self._on_track_menu(action, track))

    @work
    async def action_track_menu(self) -> None:
        """``i``: the menu for what's highlighted in the focused widget. A track
        row gets the track actions; an album (a grid card or the album page on
        show) adds its favourite toggle; an artist (a card, or the artist page
        on show) adds follow/unfollow. Nothing to act on: no menu."""
        from .screens.context_menu import ContextMenu, album_option, artist_option, track_options
        from .screens.queue import QueueTrackRow

        focused = self.focused
        if focused is None:
            return
        row = focused.highlighted_child if isinstance(focused, ListView) else None
        track: Track | None = getattr(row, "track", None)
        album, artist = self._menu_subjects(focused)
        try:
            options = []
            if track is not None:
                fav = str(track.id) in await self.ensure_favorite_ids()
                options += track_options(favorite=fav, in_queue=isinstance(row, QueueTrackRow))
            if album is not None:
                fav_albums = await self.ensure_favorite_albums()
                options.append(album_option(any(a.id == album.id for a in fav_albums)))
            if artist is not None:
                followed = await self.ensure_favorite_artists()
                options.append(artist_option(any(a.id == artist.id for a in followed)))
        except QobuzError:
            self._flash_status("Couldn't load favourites")
            return
        if not options or self.focused is not focused:  # focus moved while loading
            return
        if track is not None:
            title, subtitle = track.display_title, track.artist
        elif album is not None:
            title, subtitle = album.title, album.artist
        else:
            title, subtitle = artist.name, "Artist"  # type: ignore[union-attr]
        self.push_screen(
            ContextMenu(title, subtitle, options),
            lambda action: self._on_track_menu(action, track, album, artist, row),
        )

    @staticmethod
    def _menu_subjects(focused: Widget) -> tuple[Album | None, Artist | None]:
        """The album and the artist the ``i`` menu can act on: the selected
        card or row first, then the album panel and artist page the focus
        sits in."""
        from .screens.album_detail import AlbumDetailPanel
        from .screens.artist_detail import AlbumGrid, ArtistGrid

        album: Album | None = None
        artist: Artist | None = None
        # A grid's selected card, or a Search result row.
        item = None
        if isinstance(focused, AlbumGrid | ArtistGrid):
            item = focused.selected_card()
        elif isinstance(focused, ListView):
            item = focused.highlighted_child
        if isinstance(getattr(item, "album", None), Album):
            album = item.album  # type: ignore[union-attr]
        if isinstance(getattr(item, "artist", None), Artist):
            artist = item.artist  # type: ignore[union-attr]
        for node in focused.ancestors_with_self:
            if album is None and isinstance(node, AlbumDetailPanel):
                album = node.album
            if artist is None:
                artist = getattr(node, "page_artist", None)
        return album, artist

    def _on_track_menu(
        self,
        action: str | None,
        track: Track | None,
        album: Album | None = None,
        artist: Artist | None = None,
        row: object = None,
    ) -> None:
        if action == "favorite_album" and album is not None:
            self.run_worker(self.toggle_favorite_album(album))
        elif action == "follow_artist" and artist is not None:
            self.run_worker(self.toggle_follow_artist(artist))
        elif track is None:
            return
        elif action == "favorite":
            self._toggle_favorite_from_menu(track, row)
        elif action == "remove_queue":
            if self.remove_from_queue(track):
                self._flash_status(f"Removed from queue: {track.display_title}")
        elif action == "play_next":
            if self.queue_next(track):
                self._flash_status(f"Playing next: {track.display_title}")
        elif action == "add_queue":
            if self.queue_last(track):
                self._flash_status(f"Added to queue: {track.display_title}")
        elif action == "radio":
            self.play_track(track)
            self._start_radio(track)
        elif action == "artist":
            self._open_artist(track)
        elif action == "album":
            self._open_album(track)

    @work
    async def _toggle_favorite_from_menu(self, track: Track, row: object) -> None:
        was = str(track.id) in await self.ensure_favorite_ids()
        on = await self.toggle_favorite(track)
        if on == was:
            return  # failed; toggle_favorite said so
        if hasattr(row, "set_favorite"):
            row.set_favorite(on)  # the row's heart; other lists update via the caches
        verb = "Added to" if on else "Removed from"
        self._flash_status(f"{verb} favourites: {track.display_title}")

    @work
    async def _open_album(self, track: Track) -> None:
        from .screens.album_detail import AlbumScreen

        album_id, title = track.album_id, track.album
        if not album_id:
            # Tracks saved before album ids were backfilled (an older queue)
            # don't carry one: look it up.
            with contextlib.suppress(QobuzError):
                info = (await self._client.get_track(str(track.id))).get("album") or {}
                album_id, title = str(info.get("id") or ""), info.get("title") or title
        if not album_id:
            self._flash_status("No album for this track")
            return
        album = Album(
            id=album_id,
            title=title,
            artist=track.artist,
            year=None,
            tracks_count=0,
            image_url=track.image_url,
        )
        self.push_screen(AlbumScreen(album, source=self._origin()[1]))

    @work
    async def _open_artist(self, track: Track) -> None:
        from .screens.artist_detail import ArtistScreen

        try:
            data = await self._client.get_track(str(track.id))
        except QobuzError:
            self._flash_status("Couldn't open artist")
            return
        album = data.get("album") or {}
        artist = album.get("artist") or data.get("performer") or {}
        artist_id = artist.get("id")
        if not artist_id:
            self._flash_status("Artist not available")
            return
        tab_id, name = self._origin()
        self.push_screen(ArtistScreen(str(artist_id), source=name, back_tab=tab_id))

    def _origin(self) -> tuple[str, str]:
        """Where a page opened from the ``i``/``g`` menu comes from: ``(tab id,
        breadcrumb text)``. The id is the active tab of the main screen (the
        artist page's Escape target; pushed pages have no tab bar of their own).
        The text names the page underneath: an album or artist page, the album
        or artist open inside a tab, or else the tab itself."""
        names = dict(_TABS)
        base = self.screen_stack[0]
        try:
            tab = base.query_one("#nav-tabs", Tabs).active
        except NoMatches:
            tab = ""
        tab = tab if tab in names else "tracks"
        title = getattr(self.screen, "page_title", None)
        if title is None and self.screen is base:
            with contextlib.suppress(NoMatches):
                title = getattr(base.query_one(f"#view-{tab}"), "page_title", None)
        return tab, title or names[tab]

    def action_seek_back(self) -> None:
        if self._ctx is not self._local_ctx:
            self._seek_to(max(0.0, self.playback_pos - 10.0))
        else:
            self._player.seek(-10.0)

    def action_seek_fwd(self) -> None:
        if self._ctx is not self._local_ctx:
            self._seek_to(self.playback_pos + 10.0)
        else:
            self._player.seek(10.0)

    # ── Qobuz Connect (connect/, ui/play_context.py) ─────────────────────────

    def action_toggle_connect(self) -> None:
        on = self.connect_state == "off"
        set_connect_enabled(on)
        if on:
            self._start_connect()
            self._flash_status("Qobuz Connect on — pick this device in the Qobuz app")
        else:
            self._stop_connect()
            self._flash_status("Qobuz Connect off")

    def _start_connect(self) -> None:
        if self._connect is not None:
            return
        from ..connect import acquire_instance_lock
        from ..store import _path as store_path

        self._connect_lock = acquire_instance_lock(str(store_path(_CONNECT_LOCK_FILE)))
        if self._connect_lock is None:
            self.connect_state = "off"
            self._flash_status(
                "Qobuz Connect is already on in another qobit on this machine", secs=6.0
            )
            return
        self._connect = ConnectPlayContext(self)
        self.connect_state = "connecting"
        self.run_worker(self._connect.session.run(), group="connect", exclusive=True)

    def _stop_connect(self) -> None:
        ctx, self._connect = self._connect, None
        if ctx is None:
            return
        ctx.session.stop()
        self.workers.cancel_group(self, "connect")
        if self._connect_lock is not None:
            self._connect_lock.close()  # releases the flock
            self._connect_lock = None
        # Leaving while active keeps the music going locally: the mirrored
        # timeline simply becomes the local one.
        self.connect_state = "off"

    def _connect_tick(self) -> None:
        """Periodic state report while playing: controllers extrapolate the
        position from the last report, so keep it fresh."""
        ctx = self._connect
        if ctx is not None and ctx.active and self.is_playing and not self.is_paused:
            ctx.renderer.report()

    def _refresh_truecolor_filter(self, theme: object) -> None:
        if not getattr(self, "_transparent", False) or self.native_ansi_color:
            super()._refresh_truecolor_filter(theme)  # type: ignore[misc]
            return
        for index, flt in enumerate(self._filters):
            if isinstance(flt, _ANSIToTruecolor):
                self._filters[index] = _TransparentANSIToTruecolor(theme, enabled=True)
                return

    def action_toggle_background(self) -> None:
        self._transparent = not getattr(self, "_transparent", False)
        for screen in self.screen_stack:
            screen.set_class(self._transparent, "-transparent")
        for index, flt in enumerate(self._filters):
            if isinstance(flt, (_ANSIToTruecolor, _TransparentANSIToTruecolor)):
                theme = flt._terminal_theme
                if self._transparent:
                    self._filters[index] = _TransparentANSIToTruecolor(theme, enabled=True)
                else:
                    self._filters[index] = _ANSIToTruecolor(theme, enabled=True)
                break
        set_transparent_background(self._transparent)
        # self.refresh(layout=True) alone leaves stale `background: transparent`
        # (Textual's own alpha=0, not the `ansi_default` this toggle needs) on
        # any widget Textual hasn't recomputed styles for yet at the moment of
        # the toggle — reproducibly so for widgets on a tab other than the
        # active one, and even for always-visible ones (TransportBar) if the
        # toggle fires before the first layout settles. refresh_css() forces a
        # full stylesheet re-apply across every screen, not just the active one.
        self.refresh_css()

    async def on_unmount(self) -> None:
        from ._images import close_client

        self._save_player_state()
        self._save_queue_state()
        self._stop_connect()
        self._media_keys.close()
        self._player.stop()
        await self._client.close()
        await close_client()

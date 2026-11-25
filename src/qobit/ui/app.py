from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from collections.abc import Callable
from functools import lru_cache, partial
from typing import IO, Any, cast

from rich.color import Color as _RichColor
from rich.color import ColorType as _ColorType
from rich.style import Style as _RichStyle
from rich.terminal_theme import TerminalTheme
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.css.query import NoMatches
from textual.filter import NO_DIM, dim_color
from textual.filter import ANSIToTruecolor as _ANSIToTruecolor
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import ContentSwitcher, ListView, Tab, Tabs
from textual.worker import get_current_worker

from ..audio.media_keys import MediaKeys
from ..audio.player import MpvPlayer
from ..audio.verify import Signal, check_signal
from ..audio.watch import PlayerWatch
from ..config import (
    get_account,
    get_animations,
    get_audio_device,
    get_connect_enabled,
    get_gapless,
    get_image_mode,
    get_lyrics,
    get_lyrics_panel,
    get_oauth_session,
    get_radio_mode,
    get_saved_theme,
    get_saved_volume,
    get_shuffle,
    get_software_volume_enabled,
    get_transparent_background,
    save_oauth_session,
    set_animations,
    set_connect_enabled,
    set_gapless,
    set_lyrics,
    set_lyrics_panel,
    set_radio_mode,
    set_saved_theme,
    set_saved_volume,
    set_shuffle,
    set_software_volume_enabled,
    set_transparent_background,
    token_location,
)
from ..config import set_image_mode as save_image_mode
from ..library import Change, Library, LibraryError
from ..lyrics import Lyrics
from ..qobuz.client import AuthExpiredError, QobuzClient, QobuzError
from ..qobuz.models import Album, Artist, NoStreamError, StreamUrl, Track
from ..qobuz.streams import StreamResolver
from ..qobuz.suggestions import SuggestionService
from ..store import load as load_state
from ..store import save as save_state
from . import lyrics_controller
from ._images import IMAGE_MODES, image_mode, set_image_mode
from ._now_playing import tick
from .app_css import BASE_CSS, QOBIT_CSS
from .gapless import GaplessQueue
from .lyrics_controller import LyricsController
from .nav import (
    TAB_NAMES,
    TABBAR_FULL_WIDTH,
    TABS,
    Crumb,
    QobitFooter,
    Wordmark,
    page_screens,
    tab_label,
)
from .play_context import UNPLAYABLE, ConnectPlayContext, LocalPlayContext, unplayable_notice
from .screens.albums import AlbumsView
from .screens.artists import ArtistsView
from .screens.mixes import MixesView
from .screens.playlists import PlaylistsView
from .screens.queue import QueueView
from .screens.search import SearchView
from .screens.tracks import TracksView
from .themes import CYBERDECK_THEME, QOBIT_THEMES, qobit_variable_defaults
from .widgets.art import Art
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


# A palette entry: (label, callback, help[, other names to search by]).
Command = tuple[str, Callable[[], Any], str] | tuple[str, Callable[[], Any], str, str]


class QobitCommands(Provider):
    def _commands(self) -> list[Command]:
        """(label, callback, help[, other names to search by])."""
        app = cast("QobitApp", self.app)
        account: Command
        if app._client.user_auth_token:
            name = get_account().get("name")
            account = (
                "Log out of Qobuz",
                app.action_logout,
                (f"Logged in as {name}. " if name else "")
                + "Erases the session, queue, history, mixes and cached covers "
                "from this machine (settings stay)",
            )
        else:
            account = ("Log in to Qobuz", app.action_login, "Opens the login dialog")
        return [
            account,
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
            *app.image_mode_commands(),
            (
                f"Animations: turn {'off' if app.animations else 'on'}",
                app.action_toggle_animations,
                "The wordmark's colour cycle and the playing track's bar meter. "
                "Off saves CPU on slow machines",
                "motion meter wordmark low power cpu",
            ),
            (
                f"Shuffle: turn {'off' if app.shuffle else 'on'}",
                app.action_toggle_shuffle,
                "Put Up Next in random order, and shuffle what you start from a list "
                "or add to the queue. Turning it off stops shuffling; the queue stays as is",
                "random",
            ),
            (
                f"Lyrics: turn {'off' if app.lyrics_enabled else 'on'}",
                app.action_toggle_lyrics,
                "Show the lyrics of the playing track (l). Each lookup sends its artist, "
                "title, album and length to LRCLIB",
                "words karaoke lrclib",
            ),
            (
                f"Gapless playback: turn {'off' if app.gapless else 'on'}",
                app.action_toggle_gapless,
                "Play the next track with no gap: it is queued in mpv shortly before "
                "the end. A change of sample rate still reopens the output",
                "gap less continuous transition",
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
        words = query.lower().split()
        for label, callback, help_text, *aliases in self._commands():
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), callback, help=help_text)
            elif words and all(any(w in a for a in aliases) for w in words):
                # Found by another name ("cover" finds "Images: …"), below the
                # label matches. Plain words, not fuzzy: fuzzy matching on the
                # descriptions found half the palette.
                yield Hit(0.5, label, callback, help=help_text)

    async def discover(self) -> Hits:
        for label, callback, help_text, *_aliases in self._commands():
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

# The playing rows' bar meter advances this often.
_BEAT_SECS = 0.18

# Most recently-played tracks kept in the session history (oldest dropped first).
_HISTORY_MAX = 100
# Pressing "previous" past this many seconds into a track restarts it; before
# it, steps back to the previous track in history (standard media convention).
_PREV_RESTART_THRESHOLD = 3.0


class QobitApp(App[None]):
    TITLE = "qobit"
    COMMANDS = App.COMMANDS | {QobitCommands}
    CSS = BASE_CSS + QOBIT_CSS

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "pause", "Pause"),
        # The footer hides keys from the end when it is short: the ones that
        # matter most come first (the seek keys are on the mouse and `[` `]`).
        Binding("l", "lyrics", "Lyrics"),
        Binding("R", "start_radio", "Radio"),
        Binding("S", "toggle_shuffle", "Shuffle"),
        Binding("[", "seek_back", "◀10s"),
        Binding("]", "seek_fwd", "10s▶"),
        Binding("p", "go_to_playing", "Playing"),
        Binding("L", "lyrics_full", "Lyrics", show=False),
        # Listed in the footer only on the lyrics page (its own bindings): the
        # Playback footer has no room for three more keys.
        Binding("comma", "lyrics_earlier", "Sync ◀0.5s", show=False),
        Binding("full_stop", "lyrics_later", "Sync 0.5s▶", show=False),
        Binding("x", "lyrics_reject", "Wrong lyrics", show=False),
        Binding("i", "track_menu", "Menu", show=False),
        Binding("g", "goto_menu", "Go to", show=False),
        # Software volume (off by default; see action_toggle_software_volume).
        # The first pair shows in the footer only while it's on (check_action);
        # while it's off the key falls through to the hidden second pair,
        # which explains how to turn it on.
        Binding("minus", "volume_down", "Vol-"),
        Binding("equals_sign", "volume_up", "Vol+"),
        Binding("minus,equals_sign", "volume_off_hint", show=False),
        # 1-7 match the tab bar's "[N]" labels (generated from TABS); escape
        # brings focus to Tabs from anywhere.
        *[
            Binding(str(n), f"switch_tab('{tid}')", name, show=False)
            for n, (tid, name) in enumerate(TABS, 1)
        ],
        # priority=True so these fire even when an Input has focus. "Back"
        # applies (and shows in the footer) only when there's somewhere to go
        # back to; otherwise Escape falls through to focusing the tab bar.
        Binding("escape", "back", "Back", priority=True),
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
    gapless: reactive[bool] = reactive(True)
    shuffle: reactive[bool] = reactive(False)
    # Lyrics (see _lyrics_track_changed): on/off, what the lookup found for the
    # playing track ("idle", "off", "loading", "found", "instrumental", "none"
    # or "failed"), the line being sung, a counter widgets redraw on, and
    # whether the Playback tab's right panel shows them instead of the hero.
    lyrics_enabled: reactive[bool] = reactive(True)
    lyrics_status: reactive[str] = reactive("idle")
    lyrics_line: reactive[int] = reactive(-1)
    lyrics_version: reactive[int] = reactive(0)
    lyrics_panel: reactive[bool] = reactive(False)
    # The wordmark's colour cycle and the bar meter: config `animations`
    # (palette), off as well with TEXTUAL_ANIMATIONS=none. Each frame redraws,
    # which costs on a slow CPU (a Raspberry Pi) for decoration only.
    animations: reactive[bool] = reactive(True)
    # Software volume (see action_toggle_software_volume): off by default,
    # since this is the one control that can take the signal path out of
    # bit-perfect. `volume` is meaningless while disabled; TransportBar only
    # surfaces it when software_volume_enabled and volume != 100.
    software_volume_enabled: reactive[bool] = reactive(False)
    volume: reactive[int] = reactive(100)
    # Qobuz Connect: "off" | "connecting" | "ready" (visible in the apps) |
    # "active" (the apps' chosen device — server owns the queue) | "offline".
    connect_state: reactive[str] = reactive("off")
    # Bumped whenever a navigation level changes (a detail opened or closed,
    # a page's title known): Breadcrumbs watch it (see nav.py).
    nav_version: reactive[int] = reactive(0)
    # Bit-perfect verdict for the playing track (audio.verify.check_signal):
    # None until checked (a couple of seconds in) and after a track change.
    signal: reactive[Signal | None] = reactive(None)

    def __init__(
        self,
        connect: bool | None = None,
        images: str | None = None,
        gapless: bool | None = None,
        shuffle: bool | None = None,
        lyrics: bool | None = None,
    ) -> None:
        """``connect`` overrides the saved Qobuz Connect setting for this run
        (``qobit --connect-on``), ``images`` the cover art mode (``qobit
        --images``), ``gapless`` and ``shuffle`` (``--[no-]gapless``,
        ``--[no-]shuffle``, ``--[no-]lyrics``); None = use the saved setting."""
        super().__init__()
        self.gapless = get_gapless() if gapless is None else gapless
        self.shuffle = get_shuffle() if shuffle is None else shuffle
        self.lyrics_enabled = get_lyrics() if lyrics is None else lyrics
        self.lyrics_panel = get_lyrics_panel()
        self._image_setting = images or get_image_mode()
        set_image_mode(self._image_setting)  # before anything draws
        for theme in QOBIT_THEMES:
            self.register_theme(theme)
        # The theme before anything is mounted: set in on_mount, it restyled
        # every widget of every tab (a quarter of the startup's CPU time).
        saved_theme = get_saved_theme()
        if saved_theme and saved_theme in self.available_themes:
            self.theme = saved_theme
        else:
            # No saved preference (first launch, or an invalid/removed theme
            # name) — default to qobit's own look rather than a generic
            # Textual built-in. Still just an ordinary theme choice: `^p` →
            # Theme switches away from it like any other.
            self.theme = CYBERDECK_THEME.name
        self._connect_wanted = get_connect_enabled() if connect is None else connect
        self._connect: ConnectPlayContext | None = None
        self._connect_lock: IO[str] | None = None  # flock'd file while Connect is on
        self._local_ctx = LocalPlayContext(self)
        self._client = QobuzClient()
        self.streams = StreamResolver(self._client)
        self.suggestions = SuggestionService(self._client)
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
        # Favourites, followed artists and playlists: loaded once, shared by the
        # tabs and the `i` menu, kept in step by the toggles (library.py).
        self.library = Library(
            self._client,
            account_key=lambda: self._account_key(),
            run_worker=lambda work, **kw: self.run_worker(work, **kw),
            on_change=lambda change: self._on_library_change(change),
        )
        # Prevents two concurrent _advance_queue calls from both seeing an empty
        # queue and both fetching radio + starting playback.
        self._advance_lock = asyncio.Lock()
        # Maps track_id → last successful stream quality so _do_play tries the
        # known-good format first instead of always starting at FLAC_24_192.
        # Bumped by every _play_stream call: a call whose stream resolved after
        # a newer one started is stale and must not touch the player.
        self._play_gen = 0
        # The stream _play_stream last started (format/rate/depth) — the Connect
        # renderer reports it to the apps.
        self._last_stream: StreamUrl | None = None
        self.gapless_queue = GaplessQueue(
            self,  # type: ignore[arg-type]
            player=lambda: self._player,
            streams=self.streams,
            fetch_radio=lambda seed: self._fetch_radio(seed),
            advance=lambda: self._advance_queue(),
        )
        self.lyrics_controller = LyricsController(
            self,  # type: ignore[arg-type]
            next_track=lambda: self.next_track() if self.is_local else None,
            flash=self._flash_status,
        )
        self._flash_timer: Timer | None = None
        self._queue_save_timer: Timer | None = None
        self._media_keys = MediaKeys(
            on_play_pause=lambda: self.call_from_thread(self.action_pause),
            on_next=lambda: self.call_from_thread(self._advance_queue),
            on_previous=lambda: self.call_from_thread(self.action_previous),
        )
        app_id, token, secrets = get_oauth_session()
        if app_id and token and secrets:
            self._client.restore_session(app_id, token, secrets)
        # A 401 anywhere (session expired or revoked) opens the login dialog
        # once, instead of every tab showing its own error. The client calls
        # this from a request on the event loop: defer, never push mid-request.
        self._client.on_auth_expired = lambda: self.call_later(
            self.request_login, "Your Qobuz session has expired. Log in again to keep listening."
        )
        self._login_open = False

    def compose(self) -> ComposeResult:
        yield Tabs(
            *[Tab(tab_label(n, name, False), id=tid) for n, (tid, name) in enumerate(TABS, 1)],
            id="nav-tabs",
            active="queue",
        )
        yield Wordmark()
        with ContentSwitcher(initial="view-queue"):
            yield PlaylistsView(id="view-playlists")
            yield TracksView(id="view-tracks")
            yield ArtistsView(id="view-artists")
            yield AlbumsView(id="view-albums")
            yield MixesView(id="view-mixes")
            yield SearchView(id="view-search")
            yield QueueView(id="view-queue")
        yield TransportBar()
        yield QobitFooter()

    async def on_mount(self) -> None:
        self._poll_player()
        # The playing rows' bar meter (_now_playing.tick): decorative, idle
        # (one flag check) while nothing plays.
        self.animations = get_animations() and self.animation_level != "none"
        self.set_interval(
            _BEAT_SECS, lambda: tick(self.animations and self.is_playing and not self.is_paused)
        )
        # Persist any subsequent theme changes (skip the initial/default ones).
        self._theme_ready = True
        if get_transparent_background():
            self.action_toggle_background()
        self.radio_mode = get_radio_mode()
        self.software_volume_enabled = _sw_vol_on = get_software_volume_enabled()
        self.volume = get_saved_volume() if _sw_vol_on else 100
        self._fit_tab_labels(self.size.width)
        self._restore_player_state()
        self.set_interval(5.0, self._connect_tick)
        self._prune_image_cache()
        # `qobit logout` in a terminal refuses while this is held (this TUI
        # would write the queue straight back).
        from ..account_data import hold_running_lock

        self._running_lock = hold_running_lock()
        if self._client.user_auth_token:
            self._go_online()
        elif token_location() == "keyring":
            self.call_after_refresh(
                self.request_login,
                "Your session is in the system keyring, which isn't reachable here "
                "(no desktop session?). Log in to use qobit here.",
            )
        else:
            self.call_after_refresh(self.request_login)

    def _go_online(self) -> None:
        """Everything that needs a session, run at startup or after a login."""
        if self.now_playing is not None:
            # The restored track's album details and artist bio for the hero
            # (playing a track fetches them; a restore didn't).
            self._fetch_now_playing_album(self.now_playing)
        if self._connect_wanted:
            self._start_connect()
        self._warm_favorite_ids()

    # ── login ────────────────────────────────────────────────────────────────

    def request_login(self, reason: str | None = None) -> None:
        """Open the login dialog (once: a burst of 401s opens one)."""
        if self._login_open:
            return
        from .screens.login import LoginScreen

        self._login_open = True
        self.push_screen(LoginScreen(self._client, reason), self._on_login_closed)

    async def _on_login_closed(self, logged_in: bool | None) -> None:
        self._login_open = False
        if not logged_in:
            if not self._client.user_auth_token:
                self.status_msg = "Not logged in: ^p → Log in to Qobuz"
            return
        # Someone else logged in (after an expiry: no logout in between): the
        # previous account's queue, mixes and library mustn't carry over.
        previous = get_account().get("name")
        current = (self._client.account or {}).get("name")
        if previous and current and previous != current:
            await self._erase_account()
        save_oauth_session(*self._client.session(), account=self._client.account)
        name = (self._client.account or {}).get("name")
        self._flash_status(f"Logged in as {name}" if name else "Logged in")
        self._reload_after_login()

    def _reload_after_login(self) -> None:
        """Refetch what failed (or belonged to another account): the
        favourites caches and the library tabs, which load on activation."""
        self.library.reset()
        for tab_view in self.screen_stack[0].query("ContentSwitcher > *"):
            if hasattr(tab_view, "_loaded"):
                tab_view._loaded = False
        current = self._base_view()
        if current is not None and hasattr(current, "activate"):
            current.activate()
        self._go_online()

    def action_login(self) -> None:
        self.request_login()

    async def action_logout(self) -> None:
        """Log out and erase what the account left on this machine. Qobuz has
        no revoke call: the token is forgotten here, not cancelled there."""
        # The session first: erasing rebuilds the views, and the new ones ask
        # for the library (the queue's hearts). With the token still set,
        # that fetched it again and saved a fresh copy of what was erased.
        self._client.user_auth_token = None
        self._client.account = None
        await self._erase_account()
        self.request_login("Logged out. Log in again, or press Esc to stay logged out.")

    async def _erase_account(self) -> None:
        """Stop, empty memory, delete the account's files, rebuild the UI.
        Memory first: the watchers would otherwise write the queue back."""
        from ..account_data import erase_account_data
        from ._images import _IMAGE_CACHE

        self._stop_connect()
        self._player.stop()
        self._pending_resume = None
        self._restoring = True  # no player/queue saves while emptying
        try:
            self._local_ctx.reset()
            self.now_playing = None
            self.now_playing_album = None
            self.now_playing_bio = ""
            self.is_playing = self.is_paused = False
            self.playback_pos = self.playback_dur = 0.0
            self.status_msg = ""
        finally:
            self._restoring = False
        if self._queue_save_timer is not None:
            self._queue_save_timer.stop()
            self._queue_save_timer = None
        self.library.reset()
        self._client.clear_caches()
        _IMAGE_CACHE.clear()
        erase_account_data()
        while len(self.screen_stack) > 1:
            self.pop_screen()
        # Every view (library, mixes, search, pages' trail) rebuilt empty.
        await self.recompose()
        self._fit_tab_labels(self.size.width)

    @work(thread=True)
    def _prune_image_cache(self) -> None:
        from ._images import prune_disk_cache

        prune_disk_cache()

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """qobit's theme variables (themes.py) for themes that don't set them,
        Textual's built-ins included, computed from the current theme on every
        switch (selection and accent-text colours checked for contrast)."""
        return qobit_variable_defaults(self.current_theme)

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
        self.nav_changed()

    def on_resize(self, event: events.Resize) -> None:
        self._fit_tab_labels(event.size.width)
        for screen in self.screen_stack:
            for mark in screen.query(Wordmark):
                mark.fit(event.size.width)

    def _fit_tab_labels(self, width: int) -> None:
        compact = width < TABBAR_FULL_WIDTH
        if compact == getattr(self, "_tabs_compact", None):
            return
        self._tabs_compact = compact
        for n, (tid, name) in enumerate(TABS, 1):
            # Not composed yet on the first resize; on_mount runs this again.
            with contextlib.suppress(Exception):
                self.query_one(f"#{tid}", Tab).label = tab_label(n, name, compact)

    def action_switch_tab(self, tab_id: str) -> None:
        """``1``–``7`` or a click on a tab: close any pages, then switch. (With
        pages open it used to switch the hidden tab underneath them.)"""
        self._close_pages()
        self.screen_stack[0].query_one("#nav-tabs", Tabs).active = tab_id

    # ── navigation trail (see nav.py) ────────────────────────────────────────

    def base_tab(self) -> str:
        """The main screen's active tab id."""
        try:
            tab = self.screen_stack[0].query_one("#nav-tabs", Tabs).active
        except NoMatches:
            tab = ""
        return tab if tab in TAB_NAMES else "queue"

    def _base_view(self) -> Widget | None:
        try:
            return self.screen_stack[0].query_one(f"#view-{self.base_tab()}")
        except NoMatches:
            return None

    def nav_changed(self) -> None:
        """A level changed: redraw the breadcrumbs (and the footer's Back)."""
        self.nav_version += 1
        self.refresh_bindings()

    def nav_trail(self, upto: object | None = None) -> list[Crumb]:
        """The trail from the active tab to ``upto`` (a screen; default the
        top one): the tab, its view's levels, then each page's levels. Every
        crumb but the last goes back to its level."""
        levels: list[tuple[str, object, int]] = [(TAB_NAMES[self.base_tab()], None, 0)]
        view = self._base_view()
        for depth, label in enumerate(getattr(view, "nav_levels", list)(), 1):
            levels.append((label, None, depth))
        for page in page_screens(self):
            if upto is not None and upto is self.screen_stack[0]:
                break
            for depth, label in enumerate(getattr(page, "nav_levels", list)()):
                levels.append((label, page, depth))
            if page is upto:
                break
        return [
            Crumb(label, None if i == len(levels) - 1 else self._goer(page, depth))
            for i, (label, page, depth) in enumerate(levels)
        ]

    def _goer(self, page: object | None, depth: int):
        def go() -> None:
            self._close_pages(above=page)
            target = page if page is not None else self._base_view()
            if hasattr(target, "nav_to"):
                target.nav_to(depth)
            self.nav_changed()

        return go

    def _close_pages(self, above: object | None = None) -> None:
        """Pop pushed pages down to ``above`` (None: all of them)."""
        while len(self.screen_stack) > 1 and self.screen is not above:
            self.pop_screen()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("volume_down", "volume_up"):
            return self.software_volume_enabled
        if action == "back":
            return self._can_go_back()
        if action in ("lyrics_earlier", "lyrics_later"):
            return self._lyrics_visible() and self.lyrics is not None and self.lyrics.synced
        if action == "lyrics_reject":
            return self._lyrics_visible() and self.lyrics_status == "found"
        if action == "toggle_shuffle":
            return self._ctx is self._local_ctx  # under Connect the server owns the order
        if action == "go_to_playing":
            return self._playing_host() is not None
        return True

    def _playing_host(self) -> object | None:
        """The track list in use (the focused one's view) that can show where
        the playing track is, if a track is playing."""
        if self.now_playing is None or self.focused is None:
            return None
        return next(
            (w for w in self.focused.ancestors_with_self if hasattr(w, "show_playing")), None
        )

    def action_go_to_playing(self) -> None:
        """`p`: the cursor on the playing track in the list you're in."""
        host = self._playing_host()
        if host is not None and not host.show_playing():  # type: ignore[attr-defined]
            self._flash_status("The playing track isn't in this list")

    def _active_view(self) -> Widget | None:
        return self._base_view()

    def _can_go_back(self) -> bool:
        """A page or menu on top, or the active tab has a detail view open or
        a filter set (each view's ``can_navigate_back``)."""
        if len(self.screen_stack) > 1:
            return True
        view = self._active_view()
        return bool(view is not None and getattr(view, "can_navigate_back", lambda: False)())

    def action_back(self) -> None:  # type: ignore[override]  # sync: Textual's is a coroutine
        """Escape: up one level of the trail. On a page with an inner level
        (an artist page showing an album) that's the page's previous level;
        else close the page (or menu). On the main screen, the tab's view."""
        if len(self.screen_stack) > 1:
            screen = self.screen
            # A screen that closes itself (dismiss runs its callback; a bare
            # pop_screen skipped the login dialog's and left it "open").
            if getattr(screen, "action_navigate_back", lambda: False)():
                self.nav_changed()
                return
            levels: list = getattr(screen, "nav_levels", list)()
            if len(levels) > 1 and hasattr(screen, "nav_to"):
                screen.nav_to(len(levels) - 2)
            else:
                self.pop_screen()
            self.nav_changed()
            return
        view = self._active_view()
        if view is not None and hasattr(view, "action_navigate_back"):
            view.action_navigate_back()
        self.nav_changed()

    def action_focus_tabs(self) -> None:
        self.query_one("#nav-tabs", Tabs).focus()

    def _focus_search_input(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#search-input").focus()

    # ── the library (library.py) ─────────────────────────────────────────────
    #
    # The lists, their loading and their disk copy are `app.library`. These are
    # what the app does about them: the user-facing toggles, and redrawing what
    # shows a list when one changed.

    def _account_key(self) -> str:
        """Whose library the disk copy is: the saved account's name."""
        return (self._client.account or {}).get("name") or get_account().get("name") or ""

    @work
    async def _warm_favorite_ids(self) -> None:
        await self.library.warm()

    def _on_library_change(self, change: Change) -> None:
        """A list changed (a toggle, or a background refresh brought a different
        one): update what shows it."""
        from .screens.playlists import PlaylistsView

        base = self.screen_stack[0]
        if change.kind == "tracks":
            with contextlib.suppress(Exception):
                if change.cause == "toggle" and change.item_id is not None:
                    self.query_one(TracksView).favorite_changed(change.item_id, bool(change.state))
                else:
                    base.query_one(TracksView).library_changed()
            self.queue_version += 1  # the hearts in every track list
        elif change.kind == "albums":
            self._refresh_hearts()
            with contextlib.suppress(Exception):
                base.query_one(AlbumsView).favorites_changed()
        elif change.kind == "artists":
            self._refresh_hearts()
            self._refresh_followed_tiles()
            with contextlib.suppress(Exception):
                base.query_one(ArtistsView).favorites_changed()
        else:
            with contextlib.suppress(Exception):
                base.query_one(PlaylistsView).library_changed()

    async def toggle_favorite(self, track: Track) -> bool:
        """Add/remove a track from the user's Qobuz favourites. Returns the new
        favourite state (unchanged on error)."""
        try:
            return await self.library.toggle_track(track)
        except LibraryError as e:
            self._flash_status(str(e))
            return str(track.id) in (self.library.track_ids or ())

    async def toggle_favorite_album(self, album: Album) -> None:
        try:
            on = await self.library.toggle_album(album)
        except LibraryError as e:
            self._flash_status(str(e))
            return
        self._flash_status(
            f"Added to favourites: {album.title}"
            if on
            else f"Removed from favourites: {album.title}"
        )

    async def toggle_follow_artist(self, artist: Artist) -> None:
        try:
            on = await self.library.toggle_artist(artist)
        except LibraryError as e:
            self._flash_status(str(e))
            return
        self._flash_status(f"Following {artist.name}" if on else f"Unfollowed {artist.name}")

    def _refresh_hearts(self) -> None:
        """Redraw the ♥ on every open album panel and artist header (pages
        included) after a favourite or follow changed."""
        from .screens.album_detail import AlbumDetailPanel
        from .screens.artist_detail import ArtistHeader

        for screen in self.screen_stack:
            for widget in (*screen.query(AlbumDetailPanel), *screen.query(ArtistHeader)):
                with contextlib.suppress(NoMatches):  # not composed yet
                    widget.refresh_heart()

    def _refresh_followed_tiles(self) -> None:
        """The ♥ on artist tiles (Similar artists) after the followed artists
        changed: a toggle, or a background refresh of the library."""
        from .screens.cards import ArtistCard

        followed = {a.id for a in self.library.artists or ()}
        for screen in self.screen_stack:
            for card in screen.query(ArtistCard):
                card.set_followed(card.artist.id in followed)

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
        watch = PlayerWatch(self._player)
        while not worker.is_cancelled:
            reading = watch.read(self.is_playing)
            if reading.state == "playing":
                if reading.moved_on:
                    # mpv went on to the track queued behind the last one.
                    self.call_from_thread(self.gapless_queue.on_transition)
                if reading.pos is not None:
                    self.call_from_thread(setattr, self, "playback_pos", reading.pos)
                if reading.dur is not None:
                    self.call_from_thread(setattr, self, "playback_dur", reading.dur)
                if reading.paused is not None:
                    self.call_from_thread(setattr, self, "is_paused", reading.paused)
                if not self.is_playing:
                    self.call_from_thread(setattr, self, "is_playing", True)
                # Persist position periodically so a crash/kill loses ≤5s.
                now = time.monotonic()
                if now - self._last_state_save > 5.0:
                    self._last_state_save = now
                    self.call_from_thread(self._save_player_state)
            elif reading.state == "stopped":
                self.call_from_thread(setattr, self, "is_playing", False)
                self.call_from_thread(setattr, self, "is_paused", False)
                self.call_from_thread(setattr, self, "playback_pos", 0.0)
                self.call_from_thread(setattr, self, "quality_label", "")
                if reading.natural_end:
                    self.call_from_thread(self._advance_queue)
            time.sleep(0.5)

    # ── what screens and widgets read (public, read-only) ────────────────────
    #
    # Screens and widgets read the app through these names and the reactives,
    # never through its underscore state (tests/test_ui_boundary.py). The play
    # contexts are the app's collaborators: they own the writes.

    @property
    def history(self) -> list[Track]:
        """Recently Played: everything behind the cursor."""
        return self._timeline[: self._cursor] if self._cursor >= 0 else []

    @property
    def up_next(self) -> list[Track]:
        """Up Next: everything ahead of the cursor."""
        return self._timeline[self._cursor + 1 :] if self._cursor >= 0 else []

    @property
    def current_track(self) -> Track | None:
        """The timeline's track at the cursor. advance/jump move the cursor at
        once; ``now_playing`` changes only once the stream URL resolves."""
        return self._timeline[self._cursor] if 0 <= self._cursor < len(self._timeline) else None

    @property
    def queue_position(self) -> tuple[int, int] | None:
        """(1-based place of the cursor, timeline length), None with no cursor."""
        return (self._cursor + 1, len(self._timeline)) if self._cursor >= 0 else None

    @property
    def lyrics(self) -> Lyrics | None:
        """The playing track's lyrics, None while there are none."""
        return self.lyrics_controller.lyrics

    @property
    def lyrics_offset(self) -> float:
        """The reader's timing nudge for the playing track, in seconds."""
        return self.lyrics_controller.offset

    @property
    def last_stream(self) -> StreamUrl | None:
        """The stream of the playing (or last played) track."""
        return self._last_stream

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
            self._flash_timer.stop()
        self.status_msg = msg
        self._flash_timer = self.set_timer(secs, lambda: self._clear_flash(msg))

    def _clear_flash(self, msg: str) -> None:
        if self.status_msg == msg:  # only clear if nothing newer replaced it
            self.status_msg = ""

    def _is_loaded(self, track: Track) -> bool:
        """Whether ``track`` is the one in the player (playing or paused)."""
        return self.is_playing and self.now_playing is not None and self.now_playing.id == track.id

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

    # ── cover art mode (_images.IMAGE_MODES; widgets.art.Art draws it) ─────────

    _IMAGE_MODE_HELP = {
        "auto": "Kitty graphics where the terminal supports them, colour blocks elsewhere",
        "kitty": "Sharp cover art with the Kitty graphics protocol (kitty, Ghostty)",
        "blocks": "Cover art as coloured half blocks: any terminal with true colour",
        "off": "Initials instead of cover art, and no covers downloaded: lightest on slow machines",
    }
    _IMAGE_MODE_NAMES = {
        "auto": "auto",
        "kitty": "Kitty graphics",
        "blocks": "colour blocks",
        "off": "off",
    }

    def image_mode_commands(self) -> list[Command]:
        """Palette entries for the cover art modes other than the current one
        ("Images", as `qobit --images`; "cover" and "art" find them too)."""
        current = self._IMAGE_MODE_NAMES[self._image_setting]
        if self._image_setting == "auto":
            current += f" ({self._IMAGE_MODE_NAMES[image_mode()]})"
        return [
            (
                f"Images: {self._IMAGE_MODE_NAMES[mode]}",
                partial(self.set_image_setting, mode),
                f"{self._IMAGE_MODE_HELP[mode]}. Cover art now: {current}",
                "cover art pictures artwork",
            )
            for mode in IMAGE_MODES
            if mode != self._image_setting
        ]

    def set_image_setting(self, setting: str) -> None:
        """Switch the cover art mode now and save it."""
        was = image_mode()
        self._image_setting = setting
        now = set_image_mode(setting)
        save_image_mode(setting)
        for screen in self.screen_stack:
            for art in screen.query(Art):
                art.refresh(layout=True)
        name = self._IMAGE_MODE_NAMES[setting]
        if setting == "auto":
            name += f" ({self._IMAGE_MODE_NAMES[now]})"
        # Covers skipped while off load as pages open (and with the next track).
        later = was == "off" and now != "off"
        self._flash_status(f"Images: {name}" + ("; covers load as you browse" if later else ""))

    def action_toggle_animations(self) -> None:
        self.animations = not self.animations
        set_animations(self.animations)
        self._flash_status(f"Animations {'on' if self.animations else 'off'}")

    # ── lyrics (the lookup, the line, prefetch, retry: lyrics_controller.py) ──

    def _lyrics_screen_open(self) -> bool:
        from .screens.lyrics import LyricsScreen

        return isinstance(self.screen, LyricsScreen)

    def _queue_view(self) -> Widget | None:
        """The Playback view, when it is what's on screen."""
        if len(self.screen_stack) != 1 or self.base_tab() != "queue":
            return None
        return self._base_view()

    def _lyrics_visible(self) -> bool:
        """Lyrics are on screen: their page, or the Playback tab's pane."""
        if self._lyrics_screen_open():
            return True
        view = self._queue_view()
        return view is not None and view.has_class("-lyrics")

    def watch_lyrics_panel(self, _: bool) -> None:
        self._refresh_bindings_if_shown()

    def watch_lyrics_version(self, _: int) -> None:
        self._refresh_bindings_if_shown()

    def _refresh_bindings_if_shown(self) -> None:
        """A lookup finishing while the app shuts down finds no screen to
        refresh: refresh_bindings() raises ScreenStackError, and a raising
        worker closes the app (CI saw it in a test's teardown)."""
        if self.screen_stack:
            self.refresh_bindings()

    def action_lyrics(self) -> None:
        """`l`: where the Playback tab has room for the right pane but not for
        the hero and the lyrics together, it swaps between them; anywhere else
        (they already show together, or there is no room, or another tab) the
        lyrics page opens."""
        view = self._queue_view()
        if self._lyrics_screen_open():
            self.action_back()
        elif view is not None and view.lyrics_swap():  # type: ignore[attr-defined]
            self.lyrics_panel = not self.lyrics_panel
            set_lyrics_panel(self.lyrics_panel)
        else:
            self.action_lyrics_full()

    def action_lyrics_full(self) -> None:
        """`L`: the lyrics page, from anywhere (again: back)."""
        from .screens.lyrics import LyricsScreen

        if self._lyrics_screen_open():
            self.action_back()
        elif not self.lyrics_enabled:
            self._flash_status("Lyrics are off: Ctrl+P → Lyrics")
        else:
            self.push_screen(LyricsScreen())

    def action_toggle_lyrics(self) -> None:
        self.lyrics_enabled = not self.lyrics_enabled
        set_lyrics(self.lyrics_enabled)
        self.lyrics_controller.track_changed(self.now_playing)  # looked up now, or cleared
        self._flash_status(f"Lyrics {'on' if self.lyrics_enabled else 'off'}")

    def action_lyrics_earlier(self) -> None:
        self.lyrics_controller.nudge(-lyrics_controller.STEP)

    def action_lyrics_later(self) -> None:
        self.lyrics_controller.nudge(lyrics_controller.STEP)

    def action_lyrics_reject(self) -> None:
        """`x`: these lyrics are wrong for this track."""
        self.lyrics_controller.reject()

    def action_toggle_shuffle(self) -> None:
        if self._ctx is not self._local_ctx:
            self._flash_status("Shuffle isn't available under Qobuz Connect yet")
            return
        self.shuffle = not self.shuffle
        set_shuffle(self.shuffle)
        if self.shuffle:
            self._local_ctx.shuffle_upcoming()  # Up Next now; turning off leaves it as is
        self._flash_status(f"Shuffle {'on' if self.shuffle else 'off'}")

    def action_toggle_gapless(self) -> None:
        self.gapless = not self.gapless
        set_gapless(self.gapless)
        if not self.gapless:
            self.gapless_queue.drop()  # what's queued in mpv goes: the track ends as before
        self._flash_status(f"Gapless {'on' if self.gapless else 'off'}")

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
        self.refresh_bindings()  # Vol-/Vol+ show in the footer only while on

    def action_volume_down(self) -> None:
        self._adjust_volume(-self._VOLUME_STEP)

    def action_volume_up(self) -> None:
        self._adjust_volume(self._VOLUME_STEP)

    def action_volume_off_hint(self) -> None:
        self._flash_status("Software volume is off — turn it on from the command palette (^p)")

    def _adjust_volume(self, delta: int) -> None:
        if not self.software_volume_enabled:
            return
        self.volume = max(0, min(100, self.volume + delta))
        set_saved_volume(self.volume)
        if self._player.running:
            self._player.set_volume(self.volume)
        else:
            self._player._volume = self.volume

    async def _fetch_radio(self, seed: Track) -> list[Track]:
        """Song-radio suggestions for ``seed`` (see qobuz/suggestions.py)."""
        album = self.now_playing_album
        known = album if self.now_playing is not None and self.now_playing.id == seed.id else None
        return await self.suggestions.radio(seed, self.history, known)

    async def generate_mix(self) -> list[Track]:
        """A DailyQ/WeeklyQ mix from the history and the favourites."""
        favourites: list[Track] = []
        with contextlib.suppress(Exception):
            favourites = await self.library.ensure_tracks()
        return await self.suggestions.mix(self.history, favourites)

    async def _do_play(self, track: Track, start: float = 0.0) -> None:
        """Local-context playback: start ``track``; on a preview, skip forward."""
        result = await self._play_stream(track, start)
        if result in UNPLAYABLE:
            # Never play a preview: it's a 30 s MP3, not the FLAC. Treat the track
            # as unavailable — skip forward like the official apps do, or stop
            # if nothing is queued after it. Same for a track Qobuz refuses
            # outright (restricted: no URL, no preview).
            self._flash_status(unplayable_notice(track, result))
            if self._cursor + 1 < len(self._timeline) or self.radio_mode:
                self._advance_queue()
            elif self._player.running:
                self._player.stop()

    async def _play_stream(self, track: Track, start: float = 0.0) -> str:
        """Resolve and start ``track`` in mpv. Returns "ok", "sample" (preview
        only — nothing started), "unavailable" (Qobuz refuses the track: no
        URL, no preview), "none" (no stream: request failures), "auth" (session expired)
        or "stale" (another track was requested while this one resolved; it
        took over, nothing started). Shared by both play contexts; skipping
        policy is the caller's."""
        self._play_gen += 1
        gen = self._play_gen
        self.status_msg = f"Loading {track.display_title}…"
        restricted = False
        try:
            stream = await self.streams.resolve(str(track.id))
        except AuthExpiredError:
            self.status_msg = "Session expired: log in again"
            return "auth"
        except NoStreamError:
            stream, restricted = None, True
        if gen != self._play_gen:
            # Picking A then B quickly: if A's URL arrives last it would stop B
            # and play A while the queue's cursor says B.
            return "stale"
        if restricted:
            self.status_msg = ""  # the caller flashes why
            return "unavailable"

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
        self.gapless_queue.reset()  # a new mpv: nothing is queued behind this track
        self._begin_track(track, stream, gen, start)
        return "ok"

    def _begin_track(self, track: Track, stream: StreamUrl, gen: int, start: float = 0.0) -> None:
        """What a track starting means to the app: started by _play_stream, or
        reached by mpv on its own (a gapless move, step_to_next)."""
        self.now_playing = track
        self.now_playing_album = None
        self.now_playing_bio = ""
        self._last_stream = stream  # before quality_label: its watchers read it
        self.quality_label = stream.quality_badge
        self.playback_pos = start
        self.playback_dur = 0.0
        self.is_playing = True
        self.is_paused = False
        self.status_msg = ""
        self._fetch_now_playing_album(track)
        self.signal = None
        self._verify_signal(gen, stream)

    # ── gapless: the next track queued in mpv (gapless.py) ───────────────────
    #
    # GaplessQueue holds the state and the logic; these are what it asks of the
    # app (GaplessHost).

    def next_track(self) -> Track | None:
        """The track after the cursor."""
        i = self._cursor + 1
        return self._timeline[i] if 0 <= i < len(self._timeline) else None

    @property
    def is_local(self) -> bool:
        """qobit owns the timeline (not Qobuz Connect)."""
        return self._ctx is self._local_ctx

    @property
    def advance_busy(self) -> bool:
        """An advance is already starting the next track."""
        return self._advance_lock.locked()

    def extend_timeline(self, tracks: list[Track]) -> None:
        self._timeline.extend(tracks)
        self.queue_version += 1

    def step_to_next(self, track: Track, stream: StreamUrl) -> None:
        """mpv went on to ``track`` by itself: the cursor and the transport
        follow it (nothing to start)."""
        self._cursor += 1
        self._trim_history()
        self.queue_version += 1
        self._play_gen += 1
        self._begin_track(track, stream, self._play_gen)

    def watch_playback_pos(self, _: float) -> None:
        self.gapless_queue.maybe_prefetch()
        self.lyrics_controller.update_line()
        self.lyrics_controller.maybe_prefetch()

    @work(exclusive=True, group="_verify_signal")  # a newer track cancels it
    async def _verify_signal(self, gen: int, stream: StreamUrl) -> None:
        """A couple of seconds into the track, check that mpv, and on Linux
        the DAC, run at the file's rate and depth (the transport's lamp)."""
        result = await check_signal(self._player, stream.sampling_rate, stream.bit_depth or 16)
        if gen == self._play_gen:
            self.signal = result

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
        self._refresh_bindings_if_shown()  # `p` shows only while something plays
        self._media_keys.update(
            track, self.is_playing, self.is_paused, self.playback_pos, self.playback_dur
        )
        if track and track.image_url:
            self._fetch_media_key_art(track.image_url)
        self._save_player_state()
        self.lyrics_controller.track_changed(track)

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

    def watch_connect_state(self, _: str) -> None:
        # Shuffle's key shows only while qobit owns the queue. Also set at exit
        # (_stop_connect), when no screen is left to refresh.
        if self.screen_stack:
            self.refresh_bindings()

    def watch_queue_version(self, _: int) -> None:
        # Every timeline/cursor change bumps queue_version (local edits, Connect
        # mirror rebuilds alike) — persist shortly after the burst settles.
        self.gapless_queue.check()
        if self._restoring or not self.is_running:
            return
        if self._queue_save_timer is not None:
            self._queue_save_timer.stop()
        self._queue_save_timer = self.set_timer(_QUEUE_SAVE_DELAY, self._save_queue_state)

    @work(exclusive=True, group="_fetch_media_key_art")  # a newer request cancels a stale one
    async def _fetch_media_key_art(self, url: str) -> None:
        from ._images import fetch_image

        image = await fetch_image(url, terminal=False)  # the OS widget's, even with images off
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
        from .screens.context_menu import (
            ContextMenu,
            album_artist_option,
            album_option,
            artist_option,
            track_options,
        )

        focused = self.focused
        if focused is None:
            return
        row = focused.highlighted_child if isinstance(focused, ListView) else None
        track: Track | None = getattr(row, "track", None)
        album, artist = self._menu_subjects(focused)
        try:
            options = []
            if track is not None:
                fav = str(track.id) in await self.library.ensure_track_ids()
                options += track_options(
                    favorite=fav, in_queue=getattr(row, "role", None) == "next"
                )
            if album is not None:
                fav_albums = await self.library.ensure_albums()
                options.append(album_option(any(a.id == album.id for a in fav_albums)))
                if track is None and artist is None:  # not already on its artist's page
                    options.append(album_artist_option())
            if artist is not None:
                followed = await self.library.ensure_artists()
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
        from .screens.cards import AlbumGrid, ArtistGrid

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
        elif action == "album_artist" and album is not None:
            self._open_album_artist(album)
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
            # The track already playing (or paused) keeps going: only Up Next
            # becomes the station, as with R. play_track would restart it; the
            # one restored at startup resumes where it was.
            if not self._is_loaded(track):
                resume = self._pending_resume
                start = resume[1] if resume and resume[0].id == track.id else 0.0
                self.play_track(track, start=start)
            self._start_radio(track)
        elif action == "artist":
            self._open_artist(track)
        elif action == "album":
            self._open_album(track)

    @work
    async def _toggle_favorite_from_menu(self, track: Track, row: object) -> None:
        was = str(track.id) in await self.library.ensure_track_ids()
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
        self.push_screen(AlbumScreen(album))

    @work
    async def _open_artist(self, track: Track) -> None:

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
        self.open_artist(str(artist_id))

    @work
    async def _open_album_artist(self, album: Album) -> None:
        """An album from a grid or a search may be lean (no artist id): the
        full record has it (cached by the client)."""
        artist_id = album.artist_id
        if not artist_id:
            try:
                artist_id = Album.from_api(await self._client.get_album(album.id)).artist_id
            except QobuzError:
                self._flash_status("Couldn't open artist")
                return
        if not artist_id:
            self._flash_status("Artist not available")
            return
        self.open_artist(artist_id)

    def open_artist(self, artist_id: str) -> None:
        """Open an artist page over the current one (`g`, `i`, Similar); the
        breadcrumb trail shows where it was opened from."""
        from .screens.artist_detail import ArtistScreen

        self.push_screen(ArtistScreen(artist_id))

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

    def _refresh_truecolor_filter(self, theme: TerminalTheme) -> None:
        if not getattr(self, "_transparent", False) or self.native_ansi_color:
            super()._refresh_truecolor_filter(theme)
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
        await self.lyrics_controller.aclose()
        await close_client()

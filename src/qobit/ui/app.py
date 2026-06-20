from __future__ import annotations

import asyncio
import dataclasses
import time
from functools import lru_cache

from rich.color import Color as _RichColor
from rich.color import ColorType as _ColorType
from rich.style import Style as _RichStyle
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.filter import NO_DIM, dim_color
from textual.filter import ANSIToTruecolor as _ANSIToTruecolor
from textual.reactive import reactive
from textual.widgets import ContentSwitcher, Footer, Label, ListView, Tab, Tabs
from textual.worker import get_current_worker

from ..audio.media_keys import MediaKeys
from ..audio.player import MpvPlayer
from ..config import (
    get_audio_device,
    get_oauth_session,
    get_saved_theme,
    get_transparent_background,
    set_saved_theme,
    set_transparent_background,
)
from ..qobuz.client import QobuzClient, QobuzError
from ..qobuz.models import StreamUrl, Track
from ..store import load as load_state
from ..store import save as save_state
from .screens.albums import AlbumsView
from .screens.artists import ArtistsView
from .screens.playlists import PlaylistsView
from .screens.queue import QueueView
from .screens.search import SearchView
from .screens.tracks import TracksView
from .widgets.transport import TransportBar


class _TransparentANSIToTruecolor(_ANSIToTruecolor):
    """ANSIToTruecolor that preserves ansi_default backgrounds so the
    terminal's own background (transparency/blur) shows through."""

    @lru_cache(1024)
    def truecolor_style(self, style: "_RichStyle", background: "_RichColor") -> "_RichStyle":
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
    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        label = "Draw theme background"
        score = matcher.match(label)
        if score > 0:
            app: QobitApp = self.app  # type: ignore[assignment]
            yield Hit(
                score,
                matcher.highlight(label),
                app.action_toggle_background,
                help="Toggle between theme background and terminal transparency",
            )

    async def discover(self) -> Hits:
        app: QobitApp = self.app  # type: ignore[assignment]
        yield Hit(
            0,
            "Draw theme background",
            app.action_toggle_background,
            help="Toggle between theme background and terminal transparency",
        )


_PLAYER_STATE_FILE = "player_state.json"

_TABS = [
    ("tracks", "Tracks"),
    ("artists", "Artists"),
    ("albums", "Albums"),
    ("playlists", "Playlists"),
    ("search", "Search"),
    ("queue", "Queue"),
]


class QobitApp(App[None]):
    TITLE = "qobit"
    COMMANDS = App.COMMANDS | {QobitCommands}
    CSS = """
    Screen {
        layout: vertical;
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
    SearchView, PlaylistsView, TracksView, ArtistsView, AlbumsView, QueueView {
        height: 1fr;
        margin: 0 1 1 1;
    }
    TransportBar {
        margin: 0 1;
    }
    ListItem.--highlight {
        background: $accent 35%;
    }
    ListItem.--highlight > Label {
        background: $accent 35%;
    }
    Label.-hl {
        color: $accent;
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
        # 1-5 work when Tabs has focus; escape brings focus to Tabs from anywhere
        Binding("1", "switch_tab('tracks')", "Tracks", show=False),
        Binding("2", "switch_tab('artists')", "Artists", show=False),
        Binding("3", "switch_tab('albums')", "Albums", show=False),
        Binding("4", "switch_tab('playlists')", "Playlists", show=False),
        Binding("5", "switch_tab('search')", "Search", show=False),
        Binding("6", "switch_tab('queue')", "Queue", show=False),
        # priority=True so this fires even when an Input has focus
        Binding("escape", "focus_tabs", "Nav", show=False, priority=True),
    ]

    now_playing: reactive[Track | None] = reactive(None)
    is_playing: reactive[bool] = reactive(False)
    is_paused: reactive[bool] = reactive(False)
    playback_pos: reactive[float] = reactive(0.0)
    playback_dur: reactive[float] = reactive(0.0)
    status_msg: reactive[str] = reactive("")
    queue_version: reactive[int] = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self._client = QobuzClient()
        self._player = MpvPlayer(audio_device=get_audio_device())
        self._play_queue: list[Track] = []
        # A restored-but-not-yet-loaded track: (track, position). Set on launch
        # from the persisted player state; resumed on the next play/pause press.
        self._pending_resume: tuple[Track, float] | None = None
        self._restoring: bool = False
        self._last_state_save: float = 0.0
        # Favourite tracks, fetched once and shared by TracksView (full list)
        # and the heart glyph in other track lists (id set). None = not loaded.
        self._fav_tracks: list[dict] | None = None
        self._fav_ids: set[str] | None = None
        self._fav_lock = asyncio.Lock()
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
            *[Tab(label, id=tid) for tid, label in _TABS],
            id="nav-tabs",
            active="tracks",
        )
        with ContentSwitcher(initial="view-tracks"):
            yield PlaylistsView(id="view-playlists")
            yield TracksView(id="view-tracks")
            yield ArtistsView(id="view-artists")
            yield AlbumsView(id="view-albums")
            yield SearchView(id="view-search")
            yield QueueView(id="view-queue")
        yield TransportBar()
        yield Footer()

    async def on_mount(self) -> None:
        self._poll_player()
        saved_theme = get_saved_theme()
        if saved_theme and saved_theme in self.available_themes:
            self.theme = saved_theme
        # Persist any subsequent theme changes (skip the initial/default ones).
        self._theme_ready = True
        if get_transparent_background():
            self.action_toggle_background()
        self._restore_player_state()
        self._warm_favorite_ids()

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
                    if hasattr(active, "action_navigate_back"):
                        if active.action_navigate_back():
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
        try:
            self.query_one("#search-input").focus()
        except Exception:
            pass

    # ── favourite tracks (heart glyph in track lists) ─────────────────────────

    async def ensure_favorite_tracks(self) -> list[dict]:
        """Raw favourite-track list, fetched exactly once and cached.

        Double-checked locking serialises concurrent first callers (TracksView's
        list load, the mount-time heart warm, the QueueView refresh, each
        track-list builder) so the paginated favourites endpoint is hit once.
        Raises on fetch failure (without caching) so callers can retry."""
        if self._fav_tracks is not None:
            return self._fav_tracks
        async with self._fav_lock:
            if self._fav_tracks is None:
                self._fav_tracks = await self._client.get_all_favorite_tracks()
            return self._fav_tracks

    async def ensure_favorite_ids(self) -> set[str]:
        """Set of favourited track ids, derived from the shared favourites
        cache. Degrades to an empty set if the fetch fails (no hearts)."""
        if self._fav_ids is None:
            try:
                tracks = await self.ensure_favorite_tracks()
            except Exception:
                tracks = []
            self._fav_ids = {str(t.get("id")) for t in tracks}
        return self._fav_ids

    @work
    async def _warm_favorite_ids(self) -> None:
        await self.ensure_favorite_ids()

    # ── seek from mouse click on bar ─────────────────────────────────────────

    def on_transport_bar_seek_to(self, event: TransportBar.SeekTo) -> None:
        self._player.seek_to(event.position)

    # ── player poll (thread — IPC blocks) ────────────────────────────────────

    @work(thread=True)
    def _poll_player(self) -> None:
        worker = get_current_worker()
        _seen_gen: int = 0
        while not worker.is_cancelled:
            if self._player.running:
                _seen_gen = self._player._stop_gen
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
                natural_end = self._player._stop_gen == _seen_gen
                self.call_from_thread(setattr, self, "is_playing", False)
                self.call_from_thread(setattr, self, "is_paused", False)
                self.call_from_thread(setattr, self, "playback_pos", 0.0)
                if natural_end:
                    self.call_from_thread(self._advance_queue)
            time.sleep(0.5)

    # ── play (async worker — shares event loop with httpx) ───────────────────

    @work
    async def play_track(
        self, track: Track, queue: list[Track] | None = None, start: float = 0.0
    ) -> None:
        self._pending_resume = None
        if queue is not None:
            self._play_queue = list(queue)
            self.queue_version += 1
        await self._do_play(track, start=start)

    @work
    async def _advance_queue(self) -> None:
        if not self._play_queue:
            return
        next_track = self._play_queue.pop(0)
        self.queue_version += 1
        await self._do_play(next_track)

    async def _do_play(self, track: Track, start: float = 0.0) -> None:
        self.status_msg = f"Loading {track.display_title}…"
        stream: StreamUrl | None = None
        for quality in ("FLAC_24_192", "FLAC_24_96", "FLAC_CD"):
            try:
                data = await self._client.get_streaming_url(str(track.id), quality)
                stream = StreamUrl.from_api(data)
                break
            except QobuzError:
                continue

        if stream is None:
            self.status_msg = "No stream available"
            return

        if self._player.running:
            self._player.stop()
        self._player.play(stream.url, start=start)
        self.now_playing = track
        self.playback_pos = start
        self.playback_dur = 0.0
        self.is_playing = True
        self.is_paused = False
        self.status_msg = ""

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
        try:
            save_state(
                _PLAYER_STATE_FILE,
                {
                    "track": dataclasses.asdict(track),
                    "position": float(self.playback_pos),
                    "duration": float(self.playback_dur),
                },
            )
        except Exception:
            pass

    def _restore_player_state(self) -> None:
        """Reload the last track + position into the transport bar (paused,
        unloaded). The next play/pause press resumes it from that position."""
        data = load_state(_PLAYER_STATE_FILE)
        raw = data.get("track")
        if not isinstance(raw, dict):
            return
        try:
            track = Track(**raw)
        except (TypeError, ValueError):
            return
        position = float(data.get("position") or 0.0)
        duration = float(data.get("duration") or track.duration or 0.0)
        self._restoring = True
        self.playback_dur = duration
        self.playback_pos = position
        self.is_paused = True
        self.now_playing = track
        self._restoring = False
        self._pending_resume = (track, position)

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
        if self._player.running:
            self._player.pause_toggle()
        elif self._pending_resume is not None:
            # Resume the track restored from the last session, from where it left off.
            track, position = self._pending_resume
            self.play_track(track, start=position)

    def action_previous(self) -> None:
        self._player.seek_to(0.0)

    def action_seek_back(self) -> None:
        self._player.seek(-10.0)

    def action_seek_fwd(self) -> None:
        self._player.seek(10.0)

    def action_focus_search(self) -> None:
        self.query_one(Tabs).active = "search"

    def _refresh_truecolor_filter(self, theme: object) -> None:
        if not getattr(self, "_transparent", False) or self.native_ansi_color:
            return super()._refresh_truecolor_filter(theme)  # type: ignore[misc]
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
        self.refresh(layout=True)

    async def on_unmount(self) -> None:
        from ._images import close_client

        self._save_player_state()
        self._media_keys.close()
        self._player.stop()
        await self._client.close()
        await close_client()

from __future__ import annotations

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
from textual.widgets import ContentSwitcher, Footer, Tab, Tabs
from textual.worker import get_current_worker

from ..audio.player import MpvPlayer
from ..config import (
    get_audio_device,
    get_oauth_session,
    get_transparent_background,
    set_transparent_background,
)
from ..qobuz.client import QobuzClient, QobuzError
from ..qobuz.models import StreamUrl, Track
from .screens.albums import AlbumsView
from .screens.artists import ArtistsView
from .screens.playlists import PlaylistsView
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


_TABS = [
    ("playlists", "Playlists"),
    ("tracks", "Tracks"),
    ("artists", "Artists"),
    ("albums", "Albums"),
    ("search", "Search"),
]


class QobitApp(App[None]):
    TITLE = "qobit"
    COMMANDS = App.COMMANDS | {QobitCommands}
    CSS = """
    Screen {
        layout: vertical;
    }
    Tabs {
        dock: top;
    }
    ContentSwitcher {
        height: 1fr;
    }
    SearchView, PlaylistsView, TracksView, ArtistsView, AlbumsView {
        height: 1fr;
    }
    Screen.-transparent,
    Screen.-transparent * {
        background: ansi_default;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "pause", "Pause", show=False),
        Binding("[", "seek_back", "◀10s"),
        Binding("]", "seek_fwd", "10s▶"),
        # 1-5 work when Tabs has focus; escape brings focus to Tabs from anywhere
        Binding("1", "switch_tab('playlists')", "Playlists", show=False),
        Binding("2", "switch_tab('tracks')", "Tracks", show=False),
        Binding("3", "switch_tab('artists')", "Artists", show=False),
        Binding("4", "switch_tab('albums')", "Albums", show=False),
        Binding("5", "switch_tab('search')", "Search", show=False),
        # priority=True so this fires even when an Input has focus
        Binding("escape", "focus_tabs", "Nav", show=False, priority=True),
    ]

    now_playing: reactive[Track | None] = reactive(None)
    is_playing: reactive[bool] = reactive(False)
    is_paused: reactive[bool] = reactive(False)
    playback_pos: reactive[float] = reactive(0.0)
    playback_dur: reactive[float] = reactive(0.0)
    status_msg: reactive[str] = reactive("")

    def __init__(self) -> None:
        super().__init__()
        self._client = QobuzClient()
        self._player = MpvPlayer(audio_device=get_audio_device())

    def compose(self) -> ComposeResult:
        yield Tabs(
            *[Tab(label, id=tid) for tid, label in _TABS],
            id="nav-tabs",
            active="search",
        )
        with ContentSwitcher(initial="view-search"):
            yield PlaylistsView(id="view-playlists")
            yield TracksView(id="view-tracks")
            yield ArtistsView(id="view-artists")
            yield AlbumsView(id="view-albums")
            yield SearchView(id="view-search")
        yield TransportBar()
        yield Footer()

    async def on_mount(self) -> None:
        app_id, token, secrets = get_oauth_session()
        if app_id and token and secrets:
            self._client.restore_session(app_id, token, secrets)
        self._poll_player()
        if get_transparent_background():
            self.action_toggle_background()

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
            self.pop_screen()
        else:
            self.query_one("#nav-tabs", Tabs).focus()

    def _focus_search_input(self) -> None:
        try:
            self.query_one("#search-input").focus()
        except Exception:
            pass

    # ── reactive watches → update TransportBar reactives directly ────────────

    def _bar(self) -> TransportBar | None:
        try:
            return self.screen.query_one(TransportBar)
        except Exception:
            return None

    def sync_transport_bar(self) -> None:
        """Prime a newly-mounted TransportBar with current playback state."""
        bar = self._bar()
        if bar is None:
            return
        if self.now_playing:
            bar.label = f"{self.now_playing.artist} — {self.now_playing.display_title}"
            bar.border_title = "⏸  Now Playing" if self.is_paused else "▶  Now Playing"
        bar.position = self.playback_pos
        bar.duration = self.playback_dur
        bar.is_paused = self.is_paused
        bar.set_class(self.is_playing, "-playing")

    def watch_now_playing(self, track: Track | None) -> None:
        bar = self._bar()
        if bar is None:
            return
        if track:
            bar.label = f"{track.artist} — {track.display_title}"
            bar.border_title = "⏸  Now Playing" if self.is_paused else "▶  Now Playing"
        else:
            bar.label = ""
            bar.border_title = ""

    def watch_is_playing(self, playing: bool) -> None:
        if bar := self._bar():
            bar.set_class(playing, "-playing")

    def watch_is_paused(self, paused: bool) -> None:
        if bar := self._bar():
            bar.is_paused = paused
            if self.now_playing:
                bar.border_title = "⏸  Now Playing" if paused else "▶  Now Playing"

    def watch_playback_pos(self, pos: float) -> None:
        if bar := self._bar():
            bar.position = pos

    def watch_playback_dur(self, dur: float) -> None:
        if bar := self._bar():
            bar.duration = dur

    def watch_status_msg(self, msg: str) -> None:
        if bar := self._bar():
            bar.label = (
                msg
                if msg
                else (
                    f"{self.now_playing.artist} — {self.now_playing.display_title}"
                    if self.now_playing
                    else ""
                )
            )

    # ── seek from mouse click on bar ─────────────────────────────────────────

    def on_transport_bar_seek_to(self, event: TransportBar.SeekTo) -> None:
        self._player.seek_to(event.position)

    # ── player poll (thread — IPC blocks) ────────────────────────────────────

    @work(thread=True)
    def _poll_player(self) -> None:
        worker = get_current_worker()
        while not worker.is_cancelled:
            if self._player.running:
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
            elif self.is_playing:
                self.call_from_thread(setattr, self, "is_playing", False)
                self.call_from_thread(setattr, self, "is_paused", False)
                self.call_from_thread(setattr, self, "playback_pos", 0.0)
            time.sleep(0.5)

    # ── play (async worker — shares event loop with httpx) ───────────────────

    @work
    async def play_track(self, track: Track) -> None:
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
        self._player.play(stream.url)
        self.now_playing = track
        self.playback_pos = 0.0
        self.playback_dur = 0.0
        self.is_playing = True
        self.is_paused = False
        self.status_msg = ""

    # ── actions ──────────────────────────────────────────────────────────────

    def action_pause(self) -> None:
        if self._player.running:
            self._player.pause_toggle()

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
        self.screen.toggle_class("-transparent")
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
        self._player.stop()
        await self._client.close()

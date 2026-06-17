from __future__ import annotations

import time

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import ContentSwitcher, Footer, Tab, Tabs
from textual.worker import get_current_worker

from ..audio.player import MpvPlayer
from ..config import get_audio_device, get_oauth_session
from ..qobuz.client import QobuzClient, QobuzError
from ..qobuz.models import StreamUrl, Track
from .screens.albums import AlbumsView
from .screens.artists import ArtistsView
from .screens.playlists import PlaylistsView
from .screens.search import SearchView
from .screens.tracks import TracksView
from .widgets.transport import TransportBar

_TABS = [
    ("playlists", "Playlists"),
    ("tracks", "Tracks"),
    ("artists", "Artists"),
    ("albums", "Albums"),
    ("search", "Search"),
]


class QobitApp(App[None]):
    TITLE = "qobit"
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
        self.query_one("#nav-tabs", Tabs).focus()

    def _focus_search_input(self) -> None:
        try:
            self.query_one("#search-input").focus()
        except Exception:
            pass

    # ── reactive watches → update TransportBar reactives directly ────────────

    def _bar(self) -> TransportBar | None:
        try:
            return self.query_one(TransportBar)
        except Exception:
            return None

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

    async def on_unmount(self) -> None:
        self._player.stop()
        await self._client.close()

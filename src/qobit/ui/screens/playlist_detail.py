from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView

from ...qobuz.models import Playlist, Track
from ..widgets.transport import TransportBar
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp


class PlaylistTrackRow(ListItem):
    DEFAULT_CSS = """
    PlaylistTrackRow { height: 2; padding: 0 1; }
    PlaylistTrackRow Label { width: 1fr; }
    PlaylistTrackRow .primary { text-style: bold; }
    PlaylistTrackRow .secondary { color: $text-muted; }
    """

    def __init__(self, track: Track) -> None:
        super().__init__()
        self.track = track

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(f"{ICON_TRACK}  {t.artist} — {t.display_title}", classes="primary")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")


class PlaylistScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    DEFAULT_CSS = """
    PlaylistScreen { layout: vertical; }
    PlaylistScreen #header {
        height: 4;
        padding: 1 2;
        background: $boost;
    }
    PlaylistScreen ListView { height: 1fr; }
    """

    def __init__(self, playlist_id: str) -> None:
        super().__init__()
        self._playlist_id = playlist_id

    def compose(self) -> ComposeResult:
        yield Label("Loading…", id="header")
        yield ListView(id="tracklist")
        yield TransportBar()
        yield Footer()

    def on_mount(self) -> None:
        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        playlist = Playlist.from_api(await app._client.get_playlist(self._playlist_id))
        self.query_one("#header", Label).update(
            f"[bold]{playlist.name}[/bold]\n"
            f"[dim]{playlist.owner}  ·  {playlist.tracks_count} tracks[/dim]"
        )
        lv = self.query_one("#tracklist", ListView)
        for track in playlist.tracks:
            await lv.append(PlaylistTrackRow(track))

    @on(ListView.Selected, "#tracklist")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, PlaylistTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.play_track(event.item.track)
            app.pop_screen()

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView

from ...qobuz.models import Album, Track
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp


class TrackRow(ListItem):
    DEFAULT_CSS = """
    TrackRow { height: 1; padding: 0 1; }
    TrackRow Label { width: 1fr; }
    """

    def __init__(self, track: Track, number: int) -> None:
        super().__init__()
        self.track = track
        self._number = number

    def compose(self) -> ComposeResult:
        t = self.track
        num = f"{self._number:2}. {ICON_TRACK}"
        yield Label(
            f"[dim]{num}[/dim]  {t.display_title}  [dim]{t.duration_str}[/dim]",
            markup=True,
        )


class AlbumScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    DEFAULT_CSS = """
    AlbumScreen { layout: vertical; }
    AlbumScreen #header {
        height: 4;
        padding: 1 2;
        background: $boost;
    }
    AlbumScreen ListView { height: 1fr; }
    """

    def __init__(self, album_id: str) -> None:
        super().__init__()
        self._album_id = album_id

    def compose(self) -> ComposeResult:
        yield Label("Loading…", id="header")
        yield ListView(id="tracklist")
        yield Footer()

    def on_mount(self) -> None:
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        album = Album.from_api(await app._client.get_album(self._album_id))
        year = str(album.year) if album.year else "—"
        self.query_one("#header", Label).update(
            f"[bold]{album.title}[/bold]\n"
            f"[dim]{album.artist}  ·  {year}  ·  {album.tracks_count} tracks[/dim]"
        )
        lv = self.query_one("#tracklist", ListView)
        for i, track in enumerate(album.tracks, 1):
            await lv.append(TrackRow(track, i))

    @on(ListView.Selected, "#tracklist")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.play_track(event.item.track)
            app.pop_screen()

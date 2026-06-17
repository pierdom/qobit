from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView

from ...qobuz.models import Album, Artist
from .search import ICON_ALBUM

if TYPE_CHECKING:
    from ..app import QobitApp


class AlbumRow(ListItem):
    DEFAULT_CSS = """
    AlbumRow { height: 2; padding: 0 1; }
    AlbumRow Label { width: 1fr; }
    """

    def __init__(self, album: Album) -> None:
        super().__init__()
        self.album = album

    def compose(self) -> ComposeResult:
        a = self.album
        year = str(a.year) if a.year else "—"
        yield Label(f"[dim]{ICON_ALBUM}[/dim]  [bold]{a.title}[/bold]", markup=True)
        yield Label(f"     [dim]{year}  ·  {a.tracks_count} tracks[/dim]", markup=True)


class ArtistScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    DEFAULT_CSS = """
    ArtistScreen { layout: vertical; }
    ArtistScreen #header {
        height: 3;
        padding: 1 2;
        background: $boost;
    }
    ArtistScreen ListView { height: 1fr; }
    """

    def __init__(self, artist_id: str) -> None:
        super().__init__()
        self._artist_id = artist_id

    def compose(self) -> ComposeResult:
        yield Label("Loading…", id="header")
        yield ListView(id="albums")
        yield Footer()

    def on_mount(self) -> None:
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        data = await app._client.get_artist(self._artist_id)
        artist = Artist.from_api(data)
        self.query_one("#header", Label).update(f"[bold]{artist.name}[/bold]")
        lv = self.query_one("#albums", ListView)
        for raw in data.get("albums", {}).get("items", []):
            await lv.append(AlbumRow(Album.from_api(raw)))

    @on(ListView.Selected, "#albums")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, AlbumRow):
            from .album_detail import AlbumScreen

            self.app.push_screen(AlbumScreen(event.item.album.id))

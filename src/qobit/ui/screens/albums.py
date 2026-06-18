from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Label

from ...qobuz.models import Album, Artist
from .album_detail import AlbumDetailPanel
from .artist_detail import AlbumCard, AlbumGrid, ArtistHeader

if TYPE_CHECKING:
    from ..app import QobitApp


class AlbumsView(Widget):
    DEFAULT_CSS = """
    AlbumsView {
        height: 1fr;
        layout: vertical;
        padding: 1;
    }
    AlbumsView ContentSwitcher { height: 1fr; }
    AlbumsView #albums-grid-view { height: 1fr; }
    AlbumsView AlbumGrid {
        grid-rows: 6;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }
    AlbumsView AlbumGrid:focus {
        border: round $accent;
        border-title-color: $accent;
    }
    AlbumsView #album-breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $boost;
    }
    AlbumsView #album-breadcrumb:hover {
        color: $text;
        background: $panel;
    }
    """

    _album_view_active: bool = False

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="albums-grid-view", id="albums-switcher"):
            with Vertical(id="albums-grid-view"):
                yield AlbumGrid(id="fav-albums-grid", tile_min_width=33)
            with Vertical(id="albums-album-view"):
                yield Label("← Favourite Albums", id="album-breadcrumb")
                yield ArtistHeader()
                yield AlbumDetailPanel(id="album-panel")

    def on_mount(self) -> None:
        self.query_one("#fav-albums-grid", AlbumGrid).border_title = "Favourite Albums"
        self._load()

    def on_show(self) -> None:
        if self._album_view_active:
            self.query_one("#album-panel", AlbumDetailPanel).focus_tracklist()
        else:
            self.query_one("#fav-albums-grid", AlbumGrid).focus()

    def action_navigate_back(self) -> bool:
        if self._album_view_active:
            self._show_grid()
            return True
        return False

    def _open_album(self, album: Album) -> None:
        panel = self.query_one("#album-panel", AlbumDetailPanel)
        panel.load(album)

        header = self.query_one(ArtistHeader)
        header.set_loading(album.artist or "Artist")
        if album.artist_id:
            self._load_artist_info(album.artist_id)

        self.query_one("#albums-switcher", ContentSwitcher).current = "albums-album-view"
        self._album_view_active = True
        panel.focus_tracklist()

    def _show_grid(self) -> None:
        self.query_one("#albums-switcher", ContentSwitcher).current = "albums-grid-view"
        self._album_view_active = False
        self.query_one("#fav-albums-grid", AlbumGrid).focus()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        try:
            data = await app._client.get_user_favorites(type="albums", limit=50)
            items = data.get("albums", {}).get("items", [])
        except Exception as e:
            await grid.mount(Label(f"[red]{e}[/red]", markup=True))
            return
        if not items:
            await grid.mount(Label("[dim]No favourite albums yet.[/dim]", markup=True))
            return
        for raw in items:
            await grid.mount(AlbumCard(Album.from_api(raw), show_artist=True))

    @work
    async def _load_artist_info(self, artist_id: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        artist = Artist.from_api(await app._client.get_artist_detail(artist_id))
        self.query_one(ArtistHeader).populate(artist)

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track)

    @on(events.Click, "#album-breadcrumb")
    def _on_breadcrumb_click(self) -> None:
        self._show_grid()

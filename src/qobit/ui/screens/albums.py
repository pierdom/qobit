from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Album
from .search import AlbumItem

if TYPE_CHECKING:
    from ..app import QobitApp


class AlbumsView(Widget):
    DEFAULT_CSS = """
    AlbumsView {
        height: 1fr;
        layout: vertical;
    }
    AlbumsView ListView {
        height: 1fr;
        margin: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield ListView(id="fav-albums")

    def on_mount(self) -> None:
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#fav-albums", ListView)
        await lv.clear()
        try:
            data = await app._client.get_user_favorites(type="albums", limit=50)
            items = data.get("albums", {}).get("items", [])
        except Exception as e:
            await lv.append(ListItem(Label(f"[red]{e}[/red]", markup=True)))
            return
        if not items:
            await lv.append(ListItem(Label("[dim]No favourite albums yet.[/dim]", markup=True)))
            return
        for raw in items:
            await lv.append(AlbumItem(Album.from_api(raw)))

    @on(ListView.Selected, "#fav-albums")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, AlbumItem):
            from .album_detail import AlbumScreen

            self.app.push_screen(AlbumScreen(event.item.album.id))

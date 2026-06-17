from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Artist
from .search import ArtistItem

if TYPE_CHECKING:
    from ..app import QobitApp


class ArtistsView(Widget):
    DEFAULT_CSS = """
    ArtistsView {
        height: 1fr;
        layout: vertical;
    }
    ArtistsView ListView {
        height: 1fr;
        margin: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield ListView(id="fav-artists")

    def on_mount(self) -> None:
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#fav-artists", ListView)
        await lv.clear()
        try:
            data = await app._client.get_user_favorites(type="artists", limit=50)
            items = data.get("artists", {}).get("items", [])
        except Exception as e:
            await lv.append(ListItem(Label(f"[red]{e}[/red]", markup=True)))
            return
        if not items:
            await lv.append(ListItem(Label("[dim]No favourite artists yet.[/dim]", markup=True)))
            return
        for raw in items:
            await lv.append(ArtistItem(Artist.from_api(raw)))

    @on(ListView.Selected, "#fav-artists")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ArtistItem):
            from .artist_detail import ArtistScreen

            self.app.push_screen(ArtistScreen(event.item.artist.id))

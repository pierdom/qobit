from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Playlist
from .search import PlaylistItem

if TYPE_CHECKING:
    from ..app import QobitApp


class PlaylistsView(Widget):
    DEFAULT_CSS = """
    PlaylistsView {
        height: 1fr;
        layout: vertical;
    }
    PlaylistsView ListView {
        height: 1fr;
        margin: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield ListView(id="user-playlists")

    def on_mount(self) -> None:
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#user-playlists", ListView)
        await lv.clear()
        try:
            data = await app._client.get_user_playlists(limit=50)
            items = data.get("playlists", {}).get("items", [])
        except Exception as e:
            await lv.append(ListItem(Label(f"[red]{e}[/red]", markup=True)))
            return
        if not items:
            await lv.append(ListItem(Label("[dim]No playlists yet.[/dim]", markup=True)))
            return
        for raw in items:
            await lv.append(PlaylistItem(Playlist.from_api(raw)))

    @on(ListView.Selected, "#user-playlists")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, PlaylistItem):
            from .playlist_detail import PlaylistScreen

            self.app.push_screen(PlaylistScreen(event.item.playlist.id))

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Track
from .search import TrackItem

if TYPE_CHECKING:
    from ..app import QobitApp


class TracksView(Widget):
    DEFAULT_CSS = """
    TracksView {
        height: 1fr;
        layout: vertical;
    }
    TracksView ListView {
        height: 1fr;
        margin: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield ListView(id="fav-tracks")

    def on_mount(self) -> None:
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#fav-tracks", ListView)
        await lv.clear()
        try:
            data = await app._client.get_user_favorites(type="tracks", limit=50)
            items = data.get("tracks", {}).get("items", [])
        except Exception as e:
            await lv.append(ListItem(Label(f"[red]{e}[/red]", markup=True)))
            return
        if not items:
            await lv.append(ListItem(Label("[dim]No favourite tracks yet.[/dim]", markup=True)))
            return
        for raw in items:
            await lv.append(TrackItem(Track.from_api(raw)))

    @on(ListView.Selected, "#fav-tracks")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackItem):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.play_track(event.item.track)

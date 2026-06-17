from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView

from ...qobuz.client import QobuzError
from ...qobuz.models import Track

if TYPE_CHECKING:
    from ..app import QobitApp


class TrackItem(ListItem):
    """Two-line track entry in the search results list."""

    DEFAULT_CSS = """
    TrackItem {
        height: 2;
        padding: 0 1;
    }
    TrackItem Label {
        width: 1fr;
    }
    """

    def __init__(self, track: Track) -> None:
        super().__init__()
        self.track = track

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(f"[bold]{t.artist}[/bold] — {t.display_title}", markup=True)
        yield Label(f"[dim]{t.album}  ·  {t.duration_str}[/dim]", markup=True)


class SearchView(Widget):
    BINDINGS = [Binding("/", "focus_input", show=False)]

    DEFAULT_CSS = """
    SearchView {
        height: 1fr;
        layout: vertical;
    }
    SearchView Input {
        margin: 1 1 0 1;
    }
    SearchView ListView {
        height: 1fr;
        margin: 0 1 1 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search Qobuz…", id="search-input")
        yield ListView(id="results")

    def on_mount(self) -> None:
        self.query_one("#search-input").focus()

    def action_focus_input(self) -> None:
        self.query_one("#search-input").focus()

    @on(Input.Submitted, "#search-input")
    def _on_submit(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._search(query)

    @work
    async def _search(self, query: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#results", ListView)
        await lv.clear()

        try:
            data = await app._client.search(query, type="tracks", limit=25)
        except (QobuzError, AssertionError) as e:
            msg = str(e) if isinstance(e, QobuzError) else "Not authenticated — run: qobit auth"
            await lv.append(ListItem(Label(f"[red]{msg}[/red]", markup=True)))
            return

        items = data.get("tracks", {}).get("items", [])
        if not items:
            await lv.append(ListItem(Label("[dim]No results.[/dim]", markup=True)))
            return

        for raw in items:
            await lv.append(TrackItem(Track.from_api(raw)))

    @on(ListView.Selected, "#results")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackItem):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.play_track(event.item.track)

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Track
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp


class QueueTrackRow(ListItem):
    DEFAULT_CSS = """
    QueueTrackRow { height: 2; padding: 0 1; }
    QueueTrackRow Label { width: 1fr; }
    QueueTrackRow .primary { text-style: bold; }
    QueueTrackRow .secondary { color: $text-muted; }
    """

    def __init__(self, track: Track, number: int) -> None:
        super().__init__()
        self.track = track
        self._number = number

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(
            f"{self._number}. {ICON_TRACK}  {t.artist} — {t.display_title}", classes="primary"
        )
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")


class QueueView(Widget):
    DEFAULT_CSS = """
    QueueView {
        height: 1fr;
        layout: vertical;
    }
    QueueView #queue-list {
        height: 1fr;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
        border-subtitle-color: $accent 40%;
        border-subtitle-align: right;
    }
    QueueView #queue-list:focus {
        border: round $accent;
        border-title-color: $accent;
        border-subtitle-color: $accent;
    }
    """

    def compose(self) -> ComposeResult:
        yield ListView(id="queue-list")

    def on_mount(self) -> None:
        self.query_one("#queue-list", ListView).border_title = "Up Next"
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "queue_version", self._on_queue_changed, init=True)

    def on_show(self) -> None:
        self.call_after_refresh(self.query_one("#queue-list", ListView).focus)

    def _on_queue_changed(self, _: int) -> None:
        self._refresh_list()

    @work
    async def _refresh_list(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#queue-list", ListView)
        await lv.clear()
        queue = list(app._play_queue)
        if not queue:
            await lv.append(ListItem(Label("[dim]Queue is empty.[/dim]", markup=True)))
            lv.border_subtitle = ""
        else:
            count = len(queue)
            await lv.mount(*[QueueTrackRow(t, i) for i, t in enumerate(queue, 1)])
            lv.border_subtitle = f"{count} track{'s' if count != 1 else ''}"

    @on(ListView.Selected, "#queue-list")
    def _on_queue_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, QueueTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            lv = self.query_one("#queue-list", ListView)
            rows = list(lv.query(QueueTrackRow))
            idx = rows.index(event.item)
            remaining = [r.track for r in rows[idx + 1 :]]
            app.play_track(event.item.track, queue=remaining)

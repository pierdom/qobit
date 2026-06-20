from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Track
from ..widgets.lists import PagedListView
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp


class NowPlayingRow(ListItem):
    """The currently playing track — always shown at the top of the queue."""

    DEFAULT_CSS = """
    NowPlayingRow { height: 2; padding: 0 1; background: $accent 8%; }
    NowPlayingRow .np-title { width: 1fr; text-style: bold; color: $accent; }
    NowPlayingRow .np-album { width: 1fr; color: $accent 55%; }
    """

    def __init__(self, track: Track, is_paused: bool) -> None:
        super().__init__()
        self.track = track
        self._is_paused = is_paused

    def compose(self) -> ComposeResult:
        t = self.track
        icon = "⏸" if self._is_paused else "▶"
        yield Label(f"{icon}  {t.artist} — {t.display_title}", classes="np-title")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="np-album")

    def set_paused(self, paused: bool) -> None:
        icon = "⏸" if paused else "▶"
        t = self.track
        self.query_one(".np-title", Label).update(f"{icon}  {t.artist} — {t.display_title}")


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

    _render_version: int = 0

    def compose(self) -> ComposeResult:
        yield PagedListView(id="queue-list")

    def on_mount(self) -> None:
        self.query_one("#queue-list", ListView).border_title = "Up Next"
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "queue_version", self._on_queue_changed, init=True)
        self.watch(app, "now_playing", self._on_now_playing_changed, init=False)
        self.watch(app, "is_paused", self._on_paused_changed, init=False)

    def on_show(self) -> None:
        self.call_after_refresh(self.query_one("#queue-list", ListView).focus)

    def _on_queue_changed(self, _: int) -> None:
        self._render_version += 1
        self._refresh_list(self._render_version)

    def _on_now_playing_changed(self, _: object) -> None:
        self._render_version += 1
        self._refresh_list(self._render_version)

    def _on_paused_changed(self, paused: bool) -> None:
        try:
            self.query_one(NowPlayingRow).set_paused(paused)
        except Exception:
            pass

    @work
    async def _refresh_list(self, version: int) -> None:
        if version != self._render_version:
            return
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#queue-list", ListView)
        await lv.clear()
        if version != self._render_version:
            return

        now_playing = app.now_playing
        queue = list(app._play_queue)
        items: list[ListItem] = []

        if now_playing:
            items.append(NowPlayingRow(now_playing, app.is_paused))

        if queue:
            items.extend(QueueTrackRow(t, i) for i, t in enumerate(queue, 1))

        if not items:
            await lv.append(ListItem(Label("[dim]Queue is empty.[/dim]", markup=True)))
            lv.border_subtitle = ""
        else:
            await lv.mount(*items)
            count = len(queue)
            lv.border_subtitle = f"{count} queued" if count else ""

    @on(ListView.Selected, "#queue-list")
    def _on_queue_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, QueueTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            lv = self.query_one("#queue-list", ListView)
            rows = list(lv.query(QueueTrackRow))
            idx = rows.index(event.item)
            remaining = [r.track for r in rows[idx + 1 :]]
            app.play_track(event.item.track, queue=remaining)

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Album, Track
from ..widgets.lists import TrackListView
from .search import ICON_FAV, ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp


class SectionHeader(ListItem):
    """A non-selectable divider labelling a queue section."""

    DEFAULT_CSS = """
    SectionHeader { height: 1; padding: 0 1; background: $surface; }
    SectionHeader.-spaced { margin-top: 1; }
    SectionHeader Label { width: 1fr; color: $accent; text-style: bold; }
    """

    def __init__(self, title: str, spaced: bool = False) -> None:
        super().__init__(disabled=True)
        self._title = title
        if spaced:
            self.add_class("-spaced")

    def compose(self) -> ComposeResult:
        yield Label(f"─ {self._title} ".ljust(60, "─"))


class NowPlayingRow(ListItem):
    """The currently playing track — a richer card showing album, year, genre,
    label and the live stream resolution alongside the title."""

    DEFAULT_CSS = """
    NowPlayingRow { height: 4; padding: 1 1 0 1; background: $accent 8%; }
    NowPlayingRow .np-title { width: 1fr; text-style: bold; color: $accent; }
    NowPlayingRow .np-album { width: 1fr; color: $accent 70%; }
    NowPlayingRow .np-extra { width: 1fr; color: $accent 45%; }
    """

    def __init__(
        self,
        track: Track,
        is_paused: bool,
        favorite: bool = False,
        album: Album | None = None,
        quality: str = "",
    ) -> None:
        super().__init__()
        self.track = track
        self._is_paused = is_paused
        self._favorite = favorite
        self._album = album
        self._quality = quality

    def _title(self) -> Content:
        t = self.track
        icon = "⏸" if self._is_paused else "▶"
        heart = (f"  {ICON_FAV}", "$accent") if self._favorite else ""
        return Content.assemble(f"{icon}  {t.artist} — {t.display_title}", heart)

    def _album_line(self) -> str:
        t = self.track
        parts: list[str] = [t.album]
        if self._album and self._album.year:
            parts.append(str(self._album.year))
        if self._quality:
            parts.append(self._quality)
        parts.append(t.duration_str)
        return "     " + "  ·  ".join(p for p in parts if p)

    def _extra_line(self) -> str:
        if not self._album:
            return ""
        bits = [b for b in (self._album.genre, self._album.label) if b]
        return ("     " + "  ·  ".join(bits)) if bits else ""

    def compose(self) -> ComposeResult:
        yield Label(self._title(), classes="np-title")
        yield Label(self._album_line(), classes="np-album")
        yield Label(self._extra_line(), classes="np-extra")

    def set_paused(self, paused: bool) -> None:
        self._is_paused = paused
        self.query_one(".np-title", Label).update(self._title())

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self.query_one(".np-title", Label).update(self._title())

    def set_details(self, album: Album | None, quality: str) -> None:
        self._album = album
        self._quality = quality
        self.query_one(".np-album", Label).update(self._album_line())
        self.query_one(".np-extra", Label).update(self._extra_line())


class QueueTrackRow(ListItem):
    DEFAULT_CSS = """
    QueueTrackRow { height: 2; padding: 0 1; }
    QueueTrackRow Label { width: 1fr; }
    QueueTrackRow .primary { text-style: bold; }
    QueueTrackRow .secondary { color: $text-muted; }
    """

    def __init__(self, track: Track, number: int, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._number = number
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = (f"  {ICON_FAV}", "$accent") if self._favorite else ""
        return Content.assemble(
            f"{self._number}. {ICON_TRACK}  {t.artist} — {t.display_title}", heart
        )

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="primary")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self.query_one(".primary", Label).update(self._primary())


class HistoryTrackRow(ListItem):
    """A previously-played track. Muted; selecting it replays the track without
    disturbing the Up Next queue."""

    DEFAULT_CSS = """
    HistoryTrackRow { height: 2; padding: 0 1; }
    HistoryTrackRow Label { width: 1fr; }
    HistoryTrackRow .primary { color: $text-muted; }
    HistoryTrackRow .secondary { color: $text-muted 60%; }
    """

    def __init__(self, track: Track, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = (f"  {ICON_FAV}", "$accent") if self._favorite else ""
        return Content.assemble(f"↺  {t.artist} — {t.display_title}", heart)

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="primary")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self.query_one(".primary", Label).update(self._primary())


class QueueView(Widget):
    BINDINGS = [
        Binding("c", "clear_queue", "Clear queue", show=True),
        Binding("X", "clear_history", "Clear history", show=True),
    ]

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
        yield TrackListView(id="queue-list")

    def on_mount(self) -> None:
        self.query_one("#queue-list", ListView).border_title = "Queue"
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "queue_version", self._on_queue_changed, init=True)
        self.watch(app, "now_playing", self._on_now_playing_changed, init=False)
        self.watch(app, "is_paused", self._on_paused_changed, init=False)
        self.watch(app, "now_playing_album", self._on_details_changed, init=False)
        self.watch(app, "quality_label", self._on_details_changed, init=False)

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

    def _on_details_changed(self, _: object) -> None:
        # The album loads (and quality is set) shortly after a track starts;
        # update the Now Playing card in place rather than re-rendering the list.
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            self.query_one(NowPlayingRow).set_details(app.now_playing_album, app.quality_label)
        except Exception:
            pass

    def action_clear_queue(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.clear_queue()

    def action_clear_history(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.clear_history()

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
        history = list(app._history)
        queue = list(app._play_queue)
        fav_ids = await app.ensure_favorite_ids()
        if version != self._render_version:
            return

        items: list[ListItem] = []
        now_idx: int | None = None

        if history:
            items.append(SectionHeader("Recently Played"))
            items.extend(HistoryTrackRow(t, str(t.id) in fav_ids) for t in history)

        if now_playing:
            items.append(SectionHeader("Now Playing", spaced=bool(history)))
            now_idx = len(items)
            items.append(
                NowPlayingRow(
                    now_playing,
                    app.is_paused,
                    str(now_playing.id) in fav_ids,
                    album=app.now_playing_album,
                    quality=app.quality_label,
                )
            )

        if queue:
            items.append(SectionHeader("Up Next", spaced=bool(now_playing or history)))
            items.extend(QueueTrackRow(t, i, str(t.id) in fav_ids) for i, t in enumerate(queue, 1))

        if not items:
            await lv.append(ListItem(Label("[dim]Queue is empty.[/dim]", markup=True)))
            lv.border_subtitle = ""
            return

        await lv.mount(*items)
        parts: list[str] = []
        if history:
            parts.append(f"{len(history)} played")
        if queue:
            parts.append(f"{len(queue)} queued")
        lv.border_subtitle = "  ·  ".join(parts)

        # Start the cursor on the current track so PageUp walks into history and
        # PageDown into the queue, and scroll it to the middle of the viewport.
        if now_idx is not None:
            lv.index = now_idx
            self.call_after_refresh(
                lambda: lv.scroll_to_widget(items[now_idx], animate=False, center=True)
            )

    @on(ListView.Selected, "#queue-list")
    def _on_queue_selected(self, event: ListView.Selected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        if isinstance(event.item, QueueTrackRow):
            lv = self.query_one("#queue-list", ListView)
            rows = list(lv.query(QueueTrackRow))
            idx = rows.index(event.item)
            remaining = [r.track for r in rows[idx + 1 :]]
            app.play_track(event.item.track, queue=remaining)
        elif isinstance(event.item, HistoryTrackRow):
            # Replay a past track as a one-off; leave Up Next untouched.
            app.play_track(event.item.track)

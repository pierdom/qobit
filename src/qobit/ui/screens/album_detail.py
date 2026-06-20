from __future__ import annotations

import html as _html
import re
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Label, ListItem, ListView
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from ...qobuz.models import Album, Track
from .._images import fetch_image
from ..widgets.transport import TransportBar
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp


def _strip_html(text: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


class TrackRow(ListItem):
    DEFAULT_CSS = """
    TrackRow { height: 1; padding: 0 1; }
    TrackRow Label { width: 1fr; }
    """

    def __init__(self, track: Track, number: int) -> None:
        super().__init__()
        self.track = track
        self._number = number

    def compose(self) -> ComposeResult:
        t = self.track
        num = f"{self._number:2}. {ICON_TRACK}"
        yield Label(f"{num}  {t.display_title}  {t.duration_str}", classes="primary")


class AlbumDetailPanel(Widget):
    """Reusable inline album detail panel: art, metadata, and track list.

    Usage: call ``load(album)`` to populate. Emits ``TrackSelected`` when the
    user picks a track; the parent decides what to do with it.
    """

    class TrackSelected(Message):
        def __init__(self, track: Track, queue: list[Track]) -> None:
            super().__init__()
            self.track = track
            self.queue = queue

    DEFAULT_CSS = """
    AlbumDetailPanel {
        height: 1fr;
        layout: vertical;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }
    AlbumDetailPanel:focus-within {
        border: round $accent;
        border-title-color: $accent;
    }
    AlbumDetailPanel .ap-header {
        height: 16;
        layout: horizontal;
        padding: 1 2;
        background: $boost;
    }
    AlbumDetailPanel .ap-art {
        width: 16;
        height: 14;
        margin-right: 2;
    }
    AlbumDetailPanel .ap-meta   { width: 1fr; height: 14; }
    AlbumDetailPanel .ap-title  { height: auto; text-style: bold; }
    AlbumDetailPanel .ap-version { height: auto; color: $text-muted; text-style: italic; }
    AlbumDetailPanel .ap-sub    { height: auto; color: $text-muted; margin-top: 1; }
    AlbumDetailPanel .ap-badges { height: auto; color: $text-muted; }
    AlbumDetailPanel .ap-awards { height: auto; color: $accent 70%; margin-top: 1; }
    AlbumDetailPanel .ap-desc   { height: auto; color: $text-muted; margin-top: 1; }
    AlbumDetailPanel .ap-tracklist {
        height: 1fr;
        border-top: solid $accent 40%;
    }
    AlbumDetailPanel:focus-within .ap-tracklist {
        border-top: solid $accent;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(classes="ap-header"):
            yield TGPImage(classes="ap-art")
            with VerticalScroll(classes="ap-meta"):
                yield Label("", classes="ap-title", markup=True)
                yield Label("", classes="ap-version", markup=True)
                yield Label("", classes="ap-sub", markup=True)
                yield Label("", classes="ap-badges", markup=True)
                yield Label("", classes="ap-awards", markup=True)
                yield Label("", classes="ap-desc", markup=True)
        yield ListView(classes="ap-tracklist")

    def on_mount(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_w = round(14 * cell.height / cell.width)
            self.query_one(TGPImage).styles.width = img_w

    def load(self, album: Album) -> None:
        """Populate the panel with album data and start async loading."""
        self.border_title = escape(album.title)
        year = str(album.year) if album.year else "—"
        parts: list[str] = [escape(album.artist), year]
        if album.genre:
            parts.append(escape(album.genre))
        self.query_one(".ap-title", Label).update(escape(album.title))
        self.query_one(".ap-sub", Label).update(f"[dim]{' · '.join(parts)}[/dim]")
        for cls in (".ap-version", ".ap-badges", ".ap-awards", ".ap-desc"):
            self.query_one(cls, Label).update("")
        self.query_one(".ap-tracklist", ListView).clear()
        if album.image_url:
            self._fetch_art(album.image_url)
        self._fetch_full_album(album)

    def focus_tracklist(self) -> None:
        self.query_one(".ap-tracklist", ListView).focus()

    @work
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_mounted:
            try:
                self.query_one(TGPImage).image = img
            except NoMatches:
                pass

    @work
    async def _fetch_full_album(self, album: Album) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        full = Album.from_api(await app._client.get_album(album.id))

        if full.version:
            self.query_one(".ap-version", Label).update(
                f"[dim italic]{escape(full.version)}[/dim italic]"
            )
        year = str(full.year) if full.year else "—"
        sub_parts: list[str] = [escape(full.artist), year]
        if full.genre:
            sub_parts.append(escape(full.genre))
        if dur := full.total_duration_str:
            sub_parts.append(dur)
        self.query_one(".ap-sub", Label).update(f"[dim]{' · '.join(sub_parts)}[/dim]")

        badge_parts: list[str] = []
        if full.label:
            badge_parts.append(escape(full.label))
        if q := full.quality_badge:
            badge_parts.append(q)
        if full.popularity is not None:
            badge_parts.append(f"★ {full.popularity}")
        if badge_parts:
            self.query_one(".ap-badges", Label).update(f"[dim]{' · '.join(badge_parts)}[/dim]")

        if full.awards:
            self.query_one(".ap-awards", Label).update("  ".join(escape(a) for a in full.awards))
        if full.description:
            self.query_one(".ap-desc", Label).update(escape(_strip_html(full.description)))

        if full.tracks:
            lv = self.query_one(".ap-tracklist", ListView)
            await lv.mount(*[TrackRow(track, i) for i, track in enumerate(full.tracks, 1)])

    @on(ListView.Selected)
    def _on_list_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackRow):
            event.stop()
            rows = list(self.query_one(".ap-tracklist", ListView).query(TrackRow))
            idx = rows.index(event.item)
            queue = [r.track for r in rows[idx + 1 :]]
            self.post_message(AlbumDetailPanel.TrackSelected(event.item.track, queue))


class AlbumScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    DEFAULT_CSS = """
    AlbumScreen { layout: vertical; }
    AlbumScreen #header {
        height: 4;
        padding: 1 2;
        background: $boost;
    }
    AlbumScreen ListView { height: 1fr; }
    """

    def __init__(self, album_id: str) -> None:
        super().__init__()
        self._album_id = album_id

    def compose(self) -> ComposeResult:
        yield Label("Loading…", id="header")
        yield ListView(id="tracklist")
        yield TransportBar()
        yield Footer()

    def on_mount(self) -> None:
        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        album = Album.from_api(await app._client.get_album(self._album_id))
        year = str(album.year) if album.year else "—"
        self.query_one("#header", Label).update(
            f"[bold]{album.title}[/bold]\n"
            f"[dim]{album.artist}  ·  {year}  ·  {album.tracks_count} tracks[/dim]"
        )
        lv = self.query_one("#tracklist", ListView)
        for i, track in enumerate(album.tracks, 1):
            await lv.append(TrackRow(track, i))

    @on(ListView.Selected, "#tracklist")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            lv = self.query_one("#tracklist", ListView)
            rows = list(lv.query(TrackRow))
            idx = rows.index(event.item)
            queue = [r.track for r in rows[idx + 1 :]]
            app.play_track(event.item.track, queue=queue)
            app.pop_screen()

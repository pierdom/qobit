from __future__ import annotations

import io
from typing import TYPE_CHECKING

import httpx
from PIL import Image as PILImage
from rich.markup import escape
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Footer, Label, ListItem, ListView
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from ...qobuz.models import Album, Artist, Track
from ..widgets.transport import TransportBar
from .album_detail import TrackRow
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp

_CARD_IMG_H = 4
_TILE_MIN_W = 22


class ArtistTrackRow(ListItem):
    DEFAULT_CSS = """
    ArtistTrackRow { height: 2; padding: 0 1; }
    ArtistTrackRow Label { width: 1fr; }
    ArtistTrackRow .secondary { color: $text-muted; }
    """

    def __init__(self, track: Track, number: int) -> None:
        super().__init__()
        self.track = track
        self._number = number

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(f"{self._number}. {ICON_TRACK}  {t.display_title}", classes="primary")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")


class AlbumCard(Widget):
    class Selected(Message):
        def __init__(self, album: Album) -> None:
            super().__init__()
            self.album = album

    DEFAULT_CSS = """
    AlbumCard {
        layout: horizontal;
        height: 4;
        padding: 0 1 0 0;
    }
    AlbumCard TGPImage {
        width: 8;
        height: 4;
        margin-right: 1;
    }
    AlbumCard .card-info {
        width: 1fr;
        height: 1fr;
        layout: vertical;
    }
    AlbumCard .card-title {
        height: 2;
        width: 1fr;
        text-style: bold;
        overflow: hidden hidden;
    }
    AlbumCard .card-year {
        height: 1;
        width: 1fr;
        color: $text-muted;
        overflow: hidden hidden;
    }
    AlbumCard.-selected .card-title { color: $accent; text-style: bold; }
    AlbumCard.-selected .card-year { color: $accent; text-style: bold; }
    """

    def __init__(self, album: Album) -> None:
        super().__init__()
        self._album = album

    def compose(self) -> ComposeResult:
        yield TGPImage()
        with Vertical(classes="card-info"):
            yield Label(escape(self._album.title), classes="card-title", markup=True)
            year = str(self._album.year) if self._album.year else "—"
            yield Label(f"[dim]{year}[/dim]", classes="card-year", markup=True)

    def on_mount(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_w = round(_CARD_IMG_H * cell.height / cell.width)
            self.query_one(TGPImage).styles.width = img_w
        if self._album.image_url:
            self._fetch_art(self._album.image_url)

    @work
    async def _fetch_art(self, url: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                r = await http.get(url)
                r.raise_for_status()
            self.query_one(TGPImage).image = PILImage.open(io.BytesIO(r.content))
        except Exception:
            pass

    def on_click(self) -> None:
        self.post_message(AlbumCard.Selected(self._album))


class AlbumGrid(ScrollableContainer):
    """Scrollable grid of AlbumCards; column count adapts to available width."""

    BINDINGS = [
        Binding("up", "move('up')", show=False),
        Binding("down", "move('down')", show=False),
        Binding("left", "move('left')", show=False),
        Binding("right", "move('right')", show=False),
        Binding("enter", "open_selected", "Open album", show=False),
    ]

    DEFAULT_CSS = """
    AlbumGrid {
        layout: grid;
        grid-size: 3;
        grid-rows: 4;
        grid-gutter: 1 2;
    }
    """

    _cols: int = 3
    _cursor: int = -1

    def on_focus(self) -> None:
        if self._cursor == -1:
            self._move_cursor(0)

    def on_resize(self) -> None:
        self._cols = max(1, self.content_size.width // _TILE_MIN_W)
        self.styles.grid_size_columns = self._cols

    def _move_cursor(self, idx: int) -> None:
        cards = list(self.query(AlbumCard))
        if not cards or idx < 0 or idx >= len(cards):
            return
        if self._cursor >= 0 and self._cursor < len(cards):
            cards[self._cursor].remove_class("-selected")
        self._cursor = idx
        cards[idx].add_class("-selected")
        cards[idx].scroll_visible()

    def action_open_selected(self) -> None:
        cards = list(self.query(AlbumCard))
        if 0 <= self._cursor < len(cards):
            self.post_message(AlbumCard.Selected(cards[self._cursor]._album))

    def action_move(self, direction: str) -> None:
        cards = list(self.query(AlbumCard))
        if not cards:
            return
        if self._cursor == -1:
            self._move_cursor(0)
            return
        idx = self._cursor
        target: int | None = None
        if direction == "right" and idx + 1 < len(cards):
            target = idx + 1
        elif direction == "left" and idx > 0:
            target = idx - 1
        elif direction == "down" and idx + self._cols < len(cards):
            target = idx + self._cols
        elif direction == "up" and idx - self._cols >= 0:
            target = idx - self._cols
        if target is not None:
            self._move_cursor(target)


class ArtistScreen(Screen):
    BINDINGS = [Binding("escape", "navigate_back", "Back", priority=True)]

    DEFAULT_CSS = """
    ArtistScreen { layout: vertical; }

    ArtistScreen #breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $boost;
    }

    ArtistScreen #breadcrumb:hover {
        color: $text;
        background: $panel;
    }

    ArtistScreen #artist-header {
        height: 8;
        padding: 1 0 0 0;
        background: $boost;
    }

    ArtistScreen #artist-image {
        width: 16;
        height: 7;
        margin-right: 1;
        border: round $accent 40%;
    }

    ArtistScreen #bio-section {
        width: 1fr;
        height: 7;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }

    ArtistScreen #bio-section:focus {
        border: round $accent;
        border-title-color: $accent;
    }

    ArtistScreen #bio {
        width: 1fr;
        color: $text-muted;
    }

    ArtistScreen #main-content {
        height: 1fr;
        margin: 0;
        padding: 0;
    }

    ArtistScreen #artist-view {
        height: 1fr;
        layout: vertical;
        margin: 0;
        padding: 0;
    }

    ArtistScreen #top-tracks {
        height: 1fr;
        margin: 0;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }

    ArtistScreen #top-tracks:focus {
        border: round $accent;
        border-title-color: $accent;
    }

    ArtistScreen #albums {
        height: 1fr;
        margin: 0;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }

    ArtistScreen #albums:focus {
        border: round $accent;
        border-title-color: $accent;
    }

    ArtistScreen #album-view {
        height: 1fr;
        layout: vertical;
        margin: 0;
        padding: 0;
    }

    ArtistScreen #album-panel {
        height: 1fr;
        margin: 0;
        layout: vertical;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }

    ArtistScreen #album-panel:focus-within {
        border: round $accent;
        border-title-color: $accent;
    }

    ArtistScreen #album-detail-header {
        height: 16;
        layout: horizontal;
        padding: 1 2;
        background: $boost;
    }

    ArtistScreen #album-art {
        width: 16;
        height: 14;
        margin-right: 2;
    }

    ArtistScreen #album-meta {
        width: 1fr;
        height: 14;
    }

    ArtistScreen #album-title {
        height: auto;
        text-style: bold;
    }

    ArtistScreen #album-version {
        height: auto;
        color: $text-muted;
        text-style: italic;
    }

    ArtistScreen #album-sub {
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }

    ArtistScreen #album-badges {
        height: auto;
        color: $text-muted;
    }

    ArtistScreen #album-awards {
        height: auto;
        color: $accent 70%;
        margin-top: 1;
    }

    ArtistScreen #album-desc {
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }

    ArtistScreen #album-tracklist {
        height: 1fr;
        border-top: solid $accent 40%;
    }

    ArtistScreen #album-panel:focus-within #album-tracklist {
        border-top: solid $accent;
    }

    ArtistScreen TransportBar {
        margin: 0 0 1 0;
    }
    """

    def __init__(self, artist_id: str, source: str = "Search") -> None:
        super().__init__()
        self._artist_id = artist_id
        self._source = source
        self._album_view_active = False

    def compose(self) -> ComposeResult:
        yield Label(f"← {self._source}", id="breadcrumb")
        with Horizontal(id="artist-header"):
            yield TGPImage(id="artist-image")
            with VerticalScroll(id="bio-section"):
                yield Label("", id="bio")
        with ContentSwitcher(initial="artist-view", id="main-content"):
            with Vertical(id="artist-view"):
                yield ListView(id="top-tracks")
                yield AlbumGrid(id="albums")
            with Vertical(id="album-view"):
                with Vertical(id="album-panel"):
                    with Horizontal(id="album-detail-header"):
                        yield TGPImage(id="album-art")
                        with VerticalScroll(id="album-meta"):
                            yield Label("", id="album-title", markup=True)
                            yield Label("", id="album-version", markup=True)
                            yield Label("", id="album-sub", markup=True)
                            yield Label("", id="album-badges", markup=True)
                            yield Label("", id="album-awards", markup=True)
                            yield Label("", id="album-desc", markup=True)
                    yield ListView(id="album-tracklist")
        yield TransportBar()
        yield Footer()

    def on_mount(self) -> None:
        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        self._fit_image_width()
        self._fit_album_art_width()
        self.query_one("#bio-section").border_title = "Loading…"
        self.query_one("#top-tracks", ListView).border_title = "Top Tracks"
        self.query_one("#albums", AlbumGrid).border_title = "Albums & EPs"
        self._load()

    def action_navigate_back(self) -> None:
        if self._album_view_active:
            self._show_artist_view()
        else:
            self.app.pop_screen()

    def _open_album(self, album: Album) -> None:
        self.query_one("#album-title", Label).update(escape(album.title))
        year = str(album.year) if album.year else "—"
        parts: list[str] = [escape(album.artist), year]
        if album.genre:
            parts.append(escape(album.genre))
        self.query_one("#album-sub", Label).update(f"[dim]{' · '.join(parts)}[/dim]")
        self.query_one("#album-version", Label).update("")
        self.query_one("#album-badges", Label).update("")
        self.query_one("#album-awards", Label).update("")
        self.query_one("#album-desc", Label).update("")
        lv = self.query_one("#album-tracklist", ListView)
        lv.clear()
        self.query_one("#album-panel").border_title = escape(album.title)

        self.query_one("#main-content", ContentSwitcher).current = "album-view"
        self._album_view_active = True
        lv.focus()

        if album.image_url:
            self._load_album_art(album.image_url)
        self._load_album_tracks(album)

    def _show_artist_view(self) -> None:
        self.query_one("#main-content", ContentSwitcher).current = "artist-view"
        self._album_view_active = False
        self.query_one("#albums", AlbumGrid).focus()

    def _fit_image_width(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_h = 7
            img_w = round(img_h * cell.height / cell.width)
            self.query_one("#artist-image").styles.width = img_w

    def _fit_album_art_width(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_h = 14
            img_w = round(img_h * cell.height / cell.width)
            self.query_one("#album-art").styles.width = img_w

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        artist = Artist.from_api(await app._client.get_artist_page(self._artist_id))

        self.query_one("#bio-section").border_title = escape(artist.name)

        if artist.biography:
            self.query_one("#bio", Label).update(escape(artist.biography))

        if artist.image_url:
            self._load_artist_image(artist.image_url)

        lv = self.query_one("#top-tracks", ListView)
        for i, track in enumerate(artist.tracks, 1):
            await lv.append(ArtistTrackRow(track, i))

        grid = self.query_one("#albums", AlbumGrid)
        for album in artist.albums:
            await grid.mount(AlbumCard(album))

    @work
    async def _load_artist_image(self, url: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                r = await http.get(url)
                r.raise_for_status()
            self.query_one("#artist-image", TGPImage).image = PILImage.open(io.BytesIO(r.content))
        except Exception:
            pass

    @work
    async def _load_album_art(self, url: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                r = await http.get(url)
                r.raise_for_status()
            self.query_one("#album-art", TGPImage).image = PILImage.open(io.BytesIO(r.content))
        except Exception:
            pass

    @work
    async def _load_album_tracks(self, album: Album) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        full = Album.from_api(await app._client.get_album(album.id))

        if full.version:
            self.query_one("#album-version", Label).update(
                f"[dim italic]{escape(full.version)}[/dim italic]"
            )

        year = str(full.year) if full.year else "—"
        sub_parts: list[str] = [escape(full.artist), year]
        if full.genre:
            sub_parts.append(escape(full.genre))
        if dur := full.total_duration_str:
            sub_parts.append(dur)
        self.query_one("#album-sub", Label).update(f"[dim]{' · '.join(sub_parts)}[/dim]")

        badge_parts: list[str] = []
        if full.label:
            badge_parts.append(escape(full.label))
        if q := full.quality_badge:
            badge_parts.append(q)
        if full.popularity is not None:
            badge_parts.append(f"★ {full.popularity}")
        if badge_parts:
            self.query_one("#album-badges", Label).update(f"[dim]{' · '.join(badge_parts)}[/dim]")

        if full.awards:
            self.query_one("#album-awards", Label).update("  ".join(escape(a) for a in full.awards))

        if full.description:
            self.query_one("#album-desc", Label).update(escape(full.description))

        lv = self.query_one("#album-tracklist", ListView)
        for i, track in enumerate(full.tracks, 1):
            await lv.append(TrackRow(track, i))

    @on(events.Click, "#breadcrumb")
    def _on_breadcrumb_click(self) -> None:
        self.app.pop_screen()

    @on(ListView.Selected, "#top-tracks")
    def _on_top_track_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ArtistTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.play_track(event.item.track)

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(ListView.Selected, "#album-tracklist")
    def _on_album_track_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.play_track(event.item.track)

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import httpx
from PIL import Image as PILImage
from rich.markup import escape
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalScroll, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Label, ListItem, ListView
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from ...qobuz.models import Album, Artist, Track
from ..widgets.transport import TransportBar
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp

_CARD_IMG_H = 5  # album card image height in cells


class ArtistTrackRow(ListItem):
    DEFAULT_CSS = """
    ArtistTrackRow { height: 2; padding: 0 1; }
    ArtistTrackRow Label { width: 1fr; }
    """

    def __init__(self, track: Track, number: int) -> None:
        super().__init__()
        self.track = track
        self._number = number

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(
            f"[dim]{self._number}. {ICON_TRACK}[/dim]  {escape(t.display_title)}",
            markup=True,
        )
        yield Label(f"     [dim]{escape(t.album)}  ·  {t.duration_str}[/dim]", markup=True)


class AlbumCard(Widget, can_focus=True):
    DEFAULT_CSS = """
    AlbumCard {
        layout: vertical;
        width: 12;
        height: auto;
        padding: 0 1 0 0;
    }
    AlbumCard TGPImage {
        width: 10;
        height: 5;
    }
    AlbumCard .card-title {
        width: 1fr;
        height: 1;
        text-style: bold;
        overflow: hidden hidden;
    }
    AlbumCard .card-year {
        width: 1fr;
        height: 1;
        color: $text-muted;
    }
    AlbumCard:focus {
        background: $accent 10%;
    }
    """

    def __init__(self, album: Album) -> None:
        super().__init__()
        self._album = album

    def compose(self) -> ComposeResult:
        yield TGPImage()
        yield Label(escape(self._album.title), classes="card-title", markup=True)
        year = str(self._album.year) if self._album.year else "—"
        yield Label(f"[dim]{year}[/dim]", classes="card-year", markup=True)

    def on_mount(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_w = round(_CARD_IMG_H * cell.height / cell.width)
            img = self.query_one(TGPImage)
            img.styles.width = img_w
            self.styles.width = img_w + 1  # +1 right padding
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


class ArtistScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

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
        height: 9;
        padding: 1 1 1 2;
        background: $boost;
    }

    ArtistScreen #artist-image {
        width: 16;
        height: 7;
        margin-right: 1;
    }

    ArtistScreen #bio-section {
        width: 1fr;
        height: 7;
        border: round $panel;
        border-title-color: $text-muted;
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

    ArtistScreen #top-tracks {
        height: 1fr;
        margin: 1;
        border: round $panel;
        border-title-color: $text-muted;
        border-title-style: bold;
    }

    ArtistScreen #top-tracks:focus {
        border: round $accent;
        border-title-color: $accent;
    }

    ArtistScreen #albums {
        height: 9;
        margin: 0 1 1 1;
        border: round $panel;
        border-title-color: $text-muted;
        border-title-style: bold;
    }

    ArtistScreen #albums:focus {
        border: round $accent;
        border-title-color: $accent;
    }
    """

    def __init__(self, artist_id: str, source: str = "Search") -> None:
        super().__init__()
        self._artist_id = artist_id
        self._source = source

    def compose(self) -> ComposeResult:
        yield Label(f"← {self._source}", id="breadcrumb")
        with Horizontal(id="artist-header"):
            yield TGPImage(id="artist-image")
            with VerticalScroll(id="bio-section"):
                yield Label("", id="bio")
        yield ListView(id="top-tracks")
        yield HorizontalScroll(id="albums")
        yield TransportBar()
        yield Footer()

    def on_mount(self) -> None:
        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        self._fit_image_width()
        self.query_one("#bio-section").border_title = "Loading…"
        self.query_one("#top-tracks", ListView).border_title = "Top Tracks"
        self.query_one("#albums", HorizontalScroll).border_title = "Albums"
        self._load()
        self.app.sync_transport_bar()  # type: ignore[attr-defined]

    def _fit_image_width(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_h = 7  # must match #artist-image height in CSS
            img_w = round(img_h * cell.height / cell.width)
            self.query_one("#artist-image").styles.width = img_w

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        artist = Artist.from_api(await app._client.get_artist_page(self._artist_id))

        self.query_one("#bio-section").border_title = escape(artist.name)

        if artist.biography:
            self.query_one("#bio", Label).update(escape(artist.biography))

        if artist.image_url:
            self._load_image(artist.image_url)

        lv = self.query_one("#top-tracks", ListView)
        for i, track in enumerate(artist.tracks, 1):
            await lv.append(ArtistTrackRow(track, i))

        hs = self.query_one("#albums", HorizontalScroll)
        for album in artist.albums:
            await hs.mount(AlbumCard(album))

    @work
    async def _load_image(self, url: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                r = await http.get(url)
                r.raise_for_status()
            img = PILImage.open(io.BytesIO(r.content))
            self.query_one("#artist-image", TGPImage).image = img
        except Exception:
            pass

    @on(events.Click, "#breadcrumb")
    def _on_breadcrumb_click(self) -> None:
        self.app.pop_screen()

    @on(ListView.Selected, "#top-tracks")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ArtistTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.play_track(event.item.track)

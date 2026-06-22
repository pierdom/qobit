from __future__ import annotations

import html as _html
import re
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Label, ListItem, ListView
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from ...qobuz.models import Album, Artist, Track
from .._images import fetch_image
from ..widgets.lists import TrackListView
from ..widgets.transport import TransportBar
from .search import ICON_FAV, ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp


def _strip_html(text: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


class TrackRow(ListItem):
    DEFAULT_CSS = """
    TrackRow { height: 1; padding: 0 1; }
    TrackRow Label { width: 1fr; }
    """

    def __init__(self, track: Track, number: int, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._number = number
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        num = f"{self._number:2}. {ICON_TRACK}"
        heart = (f"  {ICON_FAV}", "$accent") if self._favorite else ""
        return Content.assemble(f"{num}  {t.display_title}  {t.duration_str}", heart)

    def compose(self) -> ComposeResult:
        yield Label(self._primary(), classes="primary")

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self.query_one(".primary", Label).update(self._primary())


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
    /* Constrain the text rows to the meta column so long text wraps instead of
       overflowing to the right (the description in particular). */
    AlbumDetailPanel .ap-meta Label { width: 1fr; }
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
    /* Vertical-compact modes (see set_compact): halve the album art, then lay
       the tracklist out in two columns when space is tight. */
    AlbumDetailPanel.-small-art .ap-header { height: 9; }
    AlbumDetailPanel.-small-art .ap-art    { height: 7; }
    AlbumDetailPanel.-small-art .ap-meta   { height: 7; }
    AlbumDetailPanel.-two-col .ap-tracklist {
        layout: grid;
        grid-size: 2;
        grid-rows: 1;
        grid-gutter: 0 2;
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
        yield TrackListView(classes="ap-tracklist")

    def on_mount(self) -> None:
        self._size_art(14)

    def _size_art(self, height: int) -> None:
        """Set the art width to keep it square for the given cell height."""
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            self.query_one(TGPImage).styles.width = round(height * cell.height / cell.width)

    def set_compact(self, small_art: bool, two_col: bool) -> None:
        """Vertical-responsive layout: halve the album art and/or lay the
        tracklist out in two columns when the page is short on height."""
        self.set_class(small_art, "-small-art")
        self.set_class(two_col, "-two-col")
        self._size_art(7 if small_art else 14)

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
            self._set_optional(cls, "")
        self.query_one(".ap-tracklist", ListView).clear()
        if album.image_url:
            self._fetch_art(album.image_url)
        self._fetch_full_album(album)

    def focus_tracklist(self) -> None:
        self.query_one(".ap-tracklist", ListView).focus()

    def _set_optional(self, selector: str, content: str) -> None:
        """Update an optional meta label and collapse it (display: none) when
        empty, so it leaves no blank row / margin behind."""
        label = self.query_one(selector, Label)
        label.update(content)
        label.display = bool(content)

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

        self._set_optional(
            ".ap-version",
            f"[dim italic]{escape(full.version)}[/dim italic]" if full.version else "",
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
        self._set_optional(
            ".ap-badges", f"[dim]{' · '.join(badge_parts)}[/dim]" if badge_parts else ""
        )

        self._set_optional(
            ".ap-awards", "  ".join(escape(a) for a in full.awards) if full.awards else ""
        )
        self._set_optional(
            ".ap-desc", escape(_strip_html(full.description)) if full.description else ""
        )

        if full.tracks:
            fav_ids = await app.ensure_favorite_ids()
            lv = self.query_one(".ap-tracklist", ListView)
            await lv.mount(
                *[
                    TrackRow(track, i, str(track.id) in fav_ids)
                    for i, track in enumerate(full.tracks, 1)
                ]
            )

    @on(ListView.Selected)
    def _on_list_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackRow):
            event.stop()
            rows = list(self.query_one(".ap-tracklist", ListView).query(TrackRow))
            idx = rows.index(event.item)
            queue = [r.track for r in rows[idx + 1 :]]
            self.post_message(AlbumDetailPanel.TrackSelected(event.item.track, queue))


# Vertical-responsive thresholds shared with AlbumsView.
_HIDE_ARTIST_BELOW = 32
_SMALL_ART_BELOW = 24
_TWO_COL_BELOW = 17


class AlbumScreen(Screen):
    """Full-screen album detail pushed from the Search page.

    Wraps AlbumDetailPanel so Search → Album looks identical to the inline
    album view opened from the Albums and Artists tabs: big art, rich metadata
    header, formatted tracklist with favourites.  Escape returns to Search."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    DEFAULT_CSS = """
    AlbumScreen { layout: vertical; }
    AlbumScreen #breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $boost;
    }
    AlbumScreen #breadcrumb:hover { color: $text; background: $panel; }
    AlbumScreen AlbumDetailPanel { height: 1fr; margin: 0 1 1 1; }
    """

    def __init__(self, album: Album, source: str = "Search results") -> None:
        super().__init__()
        self._album = album
        self._source = source

    def compose(self) -> ComposeResult:
        # Lazy import avoids a circular dependency: artist_detail imports
        # AlbumDetailPanel from this module at the top level.
        from .artist_detail import ArtistHeader

        yield Label(f"← {self._source}", id="breadcrumb")
        yield ArtistHeader()
        yield AlbumDetailPanel(id="album-panel")
        yield TransportBar()
        yield Footer()

    def on_mount(self) -> None:
        from .artist_detail import ArtistHeader

        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        panel = self.query_one("#album-panel", AlbumDetailPanel)
        panel.load(self._album)
        header = self.query_one(ArtistHeader)
        header.set_loading(self._album.artist or "Artist")
        if self._album.artist_id:
            self._load_artist_info(self._album.artist_id)
        self.call_after_refresh(panel.focus_tracklist)

    def on_resize(self, event: events.Resize) -> None:
        from .artist_detail import ArtistHeader

        h = event.size.height
        self.query_one(ArtistHeader).display = h >= _HIDE_ARTIST_BELOW
        self.query_one("#album-panel", AlbumDetailPanel).set_compact(
            small_art=h < _SMALL_ART_BELOW,
            two_col=h < _TWO_COL_BELOW,
        )

    @work
    async def _load_artist_info(self, artist_id: str) -> None:
        from .artist_detail import ArtistHeader

        app: QobitApp = self.app  # type: ignore[assignment]
        artist = Artist.from_api(await app._client.get_artist_detail(artist_id))
        if self.is_mounted:
            try:
                self.query_one(ArtistHeader).populate(artist)
            except NoMatches:
                pass

    @on(events.Click, "#breadcrumb")
    def _on_breadcrumb_click(self) -> None:
        self.app.pop_screen()

    @on(AlbumDetailPanel.TrackSelected)
    def _on_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track, queue=event.queue)

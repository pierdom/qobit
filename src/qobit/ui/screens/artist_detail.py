from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Footer, Label, ListItem, ListView
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from ...qobuz.models import Album, Artist, Track
from .._images import fetch_image
from ..widgets.transport import TransportBar
from .album_detail import AlbumDetailPanel
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp

_CARD_IMG_H = 4
_CARD_IMG_H_FULL = 6
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
        padding: 0;
    }
    AlbumCard.full {
        height: 6;
    }
    AlbumCard TGPImage {
        width: 8;
        height: 4;
        margin-right: 1;
    }
    AlbumCard.full TGPImage {
        height: 6;
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
    AlbumCard.full .card-title {
        height: 3;
    }
    AlbumCard .card-artist {
        display: none;
        height: 1;
        width: 1fr;
        color: $text-muted;
        overflow: hidden hidden;
    }
    AlbumCard.full .card-artist {
        display: block;
    }
    AlbumCard .card-year {
        height: 1;
        width: 1fr;
        color: $text-muted;
        overflow: hidden hidden;
    }
    AlbumCard.-selected .card-title { color: $accent; text-style: bold; }
    AlbumCard.-selected .card-artist { color: $accent; }
    AlbumCard.-selected .card-year { color: $accent; text-style: bold; }
    """

    def __init__(self, album: Album, show_artist: bool = False) -> None:
        super().__init__(classes="full" if show_artist else "")
        self._album = album
        self._show_artist = show_artist

    def compose(self) -> ComposeResult:
        yield TGPImage()
        with Vertical(classes="card-info"):
            yield Label(escape(self._album.title), classes="card-title", markup=True)
            yield Label(escape(self._album.artist or ""), classes="card-artist", markup=True)
            year = str(self._album.year) if self._album.year else "—"
            yield Label(f"[dim]{year}[/dim]", classes="card-year", markup=True)

    def on_mount(self) -> None:
        img_h = _CARD_IMG_H_FULL if self._show_artist else _CARD_IMG_H
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_w = round(img_h * cell.height / cell.width)
            self.query_one(TGPImage).styles.width = img_w
        if self._album.image_url:
            self._fetch_art(self._album.image_url)

    @work
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_mounted:
            try:
                self.query_one(TGPImage).image = img
            except NoMatches:
                pass

    def on_click(self) -> None:
        self.post_message(AlbumCard.Selected(self._album))


class _GridNav:
    """Shared Page/Home/End navigation for the card grids.

    Moves the cursor by a page (visible rows × columns) and top-aligns the
    scroll so the selection follows the visible page, mirroring PagedListView.
    Relies on the host grid providing ``_visible_cards()``, ``_move_cursor()``,
    ``_cols`` and ``_cursor`` — both AlbumGrid and ArtistGrid do."""

    def _page_rows(self) -> int:
        cards = self._visible_cards()  # type: ignore[attr-defined]
        if not cards:
            return 1
        # Row pitch = gap between a card and the one a full row below it
        # (height + gutter); fall back to a single card's height.
        pitch = cards[0].region.height
        if len(cards) > self._cols:  # type: ignore[attr-defined]
            step = cards[self._cols].region.y - cards[0].region.y  # type: ignore[attr-defined]
            if step > 0:
                pitch = step
        if pitch <= 0:
            pitch = 1
        return max(1, self.scrollable_content_region.height // pitch)  # type: ignore[attr-defined]

    def action_page(self, direction: str) -> None:
        cards = self._visible_cards()  # type: ignore[attr-defined]
        if not cards:
            return
        if self._cursor == -1:  # type: ignore[attr-defined]
            self._move_cursor(0, cards, top=True)  # type: ignore[attr-defined]
            return
        delta = self._page_rows() * self._cols  # type: ignore[attr-defined]
        if direction == "up":
            target = max(0, self._cursor - delta)  # type: ignore[attr-defined]
        else:
            target = min(len(cards) - 1, self._cursor + delta)  # type: ignore[attr-defined]
        self._move_cursor(target, cards, top=True)  # type: ignore[attr-defined]

    def action_jump_first(self) -> None:
        cards = self._visible_cards()  # type: ignore[attr-defined]
        if cards:
            self._move_cursor(0, cards, top=True)  # type: ignore[attr-defined]

    def action_jump_last(self) -> None:
        cards = self._visible_cards()  # type: ignore[attr-defined]
        if cards:
            self._move_cursor(len(cards) - 1, cards, top=True)  # type: ignore[attr-defined]


class AlbumGrid(_GridNav, ScrollableContainer):
    """Scrollable grid of AlbumCards; column count adapts to available width."""

    BINDINGS = [
        Binding("up", "move('up')", show=False),
        Binding("down", "move('down')", show=False),
        Binding("left", "move('left')", show=False),
        Binding("right", "move('right')", show=False),
        Binding("pageup", "page('up')", show=False),
        Binding("pagedown", "page('down')", show=False),
        Binding("home", "jump_first", show=False),
        Binding("end", "jump_last", show=False),
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

    def __init__(self, *args: object, tile_min_width: int = _TILE_MIN_W, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._tile_min_width = tile_min_width

    def on_focus(self) -> None:
        if self._cursor == -1:
            self._move_cursor(0)

    def on_resize(self) -> None:
        self._cols = max(1, self.content_size.width // self._tile_min_width)
        self.styles.grid_size_columns = self._cols

    def _visible_cards(self) -> list[AlbumCard]:
        return [c for c in self.query(AlbumCard) if c.display]

    def filter_cards(self, visible_ids: set[str] | None) -> bool:
        """Hide/show mounted cards by album id without remounting (so the
        Kitty images are never re-transmitted).  ``None`` shows everything.
        Returns whether any card is visible."""
        any_visible = False
        for card in self.query(AlbumCard):
            vis = visible_ids is None or card._album.id in visible_ids
            if bool(card.display) != vis:
                card.display = vis
            any_visible = any_visible or vis
        self._cursor = -1
        self.scroll_home(animate=False)
        return any_visible

    def _move_cursor(
        self, idx: int, cards: list[AlbumCard] | None = None, top: bool = False
    ) -> None:
        if cards is None:
            cards = self._visible_cards()
        if not cards or idx < 0 or idx >= len(cards):
            return
        if self._cursor >= 0 and self._cursor < len(cards):
            cards[self._cursor].remove_class("-selected")
        self._cursor = idx
        cards[idx].add_class("-selected")
        if top:
            cards[idx].scroll_visible(animate=False, top=True)
        else:
            cards[idx].scroll_visible()

    def action_open_selected(self) -> None:
        cards = self._visible_cards()
        if 0 <= self._cursor < len(cards):
            self.post_message(AlbumCard.Selected(cards[self._cursor]._album))

    def action_move(self, direction: str) -> None:
        cards = self._visible_cards()
        if not cards:
            return
        if self._cursor == -1:
            self._move_cursor(0, cards)
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
            self._move_cursor(target, cards)


class ArtistCard(Widget):
    class Selected(Message):
        def __init__(self, artist: Artist) -> None:
            super().__init__()
            self.artist = artist

    DEFAULT_CSS = """
    ArtistCard {
        layout: horizontal;
        height: 5;
        padding: 0;
    }
    ArtistCard TGPImage {
        width: 8;
        height: 5;
        margin-right: 1;
    }
    ArtistCard .card-info {
        width: 1fr;
        height: 1fr;
        layout: vertical;
    }
    ArtistCard .card-name {
        height: 3;
        width: 1fr;
        text-style: bold;
        overflow: hidden hidden;
    }
    ArtistCard .card-meta {
        height: 1;
        width: 1fr;
        color: $text-muted;
        overflow: hidden hidden;
    }
    ArtistCard.-selected .card-name { color: $accent; text-style: bold; }
    ArtistCard.-selected .card-meta { color: $accent; }
    """

    def __init__(self, artist: Artist) -> None:
        super().__init__()
        self._artist = artist

    @property
    def artist(self) -> Artist:
        return self._artist

    def compose(self) -> ComposeResult:
        yield TGPImage()
        with Vertical(classes="card-info"):
            yield Label(escape(self._artist.name), classes="card-name", markup=True)
            count = self._artist.albums_count
            meta = f"[dim]{count} albums[/dim]" if count else ""
            yield Label(meta, classes="card-meta", markup=True)

    def on_mount(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_w = round(5 * cell.height / cell.width)
            self.query_one(TGPImage).styles.width = img_w
        if self._artist.image_url:
            self._fetch_art(self._artist.image_url)

    @work
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_mounted:
            try:
                self.query_one(TGPImage).image = img
            except NoMatches:
                pass

    def on_click(self) -> None:
        self.post_message(ArtistCard.Selected(self._artist))


class ArtistGrid(_GridNav, ScrollableContainer):
    """Scrollable grid of ArtistCards; column count adapts to available width."""

    BINDINGS = [
        Binding("up", "move('up')", show=False),
        Binding("down", "move('down')", show=False),
        Binding("left", "move('left')", show=False),
        Binding("right", "move('right')", show=False),
        Binding("pageup", "page('up')", show=False),
        Binding("pagedown", "page('down')", show=False),
        Binding("home", "jump_first", show=False),
        Binding("end", "jump_last", show=False),
        Binding("enter", "open_selected", "Open artist", show=False),
    ]

    DEFAULT_CSS = """
    ArtistGrid {
        layout: grid;
        grid-size: 3;
        grid-rows: 5;
        grid-gutter: 1 2;
    }
    """

    _cols: int = 3
    _cursor: int = -1

    def __init__(self, *args: object, tile_min_width: int = _TILE_MIN_W, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._tile_min_width = tile_min_width

    def on_focus(self) -> None:
        if self._cursor == -1:
            self._move_cursor(0)

    def on_resize(self) -> None:
        self._cols = max(1, self.content_size.width // self._tile_min_width)
        self.styles.grid_size_columns = self._cols

    def _visible_cards(self) -> list[ArtistCard]:
        return [c for c in self.query(ArtistCard) if c.display]

    def filter_cards(self, visible_ids: set[str] | None) -> bool:
        """Hide/show mounted cards by artist id without remounting (so the
        Kitty images are never re-transmitted).  ``None`` shows everything.
        Returns whether any card is visible."""
        any_visible = False
        for card in self.query(ArtistCard):
            vis = visible_ids is None or card._artist.id in visible_ids
            if bool(card.display) != vis:
                card.display = vis
            any_visible = any_visible or vis
        self._cursor = -1
        self.scroll_home(animate=False)
        return any_visible

    def _move_cursor(
        self, idx: int, cards: list[ArtistCard] | None = None, top: bool = False
    ) -> None:
        if cards is None:
            cards = self._visible_cards()
        if not cards or idx < 0 or idx >= len(cards):
            return
        if self._cursor >= 0 and self._cursor < len(cards):
            cards[self._cursor].remove_class("-selected")
        self._cursor = idx
        cards[idx].add_class("-selected")
        if top:
            cards[idx].scroll_visible(animate=False, top=True)
        else:
            cards[idx].scroll_visible()

    def action_open_selected(self) -> None:
        cards = self._visible_cards()
        if 0 <= self._cursor < len(cards):
            self.post_message(ArtistCard.Selected(cards[self._cursor]._artist))

    def action_move(self, direction: str) -> None:
        cards = self._visible_cards()
        if not cards:
            return
        if self._cursor == -1:
            self._move_cursor(0, cards)
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
            self._move_cursor(target, cards)


class ArtistHeader(Widget):
    """Reusable artist image + biography header.

    Call ``set_loading(name)`` immediately when switching context, then
    ``populate(artist)`` once the API data arrives.
    """

    DEFAULT_CSS = """
    ArtistHeader {
        height: 8;
        padding: 1 0 0 0;
        background: $boost;
        layout: horizontal;
    }
    ArtistHeader .ah-image {
        width: 16;
        height: 7;
        margin-right: 1;
        border: round $accent 40%;
    }
    ArtistHeader .ah-bio-section {
        width: 1fr;
        height: 7;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }
    ArtistHeader .ah-bio-section:focus {
        border: round $accent;
        border-title-color: $accent;
    }
    ArtistHeader .ah-bio {
        width: 1fr;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield TGPImage(classes="ah-image")
        with VerticalScroll(classes="ah-bio-section"):
            yield Label("", classes="ah-bio")

    def on_mount(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_w = round(7 * cell.height / cell.width)
            self.query_one(TGPImage).styles.width = img_w

    def set_loading(self, name: str = "Loading…") -> None:
        self.query_one(".ah-bio-section").border_title = name
        self.query_one(".ah-bio", Label).update("")

    def populate(self, artist: Artist) -> None:
        self.query_one(".ah-bio-section").border_title = escape(artist.name)
        if artist.biography:
            self.query_one(".ah-bio", Label).update(escape(artist.biography))
        if artist.image_url:
            self._fetch_image(artist.image_url)

    @work
    async def _fetch_image(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_mounted:
            try:
                self.query_one(TGPImage).image = img
            except NoMatches:
                pass


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
        yield ArtistHeader()
        with ContentSwitcher(initial="artist-view", id="main-content"):
            with Vertical(id="artist-view"):
                yield ListView(id="top-tracks")
                yield AlbumGrid(id="albums")
            with Vertical(id="album-view"):
                yield AlbumDetailPanel(id="album-panel")
        yield TransportBar()
        yield Footer()

    def on_mount(self) -> None:
        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        self.query_one(ArtistHeader).set_loading()
        self.query_one("#top-tracks", ListView).border_title = "Top Tracks"
        self.query_one("#albums", AlbumGrid).border_title = "Albums & EPs"
        self._load_detail()
        self._load_tracks()

    def action_navigate_back(self) -> None:
        if self._album_view_active:
            self._show_artist_view()
        else:
            app: QobitApp = self.app  # type: ignore[assignment]
            app.action_switch_tab(self._source.lower())
            app.pop_screen()

    def _open_album(self, album: Album) -> None:
        panel = self.query_one("#album-panel", AlbumDetailPanel)
        panel.load(album)
        self.query_one("#main-content", ContentSwitcher).current = "album-view"
        self._album_view_active = True
        panel.focus_tracklist()

    def _show_artist_view(self) -> None:
        self.query_one("#main-content", ContentSwitcher).current = "artist-view"
        self._album_view_active = False
        self.query_one("#albums", AlbumGrid).focus()

    @work
    async def _load_detail(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        artist = Artist.from_api(await app._client.get_artist_detail(self._artist_id))
        self.query_one(ArtistHeader).populate(artist)
        if artist.albums:
            grid = self.query_one("#albums", AlbumGrid)
            await grid.mount(*[AlbumCard(album) for album in artist.albums])

    @work
    async def _load_tracks(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        items = await app._client.get_artist_top_tracks(self._artist_id)
        if items:
            lv = self.query_one("#top-tracks", ListView)
            rows = [ArtistTrackRow(Track.from_api(raw), i) for i, raw in enumerate(items, 1)]
            await lv.mount(*rows)

    @on(events.Click, "#breadcrumb")
    def _on_breadcrumb_click(self) -> None:
        self.app.pop_screen()

    @on(ListView.Selected, "#top-tracks")
    def _on_top_track_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ArtistTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            lv = self.query_one("#top-tracks", ListView)
            rows = list(lv.query(ArtistTrackRow))
            idx = rows.index(event.item)
            queue = [r.track for r in rows[idx + 1 :]]
            app.play_track(event.item.track, queue=queue)

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_album_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track, queue=event.queue)

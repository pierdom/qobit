from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import Screen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Footer, Label, ListItem, ListView
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from ...qobuz.models import Album, Artist, Track
from .._images import fetch_image
from .._now_playing import NowPlayingRowMixin, NowPlayingViewMixin, heart_suffix, playing_content
from ..widgets.lists import TrackListView
from ..widgets.transport import TransportBar
from .album_detail import AlbumDetailPanel
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp

_CARD_IMG_H = 4
_CARD_IMG_H_FULL = 6
_TILE_MIN_W = 22


def _set_art_width(image: TGPImage, height: int) -> None:
    """Fix a card/header art widget's width so it renders square for the given
    cell height, using the terminal's actual cell aspect ratio. Shared by every
    grid card (AlbumCard, ArtistCard, PlaylistCard) and detail header that
    sizes a TGPImage against a fixed height."""
    cell = get_cell_size()
    if cell.width > 0 and cell.height > 0:
        image.styles.width = round(height * cell.height / cell.width)


class ArtistTrackRow(NowPlayingRowMixin, ListItem):
    DEFAULT_CSS = """
    ArtistTrackRow { height: 2; padding: 0 1; }
    ArtistTrackRow Label { width: 1fr; }
    ArtistTrackRow .secondary { color: $text-muted; }
    """

    def __init__(self, track: Track, number: int, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._number = number
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = heart_suffix(self._favorite)
        if self._is_playing:
            text = f"{self._number}.  {t.display_title}"
        else:
            text = f"{self._number}. {ICON_TRACK}  {t.display_title}"
        return playing_content(text, self._is_playing, self._is_paused, heart)

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="primary")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self._refresh_primary()


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
    /* outline draws on top of the card instead of adding a border row/column,
       so the tight fixed-height layout above never has to make room for it. */
    AlbumCard.-selected {
        outline: round $accent;
        background: $accent 35%;
    }
    AlbumCard.-selected .card-title { color: $accent; text-style: bold; }
    AlbumCard.-selected .card-artist { color: $accent; }
    AlbumCard.-selected .card-year { color: $accent; text-style: bold; }
    """

    def __init__(self, album: Album, show_artist: bool = False) -> None:
        super().__init__(classes="full" if show_artist else "")
        self._album = album
        self._show_artist = show_artist
        self._art_requested = False

    def compose(self) -> ComposeResult:
        yield TGPImage()
        with Vertical(classes="card-info"):
            yield Label(escape(self._album.title), classes="card-title", markup=True)
            yield Label(escape(self._album.artist or ""), classes="card-artist", markup=True)
            year = str(self._album.year) if self._album.year else "—"
            yield Label(f"[dim]{year}[/dim]", classes="card-year", markup=True)

    def on_mount(self) -> None:
        img_h = _CARD_IMG_H_FULL if self._show_artist else _CARD_IMG_H
        _set_art_width(self.query_one(TGPImage), img_h)
        # Art is fetched lazily by the parent grid's sync_visible_art() once
        # this card's region is known — not eagerly here — so a 200-card
        # favourites grid doesn't fire 200 fetches for art never scrolled to.

    def maybe_fetch_art(self) -> None:
        """Fetch art once, the first time the grid decides this card is within
        the visible range (+ margin). No-op on repeat calls."""
        if self._art_requested or not self._album.image_url:
            return
        self._art_requested = True
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
    """Shared Page/Home/End navigation, plus viewport-lazy art loading, for the
    card grids.

    Navigation moves the cursor by a page (visible rows × columns) and
    top-aligns the scroll so the selection follows the visible page, mirroring
    PagedListView. Relies on the host grid providing ``_visible_cards()``,
    ``_move_cursor()``, ``_cols`` and ``_cursor`` — AlbumGrid, ArtistGrid and
    PlaylistGrid all do.

    Art loading: ``sync_visible_art()`` fetches art only for cards within the
    viewport plus one page of margin above/below, instead of every mounted
    card fetching eagerly on its own ``on_mount``. Cards must implement
    ``maybe_fetch_art()`` (idempotent — safe to call repeatedly). Call
    ``sync_visible_art()`` after mounting/filtering cards; it also self-wires
    to scroll and resize so it keeps up as the user scrolls or the layout
    changes. Once fetched, a card's art is never released — consistent with
    the hide-don't-remove filtering elsewhere, so Kitty images are never
    re-transmitted."""

    _art_sync_timer: Timer | None = None

    def sync_visible_art(self) -> None:
        """Fetch art for cards in the viewport + margin. Deferred until after
        layout so freshly mounted/filtered cards have real regions to measure."""
        self.call_after_refresh(self._sync_visible_art_now)  # type: ignore[attr-defined]

    def _schedule_sync_art(self) -> None:
        """Coalesce a burst of scroll/resize events into one sync pass."""
        if self._art_sync_timer is not None:
            self._art_sync_timer.stop()
        self._art_sync_timer = self.set_timer(0.1, self.sync_visible_art)  # type: ignore[attr-defined]

    def _sync_visible_art_now(self) -> None:
        cards = self._visible_cards()  # type: ignore[attr-defined]
        if not cards:
            return
        lo, hi = self._visible_art_range(cards)
        for card in cards[lo:hi]:
            card.maybe_fetch_art()  # type: ignore[attr-defined]

    def _visible_art_range(self, cards: list) -> tuple[int, int]:
        """Row-based index range covering the viewport plus one page of
        margin above and below (same row-pitch measurement as _page_rows)."""
        pitch = cards[0].region.height
        if len(cards) > self._cols:  # type: ignore[attr-defined]
            step = cards[self._cols].region.y - cards[0].region.y  # type: ignore[attr-defined]
            if step > 0:
                pitch = step
        if pitch <= 0:
            pitch = 1
        page_rows = max(1, self.scrollable_content_region.height // pitch)  # type: ignore[attr-defined]
        first_row = max(0, int(self.scroll_y // pitch) - page_rows)  # type: ignore[attr-defined]
        row_span = page_rows * 3  # margin above + viewport + margin below
        lo = first_row * self._cols  # type: ignore[attr-defined]
        hi = lo + row_span * self._cols  # type: ignore[attr-defined]
        return lo, hi

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)  # type: ignore[misc]
        self._schedule_sync_art()

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

    def action_move(self, direction: str) -> None:
        cards = self._visible_cards()  # type: ignore[attr-defined]
        if not cards:
            return
        if self._cursor == -1:  # type: ignore[attr-defined]
            self._move_cursor(0, cards)  # type: ignore[attr-defined]
            return
        idx = self._cursor  # type: ignore[attr-defined]
        target: int | None = None
        if direction == "right" and idx + 1 < len(cards):
            target = idx + 1
        elif direction == "left" and idx > 0:
            target = idx - 1
        elif direction == "down" and idx + self._cols < len(cards):  # type: ignore[attr-defined]
            target = idx + self._cols  # type: ignore[attr-defined]
        elif direction == "up" and idx - self._cols >= 0:  # type: ignore[attr-defined]
            target = idx - self._cols  # type: ignore[attr-defined]
        if target is not None:
            self._move_cursor(target, cards)  # type: ignore[attr-defined]

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
        self._schedule_sync_art()

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
        self.sync_visible_art()
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
        align: left middle;
    }
    ArtistCard .card-name {
        height: auto;
        max-height: 5;
        width: 1fr;
        text-style: bold;
        overflow: hidden hidden;
    }
    ArtistCard.-selected {
        outline: round $accent;
        background: $accent 35%;
    }
    ArtistCard.-selected .card-name { color: $accent; text-style: bold; }
    """

    def __init__(self, artist: Artist) -> None:
        super().__init__()
        self._artist = artist
        self._art_requested = False

    @property
    def artist(self) -> Artist:
        return self._artist

    def compose(self) -> ComposeResult:
        yield TGPImage()
        with Vertical(classes="card-info"):
            yield Label(escape(self._artist.name), classes="card-name", markup=True)

    def on_mount(self) -> None:
        _set_art_width(self.query_one(TGPImage), 5)
        # See AlbumCard.on_mount — art loads lazily via the grid's
        # sync_visible_art(), not eagerly here.

    def maybe_fetch_art(self) -> None:
        if self._art_requested or not self._artist.image_url:
            return
        self._art_requested = True
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
        self._schedule_sync_art()

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
        self.sync_visible_art()
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
        _set_art_width(self.query_one(TGPImage), 7)

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


class ArtistScreen(NowPlayingViewMixin, Screen):
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
                yield TrackListView(id="top-tracks")
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
        self._wire_now_playing(ArtistTrackRow, "#top-tracks")
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
        try:
            artist = Artist.from_api(await app._client.get_artist_detail(self._artist_id))
        except Exception:
            return
        if not self.is_mounted:
            return
        try:
            self.query_one(ArtistHeader).populate(artist)
            if artist.albums:
                grid = self.query_one("#albums", AlbumGrid)
                await grid.mount(*[AlbumCard(album) for album in artist.albums])
                grid.sync_visible_art()
        except NoMatches:
            pass

    @work
    async def _load_tracks(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            items = await app._client.get_artist_top_tracks(self._artist_id)
        except Exception:
            return
        if not items or not self.is_mounted:
            return
        fav_ids = await app.ensure_favorite_ids()
        if not self.is_mounted:
            return
        try:
            lv = self.query_one("#top-tracks", ListView)
            rows = [
                ArtistTrackRow(t := Track.from_api(raw), i, str(t.id) in fav_ids)
                for i, raw in enumerate(items, 1)
            ]
            self._apply_now_playing(rows)
            await lv.mount(*rows)
        except NoMatches:
            pass

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
            self.call_after_refresh(lv.focus)

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_album_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track, queue=event.queue)

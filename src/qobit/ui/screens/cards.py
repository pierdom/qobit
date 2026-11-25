"""The cards and grids of albums and artists: the Albums and Artists tabs, the
artist page's releases, and the Similar artists. Navigation is `widgets/grid.py`."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label
from textual_image.widget import TGPImage

from ...qobuz.client import QobuzError
from ...qobuz.models import Album, Artist
from .._images import fetch_image
from .._now_playing import (
    heart_suffix,
    plain_if_highlighted,
)
from .._utils import markup_escape, qapp
from ..widgets.art import Art, square_width
from ..widgets.grid import GRID_CARD_IMG_H, GridNav

if TYPE_CHECKING:
    pass

_CARD_IMG_H = 4  # album tiles inside an artist page (title + year only)
_TILE_MIN_W = 22


def is_hires_album(album: Album) -> bool:
    """Streamable above CD quality (more than 16 bits)."""
    return album.hires_streamable or (album.maximum_bit_depth or 0) > 16


class _YearLine(Label):
    """An album card's year line. Whether HI-RES fits beside the year
    depends on the label's width, known for sure only once it's laid out:
    rebuild on its own resize (the card's came first and saw width 0).
    Only when the width changed: Textual sends a Resize to every widget of
    a tab each time it's shown, and each rebuild cost a layout pass."""

    _width = 0

    def on_resize(self, event: events.Resize) -> None:
        if event.size.width in (0, self._width):
            return
        self._width = event.size.width
        card = next((a for a in self.ancestors if isinstance(a, AlbumCard)), None)
        if card is not None:
            card.refresh_selected(layout=True)


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
        height: 5;  /* GRID_CARD_IMG_H */
    }
    AlbumCard TGPImage {
        width: 8;
        height: 4;
        margin-right: 1;
    }
    AlbumCard.full TGPImage {
        height: 5;  /* GRID_CARD_IMG_H */
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
        color: $qobit-muted;
        overflow: hidden hidden;
    }
    AlbumCard.full .card-artist {
        display: block;
    }
    AlbumCard .card-year {
        height: auto;  /* 2 rows when HI-RES moves below the year (_year_line) */
        max-height: 2;
        width: 1fr;
        color: $qobit-muted;
        overflow: hidden hidden;
    }
    /* Selected state: shared by every grid card (.grid-card) and defined
       once in app_css.py — see the "Grid card selection" block there. */
    """

    def __init__(self, album: Album, show_artist: bool = False) -> None:
        super().__init__(classes="grid-card full" if show_artist else "grid-card")
        self._album = album
        self._show_artist = show_artist
        self._art_requested = False

    @property
    def album(self) -> Album:
        return self._album

    def compose(self) -> ComposeResult:
        yield Art(self._album.title, rows=GRID_CARD_IMG_H if self._show_artist else _CARD_IMG_H)
        with Vertical(classes="card-info"):
            yield Label(
                markup_escape(self._album.title), classes="card-title card-primary", markup=True
            )
            yield Label(
                markup_escape(self._album.artist or ""),
                classes="card-artist card-secondary",
                markup=True,
            )
            yield _YearLine(self._year_line(), classes="card-year card-secondary")

    def _year_line(self) -> Content:
        """``1997  ·  HI-RES``, or HI-RES on the row below when the card is
        too narrow for both (an artist page's small tiles: it wrapped after
        the ``·`` and the one-row line cut HI-RES off). Colour comes from the
        CSS (muted; the selection's text colour when selected), so no `dim`
        here: on a selected card it halved the contrast."""
        year = str(self._album.year) if self._album.year else "—"
        if not is_hires_album(self._album):
            return plain_if_highlighted(self, Content(year))
        hires = ("HI-RES", "bold $qobit-hires")
        sep = "  ·  "
        try:
            width = self.query_one(_YearLine).size.width
        except NoMatches:
            width = 0
        if width and len(year) + len(sep) + len(hires[0]) > width:
            sep = "\n"
        return plain_if_highlighted(self, Content.assemble(year, sep, hires))

    @property
    def highlighted(self) -> bool:
        """Selected in its grid (for plain_if_highlighted)."""
        return self.has_class("-selected")

    def refresh_selected(self, layout: bool = False) -> None:
        """Redraw the year line in the card's colours. Selecting changes no
        text, so no layout pass (it reflows the whole screen); a resize can
        move HI-RES to a second row and asks for one."""
        with contextlib.suppress(NoMatches):
            self.query_one(".card-year", Label).update(self._year_line(), layout=layout)

    def on_mount(self) -> None:
        img_h = GRID_CARD_IMG_H if self._show_artist else _CARD_IMG_H
        square_width(self.query_one(TGPImage), img_h)
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
        if img is not None and self.is_attached:
            with contextlib.suppress(NoMatches):
                await self.query_one(Art).show(img)

    def on_click(self) -> None:
        self.post_message(AlbumCard.Selected(self._album))


class AlbumGrid(GridNav, ScrollableContainer):
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

    def __init__(self, *args: Any, tile_min_width: int = _TILE_MIN_W, **kwargs: Any) -> None:
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
        # The cards are the grid's children: a query walked every widget
        # inside every card, twice per cursor move.
        return [c for c in self.children if isinstance(c, AlbumCard) and c.display]

    def filter_cards(self, visible_ids: set[str] | None) -> bool:
        """Hide/show cards by album id without remounting (so the
        Kitty images are never re-transmitted).  ``None`` shows everything.
        Returns whether any card is visible."""
        any_visible = False
        # Cards not mounted yet may match: mount them all while filtering (a
        # card listed twice below is harmless).
        cards = [*(c for c in self.children if isinstance(c, AlbumCard)), *(self._backlog or [])]
        if visible_ids is not None:
            self._mount_more(everything=True)
        for card in cards:
            vis = visible_ids is None or card._album.id in visible_ids
            if bool(card.display) != vis:
                card.display = vis
            any_visible = any_visible or vis
        self.scroll_home(animate=False)
        self.select_first()
        self.sync_visible_art()
        return any_visible

    def action_open_selected(self) -> None:
        if isinstance(card := self.selected_card(), AlbumCard):
            self.post_message(AlbumCard.Selected(card.album))


class ArtistCard(Widget):
    class Selected(Message):
        def __init__(self, artist: Artist) -> None:
            super().__init__()
            self.artist = artist

    DEFAULT_CSS = """
    ArtistCard {
        layout: horizontal;
        height: 5;  /* GRID_CARD_IMG_H */
        padding: 0;
    }
    ArtistCard TGPImage {
        width: 8;
        height: 5;  /* GRID_CARD_IMG_H */
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
    /* Selected state: shared .grid-card rules in app_css.py. */
    """

    def __init__(self, artist: Artist, followed: bool = False) -> None:
        """``followed``: mark with ♥ (used where not every artist is followed,
        like Similar artists)."""
        super().__init__(classes="grid-card")
        self._artist = artist
        self._followed = followed
        self._art_requested = False

    def set_followed(self, followed: bool) -> None:
        if followed == self._followed:
            return  # called for every tile on screen: only the changed redraw
        self._followed = followed
        with contextlib.suppress(NoMatches):
            self.query_one(".card-name", Label).update(self._label())

    def _label(self) -> Content:
        content = Content.assemble(self._artist.name, heart_suffix(self._followed))
        return plain_if_highlighted(self, content)

    @property
    def highlighted(self) -> bool:
        """Selected in its grid (for plain_if_highlighted)."""
        return self.has_class("-selected")

    def refresh_selected(self) -> None:
        with contextlib.suppress(NoMatches):
            # Same text, new colours: no layout pass (see AlbumCard's).
            self.query_one(".card-name", Label).update(self._label(), layout=False)

    @property
    def artist(self) -> Artist:
        return self._artist

    def compose(self) -> ComposeResult:
        yield Art(self._artist.name, rows=GRID_CARD_IMG_H)
        with Vertical(classes="card-info"):
            yield Label(self._label(), classes="card-name card-primary")

    def on_mount(self) -> None:
        square_width(self.query_one(TGPImage), GRID_CARD_IMG_H)
        # See AlbumCard.on_mount — art loads lazily via the grid's
        # sync_visible_art(), not eagerly here.

    def maybe_fetch_art(self) -> None:
        if self._art_requested:
            return
        self._art_requested = True
        self._fetch_art(self._artist.image_url)

    @work
    async def _fetch_art(self, url: str | None) -> None:
        if url is None:
            # No photo in the favourites payload: the full artist record (cached,
            # reused by the artist page) falls back to the newest release cover.
            app = qapp(self)
            try:
                full = Artist.from_api(await app._client.get_artist_detail(self._artist.id))
            except QobuzError:
                return
            url = self._artist.image_url = full.image_url
            if url is None:
                return
        img = await fetch_image(url)
        if img is not None and self.is_attached:
            with contextlib.suppress(NoMatches):
                await self.query_one(Art).show(img)

    def on_click(self) -> None:
        self.post_message(ArtistCard.Selected(self._artist))


class ArtistGrid(GridNav, ScrollableContainer):
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

    def __init__(self, *args: Any, tile_min_width: int = _TILE_MIN_W, **kwargs: Any) -> None:
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
        # The cards are the grid's children: a query walked every widget
        # inside every card, twice per cursor move.
        return [c for c in self.children if isinstance(c, ArtistCard) and c.display]

    def filter_cards(self, visible_ids: set[str] | None) -> bool:
        """Hide/show mounted cards by artist id without remounting (so the
        Kitty images are never re-transmitted).  ``None`` shows everything.
        Returns whether any card is visible."""
        any_visible = False
        # Cards not mounted yet may match: mount them all while filtering (a
        # card listed twice below is harmless).
        cards = [*(c for c in self.children if isinstance(c, ArtistCard)), *(self._backlog or [])]
        if visible_ids is not None:
            self._mount_more(everything=True)
        for card in cards:
            vis = visible_ids is None or card._artist.id in visible_ids
            if bool(card.display) != vis:
                card.display = vis
            any_visible = any_visible or vis
        self.scroll_home(animate=False)
        self.select_first()
        self.sync_visible_art()
        return any_visible

    def action_open_selected(self) -> None:
        if isinstance(card := self.selected_card(), ArtistCard):
            self.post_message(ArtistCard.Selected(card.artist))

from __future__ import annotations

import contextlib
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
from textual.widget import AwaitMount, Widget
from textual.widgets import ContentSwitcher, Footer, Label, ListItem, ListView
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from ...qobuz.client import QobuzError
from ...qobuz.models import Album, Artist, Track
from .._images import fetch_image
from .._now_playing import (
    GUTTER_ICON,
    GUTTER_NUM,
    NowPlayingRowMixin,
    heart_suffix,
    meta_indent,
    playing_content,
)
from .._utils import readout
from ..nav import page_header
from ..widgets.art import Art
from ..widgets.lists import TrackListView
from ..widgets.transport import TransportBar
from .album_detail import AlbumDetailPanel
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp

_CARD_IMG_H = 4  # album tiles inside an artist page (title + year only)
# Image height, in rows, shared by the library grids — Albums, Artists and
# Playlists tabs — so all three tile sizes match. Every card's text column is
# laid out to fit in this many rows.
GRID_CARD_IMG_H = 5
# Minimum tile width for the same three library grids, so they show the same
# number of columns at a given terminal width. 32 leaves ~22 cells of text
# beside the art: 3 columns at 120 terminal columns, 5 at 180 (at 40, tiles
# were mostly empty space: 2 and 4).
LIBRARY_TILE_MIN_W = 32
_TILE_MIN_W = 22


def is_hires_album(album: Album) -> bool:
    """Streamable above CD quality (more than 16 bits)."""
    return album.hires_streamable or (album.maximum_bit_depth or 0) > 16


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
    ArtistTrackRow .row-meta { color: $text-muted; }
    """

    def __init__(self, track: Track, number: int | None, favorite: bool = False) -> None:
        """``number`` None: an unnumbered row (♪), as in the favourites list."""
        super().__init__()
        self.track = track
        self._number = number
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = heart_suffix(self._favorite)
        lead = f"{self._number}." if self._number else ICON_TRACK  # numbered lists: no ♪
        return playing_content(
            lead, t.display_title, self._is_playing, self._is_paused, heart, width=self._width
        )

    @property
    def _width(self) -> int:
        return GUTTER_NUM if self._number else GUTTER_ICON

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="row-title")
        yield Label(
            f"{meta_indent(self._width)}{t.album}  ·  {t.duration_str}",
            classes="row-meta",
            markup=False,
        )

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self._refresh_primary()


# Both lists side by side from this width; below it, one panel and a switch.
ARTIST_TRACKS_COLUMNS_MIN_W = 120
# The header lists a few similar artists beside the bio from this terminal width.
ARTIST_HEADER_SIMILAR_MIN_W = 128

# Panels that hold two views (Top Tracks / Your favourites, Albums / Similar)
# name both in the border title: the shown one underlined, like the active tab
# in the top bar; `t` or a click on a name switches. (Not `dim` for the other:
# on an unfocused panel's 40% title it became unreadable.)
_SWITCH_SEP = "  │  "
SWITCH_HINT = "t or click the title to switch"


def switch_title(labels: list[str], active: int) -> str:
    return _SWITCH_SEP.join(
        f"[underline]{label}[/underline]" if i == active else f"[not bold]{label}[/not bold]"
        for i, label in enumerate(labels)
    )


def clicked_label(panel: Widget, event: events.Click, labels: list[str]) -> int | None:
    """Which name in ``panel``'s switch title was clicked, if any. The title
    starts 3 cells in: corner, one rule, one space."""
    if event.screen_y != panel.region.y:
        return None
    x = event.screen_x - (panel.region.x + 3)
    start = 0
    for i, label in enumerate(labels):
        if start <= x < start + len(label):
            return i
        start += len(label) + len(_SWITCH_SEP)
    return None


class _TitleSwitch:
    """A panel with two views named in its border title: a click on the
    hidden view's name runs ``action_switch``. Hosts say which panel shows
    the title, its labels and the active index (None: not switching)."""

    def _switch_state(self) -> tuple[Widget, list[str], int] | None:
        raise NotImplementedError

    def action_switch(self) -> None:
        raise NotImplementedError

    def on_click(self, event: events.Click) -> None:
        state = self._switch_state()
        if state is None:
            return
        panel, labels, active = state
        picked = clicked_label(panel, event, labels)
        if picked is not None and picked != active:
            event.stop()
            self.action_switch()


class ArtistTracks(_TitleSwitch, Widget):
    """An artist's Top Tracks and the user's favourite tracks by that artist.

    Wide: two columns. Narrow: one panel showing either list; `t`, or ←/→,
    switches. No favourites by this artist: Top Tracks alone. Shared by the
    artist page and the Artists tab. Call ``load(artist_id)``."""

    BINDINGS = [
        Binding("t", "switch", "Top / Favs", show=False),
        Binding("left", "switch", show=False),
        Binding("right", "switch", show=False),
    ]

    DEFAULT_CSS = """
    ArtistTracks { layout: horizontal; height: 1fr; }
    ArtistTracks TrackListView {
        width: 1fr;
        height: 1fr;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }
    ArtistTracks TrackListView:focus {
        border: round $accent;
        border-title-color: $accent;
    }
    ArtistTracks.-columns #artist-favs { margin-left: 1; }
    """

    _artist_id: str | None = None
    _showing: str = "top"  # in switch mode: "top" | "favs"
    _fav_ids: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        yield TrackListView(id="top-tracks")
        yield TrackListView(id="artist-favs")

    def on_mount(self) -> None:
        app = self.app
        self.watch(app, "now_playing", lambda _: self._mark_playing(), init=False)
        self.watch(app, "is_paused", lambda _: self._mark_playing(), init=False)
        # toggle_favorite bumps queue_version: refresh the favourites column
        # and the hearts when a track is (un)favourited anywhere.
        self.watch(app, "queue_version", lambda _: self._sync_favourites(), init=False)
        self._layout()

    def load(self, artist_id: str) -> None:
        self._artist_id = artist_id
        self._showing = "top"
        self._fav_ids = ()
        self._load()

    @work(exclusive=True, group="_load")  # a newer artist cancels a stale load
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        top = self.query_one("#top-tracks", TrackListView)
        await top.clear()
        await self.query_one("#artist-favs", TrackListView).clear()
        self._layout()
        artist_id = self._artist_id
        try:
            items = await app._client.get_artist_top_tracks(artist_id or "")
        except QobuzError:
            items = []
        fav_ids = await app.ensure_favorite_ids()
        if not self.is_attached or artist_id != self._artist_id:
            return
        rows = [
            ArtistTrackRow(t := Track.from_api(raw), i, str(t.id) in fav_ids)
            for i, raw in enumerate(items, 1)
        ]
        await top.mount(*rows)
        await self._refresh_favs()

    def _favourites(self) -> list[Track]:
        app: QobitApp = self.app  # type: ignore[assignment]
        if self._artist_id is None:
            return []
        tracks = [t for t in (app._fav_tracks or []) if t.by_artist(self._artist_id)]
        return sorted(tracks, key=lambda t: t.favorited_at or 0, reverse=True)

    async def _refresh_favs(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        with contextlib.suppress(Exception):
            await app.ensure_favorite_tracks()
        tracks = self._favourites()
        self._fav_ids = tuple(t.id for t in tracks)
        favs = self.query_one("#artist-favs", TrackListView)
        await favs.clear()
        await favs.mount(*[ArtistTrackRow(t, None, favorite=True) for t in tracks])
        self._mark_playing()
        self._layout()

    def _sync_favourites(self) -> None:
        """After a (un)favourite anywhere: fix the hearts, and rebuild the
        favourites list only if its tracks changed."""
        if self._artist_id is None or not self.is_attached:
            return
        app: QobitApp = self.app  # type: ignore[assignment]
        ids = app._fav_ids or set()
        for row in self.query_one("#top-tracks", TrackListView).query(ArtistTrackRow):
            row.set_favorite(str(row.track.id) in ids)
        if tuple(t.id for t in self._favourites()) != self._fav_ids:
            self.run_worker(self._refresh_favs(), group="_refresh_favs", exclusive=True)

    def _mark_playing(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        now_id = getattr(app.now_playing, "id", None)
        for row in self.query(ArtistTrackRow):
            row.set_playing(row.track.id == now_id, paused=app.is_paused)

    # ── layout ───────────────────────────────────────────────────────────────

    def on_resize(self) -> None:
        self._layout()

    @property
    def _columns(self) -> bool:
        return bool(self._fav_ids) and self.size.width >= ARTIST_TRACKS_COLUMNS_MIN_W

    def _layout(self) -> None:
        top = self.query_one("#top-tracks", TrackListView)
        favs = self.query_one("#artist-favs", TrackListView)
        n = len(self._fav_ids)
        columns = self._columns
        self.set_class(columns, "-columns")
        top.display = columns or not n or self._showing == "top"
        favs.display = bool(n) and (columns or self._showing == "favs")
        if columns or not n:
            top.border_title, favs.border_title = self._labels
        else:  # one panel: name both lists, the hidden one dimmed
            title = switch_title(self._labels, 0 if self._showing == "top" else 1)
            (top if self._showing == "top" else favs).border_title = title
        top.border_subtitle = favs.border_subtitle = "" if columns or not n else SWITCH_HINT

    @property
    def _labels(self) -> list[str]:
        return [readout("Top Tracks"), readout(f"Your favourites · {len(self._fav_ids)}")]

    def _switch_state(self) -> tuple[Widget, list[str], int] | None:
        if not self._fav_ids or self._columns:
            return None
        top = self._showing == "top"
        panel = self.query_one("#top-tracks" if top else "#artist-favs")
        return panel, self._labels, 0 if top else 1

    def action_switch(self) -> None:
        if not self._fav_ids:
            return
        if self._columns:  # both visible: move focus across
            target = (
                "#artist-favs"
                if self.screen.focused is self.query_one("#top-tracks")
                else "#top-tracks"
            )
            self.query_one(target, TrackListView).focus()
            return
        self._showing = "favs" if self._showing == "top" else "top"
        self._layout()
        self.query_one("#top-tracks" if self._showing == "top" else "#artist-favs").focus()

    # ── play ─────────────────────────────────────────────────────────────────

    @on(ListView.Selected)
    def _on_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, ArtistTrackRow):
            return
        event.stop()
        rows = list(event.list_view.query(ArtistTrackRow))
        idx = rows.index(event.item)
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.item.track, queue=[r.track for r in rows[idx + 1 :]])
        self.call_after_refresh(event.list_view.focus)


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
    /* Selected state: shared by every grid card (.grid-card) and defined
       once in QobitApp.CSS — see the "Grid card selection" block there. */
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
        yield Art(self._album.title)
        with Vertical(classes="card-info"):
            yield Label(escape(self._album.title), classes="card-title card-primary", markup=True)
            yield Label(
                escape(self._album.artist or ""), classes="card-artist card-secondary", markup=True
            )
            year = str(self._album.year) if self._album.year else "—"
            hires = (
                ("  ·  ", ("HI-RES", "bold $qobit-hires")) if is_hires_album(self._album) else ()
            )
            yield Label(Content.assemble((year, "dim"), *hires), classes="card-year card-secondary")

    def on_mount(self) -> None:
        img_h = GRID_CARD_IMG_H if self._show_artist else _CARD_IMG_H
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
        if img is not None and self.is_attached:
            with contextlib.suppress(NoMatches):
                self.query_one(TGPImage).image = img

    def on_click(self) -> None:
        self.post_message(AlbumCard.Selected(self._album))


_GRID_PAGE = 60  # cards per lazy mount (see _GridNav.set_cards)


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
    _backlog: list[Widget] | None = None

    # ── lazy mounting ────────────────────────────────────────────────────────
    # Mounting cards gets slower the more a grid already holds (~0.6 s for 200,
    # ~6 s for 1000), so ``set_cards`` mounts a first page and keeps the rest,
    # mounted a page at a time as the cursor or scroll position nears the end.

    async def clear_cards(self) -> None:
        self._backlog = None
        await self.remove_children()  # type: ignore[attr-defined]

    async def set_cards(self, cards: list[Widget]) -> None:
        """Fill an empty grid: the first page now, the rest on demand."""
        self._backlog = cards[_GRID_PAGE:]
        await self.mount(*cards[:_GRID_PAGE])  # type: ignore[attr-defined]
        self.sync_visible_art()

    def _mount_more(self, everything: bool = False) -> AwaitMount | None:
        if not self._backlog:
            return None
        n = len(self._backlog) if everything else _GRID_PAGE
        batch, self._backlog = self._backlog[:n], self._backlog[n:]
        self.sync_visible_art()
        return self.mount(*batch)  # type: ignore[attr-defined]

    def _near_end(self) -> None:
        """Mount the next page once the cursor is within a few rows of the end."""
        if self._backlog and self._cursor >= len(self._visible_cards()) - self._cols * 4:  # type: ignore[attr-defined]
            self._mount_more()

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
        # Mouse-wheel scrolling moves the view, not the cursor.
        if self._backlog and new_value >= self.max_scroll_y - self.size.height:  # type: ignore[attr-defined]
            self._mount_more()

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
            self._near_end()

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
        self._near_end()

    def selected_card(self) -> Widget | None:
        """The card under the cursor, or None."""
        cards = self._visible_cards()  # type: ignore[attr-defined]
        return cards[self._cursor] if 0 <= self._cursor < len(cards) else None  # type: ignore[attr-defined]

    def action_jump_first(self) -> None:
        cards = self._visible_cards()  # type: ignore[attr-defined]
        if cards:
            self._move_cursor(0, cards, top=True)  # type: ignore[attr-defined]

    async def action_jump_last(self) -> None:
        if (pending := self._mount_more(everything=True)) is not None:
            await pending
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
        """Hide/show cards by album id without remounting (so the
        Kitty images are never re-transmitted).  ``None`` shows everything.
        Returns whether any card is visible."""
        any_visible = False
        # Cards not mounted yet may match: mount them all while filtering (a
        # card listed twice below is harmless).
        cards = [*self.query(AlbumCard), *(self._backlog or [])]
        if visible_ids is not None:
            self._mount_more(everything=True)
        for card in cards:
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
    /* Selected state: shared .grid-card rules in QobitApp.CSS. */
    """

    def __init__(self, artist: Artist, followed: bool = False) -> None:
        """``followed``: mark with ♥ (used where not every artist is followed,
        like Similar artists)."""
        super().__init__(classes="grid-card")
        self._artist = artist
        self._followed = followed
        self._art_requested = False

    def set_followed(self, followed: bool) -> None:
        self._followed = followed
        with contextlib.suppress(NoMatches):
            self.query_one(".card-name", Label).update(self._label())

    def _label(self) -> Content:
        return Content.assemble(self._artist.name, heart_suffix(self._followed))

    @property
    def artist(self) -> Artist:
        return self._artist

    def compose(self) -> ComposeResult:
        yield Art(self._artist.name)
        with Vertical(classes="card-info"):
            yield Label(self._label(), classes="card-name card-primary")

    def on_mount(self) -> None:
        _set_art_width(self.query_one(TGPImage), GRID_CARD_IMG_H)
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
            app: QobitApp = self.app  # type: ignore[assignment]
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
                self.query_one(TGPImage).image = img

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
        # Cards not mounted yet may match: mount them all while filtering (a
        # card listed twice below is harmless).
        cards = [*self.query(ArtistCard), *(self._backlog or [])]
        if visible_ids is not None:
            self._mount_more(everything=True)
        for card in cards:
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
        if isinstance(card := self.selected_card(), ArtistCard):
            self.post_message(ArtistCard.Selected(card.artist))


class ArtistReleases(_TitleSwitch, Widget):
    """The artist's Albums & EPs, and their Similar artists in the same panel:
    `t` switches (the arrows move the grid cursor). Shown only when Qobuz has
    similar artists. The full list loads the first time it's opened. The
    album grid keeps the id the host gives it; the host fills it."""

    BINDINGS = [Binding("t", "switch", "Albums / Similar", show=False)]

    DEFAULT_CSS = """
    ArtistReleases { height: 1fr; }
    ArtistReleases #similar-artists {
        display: none;
        height: 1fr;
        grid-rows: 5;  /* GRID_CARD_IMG_H */
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
        border-subtitle-color: $accent 40%;
    }
    ArtistReleases #similar-artists:focus {
        border: round $accent;
        border-title-color: $accent;
    }
    """

    def __init__(self, albums_id: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._albums_id = albums_id
        self._artist_id: str | None = None
        self._showing = "albums"
        self._has_similar = False
        self._similar_loaded = False
        self._similar_count: int | None = None

    def compose(self) -> ComposeResult:
        yield AlbumGrid(id=self._albums_id)
        yield ArtistGrid(id="similar-artists")

    @property
    def albums(self) -> AlbumGrid:
        return self.query_one(f"#{self._albums_id}", AlbumGrid)

    def on_mount(self) -> None:
        self._layout()

    async def set_artist(self, artist_id: str) -> None:
        self._artist_id = artist_id
        self._showing = "albums"
        self._has_similar = self._similar_loaded = False
        self._similar_count = None
        await self.query_one("#similar-artists", ArtistGrid).clear_cards()
        self._layout()
        self._check_similar(artist_id)

    @work(exclusive=True, group="_check_similar")  # a newer artist cancels a stale one
    async def _check_similar(self, artist_id: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            items, _ = await app._client.get_similar_artists_preview(artist_id)
        except QobuzError:
            return
        if self.is_attached and artist_id == self._artist_id:
            self._has_similar = bool(items)
            self._layout()

    def _layout(self) -> None:
        albums = self.albums
        similar = self.query_one("#similar-artists", ArtistGrid)
        on_similar = self._has_similar and self._showing == "similar"
        albums.display = not on_similar
        similar.display = on_similar
        if not self._has_similar:
            albums.border_title, albums.border_subtitle = readout("Albums & EPs"), ""
        elif on_similar:
            similar.border_title = switch_title(self._labels, 1)
            similar.border_subtitle = SWITCH_HINT
        else:
            albums.border_title = switch_title(self._labels, 0)
            albums.border_subtitle = SWITCH_HINT

    @property
    def _labels(self) -> list[str]:
        count = f" · {self._similar_count}" if self._similar_count is not None else ""
        return [readout("Albums & EPs"), readout(f"Similar artists{count}")]

    def _switch_state(self) -> tuple[Widget, list[str], int] | None:
        if not self._has_similar:
            return None
        on_similar = self._showing == "similar"
        panel = self.query_one("#similar-artists") if on_similar else self.albums
        return panel, self._labels, int(on_similar)

    def action_switch(self) -> None:
        if not self._has_similar:
            return
        self._showing = "similar" if self._showing == "albums" else "albums"
        self._layout()
        if self._showing == "similar":
            self.query_one("#similar-artists", ArtistGrid).focus()
            if not self._similar_loaded:
                self._similar_loaded = True
                self._load_similar(self._artist_id or "")
        else:
            self.albums.focus()

    @work(exclusive=True, group="_load_similar")
    async def _load_similar(self, artist_id: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        grid = self.query_one("#similar-artists", ArtistGrid)
        grid.border_subtitle = "loading…"
        try:
            items = await app._client.get_similar_artists(artist_id)
        except QobuzError:
            self._similar_loaded = False  # let `t` retry
            grid.border_subtitle = "couldn't load"
            return
        if not self.is_attached or artist_id != self._artist_id:
            return
        followed = {a.id for a in (app._fav_artists or [])}
        artists = [Artist.from_api(raw) for raw in items]
        self._similar_count = len(artists)
        await grid.set_cards([ArtistCard(a, followed=a.id in followed) for a in artists])
        self._layout()

    @on(ArtistCard.Selected)
    def _on_similar_selected(self, event: ArtistCard.Selected) -> None:
        event.stop()
        app: QobitApp = self.app  # type: ignore[assignment]
        app.open_artist(event.artist.id)


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
    ArtistHeader .ah-similar {
        display: none;
        width: 32;
        height: 7;
        margin-left: 1;
        background: transparent;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
        border-subtitle-color: $accent 40%;
    }
    ArtistHeader .ah-similar:focus {
        border: round $accent;
        border-title-color: $accent;
    }
    ArtistHeader.-similar .ah-similar { display: block; }
    ArtistHeader .ah-similar ListItem { height: 1; padding: 0 1; }
    """

    def __init__(self, *, similar: bool = False, **kwargs: object) -> None:
        """``similar``: on wide terminals, list a few similar artists beside
        the bio (artist pages; not the album pages that reuse this header)."""
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._similar = similar
        self._similar_items: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Art(classes="ah-image")
        with VerticalScroll(classes="ah-bio-section"):
            yield Label("", classes="ah-bio")
        yield ListView(classes="ah-similar")

    def on_mount(self) -> None:
        _set_art_width(self.query_one(TGPImage), 7)

    artist: Artist | None = None
    """The artist shown, once ``populate`` has run."""

    def set_loading(self, name: str = "Loading…") -> None:
        self.artist = None
        self._similar_items = []
        self._layout_similar()
        self.query_one(".ah-bio-section").border_title = escape(readout(name))
        art = self.query_one(Art)
        art.image = None  # not the previous artist's photo under this name
        art.placeholder = name
        self.query_one(".ah-bio", Label).update("")

    def populate(self, artist: Artist) -> None:
        self.artist = artist
        self.query_one(".ah-bio-section").border_title = escape(readout(artist.name))
        self.query_one(Art).placeholder = artist.name
        if artist.biography:
            self.query_one(".ah-bio", Label).update(escape(artist.biography))
        if artist.image_url:
            self._fetch_image(artist.image_url)
        if self._similar:
            self._load_similar(artist.id)

    @work(exclusive=True, group="_load_similar")  # a newer artist cancels a stale one
    async def _load_similar(self, artist_id: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            items, more = await app._client.get_similar_artists_preview(artist_id)
        except QobuzError:
            return
        if not self.is_attached or self.artist is None or self.artist.id != artist_id:
            return
        self._similar_items = items
        lv = self.query_one(".ah-similar", ListView)
        await lv.clear()
        await lv.mount(*[ListItem(Label(it["name"], markup=False)) for it in items])
        lv.border_title = readout("Similar")
        lv.border_subtitle = "more below (t)" if more else ""
        self._layout_similar()

    def on_resize(self) -> None:
        self._layout_similar()

    def _layout_similar(self) -> None:
        wide = self.app.size.width >= ARTIST_HEADER_SIMILAR_MIN_W
        self.set_class(bool(self._similar_items) and wide, "-similar")

    @on(ListView.Selected, ".ah-similar")
    def _on_similar_selected(self, event: ListView.Selected) -> None:
        event.stop()
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._similar_items):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.open_artist(self._similar_items[idx]["id"])

    @work(exclusive=True, group="_fetch_image")  # a newer request cancels a stale one
    async def _fetch_image(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_attached:
            with contextlib.suppress(NoMatches):
                self.query_one(TGPImage).image = img


class ArtistScreen(Screen):
    """An artist page pushed over the current one (Search, `g`, `i`,
    Similar). Two levels: the artist, and an album opened from it."""

    DEFAULT_CSS = """
    ArtistScreen { layout: vertical; }

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

    def __init__(self, artist_id: str) -> None:
        super().__init__()
        self._artist_id = artist_id
        self._album_view_active = False

    def compose(self) -> ComposeResult:
        yield from page_header(self.app)  # type: ignore[arg-type]
        yield ArtistHeader(similar=True)
        with ContentSwitcher(initial="artist-view", id="main-content"):
            with Vertical(id="artist-view"):
                yield ArtistTracks()
                yield ArtistReleases("albums")
            with Vertical(id="album-view"):
                yield AlbumDetailPanel(id="album-panel")
        yield TransportBar()
        yield Footer()

    def on_mount(self) -> None:
        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        self.query_one(ArtistHeader).set_loading()
        self.run_worker(self.query_one(ArtistReleases).set_artist(self._artist_id))
        self._load_detail()
        self.query_one(ArtistTracks).load(self._artist_id)

    @property
    def page_artist(self) -> Artist | None:
        """The artist this page is about: what the ``i`` menu's follow acts on."""
        return self.query_one(ArtistHeader).artist

    def nav_levels(self) -> list[str]:
        artist = self.page_artist
        levels = [artist.name if artist else "Artist"]
        if self._album_view_active:
            album = self.query_one("#album-panel", AlbumDetailPanel).album
            levels.append(album.title if album else "Album")
        return levels

    def nav_to(self, depth: int) -> None:
        if depth == 0 and self._album_view_active:
            self._show_artist_view()

    def _open_album(self, album: Album) -> None:
        panel = self.query_one("#album-panel", AlbumDetailPanel)
        panel.load(album)
        self.query_one("#main-content", ContentSwitcher).current = "album-view"
        self._album_view_active = True
        panel.focus_tracklist()
        self.app.nav_changed()  # type: ignore[attr-defined]

    def _show_artist_view(self) -> None:
        self.query_one("#main-content", ContentSwitcher).current = "artist-view"
        self._album_view_active = False
        self.query_one("#albums", AlbumGrid).focus()
        self.app.nav_changed()  # type: ignore[attr-defined]

    @work
    async def _load_detail(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            artist = Artist.from_api(await app._client.get_artist_detail(self._artist_id))
        except Exception:
            return
        if not self.is_attached:
            return
        try:
            self.query_one(ArtistHeader).populate(artist)
            app.nav_changed()  # the trail can name the artist now
            if artist.albums:
                grid = self.query_one("#albums", AlbumGrid)
                await grid.set_cards([AlbumCard(album) for album in artist.albums])
        except NoMatches:
            pass

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_album_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track, queue=event.queue)

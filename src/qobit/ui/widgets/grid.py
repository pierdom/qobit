"""The shared navigation of the grids of cards (Albums, Artists, Playlists tabs and
the artist page): Page/Home/End keys, lazy mounting of the cards, art loaded only
for the cards in view. Pure Textual: the cards themselves live with their models."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from textual.timer import Timer
from textual.widget import AwaitMount, Widget

from .._utils import WidgetBase, qapp

if TYPE_CHECKING:
    pass

# Image height, in rows, shared by the library grids — Albums, Artists and
# Playlists tabs — so all three tile sizes match. Every card's text column is
# laid out to fit in this many rows.
GRID_CARD_IMG_H = 5
# Minimum tile width for the same three library grids, so they show the same
# number of columns at a given terminal width. 32 leaves ~22 cells of text
# beside the art: 3 columns at 120 terminal columns, 5 at 180 (at 40, tiles
# were mostly empty space: 2 and 4).
LIBRARY_TILE_MIN_W = 32


def select_card(card: Widget, selected: bool) -> None:
    """Mark a grid card (un)selected; cards with span colours (a heart, HI-RES)
    redraw them plain while selected, in the selection's text colour."""
    card.set_class(selected, "-selected")
    if hasattr(card, "refresh_selected"):
        card.refresh_selected()


_GRID_PAGE = 60  # cards per lazy mount (see GridNav.set_cards)


class GridNav(WidgetBase):
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
    _backlog: list[Any] | None = None
    # Provided by the grid that mixes this in (AlbumGrid, ArtistGrid, PlaylistGrid).
    _cols: int = 3
    _cursor: int = -1

    def _visible_cards(self) -> list[Any]:
        raise NotImplementedError

    def watch_has_focus(self, _has_focus: bool) -> None:
        """Restyle the grid alone on focus (its border): no card's look
        depends on it (``.grid-card.-selected`` in app_css.py). Textual
        restyled every widget of every card, on each tab switch."""
        qapp(self).stylesheet.update_nodes([self], animate=True)

    # ── lazy mounting ────────────────────────────────────────────────────────
    # Mounting cards gets slower the more a grid already holds (~0.6 s for 200,
    # ~6 s for 1000), so ``set_cards`` mounts a first page and keeps the rest,
    # mounted a page at a time as the cursor or scroll position nears the end.

    async def clear_cards(self) -> None:
        self._backlog = None
        await self.remove_children()

    async def set_cards(self, cards: list[Widget]) -> None:
        """Fill an empty grid: the first page now, the rest on demand."""
        self._backlog = cards[_GRID_PAGE:]
        await self.mount(*cards[:_GRID_PAGE])
        self.sync_visible_art()

    def _mount_more(self, everything: bool = False) -> AwaitMount | None:
        if not self._backlog:
            return None
        n = len(self._backlog) if everything else _GRID_PAGE
        batch, self._backlog = self._backlog[:n], self._backlog[n:]
        self.sync_visible_art()
        return self.mount(*batch)

    def _near_end(self) -> None:
        """Mount the next page once the cursor is within a few rows of the end."""
        if self._backlog and self._cursor >= len(self._visible_cards()) - self._cols * 4:
            self._mount_more()

    def sync_visible_art(self) -> None:
        """Fetch art for cards in the viewport + margin. Deferred until after
        layout so freshly mounted/filtered cards have real regions to measure."""
        self.call_after_refresh(self._sync_visible_art_now)

    def _schedule_sync_art(self) -> None:
        """Coalesce a burst of scroll/resize events into one sync pass."""
        if self._art_sync_timer is not None:
            self._art_sync_timer.stop()
        self._art_sync_timer = self.set_timer(0.1, self.sync_visible_art)

    def _sync_visible_art_now(self) -> None:
        cards = self._visible_cards()
        if not cards:
            return
        lo, hi = self._visible_art_range(cards)
        for card in cards[lo:hi]:
            card.maybe_fetch_art()

    def _visible_art_range(self, cards: list) -> tuple[int, int]:
        """Row-based index range covering the viewport plus one page of
        margin above and below (same row-pitch measurement as _page_rows)."""
        pitch = cards[0].region.height
        if len(cards) > self._cols:
            step = cards[self._cols].region.y - cards[0].region.y
            if step > 0:
                pitch = step
        if pitch <= 0:
            pitch = 1
        page_rows = max(1, self.scrollable_content_region.height // pitch)
        first_row = max(0, int(self.scroll_y // pitch) - page_rows)
        row_span = page_rows * 3  # margin above + viewport + margin below
        lo = first_row * self._cols
        hi = lo + row_span * self._cols
        return lo, hi

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self._schedule_sync_art()
        # Mouse-wheel scrolling moves the view, not the cursor.
        if self._backlog and new_value >= self.max_scroll_y - self.size.height:
            self._mount_more()

    def _page_rows(self) -> int:
        cards = self._visible_cards()
        if not cards:
            return 1
        # Row pitch = gap between a card and the one a full row below it
        # (height + gutter); fall back to a single card's height.
        pitch = cards[0].region.height
        if len(cards) > self._cols:
            step = cards[self._cols].region.y - cards[0].region.y
            if step > 0:
                pitch = step
        if pitch <= 0:
            pitch = 1
        return max(1, self.scrollable_content_region.height // pitch)

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
            self._near_end()

    def action_page(self, direction: str) -> None:
        cards = self._visible_cards()
        if not cards:
            return
        if self._cursor == -1:
            self._move_cursor(0, cards, top=True)
            return
        delta = self._page_rows() * self._cols
        if direction == "up":
            target = max(0, self._cursor - delta)
        else:
            target = min(len(cards) - 1, self._cursor + delta)
        self._move_cursor(target, cards, top=True)
        self._near_end()

    def _move_cursor(self, idx: int, cards: list | None = None, top: bool = False) -> None:
        if cards is None:
            cards = self._visible_cards()
        if not cards or idx < 0 or idx >= len(cards):
            return
        if 0 <= self._cursor < len(cards):
            select_card(cards[self._cursor], False)
        self._cursor = idx
        select_card(cards[idx], True)
        if top:
            cards[idx].scroll_visible(animate=False, top=True)
        else:
            cards[idx].scroll_visible()

    async def select_where(self, match: Callable[[Widget], bool]) -> bool:
        """Put the cursor on the first shown card ``match`` accepts, mounting
        the backlog up to it if needed (a grid redrawn after a background
        refresh keeps the user's place). False if there's none."""
        cards = self._visible_cards()
        idx = next((i for i, c in enumerate(cards) if match(c)), None)
        backlog = self._backlog or []
        at = next((i for i, c in enumerate(backlog) if match(c)), None)
        if idx is None and at is not None:
            # Through that card and a page more, not the whole backlog
            # (mounting gets slower the more a grid holds).
            batch, self._backlog = backlog[: at + _GRID_PAGE], backlog[at + _GRID_PAGE :]
            await self.mount(*batch)
            self.sync_visible_art()
            cards = self._visible_cards()
            idx = next((i for i, c in enumerate(cards) if match(c)), None)
        if idx is None:
            return False
        self._move_cursor(idx, cards)
        return True

    def select_first(self) -> None:
        """After a filter: the cursor on the first visible card, and no card
        (hidden ones included) left looking selected. It was -1 (nothing
        selected), so Enter and the ``i`` menu had no card to act on."""
        for card in self.query(".-selected"):
            select_card(card, False)
        self._cursor = -1
        self._move_cursor(0, top=True)

    def selected_card(self) -> Widget | None:
        """The card under the cursor, or None."""
        cards = self._visible_cards()
        return cards[self._cursor] if 0 <= self._cursor < len(cards) else None

    def action_jump_first(self) -> None:
        cards = self._visible_cards()
        if cards:
            self._move_cursor(0, cards, top=True)

    async def action_jump_last(self) -> None:
        if (pending := self._mount_more(everything=True)) is not None:
            await pending
        cards = self._visible_cards()
        if cards:
            self._move_cursor(len(cards) - 1, cards, top=True)

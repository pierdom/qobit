from __future__ import annotations

from collections.abc import Callable

from textual import work
from textual.binding import Binding
from textual.widget import AwaitMount, Widget
from textual.widgets import ListItem, ListView

# Long lists mount in pages: mounting gets slower the more rows a list already
# holds (~0.3 s per 200 rows, ~4.5 s for 2000), so ``set_rows`` mounts the
# first page and keeps the rest as a backlog, mounted a page at a time once the
# cursor (or the scroll position) is within ``_LOOKAHEAD`` rows of the end.
_PAGE_ROWS = 100
_LOOKAHEAD = 50


class PagedListView(ListView):
    """A ListView whose Page Up/Down, Home and End move the highlighted
    selection — and scroll to follow it — instead of scrolling the viewport
    past a stationary cursor.

    Plain ``ListView`` only binds up/down/enter, so page keys fall through to
    ``VerticalScroll`` and move the view while the selection stays put. Setting
    ``index`` here triggers ``watch_index``, which scrolls the new item into
    view, so the selection tracks the visible page.

    Lists filled with ``set_rows`` mount lazily (see ``_PAGE_ROWS``); ``rows``
    is every row in order, mounted or not, and End mounts them all.
    """

    BINDINGS = [
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("home", "cursor_first", "First", show=False),
        Binding("end", "cursor_last", "Last", show=False),
    ]

    _backlog: list[ListItem] | None = None

    def watch_has_focus(self, _has_focus: bool) -> None:
        """Restyle what focus changes: the list and its highlighted row.
        Textual restyles every row and label in the list (600 widgets for
        200 rows), twice per tab switch (the list left and the one
        entered): most of a tab switch's time. Only the highlighted row's
        look depends on the list's focus (QobitApp.CSS, ``ListView:focus >
        ListItem.-highlight``)."""
        nodes: list[Widget] = [self]
        if (row := self.highlighted_child) is not None:
            nodes.extend(row.walk_children(with_self=True))
        self.app.stylesheet.update_nodes(nodes, animate=True)

    async def set_rows(self, rows: list[ListItem]) -> None:
        """Replace the list's rows: mount the first page now, the rest as the
        cursor approaches them."""
        await self.clear()
        self._backlog = rows[_PAGE_ROWS:]
        if rows:
            await self.mount(*rows[:_PAGE_ROWS])

    async def select_where(self, match: Callable[[ListItem], bool]) -> bool:
        """Put the cursor on the first row ``match`` accepts, mounting the
        backlog up to it if needed (a list redrawn after a background
        refresh keeps the user's place). False if there's none."""
        rows = self.rows
        idx = next((i for i, r in enumerate(rows) if match(r)), None)
        if idx is None:
            return False
        await self.mount_through(idx)
        self.index = idx
        return True

    async def mount_through(self, idx: int) -> None:
        """Mount the backlog up to row ``idx`` (and a page more, not all of it)."""
        if idx >= len(self._nodes) and self._backlog:
            n = idx - len(self._nodes) + _PAGE_ROWS
            batch, self._backlog = self._backlog[:n], self._backlog[n:]
            await self.mount(*batch)

    @property
    def rows(self) -> list[ListItem]:
        """Every row in order, including those not mounted yet."""
        return [*self._nodes, *(self._backlog or [])]

    def remove_row(self, row: ListItem) -> None:
        if self._backlog and row in self._backlog:
            self._backlog.remove(row)
        else:
            row.remove()

    def _mount_more(self, everything: bool = False) -> AwaitMount | None:
        if not self._backlog:
            return None
        n = len(self._backlog) if everything else _PAGE_ROWS
        batch, self._backlog = self._backlog[:n], self._backlog[n:]
        return self.mount(*batch)

    def watch_index(self, old_index: int | None, new_index: int | None) -> None:
        super().watch_index(old_index, new_index)
        if new_index is not None and new_index >= len(self._nodes) - _LOOKAHEAD:
            self._mount_more()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        # Mouse-wheel scrolling moves the view, not the cursor.
        if self._backlog and new_value >= self.max_scroll_y - _LOOKAHEAD * 2:
            self._mount_more()

    def _page(self) -> int:
        """Number of rows that fit in the viewport (rows are uniform height)."""
        item_h = self._nodes[0].region.height if self._nodes else 0
        if item_h <= 0:
            item_h = 1
        return max(1, self.scrollable_content_region.height // item_h)

    def _jump(self, delta: int) -> None:
        current = self.index if self.index is not None else 0
        target = max(0, min(len(self._nodes) - 1, current + delta))
        self.index = target
        # Top-align so a Page key advances a clean page with the selection at
        # the head of it, instead of the default minimal scroll that would park
        # the new selection at the viewport edge.
        self.scroll_to_widget(self._nodes[target], animate=False, top=True)

    def action_page_down(self) -> None:
        if self._nodes:
            self._jump(self._page())

    def action_page_up(self) -> None:
        if self._nodes:
            self._jump(-self._page())

    def action_cursor_first(self) -> None:
        if self._nodes:
            self._jump(-len(self._nodes))

    async def action_cursor_last(self) -> None:
        if (pending := self._mount_more(everything=True)) is not None:
            await pending
        if self._nodes:
            self._jump(len(self._nodes))


class TrackListView(PagedListView):
    """A PagedListView for track rows. Adds `f` to toggle the highlighted
    track's favourite state.

    Rows are expected to expose a ``track`` attribute; rows that show a heart
    also implement ``set_favorite(bool)`` to flip it in place. ``toggle_favorite``
    on the app keeps the shared favourites cache and the Tracks tab in sync.
    """

    BINDINGS = [Binding("f", "toggle_favorite", "Favourite", show=True)]

    def action_toggle_favorite(self) -> None:
        child = self.highlighted_child
        if child is not None and hasattr(child, "track"):
            self._toggle_favorite(child)

    @work
    async def _toggle_favorite(self, child: ListItem) -> None:
        new_state = await self.app.toggle_favorite(child.track)  # type: ignore[attr-defined]
        if hasattr(child, "set_favorite"):
            child.set_favorite(new_state)  # type: ignore[attr-defined]

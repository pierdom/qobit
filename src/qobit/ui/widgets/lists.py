from __future__ import annotations

from textual import work
from textual.binding import Binding
from textual.widgets import ListItem, ListView


class PagedListView(ListView):
    """A ListView whose Page Up/Down, Home and End move the highlighted
    selection — and scroll to follow it — instead of scrolling the viewport
    past a stationary cursor.

    Plain ``ListView`` only binds up/down/enter, so page keys fall through to
    ``VerticalScroll`` and move the view while the selection stays put. Setting
    ``index`` here triggers ``watch_index``, which scrolls the new item into
    view, so the selection tracks the visible page.
    """

    BINDINGS = [
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("home", "cursor_first", "First", show=False),
        Binding("end", "cursor_last", "Last", show=False),
    ]

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

    def action_cursor_last(self) -> None:
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

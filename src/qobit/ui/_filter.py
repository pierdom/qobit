"""`/` type-to-filter, shared by the Tracks, Albums, Artists and Playlists
tabs. The list or grid keeps focus; while typing, printable keys build the
query instead of acting (so `i`, `f`, `s` are letters then). Enter ends the
typing and keeps the results, so those keys, and Enter to play or open, act
on them again; before, Enter played the highlighted track and there was no
way to reach the `i` menu on a filtered result. Escape (the view's
``action_navigate_back``) clears the filter."""

from __future__ import annotations

from textual import events

from ._utils import WidgetBase


def filter_prompt(query: str) -> str:
    """The panel subtitle while typing."""
    return f"/ {query}_  ·  ⏎ done"


class TypeToFilter(WidgetBase):
    """Mixin for a view with ``_update_subtitle()`` and
    ``_schedule_filter()`` (re-render the results, debounced)."""

    _filter_active: bool = False  # typing the query
    _filter_query: str = ""  # kept after Enter: the results stay filtered

    def _update_subtitle(self) -> None:
        raise NotImplementedError

    def _schedule_filter(self) -> None:
        raise NotImplementedError

    def _filter_allowed(self) -> bool:
        """Whether `/` filters now (the grid tabs: not inside a detail view)."""
        return True

    def action_start_filter(self) -> None:
        if not self._filter_allowed():
            return
        self._filter_active = True
        self._filter_changed(rerender=False)

    def on_key(self, event: events.Key) -> None:
        if not self._filter_active or not self._filter_allowed():
            return
        if event.key == "enter":
            self._filter_active = False
            event.stop()
            event.prevent_default()
            self._filter_changed(rerender=False)
        elif event.is_printable and event.character:
            self._filter_query += event.character
            event.stop()
            self._filter_changed()
        elif event.key in ("backspace", "ctrl+h") and self._filter_query:
            self._filter_query = self._filter_query[:-1]
            event.stop()
            self._filter_changed()

    def _filter_changed(self, rerender: bool = True) -> None:
        self._update_subtitle()
        if rerender:
            self._schedule_filter()
        else:  # typing started or ended: Sort/Filter keys show or hide
            self.refresh_bindings()

from __future__ import annotations

from textual.content import Content
from textual.css.query import NoMatches
from textual.widgets import Label, ListView


def playing_content(
    text: str, is_playing: bool, is_paused: bool, *suffix: tuple[str, str] | str
) -> Content:
    """Assemble a Content for a track-row primary label.

    When is_playing, prepends ▶/⏸ in accent colour and wraps text in accent.
    suffix elements are forwarded to Content.assemble (e.g. a heart-icon tuple).
    """
    if is_playing:
        icon = "⏸" if is_paused else "▶"
        return Content.assemble((f"{icon}  {text}", "$accent"), *suffix)
    return Content.assemble(text, *suffix)


class NowPlayingRowMixin:
    """Mixin for ListItem subclasses that show a ▶/⏸ now-playing indicator.

    Subclasses must implement ``_primary() -> Content`` and have a ``.primary``
    CSS class on their primary Label.
    """

    _is_playing: bool = False
    _is_paused: bool = False

    def set_playing(self, playing: bool, paused: bool = False) -> None:
        if self._is_playing == playing and self._is_paused == paused:
            return
        self._is_playing = playing
        self._is_paused = paused
        self._refresh_primary()

    def _refresh_primary(self) -> None:
        try:
            self.query_one(".primary", Label).update(self._primary())  # type: ignore[attr-defined]
        except NoMatches:
            pass


class NowPlayingViewMixin:
    """Mixin for view widgets that host a track list with a now-playing indicator.

    Call ``_wire_now_playing(RowClass, list_sel)`` from ``on_mount``.
    ``list_sel`` is a CSS selector for the ListView; when provided the cursor
    automatically follows the now-playing track whenever it changes.
    Call ``_apply_now_playing(rows)`` on a freshly-built row list before mounting.
    """

    _np_row_cls: type | None = None
    _np_list_sel: str | None = None
    _np_playing_row: object | None = None  # live reference to the highlighted row

    def _wire_now_playing(self, row_cls: type, list_sel: str | None = None) -> None:
        self._np_row_cls = row_cls
        self._np_list_sel = list_sel
        app = self.app  # type: ignore[attr-defined]
        self.watch(app, "now_playing", self._on_now_playing, init=True)  # type: ignore[attr-defined]
        self.watch(app, "is_paused", self._on_paused_changed, init=False)  # type: ignore[attr-defined]

    def _on_now_playing(self, track: object) -> None:
        now_id: str | None = getattr(track, "id", None)
        paused: bool = getattr(self.app, "is_paused", False)  # type: ignore[attr-defined]

        # O(1): clear the previously-playing row via stored reference.
        prev = self._np_playing_row
        if prev is not None:
            try:
                prev.set_playing(False)  # type: ignore[attr-defined]
            except Exception:
                pass
            self._np_playing_row = None

        if now_id is None:
            return

        # Single O(n) pass: find the new row, update it, move cursor.
        if self._np_list_sel is not None:
            try:
                lv = self.query_one(self._np_list_sel, ListView)  # type: ignore[attr-defined]
                for idx, row in enumerate(lv.query(self._np_row_cls)):
                    if row.track.id == now_id:
                        row.set_playing(True, paused=paused)
                        self._np_playing_row = row
                        lv.index = idx
                        break
                return
            except NoMatches:
                pass

        # Fallback for views without a list selector.
        for row in self.query(self._np_row_cls):  # type: ignore[attr-defined]
            if row.track.id == now_id:
                row.set_playing(True, paused=paused)
                self._np_playing_row = row
                break

    def _on_paused_changed(self, paused: bool) -> None:
        # O(1): update the stored playing row directly.
        if self._np_playing_row is not None:
            try:
                self._np_playing_row.set_playing(True, paused=paused)  # type: ignore[attr-defined]
                return
            except Exception:
                self._np_playing_row = None
        # Fallback scan if reference is stale.
        now_id: str | None = getattr(self.app.now_playing, "id", None)  # type: ignore[attr-defined]
        for row in self.query(self._np_row_cls):  # type: ignore[attr-defined]
            if row.track.id == now_id:
                row.set_playing(True, paused=paused)
                self._np_playing_row = row
                break

    def _apply_now_playing(self, rows: list, *, first_batch: bool = True) -> None:
        """Mark the playing track in a batch of rows before mounting.

        For batched mounts call with ``first_batch=False`` on every batch after
        the first so the reference found in an earlier batch is not discarded.
        """
        if first_batch:
            self._np_playing_row = None  # reset stale reference at start of mount
        if self._np_playing_row is not None:
            return  # already found in a previous batch
        app = self.app  # type: ignore[attr-defined]
        now_id: str | None = getattr(app.now_playing, "id", None)
        if now_id is None:
            return
        is_paused: bool = getattr(app, "is_paused", False)
        for row in rows:
            if row.track.id == now_id:
                row.set_playing(True, paused=is_paused)
                self._np_playing_row = row
                break

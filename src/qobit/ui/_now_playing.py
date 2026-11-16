from __future__ import annotations

import contextlib
import weakref

from textual.content import Content
from textual.css.query import NoMatches
from textual.widgets import Label, ListView

# Every track row is a fixed-width gutter, then the text: ``  ♪  title`` or
# `` 12.  title``, with the second (meta) line indented to the text column.
# The gutter holds the row's lead mark (♪, ↺ or its number) right-aligned in
# ``width`` cells; on the playing row ▶/⏸ takes the lead's place, so nothing
# shifts. Lists pick one width for all their rows so the columns line up.
GUTTER_ICON = 1  # ♪ / ↺ lists
GUTTER_NUM = 3  # numbered lists up to 99 (" 1." … "99.")
GUTTER_NUM_LONG = 4  # playlists (up to "999.")
_GUTTER_GAP = "  "


def gutter(lead: str, width: int) -> str:
    """The lead cell of a row: ``lead`` right-aligned in ``width``, then the gap."""
    return f"{lead:>{width}}{_GUTTER_GAP}"


def meta_indent(width: int) -> str:
    """Indent for a row's second line, so it starts under the title text."""
    return " " * (width + len(_GUTTER_GAP))


# ── playing bars ─────────────────────────────────────────────────────────────
# While a track plays, its row's ▶ becomes a small bar meter (▁▃▅) that the
# app's beat timer animates. Decorative only: qobit never reads the audio.
# Paused (or not started) shows ⏸ / ▶ as before.

_LEVELS = "▁▂▃▄▅▆▇"
# One loop per bar, out of phase, so the meter never pulses in unison.
_BAR_LOOPS = (
    (1, 3, 5, 6, 4, 2, 3, 5, 6, 4),
    (5, 6, 3, 1, 2, 4, 6, 5, 2, 3),
    (2, 1, 3, 5, 6, 5, 3, 1, 4, 6),
)
_beat = {"frame": 0, "live": False}  # set by tick(); read by every row
_LIVE_ROWS: weakref.WeakSet = weakref.WeakSet()  # rows showing the meter


def playing_icon(width: int, paused: bool) -> str:
    """The playing row's lead: the bar meter (up to 3 bars, one per gutter
    cell) while playback runs, else ⏸ paused / ▶ starting."""
    if paused:
        return "⏸"
    if not _beat["live"]:
        return "▶"
    f = _beat["frame"]
    n = max(1, min(width, len(_BAR_LOOPS)))
    loops = _BAR_LOOPS[1:2] if n == 1 else _BAR_LOOPS[:n]
    return "".join(_LEVELS[loop[f % len(loop)]] for loop in loops)


def track_live(row: object, live: bool) -> None:
    """Register (or drop) a row whose title shows the meter, for tick()."""
    if live:
        _LIVE_ROWS.add(row)
    else:
        _LIVE_ROWS.discard(row)


def tick(playing: bool) -> None:
    """The app's beat (a few times a second): advance the meter and redraw
    the rows showing it. ``playing`` False freezes them on ▶/⏸."""
    changed = playing != _beat["live"]
    _beat["live"] = playing
    if not playing and not changed:
        return
    _beat["frame"] += 1
    for row in list(_LIVE_ROWS):
        with contextlib.suppress(Exception):
            row._refresh_primary()  # type: ignore[attr-defined]


def playing_content(
    lead: str,
    text: str,
    is_playing: bool,
    is_paused: bool,
    *suffix: tuple[str, str] | str,
    width: int = GUTTER_ICON,
) -> Content:
    """Assemble a track row's primary label: gutter + text + ``suffix`` parts.

    On the playing row the meter (or ⏸/▶) replaces ``lead`` in the gutter and
    the text turns accent. ``suffix`` parts go to Content.assemble (e.g.
    ``heart_suffix``).
    """
    if is_playing:
        icon = playing_icon(width, is_paused)
        return Content.assemble((gutter(icon, width) + text, "$accent"), *suffix)
    return Content.assemble(gutter(lead, width) + text, *suffix)


def heart_suffix(favorite: bool, pad: str = "  ") -> tuple[str, str] | str:
    """The trailing ``♥`` Content.assemble part every favourite-aware track
    row appends to its primary label — "" when not favourited."""
    from .screens.search import ICON_FAV

    return (f"{pad}{ICON_FAV}", "$accent") if favorite else ""


class NowPlayingRowMixin:
    """Mixin for ListItem subclasses that show a ▶/⏸ now-playing indicator.

    Subclasses must implement ``_primary() -> Content`` and have a ``.row-title``
    CSS class on their primary Label.
    """

    _is_playing: bool = False
    _is_paused: bool = False

    def set_playing(self, playing: bool, paused: bool = False) -> None:
        if self._is_playing == playing and self._is_paused == paused:
            return
        self._is_playing = playing
        self._is_paused = paused
        track_live(self, playing)
        self._refresh_primary()

    def _refresh_primary(self) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one(".row-title", Label).update(self._primary())  # type: ignore[attr-defined]


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
        # The cursor follows playback only if it was on the playing row (or
        # nowhere): moving it on every track change lost your place while
        # browsing a list that's playing.
        follow = True
        if self._np_list_sel is not None:
            with contextlib.suppress(NoMatches):
                lv = self.query_one(self._np_list_sel, ListView)  # type: ignore[attr-defined]
                follow = lv.index is None or lv.highlighted_child is prev
        if prev is not None:
            with contextlib.suppress(Exception):
                prev.set_playing(False)  # type: ignore[attr-defined]
            self._np_playing_row = None

        if now_id is None:
            return

        # Single O(n) pass: find the new row, update it, move cursor.
        if self._np_list_sel is not None:
            try:
                lv = self.query_one(self._np_list_sel, ListView)  # type: ignore[attr-defined]
                # A PagedListView's `rows` includes rows not mounted yet: mark
                # those too (they render marked), but only move the cursor to
                # a mounted one.
                rows = getattr(lv, "rows", None) or list(lv.query(self._np_row_cls))
                for idx, row in enumerate(rows):
                    if isinstance(row, self._np_row_cls) and row.track.id == now_id:
                        row.set_playing(True, paused=paused)
                        self._np_playing_row = row
                        if follow and idx < len(lv.children):
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

"""The lyrics of the playing track, drawn as a window of centred rows around the
line being sung (or, for lyrics without timing, a scrollable text).

One widget for the Playback tab's panel and the lyrics page. It wires itself to
the app's reactives (the same pattern as TransportBar): ``lyrics_version``
(something new was found) redraws it, ``lyrics_line`` (the sung line moved)
re-centres it. It holds no widget per line: a song is a few dozen lines, and
every mounted widget costs on each repaint on a slow machine, so it draws only
the rows that fit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.cells import cell_len, chop_cells
from rich.text import Text
from textual.binding import Binding
from textual.widget import Widget

from .._utils import readout
from ..themes import ACCENT_TEXT, MUTED, theme_color

if TYPE_CHECKING:
    from ..app import QobitApp

# What is said when there are no lines to show, by the app's lyrics_status.
_MESSAGES = {
    "idle": "Nothing playing",
    "off": "Lyrics are off  ·  Ctrl+P → Lyrics",
    "loading": "Looking for lyrics…",
    "none": "No lyrics found",
    "instrumental": "Instrumental",
    "failed": "Couldn't reach the lyrics service",
}


def wrap(text: str, width: int) -> list[str]:
    """``text`` in rows of at most ``width`` cells, broken at spaces (a word
    longer than a row is cut). Centred rows read best when they are about as
    long as each other: of the widths that give this many rows, the narrowest
    is used, so a last word doesn't sit alone."""
    rows = _greedy(text, width)
    if len(rows) < 2:
        return rows
    low, high = max(cell_len(w) for w in text.split(" ")), width
    while low < high:  # the narrowest width that still makes as many rows
        mid = (low + high) // 2
        if len(_greedy(text, mid)) == len(rows):
            high = mid
        else:
            low = mid + 1
    return _greedy(text, high)


def _greedy(text: str, width: int) -> list[str]:
    if cell_len(text) <= width:
        return [text]
    rows: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if cell_len(candidate) <= width:
            current = candidate
            continue
        if current:
            rows.append(current)
            current = ""
        if cell_len(word) <= width:
            current = word
        else:
            *whole, current = chop_cells(word, width)
            rows.extend(whole)
    if current:
        rows.append(current)
    return rows or [""]


def _indent(row: str, width: int) -> int:
    return max(0, (width - cell_len(row)) // 2)


def _centred(row: str, width: int) -> str:
    return " " * _indent(row, width) + row


class LyricsView(Widget):
    can_focus = True
    BINDINGS = [
        Binding("up", "scroll_up", "Scroll up", show=False),
        Binding("down", "scroll_down", "Scroll down", show=False),
        Binding("pageup", "scroll_page_up", "Page up", show=False),
        Binding("pagedown", "scroll_page_down", "Page down", show=False),
    ]

    DEFAULT_CSS = """
    LyricsView {
        height: 1fr;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-title-style: bold;
        border-subtitle-color: $qobit-muted;
        border-subtitle-align: right;
        padding: 0 2;
    }
    LyricsView:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    """

    # The rows for the current lyrics at the current width: (text, line index),
    # and the first row of each line. Rebuilt when either changes.
    _rows: list[tuple[str, int]]
    _starts: list[int]
    _built_for: tuple[int, int] | None = None
    _scroll: int = 0  # first row shown, for lyrics with no timing

    def on_mount(self) -> None:
        self.border_title = readout("Lyrics")
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "lyrics_version", self._on_new, init=True)
        self.watch(app, "lyrics_line", self._on_line, init=False)
        self.watch(app, "theme", self._on_theme, init=False)

    # ── following the app ────────────────────────────────────────────────────

    def _on_new(self, _: int) -> None:
        self._built_for = None
        self._scroll = 0
        self.refresh_subtitle()
        self.refresh()

    # What the title bar says about the lyrics (the source, a shifted timing)
    # and, where the panel takes turns with the hero, the key that swaps them.
    hint: str = ""

    def refresh_subtitle(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        parts: list[str] = []
        ly = app.lyrics
        if ly is not None and ly.lines:
            if ly.source:
                parts.append(ly.source)
            if not ly.synced:
                parts.append("not timed")
            elif app.lyrics_offset:
                parts.append(f"{app.lyrics_offset:+.1f} s")
        if self.hint:
            parts.append(self.hint)
        self.border_subtitle = "  ·  ".join(parts)

    def _on_line(self, _: int) -> None:
        self.refresh()

    def _on_theme(self, _: object) -> None:
        # theme_variables is recomputed after the `theme` reactive fires.
        self.call_after_refresh(self.refresh)

    # ── scrolling (lyrics with no timing; timed ones follow the music) ──────

    def _can_scroll(self) -> bool:
        ly = self.app.lyrics  # type: ignore[attr-defined]
        return ly is not None and ly.lines and not ly.synced

    def _scroll_by(self, rows: int) -> None:
        if self._can_scroll():
            self._scroll = max(0, self._scroll + rows)
            self.refresh()

    def action_scroll_up(self) -> None:
        self._scroll_by(-1)

    def action_scroll_down(self) -> None:
        self._scroll_by(1)

    def action_scroll_page_up(self) -> None:
        self._scroll_by(-max(1, self.content_size.height - 1))

    def action_scroll_page_down(self) -> None:
        self._scroll_by(max(1, self.content_size.height - 1))

    # ── drawing ──────────────────────────────────────────────────────────────

    def _build(self, width: int) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        ly = app.lyrics
        self._rows, self._starts = [], []
        for i, line in enumerate(ly.lines if ly else ()):
            self._starts.append(len(self._rows))
            self._rows.extend((row, i) for row in wrap(line.text, width))
        self._built_for = (id(ly), width)

    def render(self) -> Text:
        app: QobitApp = self.app  # type: ignore[assignment]
        width, height = self.content_size
        if width <= 0 or height <= 0:
            return Text("")
        muted = theme_color(app, MUTED)
        ly = app.lyrics
        if ly is None or not ly.lines:
            message = _MESSAGES.get(app.lyrics_status, "No lyrics found")
            return Text(
                "\n" * ((height - 1) // 2) + _centred(message, width), style=muted, no_wrap=True
            )
        if self._built_for != (id(ly), width):
            self._build(width)
        rows = self._rows
        current = app.lyrics_line if ly.synced else -1
        if ly.synced:
            first = self._starts[current] if 0 <= current < len(self._starts) else 0
            top = first - height // 2 + 1 if current >= 0 else 0
        else:
            top = min(self._scroll, max(0, len(rows) - height))
            self._scroll = top
        top = max(0, min(top, max(0, len(rows) - height)))
        accent = theme_color(app, ACCENT_TEXT)
        text = Text(no_wrap=True, overflow="ellipsis")
        for n, (row, index) in enumerate(rows[top : top + height]):
            if n:
                text.append("\n")
            text.append(" " * _indent(row, width))  # centred: Textual drops Rich's justify
            if not ly.synced:
                text.append(row)
            elif index == current:
                text.append(row, style=f"bold {accent}")
            elif index < current:
                text.append(row, style=muted)  # sung already
            else:
                text.append(row)
        return text

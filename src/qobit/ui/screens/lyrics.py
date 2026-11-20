"""The lyrics page: the lyrics of the playing track over the whole screen
(``L`` anywhere, or ``l`` where the Playback panel has no room). Escape (the
app's ``back``) closes it."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen

from ..nav import QobitFooter, page_header
from ..widgets.lyrics import LyricsView
from ..widgets.transport import TransportBar


class LyricsScreen(Screen):
    # The same keys as the app's, listed here (the footer has room on this page).
    BINDINGS = [
        Binding("comma", "app.lyrics_earlier", "Sync ◀0.5s"),
        Binding("full_stop", "app.lyrics_later", "Sync 0.5s▶"),
        Binding("x", "app.lyrics_reject", "Wrong lyrics"),
    ]

    DEFAULT_CSS = """
    LyricsScreen { layout: vertical; }
    /* Bottom margin only: the app's horizontal inset is the Screen's padding
       (see QobitApp.CSS), like AlbumScreen. */
    LyricsScreen LyricsView { margin: 0 0 1 0; }
    """

    def nav_levels(self) -> list[str]:
        return ["Lyrics"]

    def nav_to(self, depth: int) -> None:
        pass  # one level

    def compose(self) -> ComposeResult:
        yield from page_header(self.app)  # type: ignore[arg-type]
        yield LyricsView(id="lyrics-full")
        yield TransportBar()
        yield QobitFooter()

    def on_mount(self) -> None:
        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        self.query_one(LyricsView).focus()

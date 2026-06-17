from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label


class PlaylistsView(Widget):
    DEFAULT_CSS = """
    PlaylistsView {
        height: 1fr;
        layout: vertical;
        align: center middle;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("[dim]Playlists coming soon[/dim]", markup=True)

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label


class ArtistsView(Widget):
    DEFAULT_CSS = """
    ArtistsView {
        height: 1fr;
        layout: vertical;
        align: center middle;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("[dim]Artists coming soon[/dim]", markup=True)

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Track
from .search import ICON_ALBUM, ICON_ARTIST

# (action id, icon, label). Order also drives the 1-5 quick-select bindings.
# Icons are all single-width, monochrome BMP glyphs of the same geometric weight
# (matching the app's ⊙/◎) — no emoji, which render double-width and coloured and
# break column alignment.
_OPTIONS: list[tuple[str, str, str]] = [
    ("play_next", "▸", "Play next"),
    ("add_queue", "⊕", "Add to queue"),
    ("radio", "◈", "Play radio"),
    ("artist", ICON_ARTIST, "Go to artist"),
    ("album", ICON_ALBUM, "Go to album"),
]

# Width of the label column so the trailing key numbers line up as a grid.
_LABEL_W = max(len(label) for _, _, label in _OPTIONS) + 2


class MenuOption(ListItem):
    def __init__(self, action: str, icon: str, label: str, number: int) -> None:
        super().__init__()
        self.action_id = action
        self._icon = icon
        self._label = label
        self._number = number

    def compose(self) -> ComposeResult:
        yield Label(
            Content.assemble(
                (f"{self._icon}   ", "$accent"),
                f"{self._label:<{_LABEL_W}}",
                (str(self._number), "$text-muted"),
            )
        )


class TrackContextMenu(ModalScreen[str | None]):
    """Popup action menu for a single track. Dismisses with the chosen action
    id (or None on escape). The caller — QobitApp — dispatches the action."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("i", "close", "Close", show=False),
        *[Binding(str(i), f"pick({i})", show=False) for i in range(1, len(_OPTIONS) + 1)],
    ]

    DEFAULT_CSS = """
    TrackContextMenu {
        align: center middle;
        background: $background 55%;
    }
    TrackContextMenu #menu {
        width: 42;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 0;
    }
    TrackContextMenu #menu-header {
        width: 1fr;
        height: auto;
        padding: 1 2;
        background: $boost;
        color: $accent;
        text-style: bold;
    }
    TrackContextMenu #menu-sub {
        width: 1fr;
        padding: 0 2 1 2;
        background: $boost;
        color: $text-muted;
    }
    TrackContextMenu ListView {
        height: auto;
        background: $surface;
    }
    TrackContextMenu MenuOption {
        height: 1;
        padding: 0 2;
    }
    TrackContextMenu MenuOption Label { width: 1fr; }
    """

    def __init__(self, track: Track) -> None:
        super().__init__()
        self._track = track

    def compose(self) -> ComposeResult:
        with Vertical(id="menu"):
            yield Label(self._track.display_title, id="menu-header")
            yield Label(self._track.artist, id="menu-sub")
            with ListView(id="menu-options"):
                for i, (action, icon, label) in enumerate(_OPTIONS, 1):
                    yield MenuOption(action, icon, label, i)

    def on_mount(self) -> None:
        self.query_one("#menu-options", ListView).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_pick(self, number: int) -> None:
        self.dismiss(_OPTIONS[number - 1][0])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, MenuOption):
            self.dismiss(event.item.action_id)

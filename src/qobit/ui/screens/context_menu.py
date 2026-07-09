from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Track
from .search import ICON_ALBUM, ICON_ARTIST

# (action id, icon, label). Icons are all single-width, monochrome BMP glyphs of
# the same geometric weight (matching the app's ⊙/◎) — no emoji, which render
# double-width and coloured and break column alignment.
_OPTIONS: list[tuple[str, str, str]] = [
    ("play_next", "▸", "Play next"),
    ("add_queue", "⊕", "Add to queue"),
    ("radio", "◈", "Play radio"),
    ("artist", ICON_ARTIST, "Go to artist"),
    ("album", ICON_ALBUM, "Go to album"),
]
# Shown only when the menu is opened from an Up Next row (circled minus mirrors
# the circled plus of "Add to queue").
_REMOVE_OPTION: tuple[str, str, str] = ("remove_queue", "⊖", "Remove from queue")

# Enough digit bindings to cover the base options plus the optional remove row.
_MAX_OPTIONS = len(_OPTIONS) + 1


class MenuOption(ListItem):
    def __init__(self, action: str, icon: str, label: str, number: int, label_w: int) -> None:
        super().__init__()
        self.action_id = action
        self._icon = icon
        self._label = label
        self._number = number
        self._label_w = label_w

    def compose(self) -> ComposeResult:
        yield Label(
            Content.assemble(
                (f"{self._icon}   ", "$accent"),
                f"{self._label:<{self._label_w}}",
                (str(self._number), "$text-muted"),
            )
        )


class TrackContextMenu(ModalScreen[str | None]):
    """Popup action menu for a single track. Dismisses with the chosen action
    id (or None on escape). The caller — QobitApp — dispatches the action.

    ``in_queue`` adds a "Remove from queue" row; it's meant only for tracks
    opened from an Up Next row."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("i", "close", "Close", show=False),
        *[Binding(str(i), f"pick({i})", show=False) for i in range(1, _MAX_OPTIONS + 1)],
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

    def __init__(self, track: Track, in_queue: bool = False) -> None:
        super().__init__()
        self._track = track
        self._options = _OPTIONS + ([_REMOVE_OPTION] if in_queue else [])
        self._label_w = max(len(label) for _, _, label in self._options) + 2

    def compose(self) -> ComposeResult:
        with Vertical(id="menu"):
            yield Label(self._track.display_title, id="menu-header")
            yield Label(self._track.artist, id="menu-sub")
            with ListView(id="menu-options"):
                for i, (action, icon, label) in enumerate(self._options, 1):
                    yield MenuOption(action, icon, label, i, self._label_w)

    def on_mount(self) -> None:
        self.query_one("#menu-options", ListView).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_pick(self, number: int) -> None:
        if 1 <= number <= len(self._options):
            self.dismiss(self._options[number - 1][0])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, MenuOption):
            self.dismiss(event.item.action_id)

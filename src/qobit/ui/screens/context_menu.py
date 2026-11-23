from __future__ import annotations

import contextlib

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Track
from .._now_playing import plain_if_highlighted
from .search import ICON_ALBUM, ICON_ARTIST

# (action id, icon, label). Icons are all single-width, monochrome BMP glyphs of
# the same geometric weight (matching the app's ⊙/◎) — no emoji, which render
# double-width and coloured and break column alignment.
Option = tuple[str, str, str]

_TRACK_OPTIONS: list[Option] = [
    ("play_next", "▸", "Play next"),
    ("add_queue", "⊕", "Add to queue"),
    ("radio", "◈", "Play radio"),
    ("artist", ICON_ARTIST, "Go to artist"),
    ("album", ICON_ALBUM, "Go to album"),
]
# Shown only when the menu is opened from an Up Next row (circled minus mirrors
# the circled plus of "Add to queue").
_REMOVE_OPTION: Option = ("remove_queue", "⊖", "Remove from queue")

# Enough digit bindings for the longest menu: track options, favourite,
# remove from queue, album favourite, follow artist.
_MAX_OPTIONS = 9


def _heart(on: bool) -> str:
    """Filled when the item already is a favourite (the action removes it)."""
    return "♥" if on else "♡"


def track_options(*, favorite: bool, in_queue: bool = False) -> list[Option]:
    fav: Option = (
        "favorite",
        _heart(favorite),
        "Remove from favourites" if favorite else "Add to favourites",
    )
    return [*_TRACK_OPTIONS[:3], fav, *_TRACK_OPTIONS[3:]] + ([_REMOVE_OPTION] if in_queue else [])


def album_option(favorite: bool) -> Option:
    label = "Remove album from favourites" if favorite else "Add album to favourites"
    return ("favorite_album", _heart(favorite), label)


def album_artist_option() -> Option:
    """Go to the album's artist: for an album with no track selected (a grid
    card), where the track options' "Go to artist" isn't offered."""
    return ("album_artist", ICON_ARTIST, "Go to artist")


def artist_option(following: bool) -> Option:
    return ("follow_artist", _heart(following), "Unfollow artist" if following else "Follow artist")


class _WrapListView(ListView):
    """Up on the first option goes to the last, down on the last to the first."""

    def action_cursor_down(self) -> None:
        if self._nodes and self.index == len(self._nodes) - 1:
            self.index = 0
        else:
            super().action_cursor_down()

    def action_cursor_up(self) -> None:
        if self._nodes and self.index in (0, None):
            self.index = len(self._nodes) - 1
        else:
            super().action_cursor_up()


class MenuOption(ListItem):
    def __init__(self, action: str, icon: str, label: str, number: int, label_w: int) -> None:
        super().__init__()
        self.action_id = action
        self._icon = icon
        self._label = label
        self._number = number
        self._label_w = label_w

    def _content(self) -> Content:
        content = Content.assemble(
            (f"{self._icon}   ", "$qobit-accent-text"),
            f"{self._label:<{self._label_w}}",
            (str(self._number), "$qobit-muted"),
        )
        return plain_if_highlighted(self, content)

    def compose(self) -> ComposeResult:
        yield Label(self._content())

    def watch_highlighted(self, value: bool) -> None:
        super().watch_highlighted(value)
        with contextlib.suppress(NoMatches):
            self.query_one(Label).update(self._content(), layout=False)  # same text


class ContextMenu(ModalScreen[str | None]):
    """Popup action menu (``i``) for a track, album or artist. Dismisses with
    the chosen action id (or None on escape); the caller, QobitApp, builds the
    options and dispatches the action."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("i", "close", "Close", show=False),
        *[Binding(str(i), f"pick({i})", show=False) for i in range(1, _MAX_OPTIONS + 1)],
    ]

    DEFAULT_CSS = """
    ContextMenu {
        align: center middle;
        /* No tint behind: see ModalScreen in app_css.py (Kitty images). */
    }
    ContextMenu #menu {
        width: 42;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 0;
    }
    /* One fill for the whole menu (a floating layer needs one to stay
       readable over the page); a rule under the header, not a second band. */
    ContextMenu #menu-header {
        width: 1fr;
        height: auto;
        padding: 1 2 0 2;  /* title and artist on consecutive lines */
        color: $qobit-accent-text;
        text-style: bold;
    }
    ContextMenu #menu-sub {
        width: 1fr;
        padding: 0 2 0 2;
        border-bottom: round $accent 40%;
        color: $qobit-muted;
    }
    ContextMenu ListView {
        height: auto;
        background: $surface;
    }
    ContextMenu MenuOption {
        height: 1;
        padding: 0 2;
    }
    ContextMenu MenuOption Label { width: 1fr; }
    """

    def __init__(self, title: str, subtitle: str, options: list[Option]) -> None:
        super().__init__()
        self._title = title
        self._subtitle = subtitle
        self._options = options
        self._label_w = max(len(label) for _, _, label in self._options) + 2

    def compose(self) -> ComposeResult:
        with Vertical(id="menu"):
            yield Label(self._title, id="menu-header", markup=False)
            yield Label(self._subtitle, id="menu-sub", markup=False)
            with _WrapListView(id="menu-options"):
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


class GoToMenu(ContextMenu):
    """The "go to" popup (``g``): jump to the artist or album of the track
    that's playing. Same look and dispatch as the track menu (the caller routes
    "artist"/"album" through QobitApp._on_track_menu); ``g`` closes it again."""

    BINDINGS = [Binding("g", "close", "Close", show=False)]

    def __init__(self, track: Track) -> None:
        options = [o for o in _TRACK_OPTIONS if o[0] in ("artist", "album")]
        super().__init__(track.display_title, track.artist, options)

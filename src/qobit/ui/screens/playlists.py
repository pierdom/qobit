from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from ...qobuz.models import Playlist
from .._images import fetch_image

if TYPE_CHECKING:
    from ..app import QobitApp

_SORT_OPTIONS: list[tuple[str, str]] = [
    ("updated_at", "Updated"),
    ("created_at", "Created"),
    ("name", "Name"),
    ("owner", "Owner"),
]
_SORT_KEYS = [k for k, _ in _SORT_OPTIONS]

_CARD_IMG_H = 7


class PlaylistCard(Widget):
    class Selected(Message):
        def __init__(self, playlist: Playlist) -> None:
            super().__init__()
            self.playlist = playlist

    DEFAULT_CSS = """
    PlaylistCard {
        layout: horizontal;
        height: 7;
        padding: 0;
    }
    PlaylistCard TGPImage {
        width: 12;
        height: 7;
        margin-right: 1;
    }
    PlaylistCard .card-info {
        width: 1fr;
        height: 1fr;
        layout: vertical;
    }
    PlaylistCard .card-name {
        height: 3;
        width: 1fr;
        text-style: bold;
        overflow: hidden hidden;
    }
    PlaylistCard .card-owner {
        height: 1;
        width: 1fr;
        color: $text-muted;
        overflow: hidden hidden;
    }
    PlaylistCard .card-meta {
        height: 1;
        width: 1fr;
        color: $text-muted;
        overflow: hidden hidden;
    }
    PlaylistCard .card-date {
        height: 1;
        width: 1fr;
        color: $text-disabled;
        overflow: hidden hidden;
    }
    PlaylistCard.-selected .card-name { color: $accent; text-style: bold; }
    PlaylistCard.-selected .card-owner { color: $accent; }
    PlaylistCard.-selected .card-meta { color: $accent; }
    PlaylistCard.-selected .card-date { color: $accent; }
    """

    def __init__(self, playlist: Playlist) -> None:
        super().__init__()
        self._playlist = playlist

    @property
    def playlist(self) -> Playlist:
        return self._playlist

    def compose(self) -> ComposeResult:
        yield TGPImage()
        with Vertical(classes="card-info"):
            yield Label(escape(self._playlist.name), classes="card-name", markup=True)
            yield Label(
                f"[dim]by[/dim] {escape(self._playlist.owner)}",
                classes="card-owner",
                markup=True,
            )
            dur = self._playlist.duration_str
            meta = f"{self._playlist.tracks_count} tracks"
            if dur:
                meta += f"  ·  {dur}"
            yield Label(f"[dim]{escape(meta)}[/dim]", classes="card-meta", markup=True)
            date = self._playlist.date_str
            yield Label(f"[dim]{escape(date)}[/dim]", classes="card-date", markup=True)

    def on_mount(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_w = round(_CARD_IMG_H * cell.height / cell.width)
            self.query_one(TGPImage).styles.width = img_w
        if self._playlist.image_url:
            self._fetch_art(self._playlist.image_url)

    @work
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_mounted:
            try:
                self.query_one(TGPImage).image = img
            except NoMatches:
                pass

    def on_click(self) -> None:
        self.post_message(PlaylistCard.Selected(self._playlist))


class PlaylistGrid(ScrollableContainer):
    """Scrollable grid of PlaylistCards; column count adapts to available width."""

    BINDINGS = [
        Binding("up", "move('up')", show=False),
        Binding("down", "move('down')", show=False),
        Binding("left", "move('left')", show=False),
        Binding("right", "move('right')", show=False),
        Binding("enter", "open_selected", "Open playlist", show=False),
    ]

    DEFAULT_CSS = """
    PlaylistGrid {
        layout: grid;
        grid-size: 2;
        grid-rows: 7;
        grid-gutter: 1 2;
    }
    """

    _cols: int = 2
    _cursor: int = -1

    def __init__(self, *args: object, tile_min_width: int = 50, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._tile_min_width = tile_min_width

    def on_focus(self) -> None:
        if self._cursor == -1:
            self._move_cursor(0)

    def on_resize(self) -> None:
        self._cols = max(1, self.content_size.width // self._tile_min_width)
        self.styles.grid_size_columns = self._cols

    def _move_cursor(self, idx: int) -> None:
        cards = list(self.query(PlaylistCard))
        if not cards or idx < 0 or idx >= len(cards):
            return
        if self._cursor >= 0 and self._cursor < len(cards):
            cards[self._cursor].remove_class("-selected")
        self._cursor = idx
        cards[idx].add_class("-selected")
        cards[idx].scroll_visible()

    def action_open_selected(self) -> None:
        cards = list(self.query(PlaylistCard))
        if 0 <= self._cursor < len(cards):
            self.post_message(PlaylistCard.Selected(cards[self._cursor]._playlist))

    def action_move(self, direction: str) -> None:
        cards = list(self.query(PlaylistCard))
        if not cards:
            return
        if self._cursor == -1:
            self._move_cursor(0)
            return
        idx = self._cursor
        target: int | None = None
        if direction == "right" and idx + 1 < len(cards):
            target = idx + 1
        elif direction == "left" and idx > 0:
            target = idx - 1
        elif direction == "down" and idx + self._cols < len(cards):
            target = idx + self._cols
        elif direction == "up" and idx - self._cols >= 0:
            target = idx - self._cols
        if target is not None:
            self._move_cursor(target)


class PlaylistsView(Widget):
    BINDINGS = [
        Binding("s", "cycle_sort", "Sort"),
        Binding("r", "toggle_reverse", "Rev"),
        Binding("/", "start_filter", "Filter"),
    ]

    DEFAULT_CSS = """
    PlaylistsView {
        height: 1fr;
        layout: vertical;
    }
    PlaylistsView #playlists-grid-view { height: 1fr; }
    PlaylistsView PlaylistGrid {
        height: 1fr;
        grid-rows: 7;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
        border-subtitle-color: $accent 40%;
        border-subtitle-align: right;
        padding: 0 1 0 1;
    }
    PlaylistsView PlaylistGrid:focus {
        border: round $accent;
        border-title-color: $accent;
        border-subtitle-color: $accent;
    }
    """

    _loaded: bool = False
    _sort_key: str = "updated_at"
    _sort_reverse: bool = True
    _filter_active: bool = False
    _filter_query: str = ""
    _render_version: int = 0
    _filter_timer: object | None = None
    _playlists: list[Playlist]

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._playlists = []

    def compose(self) -> ComposeResult:
        with Vertical(id="playlists-grid-view"):
            yield PlaylistGrid(id="user-playlists-grid", tile_min_width=50)

    def on_mount(self) -> None:
        grid = self.query_one("#user-playlists-grid", PlaylistGrid)
        grid.border_title = "Playlists"
        self._update_subtitle()

    def on_show(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._load()
        self.call_after_refresh(self.query_one("#user-playlists-grid", PlaylistGrid).focus)

    # ── filter ───────────────────────────────────────────────────────────────

    def action_navigate_back(self) -> bool:
        if self._filter_active or self._filter_query:
            self._filter_active = False
            self._filter_query = ""
            self._update_subtitle()
            self._render_grid()
            return True
        return False

    def action_start_filter(self) -> None:
        self._filter_active = True
        self._update_subtitle()

    def on_key(self, event: events.Key) -> None:
        if not self._filter_active:
            return
        if event.is_printable and event.character:
            self._filter_query += event.character
            event.stop()
            self._update_subtitle()
            self._schedule_filter()
        elif event.key in ("backspace", "ctrl+h"):
            if self._filter_query:
                self._filter_query = self._filter_query[:-1]
                event.stop()
                self._update_subtitle()
                self._schedule_filter()

    def _schedule_filter(self) -> None:
        if self._filter_timer is not None:
            self._filter_timer.stop()  # type: ignore[union-attr]
        self._filter_timer = self.set_timer(0.15, self._render_grid)

    # ── sort ─────────────────────────────────────────────────────────────────

    def action_cycle_sort(self) -> None:
        if self._filter_active:
            return
        idx = (_SORT_KEYS.index(self._sort_key) + 1) % len(_SORT_KEYS)
        self._sort_key = _SORT_KEYS[idx]
        self._sort_reverse = self._sort_key in ("updated_at", "created_at")
        self._apply_sort()

    def action_toggle_reverse(self) -> None:
        if self._filter_active:
            return
        self._sort_reverse = not self._sort_reverse
        self._apply_sort()

    def _update_subtitle(self) -> None:
        grid = self.query_one("#user-playlists-grid", PlaylistGrid)
        if self._filter_active:
            grid.border_subtitle = f"/ {self._filter_query}_"
        elif self._filter_query:
            grid.border_subtitle = f"⌕ {self._filter_query}"
        else:
            arrow = "↓" if self._sort_reverse else "↑"
            label = dict(_SORT_OPTIONS)[self._sort_key]
            grid.border_subtitle = f"{arrow} {label}"

    def _apply_sort(self) -> None:
        self._update_subtitle()
        self._render_grid()

    # ── data ─────────────────────────────────────────────────────────────────

    def _sorted_playlists(self) -> list[Playlist]:
        def key(p: Playlist) -> object:
            if self._sort_key == "updated_at":
                return p.updated_at or p.created_at or 0
            if self._sort_key == "created_at":
                return p.created_at or 0
            if self._sort_key == "name":
                return p.name.lower()
            if self._sort_key == "owner":
                return p.owner.lower()
            return 0

        return sorted(self._playlists, key=key, reverse=self._sort_reverse)

    def _filtered_playlists(self) -> list[Playlist]:
        playlists = self._sorted_playlists()
        if not self._filter_query:
            return playlists
        q = self._filter_query.lower()
        return [p for p in playlists if q in p.name.lower() or q in p.owner.lower()]

    def _render_grid(self) -> None:
        self._render_version += 1
        grid = self.query_one("#user-playlists-grid", PlaylistGrid)
        grid._cursor = -1
        self._mount_cards(self._filtered_playlists(), self._render_version)

    @work
    async def _mount_cards(self, playlists: list[Playlist], version: int) -> None:
        if version != self._render_version:
            return
        grid = self.query_one("#user-playlists-grid", PlaylistGrid)
        await grid.remove_children()
        if version != self._render_version:
            return
        if not playlists:
            msg = "[dim]No matches.[/dim]" if self._filter_query else "[dim]No playlists yet.[/dim]"
            await grid.mount(Label(msg, markup=True))
            return
        await grid.mount(*[PlaylistCard(p) for p in playlists])

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        grid = self.query_one("#user-playlists-grid", PlaylistGrid)
        try:
            items = await app._client.get_all_user_playlists()
        except Exception as e:
            await grid.mount(Label(f"[red]{e}[/red]", markup=True))
            return
        self._playlists = [Playlist.from_api(raw) for raw in items]
        self._render_grid()

    # ── navigation ───────────────────────────────────────────────────────────

    @on(PlaylistCard.Selected)
    def _on_playlist_selected(self, event: PlaylistCard.Selected) -> None:
        from .playlist_detail import PlaylistScreen

        self.app.push_screen(PlaylistScreen(event.playlist.id))
